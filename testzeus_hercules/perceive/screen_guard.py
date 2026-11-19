"""Secret values kept out of screenshots.

The text a model reads has secret values masked; a screenshot of the same page would show them as typed. A run
registers its secret values with :func:`guard_secrets` for the browser context of its page (so popups are covered
too), and every screenshot Hercules keeps or sends goes through :func:`capture` or :func:`cover_secrets`: each
field whose value holds a secret, each text node that shows one, and each field that looks like it holds a secret
(a one-time code, a card number, a secret-like name) is painted over before the image leaves memory.

The page is never modified: positions are read with a script and the covering happens on the image. A frame whose
positions cannot be read while secrets are set is covered whole, so an unreadable page never leaks a value.
A short secret (under 8 characters) counts only where it stands alone, as in :meth:`CaseData.redact`.
Browser video recordings are written by the browser itself and are not covered.
"""

from __future__ import annotations

import asyncio
import io
import logging
import weakref
from collections.abc import Callable, Sequence
from typing import Any

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

COVER = (60, 64, 72)
_PAD = 3
_ANYWHERE_SECRET_CHARS = 8
_READ_TIMEOUT_S = 2.0

Box = tuple[float, float, float, float]  # x, y, width, height in CSS px of the captured image
SecretValues = Callable[[], Sequence[str]]

_ATTR = "_hercules_secret_guard"
_GUARDS: "weakref.WeakKeyDictionary[Any, SecretValues]" = weakref.WeakKeyDictionary()

# Rectangles (viewport CSS px, or document CSS px for a full-page capture) of everything in this frame that shows a
# secret: fields whose value holds one, text nodes that contain one, and filled fields that look secret.
_FIND_JS = r"""
({values, anywhere, document: whole}) => {
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const tests = values.filter(Boolean).map((v) => v.length >= anywhere
    ? (s) => s.includes(v)
    : ((re) => (s) => re.test(s))(new RegExp('(^|[^A-Za-z0-9])' + esc(v) + '(?![A-Za-z0-9])')));
  const holds = (s) => !!s && tests.some((t) => t(String(s)));
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const secretLike = (el) => {
    const named = [el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('autocomplete')].map(clean).join(' ');
    return /(one-time-code|current-password|new-password|cc-number|cc-csc)/.test(named)
      || /(passw|secret|token|otp|one.?time|api.?key|cvv|cvc|ssn|(^|[^a-z])pin([^a-z]|$))/.test(named);
  };
  const W = innerWidth, H = innerHeight, dx = whole ? scrollX : 0, dy = whole ? scrollY : 0;
  const out = [];
  const push = (r) => {
    if (r.width <= 0 || r.height <= 0) return;
    if (!whole && (r.right <= 0 || r.bottom <= 0 || r.left >= W || r.top >= H)) return;
    out.push([r.left + dx, r.top + dy, r.width, r.height]);
  };
  const walk = (root) => {
    for (const el of root.querySelectorAll('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) {
      const type = (el.type || '').toLowerCase();
      if (['hidden', 'checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file'].includes(type)) continue;
      let shown = el.isContentEditable ? el.innerText : el.value;
      if (el.localName === 'select') shown = [...el.selectedOptions].map((o) => o.text + ' ' + o.value).join(' ');
      if (holds(shown) || (shown && type !== 'password' && el.localName !== 'select' && secretLike(el))) push(el.getBoundingClientRect());
    }
    if (tests.length) {
      const texts = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = texts.nextNode(); node; node = texts.nextNode()) {
        if (!holds(node.nodeValue)) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const r of range.getClientRects()) push(r);
      }
    }
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) walk(el.shadowRoot);
  };
  walk(document);
  return out;
}
"""


def guard_secrets(page: Any, values: SecretValues) -> None:
    """Cover ``values()`` (read at each capture) in every screenshot of ``page``'s browser context."""
    key = _key(page)
    try:
        _GUARDS[key] = values
    except TypeError:  # an object that cannot be a weak key (a test double): keep the guard on it
        try:
            setattr(key, _ATTR, values)
        except Exception:
            logger.info("cannot guard screenshots of %s", type(key).__name__)


def _key(page: Any) -> Any:
    try:
        return page.context
    except Exception:
        return page


