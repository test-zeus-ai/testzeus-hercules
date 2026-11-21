"""Quality checks in the engine without a browser: the payload, the model's reading, options, the result section, annotation."""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from testzeus_hercules.cases.gherkin import VERIFY_ACTION, compile_feature
from testzeus_hercules.cases.models import QualityCheck, QualityPage, QualitySection, RunResult, Step, StepResult, TestCase
from testzeus_hercules.engine.quality import RunQualityOptions, _resolved
from testzeus_hercules.engine.quality_intent import QualityInterpreter, QualityPlan
from testzeus_hercules.llm.client import LLMError
from testzeus_hercules.quality.annotate import annotate
from testzeus_hercules.quality.findings import Finding, FixSuggestion, at_or_above

EMPTY: dict[str, Any] = {
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


def finding(rule: str = "color-contrast", impact: str = "serious", *, selector: str = "#subscribe", bbox: list[float] | None = None) -> Finding:
    return Finding(id=f"{rule}:{selector}", kind="a11y", rule=rule, impact=impact, selector=selector, message="problem", measured={"bbox": bbox} if bbox else None)  # type: ignore[arg-type]


def fix(ref: str, *, verified: bool = True, snippet: str = "#subscribe { color: #767676; }") -> FixSuggestion:
    return FixSuggestion(finding_ref=ref, property_or_attr="color", current="#999999", suggested="#767676", method="css_override", verified=verified, snippet=snippet)


# ── the payload ─────────────────────────────────────────────────────────────


def test_quality_payload_validates_fields_per_kind() -> None:
    assert QualityCheck(kind="a11y").fail_on == "serious" and QualityCheck(kind="a11y").level == "AA"
    with pytest.raises(ValidationError, match="needs a baseline name"):
        QualityCheck(kind="visual")
    with pytest.raises(ValidationError, match="baseline, mask: not used by a11y checks"):
        QualityCheck(kind="a11y", baseline="home", mask=[".clock"])
    with pytest.raises(ValidationError, match="scope: not used by visual checks"):
        QualityCheck(kind="visual", baseline="home", scope="main")
    with pytest.raises(ValidationError, match="viewports: not used by a11y checks"):
        QualityCheck(kind="a11y", viewports=[375])
    with pytest.raises(ValidationError):
        QualityCheck(kind="visual", baseline="../etc/passwd")
    with pytest.raises(ValidationError):
        QualityCheck(kind="layout", viewports=[100])
    with pytest.raises(ValidationError, match="one payload: api, sql, quality or security"):
        Step(action="x", quality=QualityCheck(kind="a11y"), api={"path": "/x"})  # type: ignore[arg-type]


def test_placeholders_in_quality_payloads_are_checked_and_resolved() -> None:
    with pytest.raises(ValidationError, match=r"unknown placeholder\(s\) \{\{panel\}\} in steps\[1\]\.quality"):
        TestCase(id="c", title="t", steps=[Step(action="check", quality=QualityCheck(kind="a11y", scope="#{{panel}}"))])
    case = TestCase(id="c", title="t", test_data={"panel": "signup"}, steps=[Step(action="check", quality=QualityCheck(kind="a11y", scope="#{{panel}}"))])
    assert case.steps[0].quality is not None
    resolved = _resolved(case.steps[0].quality, lambda text: text.replace("{{panel}}", "signup"))
    assert resolved.scope == "#signup" and resolved.model_fields_set == {"kind", "scope"}


def test_gherkin_quality_lines_compile_to_verify_steps_for_the_model() -> None:
    feature = (
        'Feature: F\n  Scenario: Checkout quality\n    Given I open "/checkout"\n    Then the page should have no accessibility violations\n    And the checkout page should look like the baseline\n'
    )
    case = compile_feature(feature)
    assert [(step.action, step.expected, step.quality) for step in case.steps[1:]] == [
        (VERIFY_ACTION, "the page should have no accessibility violations", None),
        (VERIFY_ACTION, "the checkout page should look like the baseline", None),
    ]


# ── the model's reading ─────────────────────────────────────────────────────


class Scripted:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []

    async def complete(self, *, system: str, user: str, schema: type, images: Any = ()) -> Any:
        self.calls.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (
            {"kind": "a11y", "fail_on": "minor", "level": "AAA", "rules": ["image-alt"], "baseline": "ignored", "viewports": [375]},
            {"kind": "a11y", "fail_on": "minor", "level": "AAA", "rules": ["image-alt"]},
        ),
        ({"kind": "visual", "baseline": "checkout", "mask": [".clock"], "scope": "main", "threshold": 0.01}, {"kind": "visual", "baseline": "checkout", "mask": [".clock"], "threshold": 0.01}),
        ({"kind": "layout", "viewports": [375, 768], "scope": "main", "level": "AA"}, {"kind": "layout", "viewports": [375, 768], "scope": "main"}),
    ],
)
def test_plan_keeps_only_the_fields_its_kind_uses(plan: dict[str, Any], expected: dict[str, Any]) -> None:
    check = QualityPlan(**{**EMPTY, **plan}).check()
    assert check is not None and check.model_dump(exclude_unset=True) == expected


def test_plans_that_are_not_checks_read_as_nothing() -> None:
    assert QualityPlan(**{**EMPTY, "kind": "none"}).check() is None
    assert QualityPlan(**{**EMPTY, "kind": "visual"}).check() is None  # no baseline
    assert QualityPlan(**{**EMPTY, "kind": "visual", "baseline": "has spaces"}).check() is None


