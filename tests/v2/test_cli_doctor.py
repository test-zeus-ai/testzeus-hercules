"""Tests for the ``hercules`` CLI: ``doctor`` and ``version``."""

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from testzeus_hercules.cli.doctor import DoctorDeps, DoctorReport
from testzeus_hercules.cli.main import main
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import ALLOWED_FIELDS, RecordingTransport, TelemetryClient
from testzeus_hercules.llm.tiers import GIB

from tests.v2.test_llm_fakes import FakeServer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _no_scanner(env: Any, cache_dir: Path) -> Any:
    from testzeus_hercules.scan.install import ScannerMissing

    raise ScannerMissing("not installed")


class Harness:
    """Runs ``main`` with a fake server, fixed memory and recorded telemetry."""

    def __init__(self, tmp_path: Path, server: FakeServer, *, memory_gib: float | None = 32, env: dict[str, str] | None = None) -> None:
        self.server = server
        self.project = tmp_path / "project"
        self.project.mkdir(exist_ok=True)
        self.home = tmp_path / "home"
        self.env = {"HERCULES_HOME": str(self.home), **(env or {})}
        self.telemetry = RecordingTransport()
        memory = None if memory_gib is None else int(memory_gib * GIB)
        # The optional scanner is looked for on this machine only by the scanner tests (tests/v2/test_cli_scan.py).
        self.deps = DoctorDeps(detect_memory=lambda: memory, make_client=server.client, make_telemetry=self._telemetry, appium_status=lambda url: None, find_scanner=_no_scanner)

    def _telemetry(self, settings: Settings, paths: HerculesPaths) -> TelemetryClient:
        return TelemetryClient(settings, paths, transport=self.telemetry)

    def run(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        code = main(["doctor", "--project", str(self.project), *args], env=self.env, doctor_deps=self.deps, stdout=out)
        return code, out.getvalue()


def test_doctor_happy_path_prints_a_readable_report(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeServer(kind="ollama"))

    code, out = harness.run()

    assert code == 0
    assert "provider" in out and "ollama" in out
    assert "recommended" in out
    assert "32.0 GiB" in out
    assert "http://127.0.0.1:11434/v1" in out
    assert "gemma4:26b-a4b-it-q4_K_M" in out
    for name in ("reachable", "model", "context", "tool_calling", "tool_choice", "json_schema", "images"):
        assert name in out
    assert (harness.home / "capabilities.json").is_file()


def test_doctor_json_output_validates_against_the_report_model(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeServer(kind="ollama"))

    code, out = harness.run("--json")

    report = DoctorReport.model_validate_json(out)
    assert code == report.exit_code == 0
    assert report.tier.name == "recommended"
    assert report.memory_bytes == 32 * GIB
    assert report.capabilities is not None and report.capabilities.model_id == "gemma4:26b-a4b-it-q4_K_M"
    assert {check.name for check in report.checks} >= {"reachable", "context", "images"}


def test_doctor_reuses_cached_capabilities_unless_no_cache(tmp_path: Path) -> None:
    server = FakeServer(kind="ollama")
    harness = Harness(tmp_path, server)
    assert harness.run()[0] == 0
    first_chats = len(server.chat_requests())

    assert harness.run()[0] == 0
    assert len(server.chat_requests()) == first_chats

    assert harness.run("--no-cache")[0] == 0
    assert len(server.chat_requests()) == 2 * first_chats


def test_doctor_4k_ollama_context_exits_1_with_the_fix(tmp_path: Path) -> None:
    code, out = Harness(tmp_path, FakeServer(kind="ollama", context_length=4096)).run()

    assert code == 1
    assert "OLLAMA_CONTEXT_LENGTH=32768 ollama serve" in out


def test_doctor_unreachable_server_exits_2(tmp_path: Path) -> None:
    code, out = Harness(tmp_path, FakeServer(reachable=False)).run()

    assert code == 2
    assert "reachable" in out
    assert "ollama serve" in out


def test_doctor_model_not_found_lists_ids_and_pull_command(tmp_path: Path) -> None:
    code, out = Harness(tmp_path, FakeServer(kind="ollama", models=["llama3.2:3b"]), memory_gib=24).run()

    assert code == 1
    assert "llama3.2:3b" in out
    assert "ollama pull gemma4:26b-a4b-it-qat" in out


def test_doctor_remote_endpoint_under_local_policy_is_blocked_without_contact(tmp_path: Path) -> None:
    server = FakeServer(kind="generic")
    harness = Harness(tmp_path, server, env={"HERCULES_LLM_PROVIDER": "openrouter", "HERCULES_EGRESS": "local", "OPENROUTER_API_KEY": "sk-or-not-a-real-key-0000"})

    code, out = harness.run("--json")

    assert code == 1
    assert server.requests == []
    report = DoctorReport.model_validate_json(out)
    egress = next(check for check in report.checks if check.name == "egress")
    assert egress.status == "fail"
    assert "hybrid" in (egress.fix or "")


def test_doctor_hosted_endpoint_allowed_by_policy_never_prints_the_key(tmp_path: Path) -> None:
    secret = "sk-or-not-a-real-key-0000"
    server = FakeServer(kind="generic", models=["google/gemma-4-26b-a4b-it"], version=None)
    env = {"HERCULES_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": secret, "HERCULES_EGRESS": "hybrid"}

    code, out = Harness(tmp_path, server, env=env).run()

    assert code == 0
    assert secret not in out
    assert server.requests and server.requests[0].url.host == "openrouter.ai"
    assert server.requests[0].headers["authorization"] == f"Bearer {secret}"


def test_doctor_missing_api_key_fails_with_variable_name(tmp_path: Path) -> None:
    server = FakeServer(kind="generic")
    code, out = Harness(tmp_path, server, env={"HERCULES_LLM_PROVIDER": "openai", "HERCULES_EGRESS": "hybrid"}).run()

    assert code == 1
    assert "OPENAI_API_KEY" in out
    assert server.requests == []


def test_doctor_hides_credentials_in_base_url(tmp_path: Path) -> None:
    server = FakeServer(kind="generic", models=["gemma-4-26b-a4b"], version=None)
    env = {"HERCULES_LLM_PROVIDER": "openai-compatible", "HERCULES_LLM_BASE_URL": "http://user:hunter2@127.0.0.1:9000/v1"}

    code, out = Harness(tmp_path, server, env=env).run()

    assert code == 0
    assert "hunter2" not in out


def test_doctor_reports_unsupported_memory_as_a_warning(tmp_path: Path) -> None:
    code, out = Harness(tmp_path, FakeServer(kind="ollama", context_length=16384), memory_gib=8).run()

    assert code == 0
    assert "unsupported" in out
    assert "gemma4:e4b" in out


def test_doctor_settings_error_exits_1(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeServer(), env={"HERCULES_LLM_PROVIDER": "nonsense"})

    code, out = harness.run()

    assert code == 1
    assert "HERCULES_LLM_PROVIDER" in out
    assert harness.server.requests == []


@pytest.mark.parametrize(
    ("server", "expected_error"),
    [(FakeServer(kind="ollama"), None), (FakeServer(reachable=False), "ReachableFailed")],
)
def test_doctor_emits_doctor_completed_telemetry(tmp_path: Path, server: FakeServer, expected_error: str | None) -> None:
    harness = Harness(tmp_path, server)

    harness.run()

    [payload] = harness.telemetry.payloads
    assert payload["event"] == "doctor_completed"
    assert set(payload) <= ALLOWED_FIELDS
    assert payload["model_tier"] == "recommended"
    assert payload["llm_provider"] == "ollama"
    assert isinstance(payload["duration_ms"], int)
    assert payload.get("error_class") == expected_error


def test_doctor_respects_telemetry_opt_out(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeServer(kind="ollama"), env={"HERCULES_TELEMETRY": "0"})

    harness.run()

    assert harness.telemetry.payloads == []


def test_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    out = io.StringIO()

    assert main(["version"], stdout=out) == 0
    assert out.getvalue().startswith("Hercules ") and out.getvalue().rstrip().endswith(" by TestZeus")


def test_no_subcommand_prints_help_and_exits_2() -> None:
    out = io.StringIO()

    assert main([], stdout=out) == 2
    assert "doctor" in out.getvalue()


def test_help_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])

    assert caught.value.code == 0
    assert "doctor" in capsys.readouterr().out


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=False)


