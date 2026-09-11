import asyncio
import os
import re

import pytest
from testzeus_hercules.utils.gherkin_helper import (
    serialize_feature_file,
    split_feature_file,
)

SCENARIO_TITLE_RE = re.compile(r"^\s*scenario(?:\s+outline)?\s*:\s*([^\n]*)", re.IGNORECASE | re.MULTILINE)


def test_split_feature_file_does_not_duplicate_scenario_title(tmp_path) -> None:
    input_file = tmp_path / "test.feature"
    output_dir = tmp_path / "gherkin_files"
    input_file.write_text(
        """Feature: User Signup and Account Deletion

  Scenario: Signup page
    Given the user is on the home page
    When the user clicks on the "Signup / Login" button
    Then the "New User Signup!" message should be visible
""",
        encoding="utf-8",
    )

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 1
    output_file = scenarios[0]["output_file"]
    split_content = (output_dir / os.path.basename(output_file)).read_text(encoding="utf-8")
    serialized = asyncio.run(serialize_feature_file(output_file))

    assert split_content.count("Scenario: Signup page") == 1
    assert "\nSignup page\n" not in split_content
    assert "Scenario: Signup pageSignup page" not in serialized


def test_split_feature_file_distinct_titles_colliding_safe_names(tmp_path) -> None:
    """Titles that sanitize to the same stem must not clobber each other's file."""
    input_file = tmp_path / "auth.feature"
    output_dir = tmp_path / "gherkin_files"
    input_file.write_text(
        """Feature: Auth

  Scenario: User logs in!
    Given the login page is open

  Scenario: User logs in?
    Given the login page is open again
""",
        encoding="utf-8",
    )

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 2
    paths = {scenario["output_file"] for scenario in scenarios}
    assert len(paths) == 2
    assert all(os.path.isfile(path) for path in paths)
    assert {os.path.basename(path) for path in paths} == {"User_logs_in_.feature", "User_logs_in__1.feature"}

    first = open(scenarios[0]["output_file"], encoding="utf-8").read()
    second = open(scenarios[1]["output_file"], encoding="utf-8").read()
    assert "Scenario: User logs in!" in first and "User logs in?" not in first
    assert "Scenario: User logs in?" in second and "User logs in!" not in second


def test_split_feature_file_tags_and_rules_stay_with_their_scenarios(tmp_path) -> None:
    """Tricky feature: feature tags, Background, Rule with own Background,
    per-scenario tags, and a Scenario Outline with pipes in Examples."""
    input_file = tmp_path / "payments.feature"
    output_dir = tmp_path / "gherkin_files"
    input_file.write_text(
        """@api-smoke
Feature: Payments

  Background:
    Given the payments service is up

  @fast @regression
  Scenario: Card charge
    Given a valid card
    Then the charge succeeds
    # trailing note for next scenario

  @rule-payments
  Rule: premium accounts
    Background:
      Given a premium account

  @slow
  Scenario: Premium payout
    Given a payout request
    Then the payout completes

  Scenario Outline: Payout limits <limit>
    Given a payout of <limit>
    Then the result is <result>

    Examples:
      | limit | result |
      | 10    | ok     |
      | 999   | error  |
""",
        encoding="utf-8",
    )

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 3
    contents = {}
    for scenario in scenarios:
        path = scenario["output_file"]
        assert os.path.isfile(path)
        content = open(path, encoding="utf-8").read()
        contents[scenario["scenario"]] = content

        # Every generated file must contain exactly its own scenario (plus the
        # outline's own title) according to split's own regex.
        titles = SCENARIO_TITLE_RE.findall(content)
        expected_title = scenario["scenario"].split(" - ")[0]
        assert titles == [expected_title], f"{path}: {titles}"

    card, premium, outline = (contents[key] for key in ("Card charge", "Premium payout", "Payout limits <limit>"))

    shared_header = "@api-smoke\nFeature: Payments\n\n  Background:\n    Given the payments service is up"
    for content in (card, premium, outline):
        assert shared_header in content

    # First scenario keeps its own tags, which must NOT leak into the others.
    assert "@fast @regression" in card
    assert "@fast @regression" not in premium and "@fast @regression" not in outline
    assert "Scenario: Card charge" in card

    # The Rule block (tags, Rule line and its Background) belongs to the
    # scenarios that follow it within the rule, not to the previous scenario.
    assert "@slow" in premium and "Scenario: Premium payout" in premium
    assert "Rule: premium accounts" in premium and "Given a premium account" in premium
    assert "Rule: premium accounts" not in card
    assert "Scenario: Premium payout" not in card and "@slow" not in card

    # The outline is inside the same rule and still carries the rule setup.
    assert "Rule: premium accounts" in outline and "Given a premium account" in outline
    assert "| limit | result |" in outline and "| 10    | ok     |" in outline
    assert "@slow" not in outline and "Scenario: Premium payout" not in outline


