"""Pydantic-typed output from a chat model: schema-constrained where possible, repaired once otherwise.

Where the server supports it, the reply is constrained with
``response_format: {"type": "json_schema", ...}``. Otherwise (for example
``mlx_lm.server``) the schema goes into the system prompt and the first JSON object
is pulled out of the reply. Either way the object is validated with pydantic; a
reply that fails gets exactly one repair turn quoting the validation errors, then
:class:`StructuredOutputError`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from testzeus_hercules.llm.budget import DEFAULT_VISION_TOKENS, fit_messages
from testzeus_hercules.llm.capabilities import Capabilities
from testzeus_hercules.llm.client import DEFAULT_MAX_TOKENS, ChatClient, ChatResult, LLMError, LLMRequestError, Message, user_message
from testzeus_hercules.llm.schema_limits import limits_from_refusal

__all__ = ["DEFAULT_SEED", "StructuredLLM", "StructuredOutputError", "extract_json_object", "json_schema_for"]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_TEMPERATURE = 0.2
DEFAULT_SEED = 1234
_MAX_REF_DEPTH = 32
_MAX_ECHO_CHARS = 2000
_MAX_ERRORS = 8
_SCHEMA_REJECTED_STATUSES = frozenset({400, 422})
# Words a server's error uses when it is ``response_format`` / JSON-schema output it does not support (not, say, an
# image or a prompt it refuses): only such an error switches the session to JSON in the prompt.
_SCHEMA_ERROR_WORDS = ("response_format", "response format", "json_schema", "json schema", "schema", "structured output", "grammar", "guided", "anyof", "additionalproperties", "object fields")
_SCHEMA_SIZE_WORDS = ("exceed", "limit", "too many", "too large", "too long", "maximum")
_SCHEMA_NAME = re.compile(r"[^A-Za-z0-9_-]")
_CODE_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)

_JSON_ONLY = "Reply with one JSON object only: no prose, no code fences."
_SCHEMA_IN_PROMPT = "Reply with one JSON object that matches this JSON Schema, and nothing else (no prose, no code fences):\n"
_REPAIR = "Your reply was not a valid JSON object for the required schema. Problems: {problems}. Reply again with only the corrected JSON object."


class StructuredOutputError(LLMError):
    """The model did not produce valid JSON for the schema, even after a repair turn."""


def extract_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in ``text`` (code fences tolerated), or None."""
    candidates = [match.group(1) for match in _CODE_FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for start in (index for index, char in enumerate(candidate) if char == "{"):
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON Schema with ``$ref``s inlined and titles dropped.

    Small models and some servers' grammar converters cope badly with references.
    Recursive models are not supported.
    """
    raw = schema.model_json_schema()
    inlined: dict[str, Any] = _inline(raw, raw.get("$defs", {}), depth=0)
    return inlined


def _inline(node: Any, defs: dict[str, Any], *, depth: int, properties: bool = False) -> Any:
    if depth > _MAX_REF_DEPTH:
        raise ValueError("schema nests too deeply (recursive models are not supported)")
    if isinstance(node, list):
        return [_inline(item, defs, depth=depth) for item in node]
    if not isinstance(node, dict):
        return node
    if properties:
        return {name: _inline(value, defs, depth=depth) for name, value in node.items()}
    if "$ref" in node:
        target = defs[node["$ref"].rsplit("/", 1)[-1]]
        return _inline({**target, **{key: value for key, value in node.items() if key != "$ref"}}, defs, depth=depth + 1)
    return {key: _inline(value, defs, depth=depth, properties=key == "properties") for key, value in node.items() if key not in ("title", "$defs")}


class StructuredLLM:
    """Ask a :class:`ChatClient` for output that validates as a pydantic model.

    ``capabilities`` (from preflight) picks the mode; ``None`` means unknown: the
    schema-constrained mode is tried first, and if the server rejects
    ``response_format`` the prompt mode is used from then on. When the context size
    is known, a prompt that cannot fit with ``max_tokens`` of output is refused
    before sending (:class:`~testzeus_hercules.llm.budget.ContextBudgetExceeded`).
    """

    def __init__(
        self,
        client: ChatClient,
        *,
        capabilities: Capabilities | None,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = DEFAULT_SEED,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        vision_tokens: int = DEFAULT_VISION_TOKENS,
    ) -> None:
        self.client = client
        self.capabilities = capabilities
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens
        self._vision_tokens = vision_tokens
        self._schema_mode: bool | None = None if capabilities is None else capabilities.supports_json_schema

    @property
    def accepts_images(self) -> bool:
        return self.capabilities is None or self.capabilities.image_mode != "none"

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T:
        if images and not self.accepts_images:
            raise ValueError("this model server cannot take image input (preflight found no working image route)")
        json_schema = json_schema_for(schema)
        schema_mode = self._schema_mode is not False
        messages = self._messages(system, user, images, json_schema, schema_mode=schema_mode)
        try:
            result = await self._send(messages, schema, json_schema, schema_mode=schema_mode)
        except LLMRequestError as exc:
            if not schema_mode or not _schema_unsupported(exc) or limits_from_refusal(exc.server_message) is not None:
                raise
            if self._schema_mode is not None and _schema_too_large(exc):
                raise
            if self._schema_mode is None:
                logger.info("server rejected response_format json_schema; using JSON in the prompt instead")
                self._schema_mode = False
            else:
                logger.info("server refused the %s reply schema; asking with the schema in the prompt for this request", schema.__name__)
            schema_mode = False
            messages = self._messages(system, user, images, json_schema, schema_mode=False)
            result = await self._send(messages, schema, json_schema, schema_mode=False)
        else:
            if self._schema_mode is None:
                self._schema_mode = schema_mode
        parsed, problems = _validate(result, schema)
        if parsed is not None:
            return parsed
        repair = [*messages, {"role": "assistant", "content": result.content[:_MAX_ECHO_CHARS]}, {"role": "user", "content": _REPAIR.format(problems=problems)}]
        result = await self._send(repair, schema, json_schema, schema_mode=schema_mode)
        parsed, problems = _validate(result, schema)
        if parsed is None:
            raise StructuredOutputError(f"model output did not match {schema.__name__} after one repair turn: {problems}")
        return parsed

    def _messages(self, system: str, user: str, images: Sequence[bytes], json_schema: dict[str, Any], *, schema_mode: bool) -> list[Message]:
        suffix = _JSON_ONLY if schema_mode else _SCHEMA_IN_PROMPT + json.dumps(json_schema, separators=(",", ":"), ensure_ascii=False)
        messages = [{"role": "system", "content": f"{system.rstrip()}\n\n{suffix}"}, user_message(user, images)]
        if self.capabilities is not None and self.capabilities.context_tokens:
            return fit_messages(messages, self.capabilities.context_tokens, self._max_tokens, vision_tokens=self._vision_tokens)
        return messages

    async def _send(self, messages: list[Message], schema: type[BaseModel], json_schema: dict[str, Any], *, schema_mode: bool) -> ChatResult:
        response_format = {"type": "json_schema", "json_schema": {"name": _schema_name(schema), "schema": json_schema, "strict": True}} if schema_mode else None
        return await self.client.chat(messages, response_format=response_format, temperature=self._temperature, max_tokens=self._max_tokens, seed=self._seed)


def _schema_unsupported(exc: LLMRequestError) -> bool:
    """True when the server's refusal is about ``response_format`` / JSON-schema output."""
    if exc.status_code not in _SCHEMA_REJECTED_STATUSES:
        return False
    message = (exc.server_message or "").lower()
    return any(word in message for word in _SCHEMA_ERROR_WORDS)


def _schema_too_large(exc: LLMRequestError) -> bool:
    message = (exc.server_message or "").lower()
    return any(word in message for word in _SCHEMA_SIZE_WORDS)


def _schema_name(schema: type[BaseModel]) -> str:
    return _SCHEMA_NAME.sub("_", schema.__name__)[:64] or "response"


def _validate(result: ChatResult, schema: type[T]) -> tuple[T | None, str]:
    data = extract_json_object(result.content)
    if data is None:
        cut = " (the reply was cut off at max_tokens)" if result.finish_reason == "length" else ""
        return None, f"no JSON object found{cut}"
    try:
        return schema.model_validate(data), ""
    except ValidationError as exc:
        return None, _describe(exc)


def _describe(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_input=False, include_context=False)
    described = [f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}" for error in errors[:_MAX_ERRORS]]
    more = f"; and {len(errors) - _MAX_ERRORS} more" if len(errors) > _MAX_ERRORS else ""
    return "; ".join(described) + more
