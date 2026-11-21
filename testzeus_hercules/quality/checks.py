"""Deterministic accessibility checks measured on the live page.

* contrast of text against its effective background (SC 1.4.3 / 1.4.6),
* programmatic names of controls (SC 4.1.2),
* target size (SC 2.5.8, 24×24 CSS px with the spacing exception),
* images without a text alternative (SC 1.1.1),
* keyboard reachability, traps and visible focus (SC 2.1.1, 2.1.2, 2.4.7), by pressing Tab.

Every number in a finding comes from the page or from :mod:`testzeus_hercules.quality.color`.
Cross-origin frames are not scanned.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from testzeus_hercules.perceive.model import Snapshot
from testzeus_hercules.quality.color import RGBA, contrast_ratio, flatten, floor2, is_large_text, required_ratio
from testzeus_hercules.quality.findings import Finding, Impact, Kind, Level, finding_id, selector_of
from testzeus_hercules.quality.frames import FrameInfo, list_frames, md_for, probe_frame

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

__all__ = [
    "KeyboardResult",
    "MIN_TARGET_PX",
    "check_contrast",
    "check_images",
    "check_keyboard",
    "check_names",
    "check_target_size",
    "contrast_measure",
    "focus_changes",
    "make_finding",
    "run_checks",
]

MIN_TARGET_PX = 24.0
_SPACING_RADIUS = MIN_TARGET_PX / 2
_MAX_TABS = 400
_VISIBLE_FOCUS_PROPS = (
    "outline-style",
    "outline-width",
    "outline-color",
    "box-shadow",
    "border-top-color",
    "border-right-color",
    "border-bottom-color",
    "border-left-color",
    "border-top-width",
    "border-bottom-width",
    "background-color",
    "background-image",
    "color",
    "text-decoration-line",
    "text-decoration-color",
    "transform",
    "filter",
    "opacity",
)


def make_finding(
    info: FrameInfo,
    rec: dict[str, Any],
    *,
    rule: str,
    kind: Kind = "a11y",
    wcag: str | None,
    level: Level,
    impact: Impact,
    message: str,
    measured: dict[str, Any] | None = None,
    snapshot: Snapshot | None = None,
    source: str = "hercules",
    extra_id: str = "",
    help_url: str | None = None,
) -> Finding:
    """A finding for the element a probe record describes, with selector, md and box filled in."""
    selector = selector_of(rec["path"])
    rect = rec.get("rect")
    values = dict(measured or {})
    if rect is not None:
        values.setdefault("bbox", [round(rect[0] + info.offset[0], 1), round(rect[1] + info.offset[1], 1), round(rect[2], 1), round(rect[3], 1)])
    return Finding(
        id=finding_id(rule, info.frame_path, selector, extra_id),
        kind=kind,
        rule=rule,
        source=source,  # type: ignore[arg-type]
        wcag=wcag,
        level=level,
        impact=impact,
        element_md=md_for(snapshot, info.chain, rec["path"]),
        selector=selector,
        frame_path=info.frame_path,
        frame=info.label,
        element_desc=rec.get("desc", ""),
        message=message,
        measured=values,
        hints=list(rec.get("tokens", [])),
        help_url=help_url,
    )


async def _frames(page: "Page", frames: Sequence[FrameInfo] | None) -> list[FrameInfo]:
    found = list(frames) if frames is not None else await list_frames(page)
    return [info for info in found if info.same_origin]


# --------------------------------------------------------------------------- contrast


def contrast_measure(rec: dict[str, Any], level: str = "AA") -> dict[str, Any]:
    """Ratio, colours and threshold for one text element record from the probe."""
    bg = flatten([RGBA.parse(layer) for layer in rec["bg"]])
    fg_raw = RGBA.parse(rec["fg"])
    fg = RGBA(fg_raw.r, fg_raw.g, fg_raw.b, fg_raw.a * float(rec.get("opacity", 1.0)))
    large = is_large_text(float(rec["font_size"]), float(rec["font_weight"]))
    ratio = contrast_ratio(fg, bg)
    required = required_ratio(large, level)
    return {
        "ratio": floor2(ratio),
        "fg": fg.hex,
        "bg": bg.hex,
        "font_size_px": round(float(rec["font_size"]), 2),
        "font_weight": int(rec["font_weight"]),
        "large_text": large,
        "required": required,
        "passes": floor2(ratio) >= required,
    }


async def check_contrast(page: "Page", *, snapshot: Snapshot | None = None, level: str = "AA", frames: Sequence[FrameInfo] | None = None) -> list[Finding]:
    """Text whose contrast with its effective background is below the WCAG threshold."""
    rule, wcag = ("color-contrast-enhanced", "1.4.6") if level == "AAA" else ("color-contrast", "1.4.3")
    findings = []
    for info in await _frames(page, frames):
        for rec in await probe_frame(info, "contrast"):
            if rec["disabled"] or rec["bg_image"]:
                continue  # inactive controls are exempt; text over images needs a pixel check, not a style one
            measured = contrast_measure(rec, level)
            if measured["passes"]:
                continue
            message = f"Text contrast {measured['ratio']}:1 ({measured['fg']} on {measured['bg']}) is below {measured['required']}:1"
            findings.append(
                make_finding(info, rec, rule=rule, wcag=wcag, level="AAA" if level == "AAA" else "AA", impact="serious", message=message, measured={**measured, "text": rec["text"]}, snapshot=snapshot)
            )
    return findings


# --------------------------------------------------------------------------- names


async def check_names(page: "Page", *, snapshot: Snapshot | None = None, frames: Sequence[FrameInfo] | None = None) -> list[Finding]:
    """Controls without a programmatic name; a field captioned only by nearby text counts as unnamed."""
    findings = []
    for info in await _frames(page, frames):
        for rec in await probe_frame(info, "names"):
            if rec["name"]:
                continue
            if rec["caption"]:
                message = f"{rec['role']} shows the caption {rec['caption']!r} but it is not tied to the control, so it has no accessible name"
            else:
                message = f"{rec['role']} has no accessible name"
            measured = {"role": rec["role"], "name": "", "caption": rec["caption"], "near": rec["near"], "icon_hints": rec["icon_hints"]}
            findings.append(make_finding(info, rec, rule="control-name", wcag="4.1.2", level="A", impact="critical", message=message, measured=measured, snapshot=snapshot))
    return findings


# --------------------------------------------------------------------------- target size


def _point_rect_distance(x: float, y: float, rect: Sequence[float]) -> float:
    dx = max(rect[0] - x, 0.0, x - (rect[0] + rect[2]))
    dy = max(rect[1] - y, 0.0, y - (rect[1] + rect[3]))
    return math.hypot(dx, dy)


def _center(rect: Sequence[float]) -> tuple[float, float]:
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def _undersized(rect: Sequence[float]) -> bool:
    return rect[2] < MIN_TARGET_PX - 0.01 or rect[3] < MIN_TARGET_PX - 0.01


def spacing_conflict(rect: Sequence[float], others: Sequence[Sequence[float]]) -> tuple[float, int] | None:
    """SC 2.5.8 spacing exception: the 24px circle on an undersized target must not touch another
    target or another undersized target's circle. Returns (distance, index) of the first conflict."""
    cx, cy = _center(rect)
    for index, other in enumerate(others):
        distance = _point_rect_distance(cx, cy, other)
        if distance < _SPACING_RADIUS:
            return round(distance, 1), index
        if _undersized(other):
            ox, oy = _center(other)
            if math.hypot(cx - ox, cy - oy) < MIN_TARGET_PX:
                return round(math.hypot(cx - ox, cy - oy), 1), index
    return None


