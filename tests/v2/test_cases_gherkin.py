"""Tests for the Gherkin to TestCase compiler."""

from pathlib import Path

import pytest

from testzeus_hercules.cases.gherkin import GherkinError, compile_feature, load_feature
from testzeus_hercules.cases.models import SecretRef, TestCase

LOGIN = """\
# A comment before the feature
@smoke @auth
Feature: Login

  Background:
    Given I open "https://app.test/login"

  # comments inside the scenario are ignored
  Scenario: Valid user can sign in!
    When I fill "alice" in "Username"
    And I fill "secret" in "Password"
    And I click "Sign in"
    Then I should see "Welcome"
    And the URL should contain "/home"
    But I should not see "Invalid password"
"""


def test_compiles_single_scenario_into_test_case() -> None:
    case = compile_feature(LOGIN)

    assert isinstance(case, TestCase)
    assert case.id == "valid-user-can-sign-in"
    assert case.title == "Valid user can sign in!"
    assert case.description == "Login"
    assert case.tags == ["smoke", "auth"]
    actions = [step.action for step in case.steps]
    assert actions == [
        'I open "https://app.test/login"',
        'I fill "alice" in "Username"',
        'I fill "secret" in "Password"',
        'I click "Sign in"',
        "verify",
        "verify",
        "verify",
    ]
    assert [step.expected for step in case.steps[4:]] == ['I should see "Welcome"', 'the URL should contain "/home"', 'I should not see "Invalid password"']
    assert all(step.expected is None for step in case.steps[:4])


def test_background_steps_are_prepended() -> None:
    case = compile_feature(LOGIN)

    assert case.steps[0].action == 'I open "https://app.test/login"'


def test_star_bullet_and_then_continuation() -> None:
    text = 'Feature: F\nScenario: S\n* I open /\nThen I should see "a"\n* I should see "b"\n'
    case = compile_feature(text)

    assert [s.action for s in case.steps] == ["I open /", "verify", "verify"]
    assert case.steps[2].expected == 'I should see "b"'


def test_base_url_and_test_data_are_carried() -> None:
    text = 'Feature: F\nScenario: S\nGiven I open "/login"\nWhen I fill "{{user}}" in "Username"\n'
    case = compile_feature(text, base_url="http://127.0.0.1:8000", test_data={"user": "alice", "pw": SecretRef(secret="APP_PW")})

    assert case.environment.base_url == "http://127.0.0.1:8000"
    assert case.test_data["user"] == "alice"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('Feature: F\nScenario: S\nGiven I open "/"\n  """\n  body\n  """\n', "doc string"),
        ("Feature: F\nScenario: S\nGiven these users\n  | name |\n  | a |\n", "data table"),
        ("Feature: F\nScenario: A\nGiven I open /\nScenario: B\nGiven I open /\n", "exactly one scenario"),
        ("Feature: F\nGiven I open /\n", "outside a scenario"),
        ("Feature: F\nScenario Outline: S\nGiven I open <url>\nExamples:\n", "Scenario Outline"),
        ("Feature: F\nScenario: S\n", "no steps"),
        ("Feature: F\n", "exactly one scenario"),
        ("Scenario: S\nGiven I open /\nThis is not a step\n", ":3: expected a step"),
    ],
)
def test_rejects_unsupported_or_malformed_input(text: str, message: str) -> None:
    with pytest.raises(GherkinError, match=message):
        compile_feature(text)


def test_error_names_source_and_line() -> None:
    with pytest.raises(GherkinError, match=r"login\.feature:3"):
        compile_feature("Feature: F\nScenario: S\n| a |\n", source_name="login.feature")


def test_slug_falls_back_when_name_has_no_letters() -> None:
    case = compile_feature("Feature: F\nScenario: ???\nGiven I open /\n")

    assert case.id == "scenario"


def test_load_feature_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "login.feature"
    path.write_text(LOGIN, encoding="utf-8")

    assert load_feature(path).id == "valid-user-can-sign-in"


def test_doc_string_after_a_colon_is_folded_into_the_step() -> None:
    text = 'Feature: F\nScenario: S\n  When I send a POST request to "/pets" with the JSON body:\n    """\n    {\n      "name": "Rex"\n    }\n    """\n  Then the query on "db" should be:\n    ```\n    SELECT 1\n    ```\n'
    case = compile_feature(text)

    assert case.steps[0].action == 'I send a POST request to "/pets" with the JSON body: {\n  "name": "Rex"\n}'
    assert case.steps[1].action == "verify" and case.steps[1].expected == 'the query on "db" should be: SELECT 1'


def test_unclosed_doc_string_is_an_error() -> None:
    with pytest.raises(GherkinError, match="not closed"):
        compile_feature('Feature: F\nScenario: S\nGiven the body:\n  """\n  x\n')
