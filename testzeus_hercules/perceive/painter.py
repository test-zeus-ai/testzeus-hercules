"""Paint numbered marks onto a plain screenshot, off-page, under a legibility budget.

Rules:

* every painted element gets a 2 px outline and a numbered badge;
* badges never overlap each other and never leave the image;
* badges avoid covering other marked boxes; a badge goes inside its own box only for must-paint marks
  or, as a last resort, when the box is much larger than the badge;
* outlines plus badges cover at most ``COVERAGE_LIMIT`` of the image (must-paint marks are exempt);
* badge size steps down as the number of marks grows;
* fields (tier 0) and focus marks are always painted; anything dropped is reported with a reason.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from PIL import Image, ImageDraw, ImageFont

from testzeus_hercules.perceive.model import BBox

COVERAGE_LIMIT = 0.40
OUTLINE_WIDTH = 2
MAX_WIDTH = 1024
JPEG_QUALITY = 75

# Fixed, high-contrast palette; a mark's colour depends only on its md.
PALETTE: tuple[str, ...] = (
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#008080",
    "#f032e6",
    "#9a6324",
    "#800000",
    "#000075",
    "#808000",
    "#d6336c",
    "#1971c2",
    "#2b8a3e",
    "#e8590c",
    "#5f3dc4",
)

# (max marks, font px) — badges shrink as the screen gets busier.
_FONT_STEPS: tuple[tuple[int, int], ...] = ((30, 14), (80, 12), (10**9, 10))
_CELL = 48  # spatial index cell size, output px
_MAX_INTRUSION = 0.3  # share of a badge that may overlap other painted boxes in the relaxed pass

Rect = tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive), output px


@dataclass(frozen=True)
class Mark:
    """An element to paint. ``bbox`` is in CSS px of the viewport; ``must`` marks are never dropped."""

    md: int
    bbox: BBox
    tier: int
    must: bool = False


@dataclass(frozen=True)
class PaintResult:
    jpeg: bytes
    size: tuple[int, int]
    painted: tuple[int, ...]
    badges: Mapping[int, Rect]
    dropped: Mapping[int, str]
    coverage: float
    font_size: int


def colour_for(md: int) -> str:
    return PALETTE[md % len(PALETTE)]


def downscale(image: Image.Image, max_width: int = MAX_WIDTH) -> Image.Image:
    if image.width <= max_width:
        return image
    height = max(1, round(image.height * max_width / image.width))
    return image.resize((max_width, height), Image.Resampling.LANCZOS)


def encode_jpeg(image: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def paint_marks(
    plain: bytes | Image.Image,
    marks: Sequence[Mark],
    *,
    viewport: tuple[int, int],
    max_width: int = MAX_WIDTH,
    quality: int = JPEG_QUALITY,
) -> PaintResult:
    """Downscale ``plain`` to at most ``max_width`` and paint ``marks`` on it."""
    source = plain if isinstance(plain, Image.Image) else Image.open(io.BytesIO(plain))
    image = downscale(source.convert("RGB"), max_width)
    scale = image.width / max(viewport[0], 1)
    layout = _layout(marks, image.size, scale)
    canvas = ImageDraw.Draw(image)
    font = _font(layout.font_size)
    for md in layout.painted:
        box = layout.boxes[md]
        colour = colour_for(md)
        canvas.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=colour, width=OUTLINE_WIDTH)
    for md in layout.painted:
        badge = layout.badges[md]
        canvas.rectangle((badge[0], badge[1], badge[2] - 1, badge[3] - 1), fill=colour_for(md))
        canvas.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2), str(md), fill="white", font=font, anchor="mm", stroke_width=1, stroke_fill="#111111")
    return PaintResult(
        jpeg=encode_jpeg(image, quality),
        size=image.size,
        painted=tuple(layout.painted),
        badges=MappingProxyType(dict(layout.badges)),
        dropped=MappingProxyType(dict(layout.dropped)),
        coverage=layout.coverage,
        font_size=layout.font_size,
    )


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # pragma: no cover - Pillow without FreeType
        return ImageFont.load_default()


@lru_cache(maxsize=64)
def _badge_size(text: str, font_size: int) -> tuple[int, int]:
    left, top, right, bottom = _font(font_size).getbbox(text)
    return int(right - left) + 6, int(font_size + 4)


@dataclass
class _Layout:
    font_size: int
    painted: list[int]
    boxes: dict[int, Rect]
    badges: dict[int, Rect]
    dropped: dict[int, str]
    coverage: float


class _Index:
    """Uniform-grid spatial index of rectangles keyed by md."""

    def __init__(self) -> None:
        self._cells: dict[tuple[int, int], list[tuple[int, Rect]]] = {}

    @staticmethod
    def _keys(r: Rect) -> Iterable[tuple[int, int]]:
        for cx in range(r[0] // _CELL, (r[2] - 1) // _CELL + 1):
            for cy in range(r[1] // _CELL, (r[3] - 1) // _CELL + 1):
                yield cx, cy

    def add(self, md: int, r: Rect) -> None:
        for key in self._keys(r):
            self._cells.setdefault(key, []).append((md, r))

    def hits(self, r: Rect, ignore: int | None = None) -> set[int]:
        found: set[int] = set()
        for key in self._keys(r):
            for md, other in self._cells.get(key, ()):
                if md != ignore and _overlaps(r, other):
                    found.add(md)
        return found


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _intersection(a: Rect, b: Rect) -> Rect:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _area(r: Rect) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def _to_output(bbox: BBox, scale: float, size: tuple[int, int]) -> Rect | None:
    x0 = max(0, int(bbox.x * scale))
    y0 = max(0, int(bbox.y * scale))
    x1 = min(size[0], int(round((bbox.x + bbox.width) * scale)))
    y1 = min(size[1], int(round((bbox.y + bbox.height) * scale)))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return (x0, y0, x1, y1)


def _outline_cost(r: Rect) -> int:
    w, h = r[2] - r[0], r[3] - r[1]
    return min(w * h, 2 * OUTLINE_WIDTH * (w + h))


def _slots(box: Rect, size: tuple[int, int], badge: tuple[int, int], *, inside: bool) -> list[Rect]:
    """Candidate badge rectangles around ``box``, nearest-first; optionally the inside slot last."""
    bw, bh = badge
    x0, y0, x1, y1 = box
    cy = (y0 + y1) // 2 - bh // 2
    outside = [
        (x0, y0 - bh),  # above, left-aligned
        (x1 + 1, cy),  # right, centred
        (x0 - bw - 1, cy),  # left, centred
        (x0, y1 + 1),  # below, left-aligned
        (x1 - bw, y0 - bh),  # above, right-aligned
        (x1 - bw, y1 + 1),  # below, right-aligned
    ]
    if x1 - x0 <= 24 and y1 - y0 <= 24:  # checkboxes and radios read best with the number beside them
        outside = [outside[2], outside[1], outside[0], *outside[3:]]
    if inside:
        outside.append((x0, y0))
    rects = [(x, y, x + bw, y + bh) for x, y in outside]
    return [r for r in rects if r[0] >= 0 and r[1] >= 0 and r[2] <= size[0] and r[3] <= size[1]]


def _spiral(box: Rect, size: tuple[int, int], badge: tuple[int, int]) -> Iterable[Rect]:
    """Further-out badge positions for must-paint marks with no free nearby slot."""
    bw, bh = badge
    for ring in range(1, 8):
        dx, dy = ring * bw, ring * bh
        for x, y in ((box[0] - dx, box[1]), (box[2] + dx - bw, box[1]), (box[0], box[1] - dy), (box[0], box[3] + dy - bh), (box[0] - dx, box[1] - dy), (box[2] + dx - bw, box[3] + dy - bh)):
            r = (x, y, x + bw, y + bh)
            if r[0] >= 0 and r[1] >= 0 and r[2] <= size[0] and r[3] <= size[1]:
                yield r


def _font_for(count: int) -> int:
    for limit, size in _FONT_STEPS:
        if count <= limit:
            return size
    return _FONT_STEPS[-1][1]  # pragma: no cover


def _layout(marks: Sequence[Mark], size: tuple[int, int], scale: float) -> _Layout:
    boxes: dict[int, Rect] = {}
    dropped: dict[int, str] = {}
    for mark in marks:
        box = _to_output(mark.bbox, scale, size)
        if box is None:
            dropped[mark.md] = "offscreen"
        else:
            boxes[mark.md] = box
    ordered = sorted((m for m in marks if m.md in boxes), key=lambda m: (not (m.must or m.tier == 0), m.tier, boxes[m.md][1], boxes[m.md][0], m.md))
    font_size = _font_for(len(ordered))
    layout = _admit(ordered, boxes, size, font_size)
    smaller = [s for _, s in _FONT_STEPS if s < font_size]
    # A crowded screen that dropped many marks reads better with the next smaller badge size.
    if smaller and len(layout.painted) < 0.75 * len(ordered):
        retry = _admit(ordered, boxes, size, smaller[0])
        if len(retry.painted) > len(layout.painted):
            layout = retry
    layout.dropped.update(dropped)
    return layout


def _contains(outer: Rect, inner: Rect) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


class _Placer:
    """Greedy badge placement for one font size."""

    def __init__(self, ordered: Sequence[Mark], boxes: Mapping[int, Rect], size: tuple[int, int], font_size: int) -> None:
        self.ordered = ordered
        self.boxes = boxes
        self.size = size
        self.font_size = font_size
        self.screen = size[0] * size[1]
        self.budget = COVERAGE_LIMIT * self.screen
        self.box_index = _Index()
        for mark in ordered:
            self.box_index.add(mark.md, boxes[mark.md])
        self.badge_index = _Index()
        self.badges: dict[int, Rect] = {}
        self.dropped: dict[int, str] = {}
        self.pending = {m.md for m in ordered}
        self.used = 0.0

    def _free(self, badge: Rect, md: int, strict: bool) -> bool:
        """Strict: the badge touches no other marked box. Relaxed: it may graze painted boxes a little."""
        if self.badge_index.hits(badge):
            return False
        own = self.boxes[md]
        intrusion = 0
        for other in self.box_index.hits(badge, ignore=md):
            live = other in self.badges or (strict and other in self.pending)
            if live and not _contains(self.boxes[other], own):  # a mark's own containers never block it
                if strict:
                    return False
                intrusion += _area(_intersection(badge, self.boxes[other]))
        return intrusion <= _MAX_INTRUSION * _area(badge)

    def _hidden_by_badge(self, md: int) -> bool:
        """True when another mark's badge sits mostly on this box, so painting it would mislead."""
        box = self.boxes[md]
        return any(_area(_intersection(self.badges[other], box)) * 2 >= _area(self.badges[other]) for other in self.badge_index.hits(box))

    def _try(self, mark: Mark, strict: bool) -> bool:
        """Place ``mark``'s badge; False means "try again later" (strict) or dropped (relaxed)."""
        must = mark.must or mark.tier == 0
        box = self.boxes[mark.md]
        badge_size = _badge_size(str(mark.md), self.font_size)
        cost = _outline_cost(box) + badge_size[0] * badge_size[1]
        if not must and self.used + cost > self.budget:
            self.dropped[mark.md] = "coverage"
            self.pending.discard(mark.md)
            return True
        if not strict:
            self.pending.discard(mark.md)
            if not must and self._hidden_by_badge(mark.md):
                self.dropped[mark.md] = "crowded"
                return True
        roomy = box[2] - box[0] >= 1.5 * badge_size[0] and box[3] - box[1] >= 1.5 * badge_size[1]
        slots = _slots(box, self.size, badge_size, inside=must or (roomy and not strict))
        chosen = next((s for s in slots if self._free(s, mark.md, strict)), None)
        if chosen is None and must and not strict:
            further = list(_spiral(box, self.size, badge_size))
            chosen = next((s for s in further if self._free(s, mark.md, strict)), None)
            if chosen is None:  # last resort: any spot that keeps badges apart
                chosen = next((s for s in [*slots, *further] if not self.badge_index.hits(s)), None)
        if chosen is None:
            if not strict:
                self.dropped[mark.md] = "crowded"
            return not strict
        self.badges[mark.md] = chosen
        self.badge_index.add(mark.md, chosen)
        self.used += cost
        self.pending.discard(mark.md)
        return True

    def run(self) -> _Layout:
        # Pass 1 (strict): a badge may not cover any marked box, painted or still waiting.
        deferred = [m for m in self.ordered if not self._try(m, strict=True)]
        # Pass 2 (relaxed): only painted boxes block; must-paint marks may search further out.
        for mark in deferred:
            self._try(mark, strict=False)
        painted = [m.md for m in self.ordered if m.md in self.badges]
        coverage = self.used / self.screen if self.screen else 0.0
        return _Layout(font_size=self.font_size, painted=painted, boxes=dict(self.boxes), badges=self.badges, dropped=self.dropped, coverage=coverage)


def _admit(ordered: Sequence[Mark], boxes: Mapping[int, Rect], size: tuple[int, int], font_size: int) -> _Layout:
    return _Placer(ordered, boxes, size, font_size).run()
