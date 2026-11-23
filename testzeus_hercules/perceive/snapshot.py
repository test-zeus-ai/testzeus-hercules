"""Take a Set-of-Mark snapshot of a Playwright page and resolve md ids back to locators."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from PIL import Image

from testzeus_hercules.perceive.model import BBox, Element, ElementPath, Snapshot
from testzeus_hercules.perceive.painter import Mark, downscale, encode_jpeg, paint_marks
from testzeus_hercules.perceive.screen_guard import Box, cover_secrets, paint_boxes, secret_boxes

if TYPE_CHECKING:
    from playwright.async_api import Frame, Locator, Page

logger = logging.getLogger(__name__)

MAX_SECTIONS = 15
MAX_MESSAGES = 12
MAX_ERRORS = 20
_ERROR_CLIP = 300
_ISOLATED_WORLD = "hercules-perception"
_SCREENSHOT_QUALITY = 85
_DESCRIBE_TIMEOUT_MS = 2000

Rect = tuple[float, float, float, float]  # x, y, width, height


class ElementGone(Exception):
    """The element behind an md no longer exists or is no longer the same element."""


class PerceptionError(Exception):
    """The page could not be read: the walker failed in the top document (after a retry and, in Chromium, a read in an
    isolated world), or every element it met failed to read. ``message`` is the JavaScript error as the page raised it."""

    def __init__(self, message: str, *, url: str = "", title: str = "", attempts: Sequence[str] = (), errors: Sequence[Mapping[str, Any]] = (), screenshot: bytes | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.url = url
        self.title = title
        self.attempts = tuple(attempts)
        self.errors = tuple(dict(error) for error in errors)
        self.screenshot = screenshot


class _WalkFailed(Exception):
    """The walker raised in a frame (the JavaScript error text is the message)."""


# Resolves once the page has settled (or after ``cap`` ms): no element added or removed for ``quiet`` ms, no finite
# animation or transition still running, and no visible loading indicator (an element marked aria-busy, an indeterminate
# progress bar). A page that is still rendering (a client-side route, content arriving from the server, a menu sliding
# open) is not perceived half-built. Endless animations are ignored: they never finish. Generic signals only.
_QUIET_DOM_JS = """
([quiet, cap]) => new Promise((resolve) => {
  const start = performance.now();
  let last = start;
  const observer = new MutationObserver((records) => {
    if (records.some((r) => r.type === 'childList' && (r.addedNodes.length || r.removedNodes.length))) last = performance.now();
  });
  observer.observe(document, { childList: true, subtree: true });
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    return typeof el.checkVisibility === 'function' ? el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true;
  };
  const animating = () => {
    if (typeof document.getAnimations !== 'function') return false;
    for (const a of document.getAnimations()) {
      if (a.playState !== 'running' || !a.effect || typeof a.effect.getComputedTiming !== 'function') continue;
      const timing = a.effect.getComputedTiming();
      if (Number.isFinite(timing.endTime) && timing.endTime > 0) return true;
    }
    return false;
  };
  const loading = () => {
    for (const el of document.querySelectorAll('[aria-busy="true"], [role="progressbar"]:not([aria-valuenow]), progress:not([value])')) {
      if (shown(el)) return true;
    }
    return false;
  };
  let prev = start;
  const tick = () => {
    const now = performance.now();
    // A tick that ran far later than its 25 ms means the page's thread was stalled (a busy machine): the quiet it
    // measured is the stall, and timers the stall held up have not run yet. Decide on the next tick instead.
    const stalled = now - prev > quiet / 2;
    prev = now;
    const settled = !stalled && now - last >= quiet && document.readyState !== 'loading' && !animating() && !loading();
    if (settled || now - start >= cap) {
      observer.disconnect();
      resolve({ ms: Math.round(now - start), settled });
    } else {
      setTimeout(tick, 25);
    }
  };
  setTimeout(tick, 25);
})
"""
QUIET_DOM_MS = 150
QUIET_DOM_CAP_MS = 3000
_LOAD_WAIT_MS = 5000


async def wait_until_rendered(page: "Page", *, quiet_ms: int = QUIET_DOM_MS, cap_ms: int = QUIET_DOM_CAP_MS) -> None:
    """Wait for the document to load (capped) and the page to settle (see ``_QUIET_DOM_JS``, capped); never raises."""
    from playwright.async_api import Error as PlaywrightError

    try:
        await page.wait_for_load_state("load", timeout=_LOAD_WAIT_MS)
    except PlaywrightError:
        pass
    for _attempt in range(2):  # a navigation can replace the document while we watch it
        try:
            await page.evaluate(_QUIET_DOM_JS, [quiet_ms, cap_ms])
            return
        except PlaywrightError as exc:
            logger.debug("waiting for a quiet DOM failed: %s", exc)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=_LOAD_WAIT_MS)
            except PlaywrightError:
                return


@lru_cache(maxsize=1)
def walker_source() -> str:
    return resources.files("testzeus_hercules.perceive").joinpath("walker.js").read_text(encoding="utf-8")


@dataclass
class _Collected:
    next_md: int = 1
    viewport: tuple[int, int] = (0, 0)
    elements: list[Element] = field(default_factory=list)
    paths: dict[int, ElementPath] = field(default_factory=dict)
    sections: list[tuple[str, float, float]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    # What the walker could not read: one record per (stage, tag, message) with a count, the elements it skipped,
    # and the child frames that could not be walked.
    errors: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    unread_frames: list[str] = field(default_factory=list)
    world: str = "main"
    notes: list[str] = field(default_factory=list)
    # The open modal dialog on top (its name), from the first frame that has one; None when there is none.
    modal: str | None = None
    # The read succeeded only after waiting for the page again (a retry): a screenshot taken before it may be stale.
    retried: bool = False

    def add_errors(self, records: Sequence[Mapping[str, Any]]) -> None:
        for record in records:
            try:
                stage, tag, message, count = str(record.get("stage", "")), str(record.get("tag", "")), str(record.get("message", ""))[:_ERROR_CLIP], int(record.get("count", 1))
            except (TypeError, ValueError, AttributeError):
                continue
            known = next((item for item in self.errors if (item["stage"], item["tag"], item["message"]) == (stage, tag, message)), None)
            if known is not None:
                known["count"] += count
            elif len(self.errors) < MAX_ERRORS:
                self.errors.append({"stage": stage, "tag": tag, "message": message, "count": count})


def _error_line(record: Mapping[str, Any]) -> str:
    tag = f" <{record['tag']}>" if record.get("tag") else ""
    return f"{record.get('message', '')} ({record.get('stage', '')}{tag})"


def _read_notes(out: _Collected) -> list[str]:
    """The lines a snapshot's observation header gets about what could not be read."""
    notes = list(out.notes)
    if out.skipped:
        first = next((record for record in out.errors if record["stage"] == "element"), out.errors[0] if out.errors else None)
        notes.append(f"{out.skipped} element{'s' if out.skipped != 1 else ''} skipped" + (f" (first error: {_error_line(first)})" if first else ""))
    elif out.errors:
        total = sum(int(record["count"]) for record in out.errors)
        notes.append(f"{total} reads failed and were left out (first error: {_error_line(out.errors[0])})")
    if out.unread_frames:
        notes.append("frames not read: " + "; ".join(out.unread_frames))
    return notes


