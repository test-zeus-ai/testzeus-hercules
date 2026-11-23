"""Annotated screenshots: a numbered box on every finding's element, numbered in the findings' order."""

from __future__ import annotations

import io
from collections.abc import Sequence

from PIL import Image, ImageDraw

from testzeus_hercules.quality.findings import Finding

__all__ = ["IMPACT_COLOURS", "annotate"]

IMPACT_COLOURS: dict[str, tuple[int, int, int]] = {"critical": (200, 0, 40), "serious": (220, 90, 0), "moderate": (180, 140, 0), "minor": (60, 110, 200)}
_LINE_PX = 3
_LABEL_PAD = 3


def _bbox(finding: Finding) -> list[float] | None:
    box = (finding.measured or {}).get("bbox")
    if isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(value, (int, float)) for value in box) and box[2] > 0 and box[3] > 0:
        return [float(value) for value in box]
    return None


def annotate(png: bytes, findings: Sequence[Finding]) -> tuple[bytes, int]:
    """The screenshot with box ``n`` drawn around the element of ``findings[n - 1]`` (boxes in CSS px of the
    viewport); returns the PNG and how many boxes were drawn. Findings without a box are skipped, keeping numbers."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    drawn = 0
    for number, finding in enumerate(findings, start=1):
        box = _bbox(finding)
        if box is None:
            continue
        x, y, width, height = box
        if x >= image.width or y >= image.height or x + width <= 0 or y + height <= 0:
            continue
        colour = IMPACT_COLOURS.get(finding.impact, IMPACT_COLOURS["moderate"])
        draw.rectangle([x, y, x + width, y + height], outline=colour, width=_LINE_PX)
        label = str(number)
        left, top, right, bottom = draw.textbbox((0, 0), label)
        label_x = max(0.0, x)
        label_y = y - (bottom - top) - 2 * _LABEL_PAD if y - (bottom - top) - 2 * _LABEL_PAD >= 0 else y
        draw.rectangle([label_x, label_y, label_x + (right - left) + 2 * _LABEL_PAD, label_y + (bottom - top) + 2 * _LABEL_PAD], fill=colour)
        draw.text((label_x + _LABEL_PAD - left, label_y + _LABEL_PAD - top), label, fill=(255, 255, 255))
        drawn += 1
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue(), drawn
