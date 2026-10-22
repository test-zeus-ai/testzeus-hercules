"""Static checks for the composite GitHub Action (.github/actions/run-tests/action.yml)."""

import pathlib
import re

import yaml

ACTION = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / ".github" / "actions" / "run-tests" / "action.yml").read_text(encoding="utf-8"))


def test_default_image_is_docker_hub() -> None:
    assert ACTION["inputs"]["image"]["default"] == "testzeus/hercules:latest"


def test_no_expression_is_interpolated_into_shell_scripts() -> None:
    """`${{ ... }}` inside a `run:` body is a script-injection vector; inputs must arrive via `env:`."""
    offenders = [step.get("name") for step in ACTION["runs"]["steps"] if "run" in step and re.search(r"\$\{\{", step["run"])]
    assert offenders == []


def test_run_step_builds_argv_array_not_shell_string() -> None:
    run_step = next(step for step in ACTION["runs"]["steps"] if step.get("id") == "run")["run"]
    assert "bash -lec" not in run_step and "/bin/bash -lc" not in run_step
    assert "CMD=(" in run_step


def test_failure_details_are_opt_in() -> None:
    assert ACTION["inputs"]["pr-comment-failure-details"]["default"] == "false"
