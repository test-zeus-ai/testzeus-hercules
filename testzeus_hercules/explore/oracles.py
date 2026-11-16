"""Oracles: how an explorer notices that something is wrong after a turn.

Three tiers, and a clue keeps the tier of the oracle that raised it:

- **code**: facts. An uncaught page error, a console error, a server error or failed request, a link to a missing
  page, an action that changed nothing (no request, no navigation, the same page text).
- **invariant**: a promise checked in code on the route it names (``Promise``): a text shown or absent, two numbers
  equal, a number not negative, saved text still there after a reload.
- **judged**: a model asked "is that right?" about what the turn did. It answers twice: the chance something is wrong,
  and the chance it is only a design preference (then it is a UX note, never a bug). Only answers at or above
  ``judge_bar`` raise a clue.

Signals seen on the happy path (recon) are the **baseline**: they are known noise and raise no clue later.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from testzeus_hercules.decide.contract import DecisionRequest, NoulCriteria, NoulQuestion, Question
from testzeus_hercules.explore.models import Move, Promise

__all__ = ["ClueDraft", "Oracles", "TurnStart", "normalize_route", "signature"]

_ID_SEGMENT = re.compile(r"/(?:\d+|[0-9a-f]{8,}|[0-9a-f-]{32,36})(?=/|$)", re.I)
_DIGITS = re.compile(r"\d+")
_PAGE_TEXT_JS = "() => document.body ? document.body.innerText : ''"
_NUMBER_JS = (
    "(selector) => { const el = document.querySelector(selector); if (!el) return null; const m = el.textContent.replace(/[,\\s]/g, '').match(/-?\\d+(\\.\\d+)?/); return m ? Number(m[0]) : null; }"
)
_TEXT_CLIP = 3000
JUDGE_PURPOSE = "explore_judge"


_STACK_URL = re.compile(r"https?://[^\s)'\"]+")


def _guard_or_abort(failure: str) -> bool:
    """A failure the scope guard caused (``net::ERR_BLOCKED_BY_CLIENT``, in any spelling) or a request the page cancelled."""
    text = failure.lower().replace("_", "")
    return "blockedbyclient" in text or "erraborted" in text


def normalize_route(url: str) -> str:
    """The URL's path with ids replaced, so one bug on many records is one signature."""
    path = urlsplit(url).path or "/"
    return _ID_SEGMENT.sub("/:id", path)


def _stem(text: str) -> str:
    return _DIGITS.sub("#", re.sub(r"\s+", " ", text)).strip()[:80]


def signature(oracle: str, route: str, key: str) -> str:
    return f"{oracle}|{route}|{_stem(key).lower()}"


@dataclass
class ClueDraft:
    oracle: str
    tier: str
    signature: str
    title: str
    detail: str = ""
    route: str = "/"
    expected: str = ""
    actual: str = ""
    confidence: float = 1.0
    promise: str | None = None
    opinion: bool = False


@dataclass
class TurnStart:
    url: str
    text: str
    digest: str
    records: int


