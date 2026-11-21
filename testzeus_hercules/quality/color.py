"""Colour maths for contrast checks and fixes: WCAG 2.x contrast, alpha compositing and OKLCH.

Every value a contrast finding or fix reports comes from these functions, so the same numbers are
produced whether a colour is measured on the page or proposed as a fix.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "RGBA",
    "ContrastFix",
    "composite",
    "contrast_ratio",
    "delta_e_ok",
    "floor2",
    "from_oklch",
    "is_large_text",
    "nearest_passing",
    "relative_luminance",
    "required_ratio",
    "to_hex",
    "to_oklch",
]

LARGE_TEXT_PX = 24.0  # 18pt
LARGE_BOLD_TEXT_PX = 18.66  # 14pt; 18.666… px, with a little slack for rounding in computed styles
BOLD_WEIGHT = 700
_SEARCH_STEPS = 40
_ACHROMATIC = 1e-4  # greys: no hue to keep


@dataclass(frozen=True)
class RGBA:
    """An sRGB colour with 0-255 channels and alpha in 0..1."""

    r: float
    g: float
    b: float
    a: float = 1.0

    @classmethod
    def parse(cls, value: Sequence[float] | str) -> RGBA:
        """From ``[r, g, b, a]`` (as the page probe returns) or ``#rrggbb`` / ``#rgb``."""
        if isinstance(value, str):
            text = value.strip().lstrip("#")
            if len(text) == 3:
                text = "".join(ch * 2 for ch in text)
            if len(text) not in (6, 8):
                raise ValueError(f"not a hex colour: {value!r}")
            channels = [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]
            alpha = channels[3] / 255 if len(channels) == 4 else 1.0
            return cls(channels[0], channels[1], channels[2], alpha)
        parts = list(value)
        if len(parts) not in (3, 4):
            raise ValueError(f"colour needs 3 or 4 channels: {value!r}")
        return cls(float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]) if len(parts) == 4 else 1.0)

    @property
    def opaque(self) -> bool:
        return self.a >= 0.999

    @property
    def hex(self) -> str:
        return to_hex(self)


WHITE = RGBA(255, 255, 255)


def to_hex(colour: RGBA) -> str:
    """``#rrggbb`` of the colour's (rounded) channels; alpha is dropped."""
    return "#" + "".join(f"{_clamp_channel(c):02x}" for c in (colour.r, colour.g, colour.b))


def _clamp_channel(value: float) -> int:
    return max(0, min(255, int(round(value))))


def composite(top: RGBA, bottom: RGBA) -> RGBA:
    """``top`` painted over ``bottom`` (source-over)."""
    alpha = top.a + bottom.a * (1 - top.a)
    if alpha <= 0:
        return RGBA(0, 0, 0, 0)

    def mix(t: float, b: float) -> float:
        return (t * top.a + b * bottom.a * (1 - top.a)) / alpha

    return RGBA(mix(top.r, bottom.r), mix(top.g, bottom.g), mix(top.b, bottom.b), alpha)


def flatten(layers: Sequence[RGBA], base: RGBA = WHITE) -> RGBA:
    """Background layers listed top first, painted over an opaque ``base`` (the canvas)."""
    result = base
    for layer in reversed(list(layers)):
        result = composite(layer, result)
    return RGBA(result.r, result.g, result.b, 1.0)


def _linear(channel: float) -> float:
    c = _clamp_channel(channel) / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: RGBA) -> float:
    """WCAG 2.x relative luminance of the rounded sRGB channels."""
    return 0.2126 * _linear(colour.r) + 0.7152 * _linear(colour.g) + 0.0722 * _linear(colour.b)


def contrast_ratio(fg: RGBA, bg: RGBA) -> float:
    """WCAG 2.x contrast ratio, 1..21; a translucent ``fg`` is composited over ``bg`` first."""
    if not fg.opaque:
        fg = composite(fg, bg)
    lighter, darker = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def floor2(value: float) -> float:
    """Truncate to two decimals, the precision ratios are reported and compared at."""
    return math.floor(value * 100 + 1e-9) / 100


def is_large_text(font_size_px: float, font_weight: float) -> bool:
    """WCAG large text: at least 18pt, or 14pt and bold."""
    return font_size_px >= LARGE_TEXT_PX or (font_size_px >= LARGE_BOLD_TEXT_PX and font_weight >= BOLD_WEIGHT)


