"""Step-indexed helpers for the plain-Playwright validator.

Every helper takes the 1-based step number of the scenario line it carries out, so a
failure is reported at the same step Hercules should report it. Text checks mirror
Hercules' own: the page's ``innerText`` with whitespace collapsed and case folded,
polled until it holds or the timeout ends.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterator
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Dialog, Locator, Page

DEFAULT_TIMEOUT_S = 15.0
_POLL_S = 0.25


class StepFailed(Exception):
    """A scenario step did not hold; ``step`` is its 1-based index."""

    def __init__(self, step: int, reason: str) -> None:
        super().__init__(f"step {step}: {reason}")
        self.step = step
        self.reason = reason


def norm(text: str) -> str:
    """Collapse whitespace and fold case, as Hercules does for text checks."""
    return " ".join(text.split()).casefold()


def _first_line(exc: BaseException) -> str:
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return (lines[0] if lines else type(exc).__name__)[:200]


class Flow:
    """One scenario's browser page plus the step it is on."""

    def __init__(self, page: Page, base_url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.page = page
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.current = 0
        self.max_step = 0
        self.dialogs: list[str] = []
        page.set_default_timeout(timeout_s * 1000)
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(dialog.message)
        dialog.accept()

    @contextmanager
    def at(self, step: int, what: str) -> Iterator[Page]:
        """Run a block as ``step``; any Playwright error or assertion fails that step."""
        self.current = step
        self.max_step = max(self.max_step, step)
        try:
            yield self.page
        except StepFailed:
            raise
        except (PlaywrightError, AssertionError) as exc:
            raise StepFailed(step, f"{what}: {_first_line(exc)}") from None

    # ------------------------------------------------------------------ actions

    def open(self, step: int, path: str) -> None:
        with self.at(step, f"open {path}") as page:
            page.goto(urljoin(self.base_url, path), wait_until="domcontentloaded")

    def click(self, step: int, locator: Locator, what: str = "click") -> None:
        with self.at(step, what):
            locator.click()

    def fill(self, step: int, locator: Locator, value: str, what: str = "fill") -> None:
        with self.at(step, f"{what} {value!r}"):
            locator.fill(value)

    def select(self, step: int, locator: Locator, label: str) -> None:
        with self.at(step, f"select {label!r}"):
            locator.select_option(label=label)

    def press(self, step: int, key: str) -> None:
        with self.at(step, f"press {key}") as page:
            page.keyboard.press(key)

    def do(self, step: int, what: str, action: Callable[[Page], None]) -> None:
        with self.at(step, what) as page:
            action(page)

    # ------------------------------------------------------------------ checks

    def idle(self, seconds: float) -> None:
        """Sleep while letting Playwright process page events (``time.sleep`` would leave ``page.url`` stale)."""
        if seconds > 0:
            self.page.wait_for_timeout(seconds * 1000)

    def text(self) -> str:
        try:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''")
        except PlaywrightError:
            return ""

    def wait_until(self, step: int, what: str, predicate: Callable[[], bool], timeout_s: float | None = None) -> None:
        """Poll ``predicate`` until true; fail ``step`` with ``what`` otherwise."""
        self.current = step
        self.max_step = max(self.max_step, step)
        deadline = time.monotonic() + (timeout_s if timeout_s is not None else self.timeout_s)
        last_error = ""
        while True:
            try:
                if predicate():
                    return
            except PlaywrightError as exc:
                last_error = f" ({_first_line(exc)})"
            if time.monotonic() >= deadline:
                raise StepFailed(step, f"{what}{last_error}")
            self.idle(_POLL_S)

    def see(self, step: int, text: str, timeout_s: float | None = None) -> None:
        wanted = norm(text)
        self.wait_until(step, f"text not shown: {text!r}", lambda: wanted in norm(self.text()), timeout_s)

    def not_see(self, step: int, text: str, timeout_s: float | None = None, settle_s: float = 1.0) -> None:
        wanted = norm(text)
        self.wait_until(step, f"text still shown: {text!r}", lambda: wanted not in norm(self.text()), timeout_s)
        self.idle(settle_s)
        if wanted in norm(self.text()):
            raise StepFailed(step, f"text shown again: {text!r}")

    def url_has(self, step: int, fragment: str, timeout_s: float | None = None) -> None:
        self.wait_until(step, f"url lacks {fragment!r}", lambda: fragment in self.page.url, timeout_s)

    def url_is(self, step: int, url: str, timeout_s: float | None = None) -> None:
        self.wait_until(step, f"url is not {url!r}", lambda: self.page.url == url, timeout_s)

    def visible(self, step: int, locator: Locator, what: str, timeout_s: float | None = None) -> None:
        self.wait_until(step, f"not visible: {what}", locator.is_visible, timeout_s)

    def pause(self, step: int, seconds: float) -> None:
        with self.at(step, "wait"):
            self.idle(seconds)
