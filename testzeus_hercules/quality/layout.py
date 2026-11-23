"""Layout checks across viewport widths: overlapping controls, clipped text and controls off screen.

Each width is checked from element boxes and computed styles. Every problem gets a short ladder of
candidate CSS changes computed from the measurements; candidates are tried on the live page in order
and the first one that resolves the problem is reported as ``verified`` (else the first candidate,
unverified). The original viewport is restored afterwards.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from testzeus_hercules.perceive.model import Snapshot
from testzeus_hercules.perceive.snapshot import wait_until_rendered
from testzeus_hercules.quality.checks import make_finding
from testzeus_hercules.quality.findings import Finding, FixSuggestion, selector_of
from testzeus_hercules.quality.fixes import likely_file, register_recheck, verify_fix
from testzeus_hercules.quality.frames import FrameInfo, locator_for, probe_element, probe_frame

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_WIDTHS", "LayoutResult", "check_layout"]

DEFAULT_WIDTHS: tuple[int, ...] = (375, 768, 1280)
GAP_PX = 8
_SIDES = ("top", "right", "bottom", "left")


class LayoutResult(BaseModel):
    """Layout findings and verified fixes at one viewport width."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int
    height: int
    page_width: int
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[FixSuggestion] = Field(default_factory=list)


# --------------------------------------------------------------------------- re-checks used by verify_fix


