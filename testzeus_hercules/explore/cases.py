"""Where a finding's case lives, by the decision a person made about it.

A reproduced finding comes with a case that replays it. Not every case is worth keeping, so each one carries a
decision (``Finding.case``) and lives where that decision says:

- ``proposed``: ``.hercules/explore/cases/<id>.json``, outside the project's cases: nothing runs it until a person
  looks at it.
- ``kept``: ``.hercules/cases/explore-<id>.json`` with ``status: known_bug``: it is part of the project's cases and
  runs report it, but the bug is still open, so it never fails a run.
- ``regression``: the same file with ``status: ready`` and a ``regression`` tag: the bug was fixed, and the case fails
  a run if it comes back.
- ``rejected``: ``.hercules/explore/cases/rejected/<id>.json``: kept for the record (a person may reconsider it), never
  run, and never proposed again while the finding stays rejected.

:func:`sync_case` moves the file to match the decision. It is idempotent and never loses a case: it reads the copy
already at the decision's place first (a person may have edited a kept case), else any other copy, writes the target,
then removes the other copies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from testzeus_hercules.explore.models import Finding

__all__ = ["CASE_DECISIONS", "CaseDecision", "case_location", "sync_case"]

CaseDecision = Literal["proposed", "kept", "regression", "rejected"]
CASE_DECISIONS: tuple[str, ...] = ("proposed", "kept", "regression", "rejected")
STAGING_DIR = ".hercules/explore/cases"
REJECTED_DIR = ".hercules/explore/cases/rejected"
SUITE_DIR = ".hercules/cases"
REGRESSION_TAG = "regression"


def case_location(finding_id: str, decision: str) -> str:
    """The project-relative path of a case with this decision."""
    if decision in ("kept", "regression"):
        return f"{SUITE_DIR}/explore-{finding_id}.json"
    if decision == "rejected":
        return f"{REJECTED_DIR}/{finding_id}.json"
    return f"{STAGING_DIR}/{finding_id}.json"


def sync_case(project_dir: Path, finding: Finding) -> str | None:
    """Put the finding's case where its decision says; returns its project-relative path, or None when no copy exists."""
    filed = _filed(project_dir, finding.id)
    target = case_location(finding.id, finding.case)
    if finding.case in ("kept", "regression"):
        target = filed or target
    copies = [target, *([filed] if filed else []), *(case_location(finding.id, decision) for decision in CASE_DECISIONS), *([finding.case_path] if finding.case_path else [])]
    places = [project_dir / path for path in dict.fromkeys(copies)]
    source = next((path for path in places if _inside(project_dir, path) and path.is_file()), None)
    if source is None:
        return None
    try:
        case: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tags = [tag for tag in case.get("tags") or [] if tag != REGRESSION_TAG]
    if finding.case == "regression":
        case["status"] = "ready"
        tags.append(REGRESSION_TAG)
    else:
        case["status"] = "known_bug"
    case["tags"] = tags
    destination = project_dir / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
    for place in places:
        if place != destination and _inside(project_dir, place) and place.is_file():
            place.unlink()
            _prune_empty(place, stop=project_dir / ".hercules")
    return target


def _filed(project_dir: Path, finding_id: str) -> str | None:
    suite = project_dir / SUITE_DIR
    if not suite.is_dir():
        return None
    found = sorted(path for path in suite.rglob(f"explore-{finding_id}.json") if path.is_file() and _inside(project_dir, path))
    return found[0].relative_to(project_dir).as_posix() if found else None


def _prune_empty(removed: Path, *, stop: Path) -> None:
    """Remove folders a moved case left empty (an earlier version's per-session folders), up to ``.hercules/cases``."""
    suite = stop / "cases"
    if removed.is_relative_to(suite) and (removed.name.startswith("explore-") or not removed.is_relative_to(suite / "explore")):
        return
    keep = {stop, suite, stop / "explore", stop / "explore" / "cases"}
    folder = removed.parent
    while folder not in keep and folder.is_relative_to(stop) and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _inside(project_dir: Path, path: Path) -> bool:
    """Only files under the project's ``.hercules`` folder are moved or removed (a case path read from the ledger is data)."""
    try:
        path.resolve().relative_to((project_dir / ".hercules").resolve())
    except ValueError:
        return False
    return not path.is_symlink()
