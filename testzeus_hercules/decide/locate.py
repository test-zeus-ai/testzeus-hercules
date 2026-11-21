"""Where an element is on the page, in a few words a decision model can match against the screenshot.

Decision models favour the first option of a long list and cannot tell apart elements
that share a role and a name ("Add to cart" on every product card) from their text
alone. Each candidate therefore carries a short location derived from the Set-of-Mark
snapshot: the screen region and approximate position of its centre, the nearest heading
above it, its place among same-named elements (with row and column when they form a
grid), and whether it sits in an iframe or a shadow root. For example::

    top-right (x 1180, y 40), under "Your cart", #2 of 4 alike (row 1, col 2), in frame "payment"

Positions are CSS pixels of the top-level viewport, measured from its top-left corner,
rounded to 10 px. Everything here is a pure function of the snapshot.

The heading and dialog come from the element's own ancestry when perception read it (the
dialog it is in, and the nearest heading before it inside its own section, landmark or
dialog), never from what is drawn above it on screen: a field in a dialog over a list page
is "in dialog "New record", under "Details"", not under the list's heading. An element
scrolled out of its scroll area (a long form inside a dialog) says so instead of giving a
position on the page::

    in dialog "New record", under "Address", in the dialog's scroll area, below the visible part

Snapshots without that context (look-alikes in tests) fall back to the nearest heading
above the element on screen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["describe_locations", "region_of"]

_ROUND_PX = 10
_SAME_LINE_PX = 12
_HEADING_CLIP = 40
_FRAME_CLIP = 40
_VERTICAL = ("top", "middle", "bottom")
_HORIZONTAL = ("left", "center", "right")


def region_of(x: float, y: float, width: float, height: float) -> str:
    """The ninth of a ``width`` x ``height`` viewport a point falls in ("top-left" … "bottom-right"), or where it lies outside."""
    if width <= 0 or height <= 0:
        return "position unknown"
    if y < 0:
        return "above the visible area"
    if y > height:
        return "below the visible area"
    if x < 0 or x > width:
        return "outside the visible area"
    row = _VERTICAL[min(int(3 * y / height), 2)]
    column = _HORIZONTAL[min(int(3 * x / width), 2)]
    if row == "middle":
        return "center" if column == "center" else column
    return row if column == "center" else f"{row}-{column}"


def _round(value: float) -> int:
    return int(round(value / _ROUND_PX) * _ROUND_PX)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _centre(element: Any) -> tuple[float, float] | None:
    box = getattr(element, "bbox", None)
    if box is None:
        return None
    return box.x + box.width / 2, box.y + box.height / 2


def _bands(values: Sequence[float]) -> list[int]:
    """1-based band of each value, where values within :data:`_SAME_LINE_PX` of the previous band share it."""
    order = sorted(range(len(values)), key=values.__getitem__)
    bands = [0] * len(values)
    band, last = 0, None
    for index in order:
        if last is None or values[index] - last > _SAME_LINE_PX:
            band += 1
            last = values[index]
        bands[index] = band
    return bands


def _alike(elements: Sequence[Any]) -> dict[int, str]:
    """``#k of n alike`` (with row and column when they form a grid) for elements sharing a role and a name."""
    groups: dict[tuple[str, str], list[tuple[int, float, float]]] = {}
    for element in elements:
        name = (getattr(element, "name", "") or "").strip()
        centre = _centre(element)
        if name and centre is not None:
            groups.setdefault((str(getattr(element, "role", "")), name.lower()), []).append((int(element.md), centre[0], centre[1]))
    found: dict[int, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        rows = _bands([y for _, _, y in members])
        columns = _bands([x for _, x, _ in members])
        reading = sorted(range(len(members)), key=lambda i: (rows[i], members[i][1]))
        grid = max(rows) > 1 and max(columns) > 1
        for rank, index in enumerate(reading, start=1):
            text = f"#{rank} of {len(members)} alike"
            if grid:
                text += f" (row {rows[index]}, col {columns[index]})"
            found[members[index][0]] = text
    return found


def _heading_above(y: float, headings: Sequence[tuple[str, float]], name: str) -> str | None:
    """The last heading whose top is above ``y`` (headings in page order), unless it is the element's own name."""
    best: str | None = None
    for heading, top in headings:
        if top > y:
            break
        best = heading
    if best is None or best.strip().lower() == name.strip().lower():
        return None
    return best


def _has_context(element: Any) -> bool:
    """True for an element perception read its context for (a perception element; look-alikes have no such fields)."""
    return hasattr(element, "section") and hasattr(element, "scroll")


def describe_locations(snapshot: Any) -> dict[int, str]:
    """``md -> location text`` for every element of ``snapshot`` (a perception snapshot or any look-alike).

    Reads ``elements`` (``md``, ``role``, ``name``, ``bbox``, ``frame``), ``viewport``,
    ``headings`` and ``paths`` when present; missing parts are simply left out.
    """
    elements: Sequence[Any] = tuple(getattr(snapshot, "elements", ()) or ())
    viewport = getattr(snapshot, "viewport", None) or (0, 0)
    width, height = float(viewport[0]), float(viewport[1])
    headings = sorted(getattr(snapshot, "headings", ()) or (), key=lambda item: item[1])
    paths: Mapping[int, Any] = getattr(snapshot, "paths", None) or {}
    alike = _alike(elements)
    found: dict[int, str] = {}
    for element in elements:
        md = int(element.md)
        parts: list[str] = []
        centre = _centre(element)
        name = str(getattr(element, "name", "") or "")
        dialog = getattr(element, "dialog", None)
        section = str(getattr(element, "section", "") or "")
        scroll = str(getattr(element, "scroll", "") or "")
        # Perception read the element's dialog and section from its ancestry: use those, not what is drawn above it.
        own_context = dialog is not None or bool(section) or _has_context(element)
        if centre is not None and not scroll:
            x, y = centre
            parts.append(f"{region_of(x, y, width, height)} (x {_round(x)}, y {_round(y)})")
        if dialog is not None:
            parts.append(f'in dialog "{_clip(dialog, _HEADING_CLIP)}"' if dialog else "in a dialog")
        if own_context:
            if section and section.strip().lower() != name.strip().lower():
                parts.append(f'under "{_clip(section, _HEADING_CLIP)}"')
        elif centre is not None:
            heading = _heading_above(element.bbox.y, headings, name)
            if heading:
                parts.append(f'under "{_clip(heading, _HEADING_CLIP)}"')
        if scroll:
            area = "in the dialog's scroll area" if getattr(element, "scroll_in_dialog", False) else "in a scroll area"
            parts.append(f"{area}, {scroll} the visible part")
        if md in alike:
            parts.append(alike[md])
        frame = getattr(element, "frame", None)
        if frame:
            parts.append(f'in frame "{_clip(str(frame), _FRAME_CLIP)}"')
        path = paths.get(md)
        if path is not None and len(getattr(path, "segments", ())) > 1:
            parts.append("in shadow DOM")
        if parts:
            found[md] = ", ".join(parts)
    return found
