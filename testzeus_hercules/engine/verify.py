"""Verification: text, URL and visibility checks recorded in an assertion ledger.

A model reads what a step expects (see :mod:`testzeus_hercules.engine.intent`); the
checks it names are run here in code against what the page shows, and every check
produces one :class:`LedgerEntry`. Expectations no literal check can hold are judged
by a model in the step graph: ``semantic`` (``Step.semantic``, or an outcome the model
could only read by its meaning) on the page text and, when images may be sent, the
screenshot; ``visual`` (how the page looks) on the screenshot. A judgement that is
not confident either way is ``unverified``, never passed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

from testzeus_hercules.engine.intent import StepIntent

CheckKind = Literal["text", "not_text", "url", "visible", "visual", "semantic"]
LedgerVerdict = Literal["passed", "failed", "unverified"]
Redactor = Callable[[str], str]

_ACTUAL_CLIP = 240
_POLL_S = 0.2


class LedgerEntry(BaseModel):
    """One assertion: what was checked, what was expected and seen, and the verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int
    check: str
    expected: str
    actual: str
    verdict: LedgerVerdict
    source: Literal["deterministic", "llm"] = "deterministic"


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    expected: str
    match: Literal["contains", "equals"] = "contains"
    reason: str = ""
    # ``expected`` as the step wrote it, placeholders kept (set by ``resolved``; for exporting the run as code).
    template: str | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        if self.kind == "text":
            return "text_contains"
        if self.kind == "not_text":
            return "text_absent"
        if self.kind == "url":
            return f"url_{self.match}"
        return self.kind

    def resolved(self, resolve: Callable[[str], str]) -> Check:
        return Check(kind=self.kind, expected=resolve(self.expected), match=self.match, reason=self.reason, template=self.template if self.template is not None else self.expected)


def check_from_intent(intent: StepIntent) -> Check | None:
    """The check an assertion intent stands for (``assert_visible`` needs an element, so None)."""
    if intent.kind == "assert_text" and intent.expected is not None:
        return Check(kind="text", expected=intent.expected)
    if intent.kind == "assert_not_text" and intent.expected is not None:
        return Check(kind="not_text", expected=intent.expected)
    if intent.kind == "assert_url" and intent.expected is not None:
        return Check(kind="url", expected=intent.expected, match=intent.match or "contains")
    if intent.kind == "assert_looks" and intent.expected is not None:
        return Check(kind="visual", expected=intent.expected, reason="an expectation about how the page looks is judged on a screenshot")
    return None


def semantic_check(text: str) -> Check:
    """An expected outcome judged by its meaning (a model compares it with the page)."""
    return Check(kind="semantic", expected=text, reason="judged by meaning")


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _ACTUAL_CLIP else flat[: _ACTUAL_CLIP - 1] + "…"


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _entry(step: int, check: Check, actual: str, verdict: LedgerVerdict, redact: Redactor) -> LedgerEntry:
    return LedgerEntry(step=step, check=check.label, expected=redact(check.expected), actual=redact(actual), verdict=verdict)


async def _poll(predicate: Callable[[], Any], timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if await predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_POLL_S)


async def run_check(check: Check, *, page: Any, actions: Any, step: int, timeout_ms: int, redact: Redactor) -> LedgerEntry:
    """Run one check against the page, waiting up to ``timeout_ms`` for it to hold."""
    if check.kind in ("semantic", "visual"):  # judged by a model in the step graph, never by matching text
        return _entry(step, check, check.reason or "not verified", "unverified", redact)
    if check.kind == "url":
        return await _check_url(check, page=page, step=step, timeout_ms=timeout_ms, redact=redact)
    if check.kind == "visible":
        ok = await actions.text_visible(page, check.expected, timeout_ms=timeout_ms)
        return _entry(step, check, "visible" if ok else "no visible element shows this text", "passed" if ok else "failed", redact)
    wanted = _norm(check.expected)
    present = check.kind == "text"
    seen = {"text": ""}
    drop_downs = getattr(actions, "closed_drop_down_texts", None)

    async def holds() -> bool:
        seen["text"] = await actions.page_text(page)
        hidden = await drop_downs(page) if callable(drop_downs) and wanted in _norm(seen["text"]) else []
        return _shown(wanted, _norm(seen["text"]), hidden) == present

    if await _poll(holds, timeout_ms):
        return _entry(step, check, "shown" if present else "not shown", "passed", redact)
    if present:
        return _entry(step, check, f'not found; page text: "{_clip(seen["text"])}"', "failed", redact)
    return _entry(step, check, "the text is shown on the page", "failed", redact)


def _shown(wanted: str, text: str, drop_downs: list[tuple[str, str]]) -> bool:
    """True when ``wanted`` occurs in the page text other than among a closed drop-down's unchosen options.

    innerText lists every option of a closed drop-down although only the chosen one is shown; a match
    that runs on past the drop-down, or occurs elsewhere too, still counts.
    """
    found = text.count(wanted)
    if not found:
        return False
    hidden = sum(_norm(options).count(wanted) for options, chosen in drop_downs if wanted not in _norm(chosen))
    return found > hidden


async def _check_url(check: Check, *, page: Any, step: int, timeout_ms: int, redact: Redactor) -> LedgerEntry:
    async def holds() -> bool:
        url = str(page.url)
        return url == check.expected if check.match == "equals" else check.expected in url

    ok = await _poll(holds, timeout_ms)
    return _entry(step, check, str(page.url), "passed" if ok else "failed", redact)


async def verify_visible(locator: Any | None, target: str, *, page: Any, actions: Any, step: int, timeout_ms: int, redact: Redactor) -> LedgerEntry:
    """The chosen element is visible; without one, some element showing ``target`` is."""
    check = Check(kind="visible", expected=target)
    if locator is None:
        return await run_check(check, page=page, actions=actions, step=step, timeout_ms=timeout_ms, redact=redact)
    ok = await actions.is_visible(locator, timeout_ms=timeout_ms)
    return _entry(step, check, "visible" if ok else "the element is present but not visible", "passed" if ok else "failed", redact)
