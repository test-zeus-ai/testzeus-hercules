"""End-to-end: a login scenario in real headless Chromium, with model stand-ins (a scripted step reader and a label-matching decider).

The DOM-perceiver tests exercise the engine with a minimal test perceiver; the
``perceive`` tests run the same scenario with the real perception package and
are skipped until it is installed.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.gherkin import compile_feature
from testzeus_hercules.cases.models import RunResult, TestCase
from testzeus_hercules.engine.ports import DefaultPerceiver
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import DomPerceiver, LabelDecider, ScriptedInterpreter, clicks, fills, opens, sees

SCENARIO = """\
Feature: Sign in
  Scenario: Registered user signs in
    Given I open "/login.html"
    When I fill "alice" in "Username"
    And I fill "{{{{password}}}}" in "Password"
    And I click the "Sign in" button
    Then I should see "{expected}"
"""
SECRET = "correct-horse-42"


def readings(expected: str) -> ScriptedInterpreter:
    return ScriptedInterpreter(
        {
            'I open "/login.html"': opens("/login.html"),
            'I fill "alice" in "Username"': fills("Username", "alice"),
            'I fill "{{password}}" in "Password"': fills("Password", "{{password}}"),
            'I click the "Sign in" button': clicks("Sign in", role="button"),
            f'I should see "{expected}"': sees(expected),
        }
    )


def run_login(tmp_path: Path, perceiver: Any, *, expected: str) -> tuple[RunResult, Path]:
    chromium = require_chromium()
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
    from testzeus_hercules.kernel.paths import HerculesPaths

    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    ctx = RunContext.create(settings, telemetry=telemetry)
    with serve_fixtures() as base_url:
        case: TestCase = compile_feature(SCENARIO.format(expected=expected), base_url=base_url, test_data={"password": {"secret": "LOGIN_PW"}})  # type: ignore[dict-item]
        deps = RunDeps(decider=LabelDecider(), interpreter=readings(expected), perceiver=perceiver, env={"HERCULES_CHROMIUM": chromium, "LOGIN_PW": SECRET})
        result = asyncio.run(run_case(case, ctx, deps))
    return result, ctx.paths.run_dir(ctx.run_id)


def assert_passes(result: RunResult, run_dir: Path) -> None:
    assert result.status == "passed", result.summary_text()
    assert [step.status for step in result.steps] == ["passed"] * 5
    assert result.assertions.passed == 1
    assert (run_dir / "run.json").exists()
    assert SECRET not in (run_dir / "run.json").read_text()
    for path in run_dir.rglob("*"):
        if path.suffix in (".txt", ".jsonl", ".json"):
            assert SECRET not in path.read_text(), path


def assert_fails_with_evidence(result: RunResult, run_dir: Path) -> None:
    assert result.status == "failed"
    failed = result.steps[-1]
    assert failed.status == "failed"
    assert "Welcome, alice" in (failed.actual or "")
    step_dir = run_dir / "steps" / "5"
    assert (step_dir / "plain.jpg").exists()
    assert failed.evidence
    stored = json.loads((run_dir / "run.json").read_text())
    assert stored["assertions"]["failed"] == 1


def test_login_passes_with_dom_perceiver(tmp_path: Path) -> None:
    result, run_dir = run_login(tmp_path, DomPerceiver(), expected="Welcome")

    assert_passes(result, run_dir)
    decisions = (run_dir / "steps" / "3" / "decisions.jsonl").read_text().splitlines()
    assert json.loads(decisions[0])["decision"]["engine"] == "scripted"
    assert (run_dir / "junit.xml").exists()
    assert result.evidence.network == "network.jsonl", "the real page's traffic is logged"
    network = [json.loads(line) for line in (run_dir / "network.jsonl").read_text().splitlines()]
    assert any(record.get("url", "").endswith("/login.html") and record.get("status") == 200 for record in network)


def test_login_fails_on_wrong_expected_text_with_dom_perceiver(tmp_path: Path) -> None:
    result, run_dir = run_login(tmp_path, DomPerceiver(), expected="Welcome back, Bob")

    assert_fails_with_evidence(result, run_dir)


def test_login_passes_with_real_perception(tmp_path: Path) -> None:
    pytest.importorskip("testzeus_hercules.perceive", reason="the perception package is not installed yet")

    result, run_dir = run_login(tmp_path, DefaultPerceiver(), expected="Welcome")

    assert_passes(result, run_dir)
    assert (run_dir / "steps" / "2" / "som.jpg").exists()


def test_login_fails_on_wrong_expected_text_with_real_perception(tmp_path: Path) -> None:
    pytest.importorskip("testzeus_hercules.perceive", reason="the perception package is not installed yet")

    result, run_dir = run_login(tmp_path, DefaultPerceiver(), expected="Welcome back, Bob")

    assert_fails_with_evidence(result, run_dir)
