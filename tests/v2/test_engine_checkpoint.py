"""SqliteCheckpointer: LangGraph checkpoints that survive the process, so a run graph resumes after a crash."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
from pathlib import Path
from typing import TypedDict

from langgraph.constants import END, START
from langgraph.graph.state import StateGraph

from testzeus_hercules.cases.automation import AutomationStep
from testzeus_hercules.cases.models import QualityPage, StepResult
from testzeus_hercules.cases.plan import Amendment, CriterionResult
from testzeus_hercules.engine.checkpoint import ALLOWED_TYPES, SqliteCheckpointer


class State(TypedDict, total=False):
    n: int
    results: tuple[StepResult, ...]


class Boom(RuntimeError):
    pass


def build(saver: SqliteCheckpointer, crash_at: int | None, seen: list[int]):  # type: ignore[no-untyped-def]
    async def work(state: State) -> dict[str, object]:
        n = state["n"]
        seen.append(n)
        if crash_at is not None and n == crash_at:
            raise Boom("worker died")
        return {"n": n + 1, "results": tuple(state.get("results", ())) + (StepResult(index=n, action=f"step {n}", status="passed"),)}

    graph = StateGraph(State)
    graph.add_node("work", work)
    graph.add_edge(START, "work")
    graph.add_conditional_edges("work", lambda state: END if state["n"] > 4 else "work", ["work", END])
    return graph.compile(checkpointer=saver)


def test_a_new_process_resumes_from_the_last_completed_node(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints" / "r_1.db"
    config = {"configurable": {"thread_id": "r_1"}}
    first: list[int] = []

    async def crash() -> None:
        saver = SqliteCheckpointer(path)
        try:
            await build(saver, crash_at=3, seen=first).ainvoke({"n": 1}, config)
        except Boom:
            pass
        finally:
            saver.close()

    asyncio.run(crash())
    assert first == [1, 2, 3]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    second: list[int] = []

    async def resume() -> State:
        saver = SqliteCheckpointer(path)
        try:
            graph = build(saver, crash_at=None, seen=second)
            snapshot = await graph.aget_state(config)
            assert snapshot.next == ("work",) and snapshot.values["n"] == 3
            return await graph.ainvoke(None, config)
        finally:
            saver.close()

    final = asyncio.run(resume())
    assert second == [3, 4]  # steps 1 and 2 are not repeated
    assert final["n"] == 5
    assert [result.index for result in final["results"]] == [1, 2, 3, 4]
    assert all(isinstance(result, StepResult) for result in final["results"])


def test_delete_thread_removes_rows(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    config = {"configurable": {"thread_id": "r_2"}}

    async def run() -> None:
        saver = SqliteCheckpointer(path)
        await build(saver, crash_at=None, seen=[]).ainvoke({"n": 1}, config)
        await saver.adelete_thread("r_2")
        saver.close()

    asyncio.run(run())
    saver = SqliteCheckpointer(path)
    try:
        assert saver.get_tuple(config) is None
    finally:
        saver.close()


class PlanState(TypedDict, total=False):
    n: int
    plan_results: tuple[CriterionResult, ...]
    amendments: tuple[Amendment, ...]


def build_plan(saver: SqliteCheckpointer, crash_at: int | None):  # type: ignore[no-untyped-def]
    async def work(state: PlanState) -> dict[str, object]:
        n = state["n"]
        if crash_at is not None and n == crash_at:
            raise Boom("worker died")
        result = CriterionResult(criterion=f"{n}.1.c1", plan_step=f"{n}.1", step=n, origin="author", check="text_visible", text="Saved", status="passed")
        amendment = Amendment(step=n, kind="reread", source="model", reason="page changed")
        return {"n": n + 1, "plan_results": tuple(state.get("plan_results", ())) + (result,), "amendments": tuple(state.get("amendments", ())) + (amendment,)}

    graph = StateGraph(PlanState)
    graph.add_node("work", work)
    graph.add_edge(START, "work")
    graph.add_conditional_edges("work", lambda state: END if state["n"] > 4 else "work", ["work", END])
    return graph.compile(checkpointer=saver)


def test_criteria_and_amendments_come_back_as_models_on_resume(tmp_path: Path) -> None:
    path = tmp_path / "plan.db"
    config = {"configurable": {"thread_id": "r_3"}}

    async def crash() -> None:
        saver = SqliteCheckpointer(path)
        try:
            await build_plan(saver, crash_at=3).ainvoke({"n": 1}, config)
        except Boom:
            pass
        finally:
            saver.close()

    async def resume() -> PlanState:
        saver = SqliteCheckpointer(path)
        try:
            return await build_plan(saver, crash_at=None).ainvoke(None, config)
        finally:
            saver.close()

    asyncio.run(crash())
    final = asyncio.run(resume())
    assert [result.criterion for result in final["plan_results"]] == ["1.1.c1", "2.1.c1", "3.1.c1", "4.1.c1"]
    assert all(isinstance(result, CriterionResult) for result in final["plan_results"])
    assert all(isinstance(amendment, Amendment) for amendment in final["amendments"])


def test_every_state_type_and_its_nested_models_are_allowed() -> None:
    allowed = set(ALLOWED_TYPES)
    for cls in (StepResult, CriterionResult, Amendment, AutomationStep, QualityPage):
        assert (cls.__module__, cls.__qualname__) in allowed
    assert ("testzeus_hercules.cases.automation", "AutomationAction") in allowed


def test_only_the_latest_checkpoint_and_its_parent_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "prune.db"
    config = {"configurable": {"thread_id": "r_4"}}

    async def run() -> None:
        saver = SqliteCheckpointer(path)
        try:
            await build(saver, crash_at=None, seen=[]).ainvoke({"n": 1}, config)
        finally:
            saver.close()

    asyncio.run(run())
    db = sqlite3.connect(str(path))
    try:
        assert db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2
        # One value per channel per kept checkpoint at most, not one per step.
        assert db.execute("SELECT COUNT(*) FROM blobs WHERE channel = 'results'").fetchone()[0] <= 2
    finally:
        db.close()
    saver = SqliteCheckpointer(path)
    try:
        values = saver.get_tuple(config).checkpoint["channel_values"]
        assert values["n"] == 5 and [result.index for result in values["results"]] == [1, 2, 3, 4]
    finally:
        saver.close()
