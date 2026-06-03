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
        self.chat_logs_dir = get_global_conf().get_source_log_folder_path(self.stake_id)
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

        for cfg, key in [
            (planner_agent_config, "planner"),
            (nav_agent_config, "nav"),
            (mem_agent_config, "mem"),
            (helper_agent_config, "helper"),
        ]:
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

        helper_model = convert_model_config_to_langchain_format(self.helper_agent_config["model_config_params"])
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

    async def _planner_node(self, state: AgentState) -> dict[str, Any]:
        self._planner_turns += 1
        if self._planner_turns > self.planner_number_of_rounds:
            return {"terminate": "yes", "messages": [AIMessage(content='{"terminate":"yes","final_response":"Max planner rounds exceeded"}')]}

        planner: PlannerAgent = self.agents_map["planner_agent"]
        response = await planner.llm.ainvoke(
            [SystemMessage(content=planner.system_message), *state["messages"]],
        )
        content = str(response.content) if response.content else ""
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
            plan_text = format_plan_steps(plan)
            self._notify_from_parsed(parsed, plan_text, MessageType.PLAN)
        elif parsed.get("next_step"):
            self._notify_from_parsed(parsed, str(parsed["next_step"]), MessageType.STEP)
        elif not parsed.get("next_step") and updates["terminate"] != "yes":
            self._notify_from_parsed(parsed, "Received no response, terminating..", MessageType.INFO)

        if updates["terminate"] == "yes" and parsed.get("final_response"):
            notify_planner_messages(str(parsed["final_response"]), message_type=MessageType.ANSWER)

        return updates

    async def _helper_node(self, state: AgentState) -> dict[str, Any]:
        helper = self._get_helper_agent(state)
        
        system_message = getattr(helper, "system_message", "")
        llm = helper.llm
        tools = getattr(helper, "tools", [])
        
        logger.info(f"[HELPER_NODE_DEBUG] Helper agent: {helper.agent_name}")
        logger.info(f"[HELPER_NODE_DEBUG] Available tools: {[t.name for t in tools] if tools else 'NONE'}")
        logger.info(f"[HELPER_NODE_DEBUG] Number of tools: {len(tools)}")
        
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        
        messages = list(state.get("messages", []))
        logger.info(f"[HELPER_NODE_DEBUG] Messages count: {len(messages)}")
        if messages:
            logger.info(f"[HELPER_NODE_DEBUG] Last message type: {type(messages[-1]).__name__}")
        
        logger.info(f"[HELPER_NODE_DEBUG] Invoking LLM with {len(tools)} tools bound")
        
        response = await llm_with_tools.ainvoke(
            [
                SystemMessage(content=system_message),
                *messages,
            ]
        )
        
        logger.info(f"[HELPER_NODE_DEBUG] LLM response type: {type(response).__name__}")
        logger.info(f"[HELPER_NODE_DEBUG] LLM response has tool_calls: {hasattr(response, 'tool_calls') and bool(response.tool_calls)}")
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.info(f"[HELPER_NODE_DEBUG] Tool calls: {[tc.get('name') if isinstance(tc, dict) else tc.name for tc in response.tool_calls]}")
        
        return {
            "messages": [response],
            "helper_rounds": state.get("helper_rounds", 0) + 1,
        }

    async def _tools_node(self, state: AgentState) -> dict[str, Any]:
        helper = self._get_helper_agent(state)
        tools = getattr(helper, "tools", [])
        
        logger.info(f"[TOOLS_NODE_DEBUG] Creating ToolNode for agent: {helper.agent_name}")
        logger.info(f"[TOOLS_NODE_DEBUG] Tools available to ToolNode: {[t.name for t in tools] if tools else 'NONE'}")
        logger.info(f"[TOOLS_NODE_DEBUG] Number of tools: {len(tools)}")
        
        if state.get("messages"):
            last_msg = state["messages"][-1]
            logger.info(f"[TOOLS_NODE_DEBUG] Last message type: {type(last_msg).__name__}")
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                logger.info(f"[TOOLS_NODE_DEBUG] Tool calls in message: {[tc.get('name') if isinstance(tc, dict) else tc.name for tc in last_msg.tool_calls]}")
        
        tool_node = ToolNode(tools)
        result = await tool_node.ainvoke(state)
        
        logger.info(f"[TOOLS_NODE_DEBUG] ToolNode execution complete")
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
            history = messages_to_chat_history(messages)
            if is_agent_stuck_in_loop(history):
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
        graph.add_conditional_edges(
            "planner",
            self._route_after_planner,
            {"helper": "helper", "end": END},
        )
        graph.add_conditional_edges(
            "helper",
            self._route_after_helper,
            {"tools": "tools", "planner": "planner"},
        )
        graph.add_edge("tools", "helper")
        return graph.compile()

    def get_chat_logs_dir(self) -> str | None:
        return get_global_conf().get_source_log_folder_path(self.stake_id)

    async def shutdown(self) -> None:
        await self.clean_up_plan()
        for agent in self.agents_map.values():
            if hasattr(agent, "shutdown"):
                result = agent.shutdown()
                if asyncio.iscoroutine(result):
                    await result
        from testzeus_hercules.utils.mcp_helper import MCPHelper

        await MCPHelper.destroy()

    def set_chat_logs_dir(self, chat_logs_dir: str) -> None:
        self.chat_logs_dir = chat_logs_dir

    def save_to_memory(self, content: str) -> None:
        config = get_global_conf()
        if not config.should_use_dynamic_ltm():
            return
        if self.memory:
            self.memory.save_content(content)
        else:
            logger.warning("Memory system not initialized")

    async def clean_up_plan(self) -> None:
        config = get_global_conf()
        if config.should_use_dynamic_ltm() and self.memory:
            self.memory.clear()
        self._planner_turns = 0
        logger.info("Plan cleaned up.")

    async def _query_memory(self, context: str) -> str:
        config = get_global_conf()
        if not config.should_use_dynamic_ltm():
            return ""
        if self.memory:
            return await self.memory.query(context)
        return ""

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
        config = get_global_conf()
        if config.should_use_dynamic_ltm():
            mem_fetch = await self._query_memory(prompt)
            prompt += "\n\nEXTRA INFORMATION: " + mem_fetch

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
            final_state = await self._graph.ainvoke(initial)
            messages = final_state.get("messages", [])
            history = messages_to_chat_history(messages)
            if history and is_agent_planner_termination_message(str(history[-1].get("content", ""))):
                history[-1]["terminate"] = "yes"
            result = GraphChatResult(chat_history=history, messages=messages)
            self._last_graph_result = result
            print("\n\nFINAL HISTORY:")
            for i, msg in enumerate(history):
                print(f"{i}: {msg}")
            return result
        except openai.BadRequestError as bre:
            logger.error('Unable to process command: "%s". %s', command, bre)
            traceback.print_exc()
        return None
