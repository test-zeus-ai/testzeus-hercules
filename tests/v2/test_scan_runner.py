"""One security scan (scan.runner, scan.nuclei, scan.scope) with a fake nuclei: the command, scope, redaction, findings, evidence.

Ported from the v1 scan tests (log redaction, path confinement of the spec, redacted
stdout/stderr), plus the v2 rules: the target is the base URL only, intrusive templates
need an explicit opt-in, no update checks or out-of-band callbacks, a time limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.act.files import PathConfinementError
from testzeus_hercules.cases.security import SecurityScanResult
from testzeus_hercules.scan.install import ScannerMissing
from testzeus_hercules.scan.nuclei import parse_findings
from testzeus_hercules.scan.runner import FINDINGS_FILE, SCAN_FILE, SUMMARY_FILE, ScanRequest, render_summary, scan
from testzeus_hercules.scan.scope import ScopeError, api_urls, origin, url_target

from tests.v2.scan_fakes import FakeNuclei, record

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
SECRET = "hunter2-secret"
SPEC = """openapi: 3.0.3
info: {title: t, version: "1"}
servers:
  - url: http://localhost/v1
paths:
  /pets: {get: {responses: {"200": {description: ok}}}}
  /pets/{petId}:
    get:
      parameters: [{name: petId, in: path, required: true, schema: {type: integer, example: 7}}]
      responses: {"200": {description: ok}}
