"""Browser actions on a Playwright page or a resolved locator.

Every action returns an :class:`ActionResult` instead of raising, waits briefly
for the page to settle (network idle, capped at :data:`SETTLE_MS`), and fails
with a clear message when the element is disabled or not editable. Typed values
are never echoed in results, since they may be secrets. A fill reads the field
back and fails when it does not hold the requested value.

After a fill or a choice the field is checked the way a user would see it: focus is moved
away from it (``blur``, as leaving the field does; never a click elsewhere), the page is let
settle briefly, and the field is read again (see :class:`FieldFacts`: its value, whether the
page or the browser marks it invalid, the error text tied to it and its format hints). A
field the page marks invalid fails the action (``kind="invalid"``); a value the field changed
on blur fails it as ``kind="changed"`` (the step graph asks a model whether the new value
means the same); a value cut short by the field's length limit fails it as ``kind="too_long"``.
These results carry the facts (``ActionResult.field``); deciding what they mean is left to the
step graph and its models. Focus is given back to a text field afterwards, so a key press the
test sends next still reaches it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import os
import re
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Literal, Sequence
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import url2pathname

from playwright.async_api import Error as PlaywrightError

from testzeus_hercules.act.files import PathConfinementError, confine

if TYPE_CHECKING:
    from playwright.async_api import APIResponse, Locator, Page

DEFAULT_TIMEOUT_MS = 5_000
NAVIGATION_TIMEOUT_MS = 30_000
SETTLE_MS = 500
NAVIGATION_COMMIT_GRACE_S = 1.0
# After a click or key press, how long to watch for a form submission that page script starts a moment later.
NAVIGATION_START_GRACE_S = 0.3
# After an action, how long at most to wait for the page's own data requests (a long poll must not stall every step).
DATA_WAIT_CAP_S = 3.0
# After typing into a field that opened a suggestion list, how long to watch for the (debounced) request for its suggestions.
SUGGESTIONS_START_GRACE_S = 1.5
MAX_WAIT_S = 60.0
_POLL_S = 0.2
_POLL_MS = 1_000
_DETAIL_CLIP = 300
_WEB_SCHEMES = frozenset({"http", "https"})
_BLANK_PAGE = "about:blank"
# ``host:port[/path]`` without a scheme (``localhost:3000/login``, ``127.0.0.1:8080``, ``[::1]:8080``,
# ``shop.test:8443``): opened as http://. A bare ``name:123`` without a dot is not taken for a host.
_HOST_PORT = re.compile(r"^(?:localhost|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+):\d{1,5}(?:[/?#].*)?$", re.IGNORECASE)
_INTERRUPTED = "ERR_ABORTED"

# Visible text of the document plus the visible text inside every open shadow root
# (innerText skips shadow content), nested roots included, plus what the visible text fields hold: a typed value is on
# screen for a tester, though innerText leaves it out (passwords, hidden and file inputs, buttons and boxes excluded).
_TEXT_JS = """
() => {
  if (!document.body) return '';
  const parts = [document.body.innerText || ''];
  const shown = (el) => (typeof el.checkVisibility === 'function' ? el.checkVisibility() : true);
  const skipped = new Set(['hidden', 'password', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'image', 'range', 'color']);
  const values = (root) => {
    for (const el of root.querySelectorAll('input, textarea')) {
      if (el.tagName === 'INPUT' && skipped.has((el.type || 'text').toLowerCase())) continue;
      if (el.value && el.value.trim() && shown(el)) parts.push(el.value);
    }
  };
  values(document);
  const visit = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (!el.shadowRoot) continue;
      values(el.shadowRoot);
      for (const node of el.shadowRoot.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) {
          if (node.textContent.trim() && shown(el)) parts.push(node.textContent);
        } else if (node.nodeType === Node.ELEMENT_NODE && !['STYLE', 'SCRIPT', 'TEMPLATE'].includes(node.tagName) && shown(node)) {
          parts.push(node.innerText || '');
        }
      }
      visit(el.shadowRoot);
    }
  };
  visit(document);
  return parts.filter((part) => part && part.trim()).join('\\n');
}
"""
# A visible suggestion list on the page (ARIA listbox or option): typing may still load it. Open shadow roots are searched
# too, nested ones included: a component's list (a Salesforce lookup's, for one) lives inside its shadow root.
_SUGGESTIONS_JS = """
() => {
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (typeof el.checkVisibility !== 'function' || el.checkVisibility({ visibilityProperty: true }));
  };
  const visit = (root) => {
    for (const el of root.querySelectorAll('[role="listbox"], [role="option"]')) if (shown(el)) return true;
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot && visit(el.shadowRoot)) return true;
    return false;
  };
  return visit(document);
}
"""
# The text innerText gives each closed drop-down (all its options, though only the chosen one is shown).
_CLOSED_DROP_DOWNS_JS = """
() => Array.from(document.querySelectorAll('select'))
  .filter((select) => !select.multiple && select.size <= 1 && (select.innerText || '').trim())
  .map((select) => ({ text: select.innerText, shown: Array.from(select.selectedOptions).map((o) => o.label || o.text).join(' ') }))
