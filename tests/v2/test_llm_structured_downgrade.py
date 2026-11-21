"""With unknown capabilities, only a refusal of response_format itself switches a session to JSON in the prompt."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from testzeus_hercules.kernel.settings import LLMSettings
from testzeus_hercules.llm.client import ChatClient, LLMRequestError
from testzeus_hercules.llm.providers import resolve_endpoint
from testzeus_hercules.llm.structured import StructuredLLM

from tests.v2.test_llm_fakes import FakeServer
from tests.v2.test_llm_structured import GOOD, MODEL, Verdict


def _replies(*first: httpx.Response) -> Any:
    queue = list(first)

    def respond(body: dict[str, Any]) -> httpx.Response:
        if queue:
            return queue.pop(0)
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(GOOD)}, "finish_reason": "stop"}]})

    return respond


def _session(server: FakeServer, calls: int) -> list[object]:
    async def run() -> list[object]:
        outcomes: list[object] = []
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(LLMSettings(), env={}), model=MODEL, timeout_s=5.0, max_retries=0, http=http)
            structured = StructuredLLM(client, capabilities=None)
            for _ in range(calls):
                try:
                    outcomes.append(await structured.complete(system="s", user="u", schema=Verdict))
                except LLMRequestError as exc:
                    outcomes.append(exc)
        return outcomes

    return asyncio.run(run())


@pytest.mark.parametrize("message", ["the prompt is too long for this model", "invalid value for temperature", "image input is not supported"])
def test_an_unrelated_400_does_not_switch_off_response_format(message: str) -> None:
    server = FakeServer(kind="generic", responder=_replies(httpx.Response(400, json={"error": {"message": message}})))

    first, second = _session(server, 2)

    assert isinstance(first, LLMRequestError)
    assert isinstance(second, Verdict)
    assert ["response_format" in body for body in server.chat_requests()] == [True, True]


def test_a_response_format_refusal_still_switches_to_prompt_json() -> None:
    server = FakeServer(kind="generic", responder=_replies(httpx.Response(422, json={"error": {"message": "Unsupported parameter: response_format.json_schema"}})))

    (result,) = _session(server, 1)

    assert isinstance(result, Verdict)
    assert ["response_format" in body for body in server.chat_requests()] == [True, False]


def _known_session(server: FakeServer, calls: int) -> list[object]:
    from testzeus_hercules.llm.capabilities import Capabilities

    async def run() -> list[object]:
        outcomes: list[object] = []
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(LLMSettings(), env={}), model=MODEL, timeout_s=5.0, max_retries=0, http=http)
            capabilities = Capabilities(server_version=None, model_id=MODEL, context_tokens=None, supports_tools=True, supports_tool_choice=True, supports_json_schema=True, image_mode="none")
            structured = StructuredLLM(client, capabilities=capabilities)
            for _ in range(calls):
                try:
                    outcomes.append(await structured.complete(system="s", user="u", schema=Verdict))
                except LLMRequestError as exc:
                    outcomes.append(exc)
        return outcomes

    return asyncio.run(run())


def test_a_schema_the_server_cannot_express_is_asked_in_the_prompt_for_that_request_only() -> None:
    refusal = httpx.Response(400, json={"error": {"message": "Object fields require at least one of: 'properties' or 'anyOf' with a list of possible properties."}})
    server = FakeServer(kind="generic", responder=_replies(refusal))

    first, second = _known_session(server, 2)

    assert isinstance(first, Verdict) and isinstance(second, Verdict)
    assert ["response_format" in body for body in server.chat_requests()] == [True, False, True], "only the refused request falls back"


def test_a_size_refusal_is_left_to_the_caller_that_splits_the_request() -> None:
    refusal = httpx.Response(400, json={"error": {"message": "Total string length of all property names, definition names and enum values cannot exceed 5000 characters. Found 8270 characters."}})
    server = FakeServer(kind="generic", responder=_replies(refusal))

    (first,) = _known_session(server, 1)

    assert isinstance(first, LLMRequestError)
