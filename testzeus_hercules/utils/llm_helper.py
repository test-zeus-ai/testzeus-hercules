"""LangChain LLM helpers for Hercules (LangGraph orchestration)."""

from __future__ import annotations

import base64
import json
import os
from urllib.parse import urlparse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model
from testzeus_hercules.utils.response_parser import parse_response

DEFAULT_LLM_SYS_MSG = "You are a helpful AI assistant."
DEFAULT_MODEL = "gpt-4o"
DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_LLM_MAX_RETRIES = 1


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %.1fs.", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d.", name, raw, default)
        return default


def get_llm_request_timeout_seconds() -> float:
    """Return the hard per-request LLM timeout in seconds."""
    return _env_float(
        "LLM_REQUEST_TIMEOUT",
        _env_float("PORTKEY_TIMEOUT", DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS),
    )


def get_llm_max_retries() -> int:
    """Return the default number of provider retries for LLM calls."""
    return _env_int("LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES)


def convert_model_config_to_langchain_format(
    model_config: dict[str, str],
) -> dict[str, Any]:
    """Normalise model configuration for LangChain ChatOpenAI."""
    return {
        k: v
        for k, v in {
            "model": model_config.get("model") or model_config.get("model_name"),
            "api_key": model_config.get("api_key") or model_config.get("model_api_key"),
            "base_url": model_config.get("base_url") or model_config.get("model_base_url"),
            "api_type": model_config.get("api_type") or model_config.get("model_api_type"),
        }.items()
        if v is not None
    }


# OpenAI-compatible endpoints of the providers listed in agents_llm_config-example.json. Every model
# is called through an OpenAI-style client, so `model_api_type` selects the endpoint when no explicit
# base_url is configured.
_PROVIDER_DEFAULT_BASE_URLS = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "anthropic": "https://api.anthropic.com/v1/",
}
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
# With no api_type and no base_url, these model-name prefixes can only mean one provider.
_MODEL_PREFIX_PROVIDERS = (("gemini", "google"), ("claude", "anthropic"))
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_KEYLESS_PLACEHOLDER_API_KEY = "not-needed"