"""
_VALUE_JS = "el => (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ? el.value : el.innerText)"
_OPTIONS_JS = "el => el.tagName === 'SELECT' ? Array.from(el.options).map(o => [o.label, o.value]) : null"
_INPUT_TYPE_JS = "el => (el.tagName === 'INPUT' ? (el.type || 'text').toLowerCase() : el.tagName.toLowerCase())"
# Inputs whose value cannot be typed key by key (the browser splits it into segments or ignores keys).
DIRECT_FILL_TYPES = frozenset({"date", "time", "datetime-local", "month", "week", "color", "range"})
_SECRET_TYPES = frozenset({"password"})


class InvalidURLError(ValueError):
    """The URL cannot be opened (unsupported scheme or a relative path without a base URL)."""


ResultKind = Literal["disabled", "not_editable", "value_mismatch", "invalid", "changed", "too_long", "option_missing", "file_missing", "too_many_files", "no_download", "not_a_file", "wrong_file"]


@dataclass(frozen=True)
class ActionResult:
    """What an action did. ``kind`` classifies known failures; ``retry`` is False when
    trying another element cannot help (the chosen element was right but did not take the value)."""

    ok: bool
    detail: str
    url_after: str = ""
    kind: ResultKind | None = None
    retry: bool = True
    # upload: ``files``, the real paths attached. download: ``file`` (where it was saved), ``name``, ``size``, ``type``,
    # ``sha256`` and ``via`` (download, tab or response).
    # select: how the option was found (``by``: label, value, option_role or text; ``option``: its text; for a custom
    # drop-down also ``role``, ``exact`` and ``typed`` when the value was typed into it first). For exporting the run.
    chosen: dict[str, Any] | None = None
    # The field as read after the action (and after it lost focus): set for fills and choices that were checked.
    field: FieldFacts | None = None


def resolve_url(url: str, base_url: str | None, *, file_roots: Iterable[Path | str] | None = None) -> str:
    """Absolute URL for ``url``: http(s), ``about:blank``, or a file inside the allowed folders.

    Relative paths are joined to ``base_url``; ``host:port[/path]`` without a scheme is opened as http. A file URL
    must point inside one of ``file_roots`` (the project folders; None means the working directory, the project a
    CLI run starts in) or inside the folder of a file ``base_url`` (the case's own local site); any other file is
    refused, so a step cannot open files elsewhere on the machine.
    """
    text = url.strip()
    if text.lower() == _BLANK_PAGE:
        return _BLANK_PAGE
    if _HOST_PORT.match(text):
        text = "http://" + text
    scheme = urlsplit(text).scheme.lower()
    if scheme in _WEB_SCHEMES:
        return text
    if scheme == "file":
        return _confined_file_url(text, base_url, file_roots)
    if scheme:
        raise InvalidURLError(f"cannot open a {scheme!r} URL; only http, https, about:blank and project file URLs are allowed")
    if not base_url:
        raise InvalidURLError(f"relative URL {url!r} needs the case's environment.base_url")
    joined = urljoin(base_url, text)
    return _confined_file_url(joined, base_url, file_roots) if urlsplit(joined).scheme.lower() == "file" else joined


def _file_path(url: str) -> Path:
    parts = urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        raise InvalidURLError("cannot open a file URL on another host")
    path = Path(url2pathname(parts.path))
    if not path.is_absolute():
        raise InvalidURLError("cannot open a file outside the project folder (or the folder of a file environment.base_url)")
    return path


def _confined_file_url(url: str, base_url: str | None, file_roots: Iterable[Path | str] | None) -> str:
    roots = [Path(root) for root in (file_roots if file_roots is not None else (os.getcwd(),))]
    if base_url and urlsplit(base_url).scheme.lower() == "file":
        base = _file_path(base_url)
        roots.append(base if urlsplit(base_url).path.endswith("/") or base.is_dir() else base.parent)
    if not roots:
        raise InvalidURLError("file URLs open only inside the project folder or the folder of a file environment.base_url")
    target = os.path.realpath(_file_path(url))
    for root in roots:
        real_root = os.path.realpath(root)
        if target == real_root or target.startswith(real_root.rstrip(os.sep) + os.sep):
            return url
    raise InvalidURLError("cannot open a file outside the project folder (or the folder of a file environment.base_url)")


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    line = text[0] if text else exc.__class__.__name__
    return line if len(line) <= _DETAIL_CLIP else line[: _DETAIL_CLIP - 1] + "…"


async def settle(page: Page, timeout_ms: int = SETTLE_MS) -> None:
    """Wait for network idle, at most ``timeout_ms``; never raises."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightError:
        return None


class _Navigations:
    """Watches a page's main frame: the document requests in flight and when a new document last committed.

    A click that submits a form starts a request that may take seconds (and a form handled by script
    may start it just after the click returns); the next step must not act, or open another URL
    (which would abort the submission), before the new document has arrived.
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self.pending: set[Any] = set()
        self.fetching: set[Any] = set()
        self.last_request = 0.0
        self.last_commit = 0.0
        self.last_data = 0.0

    def _main_frame_document(self, request: Any) -> bool:
        try:
            return bool(request.is_navigation_request()) and request.frame == self._page.main_frame
        except PlaywrightError:
            return False

    def _on_request(self, request: Any) -> None:
        if self._main_frame_document(request):
            self.pending.add(request)
            self.last_request = time.monotonic()
        elif self._same_site_data(request):
            self.fetching.add(request)
            self.last_data = time.monotonic()

    def _same_site_data(self, request: Any) -> bool:
        try:
            return request.resource_type in ("xhr", "fetch") and _origin(request.url) == _origin(self._page.url)
        except PlaywrightError:
            return False

    def _on_done(self, request: Any) -> None:
        self.pending.discard(request)
        self.fetching.discard(request)

    async def data_settled(self, cap_s: float = DATA_WAIT_CAP_S) -> None:
        """Wait (at most ``cap_s``) for the page's own data requests in flight (search results, suggestions)."""
        deadline = time.monotonic() + cap_s
        while self.fetching and time.monotonic() < deadline:
            await asyncio.sleep(_POLL_S / 4)

    async def data_since(self, since: float, *, grace_s: float, cap_s: float = DATA_WAIT_CAP_S) -> None:
        """Wait up to ``grace_s`` for a data request started after ``since`` (a debounced search), then for it to finish."""
        deadline = time.monotonic() + grace_s
        while self.last_data <= since and not self.fetching and time.monotonic() < deadline:
            await asyncio.sleep(_POLL_S / 4)
        await self.data_settled(cap_s)

    def _on_navigated(self, frame: Any) -> None:
        if frame == self._page.main_frame:
            self.last_commit = time.monotonic()

    def _listeners(self) -> tuple[tuple[str, Callable[[Any], None]], ...]:
        return (("request", self._on_request), ("requestfinished", self._on_done), ("requestfailed", self._on_done), ("framenavigated", self._on_navigated))

    def attach(self) -> _Navigations:
        for event, listener in self._listeners():
            self._page.on(event, listener)
        return self

    def detach(self) -> None:
        for event, listener in self._listeners():
            try:
                self._page.remove_listener(event, listener)
            except (PlaywrightError, ValueError, KeyError):
                continue

    def started_since(self, since: float) -> bool:
        return bool(self.pending) or self.last_request > since

    async def wait(self, since: float, *, grace_s: float = 0.0, timeout_ms: int = NAVIGATION_TIMEOUT_MS) -> None:
        """When a navigation started after ``since`` (or within ``grace_s`` from now), wait until its document has loaded."""
        grace_end = time.monotonic() + grace_s
        while not self.started_since(since) and time.monotonic() < grace_end:
            await asyncio.sleep(_POLL_S / 4)
        if not self.started_since(since):
            return
        deadline = time.monotonic() + timeout_ms / 1000
        idle_since: float | None = None
        while time.monotonic() < deadline:
            if self.pending:
                idle_since = None
            elif self.last_commit >= self.last_request:
                break
            else:  # the request ended without a new document (a download, 204): give it a moment to commit
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= NAVIGATION_COMMIT_GRACE_S:
                    return
            await asyncio.sleep(_POLL_S / 4)
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            await self._page.wait_for_load_state("load", timeout=max(remaining, 1))
        except PlaywrightError:
            return
        await settle(self._page)


# Per watched page: its navigation tracker and the messages of the JavaScript dialogs the latest action caused.
_TRACKERS: weakref.WeakKeyDictionary[Any, _Navigations] = weakref.WeakKeyDictionary()
_DIALOGS: weakref.WeakKeyDictionary[Any, list[str]] = weakref.WeakKeyDictionary()
_DIALOG_CLIP = 200


def _lookup(table: weakref.WeakKeyDictionary[Any, Any], page: Any) -> Any:
    try:
        return table.get(page)
    except TypeError:  # an object that cannot be weakly referenced is never watched
        return None


def watch_page(page: Page) -> None:
    """Track the page's navigations for the whole run, and answer its JavaScript dialogs.

    With the tracker, an action or URL opened after a form submission waits for the submission's page,
    even when the page script starts it a moment after the click. An alert is accepted; a confirmation,
    prompt or leave-page question is declined (the cautious answer). The messages of the latest action's
    dialogs are added to its result and to :func:`page_text`, so a step can check what a dialog said.
    """
    _TRACKERS[page] = _Navigations(page).attach()
    messages: list[str] = []
    _DIALOGS[page] = messages

    async def answer(dialog: Any) -> None:
        messages.append(" ".join(str(dialog.message).split())[:_DIALOG_CLIP])
        try:
            await (dialog.accept() if dialog.type == "alert" else dialog.dismiss())
        except PlaywrightError:
            return

    page.on("dialog", answer)


def _dialog_messages(page: Any) -> list[str]:
    return _lookup(_DIALOGS, page) or []


async def settle_navigation(page: Page) -> None:
    """Wait for a navigation still in flight on a watched page (a form submitted by an earlier step)."""
    tracker = _lookup(_TRACKERS, page)
    if tracker is not None and tracker.pending:
        await tracker.wait(since=0.0)


async def settle_page(page: Page, *, data_cap_s: float = DATA_WAIT_CAP_S) -> None:
    """Before the page is read: wait for a navigation still in flight (a form an earlier step submitted), the page's own
    data requests (at most ``data_cap_s``) and network idle (at most :data:`SETTLE_MS`). Never raises.

    Perception then waits for the rendering to settle (DOM, animations, loading indicators; see the perceive package).
    """
    tracker = _lookup(_TRACKERS, page)
    if tracker is not None:
        if tracker.pending:
            await tracker.wait(since=0.0)
        await tracker.data_settled(data_cap_s)
    await settle(page)


def data_pending(page: Page) -> bool:
    """True when a watched page has a navigation or one of its own data requests in flight (results still on their way)."""
    tracker = _lookup(_TRACKERS, page)
    return bool(tracker is not None and (tracker.pending or tracker.fetching))


async def _perform(page: Page, what: str, operation: Callable[[], Awaitable[Any]], *, navigation_grace_s: float = 0.0) -> ActionResult:
    """Run ``operation``, then wait for the page to settle and for any navigation it started.

    ``navigation_grace_s``: how long to watch for a navigation that page script starts just after the
    operation returned (clicks and key presses that may submit a form).
    """
    _dialog_messages(page).clear()
    tracker = _lookup(_TRACKERS, page)
    temporary = tracker is None
    if tracker is None:
        tracker = _Navigations(page).attach()
    started = time.monotonic()
    try:
        try:
            await operation()
        except PlaywrightError as exc:
            return ActionResult(ok=False, detail=f"{what} failed: {_first_line(exc)}", url_after=page.url)
        await settle(page)
        await tracker.wait(since=started, grace_s=navigation_grace_s)
        await tracker.data_settled()
    finally:
        if temporary:
            tracker.detach()
    shown = _dialog_messages(page)
    detail = what + "".join(f"; the page showed a dialog: {message!r}" for message in shown)
    return ActionResult(ok=True, detail=detail, url_after=page.url)


async def _blocked(locator: Locator, what: str, timeout_ms: int, *, editable: bool = False) -> ActionResult | None:
    """A failed result when the element is missing, disabled or (for typing) read-only."""
    page = locator.page
    try:
        if await locator.is_disabled(timeout=timeout_ms):
            return ActionResult(ok=False, detail=f"{what} failed: the element is disabled", url_after=page.url, kind="disabled")
        if editable and not await locator.is_editable(timeout=timeout_ms):
            return ActionResult(ok=False, detail=f"{what} failed: the element is not editable (read-only)", url_after=page.url, kind="not_editable")
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"{what} failed: {_first_line(exc)}", url_after=page.url)
    return None


