"""Async client for OpenAI-compatible ``/chat/completions`` endpoints (local servers first).

Plain httpx, no framework, so importing it stays cheap. The client adapts one
request to what the server can take (it drops ``tool_choice`` where preflight
found it unsupported), cleans up what comes back (reasoning channels stripped,
tool-call arguments parsed), and maps failures onto two errors:

* :class:`LLMUnavailable` - the server could not answer (unreachable, timed out,
  overloaded, malformed reply). Connect errors, 429 and 5xx are retried first.
* :class:`LLMRequestError` - the server refused the request (4xx); retrying the
  same request will not help. The message carries the server's error text, never
  the prompt.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from testzeus_hercules.kernel.settings import EgressPolicy
from testzeus_hercules.llm.capabilities import Capabilities
from testzeus_hercules.llm.providers import Endpoint, check_egress
from testzeus_hercules.llm.server_info import auth_headers

__all__ = [
    "ChatClient",
    "ChatResult",
    "LLMError",
    "LLMRequestError",
    "LLMUnavailable",
    "Message",
    "ToolCall",
    "Usage",
    "image_part",
    "strip_reasoning",
    "user_message",
]

Message = dict[str, Any]
Sleep = Callable[[float], Awaitable[None]]

DEFAULT_MAX_TOKENS = 1024
_MAX_ERROR_CHARS = 300
_MAX_RETRY_AFTER_S = 30.0
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

_REASONING_BLOCKS = (
    re.compile(r"<think>.*?</think>", re.DOTALL),
    re.compile(r"<\|channel>thought.*?<channel\|>", re.DOTALL),
)
_DANGLING_THINK_CLOSE = re.compile(r"^.*?</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"^```[A-Za-z0-9_-]*\s*(.*?)\s*```$", re.DOTALL)
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


class LLMError(Exception):
    """Base class for chat-client failures."""


class LLMUnavailable(LLMError):
    """The server could not answer: unreachable, timed out, overloaded or replied with garbage."""


class LLMRequestError(LLMError):
    """The server refused the request (HTTP 4xx other than 429)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"model server rejected the request (HTTP {status_code}): {message}")
        self.status_code = status_code
        self.server_message = message


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None


@dataclass(frozen=True)
class Usage:
    prompt: int = 0
    completion: int = 0


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    # A caching proxy answered from its cache (a LiteLLM proxy marks it with the x-litellm-cache-key header).
    cached: bool = False


# Response header a LiteLLM proxy sets on an answer it served from its cache.
CACHE_HIT_HEADER = "x-litellm-cache-key"
# Body field that asks a LiteLLM proxy for a fresh answer (llm.cache: bypass).
NO_CACHE_BODY = {"cache": {"no-cache": True}}


# --------------------------------------------------------------------------- message helpers


def image_part(data: bytes) -> Message:
    """An ``image_url`` content part holding ``data`` (PNG or JPEG) as a base64 data URL.

    Data URLs are the one form every local server accepts (Ollama takes nothing else).
    """
    mime = next((kind for signature, kind in _IMAGE_SIGNATURES if data.startswith(signature)), None)
    if mime is None:
        raise ValueError("image bytes must be PNG or JPEG")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}}


def user_message(text: str, images: Sequence[bytes] = ()) -> Message:
    """A user turn; images go before the text, as Gemma-family models expect."""
    if not images:
        return {"role": "user", "content": text}
    return {"role": "user", "content": [*(image_part(image) for image in images), {"type": "text", "text": text}]}


def strip_reasoning(text: str) -> str:
    """Remove thinking blocks (``<think>``, Gemma's thought channel), including empty ones."""
    for pattern in _REASONING_BLOCKS:
        text = pattern.sub("", text)
    if "</think>" in text:
        text = _DANGLING_THINK_CLOSE.sub("", text, count=1)
    return text.strip()


# --------------------------------------------------------------------------- client


