"""Limits some servers put on the size of a structured-output schema, and a schema's size as they count it.

Cerebras, for example, refuses a schema with more than 500 object properties, or whose property names, definition
names and enum values add up to more than 5000 characters. A long case's reading schema passes both, so it is read
in parts that fit (see :class:`testzeus_hercules.engine.intent.LLMInterpreter`). Limits come from this table for
known servers, and from the server's own refusal message for any other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

_PROPERTIES = re.compile(r"maximum\s+(\d+)\s+object\s+properties", re.IGNORECASE)
_CHARACTERS = re.compile(r"cannot\s+exceed\s+(\d+)\s+characters", re.IGNORECASE)


@dataclass(frozen=True)
class SchemaSize:
    properties: int
    characters: int


@dataclass(frozen=True)
class SchemaLimits:
    properties: int | None = None
    characters: int | None = None

    def fits(self, size: SchemaSize) -> bool:
        return (self.properties is None or size.properties <= self.properties) and (self.characters is None or size.characters <= self.characters)

    def tightened(self, other: SchemaLimits | None) -> SchemaLimits:
        if other is None:
            return self

        def smaller(first: int | None, second: int | None) -> int | None:
            return second if first is None else first if second is None else min(first, second)

        return SchemaLimits(smaller(self.properties, other.properties), smaller(self.characters, other.characters))

    def describe(self) -> str:
        parts = [f"{self.properties} object properties" if self.properties else "", f"{self.characters} characters of names and enum values" if self.characters else ""]
        return " and ".join(part for part in parts if part)


KNOWN_LIMITS: dict[str, SchemaLimits] = {"api.cerebras.ai": SchemaLimits(properties=500, characters=5000)}


def known_schema_limits(base_url: str | None) -> SchemaLimits | None:
    """The schema limits of a known hosted server, by its address."""
    host = (urlsplit(base_url or "").hostname or "").lower()
    return KNOWN_LIMITS.get(host)


def limits_from_refusal(message: str) -> SchemaLimits | None:
    """The limits a server's refusal message states (``Maximum 500 object properties``, ``cannot exceed 5000 characters``)."""
    properties = _PROPERTIES.search(message or "")
    characters = _CHARACTERS.search(message or "")
    if properties is None and characters is None:
        return None
    return SchemaLimits(int(properties.group(1)) if properties else None, int(characters.group(1)) if characters else None)


def schema_size(schema: Any) -> SchemaSize:
    """Object properties, and characters of property names, definition names and enum (or const) values, in ``schema``."""
    properties = 0
    characters = 0

    def walk(node: Any) -> None:
        nonlocal properties, characters
        if isinstance(node, dict):
            names = node.get("properties")
            if isinstance(names, dict):
                properties += len(names)
                characters += sum(len(name) for name in names)
            for key in ("$defs", "definitions"):
                if isinstance(node.get(key), dict):
                    characters += sum(len(name) for name in node[key])
            if isinstance(node.get("enum"), list):
                characters += sum(len(str(value)) for value in node["enum"])
            if isinstance(node.get("const"), str):
                characters += len(node["const"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return SchemaSize(properties, characters)
