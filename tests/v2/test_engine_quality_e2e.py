"""End to end: quality steps and the run's a11y/visual audits through the engine, in Chromium, on local fixtures.

The quality fixtures are served over HTTP on a free port. A step that must fail fails with the
known violations and fix values verified in the page; a clean page passes; a visual baseline is
created (unverified), matched (passed) and then compared against a regressed page (failed, with
the style deltas and the decider's classification). Deciders are stubs and the model is a fake.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.models import QualityCheck, RunResult, Step, TestCase
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.engine.intent import CASE_SCHEMA_NAME, IntentPlan, IntentReply, LLMInterpreter, StepInterpreter
from testzeus_hercules.engine.quality import RunQualityOptions
from testzeus_hercules.engine.quality_intent import QualityPlan
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import DomPerceiver, ScriptedInterpreter, opens, sees

# How a model reads the navigation steps below.
OPENS = ScriptedInterpreter(
    {
        **{f'I open "{page}"': opens(page) for page in ["/a11y.html", "/clean.html", "/layout.html", "/visual_base.html", "/visual_regressed.html"]},
        'I should see "Account"': sees("Account"),
        'I should see "Newsletter"': sees("Newsletter"),
    }
)

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"
VISUAL = {"kind": "visual", "baseline": "hero", "mask": ["#clock"], "key_elements": ["#cta", "#title"]}


@pytest.fixture(scope="module")
def site() -> Any:
    require_chromium()
    with serve_fixtures(QUALITY) as base:
        yield base


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def run(case: TestCase, project: Path, **deps: Any) -> tuple[RunResult, Path]:
    settings = Settings.load(project, env={"HERCULES_HOME": str(project.parent / "home")})
    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    ctx = RunContext.create(settings, telemetry=telemetry)
    options = {"decider": StubDecider(), "interpreter": OPENS, "perceiver": DomPerceiver(), "env": {"HERCULES_CHROMIUM": require_chromium()}, **deps}
    result = asyncio.run(run_case(case, ctx, RunDeps(**options)))
    assert (ctx.paths.run_dir(ctx.run_id) / "run.json").is_file()
    assert RunResult.model_validate_json((ctx.paths.run_dir(ctx.run_id) / "run.json").read_text()) == result
    return result, ctx.paths.run_dir(ctx.run_id)


def case(site: str, *steps: Step, case_id: str = "quality") -> TestCase:
    return TestCase(id=case_id, title="Quality", environment={"base_url": site}, steps=list(steps))  # type: ignore[arg-type]


def evidence_file(run_dir: Path, uri: str) -> Path:
    return run_dir / uri.split("/", 4)[4]


# ── accessibility steps ─────────────────────────────────────────────────────


def test_a11y_step_fails_with_known_violations_and_verified_fixes(site: str, project: Path) -> None:
    result, run_dir = run(case(site, Step(action='I open "/a11y.html"'), Step(action="The page has no accessibility violations", quality=QualityCheck(kind="a11y"))), project)
    assert result.status == "failed" and [step.status for step in result.steps] == ["passed", "failed"]
    step = result.steps[1]
    assert step.actual and "accessibility finding(s) at or above serious: button-name (critical) on #dismiss - fix aria-label: Dismiss (verified)" in step.actual
    assert result.quality is not None and len(result.quality.pages) == 1
    page = result.quality.pages[0]
    assert (page.kind, page.source, page.step, page.title) == ("a11y", "step", 2, "Accessibility fixture")
    rules = {finding.rule for finding in page.findings}
    assert {"color-contrast", "image-alt", "button-name"} <= rules
    by_selector = {(fix.property_or_attr, fix.suggested): fix for fix in page.fixes}
    contrast = by_selector[("background-color", "#3278c9")]
    assert contrast.verified and contrast.snippet == "#subscribe { background-color: #3278c9; }"
    assert by_selector[("color", "#767676")].verified
    alt = next(fix for fix in page.fixes if fix.property_or_attr == "alt")
    assert not alt.verified and "Placeholder" in alt.rationale  # no vision model: the wording stays unverified
    assert result.assertions.failed == 1 and result.quality.counts.verified_fixes >= 2
    # evidence: findings JSON and an annotated screenshot, served from the step's quality folder
    names = sorted(Path(uri).name for uri in page.evidence)
    assert names == ["step-a11y.annotated.png", "step-a11y.json"]
    assert set(page.evidence) <= set(step.evidence)
    saved = json.loads(evidence_file(run_dir, page.evidence[1] if page.evidence[1].endswith(".json") else page.evidence[0]).read_text())
    assert len(saved["findings"]) == len(page.findings)
    assert evidence_file(run_dir, next(uri for uri in page.evidence if uri.endswith(".png"))).read_bytes().startswith(b"\x89PNG")
    assert "Quality: " in result.summary_text() and "Fix (" in result.summary_text()


def test_a11y_step_passes_on_a_clean_page_and_respects_scope_rules_and_gate(site: str, project: Path) -> None:
    result, _ = run(case(site, Step(action='I open "/clean.html"'), Step(action="no violations", quality=QualityCheck(kind="a11y"))), project)
    assert result.status == "passed", result.summary_text()
    assert result.quality is not None and result.quality.pages[0].findings == []

    only_alt = QualityCheck(kind="a11y", rules=["image-alt"], fail_on="critical", keyboard=False)
    result, _ = run(case(site, Step(action='I open "/a11y.html"'), Step(action="images have alt text", quality=only_alt)), project)
    assert result.status == "failed" and {f.rule for f in result.quality.pages[0].findings} == {"image-alt"}  # type: ignore[union-attr]

    scoped = QualityCheck(kind="a11y", scope="#host", fail_on="minor", keyboard=False)
    result, _ = run(case(site, Step(action='I open "/a11y.html"'), Step(action="the menu is accessible", quality=scoped)), project)
    selectors = {f.selector for f in result.quality.pages[0].findings}  # type: ignore[union-attr]
    assert selectors and all(selector.startswith("#host") for selector in selectors)

    missing = QualityCheck(kind="a11y", scope="#nowhere")
    result, _ = run(case(site, Step(action='I open "/a11y.html"'), Step(action="check", quality=missing)), project)
    assert result.status == "error" and "matches nothing" in (result.steps[1].actual or "")


# ── visual baselines ────────────────────────────────────────────────────────


def test_visual_baseline_created_then_matched_then_regression_fails(site: str, project: Path) -> None:
    decider = StubDecider(
        answers={"diff_kind": {"type": "choice", "choice": "layout_break", "confidence": 0.9, "probabilities": {"layout_break": 0.9, "intended_change": 0.05, "content_change": 0.05}}}
    )
    look = Step(action="The hero looks like the baseline", quality=QualityCheck.model_validate(VISUAL))
    first, _ = run(case(site, Step(action='I open "/visual_base.html"'), look), project, decider=decider, images=True)
    assert first.status == "failed" and first.steps[1].status == "unverified"  # created, never passed
    assert "did not exist; it was created" in (first.steps[1].actual or "")
    assert first.quality is not None and first.quality.pages[0].visual is not None and first.quality.pages[0].visual.created
    assert (project / ".hercules" / "baselines" / "hero.png").is_file()

    second, _ = run(case(site, Step(action='I open "/visual_base.html"'), look), project, decider=decider, images=True)
    assert second.status == "passed", second.summary_text()

    third, run_dir = run(case(site, Step(action='I open "/visual_regressed.html"'), look), project, decider=decider, images=True)
    assert third.status == "failed" and third.steps[1].status == "failed"
    actual = third.steps[1].actual or ""
    assert "font-size 18px -> 14px on #cta" in actual and "layout break" in actual
    visual = third.quality.pages[0].visual  # type: ignore[union-attr]
    assert visual is not None and visual.classification is not None and visual.classification.label == "layout_break"
    assert {(d.property, d.before, d.after) for d in visual.style_deltas} >= {("font-size", "18px", "14px"), ("margin-top", "16px", "0px")}
    assert sorted(visual.images) == ["after", "before", "diff"] and all(evidence_file(run_dir, uri).is_file() for uri in visual.images.values())
    assert any(f.rule == "visual-diff" and f.impact == "serious" for f in third.quality.pages[0].findings)  # type: ignore[union-attr]
    reverts = {fix.property_or_attr: fix for fix in third.quality.pages[0].fixes}  # type: ignore[union-attr]
    assert reverts["font-size"].suggested == "18px" and reverts["font-size"].verified
    assert any("visual_diff" in line for line in (run_dir / "steps" / "2" / "decisions.jsonl").read_text().splitlines())

    intended = StubDecider(
        answers={"diff_kind": {"type": "choice", "choice": "intended_change", "confidence": 0.8, "probabilities": {"layout_break": 0.1, "intended_change": 0.8, "content_change": 0.1}}}
    )
    fourth, _ = run(case(site, Step(action='I open "/visual_regressed.html"'), look), project, decider=intended, images=True)
    assert fourth.steps[1].status == "unverified" and "intended change" in (fourth.steps[1].actual or "")

    blind, _ = run(case(site, Step(action='I open "/visual_regressed.html"'), look), project, decider=decider, images=False)
    assert blind.steps[1].status == "unverified" and "cannot see screenshots" in (blind.steps[1].actual or "")


def test_layout_step_finds_problems_at_widths(site: str, project: Path) -> None:
    result, _ = run(case(site, Step(action='I open "/layout.html"'), Step(action="nothing overlaps", quality=QualityCheck(kind="layout", viewports=[375], fail_on="moderate"))), project)
    assert result.status == "failed", result.summary_text()
    page = result.quality.pages[0]  # type: ignore[union-attr]
    assert page.kind == "layout" and page.findings and all(f.kind == "layout" for f in page.findings)


# ── run-level audits ────────────────────────────────────────────────────────


def test_run_with_a11y_audits_each_page_once_and_fails_only_on_fail_on(site: str, project: Path) -> None:
    steps = (
        Step(action='I open "/clean.html"'),
        Step(action="verify", expected='I should see "Account"'),
        Step(action='I open "/a11y.html"'),
        Step(action="verify", expected='I should see "Newsletter"'),
    )
    result, _ = run(case(site, *steps), project, quality=RunQualityOptions(a11y=True))
    assert result.status == "passed", result.summary_text()
    section = result.quality
    assert section is not None and section.a11y and not section.visual
    assert [(page.step, page.source, page.title) for page in section.pages] == [(1, "run", "Clean fixture"), (3, "run", "Accessibility fixture")]  # the verify steps left the page as it was
    assert section.pages[0].findings == [] and section.counts.serious >= 1 and section.counts.critical >= 1
    assert all(f.rule not in ("keyboard-unreachable", "focus-not-visible") for f in section.pages[1].findings)  # no keyboard walk in audits

    gated, _ = run(case(site, *steps), project, quality=RunQualityOptions(a11y=True, fail_on="serious"))
    assert gated.status == "failed" and all(step.status == "passed" for step in gated.steps)
    assert gated.quality is not None and gated.quality.gate_failures() and any("fail_on" in hint for hint in gated.hints)


def test_run_with_visual_creates_page_baselines_then_compares(site: str, project: Path) -> None:
    steps = (Step(action='I open "/clean.html"'),)
    first, _ = run(case(site, *steps, case_id="visual-run"), project, quality=RunQualityOptions(visual=True, widths=(375,)))
    assert first.status == "passed"
    kinds = sorted(page.kind for page in first.quality.pages)  # type: ignore[union-attr]
    assert kinds == ["layout", "visual"]
    visual = next(page.visual for page in first.quality.pages if page.kind == "visual")  # type: ignore[union-attr]
    assert visual is not None and visual.created and visual.baseline.startswith("visual-run.clean-html.")
    second, _ = run(case(site, *steps, case_id="visual-run"), project, quality=RunQualityOptions(visual=True, widths=(375,)))
    again = next(page.visual for page in second.quality.pages if page.kind == "visual")  # type: ignore[union-attr]
    assert again is not None and not again.created and not again.changed and again.baseline == visual.baseline


# ── plain-language steps read by the model ──────────────────────────────────


class FakeModel:
    """Reads steps like a model would: the interpreter's plan, then the quality payload."""

    def __init__(self) -> None:
        self.asked: list[type] = []

    @staticmethod
    def _plan(text: str) -> IntentReply:
        if "accessibility" in text or "look like" in text:
            return IntentReply(kind="quality", target=None, value=None, url=None, expected=None, match=None, role=None)
        if text.startswith('I open "'):
            return IntentReply(kind="navigate", target=None, value=None, url=text.split('"')[1], expected=None, match=None, role=None)
        return IntentReply(kind="unknown", target=None, value=None, url=None, expected=None, match=None, role=None)

    async def complete(self, *, system: str, user: str, schema: type, images: Any = ()) -> Any:
        self.asked.append(schema)
        if schema.__name__ == CASE_SCHEMA_NAME:
            items = [line.split(": ", 1) for line in user.split("ITEMS:\n", 1)[1].splitlines()]
            return schema.model_validate({key: [self._plan(text).model_dump()] for key, text in items})
        if schema is IntentPlan:
            return IntentPlan(actions=[self._plan(user.rsplit("Step: ", 1)[-1])])
        if schema is QualityPlan:
            base = {
                "scope": None,
                "fail_on": None,
                "level": None,
                "rules": [],
                "keyboard": None,
                "baseline": None,
                "mask": [],
                "threshold": None,
                "key_elements": [],
                "full_page": None,
                "viewports": [],
            }
            if "look like" in user:
                return QualityPlan(**{**base, "kind": "visual", "baseline": "hero", "mask": ["#clock"]})
            return QualityPlan(**{**base, "kind": "a11y", "fail_on": "critical"})
        raise AssertionError(f"unexpected schema {schema}")