async def open_url(page: Page, url: str, *, base_url: str | None = None, timeout_ms: int = NAVIGATION_TIMEOUT_MS, file_roots: Iterable[Path | str] | None = None) -> ActionResult:
    try:
        target = resolve_url(url, base_url, file_roots=file_roots)
    except InvalidURLError as exc:
        return ActionResult(ok=False, detail=str(exc), url_after=page.url)
    await settle_navigation(page)  # opening a URL now would abort a submission still on its way
    result = await _perform(page, f"opened {target}", lambda: page.goto(target, wait_until="load", timeout=timeout_ms))
    if not result.ok and _INTERRUPTED in result.detail:
        # Another navigation (a form the previous step submitted, a redirect) was still in flight: let it finish, then retry once.
        try:
            await page.wait_for_load_state("load", timeout=timeout_ms)
        except PlaywrightError:
            pass
        result = await _perform(page, f"opened {target}", lambda: page.goto(target, wait_until="load", timeout=timeout_ms))
    return result


_OFF_THE_PAGE_JS = """
el => {
  const box = el.getBoundingClientRect();
  if (!box.width && !box.height) return false;
  let top = box.top;
  let left = box.left;
  for (let node = el; node && getComputedStyle(node).position !== 'fixed'; ) {
    const parent = node.assignedSlot || node.parentElement || (node.parentNode instanceof ShadowRoot ? node.parentNode.host : null);
    if (parent) {
      top += Math.max(0, parent.scrollTop);
      left += Math.max(0, parent.scrollLeft);
    }
    node = parent;
  }
  return top + box.height <= 0 || left + box.width <= 0;
}
"""


async def _off_the_page(locator: Locator, timeout_ms: int) -> bool:
    """True when the element lies wholly above or left of the page, where no scrolling reaches it (a skip link that
    appears only when it has keyboard focus)."""
    try:
        return bool(await locator.evaluate(_OFF_THE_PAGE_JS, timeout=timeout_ms))
    except PlaywrightError:
        return False


async def _press_enter(locator: Locator) -> None:
    await locator.focus()
    await locator.page.keyboard.press("Enter")


async def click(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    """Click the element; one placed off the page until it has focus (a skip link) is activated with the keyboard instead,
    as a person would reach it."""
    blocked = await _blocked(locator, "click", timeout_ms)
    if blocked is not None:
        return blocked
    if await _off_the_page(locator, timeout_ms):
        return await _perform(locator.page, "activated with the keyboard (the element is off the page until it has focus)", lambda: _press_enter(locator), navigation_grace_s=NAVIGATION_START_GRACE_S)
    return await _perform(locator.page, "clicked", lambda: locator.click(timeout=timeout_ms, no_wait_after=True), navigation_grace_s=NAVIGATION_START_GRACE_S)


async def fill(locator: Locator, value: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS, verify: bool = True) -> ActionResult:
    """Set the field to ``value`` and check that it holds it, also once it has lost focus.

    Text fields are cleared and typed key by key (so input handlers fire). Date, time,
    month, week, colour and range inputs get the value directly (typing would be split
    across the browser's segments). Afterwards the field is read back; a field that does
    not hold the value (see :func:`same_value`) fails the action without retry, as
    ``too_long`` when its length limit cut the value short. With ``verify`` the field is
    then left (see :func:`_left`) and read again: invalid or changed fails the action
    (``kind`` ``invalid`` or ``changed``, with the field's facts).
    """
    blocked = await _blocked(locator, "fill", timeout_ms, editable=True)
    if blocked is not None:
        return blocked
    page = locator.page
    try:
        input_type = str(await locator.evaluate(_INPUT_TYPE_JS, timeout=timeout_ms))
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"fill failed: {_first_line(exc)}", url_after=page.url)
    before = await _read_field(locator, timeout_ms=timeout_ms) if verify else None

    async def type_value() -> None:
        if input_type in DIRECT_FILL_TYPES:
            await locator.fill(value, timeout=timeout_ms)
            return
        await locator.clear(timeout=timeout_ms)
        await locator.press_sequentially(value, timeout=timeout_ms)

    started = time.monotonic()
    result = await _perform(page, f"filled {len(value)} characters", type_value)
    if not result.ok:
        return result
    await _suggestions_settled(page, started)
    held = await read_value(locator, timeout_ms=timeout_ms)
    if held is not None and not same_value(value, held) and input_type not in DIRECT_FILL_TYPES:
        # Some fields swallow typed keys (key handlers, masks); set the value directly, as a paste would.
        retried = await _perform(page, f"filled {len(value)} characters", lambda: locator.fill(value, timeout=timeout_ms))
        held = await read_value(locator, timeout_ms=timeout_ms) if retried.ok else held
        result = retried if retried.ok else result
    if held is not None and not same_value(value, held):
        too_long = _too_long(value, held, before, input_type, page.url)
        if too_long is not None:
            return too_long
        detail = f"fill failed: the field holds {_shown_value(held)} after filling, not the requested value ({len(value)} characters)"
        return ActionResult(ok=False, detail=detail, url_after=page.url, kind="value_mismatch", retry=False, field=_public(before, input_type))
    if not verify:
        return result
    return _checked(result, value, await _left(locator, before, timeout_ms), input_type)


async def _suggestions_settled(page: Page, since: float) -> None:
    """When typing opened a suggestion list (autocomplete), wait for the request that fills it, which a page often sends
    a moment after the last key (debounced); bounded. Generic: an ARIA listbox or option on show, and the page's own requests."""
    tracker = _lookup(_TRACKERS, page)
    if tracker is None:
        return
    try:
        shown = bool(await page.evaluate(_SUGGESTIONS_JS))
    except PlaywrightError:
        return
    if shown:
        await tracker.data_since(since, grace_s=SUGGESTIONS_START_GRACE_S)


