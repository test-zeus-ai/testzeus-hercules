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


# ── marker-comment lookup is limited to the Actions bot ──────────────────────

_FAKE_GH = """#!/usr/bin/env bash
# Offline stand-in for `gh api`: logs every call; GET applies --jq (via jq) to $FAKE_GH_COMMENTS.
echo "$*" >> "$FAKE_GH_LOG"
method=GET; jq_expr=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    -X) method="${args[$((i + 1))]}" ;;
    --jq) jq_expr="${args[$((i + 1))]}" ;;
  esac
done
if [ "$method" = GET ]; then
  jq -r "$jq_expr" "$FAKE_GH_COMMENTS"
fi
"""

_MARKER = "<!-- hercules-results -->"


def _post_with_fake_gh(tmp_path, comments: list[dict]):
    import json

    junit = tmp_path / "x_result.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(_FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    comments_file = tmp_path / "comments.json"
    comments_file.write_text(json.dumps(comments), encoding="utf-8")
    log = tmp_path / "gh.log"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_PR_NUMBER": "12",
        "GITHUB_TOKEN": "dummy-token",
        "GITHUB_REPOSITORY": "o/r",
        "JUNIT_PATH": str(junit),
        "FAKE_GH_COMMENTS": str(comments_file),
        "FAKE_GH_LOG": str(log),
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return log.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_marker_comment_lookup_ignores_comments_not_authored_by_actions_bot(tmp_path) -> None:
    calls = _post_with_fake_gh(
        tmp_path,
        [
            {"id": 111, "user": {"login": "mallory"}, "body": f"{_MARKER}\nplanted"},
            {"id": 222, "user": {"login": "github-actions[bot]"}, "body": f"{_MARKER}\nreal"},
        ],
    )
    writes = [call for call in calls if "-X" in call]
    assert len(writes) == 1
    assert "-X PATCH" in writes[0] and "/comments/222" in writes[0]
    assert not any("/comments/111" in call for call in calls)


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_planted_marker_from_another_user_results_in_a_new_comment_not_a_patch(tmp_path) -> None:
    calls = _post_with_fake_gh(tmp_path, [{"id": 111, "user": {"login": "mallory"}, "body": f"{_MARKER}\nplanted"}])
    writes = [call for call in calls if "-X" in call]
    assert len(writes) == 1 and "-X POST" in writes[0]
    assert not any("PATCH" in call for call in calls)


def test_example_workflow_and_readme_harden_checkout_and_mention_pinning() -> None:
    import yaml

    root = pathlib.Path(__file__).resolve().parents[1] / ".github"
    workflow = yaml.safe_load((root / "examples" / "hercules-workflow.yml").read_text(encoding="utf-8"))
    checkouts = [step for job in workflow["jobs"].values() for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout")]
    assert checkouts and all(step.get("with", {}).get("persist-credentials") is False for step in checkouts)

    readme = (root / "actions" / "run-tests" / "README.md").read_text(encoding="utf-8")
    assert "persist-credentials: false" in readme
    assert "commit SHAs" in readme and "github-actions[bot]" in readme


def test_failure_note_matches_failure_details_setting(tmp_path) -> None:
    assert "they are not posted here" in _run(tmp_path)
    detailed = _run(tmp_path, INCLUDE_FAILURE_DETAILS="true")
    assert "they are not posted here" not in detailed
    assert "redacted and truncated" in detailed
