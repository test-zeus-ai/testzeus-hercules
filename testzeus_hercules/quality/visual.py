"""Visual baselines: screenshot plus computed styles of key elements, perceptual diff and style deltas.

``capture_baseline`` stores ``<name>.png`` and ``<name>.json`` (mask rectangles and the computed styles
of key elements) under a baseline directory. ``compare`` diffs the live page against it with dynamic
regions masked, reports property-level style deltas for the key elements, and proposes reverting
values for the changed properties, verified by applying them as overrides and diffing again.
``classify_diff`` asks a decision engine whether a diff is an intended change or a break.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from testzeus_hercules.decide.contract import ChoiceQuestion, DecisionRequest, ImagePart
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.perceive.model import Snapshot
from testzeus_hercules.quality.findings import DiffClassification, Finding, FixSuggestion, StyleDelta, finding_id, selector_of
from testzeus_hercules.quality.fixes import apply_fix
from testzeus_hercules.quality.frames import locator_for, probe_element
from testzeus_hercules.quality.imaging import DEFAULT_SCALE, DEFAULT_TOLERANCE, diff_images, shrink

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

__all__ = [
    "Baseline",
    "DIFF_KINDS",
    "DiffClassification",
    "KeyElement",
    "StyleDelta",
    "VisualDiff",
    "capture_baseline",
    "classify_diff",
    "compare",
    "load_baseline",
]

ElementSpec = Union[str, int]  # a selector, or a snapshot md
DEFAULT_THRESHOLD = 0.001
NOT_REVERTED = frozenset({"width", "height"})  # usually results of layout, not its cause: reported, not reverted
GEOMETRY = ("x", "y", "box-width", "box-height")  # the element's box: an effect, never set directly
DIFF_KINDS: dict[str, Any] = {
    "intended_change": "The page changed on purpose: new or restyled content that still looks deliberate and intact.",
    "layout_break": "Something is broken: misaligned, overlapping, clipped, collapsed or shifted elements, or lost spacing.",
    "content_change": "Only data or text changed (numbers, dates, names, copy); layout and styling are the same.",
}
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KeyElement(_Model):
    """A key element's stable selector, box and computed styles at capture time."""

    spec: str
    selector: str
    rect: list[float]
    styles: dict[str, str]
    hints: list[str] = Field(default_factory=list)
    desc: str = ""


class Baseline(_Model):
    name: str
    url: str
    viewport: list[int]
    full_page: bool = False
    png: str
    masks: list[list[float]] = Field(default_factory=list)
    mask_spec: list[str] = Field(default_factory=list)
    key_elements: list[KeyElement] = Field(default_factory=list)


class VisualDiff(_Model):
    """Comparison of the live page with a baseline."""

    name: str
    diff_ratio: float
    changed: bool
    regions: list[list[int]]
    size_before: list[int]
    size_after: list[int]
    style_deltas: list[StyleDelta] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[FixSuggestion] = Field(default_factory=list)


def _check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"baseline name {name!r}: use 1-100 letters, digits, '.', '_' or '-'")
    return name


async def _locator(page: "Page", spec: ElementSpec, snapshot: Snapshot | None) -> "Locator":
    if isinstance(spec, int):
        if snapshot is None:
            raise ValueError("an md needs the snapshot it came from")
        from testzeus_hercules.perceive.snapshot import resolve

        return await resolve(page, snapshot, spec)
    return page.locator(spec).first


async def _scroll(page: "Page", full_page: bool) -> tuple[float, float]:
    if not full_page:
        return 0.0, 0.0
    x, y = await page.evaluate("() => [scrollX, scrollY]")
    return float(x), float(y)


async def _mask_rects(page: "Page", specs: Sequence[ElementSpec], snapshot: Snapshot | None, full_page: bool) -> list[list[float]]:
    dx, dy = await _scroll(page, full_page)
    rects = []
    for spec in specs:
        locator = page.locator(spec) if isinstance(spec, str) else await _locator(page, spec, snapshot)
        for index in range(await locator.count() if isinstance(spec, str) else 1):
            item = locator.nth(index) if isinstance(spec, str) else locator
            rect = (await probe_element(item, "rect"))["rect"]
            if rect[2] > 0 and rect[3] > 0:
                rects.append([round(rect[0] + dx, 2), round(rect[1] + dy, 2), round(rect[2], 2), round(rect[3], 2)])
    return rects


async def _screenshot(page: "Page", full_page: bool) -> bytes:
    from testzeus_hercules.perceive.screen_guard import capture

    return await capture(page, type="png", full_page=full_page, animations="disabled", caret="hide")


