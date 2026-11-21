"""Offline tests for the decision contract (System One request/answer shapes)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from testzeus_hercules.decide.contract import (
    Answer,
    BoxAnswer,
    BoxQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    Decision,
    DecisionRequest,
    ImagePart,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from testzeus_hercules.decide.engine import DecisionError

# 16x16 PNG used by the public System One box example.
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAHElEQVR4nGP4TyJgoKcGBgbsaFQDfTUQB4aDBgDAen2fpitsVAAAAABJRU5ErkJggg=="
PNG_URL = f"data:image/png;base64,{PNG_B64}"

# Request body from the public docs (state object from the "State" concept page,
# questions from the System One API examples), without the `model` field.
DOC_REQUEST: dict[str, Any] = {
    "state": {
        "ticket": {
            "subject": "Duplicate charge",
            "messages": [
                {"from": "customer", "text": "I was charged twice for order A-104. Please refund the duplicate."},
                {"from": "support", "text": "We are checking the charges."},
            ],
        },
        "order": {"id": "A-104", "charges": [{"amount_usd": 49, "status": "captured"}, {"amount_usd": 49, "status": "captured"}]},
        "refund_policy": "Duplicate charges are eligible for a refund.",
    },
    "questions": {
        "billing": {"type": "noul", "instructions": "Is this message about billing?"},
        "tone": {
            "type": "choice",
            "instructions": "What is the tone of this message?",
            "criteria": {"angry": "An upset or hostile message", "calm": "A neutral or polite message", "excited": "An enthusiastic or eager message"},
        },
        "urgency": {"type": "score", "instructions": "How urgent is this message?", "criteria": ["Can wait", "Needs attention this week", "Needs attention today"]},
        "spam": {"type": "noul", "instructions": "Is this message spam?", "criteria": {"true": "Unsolicited advertising", "false": "A legitimate conversation"}},
        "color": {"type": "choice", "instructions": "What color is the item?", "criteria": {"red": None, "green": None, "blue": None}},
    },
}

# Answers from the public System One response examples.
DOC_ANSWERS: dict[str, Any] = {
    "billing": {"type": "noul", "noul": 0.98},
    "tone": {"type": "choice", "choice": "calm", "confidence": 0.7, "probabilities": {"angry": 0.15, "calm": 0.8, "excited": 0.05}},
    "urgency": {
        "type": "score",
        "score": 1.7,
        "confidence": 0.55,
        "legend": {"0": "Can wait", "1": "Needs attention this week", "2": "Needs attention today"},
        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
    },
    "red_rectangle": {"type": "box", "box": {"top": 0.2, "bottom": 0.66, "left": 0.19, "right": 0.68}, "confidence": None},
}

ANSWER_ADAPTER: TypeAdapter[Answer] = TypeAdapter(Answer)


def _image_part() -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": PNG_URL}}


def _nest(depth: int) -> dict[str, Any]:
    """Return a dict whose nesting depth is exactly ``depth`` (a flat dict is depth 1)."""
    value: dict[str, Any] = {"leaf": "x"}
    for _ in range(depth - 1):
        value = {"n": value}
    return value


def _noul_request(state: dict[str, Any] | None = None) -> DecisionRequest:
    return DecisionRequest(state=state or {"page": "x"}, questions={"q": NoulQuestion(instructions="Is it?")})


# --------------------------------------------------------------------------- wire shape


def test_doc_request_round_trips_to_identical_wire_body() -> None:
    request = DecisionRequest.model_validate(copy.deepcopy(DOC_REQUEST))
    assert request.to_wire() == DOC_REQUEST
    assert DecisionRequest.model_validate(request.to_wire()) == request


def test_wire_body_has_no_model_and_omits_unset_fields() -> None:
    request = DecisionRequest(state={"a": 1}, questions={"loc": BoxQuestion()})
    assert request.to_wire() == {"state": {"a": 1}, "questions": {"loc": {"type": "box"}}}


def test_wire_body_is_json_serialisable() -> None:
    request = DecisionRequest.model_validate(copy.deepcopy(DOC_REQUEST))
    assert json.loads(json.dumps(request.to_wire())) == DOC_REQUEST


def test_questions_parse_to_typed_models() -> None:
    request = DecisionRequest.model_validate(copy.deepcopy(DOC_REQUEST))
    assert isinstance(request.questions["billing"], NoulQuestion)
    assert isinstance(request.questions["tone"], ChoiceQuestion)
    assert isinstance(request.questions["urgency"], ScoreQuestion)


def test_spec_appendix_fan_out_request_validates() -> None:
    body = {
        "state": {
            "step": "Click Continue to payment",
            "rows": [{"md": "12", "role": "button", "name": "Continue to payment"}, {"md": "13", "role": "link", "name": "Back to cart"}],
        },
        "questions": {
            "tool": {"type": "choice", "instructions": "Which tool performs `step`?", "criteria": {"click": "Activate a control", "hover": "Reveal a menu"}},
            "target": {"type": "choice", "instructions": "Which row?", "criteria": {"12": 'button "Continue to payment"', "13": 'link "Back to cart"', "no_match": "None"}},
            "target_absent": {"type": "noul", "instructions": "Missing?", "criteria": {"true": "Not listed", "false": "Listed"}},
        },
    }
    assert DecisionRequest.model_validate(body).to_wire() == body


@pytest.mark.parametrize("raw", list(DOC_ANSWERS.values()), ids=list(DOC_ANSWERS))
def test_doc_answers_round_trip(raw: dict[str, Any]) -> None:
    answer = ANSWER_ADAPTER.validate_python(raw)
    assert answer.model_dump(mode="json") == raw


# --------------------------------------------------------------------------- request validation


@pytest.mark.parametrize("qid", ["1abc", "has-dash", "has space", "", "a" * 65, "é"])
def test_rejects_bad_question_ids(qid: str) -> None:
    with pytest.raises(ValidationError, match="question id"):
        DecisionRequest(state={}, questions={qid: NoulQuestion()})


@pytest.mark.parametrize("qid", ["a", "_x", "Target_absent2", "a" * 64])
def test_accepts_good_question_ids(qid: str) -> None:
    assert qid in DecisionRequest(state={}, questions={qid: NoulQuestion()}).questions


def test_requires_at_least_one_question() -> None:
    with pytest.raises(ValidationError):
        DecisionRequest(state={}, questions={})


def test_rejects_unknown_request_fields() -> None:
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate({"state": {}, "questions": {"q": {"type": "noul"}}, "model": "x"})


def test_rejects_unknown_question_type() -> None:
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate({"state": {}, "questions": {"q": {"type": "rank"}}})


def test_choice_accepts_255_options_and_rejects_256() -> None:
    ChoiceQuestion(criteria={f"o{i}": None for i in range(255)})
    with pytest.raises(ValidationError, match="255"):
        ChoiceQuestion(criteria={f"o{i}": None for i in range(256)})


def test_choice_rejects_empty_criteria_and_empty_keys() -> None:
    with pytest.raises(ValidationError):
        ChoiceQuestion(criteria={})
    with pytest.raises(ValidationError):
        ChoiceQuestion(criteria={"": "blank"})


def test_choice_option_values_accept_structures_and_images() -> None:
    q = ChoiceQuestion(criteria={"a": "text", "b": {"does": "x", "not": ["y"]}, "c": ["see", _image_part()], "d": None})
    assert q.criteria["c"][1]["type"] == "image_url"


def test_score_levels_between_one_and_ten() -> None:
    ScoreQuestion(criteria=["only"])
    ScoreQuestion(criteria=[str(i) for i in range(10)])
    with pytest.raises(ValidationError):
        ScoreQuestion(criteria=[])
    with pytest.raises(ValidationError):
        ScoreQuestion(criteria=[str(i) for i in range(11)])


def test_noul_criteria_only_true_and_false() -> None:
    q = NoulQuestion(criteria={"true": "yes"})
    assert q.to_wire() == {"type": "noul", "criteria": {"true": "yes"}}
    with pytest.raises(ValidationError):
        NoulQuestion(criteria={"true": "yes", "maybe": "?"})


def test_box_question_has_no_criteria() -> None:
    with pytest.raises(ValidationError):
        BoxQuestion.model_validate({"type": "box", "criteria": {"a": "b"}})


def test_instructions_accept_str_dict_and_list() -> None:
    for instructions in ("plain", {"question": "q", "compare": ["a", "b"]}, ["Compare", _image_part(), "with the badge"]):
        assert NoulQuestion(instructions=instructions).instructions == instructions


def test_state_depth_64_ok_and_65_rejected() -> None:
    _noul_request(_nest(64))
    with pytest.raises(ValidationError, match="64"):
        _noul_request(_nest(65))


def test_depth_limit_applies_to_instructions_and_criteria() -> None:
    NoulQuestion(instructions=_nest(64))
    with pytest.raises(ValidationError, match="64"):
        NoulQuestion(instructions=_nest(65))
    with pytest.raises(ValidationError, match="64"):
        ChoiceQuestion(criteria={"a": _nest(65)})
    with pytest.raises(ValidationError, match="64"):
        ScoreQuestion(criteria=[_nest(65)])
    with pytest.raises(ValidationError, match="64"):
        NoulQuestion(criteria={"false": _nest(65)})


def test_state_rejects_non_json_values() -> None:
    with pytest.raises(ValidationError):
        _noul_request({"when": object()})


# --------------------------------------------------------------------------- images


@pytest.mark.parametrize("mime", ["png", "jpeg"])
def test_image_part_accepts_png_and_jpeg(mime: str) -> None:
    part = ImagePart.model_validate({"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{PNG_B64}"}})
    assert part.to_wire() == {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{PNG_B64}"}}


@pytest.mark.parametrize(
    "url",
    [
        f"data:image/webp;base64,{PNG_B64}",
        f"data:image/gif;base64,{PNG_B64}",
        "https://example.test/a.png",
        PNG_B64,
        "data:image/png;base64,",
        "data:image/png;base64,not base64!",
    ],
)
def test_image_part_rejects_other_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        ImagePart.model_validate({"type": "image_url", "image_url": {"url": url}})


def test_image_part_from_bytes() -> None:
    part = ImagePart.from_bytes(b"\x89PNG\r\n", "image/png")
    assert part.image_url.url.startswith("data:image/png;base64,")


def test_invalid_image_part_nested_in_state_is_rejected() -> None:
    bad = {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}
    with pytest.raises(ValidationError):
        _noul_request({"page": {"frames": [bad]}})


def test_image_part_models_in_state_are_normalised_to_wire_dicts() -> None:
    request = _noul_request({"shot": ImagePart.from_bytes(b"abc", "image/jpeg")})
    assert request.to_wire()["state"]["shot"]["type"] == "image_url"
    assert request.has_images()


def test_has_images_false_without_images() -> None:
    assert not DecisionRequest.model_validate(copy.deepcopy(DOC_REQUEST)).has_images()


def test_has_images_finds_parts_at_depth() -> None:
    state = _nest(40)
    state["deep"] = {"a": [{"b": [[_image_part()]]}]}
    assert _noul_request(state).has_images()


def test_has_images_finds_parts_in_instructions_and_criteria() -> None:
    assert DecisionRequest(state={}, questions={"q": NoulQuestion(instructions=["Compare", _image_part()])}).has_images()
    assert DecisionRequest(state={}, questions={"q": ChoiceQuestion(criteria={"a": {"example": _image_part()}})}).has_images()
    assert DecisionRequest(state={}, questions={"q": ScoreQuestion(criteria=["low", _image_part()])}).has_images()
    assert DecisionRequest(state={}, questions={"q": NoulQuestion(criteria={"true": _image_part()})}).has_images()


def test_box_question_alone_is_not_an_image() -> None:
    assert DecisionRequest(state={}, questions={"q": BoxQuestion()}).has_images() is False
    assert DecisionRequest(state={"shot": _image_part()}, questions={"q": BoxQuestion()}).has_images()


def test_text_size_excludes_image_payloads() -> None:
    small = _noul_request({"shot": _image_part()})
    big_url = "data:image/png;base64," + "A" * 400_000
    big = _noul_request({"shot": {"type": "image_url", "image_url": {"url": big_url}}})
    assert small.text_size_bytes() == big.text_size_bytes()
    assert big.text_size_bytes() < 200


def test_text_size_counts_utf8_bytes_of_canonical_json() -> None:
    request = _noul_request({"page": "é" * 100})
    expected = len(json.dumps(request.to_wire(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert request.text_size_bytes() == expected
    assert request.text_size_bytes() > 200


# --------------------------------------------------------------------------- answers


def test_box_answer_requires_ordered_edges() -> None:
    BoxAnswer(box={"top": 0.1, "bottom": 0.2, "left": 0.3, "right": 0.4})
    with pytest.raises(ValidationError, match="top"):
        BoxAnswer(box={"top": 0.5, "bottom": 0.2, "left": 0.3, "right": 0.4})
    with pytest.raises(ValidationError, match="left"):
        BoxAnswer(box={"top": 0.1, "bottom": 0.2, "left": 0.4, "right": 0.4})
    with pytest.raises(ValidationError):
        BoxAnswer(box={"top": -0.1, "bottom": 0.2, "left": 0.3, "right": 0.4})
    with pytest.raises(ValidationError):
        BoxAnswer(box={"top": 0.1, "bottom": 1.2, "left": 0.3, "right": 0.4})


def test_noul_answer_is_a_probability() -> None:
    assert NoulAnswer(noul=0.5).noul == 0.5
    with pytest.raises(ValidationError):
        NoulAnswer(noul=1.5)


def test_choice_answer_confidence_range() -> None:
    with pytest.raises(ValidationError):
        ChoiceAnswer(choice="a", confidence=2.0, probabilities={"a": 1.0})
    with pytest.raises(ValidationError):
        ChoiceAnswer(choice="a", confidence=1.0, probabilities={"a": -0.1})


def test_score_answer_optional_fields() -> None:
    answer = ScoreAnswer(score=1.0, probabilities={"0": 0.0, "1": 1.0})
    assert answer.confidence is None and answer.legend is None


# --------------------------------------------------------------------------- Decision


def _doc_request_with_box() -> DecisionRequest:
    body = copy.deepcopy(DOC_REQUEST)
    body["questions"] = {k: body["questions"][k] for k in ("billing", "tone", "urgency")}
    body["questions"]["red_rectangle"] = {"type": "box", "instructions": "Locate the red rectangle."}
    body["state"]["photo"] = _image_part()
    return DecisionRequest.model_validate(body)


def _decision(answers: dict[str, Any]) -> Decision:
    return Decision.model_validate({"answers": answers, "engine": "test", "model_version": "test-1", "latency_ms": 5, "cost_usd": None})


def test_decision_typed_accessors() -> None:
    decision = _decision(copy.deepcopy(DOC_ANSWERS))
    assert decision.choice("tone").choice == "calm"
    assert decision.noul("billing").noul == 0.98
    assert decision.score("urgency").score == 1.7
    assert decision.box("red_rectangle").confidence is None
    assert isinstance(decision.answer("tone"), ChoiceAnswer)
    assert decision.cached is False


def test_decision_accessor_errors() -> None:
    decision = _decision(copy.deepcopy(DOC_ANSWERS))
    with pytest.raises(DecisionError, match="missing"):
        decision.answer("nope")
    with pytest.raises(DecisionError, match="noul"):
        decision.noul("tone")


def test_validate_against_accepts_matching_decision() -> None:
    decision = _decision(copy.deepcopy(DOC_ANSWERS))
    assert decision.validate_against(_doc_request_with_box()) is decision


def test_validate_against_reports_missing_answers() -> None:
    answers = copy.deepcopy(DOC_ANSWERS)
    del answers["tone"]
    with pytest.raises(DecisionError, match="tone"):
        _decision(answers).validate_against(_doc_request_with_box())


def test_validate_against_reports_type_mismatch() -> None:
    answers = copy.deepcopy(DOC_ANSWERS)
    answers["billing"] = DOC_ANSWERS["tone"]
    with pytest.raises(DecisionError, match="billing"):
        _decision(answers).validate_against(_doc_request_with_box())


def test_validate_against_reports_unexpected_answers() -> None:
    answers = copy.deepcopy(DOC_ANSWERS)
    answers["extra"] = {"type": "noul", "noul": 0.1}
    with pytest.raises(DecisionError, match="extra"):
        _decision(answers).validate_against(_doc_request_with_box())


def test_validate_against_reports_choice_outside_criteria() -> None:
    answers = copy.deepcopy(DOC_ANSWERS)
    answers["tone"]["choice"] = "sarcastic"
    with pytest.raises(DecisionError, match="sarcastic"):
        _decision(answers).validate_against(_doc_request_with_box())


def test_validate_against_reports_score_out_of_range() -> None:
    answers = copy.deepcopy(DOC_ANSWERS)
    answers["urgency"]["score"] = 3.5
    with pytest.raises(DecisionError, match="urgency"):
        _decision(answers).validate_against(_doc_request_with_box())


def test_decision_rejects_negative_latency_and_cost() -> None:
    with pytest.raises(ValidationError):
        Decision(answers={}, engine="x", model_version="y", latency_ms=-1, cost_usd=None)
    with pytest.raises(ValidationError):
        Decision(answers={}, engine="x", model_version="y", latency_ms=1, cost_usd=-0.5)
