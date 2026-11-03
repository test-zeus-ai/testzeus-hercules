"""Execution-tracing contract tests (offline, no network, no LLM).

With every tracing environment variable unset, a graph turn must attach no
tracer and therefore record no traces. With an in-memory run collector active
(``collect_runs`` — no tracing backend contacted), the same turn must expose a
root run named "hercules-agent" attributed with the scenario id, so an
opt-in tracing backend receives a complete, correctly-attributed trace.

The graph turn uses scripted fake chat models exactly like the core loop-safety
tests; nothing here talks to a model provider.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("langchain_core.tracers.context")
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.tracers.context import collect_runs  # noqa: E402
from testzeus_hercules.core.simple_hercules import SimpleHercules  # noqa: E402

STAKE_ID = "observability-stake"


class _TerminalPlannerLLM:
    """Planner LLM that immediately emits a valid assertion-shaped decision."""

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(
            content=json.dumps(
                {
                    "terminate": "yes",
                    "final_response": "done",
                    "is_assert": True,
                    "is_passed": True,
                    "assert_summary": "ok",
                }
            )
        )


class _IdleNavLLM:
    """Nav-agent LLM that terminates immediately (never reached in this turn)."""

    def bind_tools(self, tools: list[Any]) -> "_IdleNavLLM":
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content="current_output: done\n##TERMINATE TASK##")


def _make_hercules() -> SimpleHercules:
    """Compiled graph over stubbed agents (offline, scripted decisions)."""
    hercules = SimpleHercules(stake_id=STAKE_ID, browser_nav_max_chat_round=2)
    hercules.agents_map = {
        "planner_agent": SimpleNamespace(
            system_message="planner system",
            llm=_TerminalPlannerLLM(),
            on_planner_message=lambda _content: None,
        )
    }
    for name in (
        "browser_nav_agent",
        "api_nav_agent",
        "sec_nav_agent",
        "sql_nav_agent",
        "time_keeper_nav_agent",
        "mcp_nav_agent",
        "executor_nav_agent",
    ):
        hercules.agents_map[name] = SimpleNamespace(
            agent_name=name,
            system_message=f"{name} system",
            llm=_IdleNavLLM(),
            tools=[],
        )
    hercules._graph = hercules._build_graph()
    return hercules


def test_no_tracer_is_attached_when_tracing_env_is_unset(monkeypatch) -> None:
    """With tracing env unset, a graph turn records zero traces.

    The in-memory collector is used only as the observation channel: it proves
    the turn executed runs while asserting that no tracing backend tracer was
    ever attached (the collector itself is not a tracing backend).
    """
    tracer_instantiations: list[Any] = []
    try:
        import langchain_core.tracers.langchain as tracers_langchain

        original_init = tracers_langchain.LangChainTracer.__init__

        def _spy_init(self: Any, *args: Any, **kwargs: Any) -> None:
            tracer_instantiations.append(self)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(tracers_langchain.LangChainTracer, "__init__", _spy_init)

        from langchain_core.tracers import context as tracing_context

        is_enabled = getattr(tracing_context, "_tracing_v2_is_enabled", None)
        if is_enabled is not None:
            assert is_enabled() is False, "tracing must be off when the env vars are unset"
    except ImportError:  # pragma: no cover - guarded for older langchain-core
        pass

    async def run() -> None:
        with collect_runs() as cb:
            result = await _make_hercules().process_command("tracing off task")
            assert result is not None and result.terminate == "yes"
        assert cb.traced_runs, "the turn must have executed runs to observe"
        assert tracer_instantiations == [], "no tracer may be attached when tracing env is unset"

    asyncio.run(run())


def test_traced_graph_turn_has_named_and_attributed_root_run() -> None:
    """With in-memory collection active, the root run is named and attributed."""

    async def run() -> None:
        with collect_runs() as cb:
            result = await _make_hercules().process_command("traced task")
            assert result is not None and result.terminate == "yes"

        runs = list(cb.traced_runs)
        assert runs, "the traced turn must record at least the root run"
        root = runs[0]
        assert root.name == "hercules-agent"
        metadata = (root.extra or {}).get("metadata") or {}
        assert metadata.get("stake_id") == STAKE_ID
        # Every recorded run must belong to a single connected trace rooted at
        # the named run.
        if len(runs) > 1:
            root_trace_id = getattr(root, "trace_id", None)
            assert all(getattr(run, "trace_id", None) == root_trace_id for run in runs[1:])

    asyncio.run(run())
