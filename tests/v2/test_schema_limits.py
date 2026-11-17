from __future__ import annotations

import asyncio
from typing import Any

from testzeus_hercules.engine.intent import CaseItem, LLMInterpreter, case_reading_schema
from testzeus_hercules.llm.client import LLMRequestError
from testzeus_hercules.llm.schema_limits import SchemaLimits, SchemaSize, known_schema_limits, limits_from_refusal, schema_size
from testzeus_hercules.llm.structured import json_schema_for

CEREBRAS_PROPERTIES = (
    "Too many properties in JSON schema. Maximum 500 object properties supported. Please ensure that your schema (1) is not recursive and (2) contains no more than 500 object properties."
)
CEREBRAS_CHARACTERS = "Total string length of all property names, definition names and enum values cannot exceed 5000 characters. Found 8270 characters."


def test_the_size_is_counted_as_the_server_counts_it() -> None:
    schema = {
        "type": "object",
        "properties": {"kind": {"enum": ["click", "fill"]}, "target": {"type": "string"}, "nested": {"type": "object", "properties": {"x": {"const": "abc"}}}},
        "$defs": {"Thing": {"type": "object"}},
    }
    assert schema_size(schema) == SchemaSize(properties=4, characters=len("kind") + len("target") + len("nested") + len("clickfill") + len("x") + len("abc") + len("Thing"))


def test_limits_come_from_known_servers_and_from_refusals() -> None:
    assert known_schema_limits("https://api.cerebras.ai/v1") == SchemaLimits(500, 5000)
    assert known_schema_limits("http://127.0.0.1:11434/v1") is None
    assert limits_from_refusal(CEREBRAS_PROPERTIES) == SchemaLimits(properties=500)
    assert limits_from_refusal(CEREBRAS_CHARACTERS) == SchemaLimits(characters=5000)
    assert limits_from_refusal("model not found") is None
    assert SchemaLimits(properties=500).tightened(SchemaLimits(characters=5000)) == SchemaLimits(500, 5000)
    assert not SchemaLimits(500, 5000).fits(SchemaSize(468, 8270)) and SchemaLimits(500, 5000).fits(SchemaSize(400, 4000))


def _items(steps: int) -> list[CaseItem]:
    return [CaseItem(step=number, role="action", text=f"I click button number {number}") for number in range(1, steps + 1)]


class _LimitedServer:
    """Answers like a server with schema limits: refuses a schema over them, else replies with one click per item."""

    def __init__(self, limits: SchemaLimits) -> None:
        self.limits = limits
        self.sizes: list[int] = []

    async def complete(self, *, system: str, user: str, schema: Any, images: Any = ()) -> Any:
        size = schema_size(json_schema_for(schema))
        keys = [name for name in schema.model_fields if name.endswith("_action")]
        self.sizes.append(len(keys))
        if not self.limits.fits(size):
            raise LLMRequestError(400, CEREBRAS_CHARACTERS.replace("8270", str(size.characters)))
        reply: dict[str, Any] = {}
        for name, field in schema.model_fields.items():
            if name.endswith("_action"):
                reply[name] = [{"kind": "click", "target": "Go", "role": None}]
            elif name.endswith("_complete"):
                reply[name] = True
            elif name.endswith(("_needs", "_criteria", "_lint")):
                reply[name] = []
            elif name.endswith("_risk"):
                reply[name] = {"flags": [], "reason": None}
        return schema.model_validate(reply)


def test_a_known_limit_sizes_the_parts_before_any_request() -> None:
    limits = SchemaLimits(500, 5000)
    server = _LimitedServer(limits)
    reader = LLMInterpreter(server, case_llm=server, part_items=16, schema_limits=limits)

    reading = asyncio.run(reader.read_case(_items(16)))

    assert len(reading.intents) == 16 and reading.source == "model"
    assert all(limits.fits(schema_size(json_schema_for(case_reading_schema([f"step_{n}_action" for n in range(1, size + 1)])))) for size in server.sizes)
    assert sum(server.sizes) == 16, "no request is refused"


def test_limits_a_refusal_states_are_learned_for_the_rest_of_the_case() -> None:
    server = _LimitedServer(SchemaLimits(500, 5000))
    reader = LLMInterpreter(server, case_llm=server, part_items=16)

    reading = asyncio.run(reader.read_case(_items(16)))

    assert len(reading.intents) == 16 and reading.source == "model"
    assert server.sizes[0] == 16 and sum(server.sizes[1:]) == 16, "one refusal, then parts that fit"
