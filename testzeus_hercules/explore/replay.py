"""Model-free replay of an explorer's turns: the recorded actions and locators, in a fresh browser from the start URL.

This is the **recorded replay** that confirms a clue reproduces in the *app*: no model reads or decides anything, so a
miss says something about the app (or the environment), never about the agent. Steps that ran through the step
runner are replayed from their recorded ``AutomationStep`` (every action with the locator that matched only that
element); direct moves (back, reload, a double submit, an offline moment) are done again directly.
"""

from __future__ import annotations

import contextlib
import re
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from testzeus_hercules.cases.automation import AutomationAction, AutomationStep, TargetSpec
from testzeus_hercules.engine.page_log import PageLog
from testzeus_hercules.explore.explorer import do_direct
from testzeus_hercules.explore.models import Promise, Scope, Turn
from testzeus_hercules.explore.oracles import Oracles
from testzeus_hercules.explore.scope import ScopeGuard
from testzeus_hercules.perceive.locators import build_locator

__all__ = ["ReplayResult", "Replayer", "replayable"]

_ACTION_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class ReplayResult:
    signatures: frozenset[str]
    completed: bool
    problem: str = ""


def replayable(turns: Sequence[Turn]) -> bool:
    """Whether every turn can be replayed without a model (each step has its recorded actions)."""
    return all(turn.move.direct is not None or turn.automation is not None for turn in turns)


@dataclass
class Replayer:
    """Opens a fresh browser for each replay (``open_page``: an async context manager yielding a page)."""

    open_page: Callable[[], Any]
    base_url: str
    start_url: str
    scope: Scope
    promises: Sequence[Promise] = ()
    data: Callable[[str], str] = lambda text: text  # fills {{name}} placeholders in recorded values
    # A judged clue is confirmed by asking the same judge again about the last turn (where the anomaly showed);
    # the moves themselves are still replayed without a model.
    decider: Any = None
    judge_last: bool = False
    mission: str = ""
    # The start case's recorded steps: done first in a clean browser so each replay starts from a fresh session.
    setup: Sequence[Turn] = ()

    async def replay(self, turns: Sequence[Turn]) -> ReplayResult:
        """Do ``turns`` again; returns every signature the code and invariant oracles raised (and, with ``judge_last``, the
        judge's verdict on the last turn)."""
        found: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="hercules-replay-") as folder:
            log = PageLog(Path(folder), lambda text: text)
            oracles = Oracles(base_url=self.base_url, allow_hosts=self.scope.allow_hosts, promises=self.promises, judge=False, decider=self.decider, mission=self.mission)
            log.subscribe(oracles.listen)
            try:
                async with self.open_page() as page:
                    guard = ScopeGuard(self.scope, self.base_url)
                    await guard.install(page.context)
                    log.attach(page)
                    if self.setup:
                        await page.goto(self.base_url + "/", wait_until="load", timeout=20_000)
                        for turn in self.setup:
                            if await self._do(page, turn) != "passed":
                                raise RuntimeError(f"the start case's step did not replay: {turn.move.text[:120]}")
                        oracles.records.clear()  # the happy path to the start is not the anomaly
                    await page.goto(self.start_url, wait_until="load", timeout=20_000)
                    for index, turn in enumerate(turns):
                        oracles.judge = self.judge_last and self.decider is not None and index == len(turns) - 1
                        start = await oracles.before(page)
                        status = await self._do(page, turn)
                        drafts, text = await oracles.after(page, turn.move, start, status=status)
                        found.update(draft.signature for draft in drafts)
                        if turn.move.lever == "inputs" and turn.move.writes and (turn.move.value or "").strip() and (turn.move.value or "").strip() in text:
                            reload = await self._persistence(page, oracles, turn)
                            found.update(reload)
            except Exception as exc:  # a replay that cannot finish is a miss, with the reason kept
                log.close()
                return ReplayResult(frozenset(found), False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}")
            log.close()
        return ReplayResult(frozenset(found), True)

    async def _persistence(self, page: Any, oracles: Oracles, turn: Turn) -> set[str]:
        from testzeus_hercules.explore.models import Move
        from testzeus_hercules.explore.oracles import normalize_route

        value = (turn.move.value or "").strip()
        await do_direct(page, Move(lever="order", variant="reload_check", text="Reload the page", direct="reload"))
        if value in await oracles.page_text(page):
            return set()
        return {f"persistence|{normalize_route(str(page.url))}|{(turn.move.clicks or turn.move.element or '').lower()}"}

    async def _do(self, page: Any, turn: Turn) -> str:
        if turn.move.direct is not None:
            return await do_direct(page, turn.move)
        if turn.automation is None:
            return "error"
        step = AutomationStep.model_validate(turn.automation)
        for action in step.actions:
            if not await self._action(page, action):
                return "failed"
        return "passed"

    async def _action(self, page: Any, action: AutomationAction) -> bool:
        try:
            if action.kind == "navigate":
                await page.goto(urljoin(self.base_url + "/", action.url or "/"), wait_until="load", timeout=20_000)
                return True
            if action.kind == "wait_time":
                await page.wait_for_timeout(min((action.seconds or 1) * 1000, 30_000))
                return True
            if action.kind == "wait_text":
                await page.get_by_text(action.text or "").first.wait_for(timeout=_ACTION_TIMEOUT_MS)
                return True
            if action.kind == "press" and action.target is None:
                await page.keyboard.press(action.key or "Enter")
                return True
            locator = await _locate_now(page, action.target)
            if locator is None:
                return False
            if action.kind == "click":
                await locator.click(timeout=_ACTION_TIMEOUT_MS)
            elif action.kind == "fill":
                await locator.fill(self.data(action.value or ""), timeout=_ACTION_TIMEOUT_MS)
            elif action.kind == "select":
                await locator.select_option(label=self.data(action.option or action.value or ""), timeout=_ACTION_TIMEOUT_MS)
            elif action.kind == "check":
                await locator.check(timeout=_ACTION_TIMEOUT_MS)
            elif action.kind == "uncheck":
                await locator.uncheck(timeout=_ACTION_TIMEOUT_MS)
            elif action.kind == "hover":
                await locator.hover(timeout=_ACTION_TIMEOUT_MS)
            elif action.kind == "press":
                await locator.press(action.key or "Enter", timeout=_ACTION_TIMEOUT_MS)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("load", timeout=_ACTION_TIMEOUT_MS)
            return True
        except Exception:
            return False


