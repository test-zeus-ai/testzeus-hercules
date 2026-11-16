"""Tests for the OpenAI-compatible chat client, against in-process fake servers."""

import asyncio
import base64
from typing import Any

import httpx
import pytest
from testzeus_hercules.kernel.settings import LLMSettings
from testzeus_hercules.llm.capabilities import Capabilities
from testzeus_hercules.llm.client import ChatClient, ChatResult, LLMRequestError, LLMUnavailable, image_part, strip_reasoning, user_message
from testzeus_hercules.llm.providers import EgressBlocked, Endpoint, resolve_endpoint

from tests.v2.test_llm_fakes import FakeServer, completion_response

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
MODEL = "gemma4:e4b"
TOOLS = [{"type": "function", "function": {"name": "click", "parameters": {"type": "object", "properties": {"mark": {"type": "string"}}}}}]


def _endpoint(provider: str = "ollama", base_url: str | None = None, env: dict[str, str] | None = None) -> Endpoint:
    return resolve_endpoint(LLMSettings(provider=provider, base_url=base_url), env=env or {})


def _caps(**overrides: Any) -> Capabilities:
    values: dict[str, Any] = {
        "server_version": "0.32.1",
        "model_id": MODEL,
        "context_tokens": 32768,
        "supports_tools": True,
        "supports_tool_choice": False,
        "supports_json_schema": True,
        "image_mode": "data_url",
    }
    return Capabilities(**{**values, **overrides})


def _chat(server: FakeServer, messages: list[dict[str, Any]], *, capabilities: Capabilities | None = None, max_retries: int = 1, **kwargs: Any) -> ChatResult:
    async def run() -> ChatResult:
        async with server.client() as http:
            client = ChatClient(_endpoint(), model=MODEL, capabilities=capabilities, timeout_s=5.0, max_retries=max_retries, http=http, backoff_s=0.0)
            return await client.chat(messages, **kwargs)

    return asyncio.run(run())


HELLO = [{"role": "user", "content": "hello"}]


# --------------------------------------------------------------------------- request shape


def test_posts_openai_chat_completion_with_model_and_sampling() -> None:
    server = FakeServer(scripted=["hi there"])
    result = _chat(server, HELLO, temperature=0.2, max_tokens=64, seed=7)
    body = server.chat_requests()[0]
    assert server.paths() == ["POST /v1/chat/completions"]
    assert body["model"] == MODEL
    assert body["messages"] == HELLO
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert body["seed"] == 7
    assert body["stream"] is False
    assert result.content == "hi there"
    assert result.finish_reason == "stop"
    assert result.latency_ms >= 0


def test_omits_seed_and_tools_when_not_given() -> None:
    server = FakeServer(scripted=["ok"])
    _chat(server, HELLO)
    body = server.chat_requests()[0]
    assert "seed" not in body and "tools" not in body and "tool_choice" not in body and "response_format" not in body


def test_extra_body_is_merged_into_the_request() -> None:
    server = FakeServer(scripted=["ok"])
    _chat(server, HELLO, extra_body={"top_k": 64})
    assert server.chat_requests()[0]["top_k"] == 64


def test_reads_usage() -> None:
    server = FakeServer(scripted=[completion_response({"content": "ok"}, usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15})])
    result = _chat(server, HELLO)
    assert (result.usage.prompt, result.usage.completion) == (12, 3)


def test_missing_usage_reads_as_zero() -> None:
    result = _chat(FakeServer(scripted=["ok"]), HELLO)
    assert (result.usage.prompt, result.usage.completion) == (0, 0)


# --------------------------------------------------------------------------- tool_choice and tool calls


def test_drops_tool_choice_when_capabilities_say_unsupported() -> None:
    server = FakeServer(scripted=["ok"])
    _chat(server, HELLO, capabilities=_caps(supports_tool_choice=False), tools=TOOLS, tool_choice="required")
    body = server.chat_requests()[0]
    assert body["tools"] == TOOLS
    assert "tool_choice" not in body