async def _key_element(page: "Page", spec: ElementSpec, snapshot: Snapshot | None) -> KeyElement:
    locator = await _locator(page, spec, snapshot)
    described = await probe_element(locator, "describe")
    measured = await probe_element(locator, "styles")
    return KeyElement(
        spec=str(spec) if isinstance(spec, str) else f"md:{spec}",
        selector=selector_of(described["path"]),
        rect=[round(v, 2) for v in measured["rect"]],
        styles=measured["styles"],
        hints=described["tokens"],
        desc=described["desc"],
    )


async def capture_baseline(
    page: "Page",
    name: str,
    *,
    baseline_dir: Path,
    mask: Sequence[ElementSpec] = (),
    key_elements: Sequence[ElementSpec] = (),
    snapshot: Snapshot | None = None,
    full_page: bool = False,
) -> Baseline:
    """Store a screenshot and the computed styles of ``key_elements``; ``mask`` names dynamic regions."""
    _check_name(name)
    baseline_dir.mkdir(parents=True, exist_ok=True)
    png = await _screenshot(page, full_page)
    baseline = Baseline(
        name=name,
        url=page.url,
        viewport=list(await page.evaluate("() => [innerWidth, innerHeight]")),
        full_page=full_page,
        png=f"{name}.png",
        masks=await _mask_rects(page, mask, snapshot, full_page),
        mask_spec=[str(m) for m in mask if isinstance(m, str)],
        key_elements=[await _key_element(page, spec, snapshot) for spec in key_elements],
    )
    (baseline_dir / baseline.png).write_bytes(png)
    (baseline_dir / f"{name}.json").write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    return baseline


def load_baseline(baseline_dir: Path, name: str) -> Baseline:
    return Baseline.model_validate_json((baseline_dir / f"{_check_name(name)}.json").read_text(encoding="utf-8"))


def _deltas(key: KeyElement, rect: list[float], styles: dict[str, str]) -> list[StyleDelta]:
    out = [StyleDelta(key=key.spec, selector=key.selector, property=prop, before=before, after=styles.get(prop, "")) for prop, before in key.styles.items() if styles.get(prop, "") != before]
    for prop, before, after in zip(GEOMETRY, key.rect, rect):
        if abs(before - after) >= 0.5:
            out.append(StyleDelta(key=key.spec, selector=key.selector, property=prop, before=f"{before:g}px", after=f"{round(after, 2):g}px"))
    return out


async def _current(page: "Page", key: KeyElement) -> tuple[list[float], dict[str, str]] | None:
    locator = locator_for(page, [], key.selector)
    if await locator.count() == 0:
        return None
    measured = await probe_element(locator, "styles")
    return [round(v, 2) for v in measured["rect"]], measured["styles"]


def _element_finding(key: KeyElement, deltas: list[StyleDelta]) -> Finding:
    changes = {d.property: [d.before, d.after] for d in deltas}
    summary = ", ".join(f"{p} {b} → {a}" for p, (b, a) in list(changes.items())[:6])
    return Finding(
        id=finding_id("style-regression", [], key.selector),
        kind="visual",
        rule="style-regression",
        impact="moderate",
        selector=key.selector,
        element_desc=key.desc,
        message=f"Computed styles changed since the baseline: {summary}",
        measured={"deltas": changes},
        hints=key.hints,
    )


def _revert_fixes(finding: Finding, deltas: list[StyleDelta]) -> list[FixSuggestion]:
    hint = next((h for h in finding.hints if h.startswith(("#", "."))), finding.selector)
    return [
        FixSuggestion(
            finding_ref=finding.id,
            property_or_attr=d.property,
            current=d.after,
            suggested=d.before,
            changes={d.property: d.before},
            method="css_override",
            snippet=f"{hint} {{ {d.property}: {d.before}; }}",
            rationale=f"Restores the baseline value of {d.property} ({d.after} now, {d.before} in the baseline).",
            confidence=0.6,
        )
        for d in deltas
        if d.property not in GEOMETRY and d.property not in NOT_REVERTED
    ]