def required_ratio(large: bool, level: str = "AA") -> float:
    """Minimum ratio for SC 1.4.3 (AA) or 1.4.6 (AAA)."""
    if level == "AAA":
        return 4.5 if large else 7.0
    return 3.0 if large else 4.5


# --------------------------------------------------------------------------- OKLab / OKLCH


def _to_oklab(colour: RGBA) -> tuple[float, float, float]:
    r, g, b = (_linear_unclamped(c / 255) for c in (colour.r, colour.g, colour.b))
    lms = (
        0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
        0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
        0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
    )
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in lms)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _linear_unclamped(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _from_oklab(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    """Linear-light sRGB (may fall outside 0..1)."""
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_**3, m_**3, s_**3
    return (
        4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
    )


def to_oklch(colour: RGBA) -> tuple[float, float, float]:
    """(lightness 0..1, chroma, hue in degrees)."""
    lightness, a, b = _to_oklab(colour)
    chroma = math.hypot(a, b)
    if chroma < _ACHROMATIC:
        return lightness, 0.0, 0.0
    return lightness, chroma, math.degrees(math.atan2(b, a)) % 360


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-6 <= c <= 1 + 1e-6 for c in rgb)


def from_oklch(lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> RGBA:
    """The sRGB colour for an OKLCH value; chroma is reduced (hue and lightness kept) until it fits sRGB."""
    lightness = max(0.0, min(1.0, lightness))
    radians = math.radians(hue)

    def linear_at(c: float) -> tuple[float, float, float]:
        return _from_oklab(lightness, c * math.cos(radians), c * math.sin(radians))

    rgb = linear_at(chroma)
    if not _in_gamut(rgb):
        low, high = 0.0, chroma
        for _ in range(_SEARCH_STEPS):
            mid = (low + high) / 2
            if _in_gamut(linear_at(mid)):
                low = mid
            else:
                high = mid
        rgb = linear_at(low)
    r, g, b = (max(0.0, min(1.0, _gamma(max(0.0, min(1.0, c))))) * 255 for c in rgb)
    return RGBA(round(r), round(g), round(b), alpha)


def delta_e_ok(first: RGBA, second: RGBA) -> float:
    """Perceptual distance (ΔE OK, Euclidean in OKLab, ×100)."""
    a = _to_oklab(first)
    b = _to_oklab(second)
    return 100 * math.dist(a, b)


# --------------------------------------------------------------------------- nearest passing colour


@dataclass(frozen=True)
class ContrastFix:
    """A replacement for one side of a colour pair that meets the required ratio."""

    side: str  # "color" (the text) or "background-color"
    current: str
    suggested: str
    ratio: float
    delta_e: float


def _passes(fg: RGBA, bg: RGBA, required: float) -> bool:
    return floor2(contrast_ratio(fg, bg)) >= required


def _search(colour: RGBA, other: RGBA, required: float, *, moving_fg: bool, darker: bool) -> RGBA | None:
    """Closest lightness (hue kept) in one direction at which the pair passes, or None."""
    lightness, chroma, hue = to_oklch(colour)

    def candidate(value: float) -> RGBA:
        return from_oklch(value, chroma, hue, colour.a)

    def ok(value: float) -> bool:
        c = candidate(value)
        return _passes(c, other, required) if moving_fg else _passes(other, c, required)

    end = 0.0 if darker else 1.0
    if not ok(end):
        return None
    near, far = lightness, end
    for _ in range(_SEARCH_STEPS):
        mid = (near + far) / 2
        if ok(mid):
            far = mid
        else:
            near = mid
    return candidate(far)


def nearest_passing(fg: RGBA, bg: RGBA, required: float) -> ContrastFix | None:
    """The smallest change (ΔE OK) to the text colour, or else the background, that meets ``required``.

    Lightness moves in OKLCH with hue kept; chroma shrinks only as far as needed to stay in sRGB.
    Of the (up to four) candidates, the one with the smallest ΔE wins; on a tie the text colour does.
    """
    found: list[ContrastFix] = []
    for side, colour, other, moving_fg in (("color", fg, bg, True), ("background-color", bg, fg, False)):
        for darker in (True, False):
            hit = _search(colour, other, required, moving_fg=moving_fg, darker=darker)
            if hit is None:
                continue
            ratio = contrast_ratio(hit, other) if moving_fg else contrast_ratio(other, hit)
            found.append(ContrastFix(side=side, current=to_hex(colour), suggested=to_hex(hit), ratio=floor2(ratio), delta_e=round(delta_e_ok(colour, hit), 2)))
    return min(found, key=lambda fix: fix.delta_e) if found else None