def test_keeps_tool_choice_when_supported_or_unknown() -> None:
    supported = FakeServer(scripted=["ok"])
    _chat(supported, HELLO, capabilities=_caps(supports_tool_choice=True), tools=TOOLS, tool_choice="required")
    unknown = FakeServer(scripted=["ok"])
    _chat(unknown, HELLO, tools=TOOLS, tool_choice="required")
    assert supported.chat_requests()[0]["tool_choice"] == "required"
    assert unknown.chat_requests()[0]["tool_choice"] == "required"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"mark": "12"}', {"mark": "12"}),
        ({"mark": "12"}, {"mark": "12"}),
        ('```json\n{"mark": "12"}\n```', {"mark": "12"}),
        ("", {}),
        (None, {}),
    ],
)
def test_parses_tool_call_arguments_robustly(arguments: Any, expected: dict[str, Any]) -> None:
    call = {"id": "call_9", "type": "function", "function": {"name": "click", "arguments": arguments}}
    result = _chat(FakeServer(scripted=[{"content": None, "tool_calls": [call]}]), HELLO, tools=TOOLS)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_9"
    assert result.tool_calls[0].name == "click"
    assert result.tool_calls[0].arguments == expected
    assert result.content == ""


def test_unparseable_tool_arguments_keep_the_raw_text() -> None:
    call = {"id": "call_1", "function": {"name": "click", "arguments": "mark=12"}}
    result = _chat(FakeServer(scripted=[{"content": None, "tool_calls": [call]}]), HELLO, tools=TOOLS)
    assert result.tool_calls[0].arguments == {}
    assert result.tool_calls[0].raw_arguments == "mark=12"


def test_tool_call_without_id_gets_one() -> None:
    call = {"function": {"name": "click", "arguments": "{}"}}
    result = _chat(FakeServer(scripted=[{"content": None, "tool_calls": [call]}]), HELLO, tools=TOOLS)
    assert result.tool_calls[0].id


# --------------------------------------------------------------------------- reasoning


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>let me see</think>The answer", "The answer"),
        ('<think>\n</think>\n\n{"a": 1}', '{"a": 1}'),
        ("<|channel>thought\nhmm<channel|>Final", "Final"),
        ("<|channel>thought<channel|>Final", "Final"),
        ("stray reasoning</think>Final", "Final"),
        ("No reasoning here", "No reasoning here"),
    ],
)
def test_strip_reasoning(raw: str, clean: str) -> None:
    assert strip_reasoning(raw) == clean


def test_content_has_reasoning_stripped() -> None:
    result = _chat(FakeServer(scripted=["<|channel>thought<channel|>Done."]), HELLO)
    assert result.content == "Done."


# --------------------------------------------------------------------------- images