def _first_config_value(model_config: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = model_config.get(key)
        if value not in (None, ""):
            return value
    return None


def _is_local_url(url: Optional[str]) -> bool:
    try:
        return bool(url) and (urlparse(str(url)).hostname or "").lower() in _LOCAL_HOSTS
    except ValueError:
        return False


def resolve_provider_settings(model_config: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    """Work out how to reach the model from ``model_api_type`` / ``base_url`` / ``api_version`` / ``client_host``.

    Returns ``{"api_type", "base_url", "api_version", "azure", "keyless"}``: ``azure`` selects the Azure OpenAI
    client (needs ``api_version``), ``keyless`` means the endpoint is local so a missing API key is fine.
    """
    api_type = str(_first_config_value(model_config, "api_type", "model_api_type") or "").strip().lower()
    base_url = _first_config_value(model_config, "base_url", "model_base_url")
    api_version = _first_config_value(model_config, "api_version", "model_api_version")
    client_host = _first_config_value(model_config, "client_host", "model_client_host")

    if not api_type and not base_url and not os.getenv("OPENAI_BASE_URL") and not os.getenv("OPENAI_API_BASE"):
        for prefix, provider in _MODEL_PREFIX_PROVIDERS:
            if model_name.lower().startswith(prefix):
                api_type = provider
                break

    azure = api_type == "azure" and bool(api_version)
    if api_type == "ollama" and not base_url:
        host = str(client_host).rstrip("/") if client_host else ""
        base_url = (host if host.endswith("/v1") else f"{host}/v1") if host else DEFAULT_OLLAMA_BASE_URL
    elif not azure and not base_url:
        base_url = _PROVIDER_DEFAULT_BASE_URLS.get(api_type)

    return {
        "api_type": api_type,
        "base_url": str(base_url) if base_url else None,
        "api_version": str(api_version) if api_version else None,
        "azure": azure,
        "keyless": api_type == "ollama" or _is_local_url(base_url),
    }


def build_chat_client(kwargs: Dict[str, Any], settings: Dict[str, Any]) -> BaseChatModel:
    """Instantiate the chat client for ``kwargs`` (model, api_key, timeout, ... ) and resolved ``settings``."""
    kwargs = dict(kwargs)
    if settings["azure"]:
        kwargs.pop("base_url", None)
        # azure_endpoint=None lets the client fall back to AZURE_OPENAI_ENDPOINT.
        return AzureChatOpenAI(azure_endpoint=settings["base_url"], api_version=settings["api_version"], azure_deployment=kwargs.get("model"), **kwargs)
    if settings["base_url"]:
        kwargs["base_url"] = settings["base_url"]
    return ChatOpenAI(**kwargs)


def create_chat_model(
    model_config: dict[str, Any],
    llm_config_params: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Create a LangChain chat model from Hercules agent configuration."""
    llm_config_params = llm_config_params or {}
    model_name = model_config.get("model") or model_config.get("model_name") or DEFAULT_MODEL
    adapted = adapt_llm_params_for_model(model_name, dict(llm_config_params))

    settings = resolve_provider_settings(model_config, model_name)
    api_key = model_config.get("api_key") or model_config.get("model_api_key") or os.getenv("MODEL_API_KEY")
    if not api_key and settings["keyless"]:
        api_key = _KEYLESS_PLACEHOLDER_API_KEY  # local servers (Ollama, llama.cpp, ...) do not check the key
    if not api_key:
        raise ValueError("LLM API key is missing. Set MODEL_API_KEY or model_api_key in config.")

    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        **adapted,
    }
    if kwargs.get("timeout") is None:
        kwargs["timeout"] = get_llm_request_timeout_seconds()
    if kwargs.get("max_retries") is None:
        kwargs["max_retries"] = get_llm_max_retries()

    return build_chat_client(kwargs, settings)


def _encode_image_path(path: str) -> dict[str, Any]:
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/{mime};base64,{data.strip()}"},
    }


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
    llm_config: Optional[Dict[str, Any]] = None,
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
            pass  # reserved for caller overrides
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
        result: Dict[str, Any] = {
            "next_step": content_json.get("next_step"),
            "plan": content_json.get("plan"),
            "target_helper": content_json.get("target_helper", "Not_Applicable"),
            "terminate": content_json.get("terminate"),
            "final_response": content_json.get("final_response"),
            "is_assert": content_json.get("is_assert", False),
            "is_passed": content_json.get("is_passed", False),
            "assert_summary": content_json.get("assert_summary", ""),
            "is_terminated": content_json.get("is_terminated", False),
            "is_completed": content_json.get("is_completed", False),
        }
        # Harness failure records ride on terminal results so the report side
        # can render category/stage/evidence alongside the failure message.
        if content_json.get("execution_failure") is not None:
            result["execution_failure"] = content_json["execution_failure"]
        return result
    except Exception:
        logger.error("Failed to parse agent response %s", content)
        return {}


def format_plan_steps(plan: list[str]) -> str:
    """Format plan steps with numbering."""
    return "\n".join([f"{idx + 1}. {step}" for idx, step in enumerate(plan)])


def messages_to_chat_history(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert LangChain messages to legacy chat_history dicts for runner telemetry."""
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


def _looks_like_planner_json(parsed: dict[str, Any]) -> bool:
    return any(key in parsed for key in ("terminate", "next_step", "plan", "target_helper"))


def _is_non_planner_content(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return True
    if stripped.startswith(("import ", "from ", "In []:", "```python")):
        return True
    if "def run_task" in stripped or "sync_playwright" in stripped:
        return True
    return False


def extract_planner_summary(messages: list[BaseMessage]) -> str:
    """Return the best planner JSON string from graph messages (not helper/tool output)."""
    termination_summary: str | None = None
    last_planner: str | None = None

    for msg in reversed(messages):
        if not isinstance(msg, AIMessage) or getattr(msg, "tool_calls", None):
            continue
        content = str(msg.content or "").strip()
        # Planner-JSON detection must run BEFORE the non-planner substring
        # heuristic: a terminal planner JSON that merely mentions e.g.
        # "sync_playwright" is still a planner decision and must never be
        # discarded. The heuristic only applies to messages that are not
        # planner-shaped — and any such message is skipped regardless, so the
        # heuristic can no longer drop a verdict.
        try:
            parsed = parse_response(content)
        except Exception:
            continue
        if _looks_like_planner_json(parsed):
            last_planner = content
            if parsed.get("terminate") == "yes":
                termination_summary = content
                break
            continue
        if _is_non_planner_content(content):
            continue

    return termination_summary or last_planner or ""


@dataclass
class GraphChatResult:
    """Result object compatible with runner expectations."""

    chat_history: list[dict[str, Any]] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    terminate: str = "no"
    # Last non-empty navigation-helper answer; carries the real PASS/FAIL
    # outcome when the final planner message is not a terminal decision.
    last_helper_response: str = ""

    @property
    def summary(self) -> str:
        # 1) A terminal planner JSON wins.
        planner_summary = extract_planner_summary(self.messages)
        if planner_summary and parse_response(planner_summary).get("terminate") == "yes":
            return planner_summary
        # 2) A terminal helper answer (PASS/FAIL verdict or terminate marker)
        # preserves the real test outcome even when the planner's latest
        # message is a non-terminal decision, provided the graph itself ended
        # (terminate == "yes"). When the run was cut short (terminate == "no":
        # planner round limit, abort) only an explicit helper verdict counts: helpers end
        # every successful STEP with the terminate marker, so a bare marker would
        # turn an unfinished run into a pass.
        helper_result = parse_response(self.last_helper_response)
        if helper_result.get("terminate") == "yes" and (self.terminate == "yes" or helper_result.get("is_assert") is True):
            return json.dumps(helper_result)
        # 3) Non-terminal planner response.
        if planner_summary:
            return planner_summary
        # 4) Generic completion message, preferring any helper text over a
        # placeholder when the graph terminated without planner JSON.
        if self.terminate == "yes":
            helper_response = self.last_helper_response.strip()
            if helper_response:
                return json.dumps({"terminate": "yes", "final_response": helper_response})
            return json.dumps(
                {
                    "terminate": "yes",
                    "final_response": "Test ended without planner JSON.",
                }
            )
        return ""