async def _recheck_overlap(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    other = locator_for(page, finding.frame_path, (finding.measured or {})["other"])
    handle = await other.element_handle()
    try:
        box = (await probe_element(element, "overlap", other=handle))["box"]
    finally:
        await handle.dispose()
    return box is None, {"overlap_px": [round(box[2], 1), round(box[3], 1)] if box else [0, 0]}


async def _recheck_clip(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    rec = await probe_element(element, "clip")
    right = rec["rect"][0] + rec["rect"][2]
    fits = right <= rec["viewport"][0] + 1
    after = {"scroll_width": rec["scroll_width"], "client_width": rec["client_width"], "scroll_height": rec["scroll_height"], "client_height": rec["client_height"], "right": round(right, 1)}
    return not rec["clipped_x"] and not rec["clipped_y"] and fits, after


async def _recheck_offscreen(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    rec = await probe_element(element, "rect")
    left, right = rec["rect"][0], rec["rect"][0] + rec["rect"][2]
    return left >= -1 and right <= rec["viewport"][0] + 1, {"left": round(left, 1), "right": round(right, 1), "viewport_width": rec["viewport"][0]}


register_recheck("overlap", ["layout-overlap"], _recheck_overlap)
register_recheck("clip", ["text-clipped"], _recheck_clip)
register_recheck("offscreen", ["off-viewport"], _recheck_offscreen)


# --------------------------------------------------------------------------- candidate fixes


def _candidate(finding: Finding, changes: dict[str, str], *, target: str | None, hint: str, rationale: str, confidence: float = 0.6, current: str | None = None) -> FixSuggestion:
    declarations = "; ".join(f"{k}: {v}" for k, v in changes.items())
    return FixSuggestion(
        finding_ref=finding.id,
        property_or_attr=", ".join(changes),
        current=current,
        suggested=declarations,
        changes=changes,
        target_selector=target,
        method="css_override",
        snippet=f"{hint} {{ {declarations}; }}",
        rationale=rationale,
        confidence=confidence,
    )


def _hint(hints: Sequence[str], fallback: str) -> str:
    return next((h for h in hints if h.startswith(("#", "."))), fallback)


def _overlap_candidates(finding: Finding, pair: Mapping[str, Any]) -> list[FixSuggestion]:
    a, b = pair["a"], pair["b"]
    box = pair["box"]
    out: list[FixSuggestion] = []
    for rec, target in ((a, None), (b, selector_of(b["path"]))):
        for side, margin in zip(_SIDES, rec["layout"]["margins"]):
            if margin < 0:
                out.append(
                    _candidate(
                        finding,
                        {f"margin-{side}": "0px"},
                        target=target,
                        hint=_hint(rec["tokens"], selector_of(rec["path"])),
                        current=f"{margin:g}px",
                        rationale=f"A negative margin-{side} pulls the control over its neighbour.",
                    )
                )
    positioned = {"absolute", "fixed"}
    a_pos, b_pos = a["layout"]["position"] in positioned, b["layout"]["position"] in positioned
    if a_pos != b_pos:
        flow, floating = (b, a) if a_pos else (a, b)
        target = None if flow is a else selector_of(flow["path"])
        flow_cx = flow["rect"][0] + flow["rect"][2] / 2
        float_cx = floating["rect"][0] + floating["rect"][2] / 2
        if box[2] <= box[3] or abs(float_cx - flow_cx) > flow["rect"][2] / 4:
            side = "right" if float_cx > flow_cx else "left"
            space = round(box[2] + GAP_PX)
        else:
            side = "bottom" if floating["rect"][1] > flow["rect"][1] else "top"
            space = round(box[3] + GAP_PX)
        out.append(
            _candidate(
                finding,
                {f"margin-{side}": f"{space}px"},
                target=target,
                hint=_hint(flow["tokens"], selector_of(flow["path"])),
                current=f"{flow['layout']['margins'][_SIDES.index(side)]:g}px",
                rationale=f"Reserves {space}px on the {side} for the positioned control it collides with ({box[2]:.0f}×{box[3]:.0f}px overlap plus a {GAP_PX}px gap).",
            )
        )
    parent = a["layout"]["parent"]
    if parent and parent["display"] in ("flex", "inline-flex") and parent["flex_wrap"] == "nowrap" and parent["path"] == (b["layout"]["parent"] or {}).get("path"):
        out.append(
            _candidate(
                finding,
                {"flex-wrap": "wrap"},
                target=selector_of(parent["path"]),
                hint=selector_of(parent["path"]),
                current="nowrap",
                rationale="Lets the row wrap instead of squeezing its controls on top of each other.",
            )
        )
    wider = a if a["rect"][2] >= b["rect"][2] else b
    width = round(wider["rect"][2] - box[2] - GAP_PX)
    if width > 0:
        out.append(
            _candidate(
                finding,
                {"max-width": f"{width}px"},
                target=None if wider is a else selector_of(wider["path"]),
                hint=_hint(wider["tokens"], selector_of(wider["path"])),
                current=f"{wider['rect'][2]:g}px",
                rationale=f"Narrows the wider control by the overlap ({box[2]:.0f}px) plus a {GAP_PX}px gap.",
                confidence=0.4,
            )
        )
    return out


def _px(styles: Mapping[str, str], *names: str) -> float:
    return sum(float(str(styles.get(name, "0")).rstrip("px") or 0) for name in names)


async def _clip_candidates(page: "Page", finding: Finding, rec: Mapping[str, Any]) -> list[FixSuggestion]:
    hint = _hint(rec["tokens"], finding.selector)
    out = []
    if rec["clipped_x"] and rec["white_space"] in ("nowrap", "pre"):
        out.append(_candidate(finding, {"white-space": "normal"}, target=None, hint=hint, current=rec["white_space"], rationale="Lets the text wrap inside its box instead of being cut off."))
    styles = (await probe_element(locator_for(page, finding.frame_path, finding.selector), "styles"))["styles"]
    if rec["clipped_x"]:
        needed = rec["scroll_width"] + (_px(styles, "border-left-width", "border-right-width") if styles.get("box-sizing") == "border-box" else -_px(styles, "padding-left", "padding-right"))
        out.append(
            _candidate(
                finding,
                {"min-width": f"{round(needed)}px"},
                target=None,
                hint=hint,
                current=rec["width"],
                rationale=f"The text needs {rec['scroll_width']}px; the box gives it {rec['client_width']}px.",
            )
        )
    if rec["clipped_y"]:
        out.append(
            _candidate(
                finding,
                {"height": "auto", "min-height": f"{rec['client_height']}px"},
                target=None,
                hint=hint,
                current=styles.get("height"),
                rationale=f"The content is {rec['scroll_height']}px tall in a {rec['client_height']}px box.",
            )
        )
    out.append(_candidate(finding, {"overflow": "visible"}, target=None, hint=hint, current=f"{rec['overflow_x']} {rec['overflow_y']}", rationale="Stops hiding the overflowing text.", confidence=0.3))
    return out


def _offscreen_candidates(finding: Finding, rec: Mapping[str, Any]) -> list[FixSuggestion]:
    viewport = rec["viewport"][0]
    out = []
    for ancestor in rec["ancestors"]:
        overflowing = ancestor["scroll_width"] > ancestor["client_width"] + 1 or ancestor["rect"][0] + ancestor["rect"][2] > viewport + 1
        if ancestor["display"] in ("flex", "inline-flex") and ancestor["flex_wrap"] == "nowrap" and overflowing:
            out.append(
                _candidate(
                    finding,
                    {"flex-wrap": "wrap"},
                    target=selector_of(ancestor["path"]),
                    hint=_hint(ancestor["tokens"], selector_of(ancestor["path"])),
                    current="nowrap",
                    rationale=f"The row is {ancestor['scroll_width']}px wide in a {viewport}px viewport; wrapping keeps every control on screen.",
                )
            )
            break
    for ancestor in rec["ancestors"]:
        if ancestor["rect"][2] > viewport + 1:
            out.append(
                _candidate(
                    finding,
                    {"max-width": "100%"},
                    target=selector_of(ancestor["path"]),
                    hint=_hint(ancestor["tokens"], selector_of(ancestor["path"])),
                    current=ancestor["width"],
                    rationale=f"The container is {ancestor['rect'][2]:.0f}px wide, more than the {viewport}px viewport.",
                )
            )
            break
    out.append(_candidate(finding, {"max-width": "100%"}, target=None, hint=_hint(rec["tokens"], finding.selector), rationale="Keeps the control within its container.", confidence=0.3))
    return out


async def _first_verified(page: "Page", finding: Finding, candidates: Sequence[FixSuggestion], files_changed: Sequence[str], file_contents: Mapping[str, str] | None) -> FixSuggestion | None:
    first: FixSuggestion | None = None
    for candidate in candidates:
        checked = await verify_fix(page, finding, candidate)
        checked = checked.model_copy(update={"likely_file": likely_file(finding.hints, files_changed, file_contents)})
        if checked.verified:
            return checked
        first = first or checked
    return first


# --------------------------------------------------------------------------- per width


async def _at_width(
    page: "Page", info: FrameInfo, width: int, height: int, snapshot: Snapshot | None, suggest: bool, files_changed: Sequence[str], file_contents: Mapping[str, str] | None
) -> LayoutResult:
    scan = await probe_frame(info, "layout")
    findings: list[Finding] = []
    fixes: list[FixSuggestion] = []
    tag = f"w{width}"
    for pair in scan["overlaps"]:
        a, b = pair["a"], pair["b"]
        measured = {
            "viewport_width": width,
            "overlap_px": [round(pair["box"][2], 1), round(pair["box"][3], 1)],
            "other": selector_of(b["path"]),
            "other_desc": b["desc"],
            "on_top": [a, b][pair["on_top"]]["desc"] if pair["on_top"] >= 0 else None,
        }
        finding = make_finding(
            info,
            a,
            rule="layout-overlap",
            kind="layout",
            wcag=None,
            level="none",
            impact="serious",
            message=f"Overlaps {b['desc']} by {measured['overlap_px'][0]:g}×{measured['overlap_px'][1]:g}px at {width}px wide",
            measured=measured,
            snapshot=snapshot,
            extra_id=tag + selector_of(b["path"]),
        )
        findings.append(finding)
        if suggest:
            fix = await _first_verified(page, finding, _overlap_candidates(finding, pair), files_changed, file_contents)
            fixes.extend([fix] if fix else [])
    for rec in scan["clipped"]:
        measured = {
            "viewport_width": width,
            "scroll_width": rec["scroll_width"],
            "client_width": rec["client_width"],
            "scroll_height": rec["scroll_height"],
            "client_height": rec["client_height"],
            "overflow": [rec["overflow_x"], rec["overflow_y"]],
            "white_space": rec["white_space"],
            "text": rec["text"],
        }
        finding = make_finding(
            info,
            rec,
            rule="text-clipped",
            kind="layout",
            wcag=None,
            level="none",
            impact="moderate",
            message=f"Text is cut off: needs {rec['scroll_width']}×{rec['scroll_height']}px, shows {rec['client_width']}×{rec['client_height']}px at {width}px wide",
            measured=measured,
            snapshot=snapshot,
            extra_id=tag,
        )
        findings.append(finding)
        if suggest:
            fix = await _first_verified(page, finding, await _clip_candidates(page, finding, rec), files_changed, file_contents)
            fixes.extend([fix] if fix else [])
    for rec in scan["offscreen"]:
        right = rec["rect"][0] + rec["rect"][2]
        measured = {"viewport_width": width, "left": round(rec["rect"][0], 1), "right": round(right, 1), "page_width": scan["page_width"]}
        finding = make_finding(
            info,
            rec,
            rule="off-viewport",
            kind="layout",
            wcag="1.4.10",
            level="AA",
            impact="serious",
            message=f"Control ends at {right:.0f}px, outside the {width}px viewport",
            measured=measured,
            snapshot=snapshot,
            extra_id=tag,
        )
        findings.append(finding)
        if suggest:
            fix = await _first_verified(page, finding, _offscreen_candidates(finding, rec), files_changed, file_contents)
            fixes.extend([fix] if fix else [])
    return LayoutResult(width=width, height=height, page_width=scan["page_width"], findings=findings, fixes=fixes)


async def check_layout(
    page: "Page",
    *,
    widths: Sequence[int] = DEFAULT_WIDTHS,
    height: int | None = None,
    snapshot: Snapshot | None = None,
    suggest: bool = True,
    files_changed: Sequence[str] = (),
    file_contents: Mapping[str, str] | None = None,
) -> list[LayoutResult]:
    """Overlap, clipping and off-screen checks of the top frame at each width; viewport restored after."""
    original = page.viewport_size
    tall = height or (original["height"] if original else 800)
    results = []
    try:
        for width in widths:
            await page.set_viewport_size({"width": width, "height": tall})
            await wait_until_rendered(page, quiet_ms=50, cap_ms=1000)
            top = FrameInfo(page.main_frame, (), None, (0.0, 0.0), page.url, True)
            results.append(await _at_width(page, top, width, tall, snapshot, suggest, files_changed, file_contents))
    finally:
        if original:
            await page.set_viewport_size(original)
    return results
