"""Finding / FixSuggestion / QualityReport models and the one-call audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from testzeus_hercules.quality import QualityReport, audit_accessibility, audit_layout
from testzeus_hercules.quality.findings import Finding, FixSuggestion, finding_id, selector_of

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"


def _finding(rule: str = "color-contrast", selector: str = "#a", **extra: Any) -> Finding:
    return Finding(id=finding_id(rule, [], selector), kind="a11y", rule=rule, selector=selector, message="m", **extra)


def test_finding_ids_are_stable_and_distinct() -> None:
    assert finding_id("x", [], "#a") == finding_id("x", [], "#a")
    assert len({finding_id("x", [], "#a"), finding_id("x", ["#f"], "#a"), finding_id("y", [], "#a"), finding_id("x", [], "#a", "w375")}) == 4
    assert selector_of(["#host", ":scope > button"]) == "#host >> :scope > button"
    assert _finding(selector="#host >> :scope > button").segments == ["#host", ":scope > button"]


def test_models_are_strict_and_frozen() -> None:
    with pytest.raises(ValidationError):
        Finding(id="a", kind="sound", rule="r", selector="#a", message="m")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FixSuggestion(finding_ref="a", property_or_attr="color", suggested="#000", method="css_override", confidence=1.5)
    with pytest.raises(ValidationError):
        _finding().model_copy().__setattr__("rule", "other")


def test_report_counts_and_merge() -> None:
    a = _finding(level="AA", impact="serious")
    b = _finding(rule="image-alt", selector="#img", level="A", impact="critical")
    fix = FixSuggestion(finding_ref=a.id, property_or_attr="color", suggested="#767676", method="css_override", verified=True)
    report = QualityReport(url="u", findings=[a], fixes=[fix], not_scanned=["x"]).merged(QualityReport(findings=[a, b], not_scanned=["y"], engine={"axe-core": "4.10.3"}))
    assert [f.id for f in report.findings] == [a.id, b.id]
    assert report.counts == {"findings": 2, "by_kind": {"a11y": 2}, "by_level": {"A": 1, "AA": 1}, "by_impact": {"critical": 1, "serious": 1}, "fixes": 1, "verified_fixes": 1}
    assert report.not_scanned == ["x", "y"] and report.fixes_for(a) == [fix] and report.fixes_for(b) == []
    dumped = json.loads(report.model_dump_json())
    assert dumped["counts"]["verified_fixes"] == 1 and dumped["engine"] == {"axe-core": "4.10.3"}


def test_full_accessibility_audit_verifies_every_computable_fix(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> QualityReport:
        context = await browser.browser.new_context(viewport={"width": 1280, "height": 800})  # type: ignore[attr-defined]
        try:
            page = await context.new_page()
            await page.goto((QUALITY / "a11y.html").as_uri())
            return await audit_accessibility(page)
        finally:
            await context.close()

    report = browser.run(run())
    assert report.engine == {"axe-core": "4.10.3"} and len(report.not_scanned) == 1
    unfixed = [f for f in report.findings if not report.fixes_for(f)]
    assert unfixed == []
    unverified = {fix.property_or_attr for fix in report.fixes if not fix.verified}
    assert unverified == {"alt"}  # the placeholder alt text without a vision model, by design
    assert report.counts["findings"] == 14 and report.counts["by_kind"] == {"a11y": 14}  # 7 from axe, 7 from the own checks


def test_layout_audit_flattens_widths(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> QualityReport:
        context = await browser.browser.new_context(viewport={"width": 1280, "height": 800})  # type: ignore[attr-defined]
        try:
            page = await context.new_page()
            await page.goto((QUALITY / "layout.html").as_uri())
            return await audit_layout(page)
        finally:
            await context.close()

    report = browser.run(run())
    assert sorted({f.measured["viewport_width"] for f in report.findings}) == [375, 768, 1280]
    assert report.counts["verified_fixes"] == report.counts["fixes"] == report.counts["findings"] == 9
