"""Tests for schema-constrained output with the JSON-in-prompt fallback and one repair turn."""

import asyncio
import json
from typing import Any, Sequence

import httpx
import pytest
from pydantic import BaseModel, Field
from testzeus_hercules.kernel.settings import LLMSettings
from testzeus_hercules.llm.budget import ContextBudgetExceeded
from testzeus_hercules.llm.capabilities import Capabilities
from testzeus_hercules.llm.client import ChatClient, LLMUnavailable
from testzeus_hercules.llm.providers import resolve_endpoint
from testzeus_hercules.llm.structured import StructuredLLM, StructuredOutputError, extract_json_object

from tests.v2.test_llm_fakes import FakeServer

MODEL = "gemma4:e4b"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class Inner(BaseModel):
    label: str


class Verdict(BaseModel):
    outcome: str
    confidence: float = Field(ge=0.0, le=1.0)
    detail: Inner


GOOD = {"outcome": "advance", "confidence": 0.9, "detail": {"label": "ok"}}


def _caps(**overrides: Any) -> Capabilities:
    values: dict[str, Any] = {
        "server_version": "1",
        "model_id": MODEL,
        "context_tokens": 32768,
        "supports_tools": True,
        "supports_tool_choice": False,
        "supports_json_schema": True,
        "image_mode": "data_url",
    }
    return Capabilities(**{**values, **overrides})


def _complete(server: FakeServer, *, capabilities: Capabilities | None, images: Sequence[bytes] = (), **kwargs: Any) -> Verdict:
    async def run() -> Verdict:
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(LLMSettings(), env={}), model=MODEL, capabilities=capabilities, timeout_s=5.0, max_retries=0, http=http)
            structured = StructuredLLM(client, capabilities=capabilities, **kwargs)
            return await structured.complete(system="You judge test steps.", user="Did the step pass?", schema=Verdict, images=images)

    return asyncio.run(run())


# --------------------------------------------------------------------------- extraction


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure! Here it is: {"a": 1} Hope that helps {"b": 2}',
        'noise { not json } then {"a": 1}',
    ],
)
def test_extract_json_object_finds_the_first_object(text: str) -> None:
    assert extract_json_object(text) == {"a": 1}


def test_extract_json_object_returns_none_without_an_object() -> None:
    assert extract_json_object("no json here [1, 2]") is None


# --------------------------------------------------------------------------- json_schema path (Ollama-like)


def test_uses_response_format_json_schema_when_supported() -> None:
    server = FakeServer(kind="ollama", json_schema=True, scripted=[json.dumps(GOOD)])
    result = _complete(server, capabilities=_caps(supports_json_schema=True))
    assert result == Verdict.model_validate(GOOD)
    body = server.chat_requests()[0]
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Verdict"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert "$defs" not in schema and "$ref" not in json.dumps(schema)
    assert schema["properties"]["detail"]["properties"]["label"]["type"] == "string"
    assert body["temperature"] == 0.2
    assert isinstance(body["seed"], int)
    assert "tool_choice" not in body
    assert len(server.chat_requests()) == 1


def test_system_prompt_comes_first_and_is_stable() -> None:
    first = FakeServer(scripted=[json.dumps(GOOD)])
    second = FakeServer(scripted=[json.dumps(GOOD)])
    _complete(first, capabilities=_caps())
    _complete(second, capabilities=_caps())
    messages = first.chat_requests()[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0] == second.chat_requests()[0]["messages"][0]


# --------------------------------------------------------------------------- prompt-JSON path (mlx-like)


def test_prompt_json_path_when_schema_unsupported() -> None:
    server = FakeServer(kind="generic", json_schema=False, scripted=["```json\n" + json.dumps(GOOD) + "\n```"])
    result = _complete(server, capabilities=_caps(supports_json_schema=False))
    assert result.outcome == "advance"
    body = server.chat_requests()[0]
    assert "response_format" not in body
    assert '"outcome"' in body["messages"][0]["content"]
    assert "JSON" in body["messages"][0]["content"]


def test_unknown_capabilities_fall_back_to_prompt_json_when_server_rejects_response_format() -> None:
    server = FakeServer(kind="generic", json_schema=False, scripted=[json.dumps(GOOD), json.dumps(GOOD)])

    async def run() -> list[Verdict]:
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(LLMSettings(), env={}), model=MODEL, timeout_s=5.0, max_retries=0, http=http)
            structured = StructuredLLM(client, capabilities=None)
            return [await structured.complete(system="s", user="u", schema=Verdict) for _ in range(2)]

    results = asyncio.run(run())
    assert all(result.outcome == "advance" for result in results)
    bodies = server.chat_requests()
    assert ["response_format" in body for body in bodies] == [True, False, False]


# --------------------------------------------------------------------------- repair


def test_malformed_then_repaired_json() -> None:
    server = FakeServer(scripted=['{"outcome": "advance", "confidence": 3}', json.dumps(GOOD)])
    result = _complete(server, capabilities=_caps())
    assert result.confidence == 0.9
    bodies = server.chat_requests()
    assert len(bodies) == 2
    repair = bodies[1]["messages"]
    assert repair[-2]["role"] == "assistant"
    assert repair[-1]["role"] == "user"
    assert "confidence" in repair[-1]["content"]
    assert "detail" in repair[-1]["content"]


def test_persistent_garbage_raises_structured_output_error() -> None:
    server = FakeServer(scripted=["I think it passed.", "Still not JSON", "never sent"])
    with pytest.raises(StructuredOutputError):
        _complete(server, capabilities=_caps())
    assert len(server.chat_requests()) == 2


def test_unavailable_server_is_not_retried_as_repair() -> None:
    server = FakeServer(scripted=[httpx.ReadTimeout("slow")])
    with pytest.raises(LLMUnavailable):
        _complete(server, capabilities=_caps())


# --------------------------------------------------------------------------- images and budget


def test_images_go_before_the_user_text() -> None:
    server = FakeServer(scripted=[json.dumps(GOOD)])
    _complete(server, capabilities=_caps(), images=[PNG])
    user = server.chat_requests()[0]["messages"][1]
    assert [part["type"] for part in user["content"]] == ["image_url", "text"]


def test_images_rejected_when_server_cannot_see() -> None:
    server = FakeServer(scripted=[json.dumps(GOOD)])
    with pytest.raises(ValueError, match="image"):
        _complete(server, capabilities=_caps(image_mode="none"), images=[PNG])
    assert server.chat_requests() == []


def test_prompt_larger_than_the_context_is_refused_before_sending() -> None:
    server = FakeServer(scripted=[json.dumps(GOOD)])
    with pytest.raises(ContextBudgetExceeded):
        _complete(server, capabilities=_caps(context_tokens=520), max_tokens=512)
    assert server.chat_requests() == []