_API_PREFIX = re.compile(r"^[A-Za-z]+\.[A-Za-z]+: ")


def _first_line(text: str) -> str:
    """The first line of an error, without Playwright's ``Frame.evaluate: `` prefix (the page's own error remains)."""
    lines = str(text).strip().splitlines()
    return _API_PREFIX.sub("", lines[0])[:_ERROR_CLIP] if lines else ""


def _intersect(a: Rect | None, b: Rect | None) -> Rect | None:
    if a is None or b is None:
        return None
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _shift(r: Sequence[float] | None, dx: float, dy: float) -> Rect | None:
    if r is None:
        return None
    return (r[0] + dx, r[1] + dy, r[2], r[3])


async def _walk_frame(frame: "Frame", out: _Collected, *, chain: tuple[tuple[str, ...], ...], offset: tuple[float, float], clip: Rect | None, label: str | None, blocked: str = "") -> None:
    """Walk ``frame`` and its child frames into ``out``. A failed walk of the top frame raises ``_WalkFailed``; a child
    frame that cannot be walked is noted in ``out.unread_frames`` and the walk goes on."""
    from playwright.async_api import Error as PlaywrightError

    children = []
    handles = []
    for child in frame.child_frames:
        if child.is_detached():
            continue
        try:
            handles.append(await child.frame_element())
            children.append(child)
        except PlaywrightError:
            logger.debug("skipping child frame without an element: %s", child.name)
    try:
        result: dict[str, Any] = await frame.evaluate(walker_source(), {"start": out.next_md, "frames": handles})
    except PlaywrightError as exc:
        if not chain:
            raise _WalkFailed(_first_line(exc.message if hasattr(exc, "message") else str(exc))) from exc
        logger.warning("a frame of the page could not be read (%s): %s", label, _first_line(str(exc)))
        out.unread_frames.append(f"{label or 'frame'} ({_first_line(str(exc))})")
        return
    finally:
        for handle in handles:
            try:
                await handle.dispose()
            except PlaywrightError:
                pass
    clip = _absorb(result, out, chain=chain, offset=offset, clip=clip, label=label, blocked=blocked)
    dx, dy = offset
    for child, info in zip(children, result.get("frames") or ()):
        if info is None:
            continue
        content = _shift(info["content"], dx, dy)
        child_clip = _intersect(_intersect(_shift(info["visible"], dx, dy), content), clip)
        await _walk_frame(
            child,
            out,
            chain=chain + (tuple(info["path"]),),
            offset=(content[0], content[1]) if content else offset,
            clip=child_clip,
            label=info["label"] or f"frame {len(chain) + 1}",
            # A frame behind a modal dialog (or inert, aria-hidden) hides everything in it the same way.
            blocked=blocked or str(info.get("blocked") or ""),
        )


