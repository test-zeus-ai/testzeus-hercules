"""Layout checks at three widths: overlap, clipped text and off-screen controls, with verified fixes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from testzeus_hercules.quality.layout import LayoutResult, check_layout

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"


@pytest.fixture(scope="module")
def results(browser: BrowserHarness) -> tuple[list[LayoutResult], dict[str, Any], str, str]:  # noqa: F811
    async def run() -> tuple[list[LayoutResult], dict[str, Any], str, str]:
        context = await browser.browser.new_context(viewport={"width": 1024, "height": 700})  # type: ignore[attr-defined]
        try:
            page = await context.new_page()
            await page.goto((QUALITY / "layout.html").as_uri())
            before = await page.evaluate("() => document.documentElement.outerHTML")
            found = await check_layout(page, files_changed=["web/styles/promo.css", "web/components/Toolbar.tsx"])
            return found, page.viewport_size, before, await page.evaluate("() => document.documentElement.outerHTML")
        finally:
            await context.close()

    return browser.run(run())


def _rules(result: LayoutResult) -> set[tuple[str, str]]:
    return {(f.rule, f.selector) for f in result.findings}


def _fix_for(result: LayoutResult, selector: str) -> Any:
    ids = {f.id for f in result.findings if f.selector == selector}
    return next(fix for fix in result.fixes if fix.finding_ref in ids)


def test_reports_per_width_and_restores_the_page(results: tuple[list[LayoutResult], dict[str, Any], str, str]) -> None:
    found, viewport, before, after = results
    assert [r.width for r in found] == [375, 768, 1280]
    assert [r.page_width for r in found] == [520, 768, 1280]
    assert viewport == {"width": 1024, "height": 700}
    assert before == after
    assert _rules(found[0]) == {("layout-overlap", "#cta"), ("text-clipped", "#badge"), ("text-clipped", "#order-ref"), ("off-viewport", "#t3"), ("off-viewport", "#t4")}
    assert _rules(found[1]) == _rules(found[2]) == {("text-clipped", "#badge"), ("text-clipped", "#order-ref")}
    assert len({f.id for r in found for f in r.findings}) == sum(len(r.findings) for r in found)


def test_overlap_at_375_gets_verified_margin(results: tuple[list[LayoutResult], dict[str, Any], str, str]) -> None:
    narrow = results[0][0]
    overlap = next(f for f in narrow.findings if f.rule == "layout-overlap")
    assert overlap.measured["overlap_px"] == [40, 40] and overlap.measured["other"] == "#close"
    assert overlap.measured["on_top"].startswith('<button id="close"')
    fix = _fix_for(narrow, "#cta")
    assert (fix.changes, fix.verified, fix.after_fix) == ({"margin-right": "48px"}, True, {"overlap_px": [0, 0]})
    assert fix.likely_file == "web/styles/promo.css"


def test_clipped_text_fix_ladder(results: tuple[list[LayoutResult], dict[str, Any], str, str]) -> None:
    narrow = results[0][0]
    badge = next(f for f in narrow.findings if f.selector == "#badge")
    assert (badge.measured["scroll_width"], badge.measured["client_width"], badge.measured["white_space"]) == (205, 120, "nowrap")
    badge_fix = _fix_for(narrow, "#badge")
    assert badge_fix.changes == {"white-space": "normal"} and badge_fix.verified
    assert (badge_fix.after_fix["scroll_width"], badge_fix.after_fix["client_width"]) == (120, 120)
    order_fix = _fix_for(narrow, "#order-ref")  # no break opportunity: wrapping fails verification, min-width is next
    width = order_fix.after_fix["client_width"]  # the text's own width: 229 px with macOS fonts, a pixel or two more on Linux
    assert order_fix.changes == {"min-width": f"{width}px"} and order_fix.verified and 225 <= width <= 235


def test_off_viewport_controls_get_flex_wrap_on_their_row(results: tuple[list[LayoutResult], dict[str, Any], str, str]) -> None:
    narrow = results[0][0]
    t4 = next(f for f in narrow.findings if f.selector == "#t4")
    assert (t4.measured["right"], t4.wcag, t4.level) == (520, "1.4.10", "AA")
    fix = _fix_for(narrow, "#t4")
    assert (fix.target_selector, fix.changes, fix.verified) == ("#toolbar", {"flex-wrap": "wrap"}, True)
    assert fix.after_fix["right"] <= 375 and fix.likely_file == "web/components/Toolbar.tsx"
    assert fix.snippet == "#toolbar { flex-wrap: wrap; }"
