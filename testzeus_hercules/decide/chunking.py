"""Long picks: split a request's candidate rows into parts a decider takes in one call, then pick among the winners.

A step request lists candidate elements as ``state["rows"]`` (each with an ``id``) and
asks one or more choice questions whose options include every row id (``target``:
``md_<n>`` options plus ``no_match``). When such a request holds more text than
``max_text_bytes``, or a row question has more options than ``max_options``:

1. the rows are put in page order (the md number, which perception assigns in DOM
   order; rows without one keep their place) and packed greedily into parts that each
   fit the limit;
2. every part is asked as its own request (at most ``max_parallel`` at a time), with
   the same state, questions and images, and only its own rows as options;
3. the rows the parts chose (their winners) are asked once more, in page order, with
   their own text, location and the request's images. That final answer is the answer.
   When only one part chose a row, its answer is the answer: it already weighed that row
   against no_match and its part's other rows, and every other part chose none. (Asking
   one row again alone only invites a model to doubt it: a local model may then answer
   no_match for the row it just picked.)

When no part chose a row, the part least sure of that is returned as the answer, so
``no_match`` keeps the most doubtful confidence and ``target_absent`` stays honest.
A part or final request that still does not fit is split again. Costs and latency
are summed over every call.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from testzeus_hercules.decide.contract import MAX_CHOICE_OPTIONS, ChoiceQuestion, Decision, DecisionRequest

__all__ = ["DEFAULT_MAX_PARALLEL", "ChunkPlan", "Decide", "decide_in_chunks", "plan_chunks"]

DEFAULT_MAX_PARALLEL = 4
_MD = re.compile(r"md_(\d+)")
_PRIMARY = "target"
# Parts are packed to this share of the limit: the per-row size estimate is close, not exact.
_PACKING_SHARE = 0.97
_MAX_DEPTH = 4

Decide = Callable[[DecisionRequest], Awaitable[Decision]]


@dataclass(frozen=True)
class ChunkPlan:
    """How one oversized request is split: the row questions and its rows, in page order, per part."""

    row_questions: tuple[str, ...]
    parts: tuple[tuple[dict[str, Any], ...], ...]


def _rows(request: DecisionRequest) -> list[dict[str, Any]]:
    rows = request.state.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]


def _row_questions(request: DecisionRequest, ids: set[str]) -> tuple[str, ...]:
    """Choice questions whose options include every row id (the questions that pick a row)."""
    return tuple(qid for qid, question in request.questions.items() if isinstance(question, ChoiceQuestion) and ids and ids <= set(question.criteria))


def _page_order(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, row = item
        found = _MD.fullmatch(row["id"])
        return (int(found.group(1)), index) if found else (index, index)

    return [row for _, row in sorted(enumerate(rows), key=key)]


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def subset_request(request: DecisionRequest, rows: Sequence[dict[str, Any]], row_questions: Sequence[str]) -> DecisionRequest:
    """``request`` with only ``rows`` in state and as options of the row questions (other options kept)."""
    all_ids = {row["id"] for row in _rows(request)}
    keep = {row["id"] for row in rows}
    wire = request.to_wire()
    wire["state"] = {**wire["state"], "rows": [dict(row) for row in rows]}
    for qid in row_questions:
        question = wire["questions"][qid]
        question["criteria"] = {name: text for name, text in question["criteria"].items() if name in keep or name not in all_ids}
    return DecisionRequest.model_validate(wire)


def _too_big(request: DecisionRequest, row_questions: Sequence[str], max_text_bytes: int, max_options: int) -> bool:
    if request.text_size_bytes() > max_text_bytes:
        return True
    return any(len(request.questions[qid].criteria) > max_options for qid in row_questions)  # type: ignore[union-attr]


def plan_chunks(request: DecisionRequest, *, max_text_bytes: int, max_options: int = MAX_CHOICE_OPTIONS) -> ChunkPlan | None:
    """How to split ``request``, or None when it fits in one call or has no rows to split."""
    rows = _rows(request)
    row_questions = _row_questions(request, {row["id"] for row in rows})
    if len(rows) < 2 or not row_questions or not _too_big(request, row_questions, max_text_bytes, max_options):
        return None
    ordered = _page_order(rows)
    criteria = {qid: request.questions[qid].criteria for qid in row_questions}  # type: ignore[union-attr]
    extras = max(len([name for name in options if name not in {row["id"] for row in rows}]) for options in criteria.values())
    costs = [_size(row) + sum(_size({row["id"]: options.get(row["id"])}) for options in criteria.values()) for row in ordered]
    base = max(request.text_size_bytes() - sum(costs), 0)
    budget = max(int(max_text_bytes * _PACKING_SHARE) - base, 1)
    per_part = max(max_options - extras, 1)
    parts: list[list[dict[str, Any]]] = [[]]
    used = 0
    for row, cost in zip(ordered, costs):
        if parts[-1] and (used + cost > budget or len(parts[-1]) >= per_part):
            parts.append([])
            used = 0
        parts[-1].append(row)
        used += cost
    return ChunkPlan(row_questions=row_questions, parts=tuple(tuple(part) for part in parts))


def _primary(row_questions: Sequence[str]) -> str:
    return _PRIMARY if _PRIMARY in row_questions else row_questions[0]


async def decide_in_chunks(
    decide: Decide,
    request: DecisionRequest,
    *,
    max_text_bytes: int,
    max_options: int = MAX_CHOICE_OPTIONS,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    engine: str | None = None,
    _depth: int = 0,
    _limit: asyncio.Semaphore | None = None,
) -> Decision:
    """Answer ``request`` with ``decide``, splitting its rows into parts when it is too large for one call."""
    plan = plan_chunks(request, max_text_bytes=max_text_bytes, max_options=max_options) if _depth < _MAX_DEPTH else None
    if plan is None:
        return await decide(request)
    started = time.perf_counter()
    limit = _limit or asyncio.Semaphore(max(1, max_parallel))
    options = {"max_text_bytes": max_text_bytes, "max_options": max_options, "max_parallel": max_parallel, "engine": engine, "_depth": _depth + 1, "_limit": limit}

    async def ask(part: Sequence[dict[str, Any]]) -> Decision:
        sub = subset_request(request, part, plan.row_questions)
        if len(part) > 1 and _too_big(sub, plan.row_questions, max_text_bytes, max_options):
            return await decide_in_chunks(decide, sub, **options)  # type: ignore[arg-type]
        async with limit:
            return await decide(sub)

    decisions = await _all_or_cancel([ask(part) for part in plan.parts])
    primary = _primary(plan.row_questions)
    by_id = {row["id"]: row for part in plan.parts for row in part}
    winners = _page_order([by_id[choice] for choice in dict.fromkeys(decision.choice(primary).choice for decision in decisions) if choice in by_id])
    if not winners:
        final = min(decisions, key=lambda decision: decision.choice(primary).confidence)
    elif len(winners) == 1:
        final = max((decision for decision in decisions if decision.choice(primary).choice == winners[0]["id"]), key=lambda decision: decision.choice(primary).confidence)
    elif len(winners) == len(by_id):
        async with limit:
            final = await decide(request)  # nothing was narrowed down (one row per part): ask the whole request once
        decisions.append(final)
    else:
        final = await decide_in_chunks(decide, subset_request(request, winners, plan.row_questions), **options)  # type: ignore[arg-type]
        decisions.append(final)
    return _merged(final, decisions, request, started, engine)


async def _all_or_cancel(calls: Sequence[Awaitable[Decision]]) -> list[Decision]:
    """Run ``calls`` together; when one fails, cancel the rest (no more page content is sent) and raise its error.

    Every task is awaited before returning, so no exception is left unretrieved."""
    tasks = [asyncio.ensure_future(call) for call in calls]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except BaseException:  # this coroutine was cancelled: take every part down with it
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if pending:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    failed = next((task for task in tasks if task in done and not task.cancelled() and task.exception() is not None), None)
    if failed is not None:
        for task in done:
            if not task.cancelled():
                task.exception()  # mark every failure as retrieved
        raise failed.exception()  # type: ignore[misc]
    return [task.result() for task in tasks]


def _merged(final: Decision, decisions: Sequence[Decision], request: DecisionRequest, started: float, engine: str | None) -> Decision:
    costs = [decision.cost_usd for decision in decisions if decision.cost_usd is not None]
    merged = Decision(
        answers=dict(final.answers),
        engine=engine or final.engine,
        model_version=final.model_version,
        latency_ms=max(int((time.perf_counter() - started) * 1000), 0),
        cost_usd=round(sum(costs), 10) if costs else None,
        cached=all(decision.cached for decision in decisions),
    )
    return merged.validate_against(request)