"""


@pytest.fixture
def fake(tmp_path: Path) -> FakeNuclei:
    return FakeNuclei(tmp_path / "bin")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def _scan(project: Path, fake: FakeNuclei, request: ScanRequest, **kwargs: Any) -> SecurityScanResult:
    return asyncio.run(scan(request, roots=(project,), out_dir=project / "out", env={}, binary=fake.path, **kwargs))


# ── the command ─────────────────────────────────────────────────────────────


def test_a_url_scan_runs_the_tags_on_the_base_url_with_safe_defaults(project: Path, fake: FakeNuclei) -> None:
    _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", tags=("misconfig", "exposure")))
    [argv] = fake.scans()
    assert argv[argv.index("-u") + 1] == "http://127.0.0.1:8000"
    assert argv[argv.index("-tags") + 1] == "misconfig,exposure"
    for flag in ("-duc", "-ni", "-jsonl", "-or"):
        assert flag in argv  # no update check, no public out-of-band servers, findings as JSON lines without raw traffic
    assert argv[argv.index("-pt") + 1] == "http,ssl"
    assert argv[argv.index("-rl") + 1] == "50"
    excluded = argv[argv.index("-etags") + 1].split(",")
    assert {"intrusive", "dos", "fuzz", "dast", "bruteforce", "default-login"} <= set(excluded)


def test_no_tags_means_cve_exposure_and_misconfig(project: Path, fake: FakeNuclei) -> None:
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000"))
    assert result.tags == ["cve", "exposure", "misconfig"]


def test_intrusive_tags_are_refused_without_the_opt_in_and_nothing_runs(project: Path, fake: FakeNuclei) -> None:
    with pytest.raises(ScopeError, match="intrusive"):
        _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", tags=("cve", "dos")))
    assert fake.calls() == []


def test_with_the_opt_in_intrusive_tags_run_and_are_not_excluded(project: Path, fake: FakeNuclei) -> None:
    _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", tags=("intrusive", "sqli"), intrusive=True, exclude_tags=("wordpress",)))
    [argv] = fake.scans()
    assert argv[argv.index("-etags") + 1] == "wordpress"
    assert argv[argv.index("-itags") + 1] == "intrusive"


def test_headers_reach_the_scanner_unchanged_but_are_masked_in_logs_and_evidence(project: Path, fake: FakeNuclei, caplog: pytest.LogCaptureFixture) -> None:
    headers = (("Authorization", f"Bearer {JWT}"), ("X-Custom", "plain-secret"), ("Authorization", f"JWT {JWT}"))
    with caplog.at_level(logging.INFO, logger="testzeus_hercules.scan.nuclei"):
        _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", headers=headers))
    [argv] = fake.scans()
    assert f"Authorization: Bearer {JWT}" in argv and "X-Custom: plain-secret" in argv
    logged = caplog.text
    assert "running the security scanner" in logged
    assert JWT not in logged and "plain-secret" not in logged
    record_ = json.loads((project / "out" / SCAN_FILE).read_text())
    assert JWT not in record_["command"] and "Authorization: [REDACTED]" in record_["command"] and "X-Custom: [REDACTED]" in record_["command"]


def test_url_credentials_of_the_target_are_masked_in_the_log_and_result(project: Path, fake: FakeNuclei, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="testzeus_hercules.scan.nuclei"):
        result = _scan(project, fake, ScanRequest(base_url="https://user:hunter2@example.test/path?token=abc123"))
    assert "https://user:hunter2@example.test/path?token=abc123" in fake.scans()[0]
    assert "hunter2" not in caplog.text and "abc123" not in caplog.text
    assert "hunter2" not in result.target and "abc123" not in result.target


# ── scope ──────────────────────────────────────────────────────────────────


def test_a_path_under_the_base_url_is_scanned_and_another_origin_is_refused(project: Path, fake: FakeNuclei) -> None:
    _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000/shop", path="/api"))
    assert fake.scans()[0][fake.scans()[0].index("-u") + 1] == "http://127.0.0.1:8000/shop/api"
    for path in ("https://other.example.com/", "http://127.0.0.1:9999/", "file:///etc/passwd", "//other.example.com/x"):
        with pytest.raises(ScopeError, match="refused"):
            _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", path=path))
    assert len(fake.scans()) == 1


def test_a_scan_needs_a_base_url(project: Path, fake: FakeNuclei) -> None:
    with pytest.raises(ScopeError, match="base URL"):
        _scan(project, fake, ScanRequest())
    with pytest.raises(ScopeError):
        url_target("ftp://example.test")


def test_origins_make_default_ports_explicit() -> None:
    assert origin("https://Example.test/x") == origin("https://example.test:443/y") == "https://example.test:443"
    assert origin("http://[::1]:8000/") == "http://[::1]:8000"


def test_an_openapi_spec_inside_the_project_is_scanned_at_the_base_url(project: Path, fake: FakeNuclei) -> None:
    (project / "spec.yaml").write_text(SPEC, encoding="utf-8")
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8001", openapi="spec.yaml"))
    [argv] = fake.scans()
    assert "-im" not in argv  # by default the API's URLs are scanned like any URL
    urls_file = argv[argv.index("-l") + 1]
    assert result.input == "openapi" and result.target == "spec.yaml"
    assert not Path(urls_file).exists()  # the working copy is gone after the scan


def test_an_intrusive_openapi_scan_fuzzes_the_spec_scoped_to_the_base_url(project: Path, fake: FakeNuclei, tmp_path: Path) -> None:
    (project / "spec.yaml").write_text(SPEC, encoding="utf-8")
    seen: dict[str, Any] = {}

    async def runner(command: Any, output: Path, timeout_s: float, redact: Any) -> Any:
        from testzeus_hercules.scan.nuclei import run_nuclei

        spec = Path(command[command.index("-l") + 1])
        seen["spec"] = json.loads(spec.read_text())
        return await run_nuclei(command, output, timeout_s, redact)

    _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8001", openapi="spec.yaml", tags=("sqli",), intrusive=True), runner=runner)
    [argv] = fake.scans()
    assert argv[argv.index("-im") + 1] == "openapi" and "-dast" in argv
    assert seen["spec"]["servers"] == [{"url": "http://127.0.0.1:8001/v1"}]


def test_api_urls_fill_path_parameters_from_examples() -> None:
    import yaml

    document = yaml.safe_load(SPEC)
    assert api_urls(document, ["http://127.0.0.1:8001/v1"]) == ["http://127.0.0.1:8001/v1/", "http://127.0.0.1:8001/v1/pets", "http://127.0.0.1:8001/v1/pets/7"]


def test_an_openapi_spec_without_base_url_needs_absolute_servers(project: Path, fake: FakeNuclei) -> None:
    (project / "relative.yaml").write_text(SPEC.replace("http://localhost/v1", "/v1"), encoding="utf-8")
    with pytest.raises(ScopeError, match="no absolute server"):
        _scan(project, fake, ScanRequest(openapi="relative.yaml"))
    (project / "absolute.yaml").write_text(SPEC, encoding="utf-8")
    _scan(project, fake, ScanRequest(openapi="absolute.yaml"))
    assert len(fake.scans()) == 1


def test_an_openapi_spec_outside_the_project_is_refused_before_anything_runs(project: Path, fake: FakeNuclei, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "spec.yaml"
    outside.write_text(SPEC, encoding="utf-8")
    with pytest.raises(PathConfinementError):
        _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8001", openapi=str(outside)))
    assert fake.calls() == []


def test_spec_path_traversal_and_secret_files_are_refused(project: Path, fake: FakeNuclei) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    for name in ("../../../../etc/passwd", ".env"):
        with pytest.raises(PathConfinementError):
            _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8001", openapi=name))
    assert fake.calls() == []


def test_a_missing_scanner_says_how_to_install_it_after_the_scope_check(project: Path) -> None:
    with pytest.raises(ScannerMissing, match="hercules scan --install"):
        asyncio.run(scan(ScanRequest(base_url="http://127.0.0.1:8000"), roots=(project,), out_dir=project / "out", env={"PATH": str(project)}))
    with pytest.raises(ScopeError):
        asyncio.run(scan(ScanRequest(base_url="http://127.0.0.1:8000", path="https://elsewhere.test/"), roots=(project,), out_dir=project / "out", env={"PATH": str(project)}))


# ── output, findings and evidence ───────────────────────────────────────────


def test_stdout_stderr_and_findings_are_redacted(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("jwt-token-exposure", "high", f"http://127.0.0.1:8000/?token={SECRET}", extracted=[f"Bearer {SECRET}"])).fails(1, f"failed: sent Authorization: Bearer {SECRET} to target\n")
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", headers=(("Authorization", f"Bearer {SECRET}"),)))
    scan_record = (project / "out" / SCAN_FILE).read_text()
    for text in (scan_record, (project / "out" / FINDINGS_FILE).read_text(), (project / "out" / SUMMARY_FILE).read_text(), result.model_dump_json()):
        assert SECRET not in text
    assert result.error is not None and result.error.startswith("nuclei failed (exit 1)")


def test_only_credential_headers_are_scrubbed_from_the_output(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("json-leak", "low", "https://t.test/api", extracted=["application/json", "Mozilla/5.0 (X11; Linux)", f"auth {SECRET}"]))
    headers = (("Accept", "application/json"), ("User-Agent", "Mozilla/5.0 (X11; Linux)"), ("Authorization", f"Bearer {SECRET}"), ("X-Api-Key", "k-12345"))
    result = _scan(project, fake, ScanRequest(base_url="https://t.test", headers=headers))
    extracted = result.findings[0].extracted
    assert extracted[:2] == ["application/json", "Mozilla/5.0 (X11; Linux)"]
    assert SECRET not in extracted[2]


def test_findings_are_ordered_counted_and_gated_by_fail_on(project: Path, fake: FakeNuclei) -> None:
    fake.findings(
        record("http-missing-security-headers", "info", "http://127.0.0.1:8000", matcher="x-frame-options"),
        record("http-missing-security-headers", "info", "http://127.0.0.1:8000", matcher="x-frame-options"),  # a duplicate
        record("exposed-env", "high", "http://127.0.0.1:8000/.env", extracted=["DB_HOST"]),
        record("weak-cipher", "unknown", "127.0.0.1:443"),
    )
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", fail_on="medium"))
    assert [finding.template_id for finding in result.findings] == ["exposed-env", "http-missing-security-headers", "weak-cipher"]
    assert result.counts.high == 1 and result.counts.info == 2 and result.counts.findings == 3
    assert [finding.template_id for finding in result.failing()] == ["exposed-env"]
    lines = [json.loads(line) for line in (project / "out" / FINDINGS_FILE).read_text().splitlines()]
    assert len(lines) == 3 and lines[0]["extracted"] == ["DB_HOST"]
    summary = (project / "out" / SUMMARY_FILE).read_text()
    assert "FAIL [high] exposed-env http://127.0.0.1:8000/.env" in summary and "below medium:" in summary


def test_a_scan_that_runs_too_long_is_stopped_and_keeps_its_findings(project: Path, fake: FakeNuclei) -> None:
    fake.hangs(30)
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000", timeout_s=1.0))
    assert result.error is not None and "stopped after 1 s" in result.error
    assert result.duration_ms < 15_000


def test_missing_templates_say_how_to_get_them(project: Path, fake: FakeNuclei) -> None:
    fake.fails(1, "[FTL] Could not run nuclei: no templates provided for scan\n")
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000"))
    assert result.error is not None and "no templates" in result.error and "--update-templates" in result.error


def test_parse_findings_skips_lines_that_are_not_findings() -> None:
    text = "\n".join(["not json", "{}", json.dumps({"info": {}}), json.dumps(record("a", "critical", "http://x.test/"))])
    findings = parse_findings(text, lambda value: value)
    assert [(finding.template_id, finding.severity) for finding in findings] == [("a", "critical")]


def test_render_summary_groups_findings_below_the_gate() -> None:
    result = SecurityScanResult(
        target="http://127.0.0.1:8000",
        tags=["misconfig"],
        findings=[{"template_id": "h", "severity": "info", "matched_at": f"http://127.0.0.1:8000/{n}", "matcher": m} for n in range(3) for m in ("a", "b")],  # type: ignore[misc]
    )
    text = render_summary(result)
    assert "no finding at or above medium" in text
    assert "[info] h (2 matchers: a, b) on 3 URLs, e.g. http://127.0.0.1:8000/0" in text


def test_a_target_the_scanner_gave_up_on_is_an_error_never_a_clean_scan(project: Path, fake: FakeNuclei) -> None:
    fake.fails(
        0,
        '[INF] Skipped 127.0.0.1:5814 from target list as found unresponsive permanently: cause="port closed or filtered"\n'
        "[INF] Skipped 127.0.0.1:8000 from target list as found unresponsive 30 times\n",
    )
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000"))
    assert result.findings == []
    assert result.error is not None and "stopped answering" in result.error and "127.0.0.1:8000" in result.error
    assert "-silent" not in fake.scans()[0]


def test_other_ports_the_templates_probe_do_not_make_the_scan_incomplete(project: Path, fake: FakeNuclei) -> None:
    fake.fails(0, '[INF] Skipped 127.0.0.1:5814 from target list as found unresponsive permanently: cause="port closed or filtered"\n')
    result = _scan(project, fake, ScanRequest(base_url="http://127.0.0.1:8000"))
    assert result.error is None
