"""Tests for ``hercules run``."""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cli.main import main
from testzeus_hercules.cli.run import RunCommandDeps, load_case
from testzeus_hercules.engine.factory import FactoryDeps
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.browser import FIXTURES_DIR, require_chromium
from tests.v2.engine_fixtures.fakes import DomPerceiver, FakeActions, FakePerceiver, ScriptedInterpreter, clicks, opens
from tests.v2.fixtures.fake_model import FakeModel, action
from tests.v2.test_engine_run_graph import FakeLauncher
from tests.v2.test_llm_fakes import FakeServer

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = "gemma-4-26b-a4b-it-q4.gguf"
# The plan's coverage audit is a decision of its own; these tests count the steps' calls (the audit has its own tests).
LLM_ENV = {"HERCULES_LLM_PROVIDER": "llamacpp", "HERCULES_LLM_MODEL": "gemma-4-26b-a4b", "HERCULES_PLAN_COVERAGE": "0"}

FEATURE = """\
Feature: Sign in
  Scenario: Registered user signs in
    Given I open "{url}"
    When I fill "alice" in "Username"
    And I fill "s3cret" in "Password"
    And I click the "Sign in" button
    Then I should see "{expected}"
"""


def model_server(model: FakeModel | None = None, *scripted: Any) -> FakeServer:
    """The local model server: a fake that reads steps and decides like a model would (see FakeModel)."""
    return FakeServer(kind="generic", models=[MODEL], version=None, scripted=list(scripted), responder=(model or FakeModel()).response)