def test_cli_import_does_not_load_heavy_or_v1_modules() -> None:
    probe = (
        "import sys, json\n"
        "import testzeus_hercules.cli.main\n"
        "heavy = ('langchain', 'langchain_core', 'langchain_openai', 'langgraph', 'playwright', 'autogen')\n"
        "allowed = ('testzeus_hercules.cli', 'testzeus_hercules.kernel', 'testzeus_hercules.llm')\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in heavy or (m.startswith('testzeus_hercules.') and not m.startswith(allowed))]\n"
        "print(json.dumps(sorted(bad)))\n"
    )

    result = _run_python(probe)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_doctor_module_import_stays_light_too() -> None:
    probe = "import sys, json\nimport testzeus_hercules.cli.doctor\n" "print(json.dumps([m for m in sys.modules if m.split('.')[0] in ('langchain', 'langgraph', 'playwright', 'langchain_core')]))\n"

    result = _run_python(probe)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_console_script_is_registered() -> None:
    import tomllib

    scripts: dict[str, Any] = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    target: Callable[..., Any] = main
    assert scripts["hercules"] == "testzeus_hercules.cli.main:main"
    assert callable(target)


# --------------------------------------------------------------------------- decider


def test_doctor_without_a_key_says_runs_are_pure_and_how_to_add_one(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeServer(kind="ollama"))

    code, out = harness.run("--json")

    assert code == 0
    report = DoctorReport.model_validate_json(out)
    assert report.decider is not None and report.decider.mode == "pure" and report.decider.key_source == "none"
    check = next(check for check in report.checks if check.name == "decider")
    assert check.status == "warn" and "hercules setup" in (check.fix or "")