async def check_target_size(page: "Page", *, snapshot: Snapshot | None = None, frames: Sequence[FrameInfo] | None = None) -> list[Finding]:
    """Targets smaller than 24×24 CSS px that are also too close to their neighbours."""
    findings = []
    for info in await _frames(page, frames):
        targets = [rec for rec in await probe_frame(info, "targets") if not rec["inline_text_link"]]
        for index, rec in enumerate(targets):
            rect = rec["rect"]
            if not _undersized(rect):
                continue
            others = [t["rect"] for i, t in enumerate(targets) if i != index]
            conflict = spacing_conflict(rect, others)
            if conflict is None:
                continue
            distance, other_index = conflict
            neighbour = [t for i, t in enumerate(targets) if i != index][other_index]
            measured = {"width": round(rect[2], 2), "height": round(rect[3], 2), "min": MIN_TARGET_PX, "display": rec["display"], "nearest_target": neighbour["desc"], "distance": distance}
            message = f"Target is {measured['width']:g}×{measured['height']:g} px (min {MIN_TARGET_PX:g}×{MIN_TARGET_PX:g}) and {distance:g} px from another target"
            findings.append(make_finding(info, rec, rule="target-size", wcag="2.5.8", level="AA", impact="moderate", message=message, measured=measured, snapshot=snapshot))
    return findings