def _locate(page: Any, target: TargetSpec | None) -> Any:
    if target is None:
        return None
    scope = page
    for frame in target.frames:
        scope = build_locator(scope, frame.locator).content_frame
    found = None
    for spec in [target.locator, *target.fallbacks]:
        if spec is not None:
            found = build_locator(scope, spec)
            break
    if found is None and target.name:
        found = scope.get_by_role(target.role or "button", name=target.name, exact=True)
    return found.first if found is not None else None


async def _locate_now(page: Any, target: TargetSpec | None) -> Any:
    """The recorded control; if it is not on the page and its name holds a count ("Cart (3)"), the same control with
    any count there (a fresh session shows another count). The exact control always wins."""
    found = _locate(page, target)
    if found is None or target is None:
        return found
    pattern = counted_name(target.name or "")
    if pattern is None:
        return found
    with contextlib.suppress(Exception):
        if await found.count() == 0:
            return page.get_by_role(target.role or "button", name=pattern).first
    return found


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def counted_name(name: str) -> re.Pattern[str] | None:
    """A pattern for an accessible name with any numbers in place of the recorded ones, or None if it has none."""
    parts = _NUMBER.split(name)
    if len(parts) == 1 or not "".join(parts).strip():
        return None
    return re.compile("^" + _NUMBER.pattern.join(re.escape(part) for part in parts) + "$")


async def confirm(replayer: Replayer, turns: Sequence[Turn], signature: str, runs: int, *, on_run: Callable[[int, bool], Awaitable[None]] | None = None) -> int:
    """How many of ``runs`` recorded replays raised ``signature``."""
    hits = 0
    for index in range(runs):
        result = await replayer.replay(turns)
        hit = signature in result.signatures
        hits += hit
        if on_run is not None:
            await on_run(index + 1, hit)
    return hits


async def minimize(replayer: Replayer, turns: Sequence[Turn], signature: str, *, max_runs: int = 15) -> tuple[list[Turn], int]:
    """Delta debugging: the fewest turns (kept in order) whose recorded replay still raises ``signature``; and the replays used.

    Removes chunks (halves, then smaller), keeping each removal that still reproduces. The last turn (where the anomaly
    showed) is always kept.
    """
    current = list(turns)
    used = 0
    if len(current) <= 1:
        return current, used
    chunk = max(1, (len(current) - 1) // 2)
    while chunk >= 1 and used < max_runs:
        changed = False
        start = 0
        while start < len(current) - 1 and used < max_runs:
            candidate = current[:start] + current[start + chunk :] if start + chunk < len(current) else current[:start] + current[-1:]
            if candidate and candidate[-1] is current[-1] and len(candidate) < len(current):
                used += 1
                if signature in (await replayer.replay(candidate)).signatures:
                    current = candidate
                    changed = True
                    continue
            start += chunk
        if not changed:
            if chunk == 1:
                break
            chunk = max(1, chunk // 2)
    return current, used
