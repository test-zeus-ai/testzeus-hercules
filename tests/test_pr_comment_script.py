"""Offline tests for .github/actions/run-tests/pr_comment.sh (body building only; the gh API is never called)."""

import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / ".github" / "actions" / "run-tests" / "pr_comment.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None, reason="needs bash and python3")

JUNIT = """<testsuites>
  <testsuite name="Feature: Login" tests="2" failures="1" errors="0" skipped="0" time="3.0">
    <testcase name="Scenario: ok" classname="Feature: Login" time="1.0"/>
    <testcase name="Scenario: bad" classname="Feature: Login" time="2.0">
      <failure message="boom">Login failed for postgresql://admin:db-pass-123@db.internal/app with password=hunter2 and Bearer abc.def.ghi and LLM_KEY=sk-live-ABCDEFGH12345 ping @everyone | pipe</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def _run(tmp_path, **extra_env):
    junit = tmp_path / "x_result.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_PR_NUMBER": "12",
        "JUNIT_PATH": str(junit),
        "RUN_URL": "https://github.com/o/r/actions/runs/1",
        "HERCULES_PR_COMMENT_DRY_RUN": "1",
        **extra_env,
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_comment_omits_failure_text_by_default(tmp_path) -> None:
    body = _run(tmp_path)
    assert "Scenario: bad" in body and "failed" in body
    assert "Failure message" not in body
    for leaked in ("hunter2", "db-pass-123", "sk-live", "boom", "Login failed"):
        assert leaked not in body


def test_failure_details_opt_in_redacts_secrets_and_mentions(tmp_path) -> None:
    body = _run(tmp_path, INCLUDE_FAILURE_DETAILS="true", MY_CUSTOM_VAR="topsecretvalue", HERCULES_PASSTHROUGH_VARS="MY_CUSTOM_VAR")
    assert "Failure message" in body and "Login failed" in body
    for leaked in ("hunter2", "db-pass-123", "abc.def.ghi", "sk-live-ABCDEFGH12345"):
        assert leaked not in body, leaked
    assert "@everyone" not in body and "@​everyone" in body
    assert "\\|" in body  # table pipe escaped


def test_passthrough_env_values_are_masked(tmp_path) -> None:
    body = _run(tmp_path, INCLUDE_FAILURE_DETAILS="true", SOME_NAME="Login", HERCULES_PASSTHROUGH_VARS="SOME_NAME")
    assert "Login failed" not in body  # the literal value of a passthrough var is masked wherever it appears


def test_rejects_non_numeric_pr_number(tmp_path) -> None:
    junit = tmp_path / "x_result.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "GITHUB_PR_NUMBER": "12/../../x", "JUNIT_PATH": str(junit), "HERCULES_PR_COMMENT_DRY_RUN": "1"}
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
