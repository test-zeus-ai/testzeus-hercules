"""Offline tests for .github/scripts/backmerge-main-to-dev.sh.

Each test builds a bare "origin" plus a working clone in tmp_path, puts a fake
``gh`` on PATH that records its arguments, and runs the script from the clone.
No network access and no real GitHub calls.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "backmerge-main-to-dev.sh"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _commit(cwd: Path, name: str, content: str, message: str) -> None:
    (cwd / name).write_text(content, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _commit(work, "shared.txt", "base\n", "base")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "push", "-q", "origin", "main:dev")
    return work, origin


@pytest.fixture()
def fake_gh(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$GH_LOG"\n'
        'if [ "$1 $2" = "pr list" ]; then printf "%s" "${FAKE_EXISTING_PR:-}"; exit 0; fi\n'
        'if [ "$1 $2" = "pr create" ] && [ -n "${FAKE_PR_CREATE_FAILS:-}" ]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return bindir, log


def _run(work: Path, fake_gh, **extra_env: str) -> subprocess.CompletedProcess:
    bindir, log = fake_gh
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GITHUB_REPOSITORY": "o/r",
        "GIT_AUTHOR_NAME": "bot",
        "GIT_AUTHOR_EMAIL": "bot@e",
        "GIT_COMMITTER_NAME": "bot",
        "GIT_COMMITTER_EMAIL": "bot@e",
        **extra_env,
    }
    return subprocess.run(["bash", str(SCRIPT)], cwd=work, env=env, capture_output=True, text=True)


def _gh_calls(fake_gh) -> list[str]:
    log = fake_gh[1]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _remote_branch(work: Path, branch: str) -> str:
    out = _git(work, "ls-remote", "--heads", "origin", branch)
    return out.split()[0] if out else ""


def test_noop_when_dev_already_contains_main(repo, fake_gh) -> None:
    work, _ = repo
    before = _remote_branch(work, "dev")
    result = _run(work, fake_gh)
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout
    assert _remote_branch(work, "dev") == before
    assert _gh_calls(fake_gh) == []


def test_clean_merge_is_pushed_to_dev(repo, fake_gh) -> None:
    work, _ = repo
    _git(work, "checkout", "-q", "-b", "devwork", "origin/dev")
    _commit(work, "dev_only.txt", "dev\n", "dev change")
    _git(work, "push", "-q", "origin", "devwork:dev")
    _git(work, "checkout", "-q", "main")
    _commit(work, "main_only.txt", "main\n", "main change")
    _git(work, "push", "-q", "origin", "main")

    result = _run(work, fake_gh)

    assert result.returncode == 0, result.stderr
    _git(work, "fetch", "-q", "origin")
    assert _git(work, "merge-base", "--is-ancestor", "origin/main", "origin/dev") == ""  # check=True would raise otherwise
    assert _git(work, "show", "origin/dev:dev_only.txt") == "dev"  # dev's own work is kept
    assert "backmerge main into dev" in _git(work, "log", "-1", "--format=%s", "origin/dev")
    assert _gh_calls(fake_gh) == []


def _make_conflict(work: Path) -> None:
    _git(work, "checkout", "-q", "-b", "devwork", "origin/dev")
    _commit(work, "shared.txt", "dev version\n", "dev edit")
    _git(work, "push", "-q", "origin", "devwork:dev")
    _git(work, "checkout", "-q", "main")
    _commit(work, "shared.txt", "main version\n", "main edit")
    _git(work, "push", "-q", "origin", "main")


def test_conflict_opens_pr_and_leaves_dev_untouched(repo, fake_gh) -> None:
    work, _ = repo
    _make_conflict(work)
    dev_before = _remote_branch(work, "dev")

    result = _run(work, fake_gh)

    assert result.returncode == 0, result.stderr
    assert _remote_branch(work, "dev") == dev_before
    assert _remote_branch(work, "backmerge/main-into-dev") == _remote_branch(work, "main")
    calls = _gh_calls(fake_gh)
    create = [c for c in calls if c.startswith("pr create")]
    assert len(create) == 1
    assert "--base dev" in create[0] and "--head backmerge/main-into-dev" in create[0]
    assert "--title Backmerge main to dev" in create[0]
    assert "- `shared.txt`" in fake_gh[1].read_text(encoding="utf-8")  # the multi-line body lists the conflicted file
    assert not _git(work, "status", "--porcelain")  # merge aborted, tree clean


def test_conflict_with_open_pr_comments_without_rewriting_branch(repo, fake_gh) -> None:
    work, _ = repo
    _make_conflict(work)
    # A human is already resolving on the back-merge branch: it must not be force-pushed.
    _git(work, "push", "-q", "origin", "origin/dev:refs/heads/backmerge/main-into-dev")
    branch_before = _remote_branch(work, "backmerge/main-into-dev")

    result = _run(work, fake_gh, FAKE_EXISTING_PR="42")

    assert result.returncode == 0, result.stderr
    assert _remote_branch(work, "backmerge/main-into-dev") == branch_before
    calls = _gh_calls(fake_gh)
    assert any(c.startswith("pr comment 42") for c in calls)
    assert not any(c.startswith("pr create") for c in calls)


def test_conflict_when_pr_creation_is_not_allowed_opens_issue_and_fails(repo, fake_gh) -> None:
    work, _ = repo
    _make_conflict(work)

    result = _run(work, fake_gh, FAKE_PR_CREATE_FAILS="1")

    assert result.returncode == 1
    assert "::error::" in result.stdout and "compare/dev...backmerge/main-into-dev" in result.stdout
    assert any(c.startswith("issue create") for c in _gh_calls(fake_gh))


def test_missing_dev_branch_is_a_noop(repo, fake_gh) -> None:
    work, _ = repo
    _git(work, "push", "-q", "origin", "--delete", "dev")
    result = _run(work, fake_gh)
    assert result.returncode == 0, result.stderr
    assert "does not exist" in result.stdout
    assert _gh_calls(fake_gh) == []