def test_plain_language_quality_steps_are_read_by_the_model(site: str, project: Path) -> None:
    model = FakeModel()
    interpreter = StepInterpreter(LLMInterpreter(model))
    steps = (Step(action='I open "/a11y.html"'), Step(action="verify", expected="the page should have no critical accessibility violations"))
    result, run_dir = run(case(site, *steps), project, interpreter=interpreter)
    assert result.steps[1].status == "failed" and "at or above critical" in (result.steps[1].actual or "")
    plan = [json.loads(line) for line in (run_dir / "steps" / "2" / "plan.jsonl").read_text().splitlines()]
    assert plan[-1] == {"stage": "quality", "kind": "a11y", "payload": {"kind": "a11y", "fail_on": "critical"}}

    look = (Step(action='I open "/visual_base.html"'), Step(action="verify", expected="the hero should look like the baseline"))
    result, _ = run(case(site, *look), project, interpreter=interpreter)
    assert result.steps[1].status == "unverified" and result.quality.pages[0].visual.baseline == "hero"  # type: ignore[union-attr]


def test_quality_step_without_payload_or_model_is_an_error(site: str, project: Path) -> None:
    class OnlyPlans(FakeModel):
        async def complete(self, *, system: str, user: str, schema: type, images: Any = ()) -> Any:
            if schema is QualityPlan:
                return QualityPlan(kind="none", scope=None, fail_on=None, level=None, rules=[], keyboard=None, baseline=None, mask=[], threshold=None, key_elements=[], full_page=None, viewports=[])
            return await super().complete(system=system, user=user, schema=schema, images=images)

    interpreter = StepInterpreter(LLMInterpreter(OnlyPlans()))
    result, _ = run(case(site, Step(action='I open "/a11y.html"'), Step(action="verify", expected="no accessibility problems")), project, interpreter=interpreter)
    assert result.steps[1].status == "error" and "structured quality payload" in (result.steps[1].actual or "")
