"""Small chat-completion requests that measure what a model server really supports.

Each probe is one tiny, deterministic request (temperature 0, a few output
tokens): tool calling, forced ``tool_choice``, ``json_schema`` structured
output, and the red/blue colour-flip image canary that catches servers which
silently drop or reuse image input.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any

import httpx

from testzeus_hercules.llm.capabilities import ImageMode
from testzeus_hercules.llm.client import CACHE_HIT_HEADER, NO_CACHE_BODY
from testzeus_hercules.llm.checks import CheckResult
from testzeus_hercules.llm.providers import Endpoint
from testzeus_hercules.llm.server_info import auth_headers

_TOOL_MAX_TOKENS = 64
_JSON_MAX_TOKENS = 32
_IMAGE_MAX_TOKENS = 16
_CANARY_SIZE = (64, 64)
_CANARY_COLOURS = {"red": (255, 0, 0), "blue": (0, 0, 255)}

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current time in a timezone.",
            "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}, "required": ["timezone"]},
        },
    },
]
_TOOL_NAMES = frozenset(tool["function"]["name"] for tool in _TOOLS)
_TOOL_PROMPT = "What is the weather in Paris right now? Answer by calling one of the available tools."
_FORCED_TOOL = "get_time"

# Where OpenAI-compatible servers put a reasoning model's thinking.
_REASONING_FIELDS = ("reasoning", "reasoning_content")
REASONING_HINT = 'set llm.request_params (or HERCULES_LLM_REQUEST_PARAMS) so it answers directly, for example {"reasoning_effort": "none"}, ' "then run `hercules doctor --no-cache`"

_JSON_SCHEMA: dict[str, Any] = {
    "name": "answer",
    "strict": True,
    "schema": {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"], "additionalProperties": False},
}


@dataclass(frozen=True)
class ChatReply:
    status_code: int
    message: dict[str, Any] | None
    error: str | None = None
    finish_reason: str | None = None
    reasoning_tokens: int = 0
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.message is not None

    @property
    def reasoned_without_answer(self) -> bool:
        """True when a reasoning model spent the reply on reasoning and gave no answer (no text, no tool call)."""
        message = self.message or {}
        if not self.ok or _text(message).strip() or _called_tools(message):
            return False
        reasoned = self.reasoning_tokens > 0 or any(isinstance(message.get(key), str) and message[key].strip() for key in _REASONING_FIELDS)
        return reasoned or self.finish_reason == "length"

    def describe_error(self) -> str:
        return f"HTTP {self.status_code}" if self.status_code else (self.error or "request failed")


class ChatProbe:
    """Sends one chat-completion request for a fixed endpoint and model."""

    def __init__(self, client: httpx.AsyncClient, endpoint: Endpoint, model_id: str, timeout_s: float, *, request_params: dict[str, Any] | None = None) -> None:
        self._client = client
        self._endpoint = endpoint
        self._model_id = model_id
        self._timeout_s = timeout_s
        self._request_params = dict(request_params or {})
        self.cache_hits = 0

    async def send(self, messages: list[dict[str, Any]], *, max_tokens: int, **extra: Any) -> ChatReply:
        body = {**self._request_params, "model": self._model_id, "messages": messages, "temperature": 0, "max_tokens": max_tokens, "stream": False, **extra}
        if self._endpoint.no_cache:
            body.update(NO_CACHE_BODY)
        url = self._endpoint.base_url.rstrip("/") + "/chat/completions"
        try:
            response = await self._client.post(url, json=body, headers=auth_headers(self._endpoint), timeout=self._timeout_s)
        except httpx.HTTPError as exc:
            return ChatReply(0, None, exc.__class__.__name__)
        if response.status_code >= 400:
            return ChatReply(response.status_code, None)
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            return ChatReply(response.status_code, None, "malformed chat completion")
        if not isinstance(message, dict):
            return ChatReply(response.status_code, None, "malformed chat completion")
        finish = choice.get("finish_reason") if isinstance(choice, dict) else None
        cached = CACHE_HIT_HEADER in response.headers
        self.cache_hits += cached
        return ChatReply(response.status_code, message, None, finish if isinstance(finish, str) else None, _reasoning_tokens(payload), cached)


def _reasoning_tokens(payload: Any) -> int:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    count = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return count if isinstance(count, int) else 0


def _reasoning_check(name: str) -> CheckResult:
    detail = f"not measured: the model spent the probe's output on reasoning and returned no answer (a reasoning model); {REASONING_HINT}"
    return CheckResult(name=name, status="warn", detail=detail, fix=REASONING_HINT)


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def _called_tools(message: dict[str, Any]) -> list[str]:
    calls = message.get("tool_calls")
    names = []
    for call in calls if isinstance(calls, list) else ():
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.append(function["name"])
    return names


async def probe_tool_calling(chat: ChatProbe, provider: str) -> tuple[CheckResult, bool]:
    reply = await chat.send([{"role": "user", "content": _TOOL_PROMPT}], max_tokens=_TOOL_MAX_TOKENS, tools=_TOOLS)
    if not reply.ok:
        return CheckResult(name="tool_calling", status="fail", detail=f"tool request rejected ({reply.describe_error()})", fix=_tool_fix(provider)), False
    if reply.reasoned_without_answer:
        return _reasoning_check("tool_calling"), False
    called = [name for name in _called_tools(reply.message or {}) if name in _TOOL_NAMES]
    if not called:
        return CheckResult(name="tool_calling", status="fail", detail="the model answered in text instead of calling a tool", fix=_tool_fix(provider)), False
    return CheckResult(name="tool_calling", status="ok", detail=f"called {called[0]}"), True


async def probe_tool_choice(chat: ChatProbe) -> tuple[CheckResult, bool]:
    forced = {"type": "function", "function": {"name": _FORCED_TOOL}}
    reply = await chat.send([{"role": "user", "content": _TOOL_PROMPT}], max_tokens=_TOOL_MAX_TOKENS, tools=_TOOLS, tool_choice=forced)
    if reply.reasoned_without_answer:
        return _reasoning_check("tool_choice"), False
    if reply.ok and _FORCED_TOOL in _called_tools(reply.message or {}):
        return CheckResult(name="tool_choice", status="ok", detail="forced tool_choice honoured"), True
    reason = f"rejected ({reply.describe_error()})" if not reply.ok else "ignored"
    return CheckResult(name="tool_choice", status="warn", detail=f"tool_choice {reason}; Hercules will steer tool use through the prompt instead"), False


async def probe_json_schema(chat: ChatProbe) -> tuple[CheckResult, bool]:
    messages = [{"role": "user", "content": "What is 2 + 3? Reply as JSON."}]
    reply = await chat.send(messages, max_tokens=_JSON_MAX_TOKENS, response_format={"type": "json_schema", "json_schema": _JSON_SCHEMA})
    fallback = "Hercules will parse and repair JSON output instead"
    if not reply.ok:
        return CheckResult(name="json_schema", status="warn", detail=f"response_format json_schema rejected ({reply.describe_error()}); {fallback}"), False
    if reply.reasoned_without_answer:
        return _reasoning_check("json_schema"), False
    if not _matches_schema(_text(reply.message or {})):
        return CheckResult(name="json_schema", status="warn", detail=f"output did not follow the JSON schema; {fallback}"), False
    return CheckResult(name="json_schema", status="ok", detail="structured output follows the schema"), True


def _matches_schema(text: str) -> bool:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        return False
    return isinstance(data, dict) and set(data) == {"answer"} and isinstance(data["answer"], int) and not isinstance(data["answer"], bool)


async def probe_images(chat: ChatProbe, provider: str) -> tuple[CheckResult, ImageMode]:
    answers: dict[str, str] = {}
    for colour in _CANARY_COLOURS:
        content = [
            {"type": "text", "text": "What colour is this image? Answer with one word."},
            {"type": "image_url", "image_url": {"url": canary_data_url(colour)}},
        ]
        reply = await chat.send([{"role": "user", "content": content}], max_tokens=_IMAGE_MAX_TOKENS)
        if not reply.ok:
            return CheckResult(name="images", status="warn", detail=f"image input rejected ({reply.describe_error()}); running without screenshots", fix=_image_fix(provider)), "none"
        if reply.reasoned_without_answer:
            return _reasoning_check("images"), "none"
        answers[colour] = _text(reply.message or {}).strip().lower()
    if all(_names_only(answer, colour) for colour, answer in answers.items()):
        return CheckResult(name="images", status="ok", detail="red/blue canary answered correctly; images sent as data URLs"), "data_url"
    seen = ", ".join(f"{colour} image -> {answer[:20]!r}" for colour, answer in answers.items())
    detail = f"colour-flip canary failed ({seen}); image input is dropped or misread, running without screenshots"
    return CheckResult(name="images", status="warn", detail=detail, fix=_image_fix(provider)), "none"


def _names_only(answer: str, colour: str) -> bool:
    others = [other for other in _CANARY_COLOURS if other != colour]
    return colour in answer and not any(other in answer for other in others)


def canary_data_url(colour: str) -> str:
    """A solid-colour 64x64 PNG as a data URL (PIL is imported on first use)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", _CANARY_SIZE, _CANARY_COLOURS[colour]).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _tool_fix(provider: str) -> str:
    if provider == "llamacpp":
        return "start llama-server with `--jinja` so the model's tool-call template is used"
    if provider == "ollama":
        return "upgrade Ollama to 0.32.1 or newer and use a model that supports tools"
    if provider == "vllm":
        return "start vLLM with `--enable-auto-tool-choice --tool-call-parser gemma4`"
    return "enable tool calling for this model on the server, or choose a model that supports tools"


def _image_fix(provider: str) -> str | None:
    if provider == "llamacpp":
        return "start llama-server with `--mmproj <vision projector .gguf>`"
    if provider == "mlx":
        return "use the mlx-vlm server (provider mlx-vlm) for image input"
    if provider == "ollama":
        return "upgrade Ollama and use a vision-capable model build"
    return None
