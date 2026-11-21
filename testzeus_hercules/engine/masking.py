"""What a run sends to deciders, masked: secret values and URL credentials never leave in a decision request.

The step texts a run works with already carry placeholders instead of URL credentials (see
:func:`~testzeus_hercules.engine.data.protect_case`); a page's own address, its element names and
its text may still hold one (a session id in the URL the page redirected to). :class:`MaskingDecider`
passes every text of a request (its state and its questions) through the run's redactor before the
decider sees it. Images are left as they are. It masks; it decides nothing.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from testzeus_hercules.decide.contract import Decision, DecisionRequest
from testzeus_hercules.decide.engine import DecisionEngine

Redactor = Callable[[str], str]
_DATA_URL = "data:"


def mask_tree(node: Any, redact: Redactor) -> Any:
    """A JSON-like value with every string passed through ``redact`` (data URLs, such as images, are kept); the same
    object when nothing changed."""
    if isinstance(node, str):
        return node if node.startswith(_DATA_URL) else redact(node)
    if isinstance(node, dict):
        masked = {key: mask_tree(value, redact) for key, value in node.items()}
        return node if all(masked[key] is node[key] for key in node) else masked
    if isinstance(node, list):
        items = [mask_tree(value, redact) for value in node]
        return node if all(new is old for new, old in zip(items, node)) else items
    return node


def masked_json(value: Any, redact: Redactor, **dumps: Any) -> str:
    """``value`` (a JSON-like value or a pydantic model) as JSON text with every string, keys included, passed through
    ``redact`` before it is encoded. Masking the encoded text instead misses a secret JSON escapes (a quote, a backslash,
    a newline) and can break numbers (a secret ``123`` inside ``1230``)."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    plain = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return json.dumps(_mask_keys(mask_tree(plain, redact), redact), ensure_ascii=False, **dumps)


def _mask_keys(node: Any, redact: Redactor) -> Any:
    if isinstance(node, dict):
        return {redact(key): _mask_keys(value, redact) for key, value in node.items()}
    if isinstance(node, list):
        return [_mask_keys(value, redact) for value in node]
    return node


class MaskingDecider:
    """Wraps a decider so its requests leave masked; every other attribute (engine, usage, route_of, ...) is the wrapped decider's."""

    def __init__(self, inner: DecisionEngine, redact: Redactor) -> None:
        self._inner = inner
        self._redact = redact

    @property
    def inner(self) -> DecisionEngine:
        return self._inner

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        return await self._inner.decide(self.mask(request), purpose=purpose)

    def mask(self, request: DecisionRequest) -> DecisionRequest:
        """``request`` with its texts masked (the same request when there was nothing to mask)."""
        state = mask_tree(request.state, self._redact)
        questions = {}
        for qid, question in request.questions.items():
            updates: dict[str, Any] = {}
            instructions = getattr(question, "instructions", None)
            if isinstance(instructions, str) and self._redact(instructions) != instructions:
                updates["instructions"] = self._redact(instructions)
            criteria = getattr(question, "criteria", None)
            if isinstance(criteria, dict):
                masked = mask_tree(criteria, self._redact)
                if masked is not criteria:
                    updates["criteria"] = masked
            questions[qid] = question.model_copy(update=updates) if updates else question
        if state is request.state and all(questions[qid] is request.questions[qid] for qid in questions):
            return request
        return request.model_copy(update={"state": state, "questions": questions})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