def test_split_feature_file_rule_block_goes_to_following_scenario_not_previous(tmp_path) -> None:
    input_file = tmp_path / "rules.feature"
    output_dir = tmp_path / "gherkin_files"
    input_file.write_text(
        """Feature: Rules

  Scenario: Plain
    Given a plain step

  Rule: gated area
    Background:
      Given the gate is locked

  Scenario: Gated
    Given a gated step
""",
        encoding="utf-8",
    )

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 2
    first = open(scenarios[0]["output_file"], encoding="utf-8").read()
    second = open(scenarios[1]["output_file"], encoding="utf-8").read()

    assert "Scenario: Plain" in first and "Rule: gated area" not in first
    assert "Scenario: Gated" in second
    assert "Rule: gated area" in second and "Given the gate is locked" in second


def test_split_feature_file_overlong_title_is_clamped(tmp_path) -> None:
    """A 248+ char safe title used to exceed NAME_MAX (255) and abort the ENTIRE split."""
    input_file = tmp_path / "long.feature"
    output_dir = tmp_path / "gherkin_files"
    long_title = "a" * 300
    input_file.write_text(
        f"""Feature: Long

  Scenario: {long_title}
    Given a step
""",
        encoding="utf-8",
    )

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 1
    output_file = scenarios[0]["output_file"]
    assert os.path.isfile(output_file)
    assert len(os.path.basename(output_file)) <= 255
    content = open(output_file, encoding="utf-8").read()
    assert f"Scenario: {long_title}" in content


def test_split_feature_file_long_titles_sharing_prefix_get_distinct_files(tmp_path) -> None:
    """Distinct over-long titles clamping to the same stem must still get distinct files."""
    input_file = tmp_path / "twolong.feature"
    output_dir = tmp_path / "gherkin_files"
    prefix = "a" * 250
    input_file.write_text(
        f"""Feature: Long

  Scenario: {prefix}ONE
    Given a step

  Scenario: {prefix}TWO
    Given another step
""",
        encoding="utf-8",
    )

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 2
    basenames = [os.path.basename(scenario["output_file"]) for scenario in scenarios]
    assert len(set(basenames)) == 2  # dedupe suffix fired after the clamp
    assert all(len(name) <= 255 for name in basenames)
    assert all(os.path.isfile(scenario["output_file"]) for scenario in scenarios)
    first = open(scenarios[0]["output_file"], encoding="utf-8").read()
    second = open(scenarios[1]["output_file"], encoding="utf-8").read()
    assert f"Scenario: {prefix}ONE" in first and prefix + "TWO" not in first
    assert f"Scenario: {prefix}TWO" in second and prefix + "ONE" not in second


def test_split_feature_file_survives_bom_and_cjk_content(tmp_path) -> None:
    """A BOM'd feature file with CJK must split cleanly regardless of host locale.

    The BOM used to be copied into every split file (or swallow the first
    scenario regex match), and a locale-codec read raises UnicodeDecodeError on
    bytes that cp1252 does not define — so the probe character below is chosen
    because its UTF-8 encoding contains 0x81 (undefined in cp1252).
    """
    probe_char = "\u0601"  # UTF-8: D8 81 — 0x81 is undefined in cp1252
    assert probe_char.encode("utf-8")[-1] == 0x81
    with pytest.raises(UnicodeDecodeError):
        probe_char.encode("utf-8").decode("cp1252")

    feature_text = f"Feature: 用户登录测试\n\n  Scenario: 登录失败 {probe_char}\n    Given a step\n"
    input_file = tmp_path / "cjk.feature"
    input_file.write_bytes(b"\xef\xbb\xbf" + feature_text.encode("utf-8"))
    output_dir = tmp_path / "gherkin_files"

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    # The BOM must not suppress the scenario match (silent empty split).
    assert len(scenarios) == 1
    output_file = scenarios[0]["output_file"]
    assert os.path.isfile(output_file)
    with open(output_file, "rb") as handle:
        raw = handle.read()
    # Written back as UTF-8 (not the locale codec) with no BOM copied through.
    assert "登录失败".encode("utf-8") in raw
    assert probe_char.encode("utf-8") in raw
    assert b"\xef\xbb\xbf" not in raw
    content = raw.decode("utf-8")
    assert "登录失败" in content and "\ufeff" not in content
    serialized = asyncio.run(serialize_feature_file(output_file))
    assert "登录失败" in serialized and "\ufeff" not in serialized
