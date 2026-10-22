"""LangGraph-based orchestration for Hercules test execution."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
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
from testzeus_hercules.core.invariants import poll_invariants, record_invariant_findings
from testzeus_hercules.core.post_process_responses import (
    final_reply_callback_planner_agent as notify_planner_messages,
)
from testzeus_hercules.core.tools import *  # noqa: F403
from testzeus_hercules.core.tools.tool_registry import tool_registry
from testzeus_hercules.utils.error_evidence import (
    _redact_error_text,
    persist_error_evidence,
)
from testzeus_hercules.utils.env_flags import observer_invariants_enabled, strict_verdict_enabled
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

    # Consecutive planner turns that made no executable progress (empty or
    # already-completed next_step); used to break zombie planner loops.
    progress_stall_count: int

    # Token accounting
    step_token_log: list[dict[str, Any]]
    total_prompt_tokens: int
    total_completion_tokens: int
    total_steps: int
    total_cost: float
    cost_available: bool
    step_timings: list[dict[str, Any]]
    completed_step_signatures: list[str]
    last_helper_response: str
    current_url: str


class SimpleHercules:
    """LangGraph orchestrator for planner and navigation helper agents."""

    # Zombie-loop guard: after this many consecutive planner turns with an empty
    # or already-completed next_step, terminate instead of cycling until the
    # round limit (default 500 — hundreds of wasted LLM calls).
    _PLANNER_STALL_LIMIT = 3

    # Proactive cap for tool results fed back into the LLM conversation, so a
    # huge tool output cannot blow the nav agent's context window.
    _MAX_TOOL_RESULT_CHARS = 20_000

    # Keys that mark a parsed planner payload as an actual decision attempt.
    _PLANNER_DECISION_KEYS = ("plan", "next_step", "terminate", "final_response", "target_helper")

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
        self.helper_agent_config: Optional[Dict[str, Any]] = None
        self.stake_id = stake_id
        self.save_chat_logs_to_files = save_chat_logs_to_files
        self._graph = None
        self._last_graph_result: GraphChatResult | None = None
        self._nav_token_log: list[dict[str, Any]] = []
        # Binding findings raised by the observer invariants during this run;
        # any binding finding marks the final result failed at graph end.
        self._invariant_binding: list[dict[str, Any]] = []

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
        self.helper_agent_config = helper_agent_config

        from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model

        for cfg in [
            planner_agent_config,
            nav_agent_config,
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

        # Optional mobile automation agent: only constructed when a device
        # manager backend is configured and the Appium client is importable;
        # a missing module must never block construction of the other agents.
        mobile_agent = self._create_mobile_nav_agent(nav_model, nav_llm, nav_prompt)
        if mobile_agent is not None:
            agents["mobile_nav_agent"] = mobile_agent

        return agents

    @classmethod
    def _mobile_gate_open(cls) -> bool:
        """True when mobile (Appium) automation is enabled and importable.

        Same double gate as :func:`testzeus_hercules.core.mobile.mobile_enabled`:
        ``DEVICE_MANAGER`` must be ``appium`` AND the Appium client importable.
        """
        try:
            from testzeus_hercules.core.mobile import mobile_enabled

            return mobile_enabled()
        except Exception:
            return False

    def _create_mobile_nav_agent(self, nav_model: dict[str, Any], nav_llm: dict[str, Any], nav_prompt: Any) -> Any | None:
        """Return a mobile nav agent when the Appium gate is open, else None.

        The gate is double (see :meth:`_mobile_gate_open`): MobileNavAgent
        itself imports fine without the Appium client, so an ImportError guard
        alone would never fire and a half-constructed agent bound only to the
        shared UI-assertion tools would silently misroute mobile steps.
        """
        if os.environ.get("DEVICE_MANAGER", "").strip().lower() != "appium":
            return None
        if not self._mobile_gate_open():
            logger.warning("Mobile automation requested but the Appium client is not installed; mobile agent not created.")
            return None
        try:
            from testzeus_hercules.core.agents.mobile_nav_agent import MobileNavAgent
        except ImportError:
            logger.info("Mobile automation agent requested but its module is not installed; skipping.")
            return None
        return MobileNavAgent(nav_model, nav_llm, nav_prompt)

    def _extract_tokens(self, response: Any) -> dict[str, Any]:
        usage = None
        if hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("token_usage")
        if not usage:
            usage = getattr(response, "usage_metadata", None)
        if not usage:
            usage = getattr(response, "usage", None)
        return usage or {}

    def _extract_response_cost(self, response: Any) -> float | None:
        sources: list[Any] = [
            getattr(response, "response_metadata", None),
            getattr(response, "usage_metadata", None),
            getattr(response, "usage", None),
        ]
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in ("total_cost", "response_cost", "cost"):
                value = source.get(key)
                if value is None:
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    logger.warning("[TOKEN_COUNT] Ignoring invalid response cost %r", value)
        return None

    def _token_counts(self, response: Any) -> tuple[int, int]:
        usage = self._extract_tokens(response)
        prompt_tokens = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)
        return prompt_tokens, completion_tokens

    def _record_nav_token_usage(self, agent_name: str, response: Any) -> None:
        prompt_tokens, completion_tokens = self._token_counts(response)
        response_cost = self._extract_response_cost(response)
        if not prompt_tokens and not completion_tokens and response_cost is None:
            return
        entry = {
            "node": "executor",
            "agent": agent_name,
            "turn": len([item for item in self._nav_token_log if item.get("agent") == agent_name]) + 1,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        if response_cost is not None:
            entry["cost"] = response_cost
        self._nav_token_log.append(entry)
        logger.info("[TOKEN_COUNT] %s", entry)

    # Patterns that indicate a context/token limit error across OpenAI, Gemini, Anthropic via LiteLLM
    _CONTEXT_LIMIT_PATTERNS = (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "context window exceeded",
        "too many tokens",
        "token limit",
        "reduce the length",
        "prompt is too long",
        "input token count",
    )

    def _is_context_limit_error(self, e: Exception) -> bool:
        msg = str(e).lower()
        if any(p in msg for p in self._CONTEXT_LIMIT_PATTERNS):
            return True
        # A bare "max_tokens" mention (e.g. "max_tokens is too large") is a client
        # configuration error, not context overflow — only treat it as a context
        # limit when the message says a limit is exceeded.
        return "max_tokens" in msg and "exceed" in msg

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
        return self._planner_failure_result(
            state,
            messages,
            turn,
            start,
            str(error),
            "PLANNER_TIMEOUT",
            expectation="planner receives a timely model response",
        )

    def _planner_failure_result(
        self,
        state: AgentState,
        messages: list[AnyMessage],
        turn: int,
        start: float,
        final_response: str,
        log_tag: str,
        expectation: str = "planner receives a valid model response",
    ) -> dict[str, Any]:
        """Build a terminal failed planner state so an LLM failure ends the graph cleanly.

        The planner-shaped JSON is appended as an AIMessage so the run still
        records a failed scenario (junit/report) instead of the whole run aborting.
        """
        elapsed = time.perf_counter() - start
        assert_summary = f"EXPECTED RESULT: {expectation}.\n" f"ACTUAL RESULT: {final_response}."
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
        logger.error("[%s] %s", log_tag, final_response)
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
    # Planner decision validation
    # ------------------------------------------------------------------

    @classmethod
    def _validate_planner_decision(cls, parsed: dict[str, Any]) -> None:
        """Raise ValueError when a planner decision violates the expected schema.

        Runs against the raw parsed payload before it enters the graph state so
        a malformed decision fails the scenario through the planner failure path
        instead of silently misrouting helpers or looping.

        Only payloads that actually attempted a decision (carry at least one
        planner key) are validated by the caller: an empty/unparseable response
        keeps its existing stall-based handling.
        """
        raw_terminate = parsed.get("terminate")
        if raw_terminate is not None and not isinstance(raw_terminate, bool):
            terminate_text = str(raw_terminate).strip().lower()
            if terminate_text not in ("yes", "no"):
                raise ValueError(f"terminate must be 'yes' or 'no', got {raw_terminate!r}")
        for field_name in ("is_assert", "is_passed"):
            value = parsed.get(field_name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_name} must be a boolean, got {value!r}")
        target_helper = parsed.get("target_helper")
        terminate_is_yes = raw_terminate is True or str(raw_terminate if raw_terminate is not None else "").strip().lower() == "yes"
        if terminate_is_yes:
            # Once terminate is "yes" the helper is never used for routing (see
            # _route_after_planner): a missing or empty target_helper is valid
            # — normalized to the Not_Applicable equivalent used in the map —
            # and membership is not enforced.
            if target_helper is not None and not str(target_helper).strip():
                parsed["target_helper"] = "Not_Applicable"
        elif target_helper is not None:
            normalized_helper = str(target_helper).strip().lower()
            if normalized_helper == "mobile" and not cls._mobile_gate_open():
                raise ValueError("target_helper 'mobile' requires an available Appium client (set DEVICE_MANAGER=appium and install the appium package)")
            allowed_helpers = set(cls._HELPER_MAP) | {"not_applicable"}
            if normalized_helper not in allowed_helpers:
                raise ValueError(f"target_helper must be one of {sorted(allowed_helpers)}, got {target_helper!r}")
        terminate_is_no = raw_terminate is not True and str(raw_terminate if raw_terminate is not None else "no").strip().lower() != "yes"
        next_step = str(parsed.get("next_step") or "").strip()
        if terminate_is_no and not next_step:
            raise ValueError("next_step must be non-empty while terminate is 'no'")

    @staticmethod
    def _corrective_planner_prompt(error: ValueError) -> str:
        return (
            f"Your previous reply was not a valid decision: {error}. "
            'Reply again with ONLY the corrected JSON decision: terminate must be "yes" or "no", '
            "is_assert and is_passed must be JSON booleans (true/false, not strings), and next_step must be "
            'non-empty while terminate is "no".'
        )

    async def _invoke_planner(
        self,
        planner: PlannerAgent,
        messages: list[AnyMessage],
        state: AgentState,
        turn: int,
        start: float,
    ) -> tuple[Any, dict[str, Any] | None]:
        """Call the planner LLM once; return ``(response, None)`` or ``(None, failed_planner_state)``."""
        self._log_model_call("planner_agent", messages)
        try:
            response = await self._ainvoke_with_context_fallback(
                planner.llm,
                messages,
                planner.system_message,
                "planner_agent",
            )
        except TimeoutError as e:
            return None, self._planner_timeout_result(state, messages, turn, start, e)
        except Exception as e:
            # Any other planner LLM failure (rate limits, connection errors,
            # provider 5xx, malformed tool messages) must terminate the graph
            # cleanly so the scenario is recorded as failed instead of the
            # exception escaping and aborting the whole run.
            return None, self._planner_failure_result(state, messages, turn, start, f"Planner LLM call failed: {e}", "PLANNER_ERROR")
        return response, None

    # ------------------------------------------------------------------
    # Planner node — uses PlannerAgent with its full system prompt
    # ------------------------------------------------------------------

    async def _planner_node(self, state: AgentState) -> dict[str, Any]:
        start = time.perf_counter()

        turn = state.get("planner_turn", 0) + 1

        if turn > self.planner_number_of_rounds:
            elapsed = time.perf_counter() - start
            max_rounds_response = "Max planner rounds exceeded."
            assert_summary = f"EXPECTED RESULT: task completes within {self.planner_number_of_rounds} rounds.\n" f"ACTUAL RESULT: {max_rounds_response}"
            # Append the canonical terminal JSON as an AIMessage so the exported
            # summary reflects this turn's termination instead of a stale
            # previous-turn planner payload.
            content = json.dumps(
                {
                    "plan": state.get("plan", ""),
                    "next_step": "",
                    "terminate": "yes",
                    "final_response": max_rounds_response,
                    "is_assert": True,
                    "assert_summary": assert_summary,
                    "is_passed": False,
                    "target_helper": "Not_Applicable",
                }
            )
            logger.error("[PLANNER_MAX_ROUNDS] %s", max_rounds_response)
            notify_planner_messages(max_rounds_response, message_type=MessageType.ANSWER)
            return {
                "planner_turn": turn,
                "messages": list(state.get("messages", [])) + [AIMessage(content=content)],
                "plan": state.get("plan", ""),
                "next_step": "",
                "target_helper": "not_applicable",
                "terminate": "yes",
                "final_response": max_rounds_response,
                "is_assert": True,
                "is_passed": False,
                "assert_summary": assert_summary,
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

        response, failure = await self._invoke_planner(planner, messages, state, turn, start)
        if failure is not None:
            return failure

        # Token accounting (accumulated over the optional corrective re-prompt)
        prompt_tokens, completion_tokens = self._token_counts(response)
        response_cost = self._extract_response_cost(response)

        content = str(response.content) if response.content else ""
        logger.warning("[PLANNER_DEBUG] turn=%d raw=%s", turn, repr(content)[:400])
        planner.on_planner_message(content)

        try:
            parsed = parse_response(content)
        except Exception:
            parsed = {}

        # A malformed planner decision (e.g. is_assert as the string "true", an
        # empty next_step while terminate is "no") gets ONE corrective re-prompt
        # before the scenario is failed, so a single formatting slip does not
        # turn a healthy run red. Empty/unparseable responses keep their existing
        # stall-based handling.
        if any(key in parsed for key in self._PLANNER_DECISION_KEYS):
            try:
                self._validate_planner_decision(parsed)
            except ValueError as first_error:
                logger.warning("[PLANNER_INVALID_DECISION] %s; re-prompting the planner once.", first_error)
                messages = messages + [
                    AIMessage(content=content),
                    HumanMessage(content=self._corrective_planner_prompt(first_error)),
                ]
                response, failure = await self._invoke_planner(planner, messages, state, turn, start)
                if failure is not None:
                    return failure
                retry_prompt_tokens, retry_completion_tokens = self._token_counts(response)
                prompt_tokens += retry_prompt_tokens
                completion_tokens += retry_completion_tokens
                retry_cost = self._extract_response_cost(response)
                if retry_cost is not None:
                    response_cost = (response_cost or 0.0) + retry_cost
                content = str(response.content) if response.content else ""
                logger.warning("[PLANNER_DEBUG] turn=%d retry raw=%s", turn, repr(content)[:400])
                planner.on_planner_message(content)
                try:
                    parsed = parse_response(content)
                except Exception:
                    parsed = {}
                if any(key in parsed for key in self._PLANNER_DECISION_KEYS):
                    try:
                        self._validate_planner_decision(parsed)
                    except ValueError as e:
                        return self._planner_failure_result(
                            state,
                            messages,
                            turn,
                            start,
                            f"Invalid planner decision: {e}",
                            "PLANNER_INVALID_DECISION",
                            expectation="planner produces a valid decision",
                        )

        step_entry = {
            "node": "planner",
            "turn": turn,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        if response_cost is not None:
            step_entry["cost"] = response_cost
        logger.info("[TOKEN_COUNT] %s", step_entry)

        # PlannerAgent schema: next_step, target_helper, terminate, is_assert, etc.
        next_step = str(parsed.get("next_step") or "")
        target_helper = str(parsed.get("target_helper") or "browser").lower()
        # Some models emit a JSON boolean (terminate: true) — normalize it so a
        # truthy terminate is not silently read as "no" (wasting stall turns).
        raw_terminate = parsed.get("terminate", "no")
        terminate = "yes" if (raw_terminate is True or str(raw_terminate).strip().lower() == "yes") else "no"
        final_response = str(parsed.get("final_response") or "")
        is_assert = bool(parsed.get("is_assert", False))
        assert_summary = str(parsed.get("assert_summary") or "")
        is_passed = bool(parsed.get("is_passed", False))
        plan = str(parsed.get("plan") or state.get("plan", ""))

        completed_signatures = set(state.get("completed_step_signatures", []))
        repeated_step = bool(next_step) and self._step_signature(next_step) in completed_signatures
        if terminate != "yes" and repeated_step:
            logger.warning(
                "[PLANNER_REPEAT_NOTICE] Planner repeated an already completed step; allowing execution to continue. step=%s",
                next_step[:200],
            )

        # Zombie-loop guard: only an empty next_step (unparseable planner output)
        # counts as no executable progress. A repeated step that executes (e.g.
        # clicking "Next page" several times) is legitimate and stays non-fatal
        # (see PLANNER_REPEAT_NOTICE above); the counter resets on any step.
        if terminate == "yes" or next_step:
            progress_stall_count = 0
        else:
            progress_stall_count = int(state.get("progress_stall_count", 0) or 0) + 1

        # Append planner AI message so helper response follows naturally
        new_messages = messages + [AIMessage(content=content)]

        if progress_stall_count >= self._PLANNER_STALL_LIMIT:
            elapsed = time.perf_counter() - start
            stall_final_response = f"Planner produced no executable progress {progress_stall_count} times in a row " "(empty or unparseable steps); terminating early to avoid a planner loop."
            assert_summary = "EXPECTED RESULT: the planner produces executable steps.\n" f"ACTUAL RESULT: {stall_final_response}"
            stall_content = json.dumps(
                {
                    "plan": plan,
                    "next_step": "",
                    "terminate": "yes",
                    "final_response": stall_final_response,
                    "is_assert": True,
                    "assert_summary": assert_summary,
                    "is_passed": False,
                    "target_helper": "Not_Applicable",
                }
            )
            logger.error("[PLANNER_STALL] %s", stall_final_response)
            notify_planner_messages(stall_final_response, message_type=MessageType.ANSWER)
            return {
                "planner_turn": turn,
                "messages": new_messages + [AIMessage(content=stall_content)],
                "plan": plan,
                "next_step": "",
                "target_helper": "not_applicable",
                "terminate": "yes",
                "final_response": stall_final_response,
                "is_assert": True,
                "assert_summary": assert_summary,
                "is_passed": False,
                "progress_stall_count": progress_stall_count,
                "step_token_log": state.get("step_token_log", []) + [step_entry],
                "total_prompt_tokens": state.get("total_prompt_tokens", 0) + prompt_tokens,
                "total_completion_tokens": state.get("total_completion_tokens", 0) + completion_tokens,
                "total_cost": float(state.get("total_cost", 0.0) or 0.0) + (response_cost or 0.0),
                "cost_available": bool(state.get("cost_available", False) or response_cost is not None),
                "step_timings": state.get("step_timings", [])
                + [
                    {
                        "node": "planner",
                        "turn": turn,
                        "duration": elapsed,
                    }
                ],
            }

        if next_step:
            notify_planner_messages(next_step, message_type=MessageType.STEP)
            print("\n===== PLANNER =====")
            print("NEXT STEP:", next_step)
            print("TARGET:", target_helper)
            print("TERMINATE:", terminate)
            print("===================\n")
        if terminate == "yes":
            notify_planner_messages(final_response, message_type=MessageType.ANSWER)

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
            "progress_stall_count": progress_stall_count,
            "step_token_log": state.get("step_token_log", []) + [step_entry],
            "total_prompt_tokens": state.get("total_prompt_tokens", 0) + prompt_tokens,
            "total_completion_tokens": state.get("total_completion_tokens", 0) + completion_tokens,
            "total_cost": float(state.get("total_cost", 0.0) or 0.0) + (response_cost or 0.0),
            "cost_available": bool(state.get("cost_available", False) or response_cost is not None),
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
        "mobile": "mobile_nav_agent",
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

    async def _get_live_current_url(self) -> str:
        try:
            from testzeus_hercules.core.playwright_manager import PlaywrightManager

            managers: list[Any] = []
            stake_manager = PlaywrightManager._instances.get(self.stake_id)
            if stake_manager is not None:
                managers.append(stake_manager)
            default_manager = PlaywrightManager._default_instance
            if default_manager is not None and default_manager not in managers:
                managers.append(default_manager)

            for manager in managers:
                get_current_url = getattr(manager, "get_current_url", None)
                if get_current_url is None:
                    continue
                current_url = get_current_url()
                if asyncio.iscoroutine(current_url):
                    current_url = await current_url
                if current_url:
                    return str(current_url)
        except Exception as e:
            logger.debug("[EXECUTOR] Unable to refresh current browser URL: %s", e)
        return ""

    async def _resolve_current_url(self, target_helper: str, state: AgentState) -> str:
        fallback_url = str(state.get("current_url") or "").strip()
        if target_helper not in {"browser", "agent"}:
            return fallback_url
        live_url = (await self._get_live_current_url()).strip()
        return live_url or fallback_url

    async def _build_helper_task(self, next_step: str, target_helper: str, state: AgentState) -> str:
        task = next_step
        current_url = await self._resolve_current_url(target_helper, state)
        if target_helper in {"browser", "agent"} and current_url:
            task = f"{task}\n\nCurrent Page: {current_url}"

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
        raw_id = tc.get("id", fallback) if isinstance(tc, dict) else getattr(tc, "id", fallback)
        # OpenAI-compatible gateways may report id=None (langchain's parse_tool_call
        # does); ToolMessage requires a non-empty string id, so fall back.
        return raw_id if isinstance(raw_id, str) and raw_id else fallback

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

    @classmethod
    def _truncate_tool_result(cls, tool_result: str) -> str:
        """Cap a tool result before it enters LLM history (proactive context guard)."""
        if len(tool_result) <= cls._MAX_TOOL_RESULT_CHARS:
            return tool_result
        logger.warning("[EXECUTOR] tool result capped for history: %d chars total", len(tool_result))
        return tool_result[: cls._MAX_TOOL_RESULT_CHARS] + f"...[truncated, {len(tool_result)} chars total]"

    async def _execute_tool_call(self, tool_obj: Any, tool_name: str, tool_args: dict[str, Any]) -> str:
        if "__invalid_tool_args__" in tool_args:
            return f"[TOOL ERROR] {tool_name}: expected tool arguments to be a dict, " f"got {type(tool_args['__invalid_tool_args__']).__name__}."
        try:
            coroutine_fn = getattr(tool_obj, "coroutine", None)
            if coroutine_fn and asyncio.iscoroutinefunction(coroutine_fn):
                # Preferred seam: invoking the registered tool object records a
                # standard tool run when execution tracing is active. The seam
                # is guaranteed by the getattr guard, so there is deliberately
                # no fallback here: an error raised by ainvoke after the side
                # effect (e.g. a None deref in the tool) must not re-run the
                # tool through the raw coroutine.
                if callable(getattr(tool_obj, "ainvoke", None)):
                    tool_result = await tool_obj.ainvoke(tool_args)
                else:
                    tool_result = await coroutine_fn(**tool_args)
            elif asyncio.iscoroutinefunction(getattr(tool_obj, "func", None)):
                tool_result = await tool_obj.func(**tool_args)
            else:
                # Sync tools stay on this thread while keeping the same seam
                # (same no-fallback reasoning as above).
                if callable(getattr(tool_obj, "invoke", None)):
                    tool_result = tool_obj.invoke(tool_args)
                else:
                    fn = getattr(tool_obj, "func", tool_obj)
                    tool_result = fn(**tool_args)
            if asyncio.iscoroutine(tool_result):
                # A bare async-def tool (no .coroutine/.func attributes) falls
                # into the sync fallback and its call returns a coroutine;
                # await it instead of stringifying "<coroutine object ...>".
                tool_result = await tool_result
            return self._truncate_tool_result(str(tool_result))
        except Exception as te:
            logger.warning("[EXECUTOR] tool %s error: %s", tool_name, te)
            return f"[TOOL ERROR] {tool_name}: {te}"

    def _build_cost_metrics(self, final_state: dict[str, Any]) -> dict[str, Any]:
        prompt_tokens = int(final_state.get("total_prompt_tokens", 0) or 0)
        completion_tokens = int(final_state.get("total_completion_tokens", 0) or 0)
        total_tokens = prompt_tokens + completion_tokens
        langgraph_usage: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        usage: dict[str, Any] = {
            "langgraph": langgraph_usage,
        }
        provider_cost = float(final_state.get("total_cost", 0.0) or 0.0) if final_state.get("cost_available") else None
        if provider_cost:
            # Provider-reported cost always wins.
            usage["total_cost"] = provider_cost
            langgraph_usage["cost"] = provider_cost
        else:
            usage["cost_unavailable"] = True
            langgraph_usage["cost_unavailable"] = True
            if prompt_tokens or completion_tokens:
                estimated_cost: float | None = None
                try:
                    # Lazy import to avoid import cycles at module load time.
                    from testzeus_hercules.utils.cost_calculator import (
                        estimate_cost_for_tokens,
                        resolve_model_pricing,
                    )

                    pricing = resolve_model_pricing()
                    if pricing is not None:
                        estimated_cost = estimate_cost_for_tokens(
                            pricing,
                            {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                        )
                except Exception as exc:
                    logger.warning("[COST] model_pricing fallback estimation failed: %s", exc)
                    estimated_cost = None
                if estimated_cost is not None:
                    usage.pop("cost_unavailable", None)
                    langgraph_usage.pop("cost_unavailable", None)
                    usage["total_cost"] = estimated_cost
                    langgraph_usage["cost"] = estimated_cost
                    usage["cost_source"] = "estimated_from_model_pricing"
                    langgraph_usage["cost_source"] = "estimated_from_model_pricing"
        return {
            "usage_including_cached_inference": usage,
        }

    # ------------------------------------------------------------------
    # Observer invariants — global evidence judged from proof artifacts
    # ------------------------------------------------------------------

    @staticmethod
    def _invariant_finding_key(finding: dict[str, Any]) -> tuple[str, str, str]:
        """Identity of an invariant finding for dedupe: (operator/kind, path/url, detail)."""
        return (
            str(finding.get("operator") or finding.get("kind")),
            str(finding.get("path") or finding.get("url")),
            str(finding.get("detail")),
        )

    def _poll_invariants_safely(self) -> dict[str, list[dict[str, Any]]]:
        """Poll proof artifacts for binding/notice findings; never break the run.

        The observer is advisory to graph control flow: any failure to read,
        classify or record is logged and swallowed so a poll problem can
        never mask or abort the scenario under test.
        """
        try:
            test_id = get_global_conf().get_default_test_id()
            findings = poll_invariants(test_id)
            record_invariant_findings(test_id, findings)
            if findings.get("binding"):
                # Dedupe by finding identity so a re-read of the same proof
                # artifact line never inflates the binding count. The
                # check-and-append runs against a growing seen-set so two
                # identical findings inside ONE poll payload collapse too.
                existing = {self._invariant_finding_key(finding) for finding in self._invariant_binding}
                fresh: list[dict[str, Any]] = []
                for finding in findings["binding"]:
                    finding_key = self._invariant_finding_key(finding)
                    if finding_key in existing:
                        continue
                    existing.add(finding_key)
                    fresh.append(finding)
                self._invariant_binding.extend(fresh)
                if fresh:
                    logger.warning("[INVARIANTS] %d binding finding(s) recorded", len(fresh))
            return findings
        except Exception as e:
            logger.warning("[INVARIANTS] observer poll failed (ignored): %s", e)
            return {"binding": [], "notice": []}

    def _invariant_summary_line(self) -> str:
        findings = self._invariant_binding
        details = "; ".join(str(finding.get("detail", "")) for finding in findings[:3])
        more = f" (+{len(findings) - 3} more)" if len(findings) > 3 else ""
        return f"Observer invariants flagged {len(findings)} binding finding(s) in the proof artifacts: {details}{more}."

    def _apply_invariant_failure(self, final_state: dict[str, Any]) -> dict[str, Any]:
        """Force a BINDING invariant verdict onto the final graph result.

        The scenario is recorded failed (containment: the run itself
        continues); the terminal planner-shaped AIMessage is rewritten so the
        exported summary reflects the invariant verdict instead of a claimed
        pass.
        """
        invariant_line = self._invariant_summary_line()
        final_response = str(final_state.get("final_response") or "").strip()
        final_state["final_response"] = f"{final_response}\n{invariant_line}" if final_response else invariant_line
        assert_summary = str(final_state.get("assert_summary") or "").strip()
        final_state["assert_summary"] = f"{assert_summary}\n{invariant_line}" if assert_summary else invariant_line
        final_state["is_passed"] = False
        final_state["is_assert"] = True
        final_state["terminate"] = "yes"

        # Rewrite the last assistant message so summary extraction (which
        # reads the terminal planner JSON) carries the invariant verdict.
        messages = list(final_state.get("messages", []))
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            content = str(getattr(message, "content", "") or "") if isinstance(message, AIMessage) else ""
            if not content.strip():
                continue
            try:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError("terminal message is not a JSON object")
            except ValueError:
                payload = {"plan": final_state.get("plan", ""), "next_step": ""}
            payload.update(
                {
                    "terminate": "yes",
                    "final_response": final_state["final_response"],
                    "is_assert": True,
                    "assert_summary": final_state["assert_summary"],
                    "is_passed": False,
                    "target_helper": "Not_Applicable",
                }
            )
            messages[index] = AIMessage(content=json.dumps(payload))
            break
        final_state["messages"] = messages
        logger.warning("[INVARIANTS] %s", invariant_line)
        return final_state

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
            logger.warning(
                "[EXECUTOR] requested helper %r is unavailable (agent not created); rerouting to browser_nav_agent.",
                target_helper,
            )
            nav_agent = self.agents_map["browser_nav_agent"]
            agent_name = "browser_nav_agent"

        logger.info("[EXECUTOR] routing to %s | step: %s", agent_name, next_step[:200])

        helper_task = await self._build_helper_task(next_step, target_helper, state)
        nav_token_start = len(self._nav_token_log)
        raw_helper_response = await self._run_nav_agent(nav_agent, helper_task, agent_name)
        helper_response = str(raw_helper_response or "")
        nav_token_entries = self._nav_token_log[nav_token_start:]
        nav_prompt_tokens = sum(int(entry.get("prompt_tokens", 0) or 0) for entry in nav_token_entries)
        nav_completion_tokens = sum(int(entry.get("completion_tokens", 0) or 0) for entry in nav_token_entries)
        nav_total_tokens = nav_prompt_tokens + nav_completion_tokens
        nav_cost_entries = [float(entry["cost"]) for entry in nav_token_entries if "cost" in entry]
        nav_cost = sum(nav_cost_entries)
        executor_entry["prompt_tokens"] = nav_prompt_tokens
        executor_entry["completion_tokens"] = nav_completion_tokens
        executor_entry["total_tokens"] = nav_total_tokens
        if nav_cost_entries:
            executor_entry["cost"] = nav_cost

        logger.info("[EXECUTOR] %s response: %s", agent_name, helper_response[:300])

        # Observer invariants (opt-in, ENABLE_OBSERVER_INVARIANTS=true): judge the
        # proof artifacts written so far. Containment-wrapped — a poll failure is
        # logged and swallowed. Off by default: HTTP 5xx / console TypeError
        # evidence can fail a scenario that the planner legitimately passed.
        if observer_invariants_enabled():
            self._poll_invariants_safely()

        # Feed the helper's full response back into the planner conversation
        messages: list[AnyMessage] = list(state.get("messages", []))
        messages.append(HumanMessage(content=f"[{agent_name}]: {helper_response}"))

        completed_step_signatures = list(state.get("completed_step_signatures", []))
        step_signature = self._step_signature(next_step)
        if step_signature and self._helper_response_succeeded(helper_response) and step_signature not in completed_step_signatures:
            completed_step_signatures.append(step_signature)

        current_url = str(state.get("current_url") or "")
        if target_helper in {"browser", "agent"}:
            current_url = (await self._get_live_current_url()) or current_url

        # Keep the previous non-empty helper answer when this run produced
        # nothing; a blank response must not clobber the last known outcome.
        last_helper_response = helper_response if helper_response.strip() else str(state.get("last_helper_response") or "")

        elapsed = time.perf_counter() - start

        try:
            from testzeus_hercules.core.notification_manager import get_notification_hub

            get_notification_hub().notify(
                message=f"Step {state.get('total_steps', 0) + 1} completed via {agent_name}",
                message_type="step_completed",
            )
        except Exception:
            pass

        return {
            "messages": messages,
            "completed_step_signatures": completed_step_signatures,
            "last_helper_response": last_helper_response,
            "current_url": current_url,
            "step_token_log": state.get("step_token_log", []) + [executor_entry],
            "total_prompt_tokens": int(state.get("total_prompt_tokens", 0) or 0) + nav_prompt_tokens,
            "total_completion_tokens": int(state.get("total_completion_tokens", 0) or 0) + nav_completion_tokens,
            "total_cost": float(state.get("total_cost", 0.0) or 0.0) + nav_cost,
            "cost_available": bool(state.get("cost_available", False) or nav_cost_entries),
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
                logger.warning("[EXECUTOR] %s LLM call failed: %s", agent_name, e)
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
                logger.warning("[EXECUTOR] %s LLM call failed: %s", agent_name, e2)
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
                    logger.warning("[EXECUTOR] %s LLM call failed: %s", agent_name, e2)
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

            for tool_call in tool_calls:
                tool_name = self._tool_call_name(tool_call)
                tool_args = self._tool_call_args(tool_call)
                tool_id = self._tool_call_id(tool_call, tool_name)

                tool_obj = tool_map.get(tool_name)
                if tool_obj is None:
                    tool_result = f"[ERROR] Tool '{tool_name}' not found."
                else:
                    tool_result = await self._execute_tool_call(tool_obj, tool_name, tool_args)

                executed_tool_calls.append(tool_call)
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

            # Only nag when tool calls were actually skipped — a refresh on the
            # last call of the batch leaves nothing to skip.
            if refresh_required and skipped_tool_count > 0:
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
            # Preserve the planner's is_assert: a non-asserting terminal decision
            # must keep the junit no-assertion verdict path.
            "is_assert": bool(state.get("is_assert", False)),
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
        is_assert = bool(state.get("is_assert", False))
        target_helper = str(state.get("target_helper", "browser")).lower()
        if terminate == "yes":
            # Assertion terminal payloads pass through the assertion node so the
            # final-result JSON AIMessage and the assertion timing row are
            # recorded; the assertion node then ends the graph. A non-asserting
            # terminal decision ends immediately (origin/main behaviour) unless
            # STRICT_VERDICT also routes Not_Applicable terminals through it.
            if is_assert or (strict_verdict_enabled() and target_helper == "not_applicable"):
                return "assertion"
            return "end"
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

    async def clean_up_plan(self) -> None:
        logger.info("Plan cleaned up.")

    # Taxonomy shared with the junit failure-record renderer.
    _FAILURE_CATEGORY = "agent_chain_failure"
    _FAILURE_STAGE = "scenario_execution"

    def _persist_failure_evidence(self, command: str, error: Exception) -> dict[str, Any] | None:
        """Persist redacted evidence for a graph-level failure (best-effort).

        Returns the structured failure record for the junit renderer, or
        None when evidence capture itself failed — the original failure must
        never be masked by the evidence path.
        """
        try:
            log_dir = get_global_conf().get_source_log_folder_path(self.stake_id)
            evidence_path = persist_error_evidence(
                log_dir,
                error,
                category=self._FAILURE_CATEGORY,
                stage=self._FAILURE_STAGE,
                extra_context={"command": command},
            )
            return {
                "category": self._FAILURE_CATEGORY,
                "stage": self._FAILURE_STAGE,
                "error_type": type(error).__name__,
                "message": _redact_error_text(str(error)),
                "evidence_path": evidence_path,
            }
        except Exception as evidence_error:
            logger.warning("Failed to persist error evidence for a failed command: %s", evidence_error)
            return None

    def _failed_graph_result(self, command: str, error: Exception) -> GraphChatResult:
        """Terminal failed result so a graph crash is recorded instead of aborting the run.

        The planner-shaped JSON is appended as an AIMessage so
        ``GraphChatResult.summary`` (and therefore the junit entry downstream)
        carries the actual failure and marks the scenario as failed. A
        redacted evidence file is persisted and the structured record rides
        on the result as ``execution_failure`` (in the terminal JSON and as a
        result attribute) so the junit renderer can show
        category/stage/type/evidence.
        """
        final_response = f'Command "{command}" failed with an unexpected error: {_redact_error_text(str(error))}'
        assert_summary = "EXPECTED RESULT: the command completes without internal errors.\n" f"ACTUAL RESULT: {final_response}."
        execution_failure = self._persist_failure_evidence(command, error)
        content_payload: dict[str, Any] = {
            "plan": "",
            "next_step": "",
            "terminate": "yes",
            "final_response": final_response,
            "is_assert": True,
            "assert_summary": assert_summary,
            "is_passed": False,
            "target_helper": "Not_Applicable",
        }
        if execution_failure:
            content_payload["execution_failure"] = execution_failure
        content = json.dumps(content_payload)
        notify_planner_messages(final_response, message_type=MessageType.ANSWER)
        result = GraphChatResult(
            chat_history=[{"role": "assistant", "content": content, "terminate": "yes"}],
            messages=[AIMessage(content=content)],
            cost={},
            terminate="yes",
        )
        if execution_failure:
            # Structured record for consumers reading the result object
            # directly; the terminal summary JSON carries the same key.
            result.execution_failure = execution_failure
        return result

    async def process_command(
        self,
        command: str,
        *args: Any,
        current_url: str | None = None,
        **kwargs: Any,
    ) -> GraphChatResult | None:

        # Binding findings are per-run: reusing this instance for another
        # command must start with a clean observer accumulator.
        self._invariant_binding = []

        if current_url is None and args:
            current_url = str(args[0]) if args[0] else None

        task = command
        if current_url:
            task = f"{command}\n\nCurrent Page: {current_url}"
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
                "progress_stall_count": 0,
                "step_token_log": [],
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_cost": 0.0,
                "cost_available": False,
                "step_timings": [],
                "completed_step_signatures": [],
                "last_helper_response": "",
                "current_url": current_url or "",
            }
            # run_name/metadata attribute the graph run for execution tracing;
            # both are inert unless a tracer is enabled via the environment.
            final_state = await self._graph.ainvoke(
                initial,
                config={
                    "recursion_limit": 2000,
                    "run_name": "hercules-agent",
                    "metadata": {"stake_id": self.stake_id},
                },
            )

            # Observer verdict: any BINDING invariant finding recorded during
            # the run marks the final result failed, whatever the planner
            # claimed — the scenario is recorded red, the run itself is done.
            if self._invariant_binding:
                final_state = self._apply_invariant_failure(final_state)

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
                if isinstance(message, SystemMessage):
                    role = "system"
                elif isinstance(message, HumanMessage):
                    role = "user"
                else:
                    role = "assistant"
                history.append({"role": role, "content": getattr(message, "content", "")})
            terminate = "yes" if final_state.get("terminate") == "yes" else "no"
            if history and terminate == "yes":
                history[-1]["terminate"] = "yes"
            result = GraphChatResult(
                chat_history=history,
                messages=messages,
                cost=self._build_cost_metrics(final_state),
                terminate=terminate,
                last_helper_response=str(final_state.get("last_helper_response") or ""),
            )
            self._last_graph_result = result
            return result
        except openai.BadRequestError as bre:
            if self._is_context_limit_error(bre):
                logger.error(
                    'Context limit exceeded even after compression for command: "%s". %s',
                    command,
                    bre,
                    exc_info=True,
                )
            else:
                logger.error('Unable to process command: "%s". %s', command, bre, exc_info=True)
            return self._failed_graph_result(command, bre)
        except Exception as exc:
            # Never let an unexpected failure escape process_command: the scenario
            # must be recorded as failed (junit/report downstream) instead of the
            # exception aborting the run and silently skipping remaining scenarios.
            logger.error('Unexpected error while processing command: "%s". %s', command, exc, exc_info=True)
            return self._failed_graph_result(command, exc)
        return None
