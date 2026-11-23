"""Tests for the step question builder (fan-out decision requests)."""

import pytest

from testzeus_hercules.decide.contract import ChoiceQuestion, NoulQuestion
from testzeus_hercules.decide.payload import iter_image_parts
from testzeus_hercules.decide.questions import MAX_ROWS, MAX_TEXT_BYTES, NO_MATCH, TargetSpec, build_step_request, build_step_requests, md_from_option, option_for, tokens

from tests.v2.engine_fixtures.fakes import FakeBBox, FakeElement, FakeSnapshot, login_elements


def snap(*elements: FakeElement) -> FakeSnapshot:
    return FakeSnapshot(seq=3, elements=elements or login_elements())


def test_tokens_normalise_case_punctuation_and_camel_case() -> None:
    assert tokens("Sign-In  NOW!") == ("sign", "in", "now")
    assert tokens("userName_field") == ("user", "name", "field")
    assert tokens("") == ()


def test_option_naming_round_trip() -> None:
    assert option_for(12) == "md_12"
    assert md_from_option("md_12") == 12
    assert md_from_option(NO_MATCH) is None
    with pytest.raises(ValueError):
        md_from_option("button_3")


def test_target_question_lists_every_row_in_page_order_and_no_match() -> None:
    built = build_step_request(TargetSpec(kind="fill", target="Password", text='fill "x" in "Password"'), snap())

    target = built.request.questions["target"]
    assert isinstance(target, ChoiceQuestion)
    options = list(target.criteria)
    assert options == [f"md_{element.md}" for element in login_elements()] + [NO_MATCH]
    assert target.criteria["md_3"] == 'textbox "Password"'
    assert isinstance(built.request.questions["target_absent"], NoulQuestion)
    assert "tool" not in built.request.questions
    assert [row.md for row in built.rows] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    "spec",
    [
        TargetSpec(kind="fill", target="password"),
        TargetSpec(kind="click", target="Sign in", role="button"),
        TargetSpec(kind="check", target="Remember me"),
        TargetSpec(kind="select", target="Language", after_input=True),
        TargetSpec(kind="click", target="nothing alike"),
    ],
)
def test_rows_do_not_depend_on_the_steps_words(spec: TargetSpec) -> None:
    reference = build_step_request(TargetSpec(kind="click", target="x"), snap())
    built = build_step_request(spec, snap())

    assert [row.md for row in built.rows] == [row.md for row in reference.rows], "nothing is ranked or dropped by matching words"
    assert not hasattr(built.rows[0], "score")


def test_rows_follow_page_order_even_when_perception_listed_them_otherwise() -> None:
    elements = (FakeElement(md=9, role="button", name="Later"), FakeElement(md=2, role="button", name="Earlier"), FakeElement(md=5, role="link", name="Middle", in_viewport=False))
    built = build_step_request(TargetSpec(kind="click", target="Later"), snap(*elements))

    assert [row.md for row in built.rows] == [2, 5, 9]


def test_a_page_with_more_elements_than_one_question_is_split_into_page_order_groups() -> None:
    elements = tuple(FakeElement(md=i, role="button", name=f"Item {i}") for i in range(1, 601))
    groups = build_step_requests(TargetSpec(kind="click", target="Item 7"), snap(*elements))

    assert [len(group.rows) for group in groups] == [MAX_ROWS, MAX_ROWS, 600 - 2 * MAX_ROWS]
    assert groups[1].rows[0].md == MAX_ROWS + 1
    assert all(len(group.request.questions["target"].criteria) == len(group.rows) + 1 for group in groups)  # type: ignore[union-attr]
    only = build_step_request(TargetSpec(kind="click", target="Item 7"), snap(*elements), only=frozenset({7, 500}))
    assert [row.md for row in only.rows] == [7, 500]


def test_excluded_candidates_are_left_out() -> None:
    built = build_step_request(TargetSpec(kind="fill", target="Password"), snap(), exclude=frozenset({3}))

    assert 3 not in {row.md for row in built.rows}


def test_tool_question_only_for_ambiguous_kind() -> None:
    built = build_step_request(TargetSpec(kind="unknown", target="Remember me"), snap())

    tool = built.request.questions["tool"]
    assert isinstance(tool, ChoiceQuestion)
    assert {"click", "fill", "select", "check", "hover"} <= set(tool.criteria)


def test_state_carries_step_page_and_rows_without_images_by_default() -> None:
    built = build_step_request(TargetSpec(kind="click", target="Sign in", text='click "Sign in"'), snap())
    state = built.request.state

    assert state["step"]["target"] == "Sign in"
    assert state["page"]["snapshot"] == 3
    assert state["rows"][0]["id"] == "md_1"
    assert not built.request.has_images()


def test_images_flag_adds_som_image() -> None:
    built = build_step_request(TargetSpec(kind="click", target="Sign in"), snap(), images=True)

    assert built.request.has_images()
    assert len(list(iter_image_parts(built.request.to_wire()))) == 1


def test_password_values_are_never_in_state() -> None:
    elements = (FakeElement(md=1, role="textbox", name="Password", attrs={"type": "password"}, value="hunter2"),)
    built = build_step_request(TargetSpec(kind="fill", target="Password"), snap(*elements))

    assert "hunter2" not in str(built.request.to_wire())


