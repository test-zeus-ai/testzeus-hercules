"""Perceptual image diff with Pillow only: masking, downscaling, blur, per-pixel tolerance and regions.

The images are downscaled and blurred before comparing so antialiasing and sub-pixel text rendering do
not count as change; a pixel counts as changed when any channel differs by more than ``tolerance``.
Changed pixels are grouped on a coarse grid into rectangular regions (in CSS px of the screenshot).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

__all__ = ["DEFAULT_SCALE", "DEFAULT_TOLERANCE", "ImageDiff", "diff_images", "png_bytes", "shrink"]

DEFAULT_SCALE = 0.5
DEFAULT_TOLERANCE = 24
BLUR_RADIUS = 1.0
CELL_PX = 16  # region grid cell, in CSS px of the screenshot
_MASK_FILL = (0, 0, 0)
_CHANGED = (230, 30, 60)
_REGION = (200, 0, 160)
_MASKED = (150, 150, 150)

Rect = Sequence[float]


@dataclass(frozen=True)
class ImageDiff:
    """Result of comparing two screenshots."""

    ratio: float
    changed_pixels: int
    compared_pixels: int
    regions: list[list[int]]
    size_before: tuple[int, int]
    size_after: tuple[int, int]
    diff_png: bytes

    @property
    def size_changed(self) -> bool:
        return self.size_before != self.size_after


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def shrink(png: bytes, max_width: int = 1024) -> bytes:
    """A PNG no wider than ``max_width`` (for sending to a model)."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    if image.width <= max_width:
        return png
    height = max(1, round(image.height * max_width / image.width))
    return png_bytes(image.resize((max_width, height), Image.Resampling.LANCZOS))


def _pad(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if image.size == size:
        return image
    canvas = Image.new("RGB", size, (255, 255, 255))
    canvas.paste(image, (0, 0))
    return canvas


def _masked(image: Image.Image, masks: Sequence[Rect]) -> Image.Image:
    if not masks:
        return image
    out = image.copy()
    draw = ImageDraw.Draw(out)
    for x, y, w, h in masks:
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=_MASK_FILL)
    return out


def _prepared(image: Image.Image, scale: float) -> Image.Image:
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    small = image.resize(size, Image.Resampling.BOX) if scale != 1 else image
    return small.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))


def _regions(changed: Image.Image, scale: float) -> list[list[int]]:
    """Connected groups of grid cells containing a change, as [x, y, width, height] in full-size px."""
    cell = max(1, round(CELL_PX * scale))
    cols, rows = -(-changed.width // cell), -(-changed.height // cell)
    grid = changed.resize((cols, rows), Image.Resampling.BOX, reducing_gap=None) if (cols, rows) != changed.size else changed
    hot = {(i % cols, i // cols) for i, value in enumerate(grid.tobytes()) if value > 0}
    regions = []
    while hot:
        stack = [hot.pop()]
        xs, ys = [], []
        while stack:
            cx, cy = stack.pop()
            xs.append(cx)
            ys.append(cy)
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if (nx, ny) in hot:
                    hot.remove((nx, ny))
                    stack.append((nx, ny))
        unit = cell / scale
        regions.append([round(min(xs) * unit), round(min(ys) * unit), round((max(xs) - min(xs) + 1) * unit), round((max(ys) - min(ys) + 1) * unit)])
    return sorted(regions, key=lambda r: (r[1], r[0]))


def diff_images(before: Image.Image, after: Image.Image, *, masks: Sequence[Rect] = (), tolerance: int = DEFAULT_TOLERANCE, scale: float = DEFAULT_SCALE) -> ImageDiff:
    """Compare two screenshots; masked rectangles (full-size px) are excluded from both."""
    before_rgb, after_rgb = before.convert("RGB"), after.convert("RGB")
    size = (max(before_rgb.width, after_rgb.width), max(before_rgb.height, after_rgb.height))
    a = _prepared(_masked(_pad(before_rgb, size), masks), scale)
    b = _prepared(_masked(_pad(after_rgb, size), masks), scale)
    r, g, bl = ImageChops.difference(a, b).split()
    strongest = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    changed = strongest.point(lambda v: 255 if v > tolerance else 0)
    changed_pixels = changed.histogram()[255]
    mask_layer = Image.new("L", size, 0)
    mask_draw = ImageDraw.Draw(mask_layer)
    for x, y, w, h in masks:
        mask_draw.rectangle([x, y, x + w - 1, y + h - 1], fill=255)
    masked_small = mask_layer.resize(changed.size, Image.Resampling.NEAREST).histogram()[255]
    compared = max(1, changed.width * changed.height - masked_small)
    regions = _regions(changed, scale)
    return ImageDiff(
        ratio=round(changed_pixels / compared, 6),
        changed_pixels=changed_pixels,
        compared_pixels=compared,
        regions=regions,
        size_before=before_rgb.size,
        size_after=after_rgb.size,
        diff_png=png_bytes(_evidence(_pad(after_rgb, size), changed, mask_layer, regions)),
    )


def _evidence(after: Image.Image, changed: Image.Image, masks: Image.Image, regions: list[list[int]]) -> Image.Image:
    """The new screenshot faded, changed pixels in red, masked areas grey, regions outlined."""
    faded = Image.blend(after, Image.new("RGB", after.size, (255, 255, 255)), 0.65)
    faded = Image.composite(Image.new("RGB", after.size, _MASKED), faded, masks)
    full = changed.resize(after.size, Image.Resampling.NEAREST)
    out = Image.composite(Image.new("RGB", after.size, _CHANGED), faded, full)
    draw = ImageDraw.Draw(out)
    for x, y, w, h in regions:
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=_REGION, width=2)
    return out
