"""Tests for token estimation and context-budget fitting."""

from typing import Any

import pytest
from testzeus_hercules.llm import budget
from testzeus_hercules.llm.budget import MESSAGE_OVERHEAD_TOKENS, ContextBudgetExceeded, chars_estimate, estimate_tokens, fit_messages


def word_counter(text: str) -> int:
    return len(text.split())


IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


# --------------------------------------------------------------------------- estimate_tokens


def test_chars_estimate_is_a_quarter_of_the_length_rounded_up() -> None:
    assert chars_estimate("") == 0
    assert chars_estimate("abcd") == 1
    assert chars_estimate("abcde") == 2


def test_estimate_text_uses_the_counter() -> None:
    assert estimate_tokens("one two three", counter=word_counter) == 3


def test_estimate_messages_adds_per_message_overhead() -> None:
    messages = [{"role": "system", "content": "a b"}, {"role": "user", "content": "c d e"}]
    assert estimate_tokens(messages, counter=word_counter) == 5 + 2 * MESSAGE_OVERHEAD_TOKENS


def test_images_count_as_vision_tokens() -> None:
    message = {"role": "user", "content": [IMAGE, IMAGE, {"type": "text", "text": "what is this"}]}
    assert estimate_tokens(message, counter=word_counter, vision_tokens=280) == 2 * 280 + 3 + MESSAGE_OVERHEAD_TOKENS


def test_tool_calls_are_counted() -> None:
    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "click", "arguments": '{"mark": "12"}'}}]}
    assert estimate_tokens(message, counter=word_counter) > MESSAGE_OVERHEAD_TOKENS


def test_default_counter_never_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a locally cached tokenizer file the estimate falls back to chars/4 (no network)."""
    monkeypatch.setattr(budget, "_cached_encoding", lambda: None)
    budget.default_counter.cache_clear()
    try:
        assert estimate_tokens("abcdefgh") == 2
    finally:
        budget.default_counter.cache_clear()


# --------------------------------------------------------------------------- fit_messages


def _msg(role: str, n: int, **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": " ".join([role[0]] * n), **extra}


def _fit(messages: list[dict[str, Any]], budget_tokens: int, reserve: int = 0, **kwargs: Any) -> list[dict[str, Any]]:
    return fit_messages(messages, budget_tokens, reserve, counter=word_counter, margin=1.0, **kwargs)


def test_returns_messages_unchanged_when_they_fit() -> None:
    messages = [_msg("system", 5), _msg("user", 5)]
    fitted = _fit(messages, 100)
    assert fitted == messages
    assert fitted is not messages


def test_drops_oldest_non_system_turns_first() -> None:
    system, old_user, old_reply, new_user = _msg("system", 10), _msg("user", 30), _msg("assistant", 30), _msg("user", 10)
    fitted = _fit([system, old_user, old_reply, new_user], budget_tokens=60 + 4 * MESSAGE_OVERHEAD_TOKENS)
    assert fitted[0] == system
    assert fitted[-1] == new_user
    assert old_user not in fitted
    assert old_reply in fitted


def test_reserve_is_subtracted_from_the_budget() -> None:
    messages = [_msg("system", 10), _msg("user", 30), _msg("user", 10)]
    total = estimate_tokens(messages, counter=word_counter)
    assert len(_fit(messages, total)) == 3
    assert len(_fit(messages, total, reserve=5)) == 2


def test_condenses_old_tool_results_before_dropping() -> None:
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}
    result = {"role": "tool", "tool_call_id": "c1", "content": "line one of a long result\n" + "x " * 200}
    recent = [_msg("assistant", 2), _msg("user", 2), _msg("assistant", 2)]
    messages = [_msg("system", 5), _msg("user", 5), call, result, *recent, _msg("user", 5)]
    fitted = _fit(messages, budget_tokens=120, keep_recent=3)
    condensed = next(message for message in fitted if message.get("role") == "tool")
    assert condensed["tool_call_id"] == "c1"
    assert condensed["content"].startswith("line one of a long result")
    assert len(condensed["content"].split()) < 30
    assert call in fitted
    assert result["content"].startswith("line one")  # the input is not mutated


def test_drops_a_tool_call_together_with_its_results() -> None:
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}
    result = {"role": "tool", "tool_call_id": "c1", "content": "r " * 50}
    messages = [_msg("system", 5), call, result, _msg("assistant", 5), _msg("user", 5)]
    fitted = _fit(messages, budget_tokens=40, keep_recent=0)
    assert all(message.get("role") != "tool" for message in fitted)
    assert all(not message.get("tool_calls") for message in fitted)


def test_old_images_are_replaced_by_a_note_when_condensing() -> None:
    old = {"role": "user", "content": [IMAGE, {"type": "text", "text": "old screenshot"}]}
    messages = [_msg("system", 5), old, _msg("assistant", 5), _msg("user", 5)]
    fitted = _fit(messages, budget_tokens=60, keep_recent=1, vision_tokens=280)
    condensed = fitted[1]
    assert isinstance(condensed["content"], list)
    assert all(part["type"] == "text" for part in condensed["content"])


def test_raises_when_system_and_last_user_alone_do_not_fit() -> None:
    with pytest.raises(ContextBudgetExceeded) as caught:
        _fit([_msg("system", 50), _msg("assistant", 5), _msg("user", 50)], budget_tokens=60)
    assert caught.value.needed > caught.value.available


def test_margin_inflates_estimates() -> None:
    messages = [_msg("system", 40), _msg("user", 40)]
    total = estimate_tokens(messages, counter=word_counter)
    fit_messages(messages, total, 0, counter=word_counter, margin=1.0)
    with pytest.raises(ContextBudgetExceeded):
        fit_messages(messages, total, 0, counter=word_counter, margin=1.15)