# --------------------------------------------------------------------------- images


def file_stem(src: str) -> str:
    """The file name of an image URL without extension: ``.../team-photo.svg?x=1`` -> ``team-photo``."""
    path = unquote(urlsplit(src).path) if src and not src.startswith("data:") else ""
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


async def check_images(page: "Page", *, snapshot: Snapshot | None = None, frames: Sequence[FrameInfo] | None = None) -> list[Finding]:
    """Images, image buttons and ``role=img`` elements with no text alternative (and not marked decorative)."""
    findings = []
    for info in await _frames(page, frames):
        for rec in await probe_frame(info, "images"):
            measured = {"src": rec["src"], "file": file_stem(rec["src"]), "natural": rec["natural"]}
            findings.append(make_finding(info, rec, rule="image-alt", wcag="1.1.1", level="A", impact="critical", message="Image has no text alternative (alt)", measured=measured, snapshot=snapshot))
    return findings


# --------------------------------------------------------------------------- keyboard


Key = tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]


@dataclass
class KeyboardResult:
    """Focus order reached by Tab, and the findings it produced."""

    order: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    traps: list[str] = field(default_factory=list)


def focus_changes(unfocused: dict[str, Any], focused: dict[str, Any]) -> list[str]:
    """Style properties (own, or ``parent.`` prefixed) that visibly change when the element takes focus."""
    changed = []
    for scope in ("self", "parent"):
        before = unfocused.get(scope, {})
        after = focused.get(scope, {})
        for prop in _VISIBLE_FOCUS_PROPS:
            if prop not in after or before.get(prop) == after.get(prop):
                continue
            if prop.startswith("outline") and (after.get("outline-style") == "none" or after.get("outline-width") in ("0px", "")):
                continue
            changed.append(prop if scope == "self" else f"parent.{prop}")
    return changed


async def _active(frames: dict[tuple[tuple[str, ...], ...], FrameInfo], top: FrameInfo) -> tuple[FrameInfo, dict[str, Any], bool] | None:
    """The deeply focused element: (frame, record, inside an unscanned frame)."""
    info = top
    while True:
        rec = await probe_frame(info, "active")
        if rec is None:
            return None
        if not rec["frame"]:
            return info, rec, False
        child = frames.get(info.chain + (tuple(rec["path"]),))
        if child is None or not child.same_origin:
            return info, rec, True
        info = child


