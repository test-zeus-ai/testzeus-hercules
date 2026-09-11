"""Requirement coverage computation and filtering for :class:`TestPlan` objects."""

from __future__ import annotations

from typing import Any

from testzeus_hercules.intelligence.test_plan import TestPlan


def _coerce_test_ids(raw: Any) -> list[str]:
    """Coerce a requirement's ``test_ids`` entry into a list of id strings.

    Accepts a list/tuple of ids or a single whitespace-separated string
    (``"TC001 TC002"``); anything else yields no ids.
    """
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, (list, tuple)):
        return [str(test_id) for test_id in raw]
    return []


def coverage_matrix(plan: TestPlan) -> dict[str, Any]:
    """Compute per-requirement coverage for a test plan.

    Returns ``{"requirements": [...], "totals": {...}}`` where each requirement
    entry carries ``id``, ``description``, ``test_ids`` and a boolean
    ``covered`` flag, and totals carry ``requirements_total``,
    ``requirements_covered`` and ``coverage_percent`` (0-100, rounded, 0 when
    there are no requirements).

    Test case ids are matched case-insensitively (``tc001`` counts as
    ``TC001``); reported ids are normalized to the canonical case id. Only ids
    that resolve to actual test cases count towards coverage — dangling
    references in malformed plans must not over-report.
    """
    canonical_id_by_casefold = {case.id.casefold(): case.id for case in plan.test_cases}
    rows: list[dict[str, Any]] = []
    for requirement in plan.requirements:
        # Only ids that resolve to actual test cases count towards coverage —
        # dangling references in malformed plans must not over-report.
        test_ids = []
        for test_id in _coerce_test_ids(requirement.get("test_ids")):
            canonical = canonical_id_by_casefold.get(test_id.casefold())
            if canonical is not None and canonical not in test_ids:
                test_ids.append(canonical)
        rows.append(
            {
                "id": str(requirement.get("id") or ""),
                "description": str(requirement.get("description") or ""),
                "test_ids": test_ids,
                "covered": bool(test_ids),
            }
        )

    total = len(rows)
    covered = sum(1 for row in rows if row["covered"])
    percent = round((covered / total) * 100, 2) if total else 0
    return {
        "requirements": rows,
        "totals": {
            "requirements_total": total,
            "requirements_covered": covered,
            "coverage_percent": percent,
        },
    }


def _escape_markdown_cell(text: str) -> str:
    """Escape pipes so a cell cannot break the Markdown table layout."""
    return text.replace("|", "\\|")


def format_coverage_markdown(plan: TestPlan) -> str:
    """Render the coverage matrix as a small Markdown report."""
    matrix = coverage_matrix(plan)
    totals = matrix["totals"]

    lines = [
        "## Coverage",
        "",
        "| Requirement | Description | Test Cases | Covered |",
        "| --- | --- | --- | --- |",
    ]
    for row in matrix["requirements"]:
        test_ids = ", ".join(row["test_ids"]) if row["test_ids"] else "-"
        covered = "Yes" if row["covered"] else "No"
        lines.append(f"| {_escape_markdown_cell(row['id'])} | {_escape_markdown_cell(row['description'])} | {test_ids} | {covered} |")

    lines.append("")
    percent = f"{totals['coverage_percent']:g}"
    lines.append(f"**{percent}% of requirements covered ({totals['requirements_covered']}/{totals['requirements_total']})**")
    return "\n".join(lines) + "\n"


def filter_plan_by_test_ids(plan: TestPlan, test_ids: list[str]) -> TestPlan:
    """Return a subset copy of ``plan`` restricted to ``test_ids``.

    Keeps only the test cases whose id is listed, and only the requirements
    whose (filtered) ``test_ids`` mapping is non-empty.
    """
    wanted = {str(test_id) for test_id in test_ids}

    kept_cases = [case.model_copy(deep=True) for case in plan.test_cases if case.id in wanted]

    kept_requirements: list[dict[str, Any]] = []
    for requirement in plan.requirements:
        kept_ids = [str(test_id) for test_id in (requirement.get("test_ids") or []) if str(test_id) in wanted]
        if not kept_ids:
            continue
        entry = dict(requirement)
        entry["test_ids"] = kept_ids
        kept_requirements.append(entry)

    return TestPlan(meta=dict(plan.meta), requirements=kept_requirements, test_cases=kept_cases)