def test_image_part_is_a_data_url() -> None:
    part = image_part(PNG)
    assert part["type"] == "image_url"
    assert part["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    assert image_part(JPEG)["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_image_part_rejects_unknown_bytes() -> None:
    with pytest.raises(ValueError):
        image_part(b"GIF89a....")


def test_user_message_puts_images_before_text() -> None:
    message = user_message("What is shown?", images=[PNG, JPEG])
    assert message["role"] == "user"
    kinds = [part["type"] for part in message["content"]]
    assert kinds == ["image_url", "image_url", "text"]
    assert message["content"][-1]["text"] == "What is shown?"


def test_user_message_without_images_is_plain_text() -> None:
    assert user_message("hi") == {"role": "user", "content": "hi"}


def test_images_reach_the_server_before_text() -> None:
    server = FakeServer(scripted=["ok"])
    _chat(server, [user_message("Describe", images=[PNG])])
    content = server.chat_requests()[0]["messages"][0]["content"]
    assert [part["type"] for part in content] == ["image_url", "text"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")


# --------------------------------------------------------------------------- errors and retries


def test_timeout_raises_unavailable_without_retry() -> None:
    server = FakeServer(scripted=[httpx.ReadTimeout("slow"), "never"])
    with pytest.raises(LLMUnavailable, match="timed out"):
        _chat(server, HELLO, max_retries=3)
    assert len(server.chat_requests()) == 1


def test_retries_connect_errors_then_succeeds() -> None:
    server = FakeServer(scripted=[httpx.ConnectError("refused"), "ok"])
    assert _chat(server, HELLO, max_retries=1).content == "ok"
    assert len(server.chat_requests()) == 2


def test_connect_errors_exhaust_retries_as_unavailable() -> None:
    server = FakeServer(reachable=False)
    with pytest.raises(LLMUnavailable):
        _chat(server, HELLO, max_retries=2)
    assert len(server.requests) == 3


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_rate_limits_and_server_errors(status: int) -> None:
    server = FakeServer(scripted=[httpx.Response(status, json={"error": "busy"}), "ok"])
    assert _chat(server, HELLO, max_retries=1).content == "ok"


def test_server_errors_beyond_retries_raise_unavailable() -> None:
    server = FakeServer(scripted=[httpx.Response(503, json={"error": "loading"})] * 3)
    with pytest.raises(LLMUnavailable, match="503"):
        _chat(server, HELLO, max_retries=1)
    assert len(server.chat_requests()) == 2


def test_client_errors_raise_request_error_with_server_message_without_retry() -> None:
    secret_prompt = "my secret prompt text"
    server = FakeServer(scripted=[httpx.Response(400, json={"error": {"message": "context length exceeded"}}), "never"])
    with pytest.raises(LLMRequestError) as caught:
        _chat(server, [{"role": "user", "content": secret_prompt}], max_retries=3)
    assert caught.value.status_code == 400
    assert "context length exceeded" in str(caught.value)
    assert secret_prompt not in str(caught.value)
    assert len(server.chat_requests()) == 1


def test_malformed_completion_raises_unavailable() -> None:
    server = FakeServer(scripted=[httpx.Response(200, text="not json")])
    with pytest.raises(LLMUnavailable, match="malformed"):
        _chat(server, HELLO)


# --------------------------------------------------------------------------- egress


def test_refuses_remote_endpoint_under_local_policy() -> None:
    remote = _endpoint("openai-compatible", base_url="https://models.example.com/v1")
    with pytest.raises(EgressBlocked):
        ChatClient(remote, model=MODEL, timeout_s=5.0, max_retries=0)


def test_allows_remote_endpoint_when_policy_permits() -> None:
    remote = _endpoint("openai-compatible", base_url="https://models.example.com/v1")
    client = ChatClient(remote, model=MODEL, timeout_s=5.0, max_retries=0, egress_policy="hybrid-text")
    asyncio.run(client.aclose())


def test_sends_bearer_key_for_keyed_endpoints() -> None:
    server = FakeServer(scripted=["ok"])
    endpoint = _endpoint("openai-compatible", base_url="http://127.0.0.1:9000/v1")

    async def run() -> None:
        async with server.client() as http:
            await ChatClient(endpoint, model=MODEL, timeout_s=5.0, max_retries=0, http=http).chat(HELLO)

    asyncio.run(run())
    assert server.requests[0].headers["authorization"].startswith("Bearer ")
    assert str(server.requests[0].url) == "http://127.0.0.1:9000/v1/chat/completions"


def test_request_params_go_with_every_request_and_never_override_the_clients_own_fields() -> None:
    server = FakeServer(scripted=["ok", "ok"])

    async def run() -> None:
        async with server.client() as http:
            client = ChatClient(_endpoint(), model=MODEL, timeout_s=5.0, http=http, request_params={"reasoning_effort": "none", "temperature": 1.5, "top_k": 20})
            await client.chat(HELLO, temperature=0.0, max_tokens=8)
            await client.chat(HELLO, extra_body={"top_k": 40})

    asyncio.run(run())
    first, second = server.chat_requests()
    assert first["reasoning_effort"] == second["reasoning_effort"] == "none"
    assert first["temperature"] == 0.0 and first["model"] == MODEL, "the client's own fields win"
    assert (first["top_k"], second["top_k"]) == (20, 40), "per-call extra fields win over the configured ones"