async def check_keyboard(page: "Page", *, snapshot: Snapshot | None = None, frames: Sequence[FrameInfo] | None = None, max_tabs: int | None = None) -> KeyboardResult:
    """Tab through the page: record focus order, traps, unreachable controls and invisible focus.

    The page's focus and scroll position are restored afterwards.
    """
    from testzeus_hercules.quality.frames import locator_for, probe_element

    all_frames = list(frames) if frames is not None else await list_frames(page)
    by_chain = {info.chain: info for info in all_frames}
    top = by_chain[()]
    scanned = [info for info in all_frames if info.same_origin]
    scroll = await probe_frame(top, "scroll")
    await page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    expected: dict[Key, tuple[FrameInfo, dict[str, Any]]] = {}
    for info in scanned:
        found = await probe_frame(info, "focusables")
        for rec in found["controls"] + found["generics"]:
            expected[(info.chain, tuple(rec["path"]))] = (info, rec)
    limit = max_tabs or min(_MAX_TABS, 2 * len(expected) + 10)
    order: list[Key] = []
    focused: dict[Key, dict[str, Any]] = {}
    where: dict[Key, tuple[FrameInfo, dict[str, Any]]] = {}
    traps: list[Key] = []
    previous: Key | None = None
    for _ in range(limit):
        await page.keyboard.press("Tab")
        active = await _active(by_chain, top)
        if active is None:
            if order:
                break
            continue
        info, rec, opaque = active
        key: Key = (info.chain, tuple(rec["path"]))
        if not opaque and (key == previous or (key in focused and key != order[0])):
            traps.append(key)
            escaped = await probe_element(locator_for(page, info.frame_path, selector_of(rec["path"])), "focus_after")
            after = await _active(by_chain, top) if escaped else None
            if after is None:
                break
            info, rec, opaque = after
            key = (info.chain, tuple(rec["path"]))
            if key in focused:
                break
        elif order and key == order[0]:
            break
        if key not in focused:
            order.append(key)
        focused[key] = rec["styles"]
        where[key] = (info, rec)
        previous = key
    await page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    await probe_frame(top, "scroll", to=scroll)
    return KeyboardResult(
        order=[" | ".join([*(selector_of(c) for c in k[0]), selector_of(k[1])]) for k in order],
        findings=_keyboard_findings(expected, focused, where, traps, snapshot),
        traps=[selector_of(k[1]) for k in traps],
    )


def _keyboard_findings(
    expected: dict[Key, tuple[FrameInfo, dict[str, Any]]],
    focused: dict[Key, dict[str, Any]],
    where: dict[Key, tuple[FrameInfo, dict[str, Any]]],
    traps: list[Key],
    snapshot: Snapshot | None,
) -> list[Finding]:
    findings = []
    reached_groups = {(k[0], expected[k][1]["radio_group"]) for k in focused if k in expected and expected[k][1]["radio_group"]}
    for key in dict.fromkeys(traps):
        info, rec = where.get(key) or expected[key]
        findings.append(
            make_finding(
                info, rec, rule="keyboard-trap", wcag="2.1.2", level="A", impact="critical", message="Keyboard focus cannot leave this element with Tab", measured={"key": "Tab"}, snapshot=snapshot
            )
        )
    for key, (info, rec) in expected.items():
        if key in focused or rec["disabled"] or rec["composite"]:
            continue
        if rec["radio_group"] and (key[0], rec["radio_group"]) in reached_groups:
            continue
        what = "clickable element" if rec["generic"] else rec["role"]
        measured = {"role": rec["role"], "tab_index": rec["tab_index"], "generic": rec["generic"], "tab_stops": len(focused)}
        findings.append(
            make_finding(
                info, rec, rule="keyboard-unreachable", wcag="2.1.1", level="A", impact="serious", message=f"{what} {rec['name']!r} is never reached with Tab", measured=measured, snapshot=snapshot
            )
        )
    for key, styles in focused.items():
        if key not in expected or key in traps:
            continue
        info, rec = expected[key]
        if focus_changes(rec["focus"], styles):
            continue
        measured = {"outline_style": styles["self"].get("outline-style"), "outline_width": styles["self"].get("outline-width"), "box_shadow": styles["self"].get("box-shadow"), "changed": []}
        findings.append(
            make_finding(
                info,
                rec,
                rule="focus-not-visible",
                wcag="2.4.7",
                level="AA",
                impact="serious",
                message="Keyboard focus is not visible: no style changes when this element is focused",
                measured=measured,
                snapshot=snapshot,
            )
        )
    return findings


# --------------------------------------------------------------------------- all


async def run_checks(page: "Page", *, snapshot: Snapshot | None = None, level: str = "AA", keyboard: bool = True) -> tuple[list[Finding], list[str]]:
    """Every deterministic check; returns the findings and the frames that were not scanned."""
    frames = await list_frames(page)
    findings = [
        *await check_contrast(page, snapshot=snapshot, level=level, frames=frames),
        *await check_names(page, snapshot=snapshot, frames=frames),
        *await check_target_size(page, snapshot=snapshot, frames=frames),
        *await check_images(page, snapshot=snapshot, frames=frames),
    ]
    if keyboard:
        findings.extend((await check_keyboard(page, snapshot=snapshot, frames=frames)).findings)
    return findings, [info.url for info in frames if not info.same_origin]