def test_rows_are_never_dropped_to_fit_a_size_limit() -> None:
    long = "x" * 3000
    elements = tuple(FakeElement(md=i, role="button", name=f"{long} {i}", attrs={"title": long, "aria-label": long}) for i in range(1, 200))
    built = build_step_request(TargetSpec(kind="click", target="x"), snap(*elements))

    assert len(built.rows) == 199, "the decider splits a large pick; the builder keeps every row"
    assert built.request.text_size_bytes() > MAX_TEXT_BYTES
    assert len(built.request.questions["target"].criteria) == 200  # type: ignore[union-attr]


def test_rows_and_options_carry_where_the_element_is() -> None:
    elements = (
        FakeElement(md=1, role="button", name="Add to cart", bbox=FakeBBox(x=40, y=300, width=100, height=30)),
        FakeElement(md=2, role="button", name="Add to cart", bbox=FakeBBox(x=1100, y=300, width=100, height=30)),
    )
    built = build_step_request(TargetSpec(kind="click", target="Add to cart"), snap(*elements))

    rows = {row["id"]: row for row in built.request.state["rows"]}
    assert rows["md_1"]["where"] == "left (x 90, y 320), #1 of 2 alike"
    assert rows["md_2"]["where"] == "right (x 1150, y 320), #2 of 2 alike"
    criteria = built.request.questions["target"].criteria  # type: ignore[union-attr]
    assert criteria["md_2"] == 'button "Add to cart" at right (x 1150, y 320), #2 of 2 alike'


def test_empty_snapshot_still_builds_a_valid_request() -> None:
    built = build_step_request(TargetSpec(kind="click", target="Sign in"), FakeSnapshot(seq=1))

    assert list(built.request.questions["target"].criteria) == [NO_MATCH]  # type: ignore[union-attr]


def test_rows_are_described_by_their_caption_not_their_input_type() -> None:
    hints = FakeElement(md=1, role="textbox", name="Start typing...", attrs={"type": "text", "label": "Person Name", "placeholder": "Start typing..."})
    nameless = FakeElement(md=2, role="textbox", name="", attrs={"type": "text", "name": "username"})
    built = build_step_request(TargetSpec(kind="fill", target="Person Name"), snap(hints, nameless))
    criteria = built.request.questions["target"].criteria  # type: ignore[union-attr]
    assert "Person Name" in criteria["md_1"]
    assert criteria["md_2"] == 'textbox "username"'


def test_fields_sharing_a_placeholder_are_told_apart_by_caption() -> None:
    employee = FakeElement(md=1, role="textbox", name="Start typing...", attrs={"label": "Person Name", "placeholder": "Start typing..."})
    supervisor = FakeElement(md=2, role="textbox", name="Start typing...", attrs={"label": "Manager Name", "placeholder": "Start typing..."})
    built = build_step_request(TargetSpec(kind="fill", target="Manager Name"), snap(employee, supervisor))
    criteria = built.request.questions["target"].criteria  # type: ignore[union-attr]
    assert "Person Name" in criteria["md_1"] and "Manager Name" in criteria["md_2"]


def test_same_named_buttons_are_told_apart_by_their_surroundings() -> None:
    light = FakeElement(md=4, role="button", name="Remove", attrs={"near": "Trail Lamp a red lamp $9.99"})
    pack = FakeElement(md=6, role="button", name="Remove", attrs={"near": "Trail Daypack holds a laptop $29.99"})
    text = 'click "Remove" (part of the step: they open the cart and remove the "Trail Daypack" from it)'
    built = build_step_request(TargetSpec(kind="click", target="Remove", role="button", text=text), snap(light, pack))
    assert [row.md for row in built.rows] == [4, 6]
    criteria = built.request.questions["target"].criteria  # type: ignore[union-attr]
    assert "Daypack" in criteria["md_6"] and "Lamp" in criteria["md_4"]


def test_after_typing_into_a_form_the_decider_is_told_and_sees_which_button_submits() -> None:
    tab = FakeElement(md=5, role="button", name="Deposit", attrs={"near": "Transactions Withdraw"})
    submit = FakeElement(md=8, role="button", name="Deposit", attrs={"type": "submit", "near": "Amount to send"})
    after = build_step_request(TargetSpec(kind="click", target="Deposit", role="button", after_input=True), snap(tab, submit))
    assert [row.md for row in after.rows] == [5, 8]
    assert "submits a form" in after.request.questions["target"].criteria["md_8"]  # type: ignore[union-attr]
    assert after.request.state["step"]["after_input"] is True


def test_an_element_out_of_view_is_described_by_its_context_and_may_be_chosen() -> None:
    from testzeus_hercules.decide.questions import _describe

    shown = _describe({"role": "combobox", "name": "Type", "where": 'center (x 440, y 320), in dialog "New Account"'})
    hidden = _describe({"role": "combobox", "name": "Rating", "where": 'in dialog "New Account", in the dialog\'s scroll area, above the visible part'})
    assert shown == 'combobox "Type" at center (x 440, y 320), in dialog "New Account"'
    assert hidden == 'combobox "Rating", in dialog "New Account", in the dialog\'s scroll area, above the visible part'
    built = build_step_request(TargetSpec(kind="select", target="Rating"), snap(FakeElement(md=1, role="combobox", name="Rating")))
    assert "the run scrolls to it" in built.request.questions["target"].instructions
