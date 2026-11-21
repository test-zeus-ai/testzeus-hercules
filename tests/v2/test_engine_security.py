"""Security steps end to end with a fake nuclei: the case model, the engine step, run.json, JUnit, events and export.

Security-only cases never start a browser (the launcher fails if asked). Findings at or
above ``fail_on`` fail the step with the findings as ``actual``; a missing scanner or a
refused target is an error; header secrets never reach evidence.
"""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from testzeus_hercules.cases.models import RunResult, SecurityScan, Step, TestCase
from testzeus_hercules.engine.checkpoint import ALLOWED_TYPES
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.export import export_run
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import ListEventSink, RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.fakes import LabelDecider
from tests.v2.scan_fakes import FakeNuclei, record

TOKEN = "s3cr3t-token-value-0042"


class NoBrowser:
    def open(self, **kwargs: Any) -> Any:
        raise AssertionError("a security-only case must not start a browser")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def fake(tmp_path: Path) -> FakeNuclei:
    return FakeNuclei(tmp_path / "bin")


def _case(*steps: Step, base_url: str = "http://127.0.0.1:8000", **fields: Any) -> TestCase:
    return TestCase(id="scan", title="Scan the app", environment={"base_url": base_url}, steps=list(steps), **fields)  # type: ignore[arg-type]


def _run(case: TestCase, project: Path, env: dict[str, str]) -> tuple[RunResult, Path, ListEventSink]:
    settings = Settings.load(project, env={"HERCULES_HOME": str(project.parent / "home")})
    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    events = ListEventSink()
    ctx = RunContext.create(settings, telemetry=telemetry, events=events)
    result = asyncio.run(run_case(case, ctx, RunDeps(decider=LabelDecider(), launcher=NoBrowser(), env=env)))
    return result, ctx.paths.run_dir(ctx.run_id), events


# ── the case model ──────────────────────────────────────────────────────────


def test_a_security_step_validates_its_scan() -> None:
    step = Step(action="Scan the app", security={"tags": ["cve", "xss"], "fail_on": "high"})  # type: ignore[arg-type]
    assert step.security is not None and step.security.fail_on == "high"
    assert SecurityScan().effective_tags() == ["cve", "exposure", "misconfig"]
    with pytest.raises(ValidationError, match="intrusive: true"):
        SecurityScan(tags=["dos"])
    assert SecurityScan(tags=["dos"], intrusive=True).tags == ["dos"]
    with pytest.raises(ValidationError, match="path or openapi"):
        SecurityScan(path="/api", openapi="spec.yaml")
    with pytest.raises(ValidationError):
        SecurityScan(tags=["Not A Tag"])
    with pytest.raises(ValidationError, match="one payload"):
        Step(action="x", security={}, api={"path": "/"})  # type: ignore[arg-type]


def test_header_placeholders_must_name_test_data() -> None:
    with pytest.raises(ValidationError, match="unknown placeholder"):
        _case(Step(action="Scan", security={"headers": {"Authorization": "Bearer {{API_TOKEN}}"}}))  # type: ignore[arg-type]
    case = _case(Step(action="Scan", security={"headers": {"Authorization": "Bearer {{token}}"}}), test_data={"token": {"secret": "API_TOKEN"}})  # type: ignore[arg-type]
    assert case.steps[0].security is not None


def test_the_run_state_types_include_the_scan_result_for_checkpoints() -> None:
    assert ("testzeus_hercules.cases.security", "SecurityScanResult") in ALLOWED_TYPES


# ── running ────────────────────────────────────────────────────────────────


def test_a_clean_scan_passes_without_a_browser_and_is_reported(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("http-missing-security-headers", "info", "http://127.0.0.1:8000", matcher="x-frame-options"))
    case = _case(Step(action="Scan the shop", expected="no security findings", security={"tags": ["misconfig"]}))  # type: ignore[arg-type]
    result, run_dir, events = _run(case, project, {"HERCULES_NUCLEI": str(fake.path)})
    assert result.status == "passed", result.summary_text()
    step = result.steps[0]
    assert step.actual is not None and "none at or above medium" in step.actual
    assert [Path(uri).name for uri in step.evidence] == ["summary.txt", "findings.jsonl", "scan.json"]
    assert all((run_dir / "steps" / "1" / Path(uri).name).is_file() for uri in step.evidence)
    assert result.security is not None and result.security.counts.info == 1 and result.security.scans[0].step == 1
    assert result.assertions.total == 1 and result.assertions.passed == 1
    assert "Security: 1 finding(s) in 1 scan(s) (1 info)" in result.summary_text()
    kinds = [(event.kind, event.data.get("action")) for event in events.events]
    assert ("action", "security_scan") in kinds and ("security_scan", None) in kinds
    assert result.automation is not None and result.automation.steps[0].kind == "security" and not result.automation.browser


