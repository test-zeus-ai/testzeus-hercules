"""Checking a plan's success criteria on the settled page after a plan step (see :mod:`testzeus_hercules.cases.plan`).

Each check form runs in code against what the page shows; only ``judged`` asks a model:

* ``text_visible`` / ``text_absent`` / ``url_matches``: the page's text and address, as the
  step's own checks are run (:func:`testzeus_hercules.engine.verify.run_check`), within a
  short wait;
* ``element_state``: the page's elements as perceived: an open dialog (modal or not) whose
  name holds the criterion's target (``open``/``closed``), or the element whose name or label
  is the target, exactly (``visible``, ``hidden``, ``enabled``, ``disabled``, ``checked``,
  ``unchecked``, ``expanded``, ``collapsed``);
* ``field_value``: the field whose label is the target, exactly, and the value it holds;
* ``count``: how many visible elements show the target text;
* ``judged``: the decider judges the criterion by its meaning on the page's text and, when
  images may be sent, its screenshot (``visual`` criteria on the screenshot only).

When the element a criterion names cannot be found exactly, the criterion is judged by the
decider instead of guessed in code. A judgement that is not confident either way, or a check
that cannot run, is ``unverified``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from testzeus_hercules.cases.plan import Criterion
from testzeus_hercules.decide.contract import NoulAnswer
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.kernel.run_context import RunCancelledError
from testzeus_hercules.decide.questions import offered_elements
from testzeus_hercules.engine.judge import judge
from testzeus_hercules.engine.verify import Check, run_check

logger = logging.getLogger(__name__)

__all__ = ["CRITERION_PURPOSE", "Evaluation", "evaluate"]

CRITERION_PURPOSE = "criterion"
_VALUE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "listbox", "slider"})
_MAX_COUNT = 200
_CLIP = 300
Status = Literal["passed", "failed", "unverified"]


@dataclass(frozen=True)
class Evaluation:
    status: Status
    actual: str
    # The decision record when a model judged it (for the step's decisions.jsonl).
    decision: dict[str, Any] | None = None


def _flat(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _CLIP else flat[: _CLIP - 1] + "…"


def _names(element: Any) -> set[str]:
    attrs = getattr(element, "attrs", {}) or {}
    return {name for name in (_flat(getattr(element, "name", "")), _flat(attrs.get("label")), _flat(attrs.get("aria-label")), _flat(attrs.get("placeholder"))) if name}


def _named(snapshot: Any, target: str, roles: frozenset[str] | None = None) -> list[Any]:
    """The offered elements whose accessible name or label is ``target`` (whitespace and case aside); when none is, the
    one element (of ``roles``, when given) whose name holds ``target``, if exactly one does ("Express shipping" for a radio
    named "Express shipping ($15.00)"). Never a guess between several."""
    wanted = _flat(target)
    if not wanted:
        return []
    offered = [element for element in offered_elements(snapshot) if roles is None or getattr(element, "role", "") in roles]
    exact = [element for element in offered if wanted in _names(element)]
    if exact:
        return exact
    partial = [element for element in offered if any(wanted in name for name in _names(element))]
    return partial if len(partial) == 1 else []


# The roles an element state is read from: a checked state, an expanded state, a value.
_CHECKABLE = frozenset({"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio", "option", "tab"})
_STATE_ROLES = {"checked": _CHECKABLE, "unchecked": _CHECKABLE}


def _dialogs(snapshot: Any) -> list[str]:
    """The names of the dialogs open on the page: the modal one and any dialog an offered element is in."""
    names: list[str] = []
    modal = getattr(snapshot, "modal", None)
    if modal is not None:
        names.append(str(modal))
    for element in getattr(snapshot, "elements", ()):
        dialog = getattr(element, "dialog", None)
        if dialog is not None and str(dialog) not in names:
            names.append(str(dialog))
        if getattr(element, "role", "") in ("dialog", "alertdialog") and getattr(element, "name", "") not in names:
            names.append(str(getattr(element, "name", "")))
    return names


async def evaluate(
    criterion: Criterion,
    *,
    page: Any,
    actions: Any,
    snapshot: Callable[[], Awaitable[Any]],
    decider: DecisionEngine,
    resolve: Callable[[str], str],
    redact: Callable[[str], str],
    images: bool,
    step: int,
    timeout_ms: int = 2_000,
) -> Evaluation:
    """Check ``criterion`` on the page as it is now (see the module docstring); never raises, but for a cancelled run."""
    try:
        return await _evaluate(criterion, page=page, actions=actions, snapshot=snapshot, decider=decider, resolve=resolve, redact=redact, images=images, step=step, timeout_ms=timeout_ms)
    except RunCancelledError:
        raise
    except Exception as exc:  # a check that cannot run is not a verdict
        logger.info("criterion %s could not be checked (%s)", criterion.id, type(exc).__name__)
        return Evaluation("unverified", redact(f"could not be checked ({type(exc).__name__})"))


async def _evaluate(
    criterion: Criterion,
    *,
    page: Any,
    actions: Any,
    snapshot: Callable[[], Awaitable[Any]],
    decider: DecisionEngine,
    resolve: Callable[[str], str],
    redact: Callable[[str], str],
    images: bool,
    step: int,
    timeout_ms: int,
) -> Evaluation:
    expected = resolve(criterion.expected) if criterion.expected else ""
    target = resolve(criterion.target) if criterion.target else ""
    options = {"page": page, "actions": actions, "step": step, "timeout_ms": timeout_ms, "redact": redact}
    if criterion.check.startswith("file_"):
        return Evaluation("unverified", "a downloaded file is checked by the step's own outcome, not as a criterion of an action")
    if criterion.check in ("text_visible", "text_absent", "url_matches"):
        kind = {"text_visible": "text", "text_absent": "not_text", "url_matches": "url"}[criterion.check]
        entry = await run_check(Check(kind=kind, expected=expected, match=criterion.match), **options)  # type: ignore[arg-type]
        return Evaluation(entry.verdict, entry.actual)
    if criterion.check == "judged":
        return await _judged(criterion, page=page, actions=actions, snapshot=snapshot, decider=decider, resolve=resolve, redact=redact, images=images)
    view = await snapshot()
    if criterion.check == "element_state" and criterion.state in ("open", "closed"):
        open_ = [name for name in _dialogs(view) if not target or _flat(target) in _flat(name) or (_flat(name) and _flat(name) in _flat(target))]
        holds = bool(open_) == (criterion.state == "open")
        seen = f"open dialog(s): {', '.join(repr(name) for name in _dialogs(view)) or 'none'}"
        return Evaluation("passed" if holds else "failed", redact(_clip(seen)))
    if criterion.check == "count":
        return await _count(criterion, page, target, redact)
    roles = _VALUE_ROLES if criterion.check == "field_value" else _STATE_ROLES.get(str(criterion.state))
    found = _named(view, target, roles)
    if criterion.check == "element_state" and criterion.state in ("visible", "hidden"):
        visible = bool(found) or bool(await actions.text_visible(page, target, timeout_ms=min(timeout_ms, 1_000)))
        holds = visible == (criterion.state == "visible")
        return Evaluation("passed" if holds else "failed", "visible" if visible else "no visible element shows it")
    if not found:  # the element is not named exactly as the criterion says: a model judges it by meaning, never a guess
        return await _judged(criterion, page=page, actions=actions, snapshot=snapshot, decider=decider, resolve=resolve, redact=redact, images=images)
    element = found[0]
    if criterion.check == "field_value":
        wanted = _flat(resolve(criterion.value or ""))
        candidates = [item for item in found if getattr(item, "role", "") in _VALUE_ROLES] or found
        held = [str(getattr(item, "value", "") or "") for item in candidates]
        holds = any(_flat(value) == wanted for value in held)
        return Evaluation("passed" if holds else "failed", redact(_clip(f"holds {', '.join(repr(value) for value in held) or 'nothing'}")))
    state = criterion.state
    attrs = getattr(element, "attrs", {}) or {}
    facts = {
        "enabled": not getattr(element, "disabled", False),
        "disabled": bool(getattr(element, "disabled", False)),
        "checked": getattr(element, "checked", None) is True,
        "unchecked": getattr(element, "checked", None) is False,
        "expanded": attrs.get("aria-expanded") == "true",
        "collapsed": attrs.get("aria-expanded") == "false",
    }
    if state in ("checked", "unchecked") and getattr(element, "checked", None) is None:
        return await _judged(criterion, page=page, actions=actions, snapshot=snapshot, decider=decider, resolve=resolve, redact=redact, images=images)
    if state in ("expanded", "collapsed") and "aria-expanded" not in attrs:
        return await _judged(criterion, page=page, actions=actions, snapshot=snapshot, decider=decider, resolve=resolve, redact=redact, images=images)
    holds = bool(facts.get(str(state)))
    described = [name for name, value in facts.items() if value]
    return Evaluation("passed" if holds else "failed", redact(_clip(f"{getattr(element, 'role', 'element')} {getattr(element, 'name', '')!r}: {', '.join(described) or 'no state'}")))


async def _count(criterion: Criterion, page: Any, target: str, redact: Callable[[str], str]) -> Evaluation:
    locator = page.get_by_text(target)
    total = min(await locator.count(), _MAX_COUNT)
    visible = 0
    for index in range(total):
        if await locator.nth(index).is_visible():
            visible += 1
    wanted = criterion.count or 0
    holds = visible == wanted if criterion.op == "eq" else visible >= wanted if criterion.op == "gte" else visible <= wanted
    return Evaluation("passed" if holds else "failed", redact(f"{visible} visible element(s) show it"))


async def _judged(
    criterion: Criterion,
    *,
    page: Any,
    actions: Any,
    snapshot: Callable[[], Awaitable[Any]],
    decider: DecisionEngine,
    resolve: Callable[[str], str],
    redact: Callable[[str], str],
    images: bool,
) -> Evaluation:
    text = redact(resolve(criterion.text))
    view = await snapshot()
    if criterion.visual and not images:
        return Evaluation("unverified", "not verified: how the page looks needs a decider that can see a screenshot")
    page_text = "" if criterion.visual else redact(await actions.page_text(page))
    if not criterion.visual and criterion.check in ("element_state", "field_value"):
        # The page's text shows no control states: the judge also gets the controls as perceived (checked, values, disabled).
        observation = getattr(view, "observation_text", None)
        if callable(observation):
            page_text = redact(observation()) + "\n\nPAGE TEXT:\n" + page_text
    judgement = await judge(
        decider,
        [text],
        purpose=CRITERION_PURPOSE,
        task=("A test's plan expects the web page, as it is now, to show one outcome after an action. Judge by meaning whether the page shows it. " "Judge only this expectation against this page."),
        url=str(getattr(view, "url", "") or ""),
        title=str(getattr(view, "title", "") or ""),
        page_text=page_text,
        screenshot=getattr(view, "plain_jpeg", None) if images else None,
    )
    probability, verdict = judgement.probabilities[0], judgement.verdicts[0]
    answer = judgement.decision.answers.get("e1")
    record = {
        "purpose": CRITERION_PURPOSE,
        "criterion": criterion.id,
        "engine": judgement.decision.engine,
        "latency_ms": judgement.decision.latency_ms,
        "model": judgement.decision.model_version,
        "cost_usd": judgement.decision.cost_usd,
        "probability": round(probability, 4) if isinstance(answer, NoulAnswer) else None,
        "decision": judgement.decision.model_dump(mode="json"),
    }
    unsure = "" if verdict != "unverified" else " (not confident either way)"
    return Evaluation(verdict, f"judged by {judgement.decision.engine}: {probability:.0%} likely to hold{unsure}", record)
