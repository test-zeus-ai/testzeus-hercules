"""The order a case's steps run in: as written, except where a field depends on a field a later step sets.

The case reading (a model, see :mod:`testzeus_hercules.engine.intent`) may say that a step's
field ``needs`` other steps' fields set first (a region list that depends on the country).
Deciding that is the model's job; this module only checks the answer against the case's
structure and executes it:

* a *block* is a run of consecutive field-entry steps: steps the reading reads as fills
  and selects only, with no expected outcome to check (no check, click, navigation or save
  between them), at least two long;
* a dependency counts only between two steps of the same block; any other is ignored and
  noted;
* each block runs in a stable topological order of its dependencies (the written order
  wherever the dependencies allow it); a block whose dependencies form a cycle runs as
  written, with a note.

Steps keep their numbers: a step that runs earlier is still step 20, and its result is
reported as step 20 with the sequence it ran in.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from testzeus_hercules.cases.gherkin import VERIFY_ACTION
from testzeus_hercules.cases.models import Step
from testzeus_hercules.engine.intent import CaseReading

__all__ = ["ENTRY_KINDS", "StepOrder", "entry_blocks", "plan_order"]

# What a field-entry step does: fill a field or choose an option in one.
ENTRY_KINDS = frozenset({"fill", "select"})


@dataclass(frozen=True)
class StepOrder:
    """The steps in the order they run (1-based numbers), and per step the notes that explain a change."""

    order: tuple[int, ...]
    notes: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    # Per step: the plan record for its plan.jsonl (only steps whose order or dependencies were considered).
    records: Mapping[int, dict[str, object]] = field(default_factory=dict)

    @property
    def reordered(self) -> bool:
        return list(self.order) != sorted(self.order)


def _is_entry(number: int, step: Step, reading: CaseReading) -> bool:
    if step.expected is not None or step.action.strip().lower() == VERIFY_ACTION or step.api is not None or step.sql is not None or step.quality is not None or step.security is not None:
        return False
    intent = reading.get(number, "action", step.action)
    return intent is not None and all(part.kind in ENTRY_KINDS for part in intent.parts())


def entry_blocks(steps: Sequence[Step], reading: CaseReading) -> list[tuple[int, ...]]:
    """The case's blocks of consecutive field-entry steps (step numbers, at least two per block)."""
    blocks: list[tuple[int, ...]] = []
    current: list[int] = []
    for number, step in enumerate(steps, start=1):
        if _is_entry(number, step, reading):
            current.append(number)
            continue
        if len(current) > 1:
            blocks.append(tuple(current))
        current = []
    if len(current) > 1:
        blocks.append(tuple(current))
    return blocks


def _target(number: int, steps: Sequence[Step], reading: CaseReading) -> str:
    intent = reading.get(number, "action", steps[number - 1].action)
    return (intent.target or "").strip() if intent is not None else ""


def _field(number: int, steps: Sequence[Step], reading: CaseReading) -> str:
    target = _target(number, steps, reading)
    return f'the "{target}" field' if target else f"step {number}'s field"


def _sorted_block(block: tuple[int, ...], needs: Mapping[int, tuple[int, ...]]) -> tuple[int, ...] | None:
    """``block`` in a stable topological order of ``needs`` (step -> steps it needs first); None on a cycle."""
    waiting = {number: set(needs.get(number, ())) for number in block}
    ready = [number for number in block if not waiting[number]]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        number = heapq.heappop(ready)
        order.append(number)
        for other in block:
            if number in waiting[other]:
                waiting[other].discard(number)
                if not waiting[other]:
                    heapq.heappush(ready, other)
    return tuple(order) if len(order) == len(block) else None


def plan_order(steps: Sequence[Step], reading: CaseReading) -> StepOrder:
    """The order to run ``steps`` in, given the dependencies the case reading states (validated as described above)."""
    order = list(range(1, len(steps) + 1))
    if not reading.needs:
        return StepOrder(order=tuple(order))
    notes: dict[int, list[str]] = {}
    records: dict[int, dict[str, object]] = {}
    blocks = entry_blocks(steps, reading)
    block_of = {number: block for block in blocks for number in block}
    kept_needs: dict[int, tuple[int, ...]] = {}
    for number, needed in sorted(reading.needs.items()):
        if not 1 <= number <= len(steps):
            continue
        block = block_of.get(number)
        kept = tuple(other for other in needed if block is not None and other in block and other != number)
        ignored = [other for other in needed if other not in kept and other != number]
        if kept:
            kept_needs[number] = kept
        if kept or ignored:
            records[number] = {"stage": "order", "needs": list(kept), **({"ignored": ignored} if ignored else {})}
    for block in blocks:
        needs = {number: needed for number, needed in kept_needs.items() if number in block}
        if not needs:
            continue
        sorted_block = _sorted_block(block, needs)
        if sorted_block is None:
            for number in block:
                notes.setdefault(number, []).append(f"steps {block[0]}-{block[-1]} run in the written order: the case reading says their fields depend on each other in a cycle")
                records.setdefault(number, {"stage": "order"})["cycle"] = list(block)
            continue
        start = order.index(block[0])
        order[start : start + len(block)] = sorted_block
        for number, needed in needs.items():
            for other in needed:
                if other > number:  # written after the step that needs it: it now runs before
                    notes.setdefault(other, []).append(f"run before step {number} because {_field(number, steps, reading)} depends on it")
                    notes.setdefault(number, []).append(f"run after step {other}, whose field ({_target(other, steps, reading) or 'its field'}) it depends on")
    sequence = {number: position for position, number in enumerate(order, start=1)}
    for number, record in records.items():
        record["sequence"] = sequence[number]
    for number in notes:
        records.setdefault(number, {"stage": "order"})["sequence"] = sequence[number]
    return StepOrder(order=tuple(order), notes={number: tuple(items) for number, items in notes.items()}, records=records)