def _absorb(result: Mapping[str, Any], out: _Collected, *, chain: tuple[tuple[str, ...], ...], offset: tuple[float, float], clip: Rect | None, label: str | None, blocked: str = "") -> Rect | None:
    """Add one frame's walker result to ``out``; returns the frame's clip (the viewport for the top frame).

    ``blocked``: why the frame's own element is not offered (behind a modal dialog, inert, aria-hidden); its elements inherit it.
    """
    modal = result.get("modal")
    if isinstance(modal, Mapping) and out.modal is None and not blocked:
        out.modal = str(modal.get("name") or "")
    if not chain:
        out.viewport = (int(result["viewport"][0]), int(result["viewport"][1]))
        clip = (0.0, 0.0, float(out.viewport[0]), float(out.viewport[1]))
    out.next_md = int(result["next"])
    dx, dy = offset
    for rec in result["elements"]:
        full = _shift(rec["rect"], dx, dy)
        seen = _intersect(_shift(rec["visible"], dx, dy), clip)
        box = seen or full
        out.elements.append(
            Element(
                md=int(rec["md"]),
                role=str(rec["role"]),
                name=str(rec["name"]),
                tag=str(rec["tag"]),
                attrs=MappingProxyType({str(k): str(v) for k, v in rec["attrs"].items()}),
                bbox=BBox(*box) if box else None,
                frame=label,
                in_viewport=seen is not None,
                tier=int(rec["tier"]),
                value=rec["value"],
                checked=rec["checked"],
                disabled=bool(rec["disabled"]),
                dialog=None if rec.get("dialog") is None else str(rec["dialog"]),
                section=str(rec.get("section") or ""),
                scroll=str(rec.get("scroll") or ""),
                scroll_in_dialog=bool(rec.get("scroll_in_dialog")),
                blocked=blocked or str(rec.get("blocked") or ""),
            )
        )
        dialog_path = rec.get("dialog_path")
        out.paths[int(rec["md"])] = ElementPath(frames=chain, segments=tuple(rec["path"]), dialog=tuple(dialog_path) if dialog_path else None)
    out.sections.extend((name, top + dy, bottom + dy) for name, top, bottom in result["sections"])
    out.messages.extend(str(message) for message in result.get("messages", ()) if str(message) not in out.messages)
    out.add_errors(result.get("errors") or ())
    out.skipped += int(result.get("skipped") or 0)
    return clip


