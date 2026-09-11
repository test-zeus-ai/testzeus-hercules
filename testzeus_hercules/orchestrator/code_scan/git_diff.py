"""Git diff analysis helpers for targeting tests at changed code.

All functions are offline and heuristic: they shell out to the local
``git`` binary and map changed files to suggested test targets.
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath
from typing import Any

from testzeus_hercules.utils.logger import logger

_GIT_TIMEOUT_SECONDS = 60
_MAX_STAT_LINES = 40

_TRACKED_STATUSES = frozenset({"A", "M", "D", "R"})

# Top-level directories that carry no feature meaning when deriving "area" tokens.
GENERIC_TOP_DIRS = frozenset({"src", "lib", "app", "test", "tests", "__tests__", "spec", "e2e", "packages", "source", "sources"})


def _run_git(project_path: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", project_path, *args], capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(args), returncode=1, stdout="", stderr=str(exc))


def _parse_name_status(stdout: str) -> list[dict[str, str]]:
    """Parse `git diff --name-status` output into {"path", "status"} entries."""
    entries: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0].upper()
        # For renames the last field is the new path (R100\told\tnew).
        path = parts[-1]
        entries.append({"path": path, "status": status})
    return entries


def _parse_porcelain(stdout: str) -> tuple[list[dict[str, str]], int]:
    """Parse `git status --porcelain` into entries plus an untracked count."""
    entries: list[dict[str, str]] = []
    untracked = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        xy, _, rest = line.partition(" ")
        if len(xy) < 2:
            continue
        path = rest
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if xy == "??":
            untracked += 1
            entries.append({"path": path, "status": "A"})
            continue
        code = (xy[0] if xy[0] != " " else xy[1]).upper()
        if code in _TRACKED_STATUSES:
            entries.append({"path": path, "status": code})
    return entries, untracked


def get_diff_summary(project_path: str, base_ref: str = "HEAD~1", include_uncommitted: bool = True) -> dict:
    """Summarize git changes against ``base_ref`` plus optional uncommitted work.

    Raises ValueError when ``project_path`` is not inside a git work tree.
    A nonexistent ``base_ref`` degrades gracefully: a warning is recorded
    and only uncommitted changes are reported.
    """
    check = _run_git(project_path, "rev-parse", "--is-inside-work-tree")
    if check.returncode != 0 or check.stdout.strip() != "true":
        raise ValueError(f"Not a git repository: {project_path}")

    changed_files: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    warnings: list[str] = []
    untracked = 0

    def extend(entries: list[dict[str, str]]) -> None:
        for entry in entries:
            key = (entry["path"], entry["status"])
            if key not in seen:
                seen.add(key)
                changed_files.append(entry)

    name_status = _run_git(project_path, "diff", "--name-status", base_ref)
    base_valid = name_status.returncode == 0
    if base_valid:
        extend(_parse_name_status(name_status.stdout))
    else:
        warnings.append(f"Could not diff against base_ref '{base_ref}'; falling back to uncommitted changes only.")

    if include_uncommitted:
        status = _run_git(project_path, "status", "--porcelain")
        if status.returncode == 0:
            entries, untracked = _parse_porcelain(status.stdout)
            extend(entries)
        else:
            warnings.append(f"git status failed: {status.stderr.strip()}")

    counts = {"added": 0, "modified": 0, "deleted": 0, "renamed": 0, "untracked": untracked}
    for entry in changed_files:
        if entry["status"] == "A":
            counts["added"] += 1
        elif entry["status"] == "M":
            counts["modified"] += 1
        elif entry["status"] == "D":
            counts["deleted"] += 1
        elif entry["status"] == "R":
            counts["renamed"] += 1

    diff_stat = ""
    if base_valid:
        stat = _run_git(project_path, "diff", "--stat", base_ref)
        if stat.returncode == 0:
            diff_stat = "\n".join(stat.stdout.splitlines()[-_MAX_STAT_LINES:])
        else:
            warnings.append(f"git diff --stat failed: {stat.stderr.strip()}")

    return {
        "project_path": project_path,
        "base_ref": base_ref,
        "changed_files": changed_files,
        "counts": counts,
        "diff_stat": diff_stat,
        "warnings": warnings,
    }


def _changed_file_tokens(path: str) -> tuple[str, set[str]]:
    """Derive an "area" token plus matching tokens for a changed file path.

    ``auth/login.py`` -> area "auth"; generic top dirs (src, tests, ...)
    are skipped, and files without a directory fall back to their stem.
    """
    norm = path.replace("\\", "/").strip("/")
    parts = [p for p in norm.split("/") if p and p != "."]
    stem = PurePosixPath(norm).stem.lower()
    parents = [p for p in parts[:-1]]
    area: str | None = next((p.lower() for p in parents if p.lower() not in GENERIC_TOP_DIRS), None)
    if area is None:
        area = parents[0].lower() if parents else stem
    match_tokens = {area, stem, norm.lower()}
    return area, match_tokens


def suggest_test_targets(diff_summary: dict, plan_test_cases: list[dict] | None = None) -> list[str]:
    """Map changed files to suggested test targets.

    Without ``plan_test_cases`` returns the sorted, unique area tokens for
    the changed files. With it, returns the ids of cases whose
    tags/description/title mention any changed area or path stem.
    """
    areas: set[str] = set()
    match_tokens: set[str] = set()
    for entry in diff_summary.get("changed_files", []):
        area, tokens = _changed_file_tokens(str(entry.get("path", "")))
        areas.add(area)
        match_tokens.update(tokens)

    if not plan_test_cases:
        return sorted(areas)

    matched: list[str] = []
    seen_ids: set[str] = set()
    for case in plan_test_cases:
        case_id = str(case.get("id") or case.get("case_id") or "")
        if not case_id or case_id in seen_ids:
            continue
        tags = case.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        haystack = " ".join([*map(str, tags), str(case.get("description") or ""), str(case.get("title") or "")]).lower()
        if any(token in haystack for token in match_tokens):
            seen_ids.add(case_id)
            matched.append(case_id)
    return matched
