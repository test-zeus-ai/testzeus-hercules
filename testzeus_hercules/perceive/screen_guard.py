"""Secret values kept out of screenshots and browser recordings.

The text a model reads has secret values masked; a screenshot of the same page would show them as typed. A run
registers its secret values with :func:`guard_secrets` for the browser context of its page (so popups are covered
too), and every screenshot Hercules keeps or sends goes through :func:`capture` or :func:`cover_secrets`: each
field whose value holds a secret, each text node that shows one, and each field that looks like it holds a secret
(a one-time code, a card number, a secret-like name) is painted over before the image leaves memory.

The page never receives a secret value: a script reads the fields' values and the page's text, the match happens
here, and a second script reads where the matching ones are. The covering happens on the image. A frame that
cannot be read while secrets are set is covered whole, so an unreadable page never leaks a value. A short secret
(under 8 characters) counts only where it stands alone, as in :meth:`CaseData.redact`.

A browser recording is written by the browser, frame by frame, so it cannot be painted afterwards. While a run
records, :func:`mask_recording` puts a cover layer in the page: :func:`mask_field` marks a field before Hercules
types a secret into it, and every field that looks secret is covered once it has a value; the layer redraws before
each frame. It knows which elements to cover, never the values. When a screenshot check finds a secret outside the
layer (text the page prints, a field it re-created, a modal dialog drawn above the layer), :func:`recording_exposed`
says so, and the run does not keep its recording.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import weakref
from collections.abc import Callable, Sequence
from typing import Any

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

COVER = (60, 64, 72)
_PAD = 3
_ANYWHERE_SECRET_CHARS = 8
_READ_TIMEOUT_S = 2.0
_MAX_ITEMS = 20_000  # fields and text nodes read from one frame; past this the frame is covered whole

Box = tuple[float, float, float, float]  # x, y, width, height in CSS px of the captured image
SecretValues = Callable[[], Sequence[str]]

_ATTR = "_hercules_secret_guard"
_EXPOSED_ATTR = "_hercules_secret_exposed"
_GUARDS: "weakref.WeakKeyDictionary[Any, SecretValues]" = weakref.WeakKeyDictionary()
_EXPOSED: "weakref.WeakKeyDictionary[Any, bool]" = weakref.WeakKeyDictionary()

# The same rule as the field facts in act/browser.py: a name, id, label or autocomplete that says "secret".
_SECRET_NAME_JS = r"""
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const secretLike = (el) => {
    const named = [el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('autocomplete')].map(clean).join(' ');
    return /(one-time-code|current-password|new-password|cc-number|cc-csc)/.test(named)
      || /(passw|secret|token|otp|one.?time|api.?key|cvv|cvc|ssn|(^|[^a-z])pin([^a-z]|$))/.test(named);
  };
  const shownOf = (el) => el.isContentEditable ? el.innerText : el.value;
  // A filled field that looks secret and shows its value (a password input already shows dots).
  const secretField = (el) => {
    const type = (el.type || '').toLowerCase();
    return el.localName !== 'select' && type !== 'password' && !!shownOf(el) && secretLike(el);
  };
"""

# The cover layer for a recording (added to every document of the context). Elements to cover: the ones Hercules
# marked before typing a secret, and filled secret-like fields. No secret value is ever given to the page.
MASK_JS = (
    r"""
