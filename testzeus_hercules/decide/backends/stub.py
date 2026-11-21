"""Deterministic, offline decision backend for tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Union

from pydantic import TypeAdapter
from testzeus_hercules.decide.contract import (
    Answer,
    Box,
    BoxAnswer,
    BoxQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    Decision,
    DecisionRequest,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)
from testzeus_hercules.decide.errors import DecisionError

AnswerFn = Callable[[DecisionRequest, str], Union[Answer, Mapping[str, Any]]]
AnswerSpec = Union[Answer, Mapping[str, Any], AnswerFn]

_ANSWER: TypeAdapter[Answer] = TypeAdapter(Answer)


@dataclass(frozen=True)
class StubCall:
    """One recorded :meth:`StubDecider.decide` call."""

    request: DecisionRequest
    purpose: str


class StubDecider:
    """Answers from fixtures, falling back to a fixed default policy.

    ``answers`` maps a question id to an answer, its wire-shaped dict, or a callable
    ``(request, qid) -> answer``. Questions without a fixture get the default: the
    first choice option with confidence 1.0, noul 0.0, score 0, and a full-frame box
    with no confidence. Every decision is checked against its request, so a fixture
    of the wrong type fails loudly. Pass ``error`` to simulate a failing backend.
    """

    engine: ClassVar[str] = "stub"

    def __init__(self, answers: Mapping[str, AnswerSpec] | None = None, *, model_version: str = "stub", error: DecisionError | None = None) -> None:
        self._answers = {qid: _coerce_fixture(spec) for qid, spec in (answers or {}).items()}
        self._model_version = model_version
        self._error = error
        self._calls: list[StubCall] = []

    @property
    def calls(self) -> tuple[StubCall, ...]:
        """Every call so far, oldest first."""
        return tuple(self._calls)

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        self._calls.append(StubCall(request=request, purpose=purpose))
        if self._error is not None:
            raise self._error
        answers = {qid: self._answer(request, qid, question) for qid, question in request.questions.items()}
        decision = Decision(answers=answers, engine=self.engine, model_version=self._model_version, latency_ms=0, cost_usd=0.0)
        return decision.validate_against(request)

    def _answer(self, request: DecisionRequest, qid: str, question: Question) -> Answer:
        spec = self._answers.get(qid)
        if spec is None:
            return default_answer(question)
        if callable(spec):
            return _ANSWER.validate_python(spec(request, qid))
        return spec


def _coerce_fixture(spec: AnswerSpec) -> Answer | AnswerFn:
    if isinstance(spec, (ChoiceAnswer, ScoreAnswer, NoulAnswer, BoxAnswer)) or callable(spec):
        return spec
    return _ANSWER.validate_python(spec)


def default_answer(question: Question) -> Answer:
    """The stub's deterministic answer for a question without a fixture."""
    if isinstance(question, ChoiceQuestion):
        first = next(iter(question.criteria))
        return ChoiceAnswer(choice=first, confidence=1.0, probabilities={name: float(name == first) for name in question.criteria})
    if isinstance(question, ScoreQuestion):
        levels = {str(index): level for index, level in enumerate(question.criteria)}
        return ScoreAnswer(score=0.0, confidence=1.0, legend=levels, probabilities={key: float(key == "0") for key in levels})
    if isinstance(question, NoulQuestion):
        return NoulAnswer(noul=0.0)
    if isinstance(question, BoxQuestion):
        return BoxAnswer(box=Box(top=0.0, bottom=1.0, left=0.0, right=1.0), confidence=None)
    raise DecisionError(f"unsupported question type {type(question).__name__}")
