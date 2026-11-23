"""``hercules scan`` with a fake nuclei: target, headers from the environment, exit codes, JSON, install and the doctor check."""

from __future__ import annotations

import dataclasses
import io
import json
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cli.main import main
from testzeus_hercules.scan import install
from testzeus_hercules.scan.install import ScannerMissing

from tests.v2.scan_fakes import FakeNuclei, record

TOKEN = "cli-secret-token-7788"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def fake(tmp_path: Path) -> FakeNuclei:
    return FakeNuclei(tmp_path / "bin")


def _env(project: Path, fake: FakeNuclei | None = None, **extra: str) -> dict[str, str]:
    env = {"HERCULES_HOME": str(project.parent / "home"), "HERCULES_TELEMETRY": "0", "PATH": str(project), **extra}
    if fake is not None:
        env["HERCULES_NUCLEI"] = str(fake.path)
    return env


def _scan(project: Path, env: dict[str, str], *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["scan", "--project", str(project), *args], env=env, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_a_clean_scan_exits_0_and_writes_its_evidence(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("http-missing-security-headers", "info", "http://127.0.0.1:8000", matcher="x-frame-options"))
    code, out, err = _scan(project, _env(project, fake), "--base-url", "http://127.0.0.1:8000", "--tags", "misconfig,exposure")
    assert code == 0, err
    assert "Security scan of http://127.0.0.1:8000" in out and "no finding at or above medium" in out
    [scan_dir] = (project / ".hercules" / "scans").iterdir()
    assert sorted(path.name for path in scan_dir.iterdir()) == ["findings.jsonl", "scan.json", "summary.txt"]
    assert fake.scans()[0][fake.scans()[0].index("-tags") + 1] == "misconfig,exposure"


def test_findings_at_fail_on_exit_1_and_json_prints_the_result(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("exposed-env", "high", "http://127.0.0.1:8000/.env"))
    code, out, _ = _scan(project, _env(project, fake), "--base-url", "http://127.0.0.1:8000", "--fail-on", "high", "--json")
    assert code == 1
    result = json.loads(out)
    assert result["counts"]["high"] == 1 and result["fail_on"] == "high" and len(result["evidence"]) == 3


def test_header_placeholders_read_environment_variables_and_values_are_never_printed(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("echo", "info", "http://127.0.0.1:8000", extracted=[TOKEN]))
    env = _env(project, fake, APP_TOKEN=TOKEN)
    code, out, err = _scan(project, env, "--base-url", "http://127.0.0.1:8000", "--header", "Authorization: Bearer {{APP_TOKEN}}")
    assert code == 0, err
    assert f"Authorization: Bearer {TOKEN}" in fake.scans()[0]
    assert TOKEN not in out + err
    for path in (project / ".hercules" / "scans").rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text()
    code, _, err = _scan(project, _env(project, fake), "--base-url", "http://127.0.0.1:8000", "--header", "Authorization: Bearer {{MISSING_TOKEN}}")
    assert code == 2 and "MISSING_TOKEN is not set" in err


def test_the_target_comes_from_a_saved_environment_and_other_origins_are_refused(project: Path, fake: FakeNuclei) -> None:
    envs = project / ".hercules" / "envs"
    envs.mkdir(parents=True)
    (envs / "local.json").write_text(json.dumps({"name": "local", "base_url": "http://127.0.0.1:8000"}), encoding="utf-8")
    code, _, err = _scan(project, _env(project, fake), "--env", "local", "--path", "/admin")
    assert code == 0, err
    assert "http://127.0.0.1:8000/admin" in fake.scans()[0]
    code, _, err = _scan(project, _env(project, fake), "--env", "local", "--base-url", "https://elsewhere.example.com")
    assert code == 2 and "refused" in err
    code, _, err = _scan(project, _env(project, fake), "--env", "local", "--path", "https://elsewhere.example.com/")
    assert code == 2 and "refused" in err
    code, _, err = _scan(project, _env(project, fake), "--env", "nope")
    assert code == 2 and "unknown environment" in err
    assert len(fake.scans()) == 1


def test_usage_problems_exit_2(project: Path, fake: FakeNuclei) -> None:
    code, _, err = _scan(project, _env(project, fake))
    assert code == 2 and "--env NAME or --base-url URL" in err
    code, _, err = _scan(project, _env(project, fake), "--base-url", "http://127.0.0.1:8000", "--tags", "cve,intrusive")
    assert code == 2 and "--allow-intrusive" in err
    code, _, err = _scan(project, _env(project), "--base-url", "http://127.0.0.1:8000")
    assert code == 2 and "brew install nuclei" in err
    assert fake.scans() == []


def test_scanning_is_announced_only_when_the_scanner_is_there(project: Path, fake: FakeNuclei) -> None:
    code, _, err = _scan(project, _env(project), "--base-url", "http://127.0.0.1:8000")
    assert code == 2 and "brew install nuclei" in err and "scanning (tags" not in err, "no 'this can take minutes' before saying nuclei is missing"
    code, _, err = _scan(project, _env(project), "--base-url", "http://127.0.0.1:8000", "--path", "https://elsewhere.example.com/")
    assert code == 2 and "refused" in err and "brew install nuclei" not in err, "a target out of scope is still refused first"
    code, _, err = _scan(project, _env(project, fake), "--base-url", "http://127.0.0.1:8000")
    assert code == 0 and "scanning (tags: cve, exposure, misconfig; this can take minutes) ..." in err


def test_install_downloads_the_pinned_release_into_the_cache(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    def fake_install(cache_dir: Path, *, asset: Any = None, fetch: Any = None) -> Path:
        calls.append(cache_dir)
        return cache_dir / "nuclei" / install.NUCLEI_VERSION / "nuclei"

    monkeypatch.setattr(install, "install_nuclei", fake_install)
    out, err = io.StringIO(), io.StringIO()
    code = main(["scan", "--install", "--project", str(project)], env=_env(project), stdout=out, stderr=err)
    assert code == 0, err.getvalue()
    assert calls == [project.parent / "home" / "cache"]
    assert f"installed nuclei {install.NUCLEI_VERSION}" in out.getvalue() and "sha256 checked" in out.getvalue()


def test_update_templates_lets_nuclei_download_them(project: Path, fake: FakeNuclei, monkeypatch: pytest.MonkeyPatch) -> None:
    # nuclei's -duc (no update check) also stops -update-templates from installing anything: it must not be passed.
    import subprocess

    calls: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[INF] Successfully installed nuclei-templates\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    code, out, err = _scan(project, _env(project, fake), "--update-templates")
    assert code == 0, err
    assert calls == [[str(fake.path), "-update-templates", "-nc"]]
    assert "Successfully installed nuclei-templates" in out


def test_the_scan_command_is_listed_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "scan your app for security issues" in capsys.readouterr().out


def _scanner_check(tmp_path: Path, find_scanner: Any) -> dict[str, Any]:
    from tests.v2.test_cli_doctor import Harness
    from tests.v2.test_llm_fakes import FakeServer

    harness = Harness(tmp_path, FakeServer(kind="ollama"))
    harness.deps = dataclasses.replace(harness.deps, find_scanner=find_scanner)
    _, out = harness.run("--json")
    return next(check for check in json.loads(out)["checks"] if check["name"] == "scanner")


def test_doctor_reports_the_scanner_or_says_it_is_optional(tmp_path: Path) -> None:
    found = _scanner_check(tmp_path, lambda env, cache: (Path("/opt/bin/nuclei"), "3.11.1"))
    assert found["status"] == "ok" and found["detail"] == "nuclei 3.11.1 at /opt/bin/nuclei"

    def missing(env: Any, cache: Any) -> Any:
        raise ScannerMissing("not installed")

    absent = _scanner_check(tmp_path, missing)
    assert absent["status"] == "skipped" and "brew install nuclei" in absent["fix"]
