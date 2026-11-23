"""The order steps run in: blocks of field entries ordered by the dependencies the case reading states, validated."""

from __future__ import annotations

from typing import Any

from testzeus_hercules.cases.models import Step
from testzeus_hercules.engine.intent import CaseReading, StepIntent
from testzeus_hercules.engine.ordering import entry_blocks, plan_order


def _intent(kind: str, target: str, value: str | None = None) -> StepIntent:
    return StepIntent(kind=kind, target=target, value=value)  # type: ignore[arg-type]


def _case(*rows: tuple[str, StepIntent, str | None], needs: dict[int, tuple[int, ...]] | None = None) -> tuple[list[Step], CaseReading]:
    steps = [Step(action=text, expected=expected) for text, _, expected in rows]
    intents: dict[Any, Any] = {(number, "action"): (text, intent) for number, (text, intent, _) in enumerate(rows, start=1)}
    return steps, CaseReading(intents=intents, source="model", needs=needs or {})


OPEN = ("open the form", StepIntent(kind="navigate", url="/form"), None)
STATE = ('enter "Ontario" in "State"', _intent("select", "State", "Ontario"), None)
CITY = ('enter "Ottawa" in "City"', _intent("fill", "City", "Ottawa"), None)
COUNTRY = ('choose "Canada" in "Country"', _intent("select", "Country", "Canada"), None)
SAVE = ('click "Save"', _intent("click", "Save"), None)


def test_without_dependencies_steps_run_as_written() -> None:
    steps, reading = _case(OPEN, STATE, CITY, COUNTRY, SAVE)

    planned = plan_order(steps, reading)

    assert planned.order == (1, 2, 3, 4, 5) and not planned.reordered and not planned.notes


def test_a_block_runs_in_a_stable_order_that_satisfies_its_dependencies() -> None:
    steps, reading = _case(OPEN, STATE, CITY, COUNTRY, SAVE, needs={2: (4,)})

    planned = plan_order(steps, reading)

    assert entry_blocks(steps, reading) == [(2, 3, 4)]
    assert planned.order == (1, 3, 4, 2, 5), "the city keeps its place before the state; the country moves just before the state"
    assert planned.notes[4] == ('run before step 2 because the "State" field depends on it',)
    assert planned.notes[2] == ("run after step 4, whose field (Country) it depends on",)
    assert planned.records[2] == {"stage": "order", "needs": [4], "sequence": 4}
    assert planned.records[4] == {"stage": "order", "sequence": 3}


def test_dependencies_outside_the_block_are_ignored() -> None:
    checked = ('enter "Ottawa" in "City"', _intent("fill", "City", "Ottawa"), "the city is shown")
    steps, reading = _case(OPEN, STATE, checked, COUNTRY, SAVE, needs={2: (4, 5, 1)})

    planned = plan_order(steps, reading)

    assert entry_blocks(steps, reading) == [], "a step with an expected outcome ends a block"
    assert planned.order == (1, 2, 3, 4, 5)
    assert planned.records[2] == {"stage": "order", "needs": [], "ignored": [4, 5, 1], "sequence": 2}


def test_a_cycle_keeps_the_written_order_with_a_note() -> None:
    steps, reading = _case(OPEN, STATE, COUNTRY, SAVE, needs={2: (3,), 3: (2,)})

    planned = plan_order(steps, reading)

    assert planned.order == (1, 2, 3, 4)
    assert planned.notes[2] == ("steps 2-3 run in the written order: the case reading says their fields depend on each other in a cycle",)
    assert planned.records[3]["cycle"] == [2, 3]


def test_an_earlier_dependency_changes_nothing() -> None:
    steps, reading = _case(OPEN, COUNTRY, STATE, SAVE, needs={3: (2,)})

    planned = plan_order(steps, reading)

    assert planned.order == (1, 2, 3, 4) and not planned.notes