class Harness:
    """Runs ``main`` with fakes (or, with ``real_browser``, Chromium and the DOM perceiver) and a fake local model server."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        real_browser: bool = False,
        launcher: Any = None,
        env: dict[str, str] | None = None,
        server: FakeServer | None = None,
        actions: Any = None,
        perceiver: Any = None,
        model: FakeModel | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.project = tmp_path / "project"
        self.project.mkdir(exist_ok=True)
        self.env = {"HERCULES_HOME": str(tmp_path / "home"), **LLM_ENV, **(env or {})}
        self.transport = RecordingTransport()
        self.model = model or FakeModel()
        self.server = server or model_server(self.model)
        if self.server.responder is None and self.server.reachable:
            self.server.responder = self.model.response
        self.err = io.StringIO()
        perceiver = perceiver or (DomPerceiver() if real_browser else FakePerceiver())
        self.deps = RunCommandDeps(
            make_perceiver=lambda: perceiver,
            launcher=None if real_browser else (launcher or FakeLauncher()),
            actions=None if real_browser else (actions or FakeActions(text="Welcome, alice")),
            make_telemetry=lambda settings, paths: TelemetryClient(settings, paths, transport=self.transport),
            factory=FactoryDeps(make_http=self.server.client, detect_memory=lambda: 32 * 1024**3),
        )

    def feature(self, url: str, expected: str) -> Path:
        """Write the sign-in feature and script how the model reads its steps."""
        self.model.add(f'I open "{url}"', action("navigate", url=url))
        self.model.add('I fill "s3cret" in "Password"', action("fill", target="Password", value="s3cret"))
        self.model.add(f'I should see "{expected}"', action("assert_text", expected=expected))
        return self.write("login.feature", FEATURE.format(url=url, expected=expected))

    def write(self, name: str, text: str) -> Path:
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def run(self, *args: str) -> tuple[int, str]:
        """Run ``hercules run`` (auto mode without a JEV key: pure, with the fake local model)."""
        out = io.StringIO()
        code = main(["run", *args, "--project", str(self.project)], env=self.env, run_deps=self.deps, stdout=out, stderr=self.err)
        return code, out.getvalue()


def test_feature_run_passes_with_fakes(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = h.feature("https://app.test/login", "Welcome")

    code, out = h.run(str(path))

    assert code == 0, out
    assert "PASSED" in out
    assert "case registered-user-signs-in" in out
    assert "Evidence:" in out


def test_failed_run_exits_1_and_json_output(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = h.feature("https://app.test/login", "Goodbye")

    code, out = h.run(str(path), "--json")

    assert code == 1
    payload = json.loads(out)
    assert payload["status"] == "failed"
    assert payload["steps"][-1]["status"] == "failed"


def test_json_case_and_stub_decider_with_an_injected_step_reader(tmp_path: Path) -> None:
    from dataclasses import replace

    h = Harness(tmp_path)
    h.deps = replace(h.deps, factory=replace(h.deps.factory, test_interpreter=ScriptedInterpreter({"Open /login": opens("/login"), 'click "Sign in"': clicks("Sign in")})))
    case = {"id": "c1", "title": "Login", "environment": {"base_url": "https://app.test"}, "steps": [{"action": "Open /login"}, {"action": 'click "Sign in"'}]}
    path = h.write("case.json", json.dumps(case))

    code, out = h.run(str(path), "--decider", "stub", "--json")

    assert code == 0, out
    payload = json.loads(out)
    assert payload["engines"]["decider"] == "stub" and payload["engines"]["mode"] == "test"
    assert h.server.requests == [], "a stub run contacts no model"


def test_a_stub_run_without_a_step_reader_reads_nothing_and_errors(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = h.write("case.json", json.dumps({"id": "c1", "title": "Login", "environment": {"base_url": "https://app.test"}, "steps": [{"action": "Open /login"}]}))

    code, out = h.run(str(path), "--decider", "stub", "--json")

    assert code == 2
    assert "no model is configured to read this step" in json.loads(out)["steps"][0]["actual"]


def test_without_a_model_server_the_run_exits_2_pointing_to_setup_and_doctor(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=FakeServer(kind="generic", reachable=False))
    path = h.feature("https://app.test/login", "Welcome")

    code, out = h.run(str(path))

    assert code == 2
    assert "not reachable" in out and "hercules doctor" in out and "hercules setup" in out
    assert "no coded fallback" in out


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("bad.json", "{not json", "invalid test case"),
        ("bad.json", json.dumps({"id": "x", "title": "t", "steps": []}), "invalid test case"),
        ("bad.feature", "Feature: F\n", "exactly one scenario"),
        ("case.txt", "hello", "unsupported case file"),
    ],
)
def test_bad_case_files_exit_2(tmp_path: Path, name: str, text: str, message: str) -> None:
    h = Harness(tmp_path)
    path = h.write(name, text)

    code, out = h.run(str(path))

    assert code == 2
    assert message in out


def test_missing_case_file_exits_2(tmp_path: Path) -> None:
    code, out = Harness(tmp_path).run(str(tmp_path / "nope.json"))

    assert code == 2
    assert "not found" in out


def test_draft_case_needs_allow_draft(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    case = {"id": "c1", "title": "Login", "status": "draft", "environment": {"base_url": "https://app.test"}, "steps": [{"action": "Open /login"}]}
    path = h.write("case.json", json.dumps(case))

    code, out = h.run(str(path))
    assert code == 2
    assert "is a draft" in out and "--allow-draft" in out

    code, out = h.run(str(path), "--allow-draft")
    assert code == 0, out


def _saved_env(h: Harness, name: str, base_url: str) -> None:
    envs = h.project / ".hercules" / "envs"
    envs.mkdir(parents=True, exist_ok=True)
    (envs / f"{name}.json").write_text(json.dumps({"name": name, "base_url": base_url}), encoding="utf-8")


def _opened_base(h: Harness) -> str | None:
    return [call for call in h.deps.actions.calls if call[0] == "open_url"][-1][2]


def test_environment_ref_is_applied_from_the_saved_environment(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _saved_env(h, "staging", "https://staging.app.test")
    case = {"id": "c1", "title": "Login", "environment": {"ref": "staging"}, "steps": [{"action": "Open /login"}]}
    path = h.write("case.json", json.dumps(case))

    code, out = h.run(str(path), "--json")

    assert code == 0, out
    assert _opened_base(h) == "https://staging.app.test"


def test_env_flag_overrides_ref_and_base_url_overrides_both(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    _saved_env(h, "staging", "https://staging.app.test")
    _saved_env(h, "prod", "https://prod.app.test")
    path = h.write("case.json", json.dumps({"id": "c1", "title": "Login", "environment": {"ref": "staging"}, "steps": [{"action": "Open /login"}]}))

    code, out = h.run(str(path), "--json", "--env", "prod")
    assert code == 0, out
    assert _opened_base(h) == "https://prod.app.test"

    code, out = h.run(str(path), "--json", "--env", "prod", "--base-url", "http://127.0.0.1:9000")
    assert code == 0, out
    assert _opened_base(h) == "http://127.0.0.1:9000"


def test_unknown_environment_ref_exits_2(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = h.write("case.json", json.dumps({"id": "c1", "title": "Login", "environment": {"ref": "missing"}, "steps": [{"action": "Open /login"}]}))

    code, out = h.run(str(path))

    assert code == 2
    assert "unknown environment 'missing'" in out and "--base-url" in out


def test_browser_failure_exits_2(tmp_path: Path) -> None:
    h = Harness(tmp_path, launcher=FakeLauncher(error=RuntimeError("no browser")))
    path = h.feature("https://app.test/login", "Welcome")

    code, out = h.run(str(path))

    assert code == 2
    assert "ERROR" in out


def test_load_case_reads_both_formats(tmp_path: Path) -> None:
    feature = tmp_path / "a.feature"
    feature.write_text(FEATURE.format(url="https://x.test/", expected="Hi"), encoding="utf-8")
    assert load_case(feature).steps[-1].action == "verify"


def test_main_import_stays_light() -> None:
    code = (
        "import sys, testzeus_hercules.cli.main; "
        "heavy = [m for m in ('langgraph', 'playwright', 'testzeus_hercules.engine.run_graph', 'testzeus_hercules.cli.run') if m in sys.modules]; "
        "print(heavy); sys.exit(1 if heavy else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_help_lists_run() -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit):
        main(["run", "--help"], stdout=out)


def test_cli_end_to_end_with_real_browser(tmp_path: Path) -> None:
    chromium = require_chromium()
    h = Harness(tmp_path, real_browser=True, env={"HERCULES_CHROMIUM": chromium})
    page = h.project / "site" / "login.html"  # file: URLs open only inside the project
    page.parent.mkdir()
    for name in ("login.html", "welcome.html"):
        shutil.copy(FIXTURES_DIR / name, page.parent / name)
    path = h.feature(page.as_uri(), "Welcome")

    code, out = h.run(str(path))

    assert code == 0, out
    run_dirs = list((h.project / ".hercules" / "runs").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "run.json").exists()
    assert (run_dirs[0] / "steps" / "2" / "som.jpg").exists()


@pytest.mark.parametrize(("page", "fail_on", "expected_code"), [("a11y.html", "serious", 1), ("clean.html", "serious", 0), ("a11y.html", None, 0)])
def test_a11y_flag_audits_pages_with_a_real_browser(tmp_path: Path, page: str, fail_on: str | None, expected_code: int) -> None:
    from tests.v2.engine_fixtures.browser import serve_fixtures

    chromium = require_chromium()
    h = Harness(tmp_path, real_browser=True, env={"HERCULES_CHROMIUM": chromium})
    with serve_fixtures(REPO_ROOT / "tests" / "v2" / "fixtures" / "quality") as base:
        h.model.add(f"Open {base}/{page}", action("navigate", url=f"{base}/{page}"))
        path = h.write("case.json", json.dumps({"id": "audit", "title": "Audit", "steps": [{"action": f"Open {base}/{page}"}]}))
        code, out = h.run(str(path), "--a11y", "--json", *(("--quality-fail-on", fail_on) if fail_on else ()))

    assert code == expected_code, out
    quality = json.loads(out)["quality"]
    assert len(quality["pages"]) == 1
    if page == "a11y.html":
        assert quality["counts"]["serious"] + quality["counts"]["critical"] > 0


def test_quality_fail_on_needs_a11y_or_visual(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = h.write("case.json", json.dumps({"id": "c1", "title": "T", "environment": {"base_url": "https://app.test"}, "steps": [{"action": "Open /"}]}))

    with pytest.raises(SystemExit, match="needs --a11y or --visual"):
        h.run(str(path), "--quality-fail-on", "serious")


def test_worker_accepts_the_quality_flags() -> None:
    from testzeus_hercules.cli.main import build_parser

    args = build_parser().parse_args(["worker", "--run-id", "r1", "--a11y", "--visual", "--quality-fail-on", "minor"])

    assert (args.a11y, args.visual, args.quality_fail_on) == (True, True, "minor")