(() => {
  const KEY = Symbol.for('hercules.mask');
  if (window[KEY]) return;
"""
    + _SECRET_NAME_JS
    + r"""
  const marked = [];
  const isMarked = (el) => marked.some((ref) => ref.deref() === el);
  const onTop = (el) => { try { return !!el.closest('dialog:modal, :popover-open'); } catch (e) { return false; } };
  let host = null, root = null, pool = [];
  const layer = () => {
    if (host && host.isConnected) return true;
    const doc = document.documentElement;
    if (!doc) return false;
    host = document.createElement('hercules-mask');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'all:initial;position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;';
    root = host.attachShadow({mode: 'closed'});
    pool = [];
    doc.appendChild(host);
    return true;
  };
  const targets = () => {
    const out = [];
    for (const ref of marked) { const el = ref.deref(); if (el && el.isConnected) out.push(el); }
    for (const el of document.querySelectorAll('input, textarea, [contenteditable]:not([contenteditable="false"])')) {
      if (!isMarked(el) && secretField(el)) out.push(el);
    }
    return out;
  };
  const draw = () => {
    if (!layer()) return;
    const rects = [];
    for (const el of targets()) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight) rects.push(r);
    }
    while (pool.length < rects.length) {
      const box = document.createElement('div');
      box.style.cssText = 'position:fixed;background:rgb(__COVER__);pointer-events:none;';
      root.appendChild(box);
      pool.push(box);
    }
    pool.forEach((box, i) => {
      const r = rects[i];
      if (!r) { box.style.display = 'none'; return; }
      box.style.display = 'block';
      box.style.left = (r.left - 3) + 'px'; box.style.top = (r.top - 3) + 'px';
      box.style.width = (r.width + 6) + 'px'; box.style.height = (r.height + 6) + 'px';
    });
  };
  // Whether the layer covers this node: a marked field, a filled secret-like field, or text inside one of them,
  // and not inside a modal dialog or popover (those are drawn above the layer).
  const covers = (node) => {
    let el = node && (node.nodeType === 1 ? node : node.parentElement);
    const start = el;
    for (; el; el = el.parentElement) {
      if (isMarked(el) || (el.matches && el.matches('input, textarea, [contenteditable]') && secretField(el))) return !onTop(start);
    }
    return false;
  };
  window[KEY] = {mark(el) { if (!isMarked(el)) marked.push(new WeakRef(el)); draw(); }, covers};
  const tick = () => { try { draw(); } catch (e) {} requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  document.addEventListener('input', () => { try { draw(); } catch (e) {} }, true);
})();
""".replace("__COVER__", ",".join(map(str, COVER)))
)

# What the frame shows (fields' values and text nodes), for the match to happen outside the page. The nodes stay in
# the frame until _RECTS_JS asks where the matching ones are.
_READ_JS = (
    r"""
({texts, limit}) => {
"""
    + _SECRET_NAME_JS
    + r"""
  const nodes = [], out = [];
  const add = (node, kind, text, secret) => {
    if (nodes.length >= limit) throw new Error('too many fields and text nodes to read');
    nodes.push(node);
    out.push([kind, text, secret]);
  };
  const walk = (root) => {
    for (const el of root.querySelectorAll('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) {
      const type = (el.type || '').toLowerCase();
      if (['hidden', 'checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file'].includes(type)) continue;
      let shown = shownOf(el);
      if (el.localName === 'select') shown = [...el.selectedOptions].map((o) => o.text + ' ' + o.value).join(' ');
      if (shown || secretField(el)) add(el, 'field', String(shown || ''), secretField(el));
    }
    if (texts) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.nodeValue && node.nodeValue.trim()) add(node, 'text', node.nodeValue, false);
      }
    }
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) walk(el.shadowRoot);
  };
  walk(document);
  window[Symbol.for('hercules.guard')] = nodes;
  return out;
}
"""
)

# Where the chosen nodes are (viewport CSS px, or document CSS px for a full-page capture), and whether the
# recording's cover layer hides each one.
_RECTS_JS = r"""
({picked, document: whole}) => {
  const nodes = window[Symbol.for('hercules.guard')] || [];
  delete window[Symbol.for('hercules.guard')];
  const mask = window[Symbol.for('hercules.mask')];
  const W = innerWidth, H = innerHeight, dx = whole ? scrollX : 0, dy = whole ? scrollY : 0;
  const out = [];
  for (const i of picked) {
    const node = nodes[i];
    if (!node || !node.isConnected) continue;
    let rects;
    if (node.nodeType === 1) rects = [node.getBoundingClientRect()];
    else { const range = document.createRange(); range.selectNodeContents(node); rects = [...range.getClientRects()]; }
    const covered = !!(mask && mask.covers(node));
    for (const r of rects) {
      if (r.width <= 0 || r.height <= 0) continue;
      if (!whole && (r.right <= 0 || r.bottom <= 0 || r.left >= W || r.top >= H)) continue;
      out.push([r.left + dx, r.top + dy, r.width, r.height, covered]);
    }
  }
  return out;
}
"""

_MARK_JS = "(el) => { const mask = window[Symbol.for('hercules.mask')]; if (mask) mask.mark(el); }"


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


def holds_secret(text: str, values: Sequence[str]) -> bool:
    """Whether ``text`` shows one of ``values``: anywhere for 8 characters or more, else standing alone."""
    if not text:
        return False
    for value in values:
        if not value:
            continue
        if len(value) >= _ANYWHERE_SECRET_CHARS:
            if value in text:
                return True
        elif re.search(r"(?:^|[^A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", text):
            return True
    return False


def _expose(page: Any) -> None:
    key = _key(page)
    try:
        _EXPOSED[key] = True
    except TypeError:
        try:
            setattr(key, _EXPOSED_ATTR, True)
        except Exception:
            pass


def recording_exposed(page: Any) -> bool:
    """Whether a screenshot check saw a secret the recording's cover layer did not hide (or could not read a frame)."""
    key = _key(page)
    try:
        if _EXPOSED.get(key):
            return True
    except TypeError:
        pass
    return bool(getattr(key, _EXPOSED_ATTR, False))