async def compare(
    page: "Page",
    baseline: Baseline,
    *,
    baseline_dir: Path,
    out_dir: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    tolerance: int = DEFAULT_TOLERANCE,
    scale: float = DEFAULT_SCALE,
    mask: Sequence[ElementSpec] = (),
    snapshot: Snapshot | None = None,
    verify: bool = True,
) -> VisualDiff:
    """Diff the live page against ``baseline``: pixels (masks from both captures excluded) and key-element styles."""
    before = Image.open(baseline_dir / baseline.png)
    masks = [*baseline.masks, *await _mask_rects(page, [*baseline.mask_spec, *mask], snapshot, baseline.full_page)]
    after_png = await _screenshot(page, baseline.full_page)
    diff = diff_images(before, Image.open(io.BytesIO(after_png)), masks=masks, tolerance=tolerance, scale=scale)
    findings: list[Finding] = []
    fixes: list[FixSuggestion] = []
    all_deltas: list[StyleDelta] = []
    missing: list[str] = []
    for key in baseline.key_elements:
        current = await _current(page, key)
        if current is None:
            missing.append(key.spec)
            continue
        deltas = _deltas(key, *current)
        if deltas:
            finding = _element_finding(key, deltas)
            findings.append(finding)
            fixes.extend(_revert_fixes(finding, deltas))
            all_deltas.extend(deltas)
    evidence = _write_evidence(out_dir, baseline, before, after_png, diff.diff_png)
    changed = diff.ratio > threshold or diff.size_changed
    if changed:
        findings.insert(0, _page_finding(baseline, diff.ratio, diff.regions, evidence))
    if verify and fixes:
        fixes = await _verify_reverts(page, findings, fixes, baseline, before, masks, tolerance, scale)
    return VisualDiff(
        name=baseline.name,
        diff_ratio=diff.ratio,
        changed=changed,
        regions=diff.regions,
        size_before=list(diff.size_before),
        size_after=list(diff.size_after),
        style_deltas=all_deltas,
        missing=missing,
        evidence=evidence,
        findings=findings,
        fixes=fixes,
    )


def _page_finding(baseline: Baseline, ratio: float, regions: list[list[int]], evidence: dict[str, str]) -> Finding:
    return Finding(
        id=finding_id("visual-diff", [], "html", baseline.name),
        kind="visual",
        rule="visual-diff",
        impact="moderate",
        selector="html",
        element_desc=baseline.url,
        message=f"{ratio:.2%} of compared pixels differ from baseline {baseline.name!r} in {len(regions)} region(s)",
        measured={"diff_ratio": ratio, "regions": regions},
        evidence=[evidence["diff"]] if "diff" in evidence else [],
    )


def _write_evidence(out_dir: Path | None, baseline: Baseline, before: Image.Image, after_png: bytes, diff_png: bytes) -> dict[str, str]:
    if out_dir is None:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"before": out_dir / f"{baseline.name}.before.png", "after": out_dir / f"{baseline.name}.after.png", "diff": out_dir / f"{baseline.name}.diff.png"}
    before.save(paths["before"], format="PNG")
    paths["after"].write_bytes(after_png)
    paths["diff"].write_bytes(diff_png)
    return {key: str(path) for key, path in paths.items()}


async def _verify_reverts(
    page: "Page", findings: list[Finding], fixes: list[FixSuggestion], baseline: Baseline, before: Image.Image, masks: list[list[float]], tolerance: int, scale: float
) -> list[FixSuggestion]:
    """Apply every revert at once, re-measure styles and pixels, undo; a fix is verified when its
    property is back at the baseline value."""
    by_id = {f.id: f for f in findings}
    keys = {k.selector: k for k in baseline.key_elements}
    async with AsyncExitStack() as stack:
        for fix in fixes:
            await stack.enter_async_context(apply_fix(page, by_id[fix.finding_ref], fix))
        after = diff_images(before, Image.open(io.BytesIO(await _screenshot(page, baseline.full_page))), masks=masks, tolerance=tolerance, scale=scale)
        styles = {}
        for selector in {by_id[fix.finding_ref].selector for fix in fixes}:
            current = await _current(page, keys[selector])
            styles[selector] = current[1] if current else {}
    out = []
    for fix in fixes:
        value = styles[by_id[fix.finding_ref].selector].get(fix.property_or_attr, "")
        out.append(fix.model_copy(update={"verified": value == fix.suggested, "after_fix": {fix.property_or_attr: value, "diff_ratio": after.ratio}}))
    return out


async def classify_diff(decider: DecisionEngine, before_png: bytes, after_png: bytes, diff_png: bytes, *, deltas: Sequence[StyleDelta] = (), context: str = "") -> DiffClassification:
    """Ask the decision engine which kind of change a visual diff shows (it sees all three images)."""
    state: dict[str, Any] = {
        "task": "Compare the baseline screenshot (before) with the current one (after). The diff image marks changed pixels in red.",
        "before": ImagePart.from_bytes(shrink(before_png), "image/png"),
        "after": ImagePart.from_bytes(shrink(after_png), "image/png"),
        "diff": ImagePart.from_bytes(shrink(diff_png), "image/png"),
        "style_deltas": [d.model_dump() for d in deltas][:40],
    }
    if context:
        state["context"] = context
    question = ChoiceQuestion(instructions="What kind of change is this?", criteria=DIFF_KINDS)
    decision = await decider.decide(DecisionRequest(state=state, questions={"diff_kind": question}), purpose="visual_diff")
    answer = decision.choice("diff_kind")
    return DiffClassification(label=answer.choice, confidence=answer.confidence, probabilities=dict(answer.probabilities), engine=decision.engine)
