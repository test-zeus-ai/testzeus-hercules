"""LangGraph-based orchestration for Hercules test execution."""

from __future__ import annotations
import time
import asyncio
import json
import re
import traceback
from typing import Any, Dict, Literal, Optional, TypedDict

import nest_asyncio
import openai
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, StateGraph

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents.api_nav_agent import ApiNavAgent
from testzeus_hercules.core.agents.browser_nav_agent import BrowserNavAgent
from testzeus_hercules.core.agents.executor_nav_agent import ExecutorNavAgent
from testzeus_hercules.core.agents.high_level_planner_agent import PlannerAgent
from testzeus_hercules.core.agents.mcp_nav_agent import McpNavAgent
from testzeus_hercules.core.agents.sec_nav_agent import SecNavAgent
from testzeus_hercules.core.agents.sql_nav_agent import SqlNavAgent
from testzeus_hercules.core.agents.time_keeper_nav_agent import TimeKeeperNavAgent
from testzeus_hercules.core.extra_tools import *  # noqa: F403
from testzeus_hercules.core.memory.dynamic_ltm import DynamicLTM
from testzeus_hercules.core.post_process_responses import (
    final_reply_callback_planner_agent as notify_planner_messages,
)
from testzeus_hercules.core.tools import *  # noqa: F403
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.utils.llm_helper import (
    GraphChatResult,
    convert_model_config_to_langchain_format,
    create_multimodal_agent,
    get_llm_request_timeout_seconds,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.response_parser import parse_response
from testzeus_hercules.utils.timestamp_helper import get_timestamp_str
from testzeus_hercules.utils.ui_messagetype import MessageType

nest_asyncio.apply()


class AgentState(TypedDict, total=False):
    # Full planner ↔ helper conversation
    messages: list[AnyMessage]
    task: str

    # Latest parsed planner JSON fields (PlannerAgent schema)
    plan: str
    next_step: str
    target_helper: str
    terminate: str  # "yes" | "no"
    final_response: str
    is_assert: bool
    assert_summary: str
    is_passed: bool

    # Planner turn counter
    planner_turn: int

    # Token accounting
    step_token_log: list[dict[str, Any]]
    total_prompt_tokens: int
    total_completion_tokens: int
    total_steps: int
    step_timings: list[dict[str, Any]]
    completed_step_signatures: list[str]
    last_helper_response: str
    current_url: str


class SimpleHercules:
    """LangGraph orchestrator for planner and navigation helper agents."""

    def __init__(
        self,
        stake_id: str,
        save_chat_logs_to_files: bool = True,
        planner_max_chat_round: int = 500,
        browser_nav_max_chat_round: int = 10,
    ) -> None:
        self.timestamp = get_timestamp_str()
        self.planner_number_of_rounds = planner_max_chat_round
        self.nav_agent_number_of_rounds = browser_nav_max_chat_round
        self.agents_map: Dict[str, Any] = {}
        self.planner_agent_config: Optional[Dict[str, Any]] = None
        self.nav_agent_config: Optional[Dict[str, Any]] = None
        self.mem_agent_config: Optional[Dict[str, Any]] = None
        self.helper_agent_config: Optional[Dict[str, Any]] = None
        self.stake_id = stake_id
        self.save_chat_logs_to_files = save_chat_logs_to_files
        self.memory: Optional[DynamicLTM] = None
        self._graph = None
        self._last_graph_result: GraphChatResult | None = None
        self._nav_token_log: list[dict[str, Any]] = []

    @staticmethod
    def _step_signature(step: str) -> str:
        return re.sub(r"\s+", " ", step.strip().lower())

    @staticmethod
    def _helper_response_succeeded(response: str) -> bool:
        if "##TERMINATE TASK##" not in response:
            return False

        lower = response.lower()
        failure_markers = (
            "[error]",
            "[tool error]",
            "issue encountered",
            "contradict",
            "uncertain",
            "incomplete",
            "max nav rounds",
        )
        return not any(marker in lower for marker in failure_markers)

    @classmethod
    async def create(
        cls,
        stake_id: str,
        planner_agent_config: dict[str, Any],
        nav_agent_config: dict[str, Any],
        mem_agent_config: dict[str, Any],
        helper_agent_config: dict[str, Any],
        save_chat_logs_to_files: bool = True,
        planner_max_chat_round: int = 500,
        browser_nav_max_chat_round: int = 10,
    ) -> "SimpleHercules":
        logger.info(
            "Creating SimpleHercules (LangGraph), planner rounds=%s, nav rounds=%s",
            planner_max_chat_round,
            browser_nav_max_chat_round,
        )
        self = cls(
            stake_id,
            save_chat_logs_to_files=save_chat_logs_to_files,
            planner_max_chat_round=planner_max_chat_round,
            browser_nav_max_chat_round=browser_nav_max_chat_round,
        )
        self.planner_agent_config = planner_agent_config
        self.nav_agent_config = nav_agent_config
        self.mem_agent_config = mem_agent_config
        self.helper_agent_config = helper_agent_config

        from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model

        for cfg in [
            planner_agent_config,
            nav_agent_config,
            mem_agent_config,
            helper_agent_config,
        ]:
            model = cfg["model_config_params"].get("model") or cfg["model_config_params"].get("model_name")
            cfg["llm_config_params"] = adapt_llm_params_for_model(model, cfg["llm_config_params"])

        self.agents_map = await self._initialize_agents()
        self._graph = self._build_graph()
        return self

    async def _initialize_agents(self) -> dict[str, Any]:
        agents: dict[str, Any] = {}
        if self.nav_agent_config is None:
            raise ValueError("Navigation agent config is not initialized.")
        if self.planner_agent_config is None:
            raise ValueError("Planner agent config is not initialized.")
        nav_cfg = self.nav_agent_config
        nav_model = convert_model_config_to_langchain_format(nav_cfg["model_config_params"])
        nav_llm = nav_cfg["llm_config_params"]
        nav_prompt = nav_cfg.get("other_settings", {}).get("system_prompt")

        planner_model = convert_model_config_to_langchain_format(self.planner_agent_config["model_config_params"])
        agents["planner_agent"] = PlannerAgent(
            planner_model,
            self.planner_agent_config["llm_config_params"],
            self.planner_agent_config.get("other_settings", {}).get("system_prompt"),
        )
        agents["browser_nav_agent"] = BrowserNavAgent(nav_model, nav_llm, nav_prompt)
        agents["api_nav_agent"] = ApiNavAgent(nav_model, nav_llm, nav_prompt)
        agents["sec_nav_agent"] = SecNavAgent(nav_model, nav_llm, nav_prompt)
        agents["sql_nav_agent"] = SqlNavAgent(nav_model, nav_llm, nav_prompt)
        agents["time_keeper_nav_agent"] = TimeKeeperNavAgent(nav_model, nav_llm, nav_prompt)
        agents["mcp_nav_agent"] = McpNavAgent(nav_model, nav_llm, nav_prompt)
        agents["executor_nav_agent"] = ExecutorNavAgent(nav_model, nav_llm, nav_prompt)
        agents["helper_agent"] = create_multimodal_agent(
            name="image-comparer",
            system_message=("You are a visual comparison agent. You can compare images and provide feedback. " "Your only purpose is to do visual comparison of images"),
        )

        config = get_global_conf()
        if config.should_use_dynamic_ltm():
            if self.mem_agent_config is None:
                raise ValueError("Memory agent config is not initialized.")
            mem_model = convert_model_config_to_langchain_format(self.mem_agent_config["model_config_params"])
            llm_config = {**mem_model, **self.mem_agent_config["llm_config_params"]}
            namespace = f"{self.stake_id}_{config.timestamp}"
            self.memory = DynamicLTM(namespace=namespace, llm_config=llm_config)

        return agents

    def _extract_tokens(self, response: Any) -> dict[str, Any]:
        usage = None
        if hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("token_usage")
        if not usage:
            usage = getattr(response, "usage_metadata", None)
        if not usage:
            usage = getattr(response, "usage", None)
        return usage or {}

    def _token_counts(self, response: Any) -> tuple[int, int]:
        usage = self._extract_tokens(response)
        prompt_tokens = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)
        return prompt_tokens, completion_tokens

    def _record_nav_token_usage(self, agent_name: str, response: Any) -> None:
        prompt_tokens, completion_tokens = self._token_counts(response)
        if not prompt_tokens and not completion_tokens:
            return
        entry = {
            "node": "executor",
            "agent": agent_name,
            "turn": len([item for item in self._nav_token_log if item.get("agent") == agent_name]) + 1,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        self._nav_token_log.append(entry)
        logger.info("[TOKEN_COUNT] %s", entry)

    # Patterns that indicate a context/token limit error across OpenAI, Gemini, Anthropic via LiteLLM
    _CONTEXT_LIMIT_PATTERNS = (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "max_tokens",
        "token limit",
        "reduce the length",
    )

    def _is_context_limit_error(self, e: Exception) -> bool:
        msg = str(e).lower()
        return any(p in msg for p in self._CONTEXT_LIMIT_PATTERNS)

    def _compress_messages(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """Compress message history to a single summary when context limit is hit."""
        lines = []
        for m in messages:
            content = str(getattr(m, "content", "") or "")
            if not content:
                continue
            tool_calls = getattr(m, "tool_calls", [])
            if tool_calls:
                names = ", ".join(tc.get("name", "") for tc in tool_calls if isinstance(tc, dict))
                content = f"[tool_calls: {names}] {content}"
            lines.append(f"[{m.type}] {content[:300]}")
        summary = "\n".join(lines)
        return [HumanMessage(content=f"COMPRESSED HISTORY (context limit reached):\n{summary}")]

    async def _llm_ainvoke(self, llm: Any, messages: list[AnyMessage], agent_name: str) -> Any:
        timeout = get_llm_request_timeout_seconds()
        try:
            return await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{agent_name} LLM call timed out after {timeout:g}s") from e

    async def _ainvoke_with_context_fallback(
        self,
        llm: Any,
        messages: list[AnyMessage],
        system_message: str,
        agent_name: str,
    ) -> Any:
        try:
            return await self._llm_ainvoke(llm, messages, agent_name)
        except Exception as e:
            if not self._is_context_limit_error(e):
                raise
            compressed = self._compress_messages(messages)
            retry_messages = [SystemMessage(content=system_message), *compressed]
            return await self._llm_ainvoke(llm, retry_messages, agent_name)

    def _log_model_call(self, agent_name: str, messages: list[AnyMessage]) -> None:
        has_system = bool(messages and isinstance(messages[0], SystemMessage))
        logger.warning(
            "[MODEL_CALL] agent=%s system_present=%s",
            agent_name,
            has_system,
        )

    def _planner_timeout_result(
        self,
        state: AgentState,
        messages: list[AnyMessage],
        turn: int,
        start: float,
        error: TimeoutError,
    ) -> dict[str, Any]:
        elapsed = time.perf_counter() - start
        final_response = str(error)
        assert_summary = "EXPECTED RESULT: planner receives a timely model response.\n" f"ACTUAL RESULT: {final_response}."
        content = json.dumps(
            {
                "plan": state.get("plan", ""),
                "next_step": "",
                "terminate": "yes",
                "final_response": final_response,
                "is_assert": True,
                "assert_summary": assert_summary,
                "is_passed": False,
                "target_helper": "Not_Applicable",
            }
        )
        logger.error("[PLANNER_TIMEOUT] %s", final_response)
        notify_planner_messages(final_response, message_type=MessageType.ANSWER)
        return {
            "planner_turn": turn,
            "messages": messages + [AIMessage(content=content)],
            "next_step": "",
            "target_helper": "not_applicable",
            "terminate": "yes",
            "final_response": final_response,
            "is_assert": True,
            "assert_summary": assert_summary,
            "is_passed": False,
            "step_timings": state.get("step_timings", [])
            + [
                {
                    "node": "planner",
                    "turn": turn,
                    "duration": elapsed,
                }
            ],
        }

    # ------------------------------------------------------------------
    # Planner node — uses PlannerAgent with its full system prompt
    # ------------------------------------------------------------------

    async def _planner_node(self, state: AgentState) -> dict[str, Any]:
        start = time.perf_counter()

        turn = state.get("planner_turn", 0) + 1

        if turn > self.planner_number_of_rounds:
            elapsed = time.perf_counter() - start
            return {
                "planner_turn": turn,
                "terminate": "yes",
                "final_response": "Max planner rounds exceeded.",
                "is_assert": True,
                "is_passed": False,
                "assert_summary": (f"EXPECTED: task completes within {self.planner_number_of_rounds} rounds. " "ACTUAL: max planner rounds exceeded."),
                "step_timings": state.get("step_timings", [])
                + [
                    {
                        "node": "planner",
                        "turn": turn,
                        "duration": elapsed,
                    }
                ],
            }

        planner: PlannerAgent = self.agents_map["planner_agent"]

        # Build conversation: system + full history (task + all prior exchanges)
        messages: list[AnyMessage] = list(state.get("messages", []))
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=planner.system_message)] + messages

        self._log_model_call("planner_agent", messages)
        try:
            response = await self._ainvoke_with_context_fallback(
                planner.llm,
                messages,
                planner.system_message,
                "planner_agent",
            )
        except TimeoutError as e:
            return self._planner_timeout_result(state, messages, turn, start, e)

        # Token accounting
        prompt_tokens, completion_tokens = self._token_counts(response)
        step_entry = {
            "node": "planner",
            "turn": turn,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        logger.info("[TOKEN_COUNT] %s", step_entry)

        content = str(response.content) if response.content else ""
        logger.warning("[PLANNER_DEBUG] turn=%d raw=%s", turn, repr(content)[:400])
        planner.on_planner_message(content)

        try:
            parsed = parse_response(content)
        except Exception:
            parsed = {}

        # PlannerAgent schema: next_step, target_helper, terminate, is_assert, etc.
        next_step = str(parsed.get("next_step") or "")
        target_helper = str(parsed.get("target_helper") or "browser").lower()
        terminate = str(parsed.get("terminate", "no")).lower()
        final_response = str(parsed.get("final_response") or "")
        is_assert = bool(parsed.get("is_assert", False))
        assert_summary = str(parsed.get("assert_summary") or "")
        is_passed = bool(parsed.get("is_passed", False))
        plan = str(parsed.get("plan") or state.get("plan", ""))

        completed_step_signatures = set(state.get("completed_step_signatures", []))
        next_step_signature = self._step_signature(next_step) if next_step else ""
        if terminate != "yes" and next_step_signature and next_step_signature in completed_step_signatures:
            logger.warning(
                "[PLANNER_LOOP_GUARD] Planner repeated an already completed step; forcing terminal success. step=%s",
                next_step[:200],
            )
            next_step = ""
            target_helper = "not_applicable"
            terminate = "yes"
            final_response = "The helper already completed the repeated step successfully; " "stopping to avoid re-running the same test action."
            is_assert = True
            is_passed = True
            assert_summary = (
                "EXPECTED RESULT: completed helper steps are not executed again.\n"
                "ACTUAL RESULT: the planner repeated an already completed successful step, "
                "so execution was stopped before re-running it."
            )
            content = json.dumps(
                {
                    "plan": plan,
                    "next_step": next_step,
                    "terminate": terminate,
                    "final_response": final_response,
                    "is_assert": is_assert,
                    "assert_summary": assert_summary,
                    "is_passed": is_passed,
                    "target_helper": "Not_Applicable",
                }
            )

        if next_step:
            notify_planner_messages(next_step, message_type=MessageType.STEP)
            print("\n===== PLANNER =====")
            print("NEXT STEP:", next_step)
            print("TARGET:", target_helper)
            print("TERMINATE:", terminate)
            print("===================\n")
        if terminate == "yes":
            notify_planner_messages(final_response, message_type=MessageType.ANSWER)

        # Append planner AI message so helper response follows naturally
        new_messages = messages + [AIMessage(content=content)]

        elapsed = time.perf_counter() - start

        return {
            "planner_turn": turn,
            "messages": new_messages,
            "plan": plan,
            "next_step": next_step,
            "target_helper": target_helper,
            "terminate": terminate,
            "final_response": final_response,
            "is_assert": is_assert,
            "assert_summary": assert_summary,
            "is_passed": is_passed,
            "step_token_log": state.get("step_token_log", []) + [step_entry],
            "total_prompt_tokens": state.get("total_prompt_tokens", 0) + prompt_tokens,
            "total_completion_tokens": state.get("total_completion_tokens", 0) + completion_tokens,
            "step_timings": state.get("step_timings", [])
            + [
                {
                    "node": "planner",
                    "turn": turn,
                    "duration": elapsed,
                }
            ],
        }

    # ------------------------------------------------------------------
    # Helper → target nav-agent mapping
    # ------------------------------------------------------------------

    _HELPER_MAP: dict[str, str] = {
        "browser": "browser_nav_agent",
        "api": "api_nav_agent",
        "sec": "sec_nav_agent",
        "sql": "sql_nav_agent",
        "time_keeper": "time_keeper_nav_agent",
        "mcp": "mcp_nav_agent",
        "executor": "executor_nav_agent",
        "agent": "browser_nav_agent",
    }

    _BROWSER_STATE_CHANGING_TOOLS = {
        "open_url",
        "click",
        "bulk_enter_text",
        "bulk_select_option",
        "bulk_set_date_time_value",
        "bulk_set_slider",
        "click_and_upload_file",
        "drag_and_drop",
        "entertext",
        "hover",
        "press_key_combination",
        "set_current_geo_location",
    }

    _STATE_REFRESH_MARKERS = (
        "as a consequence of this action",
        "get all_fields dom",
        "retrieve dom again",
        "retrieving dom again",
        "new elements have appeared",
        "page has changed",
    )

    def _lookup_browser_tool(self, tool_name: str) -> Any | None:
        for tool_entry in tool_registry.get("browser_nav_agent", []):
            if tool_entry.get("name") == tool_name:
                return tool_entry.get("func")
        return None

    async def _ensure_nav_agent_ready(self, nav_agent: Any) -> None:
        ensure_tools_ready = getattr(nav_agent, "ensure_tools_ready", None)
        if ensure_tools_ready is None:
            return
        result = ensure_tools_ready()
        if asyncio.iscoroutine(result):
            await result

    async def _build_helper_task(self, next_step: str, target_helper: str, state: AgentState) -> str:
        task = next_step
        current_url = str(state.get("current_url") or "").strip()
        if target_helper in {"browser", "agent"} and current_url:
            task = f"{task}\n\nCurrent Page: {current_url}"

        memory_context = await self._query_memory(task)
        if memory_context:
            task = f"{task}\n\nEXTRA INFORMATION: {memory_context}"
        return task

    @staticmethod
    def _tool_call_name(tc: Any) -> str:
        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
        return str(name or "")

    @staticmethod
    def _tool_call_args(tc: Any) -> dict[str, Any]:
        args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
        return args if isinstance(args, dict) else {"__invalid_tool_args__": args}

    @staticmethod
    def _tool_call_id(tc: Any, fallback: str) -> str:
        return tc.get("id", fallback) if isinstance(tc, dict) else getattr(tc, "id", fallback)

    def _tool_call_for_history(self, tc: Any) -> dict[str, Any]:
        name = self._tool_call_name(tc)
        return {
            "name": name,
            "args": self._tool_call_args(tc),
            "id": self._tool_call_id(tc, name),
        }

    def _ai_message_with_tool_calls(self, response: Any, tool_calls: list[Any]) -> AIMessage:
        return AIMessage(
            content=getattr(response, "content", "") or "",
            tool_calls=[self._tool_call_for_history(tc) for tc in tool_calls],
        )

    def _requires_state_refresh(self, agent_name: str, tool_name: str, tool_result: str) -> bool:
        if agent_name != "browser_nav_agent":
            return False
        lower_result = tool_result.lower()
        if any(marker in lower_result for marker in self._STATE_REFRESH_MARKERS):
            return True
        return tool_name in self._BROWSER_STATE_CHANGING_TOOLS and "[error]" not in lower_result and "[tool error]" not in lower_result and "unable to" not in lower_result

    async def _execute_tool_call(self, tool_obj: Any, tool_name: str, tool_args: dict[str, Any]) -> str:
        if "__invalid_tool_args__" in tool_args:
            return f"[TOOL ERROR] {tool_name}: expected tool arguments to be a dict, " f"got {type(tool_args['__invalid_tool_args__']).__name__}."
        try:
            coroutine_fn = getattr(tool_obj, "coroutine", None)
            if coroutine_fn and asyncio.iscoroutinefunction(coroutine_fn):
                tool_result = await coroutine_fn(**tool_args)
            elif asyncio.iscoroutinefunction(getattr(tool_obj, "func", None)):
                tool_result = await tool_obj.func(**tool_args)
            else:
                fn = getattr(tool_obj, "func", tool_obj)
                tool_result = fn(**tool_args)
            return str(tool_result)
        except Exception as te:
            logger.warning("[EXECUTOR] tool %s error: %s", tool_name, te)
            return f"[TOOL ERROR] {tool_name}: {te}"

    def _build_cost_metrics(self, final_state: dict[str, Any]) -> dict[str, Any]:
        prompt_tokens = int(final_state.get("total_prompt_tokens", 0) or 0)
        completion_tokens = int(final_state.get("total_completion_tokens", 0) or 0)
        total_tokens = prompt_tokens + completion_tokens
        return {
            "usage_including_cached_inference": {
                "total_cost": 0.0,
                "langgraph": {
                    "cost": 0.0,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            }
        }

    # ------------------------------------------------------------------
    # Executor node — runs the full nav-agent tool loop for next_step
    # ------------------------------------------------------------------

    async def _executor_node(self, state: AgentState) -> dict[str, Any]:
        start = time.perf_counter()

        print("\n===== EXECUTOR =====")
        print("STEP RECEIVED:", state.get("next_step"))
        print("TARGET:", state.get("target_helper"))
        print("====================\n")

        next_step = state.get("next_step", "")
        target_helper = state.get("target_helper", "browser").lower()

        executor_entry = {
            "node": "executor",
            "turn": len([e for e in state.get("step_token_log", []) if e.get("node") == "executor"]) + 1,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "total_steps": 0,
        }

        if not next_step or target_helper == "not_applicable":
            # Nothing to execute — go straight back to planner
            elapsed = time.perf_counter() - start
            return {
                "step_token_log": state.get("step_token_log", []) + [executor_entry],
                "step_timings": state.get("step_timings", [])
                + [
                    {
                        "node": "executor",
                        "turn": len([t for t in state.get("step_timings", []) if t["node"] == "executor"]) + 1,
                        "duration": elapsed,
                    }
                ],
            }

        agent_name = self._HELPER_MAP.get(target_helper, "browser_nav_agent")
        nav_agent = self.agents_map.get(agent_name)
        if nav_agent is None:
            nav_agent = self.agents_map["browser_nav_agent"]
            agent_name = "browser_nav_agent"

        logger.info("[EXECUTOR] routing to %s | step: %s", agent_name, next_step[:200])

        helper_task = await self._build_helper_task(next_step, target_helper, state)
        nav_token_start = len(self._nav_token_log)
        helper_response = await self._run_nav_agent(nav_agent, helper_task, agent_name)
        nav_token_entries = self._nav_token_log[nav_token_start:]
        nav_prompt_tokens = sum(int(entry.get("prompt_tokens", 0) or 0) for entry in nav_token_entries)
        nav_completion_tokens = sum(int(entry.get("completion_tokens", 0) or 0) for entry in nav_token_entries)
        nav_total_tokens = nav_prompt_tokens + nav_completion_tokens
        executor_entry["prompt_tokens"] = nav_prompt_tokens
        executor_entry["completion_tokens"] = nav_completion_tokens
        executor_entry["total_tokens"] = nav_total_tokens

        logger.info("[EXECUTOR] %s response: %s", agent_name, helper_response[:300])

        # Persist memory if flagged
        if "##FLAG::SAVE_IN_MEM##" in helper_response:
            self.save_to_memory(helper_response)

        # Feed the helper's full response back into the planner conversation
        messages: list[AnyMessage] = list(state.get("messages", []))
        messages.append(HumanMessage(content=f"[{agent_name}]: {helper_response}"))

        completed_step_signatures = list(state.get("completed_step_signatures", []))
        step_signature = self._step_signature(next_step)
        if step_signature and self._helper_response_succeeded(helper_response) and step_signature not in completed_step_signatures:
            completed_step_signatures.append(step_signature)

        elapsed = time.perf_counter() - start

        return {
            "messages": messages,
            "completed_step_signatures": completed_step_signatures,
            "last_helper_response": helper_response,
            "step_token_log": state.get("step_token_log", []) + [executor_entry],
            "total_prompt_tokens": int(state.get("total_prompt_tokens", 0) or 0) + nav_prompt_tokens,
            "total_completion_tokens": int(state.get("total_completion_tokens", 0) or 0) + nav_completion_tokens,
            "total_steps": state.get("total_steps", 0) + 1,
            "step_timings": state.get("step_timings", [])
            + [
                {
                    "node": "executor",
                    "turn": len([t for t in state.get("step_timings", []) if t["node"] == "executor"]) + 1,
                    "duration": elapsed,
                }
            ],
        }

    async def _run_nav_agent(self, nav_agent: Any, task: str, agent_name: str) -> str:
        """
        Run a nav agent's full multi-turn tool-calling loop.
        Exits when the agent outputs ##TERMINATE TASK## or rounds are exhausted.
        """
        await self._ensure_nav_agent_ready(nav_agent)
        tools = getattr(nav_agent, "tools", [])
        llm = getattr(nav_agent, "llm", None)
        system_msg = getattr(nav_agent, "system_message", "You are a helpful agent.")

        if llm is None:
            return f"[ERROR] {agent_name} has no LLM."

        if not tools:
            # No tools registered — bare LLM call
            try:
                resp = await self._llm_ainvoke(
                    llm,
                    [
                        SystemMessage(content=system_msg),
                        HumanMessage(content=task),
                    ],
                    agent_name,
                )
                self._record_nav_token_usage(agent_name, resp)
                return str(getattr(resp, "content", "") or "")
            except Exception as e:
                return f"[ERROR] {agent_name} bare LLM call failed: {e}"

        try:
            llm_with_tools = llm.bind_tools(tools)
        except Exception as e:
            logger.warning("[EXECUTOR] bind_tools failed for %s: %s", agent_name, e)
            try:
                resp = await self._llm_ainvoke(
                    llm,
                    [
                        SystemMessage(content=system_msg),
                        HumanMessage(content=task),
                    ],
                    agent_name,
                )
                self._record_nav_token_usage(agent_name, resp)
                return str(getattr(resp, "content", "") or "")
            except Exception as e2:
                return f"[ERROR] {agent_name} fallback LLM call failed: {e2}"

        tool_map: dict[str, Any] = {t.name: t for t in tools}
        messages: list[AnyMessage] = [
            SystemMessage(content=system_msg),
            HumanMessage(content=task),
        ]

        for _turn in range(self.nav_agent_number_of_rounds):
            try:
                response = await self._llm_ainvoke(llm_with_tools, messages, agent_name)
                self._record_nav_token_usage(agent_name, response)
            except Exception as e:
                if not self._is_context_limit_error(e):
                    logger.error("[EXECUTOR] %s LLM error: %s", agent_name, e)
                    return f"[ERROR] {agent_name} LLM error: {e}"
                messages = self._compress_messages(messages)
                try:
                    response = await self._llm_ainvoke(
                        llm_with_tools,
                        [SystemMessage(content=system_msg)] + messages,
                        agent_name,
                    )
                    self._record_nav_token_usage(agent_name, response)
                except Exception as e2:
                    return f"[ERROR] {agent_name} after compress: {e2}"

            content_str = str(getattr(response, "content", "") or "")
            if "##TERMINATE TASK##" in content_str:
                messages.append(response)
                return content_str

            tool_calls = getattr(response, "tool_calls", []) or []
            if not tool_calls:
                # No tools called and no terminate — agent is done
                messages.append(response)
                return content_str

            tool_messages: list[ToolMessage] = []
            executed_tool_calls: list[Any] = []
            refresh_required = False
            skipped_tool_count = 0

            for tc in tool_calls:
                tool_name = self._tool_call_name(tc)
                tool_args = self._tool_call_args(tc)
                tool_id = self._tool_call_id(tc, tool_name)

                tool_obj = tool_map.get(tool_name)
                if tool_obj is None:
                    tool_result = f"[ERROR] Tool '{tool_name}' not found."
                else:
                    tool_result = await self._execute_tool_call(tool_obj, tool_name, tool_args)

                executed_tool_calls.append(tc)
                tool_messages.append(ToolMessage(content=tool_result, tool_call_id=tool_id))

                if self._requires_state_refresh(agent_name, tool_name, tool_result):
                    refresh_required = True
                    skipped_tool_count = len(tool_calls) - len(executed_tool_calls)
                    logger.warning(
                        "[EXECUTOR] %s tool %s changed browser state; skipped %s stale tool call(s).",
                        agent_name,
                        tool_name,
                        skipped_tool_count,
                    )
                    break

            if refresh_required:
                messages.append(self._ai_message_with_tool_calls(response, executed_tool_calls))
            else:
                messages.append(response)
            messages.extend(tool_messages)

            if refresh_required:
                messages.append(
                    HumanMessage(content=(f"{agent_name} skipped {skipped_tool_count} remaining " "tool call(s) because browser state changed. Re-read the " "current page/DOM before continuing."))
                )

        # Max rounds — return an explicit failure, even when the last response only had tool calls.
        last_ai_content = ""
        for m in reversed(messages):
            if isinstance(m, AIMessage) and str(getattr(m, "content", "") or "").strip():
                last_ai_content = str(getattr(m, "content", "") or "")
                break
        if not last_ai_content:
            last_ai_content = "[empty or tool-calls-only assistant response]"
        return f"[ERROR] {agent_name} max nav rounds ({self.nav_agent_number_of_rounds}) " f"reached before ##TERMINATE TASK##. Last assistant response: {last_ai_content}"

    # ------------------------------------------------------------------
    # Assertion node — trusts planner's is_assert/is_passed/assert_summary
    # ------------------------------------------------------------------

    def _assertion_node(self, state: AgentState) -> dict[str, Any]:
        start = time.perf_counter()

        is_passed = bool(state.get("is_passed", False))
        assert_summary = str(state.get("assert_summary") or "")
        final_response = str(state.get("final_response") or ("The test passed." if is_passed else "The test failed."))

        final_result = {
            "plan": state.get("plan", ""),
            "next_step": "",
            "terminate": "yes",
            "final_response": final_response,
            "is_assert": True,
            "assert_summary": assert_summary,
            "is_passed": is_passed,
            "target_helper": "Not_Applicable",
        }
        notify_planner_messages(final_response, message_type=MessageType.ANSWER)

        elapsed = time.perf_counter() - start

        return {
            "terminate": "yes",
            "final_response": final_response,
            "is_passed": is_passed,
            "assert_summary": assert_summary,
            "messages": state.get("messages", []) + [AIMessage(content=json.dumps(final_result))],
            "step_timings": state.get("step_timings", [])
            + [
                {
                    "node": "assertion",
                    "turn": 1,
                    "duration": elapsed,
                }
            ],
        }

    def _route_after_planner(self, state: AgentState) -> Literal["executor", "assertion", "end"]:
        terminate = state.get("terminate", "no")
        if terminate == "yes":
            return "end"
        is_assert = bool(state.get("is_assert", False))
        target_helper = str(state.get("target_helper", "browser")).lower()
        if is_assert and target_helper == "not_applicable":
            return "assertion"
        return "executor"

    def _route_after_executor(self, state: AgentState) -> Literal["planner", "assertion"]:
        # Always return to planner — it decides when to assert
        return "planner"

    def _build_graph(self) -> Any:
        graph = StateGraph(AgentState)
        graph.add_node("planner", self._planner_node)
        graph.add_node("executor", self._executor_node)
        graph.add_node("assertion", self._assertion_node)
        graph.set_entry_point("planner")
        graph.add_conditional_edges(
            "planner",
            self._route_after_planner,
            {"executor": "executor", "assertion": "assertion", "end": END},
        )
        graph.add_conditional_edges(
            "executor",
            self._route_after_executor,
            {"planner": "planner", "assertion": "assertion"},
        )
        graph.add_edge("assertion", END)
        return graph.compile()

    async def shutdown(self) -> None:
        await self.clean_up_plan()
        for agent in self.agents_map.values():
            if hasattr(agent, "shutdown"):
                result = agent.shutdown()
                if asyncio.iscoroutine(result):
                    await result
        from testzeus_hercules.utils.mcp_help import MCPHelper

        await MCPHelper.destroy()

    def save_to_memory(self, content: str) -> None:
        if not get_global_conf().should_use_dynamic_ltm():
            return
        if self.memory:
            self.memory.save_content(content)
        else:
            logger.warning("Memory system not initialized")

    async def clean_up_plan(self) -> None:
        if get_global_conf().should_use_dynamic_ltm() and self.memory:
            self.memory.clear()
        logger.info("Plan cleaned up.")

    async def _query_memory(self, context: str) -> str:
        if not get_global_conf().should_use_dynamic_ltm() or not self.memory:
            return ""
        return await self.memory.query(context)

    async def process_command(
        self,
        command: str,
        *args: Any,
        current_url: str | None = None,
        **kwargs: Any,
    ) -> GraphChatResult | None:

        if current_url is None and args:
            current_url = str(args[0]) if args[0] else None

        task = command
        if current_url:
            task = f"{command}\n\nCurrent Page: {current_url}"
        if get_global_conf().should_use_dynamic_ltm():
            task += "\n\nEXTRA INFORMATION: " + await self._query_memory(task)

        logger.info("Task for command: %s", task)
        try:
            if self._graph is None:
                raise ValueError("Graph is not initialized.")
            initial: AgentState = {
                "messages": [HumanMessage(content=task)],
                "task": task,
                "plan": "",
                "next_step": "",
                "target_helper": "browser",
                "terminate": "no",
                "final_response": "",
                "is_assert": False,
                "assert_summary": "",
                "is_passed": False,
                "planner_turn": 0,
                "step_token_log": [],
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "step_timings": [],
                "completed_step_signatures": [],
                "last_helper_response": "",
                "current_url": current_url or "",
            }
            final_state = await self._graph.ainvoke(initial, config={"recursion_limit": 2000})

            print("\n===== STEP TIMINGS =====")

            for t in final_state.get("step_timings", []):
                print(f"{t['node']} " f"(Turn {t['turn']}): " f"{t['duration']:.2f}s")

            print("========================\n")

            total_tokens = final_state.get("total_prompt_tokens", 0) + final_state.get("total_completion_tokens", 0)

            print("\n========== EXECUTION SUMMARY ==========")
            print(f"Steps Executed : {final_state.get('total_steps', 0)}")
            print(f"Total Tokens  : {total_tokens}")
            print("=======================================\n")

            messages = final_state.get("messages", [])
            history = []
            for message in messages:
                role = "user" if isinstance(message, HumanMessage) else "assistant"
                history.append({"role": role, "content": getattr(message, "content", "")})
            terminate = "yes" if final_state.get("terminate") == "yes" else "no"
            if history and terminate == "yes":
                history[-1]["terminate"] = "yes"
            result = GraphChatResult(
                chat_history=history,
                messages=messages,
                cost=self._build_cost_metrics(final_state),
                terminate=terminate,
            )
            self._last_graph_result = result
            return result
        except openai.BadRequestError as bre:
            if self._is_context_limit_error(bre):
                logger.error(
                    'Context limit exceeded even after compression for command: "%s". %s',
                    command,
                    bre,
                )
            else:
                logger.error('Unable to process command: "%s". %s', command, bre)
            traceback.print_exc()
        return None