def test_findings_at_or_above_fail_on_fail_the_step_and_name_the_findings(project: Path, fake: FakeNuclei) -> None:
    fake.findings(
        record("exposed-env-file", "high", "http://127.0.0.1:8000/.env", name="Exposed .env"),
        record("http-missing-security-headers", "info", "http://127.0.0.1:8000"),
    )
    case = _case(
        Step(action="Scan the shop", security={"tags": ["exposure"], "fail_on": "high"}),  # type: ignore[arg-type]
        Step(action="Never runs", security={"tags": ["cve"]}),  # type: ignore[arg-type]
    )
    result, run_dir, _ = _run(case, project, {"HERCULES_NUCLEI": str(fake.path)})
    assert result.status == "failed"
    first = result.steps[0]
    assert first.status == "failed"
    assert first.actual == "1 finding(s) at or above high: [high] exposed-env-file http://127.0.0.1:8000/.env"
    assert result.steps[1].status == "skipped"
    assert result.failure is not None and result.failure.category == "product_bug"
    suite = ElementTree.parse(run_dir / "junit.xml").getroot()
    properties = {item.get("name"): item.get("value") for item in suite.iter("property")}
    assert properties["security_high"] == "1" and properties["security_findings"] == "2"
    failure = suite.find("testcase/failure")
    assert failure is not None and "exposed-env-file" in (failure.get("message") or "")
    system_out = suite.findtext("system-out") or ""
    assert "security (step 1): 2 finding(s)" in system_out and "[high] exposed-env-file" in system_out
    stored = RunResult.model_validate_json((run_dir / "run.json").read_text())
    assert stored.security is not None and stored.security.counts.findings == 2


def test_secret_headers_are_sent_but_never_written(project: Path, fake: FakeNuclei) -> None:
    fake.findings(record("token-echo", "medium", "http://127.0.0.1:8000/", extracted=[f"Bearer {TOKEN}"]))
    case = _case(
        Step(action="Scan with a token", security={"headers": {"Authorization": "Bearer {{token}}"}, "fail_on": "critical"}),  # type: ignore[arg-type]
        test_data={"token": {"secret": "APP_TOKEN"}},  # type: ignore[dict-item]
    )
    result, run_dir, _ = _run(case, project, {"HERCULES_NUCLEI": str(fake.path), "APP_TOKEN": TOKEN})
    assert result.status == "passed", result.summary_text()
    [argv] = fake.scans()
    assert f"Authorization: Bearer {TOKEN}" in argv
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(encoding="utf-8", errors="replace"), path.name


def test_a_missing_scanner_or_a_refused_target_is_an_error(project: Path, fake: FakeNuclei) -> None:
    case = _case(Step(action="Scan", security={}))  # type: ignore[arg-type]
    result, _, _ = _run(case, project, {"PATH": str(project)})
    assert result.status == "error" and result.steps[0].status == "error"
    assert result.steps[0].actual is not None and "hercules scan --install" in result.steps[0].actual

    refused = _case(Step(action="Scan elsewhere", security={"path": "https://other.example.com/"}))  # type: ignore[arg-type]
    result, _, _ = _run(refused, project, {"HERCULES_NUCLEI": str(fake.path)})
    assert result.steps[0].status == "error" and "scan refused" in (result.steps[0].actual or "")
    assert fake.scans() == []

    nothing = TestCase(id="scan", title="No base URL", steps=[Step(action="Scan", security={})])  # type: ignore[list-item]
    result, _, _ = _run(nothing, project, {"HERCULES_NUCLEI": str(fake.path)})
    assert "base URL" in (result.steps[0].actual or "")


def test_a_scanner_failure_is_an_error_with_its_message(project: Path, fake: FakeNuclei) -> None:
    fake.fails(1, "[FTL] Could not run nuclei: no templates provided for scan\n")
    result, _, _ = _run(_case(Step(action="Scan", security={})), project, {"HERCULES_NUCLEI": str(fake.path)})  # type: ignore[arg-type]
    step = result.steps[0]
    assert step.status == "error" and "no templates" in (step.actual or "") and "--update-templates" in (step.actual or "")
    assert result.security is not None and result.security.scans[0].error is not None


def test_the_export_writes_the_scan_as_a_comment_and_a_todo(project: Path, fake: FakeNuclei) -> None:
    result, _, _ = _run(_case(Step(action="Scan", security={"tags": ["cve", "xss"], "fail_on": "high"})), project, {"HERCULES_NUCLEI": str(fake.path)})  # type: ignore[arg-type]
    for fmt in ("playwright-py", "playwright-ts"):
        exported = export_run(result, fmt)  # type: ignore[arg-type]
        assert "hercules scan --base-url <url> --tags cve,xss --fail-on high" in exported.code
        assert any("security scan" in note for note in exported.notes)


def test_plans_list_security_steps_as_payload_steps() -> None:
    from testzeus_hercules.engine.plan import source_steps

    [listed] = source_steps(_case(Step(action="Scan", security={})))  # type: ignore[arg-type]
    assert (listed.kind, listed.payload) == ("security", True)


def test_scan_results_round_trip_through_json() -> None:
    from testzeus_hercules.cases.security import SecurityScanResult, SecuritySection

    scan = SecurityScanResult(target="http://x.test", findings=[{"template_id": "a", "severity": "low"}], counts={"findings": 99})  # type: ignore[list-item, arg-type]
    assert scan.counts.findings == 1  # counts are derived, never trusted
    section = SecuritySection.model_validate(json.loads(SecuritySection(scans=[scan]).model_dump_json()))
    assert section.counts.low == 1
