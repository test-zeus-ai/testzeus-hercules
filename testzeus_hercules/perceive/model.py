"""Immutable snapshot model shared by perception, decision and action code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from testzeus_hercules.perceive.painter import PaintResult

_EMPTY: Mapping[int, "ElementPath"] = MappingProxyType({})
_VALUE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"})
_PLAIN_TYPES = frozenset({"text", "submit", "button", "checkbox", "radio", "reset", "search"})
# A form field's facts the walker reads (its error and help text, its format attributes), shown with its row.
FIELD_FACT_ATTRS = ("error", "hint", "placeholder", "pattern", "maxlength", "minlength", "min", "max", "inputmode")
_STATE_ATTRS = (("aria-expanded", "expanded"), ("aria-selected", "selected"), ("aria-pressed", "pressed"), ("aria-current", "current"))


@dataclass(frozen=True)
class BBox:
    """Rectangle in CSS px of the top-level viewport."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True)
class Element:
    """One marked element. ``md`` is unique within its snapshot."""

    md: int
    role: str
    name: str
    tag: str
    attrs: Mapping[str, str]
    bbox: BBox | None
    frame: str | None
    in_viewport: bool
    tier: int
    value: str | None
    checked: bool | None
    disabled: bool
    # Context from the element's own ancestry (never its position on screen): the accessible name of the dialog it is in
    # ("" for an unnamed one, None outside dialogs), the heading that names its section, and where it lies in its scroll
    # area ("above" or "below" the part shown, "" when shown) and whether that scroll area is inside a dialog.
    dialog: str | None = None
    section: str = ""
    scroll: str = ""
    scroll_in_dialog: bool = False
    # Why the element is not offered to a decider: "behind_modal" (outside the open modal dialog), "inert" or
    # "aria-hidden" (in a subtree hidden from users and assistive technology); "" when it is offered.
    blocked: str = ""


@dataclass(frozen=True)
class ElementPath:
    """How to find an element again: iframe element paths from the top frame down, then the element's path.

    Each path is a tuple of CSS selectors, one per tree: the first is relative to the frame's document,
    each following one is relative to the shadow host matched by the previous selector.
    """

    frames: tuple[tuple[str, ...], ...]
    segments: tuple[str, ...]
    # The path of the dialog the element is in (same form as ``segments``, in the element's frame), when it is in one.
    dialog: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Snapshot:
    """What the page looked like at one moment: marked elements and two screenshots."""

    seq: int
    url: str
    title: str
    viewport: tuple[int, int]
    elements: tuple[Element, ...]
    plain_jpeg: bytes
    som_jpeg: bytes
    above: tuple[str, ...]
    below: tuple[str, ...]
    focus_terms: tuple[str, ...] = ()
    paths: Mapping[int, ElementPath] = field(default_factory=lambda: _EMPTY, repr=False, compare=False)
    # Headings and labelled landmarks as (name, top in CSS px of the top-level viewport), in page order.
    headings: tuple[tuple[str, float], ...] = field(default=(), repr=False, compare=False)
    # What the page announces: alert and live-region text, and the error text invalid fields point to (ARIA), in page order.
    messages: tuple[str, ...] = field(default=(), repr=False, compare=False)
    paint: "PaintResult | None" = field(default=None, repr=False, compare=False)
    # What could not be read ("12 elements skipped (first error: ...)", "frames not read: ..."), shown in the observation header.
    notes: tuple[str, ...] = field(default=(), repr=False, compare=False)
    # The walker's error records: stage, tag, message and count (at most a few distinct ones).
    read_errors: tuple[Mapping[str, object], ...] = field(default=(), repr=False, compare=False)
    # "main": read in the page's own world; "isolated": in an isolated world, after the page's scripts broke the walk.
    world: str = field(default="main", repr=False, compare=False)
    # The open modal dialog on top ("" when it has no name), None when no modal dialog is open.
    modal: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_md", {e.md: e for e in self.elements})
        kept = tuple(e for e in self.elements if not e.blocked)
        # A modal whose content cannot be read (no element in it, e.g. a stale aria-modal): offer every element rather than none.
        object.__setattr__(self, "_offer_all", bool(self.elements) and not kept)
        object.__setattr__(self, "_offered", self.elements if not kept else kept)

    @property
    def offered(self) -> tuple[Element, ...]:
        """The elements a decider is offered: not behind an open modal dialog, not inert or aria-hidden (all of them when
        none would be left)."""
        return self._offered  # type: ignore[attr-defined,no-any-return]

    @property
    def not_offered(self) -> int:
        """How many elements are left out of :attr:`offered`."""
        return len(self.elements) - len(self.offered)

    def element(self, md: int) -> Element:
        """The element with this md; ``KeyError`` if the snapshot has none."""
        return self._by_md[md]  # type: ignore[attr-defined,no-any-return]

    def observation_text(self, *, max_elements: int = 120, max_tokens: int = 2500, page: int = 1) -> str:
        """Header, off-screen sections and one row per on-screen element, within the given budget.

        Rows are ordered by tier, then reading order. Elements whose name contains a focus term are
        always listed on page 1. Later pages continue where the previous one stopped.
        """
        header = self._header_lines()
        visible = sorted((e for e in self.offered if e.in_viewport), key=_reading_key)
        pinned = [e for e in visible if _matches(e.name, self.focus_terms)]
        rest = [e for e in visible if e not in pinned]
        budget_chars = max_tokens * 4 - sum(len(line) + 1 for line in header)
        start = 0
        rows: list[Element] = []
        for current in range(1, max(page, 1) + 1):
            chunk_pinned = pinned if current == 1 else []
            taken = _fill(chunk_pinned, rest[start:], max_elements, budget_chars)
            if current == max(page, 1):
                rows = chunk_pinned + rest[start : start + taken]
            start += taken
        shown = sorted(rows, key=_reading_key)
        lines = header + [row_text(e) for e in shown]
        remaining = len(rest) - start
        if remaining > 0:
            lines.append(f"# … {remaining} more elements (scroll or page={max(page, 1) + 1})")
        return "\n".join(lines)

    def _header_lines(self) -> list[str]:
        path = urlsplit(self.url).path or "/"
        width, height = self.viewport
        lines = [f"# page: {self.title} · {path} · viewport {width}x{height} · snapshot {self.seq}"]
        if self.above:
            lines.append("# above: " + ", ".join(self.above))
        if self.below:
            lines.append("# below: " + ", ".join(self.below))
        lines.extend(f"# read: {note}" for note in self.notes)
        lines.extend(self._offer_lines())
        return lines

    def _offer_lines(self) -> list[str]:
        """What the header says about elements that are not offered (behind a modal dialog, inert, aria-hidden)."""
        behind = sum(1 for e in self.elements if e.blocked == "behind_modal")
        hidden = sum(1 for e in self.elements if e.blocked in ("inert", "aria-hidden"))
        name = _quote(self.modal) if self.modal else "a dialog"
        if self._offer_all:  # type: ignore[attr-defined]
            if self.modal is not None:
                return [f"# modal: {name} open, but no element in it could be read; every element is offered"]
            return ["# hidden: every element is inert or aria-hidden; every element is offered"]
        lines = []
        if self.modal is not None:
            lines.append(f"# modal: {name} open; {behind} element{'s' if behind != 1 else ''} behind it not offered")
        if hidden:
            lines.append(f"# hidden: {hidden} element{'s' if hidden != 1 else ''} inert or aria-hidden, not offered")
        return lines


