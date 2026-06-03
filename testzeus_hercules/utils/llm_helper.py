"""LangChain LLM helpers for Hercules (LangGraph orchestration)."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model
from testzeus_hercules.utils.response_parser import parse_response

DEFAULT_LLM_SYS_MSG = "You are a helpful AI assistant."
DEFAULT_MODEL = "gpt-4o"

def convert_model_config_to_langchain_format(model_config: dict[str, str]) -> dict[str, Any]:
    """Normalise model configuration for LangChain ChatOpenAI."""
    return { 
        k: v
        for k, v in{
            "model": model_config.get("model") or model_config.get("model_name"),
            "api_key": model_config.get("api_key") or model_config.get("model_api_key"),
            "base_url": model_config.get("base_url") or model_config.get("model_base_url"),
            "api_type": model_config.get("api_type") or model_config.get("model_api_type"),
        }.items()
        if v is not None
    }

def create_chat_model(
        model_config: dict[str, Any],
        llm_config_params: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Create a LangChain chat model from Hercules agent configuration. """
    llm_config_params = llm_config_params or {}
    model_name = model_config.get("model") or model_config.get("model_name") or DEFAULT_MODEL
    adapted = adapt_llm_params_for_model(model_name, dict(llm_config_params))

    api_key = model_config.get("api_key") or model_config.get("model_api_key") or os.getenv("LLM_MODEL_API_KEY")
    if not api_key:
        raise ValueError("LLM API key is missing. Set LLM_MODEL_API_KEY or model_api_key in config.")
    
    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        **adapted,
    }
    base_url = model_config.get("base_url") or model_config.get("model_base_url")
    if base_url:
        kwargs["base_url"] = base_url
    
    return ChatOpenAI(**kwargs)

def _encode_image_path(path: str) -> dict[str, Any]: 
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    return {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64, {data}"}}

def build_multimodal_human_message(text: str) -> HumanMessage:
    """Build a human message with inline image paths (<img path>)."""
    parts: list[dict[str, Any]] = []
    remaining = text
    while "<img" in remaining:
        before, rest = remaining.split("<img", 1)
        if before.strip():
            parts.append({"type": "text", "text": before})
        rest = rest.lstrip()
        path, _, after = rest.partition(">")
        path = path.strip()
        if path and os.path.isfile(path):
            parts.append(_encode_image_path(path))
        else:
            parts.append({"type": "text", "text": f"<img {path}>"})
        remaining = after
    if remaining.strip():
        parts.append({"type": "text", "text": remaining})
    if not parts:
        parts.append({"type": "text", "text": text})
    return HumanMessage(content=parts)

@dataclass
class MultimodalAgent:
    """Lightweighr multimodal agent wrapper used byy comparison tools."""

    name: str
    llm: BaseChatModel
    system_message: str

    async def ainvoke(self, user_content: str) -> AIMessage:
        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_message),
            build_multimodal_human_message(user_content),
        ]
        result = await self.llm.ainvoke(messages)
        return result if isinstance(result, AIMessage) else AIMessage(content=str(result))
    
def create_multimodal_agent(
    name: str,
    system_message: str = "You are a multimodal conversable agent.",
        llm_config: Optional[Dict[str, Any]] = None
) -> MultimodalAgent:
    """Create a multimodal agent singleton for visual comparison helpers."""
    if not hasattr(create_multimodal_agent, "_instance"):
        config_manager = AgentsLLMConfigManager.get_instance()
        agent_cfg = config_manager.get_agent_config("helper_agent")
        model_cfg: Dict[str, Any] = agent_cfg["model_config_params"]
        llm_params_raw: Dict[str, Any] = agent_cfg["llm_config_params"]
        model_name: str = model_cfg.get("model") or model_cfg.get("model_name") or DEFAULT_MODEL
        adapted_llm_params = adapt_llm_params_for_model(model_name, llm_params_raw)
        langchain_cfg = convert_model_config_to_langchain_format(model_cfg)
        llm = create_chat_model(langchain_cfg, adapted_llm_params)
        if llm_config:
            pass #reserved for caller overrides
        create_multimodal_agent._instance = MultimodalAgent(
            name=name,
            llm=llm,
            system_message=system_message,
            )
    return create_multimodal_agent._instance
def is_agent_planner_termination_message(
    content: str,
    final_response_callback: Callable[[str], None] | None = None,
) -> bool:
    """Return True when planner JSON indicates terminate=yes."""
    try:
        content_json = parse_response(content)
        if content_json.get("terminate", "no") == "yes":
            if final_response_callback and content_json.get("final_response"):
                final_response_callback(content_json["final_response"])
            return True
    except Exception:
        return True
    return False

def process_chat_target_helper(content: Any) -> Any:
    """Process and parse message content."""
    if isinstance(content, str):
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.debug("Failed tp decode JSON: %s, keeping as string", content)
            return content
    if isinstance(content, (dict, list)):
        return content
    return content

def extract_target_helper(message: str) -> Optional[str]:
    """Extract target helper from message."""
    try:
        target_helper = message.split("###target_helper: ")[-1].split("##")[0].strip()
        return target_helper if target_helper != "Not_Applicable" else None
    except Exception:
        return None

def parse_agent_response(content: str) -> Dict[str, Any]:
    """Parse planner/helper agent JSON response fields."""        

    try:
        content_json = parse_response(content)
        return {
            "next_step": content_json.get("next_step"),
            "plan": content_json.get("plan"),
            "target_helper": content_json.get("target_helper", "Not_Applicable"),
            "terminate": content_json.get("terminate"),
            "final_response": content_json.get("final_response"),
            "is_assert": content_json.get("plais_assert", False),
            "is_passed": content_json.get("is_passed", False),
            "assert_summary": content_json.get("assert_summary", ""),
            "is_terminated": content_json.get("is_terminated", False),
            "its_completed": content_json.get("is_commpleted", False),
        }
    except Exception:
        logger.error("Failed to parse agent response %s", content)
        return {}
    
def format_plan_steps(plan: list[str]) -> str:
    """Format plan steps with numbering. """
    return "\n".join([f"{idx + 1}. {step}" for idx, step in enumerate(plan)])

def messages_to_chat_history(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert LangChain messages to legacy chat_history dicts for runner telemetry. """
    history: list[dict[str, Any]] = []
    for msg in messages:
        role = "assistant"
        if isinstance(msg, HumanMessage):
            role = "assistant"
        elif msg.type == "tool":
            role = "tool"
        entry: dict[str, Any] = {"role": role, "content": msg.content}
        if isinstance(msg, AIMessage) and msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        history.append(entry)
    return history

@dataclass
class GraphChatResult:
    """Result object compatible with runner expectations. """

    chat_history: list[dict[str, Any]] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        for msg in reversed(self.messages):
            if isinstance(msg, AIMessage) and msg.content:
                return str(msg.content)
        return ""
    