class ChatClient:
    """One model on one OpenAI-compatible endpoint.

    ``capabilities`` (from preflight) lets the client adapt requests; ``None`` means
    unknown, and the request is sent as given. ``request_params`` (``llm.request_params``)
    are added to every request; the client's own fields take precedence over them. The endpoint must be local unless
    ``egress_policy`` allows hosted models. Pass ``http`` to share a connection pool
    (the caller then owns it); otherwise the client creates and closes its own.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        model: str,
        capabilities: Capabilities | None = None,
        timeout_s: float = 300.0,
        max_retries: int = 1,
        http: httpx.AsyncClient | None = None,
        egress_policy: EgressPolicy = "local",
        backoff_s: float = 0.5,
        sleep: Sleep = asyncio.sleep,
        request_params: Mapping[str, Any] | None = None,
    ) -> None:
        check_egress(endpoint, egress_policy)
        if timeout_s <= 0 or max_retries < 0 or backoff_s < 0:
            raise ValueError("timeout_s must be positive; max_retries and backoff_s must not be negative")
        self.endpoint = endpoint
        self.model = model
        self.capabilities = capabilities
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._owns_http = http is None
        # A local server is reached directly: an environment proxy never sees prompts, page text or screenshots meant for it.
        self._http = http if http is not None else httpx.AsyncClient(trust_env=not endpoint.is_local)
        self._url = endpoint.base_url.rstrip("/") + "/chat/completions"
        self._request_params = dict(request_params or {})

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> ChatClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        seed: int | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        body = self._body(messages, tools, tool_choice, response_format, temperature, max_tokens, seed, extra_body)
        started = time.perf_counter()
        payload, cached = await self._post(body)
        result = _parse_completion(payload, latency_ms=int((time.perf_counter() - started) * 1000))
        return dataclasses.replace(result, cached=cached) if cached else result

    def _body(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] | None,
        tool_choice: str | Mapping[str, Any] | None,
        response_format: Mapping[str, Any] | None,
        temperature: float,
        max_tokens: int,
        seed: int | None,
        extra_body: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {**self._request_params, **(extra_body or {}), "model": self.model, "messages": list(messages), "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if seed is not None:
            body["seed"] = seed
        if tools:
            body["tools"] = list(tools)
            if tool_choice is not None and self._tool_choice_supported():
                body["tool_choice"] = tool_choice
        if response_format is not None:
            body["response_format"] = dict(response_format)
        if self.endpoint.no_cache:
            body.update(NO_CACHE_BODY)
        return body

    def _tool_choice_supported(self) -> bool:
        return self.capabilities is None or self.capabilities.supports_tool_choice

    async def _post(self, body: dict[str, Any]) -> tuple[Any, bool]:
        """The completion's JSON and whether a caching proxy answered from its cache."""
        attempt = 0
        while True:
            try:
                response = await self._http.post(self._url, json=body, headers=auth_headers(self.endpoint), timeout=self._timeout_s)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(f"model server timed out after {self._timeout_s:g}s ({exc.__class__.__name__})") from None
            except httpx.ConnectError as exc:
                if attempt < self._max_retries:
                    attempt += 1
                    await self._sleep(self._backoff(attempt))
                    continue
                raise LLMUnavailable(f"cannot reach the model server ({exc.__class__.__name__})") from None
            except httpx.HTTPError as exc:
                raise LLMUnavailable(f"model server connection failed ({exc.__class__.__name__})") from None
            if response.status_code in _RETRY_STATUSES:
                if attempt < self._max_retries:
                    attempt += 1
                    await self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                    continue
                raise LLMUnavailable(f"model server is busy or failing (HTTP {response.status_code}): {_server_message(response)}")
            if response.status_code >= 400:
                raise LLMRequestError(response.status_code, _server_message(response))
            try:
                return response.json(), CACHE_HIT_HEADER in response.headers
            except ValueError:
                raise LLMUnavailable("model server sent a malformed completion (not JSON)") from None

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), _MAX_RETRY_AFTER_S)
            except ValueError:
                pass
        return self._backoff_s * 2.0 ** (attempt - 1)


# --------------------------------------------------------------------------- response parsing


def _parse_completion(payload: Any, *, latency_ms: int) -> ChatResult:
    try:
        choice = payload["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMUnavailable("model server sent a malformed completion (no message)") from None
    if not isinstance(message, dict):
        raise LLMUnavailable("model server sent a malformed completion (no message)")
    content = message.get("content")
    text = content if isinstance(content, str) else _join_text_parts(content)
    finish = choice.get("finish_reason") if isinstance(choice, dict) else None
    return ChatResult(
        content=strip_reasoning(text),
        tool_calls=tuple(_tool_calls(message.get("tool_calls"))),
        finish_reason=finish if isinstance(finish, str) else None,
        usage=_usage(payload.get("usage") if isinstance(payload, dict) else None),
        latency_ms=latency_ms,
    )


def _join_text_parts(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")


def _tool_calls(raw: Any) -> list[ToolCall]:
    calls = []
    for item in raw if isinstance(raw, list) else ():
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            continue
        arguments, raw_arguments = _arguments(function.get("arguments"))
        call_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else f"call_{uuid.uuid4().hex[:12]}"
        calls.append(ToolCall(id=call_id, name=function["name"], arguments=arguments, raw_arguments=raw_arguments))
    return calls


def _arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Tool-call arguments as a dict; unparseable text yields ``{}`` and is kept as ``raw_arguments``."""
    if isinstance(raw, dict):
        return dict(raw), None
    if not isinstance(raw, str) or not raw.strip():
        return {}, None
    text = raw.strip()
    fenced = _CODE_FENCE.match(text)
    try:
        parsed = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        return {}, raw
    return (parsed, raw) if isinstance(parsed, dict) else ({}, raw)


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    return Usage(prompt=prompt if isinstance(prompt, int) else 0, completion=completion if isinstance(completion, int) else 0)


def _server_message(response: httpx.Response) -> str:
    """The server's own error text, trimmed; never the request."""
    try:
        data = response.json()
    except ValueError:
        return _trim(response.text) or "no error message"
    error = data.get("error", data.get("detail", data.get("message"))) if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message", error.get("detail"))
    return _trim(error if isinstance(error, str) else json.dumps(error)) or "no error message"


def _trim(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_ERROR_CHARS else text[: _MAX_ERROR_CHARS - 1] + "…"