async def _walk_isolated(page: "Page", out: _Collected) -> bool:
    """Walk the top document in an isolated world (Chromium only): the page's own scripts, and whatever they changed in
    the page's globals and prototypes, are not there. Child frames are not walked this way. False when there is no such
    world to use; raises ``_WalkFailed`` when the walk itself fails."""
    try:
        session = await page.context.new_cdp_session(page)
    except Exception as exc:  # not Chromium, or no protocol session: nothing more to try
        logger.debug("no isolated world for perception: %s", exc)
        return False
    try:
        tree = await session.send("Page.getFrameTree")
        world = await session.send("Page.createIsolatedWorld", {"frameId": tree["frameTree"]["frame"]["id"], "worldName": _ISOLATED_WORLD})
        expression = f"({walker_source()})({json.dumps({'start': out.next_md})})"
        response = await session.send("Runtime.evaluate", {"expression": expression, "contextId": world["executionContextId"], "returnByValue": True})
    except Exception as exc:
        raise _WalkFailed(_first_line(str(exc))) from exc
    finally:
        try:
            await session.detach()
        except Exception:
            pass
    if response.get("exceptionDetails"):
        details = response["exceptionDetails"]
        raise _WalkFailed(_first_line((details.get("exception") or {}).get("description") or details.get("text") or "the walker failed"))
    _absorb(response["result"]["value"], out, chain=(), offset=(0.0, 0.0), clip=None, label=None)
    out.world = "isolated"
    frames = [child for child in page.main_frame.child_frames if not child.is_detached()]
    if frames:
        out.unread_frames.append(f"{len(frames)} frame(s) (the page was read in an isolated world)")
    return True


