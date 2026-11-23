"""Deterministic accessibility checks on fixture pages with known violations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from testzeus_hercules.perceive import take_snapshot
from testzeus_hercules.quality.checks import check_contrast, check_images, check_keyboard, check_names, check_target_size, focus_changes, run_checks, spacing_conflict
from testzeus_hercules.quality.frames import list_frames

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"


async def open_quality(harness: BrowserHarness, name: str, viewport: tuple[int, int] = (1280, 800)) -> tuple[Any, Any]:
    context = await harness.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})  # type: ignore[attr-defined]
    page = await context.new_page()
    await page.goto((QUALITY / name).as_uri())
    await page.wait_for_load_state("load")
    return context, page


def by_selector(findings: list[Any], rule: str) -> dict[str, Any]:
    return {f.selector: f for f in findings if f.rule == rule}


# --------------------------------------------------------------------------- pure helpers


def test_spacing_exception_passes_isolated_small_target() -> None:
    assert spacing_conflict([100, 100, 16, 16], [[0, 0, 40, 40]]) is None


def test_spacing_exception_fails_when_neighbour_inside_circle() -> None:
    assert spacing_conflict([100, 100, 16, 16], [[60, 100, 40, 16]]) == (8.0, 0)


def test_focus_changes_ignores_outline_none() -> None:
    before = {"self": {"outline-style": "none", "outline-width": "0px", "color": "rgb(0, 0, 0)"}, "parent": {}}
    after = {"self": {"outline-style": "none", "outline-width": "2px", "color": "rgb(0, 0, 0)"}, "parent": {}}
    assert focus_changes(before, after) == []
    ring = {"self": {"outline-style": "auto", "outline-width": "1px", "color": "rgb(0, 0, 0)"}, "parent": {}}
    assert focus_changes(before, ring) == ["outline-style", "outline-width"]


# --------------------------------------------------------------------------- page checks


def test_contrast_findings_have_exact_ratios(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> list[Any]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            return await check_contrast(page)
        finally:
            await context.close()

    findings = by_selector(browser.run(run()), "color-contrast")
    assert set(findings) == {"#subscribe", "#fine-print", "#save"}
    button = findings["#subscribe"].measured
    assert (button["fg"], button["bg"], button["ratio"], button["required"], button["large_text"]) == ("#ffffff", "#4a90e2", 3.29, 4.5, False)
    note = findings["#fine-print"].measured
    assert (note["fg"], note["bg"], note["ratio"]) == ("#999999", "#ffffff", 2.84)
    framed = findings["#save"]
    assert framed.frame_path == ["#inner"] and framed.frame == "Inner form"
    assert (framed.measured["fg"], framed.measured["ratio"]) == ("#aaaaaa", 2.32)
    assert findings["#subscribe"].wcag == "1.4.3" and findings["#subscribe"].level == "AA"
    assert ".btn-primary" in findings["#subscribe"].hints


def test_names_find_icon_button_and_shadow_button_with_md(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[list[Any], Any]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            snapshot = await take_snapshot(page, seq=1)
            return await check_names(page, snapshot=snapshot), snapshot
        finally:
            await context.close()

    findings, snapshot = browser.run(run())
    named = by_selector(findings, "control-name")
    assert set(named) == {"#dismiss", "#host >> :scope > button"}
    dismiss = named["#dismiss"]
    assert dismiss.element_md is not None and snapshot.element(dismiss.element_md).attrs.get("id") == "dismiss"
    assert dismiss.wcag == "4.1.2" and dismiss.level == "A"
    assert dismiss.measured["near"] == "Newsletter"
    assert "icon-close" in dismiss.measured["icon_hints"]


def test_target_size_reports_small_crowded_target(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> list[Any]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            return await check_target_size(page)
        finally:
            await context.close()

    findings = by_selector(browser.run(run()), "target-size")
    assert set(findings) == {"#help"}
    measured = findings["#help"].measured
    assert (measured["width"], measured["height"], measured["distance"]) == (16, 16, 8.0)
    assert measured["nearest_target"].startswith('<input id="email"')


def test_images_without_alt_skip_decorative(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> list[Any]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            return await check_images(page)
        finally:
            await context.close()

    findings = browser.run(run())
    assert [f.selector for f in findings] == ["#team"]
    assert findings[0].measured["file"] == "team-photo" and findings[0].measured["natural"] == [120, 80]


def test_keyboard_walk_finds_trap_unreachable_and_invisible_focus(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> Any:
        context, page = await open_quality(browser, "keyboard.html")
        try:
            result = await check_keyboard(page)
            active = await page.evaluate("() => document.activeElement === document.body")
            return result, active
        finally:
            await context.close()

    result, restored = browser.run(run())
    assert restored
    assert result.order == ["#first", "#quiet", "#search", "#trap", "#last"]
    assert result.traps == ["#trap"]
    rules = {(f.rule, f.selector) for f in result.findings}
    assert rules == {("keyboard-trap", "#trap"), ("keyboard-unreachable", "#save"), ("focus-not-visible", "#quiet")}
    unreachable = next(f for f in result.findings if f.rule == "keyboard-unreachable")
    assert unreachable.measured["generic"] is True and unreachable.wcag == "2.1.1"


def test_run_checks_reports_cross_origin_frame_as_not_scanned(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[list[Any], list[str], list[Any]]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            findings, skipped = await run_checks(page, keyboard=False)
            return findings, skipped, await list_frames(page)
        finally:
            await context.close()

    findings, skipped, frames = browser.run(run())
    assert len(skipped) == 1 and skipped[0].startswith("data:text/html")
    assert [f.label for f in frames] == [None, "Inner form", "Outside widget"]
    assert {f.rule for f in findings} == {"color-contrast", "control-name", "target-size", "image-alt"}
    assert not [f for f in findings if f.frame == "Outside widget"]


@pytest.mark.parametrize("name", ["a11y.html", "keyboard.html"])
def test_checks_leave_no_trace_on_the_page(browser: BrowserHarness, name: str) -> None:  # noqa: F811
    async def run() -> tuple[str, str]:
        context, page = await open_quality(browser, name)
        try:
            before = await page.evaluate("() => document.documentElement.outerHTML")
            await run_checks(page)
            return before, await page.evaluate("() => document.documentElement.outerHTML")
        finally:
            await context.close()

    before, after = browser.run(run())
    assert before == after