def test_interpreter_caches_and_survives_model_errors() -> None:
    model = Scripted(QualityPlan(**{**EMPTY, "kind": "a11y"}), LLMError("server down"))
    interpreter = QualityInterpreter(model)
    first = asyncio.run(interpreter.interpret("the page should be accessible"))
    again = asyncio.run(interpreter.interpret("  the page should be accessible "))
    failed = asyncio.run(interpreter.interpret("another step"))
    assert first == again == QualityCheck(kind="a11y") and failed is None and len(model.calls) == 2
    assert asyncio.run(QualityInterpreter(None).interpret("anything")) is None


# ── run options and the result section ──────────────────────────────────────


def test_run_options_from_flags() -> None:
    assert not RunQualityOptions().enabled
    options = RunQualityOptions.from_flags(a11y=True, visual=False, fail_on="serious")
    assert options.enabled and options.fail_on == "serious" and options.widths == (375, 768, 1280)
    assert RunQualityOptions.from_flags(visual=True, fail_on="").fail_on is None
    with pytest.raises(ValueError, match="fail_on must be one of"):
        RunQualityOptions.from_flags(a11y=True, fail_on="blocker")


def test_impact_order() -> None:
    assert at_or_above("critical", "serious") and at_or_above("serious", "serious") and not at_or_above("moderate", "serious")
    assert at_or_above("minor", "minor")


def test_section_counts_distinct_findings_and_gates_run_audits_only() -> None:
    contrast, alt = finding(), finding("image-alt", "critical", selector="#team")
    run_page = QualityPage(kind="a11y", source="run", step=1, findings=[contrast, alt], fixes=[fix(contrast.id), fix(alt.id, verified=False, snippet='alt="Team photo"')])
    again = QualityPage(kind="a11y", source="run", step=3, findings=[contrast], fixes=[fix(contrast.id)])
    step_page = QualityPage(kind="a11y", source="step", step=2, findings=[finding("label", "serious", selector="#email")])
    section = QualitySection(a11y=True, fail_on="critical", pages=[run_page, again, step_page])
    assert section.counts.model_dump() == {"critical": 1, "serious": 2, "moderate": 0, "minor": 0, "findings": 3, "fixes": 3, "verified_fixes": 2, "pages": 3}
    assert [f.id for f in section.gate_failures()] == [alt.id]
    assert [(f.rule, x.verified) for f, x in section.top_fixes()] == [("color-contrast", True), ("image-alt", False)]
    assert QualitySection(pages=[step_page], fail_on=None).gate_failures() == []
    forged = QualitySection.model_validate({**section.model_dump(mode="json"), "counts": {"critical": 99}})
    assert forged.counts == section.counts  # counts are derived, never taken from input


def test_run_result_round_trips_quality_and_refuses_a_gated_pass() -> None:
    page = QualityPage(kind="a11y", source="run", step=1, findings=[finding()], fixes=[fix(finding().id)])
    steps = [StepResult(index=1, action="open", status="passed")]
    result = RunResult(run_id="r1", status="passed", case_id="c", steps=steps, quality=QualitySection(a11y=True, pages=[page]))
    assert RunResult.model_validate_json(result.model_dump_json()) == result
    text = result.summary_text()
    assert "Quality: 1 finding(s) on 1 page(s) (1 serious); 1/1 fixes verified" in text and "Fix (serious, verified): #subscribe { color: #767676; } for #subscribe" in text
    with pytest.raises(ValidationError, match="fail_on impact"):
        RunResult(run_id="r1", status="passed", case_id="c", steps=steps, quality=QualitySection(a11y=True, fail_on="serious", pages=[page]))


# ── annotated screenshots ───────────────────────────────────────────────────


def test_annotation_draws_numbered_boxes_in_findings_order() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), (255, 255, 255)).save(buffer, format="PNG")
    found = [
        finding(bbox=[20, 40, 100, 30]),
        finding("image-alt", "critical", selector="#x"),
        finding("label", "minor", selector="#y", bbox=[150, 120, 60, 40]),
        finding("z", selector="#off", bbox=[900, 900, 10, 10]),
    ]
    png, drawn = annotate(buffer.getvalue(), found)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert drawn == 2 and image.size == (300, 200)
    assert image.getpixel((60, 69)) != (255, 255, 255)  # the box's bottom edge
    assert image.getpixel((60, 55)) == (255, 255, 255)  # inside the box stays untouched


# ── optional: the local model reads plain steps ─────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("HERCULES_LIVE_LLM") != "1", reason="set HERCULES_LIVE_LLM=1 to run against a real local model server")
def test_local_model_reads_plain_quality_steps(tmp_path: Path) -> None:
    from testzeus_hercules.engine.factory import FactoryOptions, build_engines
    from testzeus_hercules.kernel.settings import Settings

    from tests.v2.test_live_local_llm import _env as live_env

    env = live_env(tmp_path, chromium="")  # any OpenAI-compatible endpoint: keys, request params, egress by its URL

    async def read() -> list[QualityCheck | None]:
        engines = await build_engines(Settings.load(tmp_path, env=env), env=env, options=FactoryOptions(mode="pure", warm_up=False))
        try:
            interpreter = QualityInterpreter(engines.interpreter.model)
            texts = ("the page should have no accessibility violations", "the checkout page should look like the baseline", "nothing should overlap on mobile")
            return [await interpreter.interpret(text) for text in texts]  # one request at a time
        finally:
            await engines.aclose()

    a11y, visual, layout = asyncio.run(read())
    assert a11y is not None and a11y.kind == "a11y"
    assert visual is not None and visual.kind == "visual" and visual.baseline and "checkout" in visual.baseline
    assert layout is not None and layout.kind == "layout"
