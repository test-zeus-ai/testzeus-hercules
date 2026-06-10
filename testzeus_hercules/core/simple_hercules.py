"""LangGraph-based orchestration for Hercules test execution."""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from string import Template
from typing import Annotated, Any, Dict, Literal, Optional, TypedDict, cast

import nest_asyncio
import openai
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

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
from testzeus_hercules.core.memory.state_handler import store_run_data
from testzeus_hercules.core.post_process_responses import (
    final_reply_callback_planner_agent as notify_planner_messages,
)
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools import *  # noqa: F403
from testzeus_hercules.core.tools.get_url import geturl
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.detect_llm_loops import is_agent_stuck_in_loop
from testzeus_hercules.utils.llm_helper import (
    GraphChatResult,
    convert_model_config_to_langchain_format,
    create_multimodal_agent,
    extract_target_helper,
    format_plan_steps,
    is_agent_planner_termination_message,
    messages_to_chat_history,
    parse_agent_response,
    process_chat_target_helper,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.response_parser import parse_response
from testzeus_hercules.utils.timestamp_helper import get_timestamp_str
from testzeus_hercules.utils.ui_messagetype import MessageType

nest_asyncio.apply()

HELPER_ALIASES: dict[str, str] = {
    "browser": "browser_nav_agent",
    "api": "api_nav_agent",
    "sec": "sec_nav_agent",
    "sql": "sql_nav_agent",
    "time_keeper": "time_keeper_nav_agent",
    "mcp": "mcp_nav_agent",
    "executor": "executor_nav_agent",
    "agent": "helper_agent",
}


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    plan: str
    current_step_number: int
    target_helper: str
    terminate: str
    helper_rounds: int


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
        self._planner_turns = 0
        self._last_graph_result: GraphChatResult | None = None

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

        for cfg in [planner_agent_config, nav_agent_config, mem_agent_config, helper_agent_config]:
            model = cfg["model_config_params"].get("model") or cfg["model_config_params"].get("model_name")
            cfg["llm_config_params"] = adapt_llm_params_for_model(model, cfg["llm_config_params"])

        self.agents_map = await self._initialize_agents()
        self._graph = self._build_graph()
        return self

    async def _initialize_agents(self) -> dict[str, Any]:
        agents: dict[str, Any] = {}
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
            system_message=(
                "You are a visual comparison agent. You can compare images and provide feedback. "
                "Your only purpose is to do visual comparison of images"
            ),
        )

        config = get_global_conf()
        if config.should_use_dynamic_ltm():
            mem_model = convert_model_config_to_langchain_format(self.mem_agent_config["model_config_params"])
            llm_config = {**mem_model, **self.mem_agent_config["llm_config_params"]}
            namespace = f"{self.stake_id}_{config.timestamp}"
            self.memory = DynamicLTM(namespace=namespace, llm_config=llm_config)

        return agents

    def _resolve_helper_key(self, target_helper: str) -> str | None:
        if not target_helper or target_helper == "Not_Applicable":
            return None
        key = HELPER_ALIASES.get(target_helper.strip())
        if key and key in self.agents_map:
            return key
        candidate = f"{target_helper}_nav_agent"
        if candidate in self.agents_map:
            return candidate
        return None

    def _get_helper_agent(self, state: AgentState) -> Any:
        key = self._resolve_helper_key(state.get("target_helper", ""))
        if not key:
            raise ValueError(f"Unknown target_helper: {state.get('target_helper')}")
        return self.agents_map[key]

    def _notify_from_parsed(self, parsed: dict[str, Any], message: str, message_type: MessageType) -> None:
        notify_planner_messages(
            message,
            message_type=message_type,
            stake_id=self.stake_id,
            helper_name=parsed.get("target_helper", ""),
            is_assert=bool(parsed.get("is_assert")),
            is_passed=bool(parsed.get("is_passed")),
            assert_summary=str(parsed.get("assert_summary", "")),
            is_terminated=bool(parsed.get("is_terminated")),
            is_completed=bool(parsed.get("is_completed")),
            final_response=str(parsed.get("final_response", "")),
        )

    def _extract_tokens(self, response: Any) -> dict[str, Any]:
        usage = None
        if hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("token_usage")
        if not usage:
            usage = getattr(response, "usage_metadata", None)
        if not usage:
            usage = getattr(response, "usage", None)
        return usage or {}

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
        """
        Compress entire message history into a single HumanMessage summary.
        Called only as a last resort when context limit is hit.
        Each message is reduced to one line capped at 300 chars.
        """
        lines = []
        for m in messages:
            content = str(getattr(m, "content", "") or "")
            if not content:
                continue
            # Include tool call names if present
            tool_calls = getattr(m, "tool_calls", [])
            if tool_calls:
                names = ", ".join(tc.get("name", "") for tc in tool_calls if isinstance(tc, dict))
                content = f"[tool_calls: {names}] {content}"
            lines.append(f"[{m.type}] {content[:300]}")
        summary = "\n".join(lines)
        return [HumanMessage(content=f"COMPRESSED HISTORY (context limit reached):\n{summary}")]

    async def _ainvoke_with_context_fallback(
        self, llm: Any, messages: list[AnyMessage], system_message: str
    ) -> Any:
        """
        Invoke LLM normally. If context limit is hit, compress all messages
        into a single summary and retry once. Raises on any other error.
        """
        try:
            raise Exception("context_length_exceeded")
        except Exception as e:
            if not self._is_context_limit_error(e):
                raise
            return await llm.ainvoke(messages)
            compressed = self._compress_messages(messages)
            retry_messages = [SystemMessage(content=system_message), *compressed]
            return await llm.ainvoke(retry_messages)

    async def _planner_node(self, state: AgentState) -> dict[str, Any]:
        self._planner_turns += 1
        if self._planner_turns > self.planner_number_of_rounds:
            return {"terminate": "yes", "messages": [AIMessage(content='{"terminate":"yes","final_response":"Max planner rounds exceeded"}')]}

        planner: PlannerAgent = self.agents_map["planner_agent"]
        # Trim and truncate history to avoid context limit on proxy
        recent = state["messages"][-3:]
        trimmed = []
        for m in recent:
            mc = getattr(m, "content", "") or ""
            if isinstance(mc, str) and len(mc) > 2000:
                try:
                    # Preserve required fields (e.g. tool_call_id for ToolMessage)
                    extra = {k: v for k, v in m.__dict__.items() 
                             if k not in ("content", "type") and not k.startswith("_")}
                    m = m.__class__(content=mc[:2000] + "...[truncated]", **extra)
                except Exception:
                    pass  # keep original if truncation fails
            trimmed.append(m)
        messages = [SystemMessage(content=planner.system_message), *trimmed]
        logger.warning("[PLANNER_DEBUG] sending %d messages, system_msg_len=%d", len(messages), len(planner.system_message))
        response = await self._ainvoke_with_context_fallback(
            planner.llm, messages, planner.system_message
        )

        state["messages"].append(HumanMessage(content="x" * 100000))

        content = str(response.content) if response.content else ""
        logger.warning("[PLANNER_DEBUG] raw response type=%s content=%s kwargs=%s tool_calls=%s", type(response), repr(getattr(response, "content", response))[:300], repr(getattr(response, "additional_kwargs", {}))[:300], repr(getattr(response, "tool_calls", []))[:200])
        planner.on_planner_message(content)
        self.save_to_memory(content)

        parsed = parse_agent_response(content)
        updates: dict[str, Any] = {
            "messages": [response],
            "terminate": str(parsed.get("terminate", "no")),
            "target_helper": str(parsed.get("target_helper", "Not_Applicable")),
            "plan": str(parsed.get("plan", "") or state.get("plan", "")),
            "helper_rounds": 0,
        }

        plan = parsed.get("plan")
        if plan is not None and isinstance(plan, list):
            self._notify_from_parsed(parsed, format_plan_steps(plan), MessageType.PLAN)
        elif parsed.get("next_step"):
            self._notify_from_parsed(parsed, str(parsed["next_step"]), MessageType.STEP)
        elif updates["terminate"] != "yes":
            self._notify_from_parsed(parsed, "Received no response, terminating..", MessageType.INFO)

        if updates["terminate"] == "yes" and parsed.get("final_response"):
            notify_planner_messages(str(parsed["final_response"]), message_type=MessageType.ANSWER)

        return updates

    async def _helper_node(self, state: AgentState) -> dict[str, Any]:
        helper = self._get_helper_agent(state)
        tools = getattr(helper, "tools", [])
        llm_with_tools = helper.llm.bind_tools(tools) if tools else helper.llm
        system_message = getattr(helper, "system_message", "")
        messages = [SystemMessage(content=system_message), *state.get("messages", [])]
        response = await self._ainvoke_with_context_fallback(
            llm_with_tools, messages, system_message
        )
        return {
            "messages": [response],
            "helper_rounds": state.get("helper_rounds", 0) + 1,
        }

    async def _tools_node(self, state: AgentState) -> dict[str, Any]:
        helper = self._get_helper_agent(state)
        tools = getattr(helper, "tools", [])
        result = await ToolNode(tools).ainvoke(state)
        return cast(dict[str, Any], result)

    def _route_after_planner(self, state: AgentState) -> Literal["helper", "end"]:
        if state.get("terminate") == "yes":
            return "end"
        if self._resolve_helper_key(state.get("target_helper", "")):
            last = state["messages"][-1] if state["messages"] else None
            if isinstance(last, AIMessage):
                parsed = parse_agent_response(str(last.content or ""))
                if parsed.get("next_step") or parsed.get("plan"):
                    return "helper"
        return "end"

    def _route_after_helper(self, state: AgentState) -> Literal["tools", "planner"]:
        messages = state["messages"]
        if not messages:
            return "planner"
        last = messages[-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            if state.get("helper_rounds", 0) >= self.nav_agent_number_of_rounds:
                return "planner"
            if is_agent_stuck_in_loop(messages_to_chat_history(messages)):
                return "planner"
            return "tools"
        content = str(getattr(last, "content", "") or "")
        if "##TERMINATE TASK##" in content:
            cleaned = content.replace("##TERMINATE TASK##", "").strip()
            if "##FLAG::SAVE_IN_MEM##" in cleaned:
                mem = "Context from execution of previous steps: " + cleaned.replace("##FLAG::SAVE_IN_MEM##", "").strip()
                self.save_to_memory(mem)
                store_run_data(mem)
            parsed = parse_agent_response(str(state["messages"][0].content if state["messages"] else ""))
            notify_planner_messages(
                cleaned,
                message_type=MessageType.STEP,
                stake_id=self.stake_id,
                helper_name=state.get("target_helper", ""),
                is_completed=True,
                final_response=str(parsed.get("final_response", "")),
            )
        return "planner"

    def _build_graph(self) -> Any:
        graph = StateGraph(AgentState)
        graph.add_node("planner", self._planner_node)
        graph.add_node("helper", self._helper_node)
        graph.add_node("tools", self._tools_node)
        graph.set_entry_point("planner")
        graph.add_conditional_edges("planner", self._route_after_planner, {"helper": "helper", "end": END})
        graph.add_conditional_edges("helper", self._route_after_helper, {"tools": "tools", "planner": "planner"})
        graph.add_edge("tools", "helper")
        return graph.compile()

    async def shutdown(self) -> None:
        await self.clean_up_plan()
        for agent in self.agents_map.values():
            if hasattr(agent, "shutdown"):
                result = agent.shutdown()
                if asyncio.iscoroutine(result):
                    await result
        from testzeus_hercules.utils.mcp_helper import MCPHelper
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
        self._planner_turns = 0
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
        current_url_prompt_segment = f"Current Page: {current_url}" if current_url else ""
        prompt = Template(LLM_PROMPTS["COMMAND_EXECUTION_PROMPT"]).substitute(
            command=command,
            current_url_prompt_segment=current_url_prompt_segment,
        )
        if get_global_conf().should_use_dynamic_ltm():
            prompt += "\n\nEXTRA INFORMATION: " + await self._query_memory(prompt)

        logger.info("Prompt for command: %s", prompt)
        try:
            if self._graph is None:
                raise ValueError("Graph is not initialized.")
            self._planner_turns = 0
            initial: AgentState = {
                "messages": [HumanMessage(content=prompt)],
                "plan": "",
                "current_step_number": 0,
                "target_helper": "",
                "terminate": "no",
                "helper_rounds": 0,
            }
            final_state = await self._graph.ainvoke(initial, config={"recursion_limit": 500})
            messages = final_state.get("messages", [])
            history = messages_to_chat_history(messages)
            terminate = str(final_state.get("terminate", "no"))
            for entry in reversed(history):
                content = str(entry.get("content", ""))
                if is_agent_planner_termination_message(content):
                    entry["terminate"] = "yes"
                    break
            result = GraphChatResult(
                chat_history=history,
                messages=messages,
                terminate=terminate,
            )
            self._last_graph_result = result
            return result
        except openai.BadRequestError as bre:
            if self._is_context_limit_error(bre):
                logger.error('Context limit exceeded even after compression for command: "%s". %s', command, bre)
            else:
                logger.error('Unable to process command: "%s". %s', command, bre)
            traceback.print_exc()
        return None