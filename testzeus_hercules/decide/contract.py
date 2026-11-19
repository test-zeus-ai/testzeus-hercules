"""Decision contract: typed questions, answers and decisions mirroring the System One API.

A :class:`DecisionRequest` carries one ``state`` and a flat map of named questions,
each answered independently against that state. :meth:`DecisionRequest.to_wire`
yields the JSON body a backend sends (every field except ``model``, which the
backend pins). A :class:`Decision` is the backend-neutral result.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from testzeus_hercules.decide.errors import DecisionError
from testzeus_hercules.decide.payload import ImagePart, ImageURL, iter_image_parts, normalize_json, strip_image_payloads

__all__ = [
    "Answer",
    "Box",
    "BoxAnswer",
    "BoxQuestion",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "Decision",
    "DecisionRequest",
    "Entry",
    "ImagePart",
    "ImageURL",
    "NoulAnswer",
    "NoulCriteria",
    "NoulQuestion",
    "Question",
    "ScoreAnswer",
    "ScoreQuestion",
]

# About four bytes of request JSON per token: good enough for an estimate, never for billing.
BYTES_PER_TOKEN = 4

MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
QUESTION_ID_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

# Instructions, option descriptions, score levels and noul criteria: text or nested JSON.
Entry = Union[str, dict[str, Any], list[Any]]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
OptionName = Annotated[str, StringConstraints(min_length=1)]


def _normalize_optional(value: Any, where: str) -> Any:
    return None if value is None else normalize_json(value, where=where)


# --------------------------------------------------------------------------- questions


class _QuestionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: Entry | None = None

    @field_validator("instructions", mode="before")
    @classmethod
    def _normalize_instructions(cls, value: Any) -> Any:
        return _normalize_optional(value, "instructions")

    def to_wire(self) -> dict[str, Any]:
        """Return the question's JSON shape, omitting unset optional fields."""
        return self.model_dump(mode="json", exclude_none=True)


