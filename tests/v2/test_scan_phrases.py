"""Security scan steps in plain words (Gherkin) and v1 security features imported as scan steps."""

from __future__ import annotations

from pathlib import Path

import pytest

from testzeus_hercules.cases.gherkin import VERIFY_ACTION, compile_feature
from testzeus_hercules.cases.importers.v1 import V1ImportOptions, import_v1
from testzeus_hercules.cases.models import Step, TestCase
from testzeus_hercules.cases.scan_phrases import fold_scan_steps, is_security_scenario, scan_from_text, v1_security_steps


@pytest.mark.parametrize(
    "text, tags, extra",
    [
        ('I run a security scan for "cve, exposure, misconfig"', ["cve", "exposure", "misconfig"], {}),
        ('I run a security scan for "sqli and xss" failing on "high"', ["sqli", "xss"], {"fail_on": "high"}),
        ('I run a security scan of the OpenAPI spec "specs/pets.yaml" for "exposure"', ["exposure"], {"openapi": "specs/pets.yaml"}),
        ('we scan the app for security issues on the path "/api"', [], {"path": "/api"}),
        ("I run a security scan for SQL injection and cross-site scripting", ["xss", "sqli"], {}),
        ("Run a vulnerability scan", [], {}),
        ('I run a security scan for "misconfig" at 15 requests per second', ["misconfig"], {"rate_limit": 15}),
    ],
)
def test_scan_sentences_become_scans(text: str, tags: list[str], extra: dict[str, str]) -> None:
    scan = scan_from_text(text)
    assert scan is not None
    assert scan.tags == tags
    for name, value in extra.items():
        assert getattr(scan, name) == value


@pytest.mark.parametrize("text", ["I open the scan page", 'I click "Run a security scan"', "I scan the QR code", 'I run a security scan for "dos"'])
def test_other_sentences_are_not_scans_and_words_never_turn_on_intrusive_templates(text: str) -> None:
    scan = scan_from_text(text)
    assert scan is None or (not scan.intrusive and "dos" not in scan.tags)


def test_a_feature_with_a_scan_step_and_its_then_compiles_to_one_security_step() -> None:
    case = compile_feature(
        """Feature: Shop security
  Scenario: The shop has no serious findings
    Given I open "/"
    When I run a security scan for "misconfig, exposure"
    Then there should be no security findings at or above "high"
    And the page title is "Shop"
""",
        base_url="http://127.0.0.1:8000",
    )
    assert [step.action for step in case.steps] == ['I open "/"', 'I run a security scan for "misconfig, exposure"', VERIFY_ACTION]
    scan_step = case.steps[1]
    assert scan_step.security is not None and scan_step.security.tags == ["misconfig", "exposure"] and scan_step.security.fail_on == "high"
    assert scan_step.expected == 'there should be no security findings at or above "high"'
    assert case.steps[2].expected == 'the page title is "Shop"'


def test_a_fail_on_written_in_the_scan_step_wins_over_the_then() -> None:
    steps = fold_scan_steps([Step(action='I run a security scan failing on "critical"'), Step(action=VERIFY_ACTION, expected='no findings at or above "low"')])
    assert len(steps) == 1 and steps[0].security is not None and steps[0].security.fail_on == "critical"


SECURITY_FEATURE = """Feature: Security Testing on the shop

  # Testing for Common Vulnerabilities and Exposures
  Scenario: Perform CVE Testing
    Given I have a base_url of the website
    When I check for publicly disclosed vulnerabilities
    Then there should not be any CVEs affecting the system

  Scenario: Test for SQL Injection
    Given I have a base_url of the website
    When I inject SQL payloads
    Then there should not be any vulnerabilities in query processing

  Scenario: Conduct Intrusive Testing
    Given I have a base_url of the website
    When I actively exploit identified vulnerabilities
    Then there should not be any unmitigated risks or impacts
"""


def test_v1_security_scenarios_become_scan_steps_by_topic() -> None:
    steps = [Step(action="I have a base_url of the website"), Step(action="I check for publicly disclosed vulnerabilities"), Step(action=VERIFY_ACTION, expected="there should not be any CVEs")]
    converted = v1_security_steps(steps)
    assert converted is not None and len(converted) == 1
    assert converted[0].security is not None and converted[0].security.tags == ["cve"]
    assert converted[0].expected == "there should not be any CVEs"
    # The topic may be named only by the scenario.
    named = v1_security_steps([Step(action="I inject payloads")], context="Test for SQL Injection")
    assert named is not None and named[0].security is not None and named[0].security.tags == ["sqli"]
    # Intrusive testing is never imported as a scan.
    assert v1_security_steps([Step(action="I actively exploit identified vulnerabilities")], context="Conduct Intrusive Testing") is None
    assert is_security_scenario(["Security Testing on YC website"], []) and not is_security_scenario(["Checkout"], ["smoke"])


def test_hercules_import_v1_turns_a_security_feature_into_scan_cases(tmp_path: Path) -> None:
    source = tmp_path / "v1"
    (source / "opt" / "input").mkdir(parents=True)
    (source / "opt" / "input" / "security.feature").write_text(SECURITY_FEATURE, encoding="utf-8")
    (source / ".env").write_text("BASE_URL=http://127.0.0.1:8000\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    report = import_v1(V1ImportOptions(source_dir=source, project_dir=project))
    assert report.cases_converted == 3
    cases = {path.stem: TestCase.model_validate_json(path.read_text()) for path in (project / ".hercules" / "cases").glob("*.json")}
    cve = cases["perform-cve-testing"]
    assert [step.security.tags if step.security else None for step in cve.steps] == [["cve"]]
    assert cve.environment.ref == "default"
    assert [step.security.tags if step.security else None for step in cases["test-for-sql-injection"].steps] == [["sqli"]]
    intrusive = cases["conduct-intrusive-testing"]
    assert all(step.security is None for step in intrusive.steps)