def _reading_key(e: Element) -> tuple[int, float, float, int]:
    if e.bbox is None:
        return (e.tier, float("inf"), float("inf"), e.md)
    return (e.tier, round(e.bbox.y), round(e.bbox.x), e.md)


def _matches(name: str, terms: Sequence[str]) -> bool:
    lowered = name.lower()
    return any(t and t.lower() in lowered for t in terms)


def _fill(pinned: list[Element], candidates: list[Element], max_elements: int, budget_chars: int) -> int:
    """How many of ``candidates`` fit after the pinned rows; a page without pinned rows always takes one."""
    used_rows = len(pinned)
    used_chars = sum(len(row_text(e)) + 1 for e in pinned)
    taken = 0
    for e in candidates:
        cost = len(row_text(e)) + 1
        guaranteed = used_rows == 0
        if not guaranteed and (used_rows >= max_elements or used_chars + cost > budget_chars):
            break
        used_rows += 1
        used_chars += cost
        taken += 1
    return taken


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def row_text(e: Element) -> str:
    """``[md] role "name" | key=val …`` for one element."""
    parts: list[str] = []
    if e.value is not None and (e.role in _VALUE_ROLES or e.checked is None):
        parts.append(f"val={_quote(e.value)}")
    if e.checked is not None:
        parts.append(f"checked={'true' if e.checked else 'false'}")
    input_type = e.attrs.get("type")
    if input_type and input_type not in _PLAIN_TYPES:
        parts.append(f"type={input_type}")
    if e.attrs.get("aria-required") == "true":
        parts.append("req")
    if e.attrs.get("aria-invalid") == "true" or e.attrs.get("invalid") == "true":
        parts.append("invalid")
    for attr in FIELD_FACT_ATTRS:
        if e.attrs.get(attr):
            parts.append(f"{attr}={_quote(e.attrs[attr])}")
    for attr, key in _STATE_ATTRS:
        if attr in e.attrs and not (attr == "aria-selected" and e.role == "option" and e.checked is not None):
            parts.append(f"{key}={e.attrs[attr]}")
    if e.attrs.get("label"):
        parts.append(f"label={_quote(e.attrs['label'])}")
    if e.attrs.get("near"):
        parts.append(f"near={_quote(e.attrs['near'])}")
    if e.disabled:
        parts.append("disabled")
    if "href" in e.attrs:
        parts.append(f"href={e.attrs['href']}")
    if e.frame:
        parts.append(f"frame={_quote(e.frame)}")
    head = f"[{e.md}] {e.role} {_quote(e.name)}"
    return head + (" | " + " ".join(parts) if parts else "")