async def _collect(page: "Page") -> _Collected:
    """Walk the page: in its own world, once more after it settled when that fails, then in an isolated world.

    Raises ``PerceptionError`` when none of these could read the page.
    """
    attempts: list[str] = []
    last: _Collected | None = None
    for attempt in range(2):
        out = _Collected()
        try:
            await _walk_frame(page.main_frame, out, chain=(), offset=(0.0, 0.0), clip=None, label=None)
        except _WalkFailed as exc:
            attempts.append(str(exc))
            logger.warning("reading the page failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                await wait_until_rendered(page)
            continue
        out.retried = attempt > 0
        if out.elements and out.skipped:
            return await _better_of(page, out)
        if out.elements or not out.skipped:
            return out
        attempts.append(f"every element failed to read: {_read_notes(out)[0]}")
        last = out
        break
    isolated = _Collected()
    try:
        used = await _walk_isolated(page, isolated)
    except _WalkFailed as exc:
        attempts.append(f"isolated world: {exc}")
        used = False
    if used and (isolated.elements or not isolated.skipped):
        isolated.notes.append(f"read in an isolated world: the page's own scripts broke the walk ({attempts[-1]})")
        isolated.retried = True
        return isolated
    errors = (last.errors if last is not None else []) + isolated.errors
    raise PerceptionError(attempts[0] if attempts else "the walker returned nothing", url=_page_url(page), attempts=attempts, errors=errors[:MAX_ERRORS])


async def _better_of(page: "Page", main: _Collected) -> _Collected:
    """The page's own world skipped elements: read it in an isolated world too and keep the read that lists more."""
    isolated = _Collected()
    try:
        used = await _walk_isolated(page, isolated)
    except _WalkFailed as exc:
        logger.debug("isolated read after skipped elements failed: %s", exc)
        return main
    if not used or len(isolated.elements) <= len(main.elements):
        return main
    isolated.retried = main.retried
    isolated.notes.append(f"read in an isolated world: the page's own scripts kept {main.skipped} element{'s' if main.skipped != 1 else ''} from being read ({_read_notes(main)[0]})")
    return isolated


def _page_url(page: Any) -> str:
    try:
        return str(page.url)
    except Exception:
        return ""


def _split_sections(sections: list[tuple[str, float, float]], height: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    above: list[str] = []
    below: list[str] = []
    for name, top, bottom in sorted(sections, key=lambda s: s[1]):
        if bottom <= 0 and name not in above:
            above.append(name)
        elif top >= height and name not in below:
            below.append(name)
    return tuple(above[-MAX_SECTIONS:]), tuple(below[:MAX_SECTIONS])


def _render(raw: bytes, marks: list[Mark], viewport: tuple[int, int], covered: Sequence[Box] = ()) -> tuple[bytes, Any]:
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    plain = downscale(paint_boxes(image, covered) if covered else image)
    plain_jpeg = encode_jpeg(plain)
    return plain_jpeg, paint_marks(plain.copy(), marks, viewport=viewport)


def _is_focus(name: str, terms: Sequence[str]) -> bool:
    lowered = name.lower()
    return any(t and t.lower() in lowered for t in terms)


async def take_snapshot(page: "Page", *, seq: int, focus_terms: Sequence[str] = ()) -> Snapshot:
    """Mark every interactive element in all frames, screenshot the viewport and paint the marks.

    The page is not modified: the walker only reads the DOM and painting happens on the image.
    Perception waits for the page to finish rendering first (see :func:`wait_until_rendered`).
    """
    await wait_until_rendered(page)
    shot = asyncio.ensure_future(_screenshot(page))
    try:
        collected = await _collect(page)
        title = await page.title()
    except PerceptionError as exc:
        # The page could not be read: keep what it looked like for the evidence.
        exc.screenshot = await _covered(page, await _screenshot_of(shot))
        exc.title = await _title_of(page)
        raise
    except BaseException:
        shot.cancel()
        raise
    raw = await shot
    if collected.retried:
        # The read waited for the page again: the first screenshot may show it before that, so the marks could drift.
        raw = await _screenshot(page)
    # Secret values on screen are covered before the image is kept or sent (see perceive.screen_guard).
    covered = await secret_boxes(page) or ()
    terms = tuple(t for t in focus_terms if t)
    viewport = collected.viewport
    # Only offered elements get a mark: nothing behind an open modal dialog (unless the modal holds nothing that can be read).
    offered = [e for e in collected.elements if not e.blocked] or collected.elements
    marks = [Mark(md=e.md, bbox=e.bbox, tier=e.tier, must=_is_focus(e.name, terms)) for e in offered if e.in_viewport and e.bbox is not None]
    plain_jpeg, paint = await asyncio.to_thread(_render, raw, marks, viewport, covered)
    above, below = _split_sections(collected.sections, viewport[1])
    return Snapshot(
        seq=seq,
        url=page.url,
        title=title,
        viewport=viewport,
        elements=tuple(collected.elements),
        plain_jpeg=plain_jpeg,
        som_jpeg=paint.jpeg,
        above=above,
        below=below,
        focus_terms=terms,
        paths=MappingProxyType(dict(collected.paths)),
        headings=tuple((name, float(top)) for name, top, _ in sorted(collected.sections, key=lambda s: s[1])),
        messages=tuple(collected.messages[:MAX_MESSAGES]),
        paint=paint,
        notes=tuple(_read_notes(collected)),
        read_errors=tuple(dict(record) for record in collected.errors),
        world=collected.world,
        modal=collected.modal,
    )


async def _screenshot(page: "Page") -> bytes:
    return await page.screenshot(type="jpeg", quality=_SCREENSHOT_QUALITY, scale="css", caret="initial")


async def _covered(page: "Page", shot: bytes | None) -> bytes | None:
    """``shot`` with the secrets on screen covered (see perceive.screen_guard)."""
    if not shot:
        return shot
    try:
        boxes = await secret_boxes(page)
    except Exception:
        boxes = [(0.0, 0.0, float("inf"), float("inf"))]
    return cover_secrets(shot, boxes, quality=_SCREENSHOT_QUALITY)


async def _screenshot_of(shot: "asyncio.Future[bytes]") -> bytes | None:
    try:
        return await shot
    except Exception:
        return None


async def _title_of(page: Any) -> str:
    try:
        return str(await page.title())
    except Exception:
        return ""


def _is_script_error(exc: Exception) -> bool:
    """True for an error the page's JavaScript raised (not a timeout, a closed page or a detached element)."""
    text = str(exc)
    return not any(marker in text for marker in ("Timeout", "Target closed", "has been closed", "detached", "not attached"))


def _chain(scope: Any, segments: Sequence[str]) -> "Locator":
    locator = scope.locator(segments[0])
    for segment in segments[1:]:
        locator = locator.locator(segment)
    return locator


async def resolve(page: "Page", snapshot: Snapshot, md: int, *, report: dict[str, Any] | None = None) -> "Locator":
    """A Locator for ``md`` that still points at the same element (same role and name).

    The element is found by its recorded path first. When that no longer finds it (a page that re-renders its fields,
    after a scroll or on a timer, replaces the nodes), it is looked for again, in its frame and dialog:

    1. by the ways a test finds an element (a test id, its role and name, its label, placeholder, text, id or ``name``
       attribute, see :mod:`testzeus_hercules.perceive.locators`), keeping one only when it matches exactly one element
       whose role and name are the recorded ones; that element is scrolled into view in its scroll area;
    2. in a fresh read of the page: the one element with the same role, name, dialog and frame (exact match only).

    ``report`` (when given) is told which way found it: ``{"via": "path"}``, or ``{"via": "locator", "strategy": ...}``
    or ``{"via": "fresh_read", ...}`` after the path failed. Raises ``ElementGone`` when the md is unknown or no way
    finds exactly one such element.
    """
    try:
        element = snapshot.element(md)
        path = snapshot.paths[md]
    except KeyError as exc:
        raise ElementGone(f"md {md} is not in snapshot {snapshot.seq}") from exc
    try:
        locator = await _by_path(page, snapshot, element, path, md)
    except ElementGone as gone:
        failed = gone
    else:
        if report is not None:
            report["via"] = "path"
        return locator
    for fallback in (_by_locators, _by_fresh_read):
        found = await fallback(page, snapshot, element, path)
        if found is not None:
            located, how = found
            if report is not None:
                report.update(how, path_error=str(failed))
            return located
    raise failed


async def _frame_scope(page: "Page", path: ElementPath, md: int) -> Any:
    scope: Any = page
    for frame_segments in path.frames:
        frame_locator = _chain(scope, frame_segments)
        if await frame_locator.count() < 1:
            raise ElementGone(f"frame of md {md} is gone")
        scope = frame_locator.first.content_frame
    return scope


async def _by_path(page: "Page", snapshot: Snapshot, element: Any, path: ElementPath, md: int) -> "Locator":
    from playwright.async_api import Error as PlaywrightError

    try:
        scope = await _frame_scope(page, path, md)
        candidates = _chain(scope, path.segments)
        count = await candidates.count()
        for index in range(count):
            locator = candidates if count == 1 else candidates.nth(index)
            try:
                seen = await locator.evaluate(walker_source(), {"describe": 1}, timeout=_DESCRIBE_TIMEOUT_MS)
            except PlaywrightError as exc:
                if count == 1 and _is_script_error(exc):
                    return locator  # the page's scripts break describing it, but its path finds exactly one element
                raise
            same_path = tuple(seen["path"]) == path.segments
            # Read in an isolated world, the element's role and name may differ from what the page's world reports.
            if same_path and (snapshot.world == "isolated" or (seen["role"] == element.role and seen["name"] == element.name)):
                return locator
    except PlaywrightError as exc:
        raise ElementGone(f"md {md} could not be re-found: {exc}") from exc
    raise ElementGone(f"md {md} ({element.role} {element.name!r}) is no longer on the page")


def _facts(element: Any) -> dict[str, Any]:
    """What the snapshot knows of an element, in the form the locator candidates are built from."""
    attrs = element.attrs
    return {
        "tag": element.tag,
        "id": attrs.get("id") or None,
        "name": attrs.get("name") or None,
        "placeholder": attrs.get("placeholder") or None,
        "aria_label": attrs.get("aria-label") or None,
        "labels": [attrs["label"]] if attrs.get("label") else [],
        "test_ids": {},
        "text": "" if element.role in _FIELD_ROLES else element.name,
    }


_FIELD_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"})


# True when ``el`` is inside ``dialog`` in the composed tree (through shadow roots and the slots it is assigned to).
_INSIDE_JS = """
(el, dialog) => {
  for (let n = el; n; n = n.assignedSlot || (n.parentNode && n.parentNode.nodeType === 11 ? n.parentNode.host : n.parentNode)) {
    if (n === dialog) return true;
  }
  return false;
}
"""
_MAX_MATCHES = 20


async def _dialog_handle(scope: Any, element: Any, path: ElementPath) -> Any:
    """The element's dialog on the live page (found by its path, else by its role and name), or None."""
    from playwright.async_api import Error as PlaywrightError

    if element.dialog is None:
        return None
    try:
        if path.dialog:
            dialog = _chain(scope, path.dialog)
            if await dialog.count() == 1:
                return await dialog.element_handle(timeout=_DESCRIBE_TIMEOUT_MS)
        if element.dialog:
            for role in ("dialog", "alertdialog"):
                named = scope.get_by_role(role, name=element.dialog, exact=True)
                if await named.count() == 1:
                    return await named.element_handle(timeout=_DESCRIBE_TIMEOUT_MS)
    except PlaywrightError:
        return None
    return None


async def _same(locator: "Locator", element: Any, world: str) -> bool:
    """True when ``locator`` (one element) has the element's role and name."""
    if world == "isolated":
        return True
    seen = await locator.evaluate(walker_source(), {"describe": 1}, timeout=_DESCRIBE_TIMEOUT_MS)
    return bool(seen["role"] == element.role and seen["name"] == element.name)


async def _only_match(locator: "Locator", dialog: Any) -> "Locator | None":
    """The one element ``locator`` matches (inside ``dialog`` when given), or None when it matches none or several."""
    count = await locator.count()
    if count == 0 or count > _MAX_MATCHES:
        return None
    inside = []
    for index in range(count):
        item = locator if count == 1 else locator.nth(index)
        if dialog is None or await item.evaluate(_INSIDE_JS, dialog, timeout=_DESCRIBE_TIMEOUT_MS):
            inside.append(item)
    return inside[0] if len(inside) == 1 else None


async def _by_locators(page: "Page", snapshot: Snapshot, element: Any, path: ElementPath) -> tuple["Locator", dict[str, Any]] | None:
    """The element found again by a locator a test would use, in its frame (and inside its dialog), matching exactly one element."""
    from playwright.async_api import Error as PlaywrightError

    from testzeus_hercules.perceive.locators import build_locator, element_candidates

    try:
        scope = await _frame_scope(page, path, element.md)
        dialog = await _dialog_handle(scope, element, path)
    except (ElementGone, PlaywrightError):
        return None
    try:
        for spec in element_candidates(_facts(element), element.role, element.name, None):
            if spec.strategy == "role" and spec.name is None:
                continue  # a role alone does not say which element
            try:
                locator = await _only_match(build_locator(scope, spec), dialog)
                if locator is None or not await _same(locator, element, snapshot.world):
                    continue
                await locator.scroll_into_view_if_needed(timeout=_DESCRIBE_TIMEOUT_MS)
            except PlaywrightError as exc:
                logger.debug("locator fallback %s failed: %s", spec.strategy, exc)
                continue
            how = spec.model_dump(mode="json", exclude_defaults=True, exclude={"count"})
            return locator, {"via": "locator", "within": "dialog" if dialog is not None else "frame", **how}
    finally:
        if dialog is not None:
            try:
                await dialog.dispose()
            except PlaywrightError:
                pass
    return None


async def _by_fresh_read(page: "Page", snapshot: Snapshot, element: Any, path: ElementPath) -> tuple["Locator", dict[str, Any]] | None:
    """The element found in a fresh read of the page: the only offered element with its role, name, dialog and frame."""
    if not element.name:
        return None
    try:
        fresh = await _collect(page)
    except PerceptionError:
        return None
    offered = [item for item in fresh.elements if not item.blocked] or fresh.elements
    matches = [item for item in offered if (item.role, item.name, item.dialog, item.frame) == (element.role, element.name, element.dialog, element.frame)]
    if len(matches) != 1:
        return None
    match = matches[0]
    view = Snapshot(
        seq=snapshot.seq,
        url=snapshot.url,
        title=snapshot.title,
        viewport=snapshot.viewport,
        elements=(match,),
        plain_jpeg=b"",
        som_jpeg=b"",
        above=(),
        below=(),
        paths=MappingProxyType({match.md: fresh.paths[match.md]}),
        world=fresh.world,
    )
    try:
        locator = await _by_path(page, view, match, fresh.paths[match.md], match.md)
    except ElementGone:
        return None
    return locator, {"via": "fresh_read", "role": match.role, "name": match.name}
