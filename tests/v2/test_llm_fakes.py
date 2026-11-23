"""In-process fake OpenAI-compatible model servers for preflight and doctor tests.

No test here: the module only provides ``FakeServer``, an ``httpx.MockTransport``
handler that emulates Ollama, llama.cpp ``llama-server`` or a generic
OpenAI-compatible server (for example an mlx-style server) closely enough for
the preflight probes. Every request is recorded so tests can assert what was,
or was not, contacted.

``scripted`` queues chat-completion replies for client tests: each chat request
(that the server does not reject for ``response_format``) pops the next entry, which
is assistant text, a full assistant message dict, an ``httpx.Response``, an
exception to raise, or a callable taking the request body.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Union

import httpx
from PIL import Image

ServerKind = Literal["ollama", "llamacpp", "generic"]
VisionMode = Literal["ok", "always_red", "reject"]
Scripted = Union[str, dict[str, Any], httpx.Response, BaseException, Callable[[dict[str, Any]], httpx.Response]]


@dataclass
class FakeServer:
    kind: ServerKind = "ollama"
    models: list[str] = field(default_factory=lambda: ["gemma4:26b-a4b-it-q4_K_M", "gemma4:e4b"])
    version: str | None = "0.32.1"
    context_length: int | None = 32768
    tools: bool = True
    tool_choice: bool = False
    json_schema: bool = True
    vision: VisionMode = "ok"
    # A reasoning model: unless a request says reasoning_effort "none", it spends max_tokens on reasoning and answers nothing.
    reasoning: bool = False
    # A caching proxy: chat answers carry the x-litellm-cache-key header (served from its cache).
    cache_hits: bool = False
    reachable: bool = True
    requests: list[httpx.Request] = field(default_factory=list)
    loaded: set[str] = field(default_factory=set)
    scripted: list[Scripted] = field(default_factory=list)
    # Answers chat requests once ``scripted`` is used up (for example a FakeModel's ``response``).
    responder: Callable[[dict[str, Any]], httpx.Response] | None = None

    def client(self, timeout: float = 5.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), timeout=timeout)

    def paths(self) -> list[str]:
        return [f"{request.method} {request.url.path}" for request in self.requests]

    def chat_requests(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests if request.url.path.endswith("/chat/completions")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.reachable:
            raise httpx.ConnectError("connection refused", request=request)
        route = (request.method, request.url.path)
        if request.method == "GET" and request.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": model, "object": "model"} for model in self.models]})
        if request.method == "POST" and request.url.path.endswith("/v1/chat/completions"):
            response = self._chat(json.loads(request.content), request)
            if self.cache_hits and response.status_code == 200:
                response.headers["x-litellm-cache-key"] = "cache-key-1"
            return response
        if self.kind == "ollama":
            return self._ollama(route, request)
        if self.kind == "llamacpp" and route == ("GET", "/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": self.context_length}, "build_info": "b6000-abc123", "total_slots": 1})
        return httpx.Response(404, json={"error": "not found"})

    def _ollama(self, route: tuple[str, str], request: httpx.Request) -> httpx.Response:
        if route == ("GET", "/api/version"):
            return httpx.Response(200, json={"version": self.version})
        if route == ("POST", "/api/show"):
            return httpx.Response(200, json={"parameters": "stop <end_of_turn>", "model_info": {"gemma4.context_length": 262144}})
        if route == ("GET", "/api/ps"):
            models = [{"name": name, "model": name, "context_length": self.context_length} for name in sorted(self.loaded)]
            return httpx.Response(200, json={"models": models})
        if route == ("POST", "/api/generate"):
            self.loaded.add(json.loads(request.content)["model"])
            return httpx.Response(200, json={"done": True, "done_reason": "load"})
        return httpx.Response(404, json={"error": "not found"})

    def _chat(self, body: dict[str, Any], request: httpx.Request) -> httpx.Response:
        self.loaded.add(body["model"])
        if self.reasoning and body.get("reasoning_effort") != "none":
            budget = int(body.get("max_tokens") or 0)
            body_json = {
                "id": "cmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "", "reasoning": "Let me think..."}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": budget, "completion_tokens_details": {"reasoning_tokens": budget}},
            }
            return httpx.Response(200, json=body_json)
        if "response_format" in body and not self.json_schema:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        if self.scripted:
            return _scripted_reply(self.scripted.pop(0), body, request)
        if self.responder is not None:
            return self.responder(body)
        if "response_format" in body:
            return _completion(content='{"answer": 5}')
        if body.get("tools"):
            return self._tool_call(body)
        colour = _image_colour(body)
        if colour is not None:
            if self.vision == "reject":
                return httpx.Response(400, json={"error": {"message": "image input is not supported"}})
            return _completion(content="red" if self.vision == "always_red" else f"{colour.title()}.")
        return _completion(content="ok")

    def _tool_call(self, body: dict[str, Any]) -> httpx.Response:
        if not self.tools:
            return _completion(content="I would call a tool here.")
        forced = body.get("tool_choice")
        name = forced["function"]["name"] if self.tool_choice and isinstance(forced, dict) else body["tools"][0]["function"]["name"]
        call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": '{"city": "Paris"}'}}
        return _completion(tool_calls=[call])


def _scripted_reply(reply: Scripted, body: dict[str, Any], request: httpx.Request) -> httpx.Response:
    if isinstance(reply, BaseException):
        if isinstance(reply, httpx.RequestError):
            reply.request = request
        raise reply
    if isinstance(reply, httpx.Response):
        return reply
    if isinstance(reply, str):
        return _completion(content=reply)
    if isinstance(reply, dict):
        return completion_response(reply)
    return reply(body)


def _completion(*, content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return completion_response(message)


def completion_response(message: dict[str, Any], *, finish_reason: str = "stop", usage: dict[str, int] | None = None) -> httpx.Response:
    """A non-streaming chat completion carrying ``message``."""
    body: dict[str, Any] = {"id": "cmpl-1", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish_reason}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _image_colour(body: dict[str, Any]) -> str | None:
    for message in body.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "image_url":
                data = part["image_url"]["url"].split(",", 1)[1]
                pixel = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB").getpixel((0, 0))
                return "red" if pixel[0] > pixel[2] else "blue"
    return None
