"""Multi-scenario Gherkin parsing for importers: backgrounds, outlines with Examples, rules, doc strings and tables."""

from __future__ import annotations

import pytest

from testzeus_hercules.cases.gherkin import GherkinError
from testzeus_hercules.cases.importers.gherkin_multi import parse_feature

FEATURE = """\
# language: en
@web
Feature: Checkout
  Free text describing the feature.

  Background:
    Given I open the page "http://localhost:3000"

  Scenario: Add to cart
    When I click "Add"
    Then the cart shows "1"
    And the total is "$10"

  @outline
  Scenario Outline: Search for <term>
    When I search for "<term>"
    Then I see "<count>" results

    Examples: common
      | term   | count |
      | shoes  | 3     |
    @rare
    Examples:
      | term   | count |
      | kayak  | 0     |
"""


def test_background_and_then_steps_follow_the_single_scenario_compiler() -> None:
    feature = parse_feature(FEATURE, source_name="checkout.feature")

    first = feature.scenarios[0]
    assert feature.name == "Checkout"
    assert feature.description == "Free text describing the feature."
    assert first.name == "Add to cart"
    assert [step.action for step in first.steps] == ['I open the page "http://localhost:3000"', 'I click "Add"', "verify", "verify"]
    assert [step.expected for step in first.steps[2:]] == ['the cart shows "1"', 'the total is "$10"']
    assert first.tags == ["web"]


def test_outline_expands_one_scenario_per_example_row_with_values_substituted() -> None:
    feature = parse_feature(FEATURE, source_name="checkout.feature")

    rows = feature.scenarios[1:]
    assert [row.name for row in rows] == ["Search for shoes", "Search for kayak"]
    assert rows[0].example == {"term": "shoes", "count": "3"}
    assert rows[0].example_index == 1 and rows[1].example_index == 2
    assert rows[0].steps[1].action == 'I search for "shoes"'
    assert rows[1].steps[2].expected == 'I see "0" results'
    assert rows[0].tags == ["web", "outline"]
    assert rows[1].tags == ["web", "outline", "rare"]
    assert rows[0].outline is True


def test_rule_background_applies_only_inside_the_rule() -> None:
    text = """\
Feature: Rules
  Background:
    Given I am on the home page
  Rule: Members
    Background:
      Given I am signed in
    Scenario: Member sees perks
      Then I see "Perks"
  Rule: Guests
    Scenario: Guest sees sign-up
      Then I see "Sign up"
"""
    feature = parse_feature(text)

    member, guest = feature.scenarios
    assert [step.action for step in member.steps] == ["I am on the home page", "I am signed in", "verify"]
    assert [step.action for step in guest.steps] == ["I am on the home page", "verify"]


def test_doc_strings_and_step_tables_are_folded_into_the_step_text() -> None:
    text = '''\
Feature: Forms
  Scenario: Fill the form
    When I fill the form with
      | field | value |
      | name  | Ada   |
    And I type the note
      """
      first line
      second line
      """
    Then the page shows
      """
      Thanks, Ada
      """
'''
    steps = parse_feature(text).scenarios[0].steps

    assert steps[0].action == "I fill the form with (table: field=name, value=Ada)"
    assert steps[1].action == 'I type the note with text: "first line\\nsecond line"'
    assert steps[2].action == "verify" and steps[2].expected == 'the page shows with text: "Thanks, Ada"'


def test_doc_string_after_a_colon_keeps_its_line_breaks() -> None:
    text = 'Feature: F\n  Scenario: S\n    When I send a POST request to "/pets" with the JSON body:\n      """\n      {"name": "Rex",\n       "tag": "dog"}\n      """\n'
    steps = parse_feature(text).scenarios[0].steps

    assert steps[0].action == 'I send a POST request to "/pets" with the JSON body: {"name": "Rex",\n "tag": "dog"}'


def test_scenario_without_steps_is_kept_with_no_steps_for_the_caller_to_report() -> None:
    feature = parse_feature("Feature: x\n  Scenario: empty\n  Scenario: full\n    Given a step\n")

    assert [(s.name, len(s.steps)) for s in feature.scenarios] == [("empty", 0), ("full", 1)]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Feature: x\n  Given a stray step\n", "step outside a scenario"),
        ("Feature: x\n  Scenario Outline: o\n    Given <a>\n", "has no Examples"),
        ("Feature: x\n  Scenario Outline: o\n    Given <a>\n    Examples:\n      | a |\n      | 1 | 2 |\n", "cells"),
        ('Feature: x\n  Scenario: s\n    Given a\n    """\n    never closed\n', "doc string is not closed"),
    ],
)
def test_malformed_files_raise_a_located_error(text: str, message: str) -> None:
    with pytest.raises(GherkinError, match=message):
        parse_feature(text, source_name="bad.feature")


@pytest.mark.parametrize(
    "text",
    [
        "Feature: x\n  Scenario: s\n    Gevin I log in\n    Then I see the dashboard\n",
        "Feature: x\n  Background:\n    Gevin I log in\n  Scenario: s\n    Then I see the dashboard\n",
        "Feature: x\n  Rule: r\n    Background:\n      Gevin I log in\n    Scenario: s\n      Then ok\n",
    ],
)
def test_a_misspelt_step_before_the_first_step_or_in_a_background_is_an_error_not_dropped(text: str) -> None:
    with pytest.raises(GherkinError, match=r"bad\.feature:\d+: expected a step .*'Gevin I log in'"):
        parse_feature(text, source_name="bad.feature")


def test_free_text_under_feature_and_rule_is_still_their_description() -> None:
    feature = parse_feature("Feature: x\n  As a user I want things.\n  Rule: r\n    Some rule text.\n    Scenario: s\n      Given a step\n")

    assert [s.name for s in feature.scenarios] == ["s"]


def test_free_text_before_a_scenarios_first_step_is_its_description() -> None:
    text = 'Feature: x\n  Scenario: s\n    This scenario checks the cart.\n    Any user can do it.\n    Given I open "/cart"\n    Then I see "Cart"\n'
    feature = parse_feature(text, source_name="ok.feature")
    assert [step.action for step in feature.scenarios[0].steps][0] == 'I open "/cart"'


def test_free_text_after_a_step_is_an_error() -> None:
    text = 'Feature: x\n  Scenario: s\n    Given I open "/cart"\n    this line is not a step\n    Then I see "Cart"\n'
    with pytest.raises(GherkinError, match="this line is not a step"):
        parse_feature(text, source_name="bad.feature")