def _values(page: Any) -> tuple[str, ...] | None:
    """The secret values to cover on ``page``, or None when no guard is set for it."""
    key = _key(page)
    try:
        provider = _GUARDS.get(key)
    except TypeError:
        provider = None
    provider = provider or getattr(key, _ATTR, None)
    if provider is None:
        return None
    try:
        return tuple(str(v) for v in provider() if v)
    except Exception:  # a secret that cannot be read cannot be shown either
        return ()


async def _frame_offset(frame: Any) -> Box | None:
    """Where a child frame's viewport sits in the page (CSS px), or None when it is not shown."""
    element = await frame.frame_element()
    box = await element.bounding_box()
    if box is None:
        return None
    left, top = await element.evaluate("(e) => [e.clientLeft, e.clientTop]")
    return box["x"] + left, box["y"] + top, box["width"] - 2 * left, box["height"] - 2 * top


async def _frame_boxes(frame: Any, values: tuple[str, ...], full_page: bool) -> list[Box]:
    args = {"values": list(values), "anywhere": _ANYWHERE_SECRET_CHARS, "document": full_page}
    found = await asyncio.wait_for(frame.evaluate(_FIND_JS, args), _READ_TIMEOUT_S)
    return [(float(x), float(y), float(w), float(h)) for x, y, w, h in found]


async def secret_boxes(page: Any, *, full_page: bool = False) -> list[Box] | None:
    """Rectangles of ``page`` that show a secret, in the coordinates of a CSS-scaled screenshot; None when unguarded.

    A frame that cannot be read while secrets are set is returned whole (the main frame as ``(0, 0, inf, inf)``).
    """
    values = _values(page)
    if values is None:
        return None
    frames = getattr(page, "frames", None)
    if not frames:
        return []
    boxes: list[Box] = []
    main = page.main_frame
    for frame in frames:
        try:
            offset = None if frame is main else await _frame_offset(frame)
            if frame is not main and offset is None:
                continue
            found = await _frame_boxes(frame, values, full_page and frame is main)
        except Exception as exc:
            if not values:
                continue
            logger.info("could not read a frame for secrets; covering it: %s", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
            if frame is main:
                return [(0.0, 0.0, float("inf"), float("inf"))]
            try:
                offset = await _frame_offset(frame)
            except Exception:
                return [(0.0, 0.0, float("inf"), float("inf"))]
            if offset is not None:
                boxes.append(offset)
            continue
        if offset is None:
            boxes.extend(found)
            continue
        ox, oy, ow, oh = offset
        for x, y, w, h in found:
            left, top = max(x, 0.0), max(y, 0.0)
            right, bottom = min(x + w, ow), min(y + h, oh)
            if right > left and bottom > top:
                boxes.append((ox + left, oy + top, right - left, bottom - top))
    return boxes


def paint_boxes(image: Image.Image, boxes: Sequence[Box], scale: float = 1.0) -> Image.Image:
    """``image`` with every box (CSS px, times ``scale`` image px) filled with the cover colour, padded a little."""
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for x, y, w, h in boxes:
        x0, y0 = max(0.0, x * scale - _PAD), max(0.0, y * scale - _PAD)
        x1, y1 = min(out.width - 1.0, (x + w) * scale + _PAD), min(out.height - 1.0, (y + h) * scale + _PAD)
        if x1 > x0 and y1 > y0:
            draw.rectangle([x0, y0, x1, y1], fill=COVER)
    return out


def _encode(image: Image.Image, kind: str, quality: int | None) -> bytes:
    buffer = io.BytesIO()
    if kind == "png":
        image.save(buffer, format="PNG")
    else:
        image.save(buffer, format="JPEG", quality=quality or 80)
    return buffer.getvalue()


def cover_secrets(data: bytes, boxes: Sequence[Box] | None, *, kind: str = "jpeg", quality: int | None = None) -> bytes:
    """``data`` (a CSS-scaled screenshot) with ``boxes`` covered; unchanged when there is nothing to cover."""
    if not boxes:
        return data
    return _encode(paint_boxes(Image.open(io.BytesIO(data)), boxes), kind, quality)


async def capture(page: Any, *, type: str = "jpeg", quality: int | None = None, full_page: bool = False, **options: Any) -> bytes:
    """``page.screenshot`` (always CSS-scaled) with every secret on screen covered."""
    kwargs: dict[str, Any] = {"type": type, "full_page": full_page, "scale": "css", **options}
    if type == "jpeg" and quality is not None:
        kwargs["quality"] = quality
    data = await page.screenshot(**kwargs)
    boxes = await secret_boxes(page, full_page=full_page)
    return cover_secrets(data, boxes, kind=type, quality=quality)