def _alphanumeric(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def same_value(wanted: str, held: str) -> bool:
    """True when a field holding ``held`` holds ``wanted``.

    Tolerates case, surrounding spaces, the formatting an input mask adds (spaces,
    dashes, brackets) and equal numbers written differently ("1.50" and "1.5").
    """
    if wanted == held or wanted.strip().casefold() == held.strip().casefold():
        return True
    if _alphanumeric(wanted) and _alphanumeric(wanted) == _alphanumeric(held):
        return True
    try:
        return float(wanted) == float(held)
    except ValueError:
        return False


# --------------------------------------------------------------------------- the field after an action

# What a field holds and how the page marks it, read on the element (its own facts, no judgement):
# - value: what it holds (a field's value, else its text); focused: whether it has focus (through shadow roots);
# - invalid: aria-invalid="true" on the field or on an ancestor inside its own container, or the browser's own validity
#   check failing (only when the field's form does not opt out of it with novalidate);
# - error: the text of its aria-errormessage targets, or (when invalid) of its aria-describedby targets; hint: the text
#   of its aria-describedby targets; native: the browser's validation message;
# - hints: its format attributes (placeholder, pattern, title, maxlength, minlength, min, max, type, inputmode);
# - texts: the visible text pieces of its own container (the highest ancestor holding no other form field: its label,
#   help and error lines), to see what appeared after an action; popup: it lists suggestions (combobox, autocomplete,
#   datalist), so leaving it would close the list the test picks from next.
_FIELD_FACTS_JS = r"""
(el) => {
  const clean = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const parentOf = (n) => n.parentElement || (n.parentNode && n.parentNode.host) || null;
  const shown = (n) => {
    try {
      const r = n.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (typeof n.checkVisibility !== 'function' || n.checkVisibility({ visibilityProperty: true }));
    } catch (e) { return false; }
  };
  const FIELD = 'input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=image]), select, textarea, '
    + '[contenteditable=""], [contenteditable=true], [role=textbox], [role=combobox], [role=searchbox], [role=spinbutton], [role=listbox], [role=slider]';
  const lineage = new Set();
  for (let n = el; n; n = parentOf(n)) lineage.add(n);
  // Another form field inside `node` (open shadow roots included), other than this field and its own hosts/wrappers.
  const otherField = (node) => {
    let seen = 0;
    const visit = (root) => {
      const all = root.querySelectorAll('*');
      for (let i = 0; i < all.length; i += 1) {
        const n = all[i];
        if (++seen > 4000) return true;
        if (!lineage.has(n) && !el.contains(n) && n.matches(FIELD)) return true;
        if (n.shadowRoot && visit(n.shadowRoot)) return true;
      }
      return false;
    };
    return visit(node) || (node.shadowRoot ? visit(node.shadowRoot) : false);
  };
  let container = el;
  for (let n = parentOf(el), depth = 0; n && depth < 8; n = parentOf(n), depth += 1) {
    if (n === document.body || n === document.documentElement || n.localName === 'form' || n.localName === 'fieldset') break;
    if (otherField(n)) break;
    container = n;
  }
  const texts = [];
  const visitText = (node, depth) => {
    if (texts.length >= 30 || depth > 40) return;
    const kids = node.shadowRoot ? node.shadowRoot.childNodes : node.childNodes;
    for (let i = 0; i < kids.length; i += 1) {
      const k = kids[i];
      if (k.nodeType === 3) {
        const t = clean(k.textContent);
        if (t && !texts.includes(clip(t, 200))) texts.push(clip(t, 200));
      } else if (k.nodeType === 1 && !['STYLE', 'SCRIPT', 'TEMPLATE', 'OPTION', 'DATALIST'].includes(k.tagName) && shown(k)) {
        visitText(k, depth + 1);
      } else if (k.nodeType === 1 && k.tagName === 'SLOT') {
        const assigned = k.assignedNodes ? k.assignedNodes() : [];
        for (let j = 0; j < assigned.length; j += 1) if (assigned[j].nodeType === 1 && shown(assigned[j])) visitText(assigned[j], depth + 1);
      }
    }
  };
  if (container !== el) visitText(container, 0);
  const root = el.getRootNode();
  const byId = (id) => (root && root.getElementById ? root.getElementById(id) : null) || document.getElementById(id);
  const refText = (name) => {
    const parts = [];
    for (const id of clean(el.getAttribute(name)).split(' ')) {
      if (!id) continue;
      const ref = byId(id);
      if (ref && shown(ref)) { const t = clean(ref.innerText || ref.textContent); if (t) parts.push(t); }
    }
    return clip(parts.join(' '), 300);
  };
  let ariaInvalid = false;
  for (let n = el; n; n = parentOf(n)) {
    if (n.getAttribute && n.getAttribute('aria-invalid') === 'true') { ariaInvalid = true; break; }
    if (n === container) break;
  }
  const form = el.form || null;
  const native = !!(el.validity && el.willValidate && !(form && form.noValidate));
  const nativeInvalid = native && !el.validity.valid;
  const describedby = refText('aria-describedby');
  const errormessage = refText('aria-errormessage');
  const invalid = ariaInvalid || nativeInvalid;
  const attr = (name) => clean(el.getAttribute(name));
  const hints = {};
  for (const name of ['placeholder', 'pattern', 'title', 'min', 'max', 'inputmode']) { const v = attr(name); if (v) hints[name] = clip(v, 120); }
  if (el.maxLength > 0) hints.maxlength = String(el.maxLength);
  if (el.minLength > 0) hints.minlength = String(el.minLength);
  if (el.localName === 'input') hints.type = (el.type || 'text').toLowerCase();
  let active = document.activeElement;
  while (active && active.shadowRoot && active.shadowRoot.activeElement) active = active.shadowRoot.activeElement;
  const role = attr('role');
  const value = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ? el.value : clean(el.innerText);
  // A field that likely holds a secret: a password input, a credential or card autocomplete, or a secret-like name.
  const named = [el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('autocomplete')].map(clean).join(' ').toLowerCase();
  const secret = (el.localName === 'input' && (el.type || '').toLowerCase() === 'password')
    || /(one-time-code|current-password|new-password|cc-number|cc-csc)/.test(named)
    || /(passw|secret|token|otp|one.?time|api.?key|cvv|cvc|ssn|(^|[^a-z])pin([^a-z]|$))/.test(named);
  return {
    value: value == null ? null : String(value),
    secret,
    focused: active === el,
    invalid,
    error: errormessage || (invalid ? describedby : ''),
    hint: invalid && !errormessage ? '' : describedby,
    native: nativeInvalid ? clip(clean(el.validationMessage), 200) : '',
    hints,
    texts,
    maxlength: el.maxLength > 0 ? el.maxLength : null,
    popup: role === 'combobox' || !!el.getAttribute('aria-autocomplete') || !!el.getAttribute('list'),
  };
}
"""
_BLUR_JS = """
(el) => new Promise((done) => {
  el.blur();
  requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
})
"""
_FOCUS_JS = "el => el.focus({ preventScroll: true })"
# After a field loses focus: how long to watch for a request its validation starts, and how long that request may take.
BLUR_REQUEST_GRACE_S = 0.15
BLUR_SETTLE_CAP_S = 1.3
_ERROR_CLIP = 300


@dataclass(frozen=True)
class FieldFacts:
    """A field as read on the page: what it holds, whether the page (or the browser) marks it invalid, the error
    text tied to it and its format hints. ``appeared``: text that appeared in the field's own container since
    before the action (its error line, when the page writes one there)."""

    value: str | None
    invalid: bool = False
    error: str = ""
    hint: str = ""
    native: str = ""
    hints: dict[str, str] = dataclasses.field(default_factory=dict)
    texts: tuple[str, ...] = ()
    appeared: tuple[str, ...] = ()
    maxlength: int | None = None
    popup: bool = False
    focused: bool = False
    blurred: bool = False
    # The field likely holds a secret (password input, credential or card autocomplete, secret-like name): its value
    # is used for comparisons inside this module only and is never put into an action result.
    secret: bool = False

    @property
    def error_text(self) -> str:
        """The field's error as the page states it: its ARIA error, else (when invalid) text that appeared next to it,
        else the browser's validation message; "" when there is none."""
        if self.error:
            return self.error
        if self.invalid and self.appeared:
            return " ".join(self.appeared)[:_ERROR_CLIP]
        return self.native

    def hint_lines(self) -> list[str]:
        """The format hints as ``name: value`` lines (for a model)."""
        lines = [f"{name}: {value}" for name, value in self.hints.items()]
        if self.hint:
            lines.append(f"help text: {self.hint}")
        return lines


async def field_facts(locator: Locator, *, since: FieldFacts | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> FieldFacts | None:
    """The field's facts now; ``since`` (read before an action) gives ``appeared``. None when it cannot be read.
    The value of a field that likely holds a secret is left out (``value`` None, ``secret`` True)."""
    return _public(await _read_field(locator, since=since, timeout_ms=timeout_ms))


async def _read_field(locator: Locator, *, since: FieldFacts | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> FieldFacts | None:
    """:func:`field_facts` with the raw value, for comparisons inside this module only."""
    try:
        raw = await locator.evaluate(_FIELD_FACTS_JS, timeout=timeout_ms)
    except PlaywrightError:
        return None
    if not isinstance(raw, dict):
        return None
    texts = tuple(str(text) for text in raw.get("texts") or ())
    before = set(since.texts) if since is not None else set(texts)
    return FieldFacts(
        value=None if raw.get("value") is None else str(raw.get("value")),
        invalid=bool(raw.get("invalid")),
        error=str(raw.get("error") or ""),
        hint=str(raw.get("hint") or ""),
        native=str(raw.get("native") or ""),
        hints={str(key): str(value) for key, value in (raw.get("hints") or {}).items()},
        texts=texts,
        appeared=tuple(text for text in texts if text not in before),
        maxlength=int(raw["maxlength"]) if raw.get("maxlength") else None,
        popup=bool(raw.get("popup")),
        focused=bool(raw.get("focused")),
        secret=bool(raw.get("secret")),
    )


async def _left(locator: Locator, since: FieldFacts | None, timeout_ms: int) -> FieldFacts | None:
    """Move focus away from the field (as leaving it does), let the page settle briefly (a validation request it starts
    included, bounded), and read the field again. Focus is given back when the field had it, so a key the test presses
    next still reaches it. A field listing suggestions is not left (that would close the list a test picks from next)."""
    page = locator.page
    current = await _read_field(locator, since=since, timeout_ms=timeout_ms)
    if current is None or current.popup or not current.focused:
        return current
    try:
        shown = bool(await page.evaluate(_SUGGESTIONS_JS))
    except PlaywrightError:
        shown = False
    if shown:
        return current
    started = time.monotonic()
    try:
        await locator.evaluate(_BLUR_JS, timeout=timeout_ms)
    except PlaywrightError:
        return current
    tracker = _lookup(_TRACKERS, page)
    if tracker is not None:
        await tracker.data_since(started, grace_s=BLUR_REQUEST_GRACE_S, cap_s=BLUR_SETTLE_CAP_S)
    else:
        await asyncio.sleep(BLUR_REQUEST_GRACE_S)
    after = await _read_field(locator, since=since, timeout_ms=timeout_ms)
    try:
        await locator.evaluate(_FOCUS_JS, timeout=timeout_ms)
    except PlaywrightError:
        pass
    return None if after is None else dataclasses.replace(after, blurred=True)


def _shown_value(value: str | None) -> str:
    """How a field's content appears in a result: its length only (typed values may be secrets)."""
    return "nothing readable" if value is None else f"{len(value)} characters"


def _public(facts: FieldFacts | None, input_type: str = "") -> FieldFacts | None:
    """``facts`` as they may appear in a result: the value of a secret field (or a password input) is dropped."""
    if facts is None or not (facts.secret or input_type in _SECRET_TYPES):
        return facts
    return dataclasses.replace(facts, value=None, secret=True)


def _too_long(value: str, held: str | None, facts: FieldFacts | None, input_type: str, url: str) -> ActionResult | None:
    """A failed result when the field's length limit (its maxlength, or a field that keeps only the start of the value)
    cut the value short; None otherwise."""
    limit = facts.maxlength if facts is not None else None
    truncated = held is not None and 0 < len(held) < len(value) and value.casefold().startswith(held.casefold())
    if not (limit is not None and len(value) > limit) and not truncated:
        return None
    accepts = limit if limit is not None and len(value) > limit else len(held or "")
    detail = f"fill failed: the field accepts at most {accepts} characters; the step's value has {len(value)}"
    return ActionResult(ok=False, detail=detail, url_after=url, kind="too_long", retry=False, field=_public(facts, input_type))


def _checked(result: ActionResult, value: str | None, facts: FieldFacts | None, input_type: str) -> ActionResult:
    """``result`` judged by the field as read after it lost focus: invalid, changed, or as it was."""
    if facts is None:
        return result
    if facts.invalid:
        error = facts.error_text
        detail = "the field marked the value invalid after it lost focus" + (f": {error}" if error else " (it shows no error text)")
        return ActionResult(ok=False, detail=detail, url_after=result.url_after, kind="invalid", retry=False, chosen=result.chosen, field=_public(facts, input_type))
    if value is not None and facts.value is not None and not same_value(value, facts.value):
        detail = f"the field holds {_shown_value(facts.value)} after it lost focus, not the requested value ({len(value)} characters)"
        return ActionResult(ok=False, detail=detail, url_after=result.url_after, kind="changed", retry=False, chosen=result.chosen, field=_public(facts, input_type))
    return dataclasses.replace(result, field=_public(facts, input_type))


async def select(locator: Locator, value: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS, verify: bool = True) -> ActionResult:
    """Select by visible label, then by value; custom drop-downs are clicked open first. With ``verify`` the field is
    read once it has lost focus, and a field the page then marks invalid fails the action (``kind="invalid"``).
    An option that is not there fails as ``option_missing``."""
    blocked = await _blocked(locator, "select", timeout_ms)
    if blocked is not None:
        return blocked
    page = locator.page
    try:
        options = await locator.evaluate(_OPTIONS_JS, timeout=timeout_ms)
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"select failed: {_first_line(exc)}", url_after=page.url)
    before = await _read_field(locator, timeout_ms=timeout_ms) if verify else None
    if options is None:
        chosen: dict[str, Any] = {}
        result = await _perform(page, f"selected {value!r}", lambda: _select_custom(page, locator, value, timeout_ms, chosen))
        if not result.ok:
            return dataclasses.replace(result, kind="option_missing", field=_public(before)) if "not found after opening" in result.detail else result
        return await _choice_checked(dataclasses.replace(result, chosen=chosen or None), locator, before, verify, timeout_ms)
    real = [label for label, option_value in options if str(label).strip() or str(option_value).strip()]
    picked = _pick_option(options, value)
    if picked is None:
        labels = ", ".join(label for label, _ in options[:20])
        listed = f"options: {labels}" if real else "the drop-down has no options"
        return ActionResult(ok=False, detail=f"select failed: option {value!r} not found ({listed})", url_after=page.url, kind="option_missing", field=_public(before))
    choice, by, option = picked
    result = await _perform(page, f"selected {value!r}", lambda: locator.select_option(value=choice, timeout=timeout_ms))
    if not result.ok:
        return result
    return await _choice_checked(dataclasses.replace(result, chosen={"by": by, "option": option}), locator, before, verify, timeout_ms)


async def _choice_checked(result: ActionResult, locator: Locator, before: FieldFacts | None, verify: bool, timeout_ms: int) -> ActionResult:
    """A choice checked like a fill: the field is read once it has lost focus; invalid fails the action."""
    if not verify:
        return result
    facts = await _left(locator, before, timeout_ms)
    if facts is not None and facts.invalid:
        return _checked(result, None, facts, "select")
    return dataclasses.replace(result, field=_public(facts)) if facts is not None else result


def _pick_option(options: list[list[str]], wanted: str) -> tuple[str, str, str] | None:
    """The option's value, and how it matched: by its exact label, its value, or its label in any case (with that label)."""
    for by, matches in (
        ("label", lambda label, _value: label == wanted),
        ("value", lambda _label, value: value == wanted),
        ("label_any_case", lambda label, _value: label.strip().lower() == wanted.strip().lower()),
    ):
        for label, value in options:
            if matches(label, value):
                return (value, "value", value) if by == "value" else (value, "label", wanted if by == "label" else label)
    return None


_EDITABLE_JS = "el => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable"
_OPTION_ROLES = ("option", "menuitem", "menuitemradio", "treeitem")
_MAX_TEXT_MATCHES = 20


async def _visible_handles(locator: Locator, created: list[Any]) -> list[Any]:
    """Element handles of the visible matches of ``locator`` (at most :data:`_MAX_TEXT_MATCHES`). Every handle is also
    added to ``created``, which the caller disposes of when done (see :func:`_dispose`)."""
    handles = []
    count = min(await locator.count(), _MAX_TEXT_MATCHES)
    for index in range(count):
        item = locator.nth(index)
        try:
            if await item.is_visible():
                handle = await item.element_handle(timeout=_POLL_MS)
                if handle is not None:
                    created.append(handle)
                    handles.append(handle)
        except PlaywrightError:
            continue
    return handles


async def _dispose(handles: list[Any]) -> None:
    """Release element handles in the page (a long run would otherwise keep every one alive)."""
    for handle in handles:
        try:
            await handle.dispose()
        except PlaywrightError:
            pass
    handles.clear()


async def _new_text_match(page: Page, value: str, before: list[Any], created: list[Any]) -> Any | None:
    """A visible element showing exactly ``value`` that was not visible before the drop-down opened."""
    for handle in await _visible_handles(page.get_by_text(value, exact=True), created):
        if not any([await page.evaluate("([a, b]) => a === b", [handle, old]) for old in before]):
            return handle
    return None


async def _option_after_opening(page: Page, value: str, before: list[Any], timeout_ms: int, chosen: dict[str, Any], created: list[Any]) -> Any | None:
    """The option to click once a custom drop-down is open: an exact option-role match, then a looser
    one, then an element with exactly this text that the opening revealed (a plain list item).
    ``chosen`` is told which of these found it. Handles of earlier polls are disposed of as it goes."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        polled: list[Any] = []
        for exact in (True, False):
            for role in _OPTION_ROLES:
                found = await _visible_handles(page.get_by_role(role, name=value, exact=exact), polled)  # type: ignore[arg-type]
                if found:
                    chosen.update(by="option_role", role=role, exact=exact)
                    created.extend(polled)
                    return found[0] if exact else await _closest_option(found, value)
        revealed = await _new_text_match(page, value, before, polled)
        if revealed is not None:
            chosen.update(by="text")
        if revealed is not None or time.monotonic() >= deadline:
            created.extend(polled)
            return revealed
        await _dispose(polled)
        await asyncio.sleep(_POLL_S)


async def _closest_option(handles: list[Any], value: str) -> Any:
    """Of options whose names contain ``value``, the one it begins (a lookup's record "Acme Corp NewYork" before its
    "Search: show more results for "Acme"" entry), then the shortest name; page order breaks ties."""
    wanted = " ".join(value.split()).casefold()
    ranked = []
    for order, handle in enumerate(handles):
        try:
            name = " ".join(str(await handle.evaluate("el => el.innerText || el.textContent || ''")).split()).casefold()
        except PlaywrightError:
            name = ""
        ranked.append((not name.startswith(wanted), len(name) or 10_000, order, handle))
    return min(ranked, key=lambda item: item[:3])[3]


async def _select_custom(page: Page, locator: Locator, value: str, timeout_ms: int, chosen: dict[str, Any]) -> None:
    """Open a custom drop-down (or type into a type-ahead box) and click the matching option."""
    created: list[Any] = []
    try:
        before = await _visible_handles(page.get_by_text(value, exact=True), created)
        typed = bool(await locator.evaluate(_EDITABLE_JS, timeout=timeout_ms))
        await locator.click(timeout=timeout_ms, no_wait_after=True)
        if typed:
            await locator.press_sequentially(value, timeout=timeout_ms)
        option = await _option_after_opening(page, value, before, timeout_ms, chosen, created)
        if option is None:
            raise _OptionMissing(f"option {value!r} not found after opening the drop-down")
        chosen.update(option=value, typed=typed)
        await option.click(timeout=timeout_ms, no_wait_after=True)
    finally:
        await _dispose(created)


class _OptionMissing(PlaywrightError):
    """No option with the requested label appeared in a custom drop-down."""


async def select_options(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> list[str] | None:
    """The option labels of a native drop-down, or None for any other element."""
    try:
        options = await locator.evaluate(_OPTIONS_JS, timeout=timeout_ms)
    except PlaywrightError:
        return None
    return None if options is None else [str(label) for label, _ in options]


async def check(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    blocked = await _blocked(locator, "check", timeout_ms)
    return blocked or await _perform(locator.page, "checked", lambda: locator.check(timeout=timeout_ms))


async def uncheck(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    blocked = await _blocked(locator, "uncheck", timeout_ms)
    return blocked or await _perform(locator.page, "unchecked", lambda: locator.uncheck(timeout=timeout_ms))


_FILE_INPUT_JS = """el => {
  const input = el instanceof HTMLLabelElement ? el.control : el;
  return input instanceof HTMLInputElement && input.type === 'file' ? { multiple: input.multiple } : null;
}"""
_FILE_NAMES_JS = """el => {
  const input = el instanceof HTMLLabelElement ? el.control : el;
  return input && input.files ? Array.from(input.files, (file) => file.name) : null;
}"""


def _named(paths: Sequence[Path]) -> str:
    return ", ".join(f"'{path.name}'" for path in paths)


async def upload(locator: Locator, names: Sequence[str], *, roots: Iterable[Path], timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    """Attach the files ``names`` (each found inside ``roots``, see :func:`confine`) to a file input.

    A file input, or a label of one, gets the files directly; any other element is clicked and the
    file picker it opens gets them. A file that cannot be found, or several files for a field that
    takes one, fails without retry (another element cannot help); a click that opens no picker fails
    with retry. Afterwards a file input is read back: it must hold every file.
    """
    page = locator.page
    if not names:
        return ActionResult(ok=False, detail="upload failed: the step names no file", url_after=page.url, kind="file_missing", retry=False)
    try:
        folders = list(roots)
        where = "the case's files, the environment's files and the project folder" if len(folders) > 1 else "the project folder"
        paths = [confine(name, folders, where=where) for name in names]
    except PathConfinementError as exc:
        return ActionResult(ok=False, detail=f"upload failed: {exc}", url_after=page.url, kind="file_missing", retry=False)
    blocked = await _blocked(locator, "upload", timeout_ms)
    if blocked is not None:
        return blocked
    try:
        field = await locator.evaluate(_FILE_INPUT_JS, timeout=timeout_ms)
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"upload failed: {_first_line(exc)}", url_after=page.url)
    one_file = f"upload failed: the field takes one file, and the step names {len(paths)}"
    if field is not None:
        if len(paths) > 1 and not field.get("multiple"):
            return ActionResult(ok=False, detail=one_file, url_after=page.url, kind="too_many_files", retry=False)
        result = await _perform(page, f"attached {_named(paths)}", lambda: locator.set_input_files(paths, timeout=timeout_ms))
        if not result.ok:
            return result
        try:
            held = await locator.evaluate(_FILE_NAMES_JS, timeout=timeout_ms)
        except PlaywrightError:
            return result
        missing = [path.name for path in paths if path.name not in (held or [])]
        if missing:
            return ActionResult(ok=False, detail=f"upload failed: the field does not hold {', '.join(missing)} after attaching", url_after=page.url, kind="value_mismatch", retry=False)
        return dataclasses.replace(result, chosen={"files": [str(path) for path in paths]})
    picker: dict[str, bool] = {}

    async def choose() -> None:
        async with page.expect_file_chooser(timeout=timeout_ms) as opened:
            await locator.click(timeout=timeout_ms)
        chooser = await opened.value
        picker["opened"] = True
        if len(paths) > 1 and not chooser.is_multiple():
            picker["single"] = True
            return
        await chooser.set_files(paths, timeout=timeout_ms)

    result = await _perform(page, f"attached {_named(paths)} through the file picker", choose)
    if picker.get("single"):
        return ActionResult(ok=False, detail=one_file, url_after=page.url, kind="too_many_files", retry=False)
    if not result.ok and not picker.get("opened"):
        return ActionResult(ok=False, detail="upload failed: the element is not a file input and clicking it opened no file picker", url_after=page.url)
    return dataclasses.replace(result, chosen={"files": [str(path) for path in paths]}) if result.ok else result


DOWNLOAD_TIMEOUT_MS = 30_000
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_PAGE_TYPES = ("text/html", "application/xhtml+xml")
_DISPOSITION_STAR = re.compile(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", re.IGNORECASE)
_DISPOSITION = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


def download_name(name: str) -> str:
    """A downloaded file's name made safe for the evidence folder: letters, digits, '.', '_' and '-'."""
    return _UNSAFE_NAME.sub("_", name.strip()).strip("._-")[:120] or "download"


def _unique(folder: Path, name: str) -> Path:
    candidate = folder / name
    number = 2
    while candidate.exists():
        candidate = folder / f"{Path(name).stem}_{number}{Path(name).suffix}"
        number += 1
    return candidate


def _response_name(url: str, headers: dict[str, str]) -> str:
    disposition = headers.get("content-disposition", "")
    found = _DISPOSITION_STAR.search(disposition) or _DISPOSITION.search(disposition)
    if found:
        return unquote(found.group(1).strip())
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "download"


def _is_page(headers: dict[str, str]) -> bool:
    kind = headers.get("content-type", "").split(";")[0].strip().lower()
    return kind in _PAGE_TYPES or (not kind and "attachment" not in headers.get("content-disposition", "").lower())


def _too_large(wanted: str, page_url: str) -> ActionResult:
    return ActionResult(ok=False, detail=f"download failed: '{wanted}' is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB", url_after=page_url, kind="wrong_file", retry=False)


def _declared_too_large(headers: dict[str, str]) -> bool:
    length = headers.get("content-length", "").strip()
    encoded = headers.get("content-encoding", "").strip().lower() not in ("", "identity")
    return not encoded and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES


async def _response_body(response: APIResponse, headers: dict[str, str]) -> bytes | None:
    try:
        return None if _declared_too_large(headers) else await response.body()
    finally:
        with contextlib.suppress(PlaywrightError):
            await response.dispose()


def _kept(path: Path, wanted: str, expected_name: str | None, via: str, page_url: str) -> ActionResult:
    from testzeus_hercules.act.file_contents import file_type

    size = path.stat().st_size
    if size == 0:
        path.unlink(missing_ok=True)
        return ActionResult(ok=False, detail=f"download failed: '{wanted}' arrived empty (0 bytes)", url_after=page_url, kind="wrong_file", retry=False)
    if size > MAX_DOWNLOAD_BYTES:
        path.unlink(missing_ok=True)
        return _too_large(wanted, page_url)
    if expected_name and expected_name.strip().casefold() not in (wanted.casefold(), download_name(wanted).casefold()):
        return ActionResult(ok=False, detail=f"download failed: the file is named '{wanted}', not '{expected_name.strip()}'", url_after=page_url, kind="wrong_file", retry=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    chosen = {"file": str(path), "name": path.name, "size": size, "type": file_type(path), "sha256": digest, "via": via}
    return ActionResult(ok=True, detail=f"downloaded '{path.name}' ({size:,} bytes)", url_after=page_url, chosen=chosen)


async def _save_download(item: Any, folder: Path, expected_name: str | None, page_url: str) -> ActionResult:
    failure = await item.failure()
    wanted = str(item.suggested_filename or "download")
    if failure:
        return ActionResult(ok=False, detail=f"download failed: {failure}", url_after=page_url, kind="wrong_file", retry=False)
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique(folder, download_name(wanted))
    await item.save_as(path)
    if path.stat().st_size == 0:
        await asyncio.sleep(1)
        await item.save_as(path)
    return _kept(path, wanted, expected_name, "download", page_url)


def _save_bytes(body: bytes | None, wanted: str, folder: Path, expected_name: str | None, via: str, page_url: str) -> ActionResult:
    if body is None:
        return _too_large(wanted, page_url)
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique(folder, download_name(wanted))
    path.write_bytes(body[: MAX_DOWNLOAD_BYTES + 1])
    return _kept(path, wanted, expected_name, via, page_url)


async def _fetched_file(page: Page, url: str, timeout_ms: int) -> tuple[bytes | None, str] | None:
    """The file at ``url`` fetched with the browser's cookies (no bytes when it says it is larger than ``MAX_DOWNLOAD_BYTES``), or None when it is a web page or cannot be fetched."""
    if urlsplit(url).scheme.lower() not in _WEB_SCHEMES:
        return None
    try:
        response = await page.context.request.get(url, timeout=timeout_ms, max_redirects=5)
        headers = {key.lower(): value for key, value in response.headers.items()}
        if response.status >= 400 or _is_page(headers):
            return None
        return await _response_body(response, headers), _response_name(response.url, headers)
    except PlaywrightError:
        return None


async def download_by_click(locator: Locator, folder: Path, *, expected_name: str | None = None, timeout_ms: int = DOWNLOAD_TIMEOUT_MS) -> ActionResult:
    """Click the element and keep the file it starts: a download, or a file it opens in a new tab or in this one.

    The file is saved into ``folder`` under its own name (made safe; ``_2``, ``_3`` on a clash). A click that starts
    no file within ``timeout_ms`` fails with retry (another element may be the right one); a file that arrives empty,
    too large or with another name than ``expected_name`` fails without retry.
    """
    page = locator.page
    blocked = await _blocked(locator, "download", min(timeout_ms, DEFAULT_TIMEOUT_MS))
    if blocked is not None:
        return blocked
    arrived: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    opened: list[Any] = []

    def on_download(item: Any) -> None:
        if not arrived.done():
            arrived.set_result(item)

    def on_page(new: Any) -> None:
        opened.append(new)
        new.on("download", on_download)

    before = page.url
    page.on("download", on_download)
    page.context.on("page", on_page)
    try:
        try:
            await locator.click(timeout=min(timeout_ms, DEFAULT_TIMEOUT_MS), no_wait_after=True)
        except PlaywrightError as exc:
            return ActionResult(ok=False, detail=f"download failed: {_first_line(exc)}", url_after=page.url)
        seen: set[str] = set()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if arrived.done():
                return await _save_download(arrived.result(), folder, expected_name, page.url)
            for source in [*opened, page]:
                address = str(getattr(source, "url", "") or "")
                if address in seen or address in ("", _BLANK_PAGE) or (source is page and address == before):
                    continue
                seen.add(address)
                fetched = await _fetched_file(page, address, timeout_ms)
                if fetched is not None:
                    if source is not page:
                        with contextlib.suppress(PlaywrightError):
                            await source.close()
                    return _save_bytes(fetched[0], fetched[1], folder, expected_name, "tab", page.url)
            await asyncio.sleep(_POLL_S)
        return ActionResult(ok=False, detail="download failed: clicking it started no download and opened no file", url_after=page.url, kind="no_download")
    finally:
        page.remove_listener("download", on_download)
        page.context.remove_listener("page", on_page)


async def download_from_url(
    page: Page, url: str, folder: Path, *, base_url: str | None = None, file_roots: Iterable[Path | str] | None = None, expected_name: str | None = None, timeout_ms: int = DOWNLOAD_TIMEOUT_MS
) -> ActionResult:
    """Fetch the file at ``url`` with the browser's cookies and keep it; the page stays where it is. An address that
    answers with a web page or an error fails without retry. A ``file:`` address inside the project is opened instead."""
    try:
        target = resolve_url(url, base_url, file_roots=file_roots)
    except InvalidURLError as exc:
        return ActionResult(ok=False, detail=str(exc), url_after=page.url, retry=False)
    if urlsplit(target).scheme.lower() not in _WEB_SCHEMES:
        return await _download_by_opening(page, target, folder, expected_name, timeout_ms)
    try:
        response = await page.context.request.get(target, timeout=timeout_ms, max_redirects=5)
        headers = {key.lower(): value for key, value in response.headers.items()}
        if response.status >= 400:
            return ActionResult(ok=False, detail=f"download of {target} failed: the server answered HTTP {response.status}", url_after=page.url, kind="not_a_file", retry=False)
        if _is_page(headers):
            return ActionResult(ok=False, detail=f"download failed: {target} is a web page, not a file", url_after=page.url, kind="not_a_file", retry=False)
        body = await _response_body(response, headers)
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"download of {target} failed: {_first_line(exc)}", url_after=page.url, retry=False)
    via = "download" if "attachment" in headers.get("content-disposition", "").lower() else "response"
    return _save_bytes(body, _response_name(response.url, headers), folder, expected_name, via, page.url)


async def _download_by_opening(page: Page, target: str, folder: Path, expected_name: str | None, timeout_ms: int) -> ActionResult:
    try:
        async with page.expect_download(timeout=timeout_ms) as info:
            try:
                await page.goto(target, timeout=timeout_ms)
            except PlaywrightError as exc:
                if "Download is starting" not in str(exc) and _INTERRUPTED not in str(exc):
                    raise
        return await _save_download(await info.value, folder, expected_name, page.url)
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"download of {target} failed: {_first_line(exc)}", url_after=page.url, kind="not_a_file", retry=False)


async def hover(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    return await _perform(locator.page, "hovered", lambda: locator.hover(timeout=timeout_ms))


async def press(target: Page | Locator, key: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    """Press ``key`` on the focused element (a page) or on a specific element (a locator)."""
    keyboard = getattr(target, "keyboard", None)
    if keyboard is not None:
        page: Page = target  # type: ignore[assignment]
        return await _perform(page, f"pressed {key}", lambda: keyboard.press(key), navigation_grace_s=NAVIGATION_START_GRACE_S)
    locator: Locator = target  # type: ignore[assignment]
    return await _perform(locator.page, f"pressed {key}", lambda: locator.press(key, timeout=timeout_ms, no_wait_after=True), navigation_grace_s=NAVIGATION_START_GRACE_S)


async def wait_seconds(page: Page, seconds: float) -> ActionResult:
    capped = max(0.0, min(seconds, MAX_WAIT_S))
    await page.wait_for_timeout(capped * 1000)
    return ActionResult(ok=True, detail=f"waited {capped:g} s", url_after=page.url)


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


async def page_text(page: Page) -> str:
    """Visible text of the page, its open shadow roots and its same-origin frames (``about:`` frames included)."""
    origin = _origin(page.url)
    parts = []
    for frame in page.frames:
        if frame is not page.main_frame and _origin(frame.url) != origin and not frame.url.startswith("about:"):
            continue
        try:
            parts.append(await frame.evaluate(_TEXT_JS))
        except PlaywrightError:
            continue
    parts.extend(_dialog_messages(page))
    return "\n".join(part for part in parts if part)


async def closed_drop_down_texts(page: Page) -> list[tuple[str, str]]:
    """Per closed drop-down in the main frame: the text innerText gives it (every option) and the option it shows."""
    try:
        found = await page.main_frame.evaluate(_CLOSED_DROP_DOWNS_JS)
    except PlaywrightError:
        return []
    return [(str(item.get("text") or ""), str(item.get("shown") or "")) for item in found]


def normalise_text(text: str) -> str:
    """Collapse whitespace and fold case, for text comparisons."""
    return " ".join(text.split()).casefold()


async def wait_for_text(page: Page, text: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
    """Wait until ``text`` appears in the visible page text (whitespace and case insensitive)."""
    wanted = normalise_text(text)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if wanted in normalise_text(await page_text(page)):
            return ActionResult(ok=True, detail=f"text {text!r} is shown", url_after=page.url)
        if time.monotonic() >= deadline:
            return ActionResult(ok=False, detail=f"text {text!r} did not appear within {timeout_ms / 1000:g} s", url_after=page.url)
        await asyncio.sleep(_POLL_S)


async def read_value(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    """A form field's value, or an element's visible text; None when it cannot be read."""
    try:
        value = await locator.evaluate(_VALUE_JS, timeout=timeout_ms)
    except PlaywrightError:
        return None
    return None if value is None else str(value)


_FIELD_ROLES = ("textbox", "combobox", "searchbox", "spinbutton")
_MAX_NAMED_FIELDS = 5


async def field_values(page: Page, label: str, *, timeout_ms: int = 1_000) -> list[str] | None:
    """What the visible fields named ``label`` hold: found by their label, then by role and accessible name (both exact),
    then by a label containing ``label``. None when no visible field is named so."""
    lookups: list[Locator] = [page.get_by_label(label, exact=True)]
    lookups += [page.get_by_role(role, name=label, exact=True) for role in _FIELD_ROLES]  # type: ignore[arg-type]
    lookups.append(page.get_by_label(label))
    for lookup in lookups:
        values: list[str] = []
        try:
            count = min(await lookup.count(), _MAX_NAMED_FIELDS)
            for index in range(count):
                item = lookup.nth(index)
                if not await item.is_visible():
                    continue
                value = await read_value(item, timeout_ms=timeout_ms)
                if value is not None:
                    values.append(value)
        except PlaywrightError:
            continue
        if values:
            return values
    return None


async def is_visible(locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
    """True once the element is visible, waiting at most ``timeout_ms``."""
    try:
        await locator.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightError:
        return False
    return True


async def text_visible(page: Page, text: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
    """True once some element whose text contains ``text`` is visible."""
    return await is_visible(page.get_by_text(text).first, timeout_ms=timeout_ms)


async def scroll(page: Page, *, pixels: int) -> ActionResult:
    try:
        await page.mouse.wheel(0, pixels)
    except PlaywrightError as exc:
        return ActionResult(ok=False, detail=f"scroll failed: {_first_line(exc)}", url_after=page.url)
    await settle(page)
    return ActionResult(ok=True, detail=f"scrolled {pixels} px", url_after=page.url)


async def screenshot_jpeg(page: Page) -> bytes | None:
    try:
        from testzeus_hercules.perceive.screen_guard import capture

        return await capture(page, type="jpeg", quality=70)
    except PlaywrightError:
        return None


class BrowserActions:
    """The action tools as one object, so the engine can be tested with a fake."""

    open_url = staticmethod(open_url)
    click = staticmethod(click)
    fill = staticmethod(fill)
    select = staticmethod(select)
    select_options = staticmethod(select_options)
    upload = staticmethod(upload)
    download_by_click = staticmethod(download_by_click)
    download_from_url = staticmethod(download_from_url)
    check = staticmethod(check)
    uncheck = staticmethod(uncheck)
    hover = staticmethod(hover)
    press = staticmethod(press)
    wait_seconds = staticmethod(wait_seconds)
    wait_for_text = staticmethod(wait_for_text)
    page_text = staticmethod(page_text)
    closed_drop_down_texts = staticmethod(closed_drop_down_texts)
    read_value = staticmethod(read_value)
    field_values = staticmethod(field_values)
    field_facts = staticmethod(field_facts)
    is_visible = staticmethod(is_visible)
    text_visible = staticmethod(text_visible)
    scroll = staticmethod(scroll)
    screenshot_jpeg = staticmethod(screenshot_jpeg)
    watch_page = staticmethod(watch_page)
    settle_navigation = staticmethod(settle_navigation)
    settle_page = staticmethod(settle_page)
    data_pending = staticmethod(data_pending)
