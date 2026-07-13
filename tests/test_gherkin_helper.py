import asyncio
import os

from testzeus_hercules.utils.gherkin_helper import (
    serialize_feature_file,
    split_feature_file,
)


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
