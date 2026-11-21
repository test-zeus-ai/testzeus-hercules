"""Human in the loop: pause, resume, step, skip and retry a run, and ``needs_human`` approvals.

A :class:`RunControl` is checked by the run graph before every step and by the step
graph before every action. Commands arrive from any thread through :meth:`RunControl.submit`
as JSON-like messages (the worker reads them from the daemon); the waits run on the
run's event loop. Events report what happened:

* ``paused`` (``step``, ``where``: ``step`` or ``action``, ``action``) when the run reached a pause point;
* ``resumed`` when it continues;
* ``needs_approval`` (``id``, ``kind``, ``step``, ``reason``, ``action``, ``target``, ``screenshot``,
  ``timeout_s``) when it waits for a decision, and ``approval_result`` with the decision;
* ``control_ignored`` for a command that does not apply.

``{"type": "detach"}`` (sent by the worker when its command stream closes) resumes a
paused run and rejects any open question, so a run never waits for nobody.

Approval kinds: ``destructive`` (a click on an element whose name suggests deleting,
paying or transferring, or that the run's plan flags destructive or irreversible; off
unless the policy enables it), ``recovery_exhausted`` (a step failed after its
recoveries; asked when the policy says so or while the tester is debugging the run),
``draft`` (a drafted case before its first step) and ``plan`` (the run's plan before the
browser starts, with ``plan_review: ask``; an edit means the edited plan is in
``plan.edited.json``). An approval
that gets no answer within the policy's timeout counts as a rejection, with that reason.
Time spent waiting for a person is excluded from step and case timeouts.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

from testzeus_hercules.kernel.run_context import CancelToken, EventSink, NullEventSink, RunCancelledError

ControlAction = Literal["pause", "resume", "step", "skip", "retry"]
ApprovalDecision = Literal["approve", "reject", "edit"]
InterruptKind = Literal["destructive", "recovery_exhausted", "draft", "plan"]

CONTROL_ACTIONS: frozenset[str] = frozenset({"pause", "resume", "step", "skip", "retry"})
DECISIONS: frozenset[str] = frozenset({"approve", "reject", "edit"})
# Generic words for actions that are hard to undo; matched as whole words in the element's name.
DEFAULT_DESTRUCTIVE_TERMS: tuple[str, ...] = (
    "delete",
    "remove",
    "erase",
    "cancel order",
    "cancel subscription",
    "close account",
    "deactivate",
    "pay",
    "pay now",
    "submit payment",
    "make payment",
    "transfer",
    "send money",
    "withdraw",
)
# Roles a click can trigger something on; typing into a field named "Delete" is not destructive.
_CLICKABLE_ROLES = frozenset({"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "option", "tab", "switch", "checkbox", "radio", "clickable", "generic", ""})
# Step failures that mean "could not interact", as opposed to a failed check (a verdict, not a stuck run).
_INTERACTION_FAILURES = frozenset({"TargetNotFound", "LowConfidence", "TargetGone", "ActionFailed", "ElementDisabled", "NavigationFailed", "WaitTimeout", "StepTimeout"})
DEFAULT_APPROVAL_TIMEOUT_S = 600.0
_POLL_S = 0.1
_TEXT_CLIP = 300
_MAX_STEP_TEXT = 2000


class StepInterrupt(Exception):
    """Raised inside a step by a person's decision; the run graph handles it (never a step error)."""


class SkipStep(StepInterrupt):
    """The tester skipped the step."""


class RetryStep(StepInterrupt):
    """Run the step again from the start, with ``text`` as its new action when given."""

    def __init__(self, text: str | None = None) -> None:
        super().__init__(text or "retry")
        self.text = text


class StepBlocked(StepInterrupt):
    """The tester rejected an action (or did not answer in time): the step is ``blocked``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DestructiveGuard:
    """Says whether an element's name suggests an action that is hard to undo."""

    def __init__(self, terms: tuple[str, ...] | list[str] = DEFAULT_DESTRUCTIVE_TERMS) -> None:
        cleaned = sorted({" ".join(str(term).casefold().split()) for term in terms if str(term).strip()}, key=len, reverse=True)
        self.terms = tuple(cleaned)
        self._patterns = [(term, re.compile(r"(?<![0-9a-z])" + r"\s+".join(re.escape(word) for word in term.split()) + r"(?![0-9a-z])")) for term in self.terms]

    def matches(self, name: str | None, role: str | None = None) -> str | None:
        """The matching term (longest first), or None; only for elements a click acts on."""
        if not name or (role or "") not in _CLICKABLE_ROLES:
            return None
        text = " ".join(name.casefold().split())
        return next((term for term, pattern in self._patterns if pattern.search(text)), None)