def test_doctor_with_a_key_file_reports_hybrid_and_zdr_but_never_the_key(tmp_path: Path) -> None:
    from testzeus_hercules.cli.keys import write_key_file

    secret = "sk-or-test-not-a-real-key-doctor"
    harness = Harness(tmp_path, FakeServer(kind="ollama"))
    write_key_file(harness.home, secret)

    code, out = harness.run()

    assert code == 0
    assert "hybrid: JEV typesafe/jev-1.14 via OpenRouter (zero data retention requested" in out
    assert "openrouter.env" in out
    key_file = harness.home / "openrouter.env"
    assert f"[auto: OPENROUTER_API_KEY is read from {key_file} and egress policy is" in out
    assert "OPENROUTER_API_KEY is set" not in out, "the key came from the key file, not the environment"
    assert secret not in out
    _, as_json = harness.run("--json")
    report = DoctorReport.model_validate_json(as_json)
    assert report.decider is not None and (report.decider.mode, report.decider.key_source, report.decider.zdr) == ("hybrid", "file", True)
    assert report.decider.reason.startswith(f"auto: OPENROUTER_API_KEY is read from {key_file}")
    assert secret not in as_json


def test_doctor_reports_an_unreachable_clef_server_as_a_warning(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeServer(kind="ollama"), env={"HERCULES_CLEF_URL": "http://127.0.0.1:30000"})

    code, out = harness.run("--json")

    assert code == 0
    report = DoctorReport.model_validate_json(out)
    clef = next(check for check in report.checks if check.name == "clef")
    assert clef.status == "warn" and "the local model decides" in clef.detail
    assert report.decider is not None and report.decider.clef_reachable is False


def test_doctor_probes_with_the_request_params_and_names_them(tmp_path: Path) -> None:
    server = FakeServer(kind="generic", models=["qwen-test"], reasoning=True, version=None)
    env = {"HERCULES_LLM_PROVIDER": "openai-compatible", "HERCULES_LLM_BASE_URL": "http://127.0.0.1:9000/v1", "HERCULES_LLM_MODEL": "qwen-test"}

    (tmp_path / "plain").mkdir()
    (tmp_path / "tuned").mkdir()
    plain = Harness(tmp_path / "plain", server, env=env)
    _, without = plain.run("--no-cache")
    tuned = Harness(tmp_path / "tuned", server, env={**env, "HERCULES_LLM_REQUEST_PARAMS": '{"reasoning_effort": "none"}'})
    code, out = tuned.run("--no-cache")

    assert "not measured" in without and "reasoning_effort" in without
    assert code == 0 and "not measured" not in out
    assert 'request params  {"reasoning_effort": "none"}' in out
    assert any(body.get("reasoning_effort") == "none" for body in server.chat_requests())


def test_doctor_reports_the_appium_server_and_cloud_credentials_without_failing(tmp_path: Path) -> None:
    server = FakeServer()
    harness = Harness(tmp_path, server, env={"BROWSERSTACK_USERNAME": "user-5c", "BROWSERSTACK_ACCESS_KEY": "key-5d"})
    seen: list[str] = []
    harness.deps = DoctorDeps(
        detect_memory=lambda: 32 * 1024**3, make_client=server.client, make_telemetry=harness._telemetry, appium_status=lambda url: seen.append(url) or {"build": {"version": "2.15.0"}}
    )
    code, out = harness.run("--json")
    report = DoctorReport.model_validate_json(out)
    checks = {check.name: check for check in report.checks}
    assert seen == ["http://127.0.0.1:4723"] and checks["appium"].status == "ok" and "2.15.0" in checks["appium"].detail
    assert "browserstack: credentials set" in checks["device_cloud"].detail and "saucelabs: not set (SAUCE_USERNAME, SAUCE_ACCESS_KEY)" in checks["device_cloud"].detail
    assert "user-5c" not in out and "key-5d" not in out
    harness.deps = DoctorDeps(detect_memory=lambda: 32 * 1024**3, make_client=server.client, make_telemetry=harness._telemetry, appium_status=lambda url: None)
    code_without, out_without = harness.run("--json")
    assert code_without == code and {check.name: check for check in DoctorReport.model_validate_json(out_without).checks}["appium"].status == "skipped"
