"""Regression tests: split_feature_file must not let scenario titles escape the output directory.

A hostile scenario title (e.g. "../../etc/passwd") used to be turned into a
filename verbatim (spaces only replaced), letting os.path.join() walk out of
the split directory. The sanitizer in gherkin_helper must keep every produced
path inside output_dir without changing names for ordinary titles.
"""

import asyncio
import os

from testzeus_hercules.utils.gherkin_helper import (
    _sanitize_scenario_filename,
    split_feature_file,
)

HOSTILE_TITLES = [
    "../../etc/passwd",
    "..\\..\\win",
    "../pwned feature",
    "..",
    "....//....//evil",
    "sub/dir/escape",
]


def _write_feature(directory, scenario_titles) -> str:
    body = "".join(f"  Scenario: {title}\n    Given a step\n\n" for title in scenario_titles)
    path = os.path.join(str(directory), "traversal.feature")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"Feature: Traversal probe\n\n{body}")
    return path


def _is_inside_output_dir(output_dir: str, path: str) -> bool:
    real_root = os.path.realpath(output_dir)
    return os.path.realpath(path).startswith(real_root + os.sep)


def test_sanitize_scenario_filename_strips_traversal() -> None:
    for title in HOSTILE_TITLES:
        sanitized = _sanitize_scenario_filename(title)
        assert "/" not in sanitized and "\\" not in sanitized
        assert not sanitized.startswith(".")
        assert ".." not in sanitized
        assert sanitized  # never empty


def test_sanitize_scenario_filename_keeps_normal_titles() -> None:
    assert _sanitize_scenario_filename("Signup page") == "Signup_page"
    assert _sanitize_scenario_filename("s0") == "s0"
    assert _sanitize_scenario_filename("Verify checkout - user v1.0") == "Verify_checkout_-_user_v1.0"


def test_sanitize_scenario_filename_falls_back_when_nothing_safe_remains() -> None:
    assert _sanitize_scenario_filename("") == "scenario"
    assert _sanitize_scenario_filename(".") == "scenario"
    # Punctuation-only titles collapse to a single safe underscore.
    assert _sanitize_scenario_filename("...") == "_"
    assert _sanitize_scenario_filename("///") == "_"


def test_split_feature_file_hostile_titles_stay_inside_output_dir(tmp_path) -> None:
    output_dir = tmp_path / "gherkin_files"
    input_file = _write_feature(tmp_path, HOSTILE_TITLES)

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == len(HOSTILE_TITLES)
    for scenario in scenarios:
        assert _is_inside_output_dir(str(output_dir), scenario["output_file"])
        assert os.path.isfile(scenario["output_file"])
        # The readable scenario title is preserved; only the filename is sanitized.
        assert scenario["scenario"] in HOSTILE_TITLES


def test_split_feature_file_duplicate_hostile_titles_stay_inside(tmp_path) -> None:
    output_dir = tmp_path / "gherkin_files"
    input_file = _write_feature(tmp_path, ["../dup", "../dup"])

    scenarios = asyncio.run(split_feature_file(str(input_file), str(output_dir)))

    assert len(scenarios) == 2
    assert len({scenario["output_file"] for scenario in scenarios}) == 2
    for scenario in scenarios:
        assert _is_inside_output_dir(str(output_dir), scenario["output_file"])
