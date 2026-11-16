"""Fix suggestions: concrete values, verified by applying them to the live page and re-checking."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from testzeus_hercules.quality.axe import run_axe
from testzeus_hercules.quality.checks import check_contrast, check_images, check_keyboard, check_names, check_target_size
from testzeus_hercules.quality.color import RGBA, contrast_ratio, floor2, nearest_passing, to_oklch
from testzeus_hercules.quality.findings import FixSuggestion, QualityReport
from testzeus_hercules.quality.fixes import aria_label_for, likely_file, suggest_fix, suggest_fixes, verify_fix

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"


async def open_quality(harness: BrowserHarness, name: str) -> tuple[Any, Any]:
    context = await harness.browser.new_context(viewport={"width": 1280, "height": 800})  # type: ignore[attr-defined]
    page = await context.new_page()
    await page.goto((QUALITY / name).as_uri())
    await page.wait_for_load_state("load")
    return context, page


class FakeLLM:
    """Stands in for StructuredLLM: returns canned objects and records what it was asked."""

    def __init__(self, replies: dict[str, dict[str, Any]], *, images: bool = True) -> None:
        self.replies = replies
        self.accepts_images = images
        self.calls: list[tuple[str, int]] = []

    async def complete(self, *, system: str, user: str, schema: type[BaseModel], images: Sequence[bytes] = ()) -> BaseModel:
        self.calls.append((schema.__name__, len(images)))
        assert all(image.startswith(b"\x89PNG") for image in images)
        return schema.model_validate(self.replies[schema.__name__])


# --------------------------------------------------------------------------- pure values


def test_nearest_passing_colour_keeps_hue_and_meets_ratio() -> None:
    grey = nearest_passing(RGBA.parse("#999999"), RGBA.parse("#ffffff"), 4.5)
    assert grey is not None and (grey.side, grey.suggested, grey.ratio) == ("color", "#767676", 4.54)
    blue = nearest_passing(RGBA.parse("#ffffff"), RGBA.parse("#4a90e2"), 4.5)
    assert blue is not None and (blue.side, blue.current, blue.suggested, blue.ratio) == ("background-color", "#4a90e2", "#3278c9", 4.5)
    assert abs(to_oklch(RGBA.parse(blue.suggested))[2] - to_oklch(RGBA.parse("#4a90e2"))[2]) < 1.5
    assert floor2(contrast_ratio(RGBA.parse("#ffffff"), RGBA.parse(blue.suggested))) >= 4.5


def test_nearest_passing_returns_none_when_unreachable() -> None:
    assert nearest_passing(RGBA.parse("#777777"), RGBA.parse("#777777"), 21.0) is None


@pytest.mark.parametrize(
    ("measured", "hints", "label"),
    [
        ({"caption": "Postcode"}, [], "Postcode"),
        ({"icon_hints": ["dismiss", "icon-close"]}, ["#dismiss"], "Dismiss"),
        ({"icon_hints": ["shadow-menu", "menu-icon"]}, [], "Menu"),
        ({"icon_hints": ["btnCartOpen"]}, [], "Cart"),
        ({"icon_hints": ["promo-thing"]}, [], "Promo thing"),
        ({"icon_hints": []}, [], None),
    ],
)
def test_aria_label_from_context(measured: dict[str, Any], hints: list[str], label: str | None) -> None:
    assert aria_label_for(measured, hints) == label


def test_likely_file_prefers_path_then_contents() -> None:
    files = ["src/app.css", "src/components/Newsletter.tsx", "src/components/SubscribeButton.tsx", "README.md"]
    assert likely_file(["#subscribe", ".btn-primary"], files) == "src/components/SubscribeButton.tsx"
    contents = {"src/app.css": ".btn-primary { color: #fff; }", "src/components/Newsletter.tsx": "<button className='icon-close'/>"}
    assert likely_file([".icon-close", "#dismiss"], files, contents) == "src/components/Newsletter.tsx"
    assert likely_file([".btn", ".icon"], files, contents) is None
    assert likely_file(["#subscribe"], []) is None


# --------------------------------------------------------------------------- verified on the page


def _by(fixes: list[FixSuggestion], findings: list[Any]) -> dict[str, FixSuggestion]:
    selectors = {f.id: f.selector for f in findings}
    return {selectors[fix.finding_ref]: fix for fix in fixes}


def test_contrast_fixes_are_verified_on_the_page(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[list[Any], list[FixSuggestion], str, str]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            before = await page.evaluate("() => document.documentElement.outerHTML")
            findings = await check_contrast(page)
            fixes = await suggest_fixes(page, findings, files_changed=["src/components/SubscribeButton.tsx", "src/footer.css"])
            return findings, fixes, before, await page.evaluate("() => document.documentElement.outerHTML")
        finally:
            await context.close()

    findings, fixes, before, after = browser.run(run())
    assert before == after
    fix = _by(fixes, findings)
    button = fix["#subscribe"]
    assert (button.property_or_attr, button.current, button.suggested, button.method) == ("background-color", "#4a90e2", "#3278c9", "css_override")
    assert button.verified and button.after_fix == {"ratio": 4.5, "fg": "#ffffff", "bg": "#3278c9", "required": 4.5}
    assert button.likely_file == "src/components/SubscribeButton.tsx"
    assert button.snippet == "#subscribe { background-color: #3278c9; }"
    note = fix["#fine-print"]
    assert (note.property_or_attr, note.suggested, note.verified, note.after_fix["ratio"]) == ("color", "#767676", True, 4.54)
    framed = fix["#save"]
    assert framed.verified and framed.after_fix["ratio"] >= 4.5


def test_name_target_and_alt_fixes(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[list[Any], list[FixSuggestion]]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            findings = [*await check_names(page), *await check_target_size(page), *await check_images(page)]
            return findings, await suggest_fixes(page, findings)
        finally:
            await context.close()

    findings, fixes = browser.run(run())
    fix = _by(fixes, findings)
    dismiss = fix["#dismiss"]
    assert (dismiss.property_or_attr, dismiss.suggested, dismiss.verified, dismiss.after_fix) == ("aria-label", "Dismiss", True, {"name": "Dismiss"})
    shadow = fix["#host >> :scope > button"]
    assert (shadow.suggested, shadow.verified) == ("Menu", True)
    help_fix = next(f for f in fixes if f.property_or_attr == "min-width, min-height")
    assert (help_fix.current, help_fix.suggested) == ("16px × 16px", "min-width: 24px; min-height: 24px")
    assert help_fix.verified and help_fix.after_fix == {"width": 24, "height": 24}
    alt = next(f for f in fixes if f.property_or_attr == "alt")
    assert (alt.suggested, alt.verified, alt.after_fix, alt.method) == ("Team photo", False, None, "text")
    assert alt.confidence < 0.5 and "Placeholder" in alt.rationale


def test_model_writes_alt_text_and_label_from_crop(browser: BrowserHarness) -> None:  # noqa: F811
    llm = FakeLLM(
        {"_AltText": {"alt": "Two team members smiling", "rationale": "Two people in blue and orange."}, "_Label": {"label": "Close newsletter", "rationale": "Icon is an X next to the newsletter."}}
    )

    async def run() -> tuple[FixSuggestion | None, FixSuggestion | None]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            image = (await check_images(page))[0]
            name = next(f for f in await check_names(page) if f.selector == "#dismiss")
            return await suggest_fix(page, image, llm=llm), await suggest_fix(page, name, llm=llm)  # type: ignore[arg-type]
        finally:
            await context.close()

    alt, label = browser.run(run())
    assert alt is not None and (alt.suggested, alt.verified, alt.after_fix) == ("Two team members smiling", True, {"alt": "Two team members smiling"})
    assert label is not None and (label.suggested, label.verified) == ("Close newsletter", True)
    assert llm.calls == [("_AltText", 1), ("_Label", 0)]


def test_keyboard_fixes_are_verified_by_pressing_tab(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[list[Any], list[FixSuggestion]]:
        context, page = await open_quality(browser, "keyboard.html")
        try:
            findings = (await check_keyboard(page)).findings
            return findings, await suggest_fixes(page, findings)
        finally:
            await context.close()

    findings, fixes = browser.run(run())
    fix = _by(fixes, findings)
    assert set(fix) == {"#save", "#quiet"}  # a trap has no computable fix: the finding stands alone
    save = fix["#save"]
    assert (save.changes, save.verified, save.after_fix) == ({"tabindex": "0", "role": "button"}, True, {"reached_with_tab": True})
    quiet = fix["#quiet"]
    assert quiet.state == ":focus-visible" and quiet.changes == {"outline": "2px solid #111111", "outline-offset": "2px"}
    assert quiet.verified and quiet.after_fix["outline_contrast"] == 18.88
    assert quiet.snippet == "#quiet:focus-visible { outline: 2px solid #111111; outline-offset: 2px; }"


def test_axe_and_own_findings_share_one_verified_fix(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> QualityReport:
        context, page = await open_quality(browser, "a11y.html")
        try:
            findings = [*(await run_axe(page)).findings, *await check_contrast(page)]
            subscribe = [f for f in findings if f.selector == "#subscribe"]
            return QualityReport(url=page.url, findings=subscribe, fixes=await suggest_fixes(page, subscribe))
        finally:
            await context.close()

    report = browser.run(run())
    assert [f.source for f in report.findings] == ["axe", "hercules"]
    assert [fix.suggested for fix in report.fixes] == ["#3278c9", "#3278c9"]
    assert {fix.finding_ref for fix in report.fixes} == {f.id for f in report.findings}
    assert report.counts["verified_fixes"] == 2


def test_failed_verification_is_reported_not_hidden(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> FixSuggestion:
        context, page = await open_quality(browser, "a11y.html")
        try:
            finding = next(f for f in await check_contrast(page) if f.selector == "#fine-print")
            wrong = FixSuggestion(finding_ref=finding.id, property_or_attr="color", current="#999999", suggested="#888888", changes={"color": "#888888"}, method="css_override")
            return await verify_fix(page, finding, wrong)
        finally:
            await context.close()

    checked = browser.run(run())
    assert checked.verified is False and checked.after_fix == {"ratio": 3.54, "fg": "#888888", "bg": "#ffffff", "required": 4.5}


def test_a_fix_for_4_5_to_1_is_not_shared_with_a_7_to_1_finding(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[list[Any], list[FixSuggestion]]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            normal = next(f for f in await check_contrast(page) if f.selector == "#subscribe")
            enhanced = normal.model_copy(update={"id": normal.id + "-enhanced", "rule": "color-contrast-enhanced", "measured": {**(normal.measured or {}), "required": 7.0}})
            return [normal, enhanced], await suggest_fixes(page, [normal, enhanced])
        finally:
            await context.close()

    findings, fixes = browser.run(run())
    by_ref = {fix.finding_ref: fix for fix in fixes}
    normal, enhanced = by_ref[findings[0].id], by_ref[findings[1].id]
    assert normal.suggested != enhanced.suggested
    assert normal.after_fix["required"] == 4.5 and enhanced.after_fix["required"] == 7.0
    assert enhanced.verified is (enhanced.after_fix["ratio"] >= 7.0)


def test_the_keyboard_recheck_puts_focus_back(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> tuple[str, str]:
        context, page = await open_quality(browser, "keyboard.html")
        try:
            findings = (await check_keyboard(page)).findings
            save = next(f for f in findings if f.selector == "#save")
            await page.evaluate("() => { const b = document.createElement('button'); b.id = 'parked'; b.textContent = 'parked'; document.body.appendChild(b); b.focus(); }")
            before = await page.evaluate("() => document.activeElement && document.activeElement.id")
            await suggest_fix(page, save)
            return before, await page.evaluate("() => document.activeElement && document.activeElement.id")
        finally:
            await context.close()

    before, after = browser.run(run())
    assert before == "parked" and after == "parked"


def test_model_written_labels_are_escaped_in_snippets(browser: BrowserHarness) -> None:  # noqa: F811
    llm = FakeLLM({"_AltText": {"alt": 'A "quoted" <b>photo</b>', "rationale": "r"}, "_Label": {"label": 'Close" onclick="x', "rationale": "r"}})

    async def run() -> tuple[FixSuggestion | None, FixSuggestion | None]:
        context, page = await open_quality(browser, "a11y.html")
        try:
            image = (await check_images(page))[0]
            name = next(f for f in await check_names(page) if f.selector == "#dismiss")
            return await suggest_fix(page, image, llm=llm, verify=False), await suggest_fix(page, name, llm=llm, verify=False)  # type: ignore[arg-type]
        finally:
            await context.close()

    alt, label = browser.run(run())
    assert alt is not None and alt.snippet == 'alt="A &quot;quoted&quot; &lt;b&gt;photo&lt;/b&gt;"'
    assert label is not None and label.snippet == 'aria-label="Close&quot; onclick=&quot;x"'
