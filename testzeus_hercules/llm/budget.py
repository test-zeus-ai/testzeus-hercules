"""Token estimates and context-budget fitting for chat messages.

Local servers rarely report a context overflow: they truncate or shift the
window and the model silently loses its instructions. So prompts are counted
before every call and trimmed client-side.

Counting uses tiktoken's ``o200k_base`` encoding when tiktoken is installed *and*
its encoding file is already cached on disk; it is never downloaded, so counting
works offline and makes no network calls. Otherwise the estimate is one token per
four characters. Either way it approximates the served model's own tokenizer,
which is why :func:`fit_messages` applies a safety margin.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Union

logger = logging.getLogger(__name__)

TokenCounter = Callable[[str], int]
Message = Mapping[str, Any]
Countable = Union[str, Message, Sequence[Message]]

MESSAGE_OVERHEAD_TOKENS = 4
DEFAULT_VISION_TOKENS = 280
DEFAULT_MARGIN = 1.15
DEFAULT_KEEP_RECENT = 3

_CONDENSED_MAX_CHARS = 120
_CONDENSED_MARK = " […condensed]"
_IMAGE_OMITTED = "[image omitted]"
_O200K_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"


class ContextBudgetExceeded(ValueError):
    """Even the system prompt and the latest user message do not fit the context budget."""

    def __init__(self, needed: int, available: int) -> None:
        super().__init__(f"prompt needs about {needed} tokens but only {available} fit in the context budget")
        self.needed = needed
        self.available = available


# --------------------------------------------------------------------------- counting


def chars_estimate(text: str) -> int:
    """One token per four characters, rounded up."""
    return math.ceil(len(text) / 4)


@functools.lru_cache(maxsize=1)
def default_counter() -> TokenCounter:
    """tiktoken ``o200k_base`` when available offline, else :func:`chars_estimate`."""
    encoding = _cached_encoding()
    if encoding is None:
        return chars_estimate
    return lambda text: len(encoding.encode(text, disallowed_special=()))


def _cached_encoding() -> Any:
    """The o200k encoding if tiktoken is importable and its file is cached locally; else None."""
    try:
        import tiktoken
    except ImportError:
        return None
    cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get("DATA_GYM_CACHE_DIR", os.path.join(tempfile.gettempdir(), "data-gym-cache")))
    if not cache_dir or not os.path.exists(os.path.join(cache_dir, hashlib.sha1(_O200K_URL.encode()).hexdigest())):
        return None
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception as exc:  # a corrupt cache would trigger a download attempt; fall back instead
        logger.debug("tiktoken encoding unavailable: %s", exc.__class__.__name__)
        return None


def estimate_tokens(value: Countable, *, counter: TokenCounter | None = None, vision_tokens: int = DEFAULT_VISION_TOKENS) -> int:
    """Tokens in a string, one chat message or a list of messages.

    Each message adds a small fixed overhead; each image part counts as
    ``vision_tokens`` (pass the hardware tier's ``vision_tokens``).
    """
    count = counter or default_counter()
    if isinstance(value, str):
        return count(value)
    if isinstance(value, Mapping):
        return _message_tokens(value, count, vision_tokens)
    return sum(_message_tokens(message, count, vision_tokens) for message in value)


def _message_tokens(message: Message, count: TokenCounter, vision_tokens: int) -> int:
    tokens = MESSAGE_OVERHEAD_TOKENS + _content_tokens(message.get("content"), count, vision_tokens)
    if message.get("tool_calls"):
        tokens += count(json.dumps(message["tool_calls"], ensure_ascii=False))
    return tokens


def _content_tokens(content: Any, count: TokenCounter, vision_tokens: int) -> int:
    if isinstance(content, str):
        return count(content)
    if not isinstance(content, list):
        return 0
    total = 0
    for part in content:
        if isinstance(part, Mapping) and part.get("type") == "image_url":
            total += vision_tokens
        elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
            total += count(part["text"])
    return total


# --------------------------------------------------------------------------- fitting


def fit_messages(
    messages: Sequence[Message],
    budget: int,
    reserve: int,
    *,
    counter: TokenCounter | None = None,
    vision_tokens: int = DEFAULT_VISION_TOKENS,
    margin: float = DEFAULT_MARGIN,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` that fits ``budget - reserve`` tokens.

    System messages and the last user message are always kept. Other turns are
    grouped (an assistant tool call stays with its tool results) and, oldest first,
    condensed (tool results cut to one line, images replaced by a note; the newest
    ``keep_recent`` groups stay verbatim) and then dropped until the rest fits.
    Estimates are multiplied by ``margin``. Raises :class:`ContextBudgetExceeded`
    when the kept messages alone do not fit. The input is never modified.
    """
    available = budget - reserve
    count = counter or default_counter()

    def needed(items: Sequence[Message]) -> int:
        return math.ceil(estimate_tokens(items, counter=count, vision_tokens=vision_tokens) * margin)

    pinned, units = _split(messages)
    if needed([messages[index] for index in pinned]) > available:
        raise ContextBudgetExceeded(needed([messages[index] for index in pinned]), available)
    kept: dict[int, list[dict[str, Any]]] = {start: [dict(message) for message in unit] for start, unit in units}

    def assemble() -> list[dict[str, Any]]:
        return [message for index in sorted([*pinned, *kept]) for message in ([dict(messages[index])] if index in pinned else kept[index])]

    starts = [start for start, _ in units]
    for start in starts[: max(len(starts) - keep_recent, 0)]:
        if needed(assemble()) <= available:
            return assemble()
        kept[start] = [_condense(message) for message in kept[start]]
    for start in starts:
        if needed(assemble()) <= available:
            break
        del kept[start]
    return assemble()


def _split(messages: Sequence[Message]) -> tuple[set[int], list[tuple[int, list[Message]]]]:
    """Indexes that are always kept, and the other messages grouped as (first index, group)."""
    last_user = max((index for index, message in enumerate(messages) if message.get("role") == "user"), default=None)
    pinned = {index for index, message in enumerate(messages) if message.get("role") == "system" or index == last_user}
    units: list[tuple[int, list[Message]]] = []
    for index, message in enumerate(messages):
        if index in pinned:
            continue
        joins_call = message.get("role") == "tool" and units and units[-1][1][0].get("tool_calls") and units[-1][0] + len(units[-1][1]) == index
        if joins_call:
            units[-1][1].append(message)
        else:
            units.append((index, [message]))
    return pinned, units


def _condense(message: Mapping[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if message.get("role") == "tool" and isinstance(content, str):
        return {**message, "content": _one_line(content)}
    if isinstance(content, list):
        parts = [{"type": "text", "text": _IMAGE_OMITTED} if isinstance(part, Mapping) and part.get("type") == "image_url" else part for part in content]
        return {**message, "content": parts}
    return dict(message)


def _one_line(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first) > _CONDENSED_MAX_CHARS:
        first = first[: _CONDENSED_MAX_CHARS - 1] + "…"
    return first + _CONDENSED_MARK if first != text.strip() else first