class ChoiceQuestion(_QuestionBase):
    """Pick one of 1–255 named options; each value describes when the option applies."""

    type: Literal["choice"] = "choice"
    criteria: dict[OptionName, Entry | None] = Field(min_length=1, max_length=MAX_CHOICE_OPTIONS)

    @field_validator("criteria", mode="before")
    @classmethod
    def _normalize_options(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {name: _normalize_optional(item, f"criteria[{name!r}]") for name, item in value.items()}


class ScoreQuestion(_QuestionBase):
    """Rate against 1–10 ordered levels; a level's position is its score, from zero."""

    type: Literal["score"] = "score"
    criteria: list[Entry] = Field(min_length=1, max_length=MAX_SCORE_LEVELS)

    @field_validator("criteria", mode="before")
    @classmethod
    def _normalize_levels(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            return value
        return [normalize_json(item, where=f"criteria[{index}]") for index, item in enumerate(value)]


class NoulCriteria(BaseModel):
    """What counts as a yes (``true``) and a no (``false``) answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    true: Entry | None = None
    false: Entry | None = None

    @field_validator("true", "false", mode="before")
    @classmethod
    def _normalize_side(cls, value: Any) -> Any:
        return _normalize_optional(value, "noul criteria")


class NoulQuestion(_QuestionBase):
    """A yes/no question or statement, answered with the probability of yes."""

    type: Literal["noul"] = "noul"
    criteria: NoulCriteria | None = None


class BoxQuestion(_QuestionBase):
    """Locate what the instructions describe in the request's image."""

    type: Literal["box"] = "box"


Question = Annotated[Union[ChoiceQuestion, ScoreQuestion, NoulQuestion, BoxQuestion], Field(discriminator="type")]


# --------------------------------------------------------------------------- request


class DecisionRequest(BaseModel):
    """One state and a flat map of named questions, answered independently."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: dict[str, Any]
    questions: dict[str, Question] = Field(min_length=1)

    @field_validator("state", mode="before")
    @classmethod
    def _normalize_state(cls, value: Any) -> Any:
        return normalize_json(value, where="state")

    @field_validator("questions")
    @classmethod
    def _valid_question_ids(cls, value: dict[str, Question]) -> dict[str, Question]:
        bad = [qid for qid in value if not QUESTION_ID_PATTERN.fullmatch(qid)]
        if bad:
            raise ValueError(f"invalid question id(s) {bad!r}: use 1-64 letters, digits or '_', not starting with a digit")
        return value

    def to_wire(self) -> dict[str, Any]:
        """Return the JSON request body without ``model``."""
        state = self.model_dump(mode="json", include={"state"})["state"]
        return {"state": state, "questions": {qid: question.to_wire() for qid, question in self.questions.items()}}

    def has_images(self) -> bool:
        """True when any image part appears in state, instructions or criteria, at any depth."""
        return next(iter_image_parts(self.to_wire()), None) is not None

    def text_size_bytes(self) -> int:
        """UTF-8 size of the canonical JSON body with every image payload left out."""
        canonical = json.dumps(strip_image_payloads(self.to_wire()), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return len(canonical.encode("utf-8"))

    def text_tokens_estimate(self) -> int:
        """Rough prompt-token count of the request's text (images left out): hosted engines report no exact count."""
        return -(-self.text_size_bytes() // BYTES_PER_TOKEN)


# --------------------------------------------------------------------------- answers


class ChoiceAnswer(BaseModel):
    """The most probable option, its confidence and every option's probability."""

    model_config = ConfigDict(frozen=True)

    type: Literal["choice"] = "choice"
    choice: str
    confidence: Probability
    probabilities: dict[str, Probability]


class ScoreAnswer(BaseModel):
    """Probability-weighted score over the levels, with the level legend."""

    model_config = ConfigDict(frozen=True)

    type: Literal["score"] = "score"
    score: float = Field(ge=0.0)
    confidence: Probability | None = None
    legend: dict[str, Any] | None = None
    probabilities: dict[str, Probability]


class NoulAnswer(BaseModel):
    """Probability that the answer is yes or the statement is true."""

    model_config = ConfigDict(frozen=True)

    type: Literal["noul"] = "noul"
    noul: Probability


class Box(BaseModel):
    """Box edges as fractions of image height (top, bottom) and width (left, right)."""

    model_config = ConfigDict(frozen=True)

    top: Probability
    bottom: Probability
    left: Probability
    right: Probability

    @model_validator(mode="after")
    def _ordered_edges(self) -> Box:
        if self.top >= self.bottom:
            raise ValueError("box top must be above its bottom (top < bottom)")
        if self.left >= self.right:
            raise ValueError("box left must be left of its right (left < right)")
        return self


class BoxAnswer(BaseModel):
    """A located box; ``confidence`` is None when the backend does not provide one."""

    model_config = ConfigDict(frozen=True)

    type: Literal["box"] = "box"
    box: Box
    confidence: Probability | None = None


Answer = Annotated[Union[ChoiceAnswer, ScoreAnswer, NoulAnswer, BoxAnswer], Field(discriminator="type")]

_AnswerT = TypeVar("_AnswerT", ChoiceAnswer, ScoreAnswer, NoulAnswer, BoxAnswer)


# --------------------------------------------------------------------------- decision


class Decision(BaseModel):
    """Backend-neutral answers to one request, with provenance and cost."""

    model_config = ConfigDict(frozen=True)

    answers: dict[str, Answer]
    engine: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    cached: bool = False

    def answer(self, qid: str) -> Answer:
        """Return the answer to ``qid`` or raise :class:`DecisionError` when it is missing."""
        try:
            return self.answers[qid]
        except KeyError:
            raise DecisionError(f"decision has no answer for question {qid!r} (missing)") from None

    def choice(self, qid: str) -> ChoiceAnswer:
        return self._typed(qid, ChoiceAnswer)

    def score(self, qid: str) -> ScoreAnswer:
        return self._typed(qid, ScoreAnswer)

    def noul(self, qid: str) -> NoulAnswer:
        return self._typed(qid, NoulAnswer)

    def box(self, qid: str) -> BoxAnswer:
        return self._typed(qid, BoxAnswer)

    def _typed(self, qid: str, kind: type[_AnswerT]) -> _AnswerT:
        found = self.answer(qid)
        if not isinstance(found, kind):
            expected = kind.model_fields["type"].default
            raise DecisionError(f"answer {qid!r} is a {found.type} answer, not {expected}")
        return found

    def validate_against(self, request: DecisionRequest) -> Decision:
        """Check every question got exactly one answer of its type; return ``self``.

        Also checks a choice names one of the options and a score lies within the
        level range. Raises :class:`DecisionError` listing every problem.
        """
        problems = [problem for qid, question in request.questions.items() for problem in self._problems(qid, question)]
        problems += [f"{qid}: unexpected answer to a question that was not asked" for qid in self.answers if qid not in request.questions]
        if problems:
            raise DecisionError("decision does not match its request: " + "; ".join(problems))
        return self

    def _problems(self, qid: str, question: Question) -> list[str]:
        found = self.answers.get(qid)
        if found is None:
            return [f"{qid}: missing answer"]
        if found.type != question.type:
            return [f"{qid}: expected a {question.type} answer, got {found.type}"]
        if isinstance(found, ChoiceAnswer) and isinstance(question, ChoiceQuestion) and found.choice not in question.criteria:
            return [f"{qid}: choice {found.choice!r} is not one of the options"]
        if isinstance(found, ScoreAnswer) and isinstance(question, ScoreQuestion) and found.score > len(question.criteria) - 1:
            return [f"{qid}: score {found.score} is outside 0..{len(question.criteria) - 1}"]
        return []