@dataclass
class Oracles:
    base_url: str
    # The session's hosts (scope.allow_hosts, else the base URL's): a script error raised only by another host's script is not the app's.
    allow_hosts: Sequence[str] = ()
    promises: Sequence[Promise] = ()
    baseline: set[str] = field(default_factory=set)
    decider: Any = None
    judge_bar: float = 0.9
    judge: bool = True
    mission: str = ""
    redact: Callable[[str], str] = lambda text: text
    records: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    saved: dict[str, str] = field(default_factory=dict)  # route -> text a save showed (for persist promises)

    def _third_party_error(self, stack: str) -> bool:
        """True when every script in a page error's stack is on a host outside the session (a push or analytics SDK
        tripping over the scope guard's stand-in answers, for example)."""
        from testzeus_hercules.explore.scope import _host

        hosts = {host.lower() for host in self.allow_hosts} or {_host(self.base_url)}
        scripts = {_host(url) for url in _STACK_URL.findall(stack)}
        scripts.discard("")
        return bool(scripts) and not scripts & hosts

    def listen(self, kind: str, record: dict[str, Any]) -> None:
        """The page log's subscriber: every console and network record of the run."""
        self.records.append((kind, record))

    async def page_text(self, page: Any) -> str:
        try:
            return str(await page.evaluate(_PAGE_TEXT_JS))[:20_000]
        except Exception:
            return ""

    async def before(self, page: Any) -> TurnStart:
        text = await self.page_text(page)
        url = str(getattr(page, "url", "") or "")
        return TurnStart(url=url, text=text, digest=_digest(url, text), records=len(self.records))

    async def after(self, page: Any, move: Move, start: TurnStart, *, status: str, charter: str = "") -> tuple[list[ClueDraft], str]:
        """The clues this turn raised (baseline signals removed), and the page text now."""
        url = str(getattr(page, "url", "") or "")
        text = await self.page_text(page)
        route = normalize_route(url)
        new = self.records[start.records :]
        drafts = self._code(new, route, move, start, url, text, status)
        drafts += await self._invariants(page, route, text)
        # The judge is asked only when no code check or promise already explains the move: one bug, one clue.
        explained = any(draft.tier in ("code", "invariant") for draft in drafts)
        if self.judge and self.decider is not None and status != "skipped" and not explained and (_digest(url, text) != start.digest):
            drafts += await self._judged(move, start, url, text, charter)
        unique: dict[str, ClueDraft] = {}
        for draft in drafts:
            if draft.signature not in self.baseline:
                unique.setdefault(draft.signature, draft)
        return list(unique.values()), text

    # ------------------------------------------------------------------ code

    def _code(self, new: Sequence[tuple[str, dict[str, Any]]], route: str, move: Move, start: TurnStart, url: str, text: str, status: str) -> list[ClueDraft]:
        drafts: list[ClueDraft] = []
        requests = 0
        for kind, record in new:
            if kind == "console" and record.get("type") == "pageerror":
                message = str(record.get("text") or "")
                if self._third_party_error(str(record.get("stack") or "")):
                    continue
                drafts.append(
                    ClueDraft(
                        "page_error",
                        "code",
                        signature("page_error", route, message),
                        f"Uncaught page error on {route}",
                        self.redact(message)[:2000],
                        route,
                        "no script error",
                        self.redact(message)[:500],
                    )
                )
            elif kind == "console" and record.get("type") == "error":
                message = str(record.get("text") or "")
                if "net::ERR_BLOCKED_BY_CLIENT" in message or "Failed to load resource" in message:
                    continue  # the scope guard's own aborts, and resources reported again as network records
                drafts.append(
                    ClueDraft(
                        "console_error",
                        "code",
                        # One console message is one bug wherever it shows (a framework warning prints on every page).
                        signature("console_error", "*", message),
                        f"Console error on {route}",
                        self.redact(message)[:2000],
                        route,
                        "no console error",
                        self.redact(message)[:500],
                        confidence=0.6,
                    )
                )
            elif kind == "network":
                requests += 1
                status_code = int(record.get("status") or 0)
                where = normalize_route(str(record.get("url") or ""))
                if status_code >= 500:
                    drafts.append(
                        ClueDraft(
                            "server_error",
                            "code",
                            signature("server_error", where, f"{status_code // 100}xx"),
                            f"Server error {status_code} on {record.get('method', 'GET')} {where}",
                            f"after: {move.text}",
                            where,
                            "a page or a message, not a server error",
                            f"HTTP {status_code}",
                        )
                    )
                elif status_code == 404 and record.get("resource_type") == "document":
                    drafts.append(
                        ClueDraft("broken_link", "code", signature("broken_link", where, "404"), f"Page not found: {where}", f"after: {move.text}", where, "the linked page exists", "HTTP 404")
                    )
                elif record.get("failed") and not _guard_or_abort(str(record.get("failed"))) and move.direct != "offline_once":
                    drafts.append(
                        ClueDraft(
                            "request_failed",
                            "code",
                            signature("request_failed", where, str(record.get("failed"))),
                            f"Request failed: {where}",
                            str(record.get("failed"))[:500],
                            where,
                            "the request succeeds",
                            str(record.get("failed"))[:300],
                            confidence=0.6,
                        )
                    )
        # A submit that ignores an empty or blank value is a reasonable refusal, not a dead control.
        empty_submit = move.lever == "inputs" and not (move.value or "").strip()
        if move.clicks and move.direct is None and status in ("passed", "unverified") and requests == 0 and _digest(url, text) == start.digest and move.variant != "visit" and not empty_submit:
            what = move.clicks
            drafts.append(
                ClueDraft(
                    "dead_action",
                    "code",
                    signature("dead_action", route, what),
                    f"“{what}” does nothing on {route}",
                    f"after: {move.text}; no request, no navigation, the page text did not change",
                    route,
                    f"clicking “{what}” does something (a change, a message or a request)",
                    "nothing happened",
                    confidence=0.8,
                )
            )
        return drafts

    # ------------------------------------------------------------------ invariants

    async def _invariants(self, page: Any, route: str, text: str) -> list[ClueDraft]:
        drafts: list[ClueDraft] = []
        for promise in self.promises:
            if not (route == promise.route or (promise.route.endswith("*") and route.startswith(promise.route[:-1]))):
                continue
            broken, actual = await self._check(page, promise, route, text)
            if broken:
                tier = "invariant"
                drafts.append(
                    ClueDraft(
                        "invariant" if promise.kind != "persist" else "persistence",
                        tier,
                        signature("invariant", route, promise.id),
                        f"Promise broken on {route}: {promise.text}",
                        f"{promise.source} promise {promise.id}",
                        route,
                        promise.text,
                        actual,
                        confidence=1.0 if promise.source == "specified" else 0.7,
                        promise=promise.id,
                    )
                )
        return drafts

    async def _check(self, page: Any, promise: Promise, route: str, text: str) -> tuple[bool, str]:
        if promise.kind == "text_visible":
            return (promise.target not in text, f"“{promise.target}” is not shown")
        if promise.kind == "text_absent":
            return (promise.target in text, f"“{promise.target}” is shown")
        if promise.kind in ("equal", "not_negative"):
            left = await _number(page, promise.target)
            if promise.kind == "not_negative":
                return (left is not None and left < 0, f"{promise.target} shows {left}")
            right = await _number(page, promise.other or "")
            return (left is not None and right is not None and left != right, f"{promise.target} shows {left}, {promise.other} shows {right}")
        if promise.kind == "persist":
            remembered = self.saved.get(route)
            return (remembered is not None and remembered not in text, f"“{remembered}” is gone after a reload")
        return (False, "")

    # ------------------------------------------------------------------ judged

    async def _judged(self, move: Move, start: TurnStart, url: str, text: str, charter: str) -> list[ClueDraft]:
        route = normalize_route(url)
        state = {
            "task": "An exploratory tester just made one move in a web app under test. Decide whether the app now behaves wrongly.",
            "mission": self.mission[:500],
            "charter": charter[:300],
            "move": move.text[:500],
            "page_before": {"url": self.redact(start.url), "text": self.redact(start.text)[:_TEXT_CLIP]},
            "page_after": {"url": self.redact(url), "text": self.redact(text)[:_TEXT_CLIP]},
        }
        questions: dict[str, Question] = {
            "wrong": NoulQuestion(
                instructions="Is the app now behaving incorrectly after this move: wrong data, wrong totals or counts, lost or duplicated records, an error where a message belongs, a refused valid value, or an accepted invalid one?",
                criteria=NoulCriteria(true="a real bug a developer should fix", false="the app behaves correctly, or reasonably handles the unusual input"),
            ),
            "opinion": NoulQuestion(
                instructions="If something looks off, is it only a design preference (wording, layout taste) rather than incorrect behaviour?",
                criteria=NoulCriteria(true="a design preference, not a bug", false="incorrect behaviour"),
            ),
            "what": NoulQuestion(instructions="Would a careful tester report this to the team today?"),
        }
        try:
            decision = await self.decider.decide(DecisionRequest(state=state, questions=questions), purpose=JUDGE_PURPOSE)
            wrong, opinion, report = decision.noul("wrong").noul, decision.noul("opinion").noul, decision.noul("what").noul
        except Exception:  # a judge that fails raises nothing: the code oracles still hold
            return []
        if wrong < self.judge_bar or report < 0.5:
            return []
        title = f"Looks wrong after “{move.text[:80]}” on {route}"
        return [
            ClueDraft(
                "judged",
                "judged",
                signature("judged", route, move.variant + " " + (move.clicks or move.element or "")),
                title,
                "the model judged the page after the move as wrong",
                route,
                "the app handles this correctly",
                self.redact(text)[:500],
                confidence=round(wrong, 3),
                opinion=opinion >= 0.5,
            )
        ]


async def _number(page: Any, selector: str) -> float | None:
    try:
        value = await page.evaluate(_NUMBER_JS, selector)
    except Exception:
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _digest(url: str, text: str) -> str:
    return hashlib.sha256(f"{url}\n{text}".encode()).hexdigest()[:16]
