"""Visual baselines: perceptual diff with masking, style deltas, verified reverts and diff classification."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.decide.payload import iter_image_parts
from testzeus_hercules.quality.imaging import diff_images
from testzeus_hercules.quality.visual import VisualDiff, capture_baseline, classify_diff, compare, load_baseline

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"
KEYS = ["#cta", "#title"]


def _canvas() -> Image.Image:
    image = Image.new("RGB", (200, 100), (255, 255, 255))
    ImageDraw.Draw(image).rectangle([10, 10, 60, 40], fill=(30, 90, 160))
    return image


# --------------------------------------------------------------------------- pure diff


def test_identical_images_do_not_differ() -> None:
    diff = diff_images(_canvas(), _canvas())
    assert (diff.ratio, diff.changed_pixels, diff.regions, diff.size_changed) == (0.0, 0, [], False)


def test_low_amplitude_noise_is_tolerated() -> None:
    noisy = _canvas().point(lambda v: min(255, v + 8))
    assert diff_images(_canvas(), noisy).ratio == 0.0


def test_changed_block_is_found_as_one_region() -> None:
    after = _canvas()
    ImageDraw.Draw(after).rectangle([120, 50, 159, 79], fill=(200, 30, 30))
    diff = diff_images(_canvas(), after)
    assert diff.changed_pixels > 0 and 0.05 < diff.ratio < 0.1
    assert diff.regions == [[112, 48, 64, 48]]  # the blur spills one cell past the block
    evidence = Image.open(io.BytesIO(diff.diff_png))
    assert evidence.size == (200, 100) and evidence.getpixel((140, 65)) == (230, 30, 60)


def test_masked_region_is_ignored() -> None:
    after = _canvas()
    ImageDraw.Draw(after).rectangle([120, 50, 159, 79], fill=(200, 30, 30))
    assert diff_images(_canvas(), after, masks=[[110, 40, 60, 50]]).ratio == 0.0


def test_size_change_counts_as_change() -> None:
    taller = Image.new("RGB", (200, 140), (255, 255, 255))
    taller.paste(_canvas(), (0, 0))
    ImageDraw.Draw(taller).rectangle([0, 110, 199, 139], fill=(0, 0, 0))
    diff = diff_images(_canvas(), taller)
    assert diff.size_changed and diff.regions[-1][1] >= 96


# --------------------------------------------------------------------------- live pages


async def _open(harness: BrowserHarness, name: str) -> tuple[Any, Any]:
    context = await harness.browser.new_context(viewport={"width": 800, "height": 400})  # type: ignore[attr-defined]
    page = await context.new_page()
    await page.goto((QUALITY / name).as_uri())
    await page.wait_for_load_state("load")
    return context, page


def _baseline_then_compare(harness: BrowserHarness, tmp_path: Path, later: str, *, mask: list[str]) -> VisualDiff:
    async def run() -> VisualDiff:
        context, page = await _open(harness, "visual_base.html")
        try:
            await capture_baseline(page, "home", baseline_dir=tmp_path / "baselines", mask=mask, key_elements=KEYS)
        finally:
            await context.close()
        context, page = await _open(harness, later)
        try:
            baseline = load_baseline(tmp_path / "baselines", "home")
            return await compare(page, baseline, baseline_dir=tmp_path / "baselines", out_dir=tmp_path / "evidence")
        finally:
            await context.close()

    return harness.run(run())


def test_unchanged_page_is_stable_with_dynamic_region_masked(browser: BrowserHarness, tmp_path: Path) -> None:  # noqa: F811
    result = _baseline_then_compare(browser, tmp_path, "visual_base.html", mask=["#clock"])
    assert (result.diff_ratio, result.changed, result.style_deltas, result.findings, result.fixes) == (0.0, False, [], [], [])
    stored = load_baseline(tmp_path / "baselines", "home")
    assert len(stored.masks) == 1 and stored.mask_spec == ["#clock"]
    assert [k.selector for k in stored.key_elements] == ["#cta", "#title"]
    assert stored.key_elements[0].styles["margin-top"] == "16px"


def test_dynamic_region_without_mask_is_a_change(browser: BrowserHarness, tmp_path: Path) -> None:  # noqa: F811
    result = _baseline_then_compare(browser, tmp_path, "visual_base.html", mask=[])
    assert result.changed and result.diff_ratio > 0 and result.style_deltas == []


def test_regression_reports_exact_style_deltas_and_verified_reverts(browser: BrowserHarness, tmp_path: Path) -> None:  # noqa: F811
    result = _baseline_then_compare(browser, tmp_path, "visual_regressed.html", mask=["#clock"])
    assert result.changed and result.diff_ratio > 0.001
    deltas = {(d.selector, d.property): (d.before, d.after) for d in result.style_deltas}
    assert deltas[("#cta", "margin-top")] == ("16px", "0px")
    assert deltas[("#cta", "font-size")] == ("18px", "14px")
    assert {selector for selector, _ in deltas} == {"#cta"}
    assert not [p for s, p in deltas if p.startswith("margin") and p != "margin-top"]
    assert [f.rule for f in result.findings] == ["visual-diff", "style-regression"]
    assert Path(result.evidence["diff"]).is_file() and result.findings[0].evidence == [result.evidence["diff"]]
    fixes = {fix.property_or_attr: fix for fix in result.fixes}
    assert set(fixes) == {"margin-top", "font-size"}
    margin = fixes["margin-top"]
    assert (margin.current, margin.suggested, margin.verified, margin.snippet) == ("0px", "16px", True, "#cta { margin-top: 16px; }")
    assert fixes["font-size"].verified and fixes["font-size"].after_fix == {"font-size": "18px", "diff_ratio": 0.0}


def test_classify_diff_sends_three_images_to_the_decider(browser: BrowserHarness, tmp_path: Path) -> None:  # noqa: F811
    result = _baseline_then_compare(browser, tmp_path, "visual_regressed.html", mask=["#clock"])
    decider = StubDecider({"diff_kind": {"type": "choice", "choice": "layout_break", "confidence": 0.83, "probabilities": {"intended_change": 0.12, "layout_break": 0.83, "content_change": 0.05}}})
    before, after, diff = (Path(result.evidence[k]).read_bytes() for k in ("before", "after", "diff"))
    verdict = browser.run(classify_diff(decider, before, after, diff, deltas=result.style_deltas))
    assert (verdict.label, verdict.confidence, verdict.engine) == ("layout_break", 0.83, "stub")
    (call,) = decider.calls
    assert call.purpose == "visual_diff"
    assert len(list(iter_image_parts(call.request.to_wire()))) == 3
    assert set(call.request.questions["diff_kind"].criteria) == {"intended_change", "layout_break", "content_change"}  # type: ignore[union-attr]
    assert {d["property"] for d in call.request.state["style_deltas"]} >= {"margin-top", "font-size"}