async def mask_recording(page: Any) -> None:
    """Add the recording's cover layer to every document of ``page``'s context (now and after each navigation)."""
    context = _key(page)
    await context.add_init_script(MASK_JS)
    for each in list(getattr(context, "pages", None) or [page]):
        for frame in list(getattr(each, "frames", None) or []):
            try:
                await frame.evaluate(MASK_JS)
            except Exception:  # a frame that is navigating gets the layer from the init script
                pass


async def mask_field(locator: Any, value: str) -> None:
    """Before a secret is typed into ``locator``'s field, put it under the recording's cover layer."""
    try:
        values = _values(locator.page)
    except Exception:
        return
    if not values or not holds_secret(value, values):
        return
    try:
        await locator.evaluate(_MARK_JS)
    except Exception as exc:  # the field could not be marked: the screenshot check will see the value
        logger.info("could not mark a field for the recording's cover: %s", type(exc).__name__)


async def _frame_offset(frame: Any) -> Box | None:
    """Where a child frame's viewport sits in the page (CSS px), or None when it is not shown."""
    element = await frame.frame_element()
    box = await element.bounding_box()
    if box is None:
        return None
    left, top = await element.evaluate("(e) => [e.clientLeft, e.clientTop]")
    return box["x"] + left, box["y"] + top, box["width"] - 2 * left, box["height"] - 2 * top


async def _frame_boxes(frame: Any, values: tuple[str, ...], full_page: bool) -> tuple[list[Box], bool]:
    """The frame's boxes that show a secret, and whether any of them is outside the recording's cover layer."""
    shown = await asyncio.wait_for(frame.evaluate(_READ_JS, {"texts": bool(values), "limit": _MAX_ITEMS}), _READ_TIMEOUT_S)
    picked = [i for i, (kind, text, secret_like) in enumerate(shown) if holds_secret(str(text), values) or (kind == "field" and secret_like)]
    if not picked:
        return [], False
    found = await asyncio.wait_for(frame.evaluate(_RECTS_JS, {"picked": picked, "document": full_page}), _READ_TIMEOUT_S)
    boxes = [(float(x), float(y), float(w), float(h)) for x, y, w, h, _ in found]
    return boxes, any(not covered for *_, covered in found)


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
            found, exposed = await _frame_boxes(frame, values, full_page and frame is main)
        except Exception as exc:
            if not values:
                continue
            _expose(page)
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
        if exposed:
            _expose(page)
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