@dataclass(frozen=True)
class HumanPolicy:
    """When a run stops for a person. Everything is off by default (bench and CLI runs never wait)."""

    confirm_destructive: bool = False
    destructive_terms: tuple[str, ...] = DEFAULT_DESTRUCTIVE_TERMS
    ask_on_failure: bool = False
    approval_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S


@dataclass(frozen=True)
class Directive:
    """What to do at a step boundary: ``run`` the step, ``skip`` it, or ``retry`` the previous one."""

    kind: Literal["run", "skip", "retry"]


@dataclass(frozen=True)
class ApprovalAnswer:
    decision: ApprovalDecision
    step_text: str | None = None
    timed_out: bool = False
    # A plan approval answered with an edited plan (written to plan.edited.json in the run directory).
    plan_edited: bool = False


@dataclass
class _Pending:
    id: str
    kind: InterruptKind
    allow_edit: bool
    future: asyncio.Future[ApprovalAnswer] = field(repr=False)


class RunControl:
    """The control channel of one run (see the module docstring)."""

    def __init__(self, events: EventSink | None = None, policy: HumanPolicy | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.events: EventSink = events if events is not None else NullEventSink()
        self.policy = policy or HumanPolicy()
        self.guard = DestructiveGuard(self.policy.destructive_terms)
        self._clock = clock
        self._lock = threading.Lock()
        self._backlog: deque[Mapping[str, Any]] = deque()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel: CancelToken | None = None
        self._changed: asyncio.Event | None = None
        self._paused = False
        self._stepping_from: int | None = None
        self._directive: str | None = None
        self._at_pause_point = False
        self._current_step = 0
        self._debugging = False
        self._pending: dict[str, _Pending] = {}
        self._approved: set[tuple[int, str]] = set()
        self._waited_s = 0.0
        self._wait_started: float | None = None
        self._detached = False

    # ------------------------------------------------------------------ wiring

    def bind(self, loop: asyncio.AbstractEventLoop, *, cancel: CancelToken | None = None) -> None:
        """Attach to the run's event loop; commands received earlier are applied now."""
        with self._lock:
            self._loop = loop
            self._cancel = cancel
            self._changed = asyncio.Event()
            backlog = list(self._backlog)
            self._backlog.clear()
        for message in backlog:
            self._apply(message)

    def submit(self, message: Mapping[str, Any]) -> None:
        """Queue a command or an approval answer; safe from any thread."""
        with self._lock:
            loop = self._loop
            if loop is None:
                self._backlog.append(message)
                return
        if _on_loop(loop):
            self._apply(message)
        else:
            try:
                loop.call_soon_threadsafe(self._apply, message)
            except RuntimeError:  # the loop is closed: the run is over
                return

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def at_pause_point(self) -> bool:
        """True while the run is stopped at a pause point (the browser is idle and may be driven by hand)."""
        return self._at_pause_point

    @property
    def interactive(self) -> bool:
        """True once a person has paused or stepped this run."""
        return self._debugging

    @property
    def human_wait_s(self) -> float:
        """Seconds spent waiting for a person so far (including a wait in progress)."""
        started = self._wait_started
        return self._waited_s + (self._clock() - started if started is not None else 0.0)

    # ------------------------------------------------------------------ commands

    def _apply(self, message: Mapping[str, Any]) -> None:
        if not isinstance(message, Mapping):
            self._ignored("a control message must be a JSON object")
            return
        kind = message.get("type")
        if kind == "control":
            self._command(str(message.get("action", "")))
        elif kind == "approval":
            self._answer(message)
        elif kind == "detach":
            self._detach()
        else:
            self._ignored(f"unknown message type {str(kind)[:40]!r}")
        if self._changed is not None:
            self._changed.set()

    def _command(self, action: str) -> None:
        if action not in CONTROL_ACTIONS:
            self._ignored(f"unknown control action {action[:40]!r}")
            return
        if action == "pause":
            self._debugging = True
            self._paused, self._stepping_from = True, None
        elif action == "resume":
            self._paused, self._stepping_from, self._directive = False, None, None
        elif action == "step":
            self._debugging = True
            self._stepping_from = self._current_step
            self._paused = False
        elif not self._at_pause_point:
            self._ignored(f"{action} only applies while the run is paused")
        else:
            self._directive = action

    def _detach(self) -> None:
        """Nobody can send commands any more (the daemon went away): never wait for a person again."""
        self._detached = True
        self._paused, self._stepping_from, self._directive = False, None, None
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_result(ApprovalAnswer(decision="reject"))

    def _answer(self, message: Mapping[str, Any]) -> None:
        decision = str(message.get("decision", ""))
        wanted = message.get("id")
        pending = self._pending.get(str(wanted)) if wanted else (next(iter(self._pending.values())) if len(self._pending) == 1 else None)
        if pending is None or pending.future.done():
            self._ignored("no approval with that id is waiting")
            return
        if decision not in DECISIONS:
            self._ignored(f"unknown decision {decision[:40]!r}")
            return
        text = " ".join(str(message.get("step_text") or "").split())[:_MAX_STEP_TEXT] or None
        if pending.kind == "plan" and decision == "edit":  # an edited plan waits in the run directory (plan.edited.json)
            pending.future.set_result(ApprovalAnswer(decision="edit", plan_edited=True))
            return
        if decision == "edit" and (not pending.allow_edit or text is None):
            self._ignored("edit needs the step's new text" if pending.allow_edit else f"a {pending.kind} approval cannot be edited")
            return
        pending.future.set_result(ApprovalAnswer(decision=decision, step_text=text if decision == "edit" else None))  # type: ignore[arg-type]

    def _ignored(self, reason: str) -> None:
        self.events.emit("control_ignored", reason=reason)

    # ------------------------------------------------------------------ pause points

    async def before_step(self, step: int, action: str, *, can_retry: bool = True) -> Directive:
        """Called before step ``step`` runs; waits while paused and returns what to do."""
        self._current_step = step
        if self._stepping_from is not None and step > self._stepping_from:
            self._paused, self._stepping_from = True, None
        if not self._paused:
            return Directive("run")
        while True:
            outcome = await self._hold(step, "step", action)
            if outcome == "retry" and not can_retry:
                self._ignored("there is no earlier step to retry")
                self._paused = True
                continue
            if outcome == "skip":
                self.events.emit("step_skipped", step=step)
                return Directive("skip")
            if outcome == "retry":
                self._stepping_from = step - 1  # run the previous step again, then stop before this one
                self._paused = False
                return Directive("retry")
            return Directive("run")

    async def before_action(self, step: int, what: str) -> None:
        """Called before each browser action; waits while paused, may raise :class:`SkipStep` or :class:`RetryStep`."""
        self._current_step = step
        if self._stepping_from is not None or not self._paused:
            return
        outcome = await self._hold(step, "action", what)
        if outcome == "skip":
            self.events.emit("step_skipped", step=step)
            raise SkipStep()
        if outcome == "retry":
            self._stepping_from = step - 1
            self._paused = False
            raise RetryStep()

    async def _hold(self, step: int, at: str, action: str) -> str:
        """Wait at a pause point until resumed or told to skip or retry; returns ``run``, ``skip`` or ``retry``."""
        self._directive = None
        self._at_pause_point = True
        self.events.emit("paused", step=step, where=at, action=_clip(action), state="paused")
        started = self._begin_wait()
        try:
            while self._paused and self._directive is None:
                await self._wait_change()
        finally:
            self._end_wait(started)
            self._at_pause_point = False
        directive, self._directive = self._directive, None
        if directive in ("skip", "retry"):
            return directive
        self.events.emit("resumed", step=step, state="running", stepping=self._stepping_from is not None)
        return "run"

    # ------------------------------------------------------------------ approvals

    async def confirm_destructive(self, step: int, action: str, *, name: str, role: str | None = None, screenshot: str | None = None, flagged: str | None = None) -> None:
        """Before a click: ask for approval when the element looks destructive, or the run's plan flags the action as
        destructive or irreversible (``flagged``: the plan's reason), and the policy says so."""
        if not self.policy.confirm_destructive:
            return
        term = self.guard.matches(name, role)
        key = (step, " ".join(name.split()).casefold())
        if (term is None and not flagged) or key in self._approved:
            return
        why = f'("{term}")' if term is not None else f"(the plan says: {_clip(flagged, 160)})"
        reason = f'about to click "{_clip(name, 120)}", which looks destructive {why}'
        answer = await self.ask("destructive", step=step, reason=reason, action=action, target=name, screenshot=screenshot)
        if answer.decision == "approve":
            self._approved.add(key)
            return
        if answer.decision == "edit":
            raise RetryStep(answer.step_text)
        raise StepBlocked(_rejection(answer, self.policy.approval_timeout_s, f'the tester rejected the click on "{_clip(name, 120)}"'))

    async def after_failure(self, step: int, action: str, error_class: str | None, problem: str | None, *, screenshot: str | None = None) -> ApprovalAnswer | None:
        """After a step failed: ask whether to retry (approve), retry with new text (edit) or stop (reject).

        None when nobody should be asked: the policy is off and the tester is not debugging this run,
        or (policy only) the step failed a check rather than an interaction.
        """
        if not self._debugging and not (self.policy.ask_on_failure and error_class in _INTERACTION_FAILURES):
            return None
        reason = f"step {step} failed: {_clip(problem or error_class or 'step failed')}; approve to retry it, edit its text, or reject to stop"
        return await self.ask("recovery_exhausted", step=step, reason=reason, action=action, screenshot=screenshot)

    async def approve_plan(self, steps: int, *, errors: int = 0, warnings: int = 0) -> ApprovalAnswer:
        """Before the browser starts: ask a person to approve the run's plan (``plan.json`` in the run directory), edit it
        (the edited plan is ``plan.edited.json``), or reject it."""
        lint = f"; lint: {errors} error(s), {warnings} warning(s)" if errors or warnings else ""
        return await self.ask("plan", step=0, reason=f"review the run's plan ({steps} plan steps{lint}) before it starts", action="", allow_edit=True)

    async def approve_draft(self, steps: int) -> bool:
        """Before the first step of a drafted case: True when approved."""
        answer = await self.ask("draft", step=0, reason=f"the case is a draft ({steps} steps); review its steps before it runs", action="", allow_edit=False)
        return answer.decision == "approve"

    async def ask(self, kind: InterruptKind, *, step: int, reason: str, action: str, target: str | None = None, screenshot: str | None = None, allow_edit: bool = True) -> ApprovalAnswer:
        """Emit ``needs_approval`` and wait (bounded) for the answer; a timeout is a rejection."""
        if self._detached:
            return ApprovalAnswer(decision="reject")
        loop = asyncio.get_running_loop()
        approval_id = secrets.token_hex(4)
        pending = _Pending(id=approval_id, kind=kind, allow_edit=allow_edit, future=loop.create_future())
        self._pending[approval_id] = pending
        timeout = self.policy.approval_timeout_s
        self.events.emit(
            "needs_approval",
            id=approval_id,
            kind=kind,
            step=step,
            reason=_clip(reason, 500),
            action=_clip(action),
            target=_clip(target) if target else None,
            screenshot=screenshot,
            timeout_s=timeout,
            allow_edit=allow_edit,
        )
        started = self._begin_wait()
        try:
            answer = await self._await_answer(pending.future, timeout)
        finally:
            self._end_wait(started)
            self._pending.pop(approval_id, None)
        self.events.emit(
            "approval_result", id=approval_id, kind=kind, step=step, decision=answer.decision, step_text=answer.step_text, timed_out=answer.timed_out, state="paused" if self._paused else "running"
        )
        return answer

    async def _await_answer(self, future: asyncio.Future[ApprovalAnswer], timeout: float) -> ApprovalAnswer:
        deadline = self._clock() + timeout
        while not future.done():
            if self._clock() >= deadline:
                future.cancel()
                return ApprovalAnswer(decision="reject", timed_out=True)
            await self._wait_change(min(_POLL_S, max(0.0, deadline - self._clock())))
        return future.result()

    # ------------------------------------------------------------------ waiting

    async def _wait_change(self, timeout: float = _POLL_S) -> None:
        if self._cancel is not None and self._cancel.is_cancelled:
            raise RunCancelledError(self._cancel.reason or "run cancelled")
        changed = self._changed
        if changed is None:
            await asyncio.sleep(timeout)
            return
        try:
            await asyncio.wait_for(changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        changed.clear()
        if self._cancel is not None and self._cancel.is_cancelled:
            raise RunCancelledError(self._cancel.reason or "run cancelled")

    def _begin_wait(self) -> float | None:
        if self._wait_started is not None:
            return None  # nested (an approval while paused): the outer wait counts the time
        self._wait_started = self._clock()
        return self._wait_started

    def _end_wait(self, started: float | None) -> None:
        if started is None:
            return
        self._waited_s += self._clock() - started
        self._wait_started = None


def _rejection(answer: ApprovalAnswer, timeout: float, rejected: str) -> str:
    return f"no answer within {timeout:g} s; the action was not performed" if answer.timed_out else rejected


def _clip(text: str | None, limit: int = _TEXT_CLIP) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False
