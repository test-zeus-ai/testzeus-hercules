"""docker-publish.yml: which refs trigger it and when it may push (offline, runs the workflow's own shell)."""

import os
import pathlib
import re
import subprocess

import pytest
import yaml

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docker-publish.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _resolve_script() -> str:
    steps = _workflow()["jobs"]["prepare"]["steps"]
    return next(step["run"] for step in steps if step.get("id") == "tags")


@pytest.fixture()
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A git repo with release tags 1.4.2 / 2.0.0 and a non-release tag 'legacy-build'."""
    git = lambda *args: subprocess.run(
        ["git", *args], cwd=tmp_path, check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    )
    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "init")
    for tag in ("1.4.2", "2.0.0", "legacy-build"):
        git("tag", tag)
    return tmp_path


def _run(repo: pathlib.Path, **env: str) -> tuple[int, dict[str, str], str]:
    output = repo / "github_output"
    output.write_text("")
    base = {
        "PATH": os.environ["PATH"],
        "GITHUB_OUTPUT": str(output),
        "IMAGE_NAME": "testzeus/hercules",
        "DOCKERHUB_USERNAME": "user",
        "DOCKERHUB_TOKEN": "token",
        "DEFAULT_BRANCH": "main",
        "INPUT_TAG": "",
        "INPUT_PUSH": "",
    }
    result = subprocess.run(["bash", "-e", "-c", _resolve_script()], cwd=repo, env={**base, **env}, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return result.returncode, outputs, result.stdout + result.stderr


def _tag_push(repo, name):
    return _run(repo, GITHUB_EVENT_NAME="push", GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=name)


def _manual(repo, tag="", push="true", ref="main"):
    return _run(repo, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF_TYPE="branch", GITHUB_REF_NAME=ref, INPUT_TAG=tag, INPUT_PUSH=push)


def _glob_to_regex(pattern: str) -> re.Pattern:
    # GitHub tag filters: '[0-9]+' repeats the class, '.' is literal, the whole ref name must match.
    return re.compile(pattern.replace(".", r"\."))


def test_only_release_tags_trigger_the_workflow() -> None:
    patterns = [_glob_to_regex(p) for p in _workflow()[True]["push"]["tags"]]
    matches = lambda name: any(p.fullmatch(name) for p in patterns)

    assert matches("1.5.0") and matches("v1.5.0") and matches("10.20.30")
    for scratch in ("ci-test", "1.5.0rc1", "1.5.0-beta", "v1.5", "latest", "1.5.0.1", "release-1.5.0"):
        assert not matches(scratch), scratch


def test_release_tag_push_publishes_and_moves_latest_only_for_the_highest(repo) -> None:
    code, out, _ = _run(repo, GITHUB_EVENT_NAME="push", GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="2.0.0")
    assert code == 0 and out["push"] == "true" and out["tags"] == "testzeus/hercules:2.0.0,testzeus/hercules:latest"

    code, out, log = _tag_push(repo, "1.4.2")
    assert code == 0 and out["push"] == "true" and out["tags"] == "testzeus/hercules:1.4.2" and "Not moving 'latest'" in log


@pytest.mark.parametrize("scratch", ["ci-test", "1.5.0rc1", "legacy-build"])
def test_non_release_tag_push_never_pushes_even_if_the_trigger_filter_changes(repo, scratch) -> None:
    code, out, _ = _tag_push(repo, scratch)

    assert code == 0 and out["push"] == "false"


@pytest.mark.parametrize("tag", ["1.4.2", "v1.4.2", "9.9.9", "legacy-build", "2.0.0"])
def test_manual_push_cannot_use_a_release_version_or_an_existing_tag(repo, tag) -> None:
    code, out, log = _manual(repo, tag=tag)

    assert code != 0 and "push" not in out, log


def test_manual_push_with_a_fresh_tag_is_allowed_and_never_moves_latest(repo) -> None:
    code, out, _ = _manual(repo, tag="edge")
    assert code == 0 and out["push"] == "true" and out["tags"] == "testzeus/hercules:edge"

    code, out, _ = _manual(repo, tag="")  # defaults to the ref name
    assert code == 0 and out["push"] == "true" and out["tags"] == "testzeus/hercules:main"


def test_manual_push_cannot_use_latest(repo) -> None:
    assert _manual(repo, tag="latest")[0] != 0


@pytest.mark.parametrize("tag", ["1.4.2", "legacy-build", "anything"])
def test_manual_build_without_push_is_free(repo, tag) -> None:
    code, out, _ = _manual(repo, tag=tag, push="false")

    assert code == 0 and out["push"] == "false"


def test_manual_push_from_a_feature_branch_only_builds(repo) -> None:
    code, out, _ = _manual(repo, tag="edge", ref="feature/x")

    assert code == 0 and out["push"] == "false"


def test_missing_docker_hub_credentials_downgrade_to_a_build(repo) -> None:
    code, out, _ = _run(repo, GITHUB_EVENT_NAME="push", GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="2.0.0", DOCKERHUB_TOKEN="")

    assert code == 0 and out["push"] == "false"


def test_image_tags_wait_for_a_successful_release_workflow() -> None:
    merge = _workflow()["jobs"]["merge"]
    gate = next(step for step in merge["steps"] if step.get("name") == "Wait for the release workflow")

    assert "release.yml" in gate["run"] and "conclusion" in gate["run"]
    assert gate["if"] == "github.event_name == 'push'"
    assert merge["steps"].index(gate) < next(i for i, step in enumerate(merge["steps"]) if "imagetools create" in step.get("run", ""))
    assert merge["permissions"]["actions"] == "read"


def _run_gate(tmp_path: pathlib.Path, answers: list[str]) -> subprocess.CompletedProcess:
    """Run the gate step with a fake `gh` that prints the next answer ("<status> <conclusion>") per call, and a no-op sleep."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "answers").write_text("\n".join(answers) + "\n")
    (bin_dir / "gh").write_text('#!/bin/bash\nf="$(dirname "$0")/../answers"\nhead -n1 "$f"\nif [ "$(wc -l < "$f")" -gt 1 ]; then tail -n +2 "$f" > "$f.next"; mv "$f.next" "$f"; fi\n')
    (bin_dir / "sleep").write_text("#!/bin/bash\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    gate = next(step for step in _workflow()["jobs"]["merge"]["steps"] if step.get("name") == "Wait for the release workflow")
    return subprocess.run(["bash", "-e", "-c", gate["run"]], env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "GITHUB_SHA": "abc123"}, capture_output=True, text=True)


def test_gate_passes_once_the_release_succeeded_after_waiting(tmp_path) -> None:
    result = _run_gate(tmp_path, ["none none", "queued none", "in_progress none", "completed success"])

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_gate_blocks_the_image_tags_when_the_release_failed(tmp_path, conclusion) -> None:
    result = _run_gate(tmp_path, ["in_progress none", f"completed {conclusion}"])

    assert result.returncode != 0 and "not publishing" in result.stdout


def test_gate_gives_up_after_the_bounded_wait(tmp_path) -> None:
    result = _run_gate(tmp_path, ["in_progress none"])

    assert result.returncode != 0 and "within 30 minutes" in result.stdout
