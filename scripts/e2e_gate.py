#!/usr/bin/env python3
"""Judge an E2E results tree by exit code, for CI gates.

The runner itself has historically reported exit 0 regardless of red/green, so
a CI job wired straight to it is permanently green. This gate scans a results
directory for JUnit XML (``<testsuite>``/``<testcase>``) and decides:

- any ``<failure>`` or ``<error>`` fails the gate;
- any ``<skipped>`` fails the gate (E2E forbids skip);
- **zero** scanned cases fails the gate (a crashed first suite or a
  mis-assembled selection also produces "zero failures");
- ``known_failures.txt`` waivers are enforced two-way: a listed case failing is
  waived (counted separately), a listed case **passing** fails the gate -- that
  is the only thing that keeps a waiver list from rotting;
- ``blocked.json`` written by the suite assembler is read and its excluded
  counts/kinds are reported in the summary.

Outputs ``e2e-summary.json`` (schema ``case-library-e2e-summary/v1``) and a
merged ``e2e-junit.xml`` next to the results directory whenever a verdict is
reached. Stdlib only.

Exit codes: 0 pass, 1 failure (incl. zero cases), 2 could not judge
(missing results directory, corrupt XML, unreadable blocked.json).
"""

import argparse
import copy
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

LOGGER = logging.getLogger(__name__)

DEFAULT_RUN_ROOT = "opt/.run"
DEFAULT_KNOWN_FAILURES = "opt/cases/known_failures.txt"
SUMMARY_SCHEMA = "case-library-e2e-summary/v1"
SUMMARY_FILENAME = "e2e-summary.json"
MERGED_JUNIT_NAME = "e2e-junit.xml"
BLOCKED_FILENAME = "blocked.json"

FAIL_KINDS = ("failure", "error", "skipped")


class GateCannotJudge(RuntimeError):
    """The gate cannot reach a verdict; exit code 2."""


_WAIVER_BOUNDARY = r"(?![A-Za-z0-9-])"


def _waiver_matches(combined: str, pattern: str) -> bool:
    """Substring match that must end on a token boundary.

    The character right after the match may not be a letter, digit, or hyphen: an
    exact case id matches, a class prefix followed by the ``::`` separator matches,
    but a short waiver like ``AUTH`` no longer greens an unrelated ``AUTH-OTHER-99``.
    """
    return re.search(re.escape(pattern) + _WAIVER_BOUNDARY, combined) is not None


@dataclass(frozen=True)
class GateCase:
    name: str
    classname: str
    source: str
    kind: str  # "passed" | "failure" | "error" | "skipped"
    message: str | None
    waived_by: str | None = None


@dataclass
class GateReport:
    results_dir: Path
    scanned_files: int
    cases: list[GateCase] = field(default_factory=list)
    unwaived_failures: list[GateCase] = field(default_factory=list)
    waived: list[GateCase] = field(default_factory=list)
    stale_waivers: list[tuple[str, GateCase]] = field(default_factory=list)
    blocked_report: dict[str, object] = field(default_factory=dict)
    waiver_file: str | None = None
    waiver_patterns: list[str] = field(default_factory=list)
    parsed_roots: list[tuple[Path, ET.Element]] = field(default_factory=list)
    zero_cases_reason: str | None = None

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.kind == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for case in self.cases if case.kind == "failure")

    @property
    def errors(self) -> int:
        return sum(1 for case in self.cases if case.kind == "error")

    @property
    def skipped(self) -> int:
        return sum(1 for case in self.cases if case.kind == "skipped")

    @property
    def verdict(self) -> str:
        return "fail" if self.unwaived_failures or self.stale_waivers or self.zero_cases_reason else "pass"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge an E2E junit results tree; the CI exit-code gate.")
    parser.add_argument("--results-dir", default=None, help="directory containing junit XML results")
    parser.add_argument("--version", default=None, help="version target; results dir becomes <run-root>/<version>/results")
    parser.add_argument("--run-root", default=DEFAULT_RUN_ROOT, help=f"assembly output root (default: {DEFAULT_RUN_ROOT})")
    parser.add_argument(
        "--known-failures",
        default=DEFAULT_KNOWN_FAILURES,
        help=f"waiver list; one substring pattern per line, matched up to a token boundary (default: {DEFAULT_KNOWN_FAILURES})",
    )
    parser.add_argument("--blocked", default=None, help=f"path to blocked.json (default: sibling {BLOCKED_FILENAME} of the results dir)")
    parser.add_argument("--summary-out", default=None, help=f"summary path (default: <results-dir>/../{SUMMARY_FILENAME})")
    parser.add_argument("--junit-out", default=None, help=f"merged junit path (default: <results-dir>/../{MERGED_JUNIT_NAME})")
    args = parser.parse_args(argv)
    if not args.results_dir and not args.version:
        parser.error("either --results-dir or --version is required")
    return args


def load_waiver_patterns(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    patterns: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def load_blocked_report(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {"available": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateCannotJudge(f"unreadable {BLOCKED_FILENAME} at {path}: {exc}") from exc
    excluded = data.get("excluded", []) if isinstance(data, dict) else []
    by_category = Counter(str(entry.get("category", "unknown")) for entry in excluded if isinstance(entry, dict))
    return {
        "available": True,
        "schema": data.get("schema") if isinstance(data, dict) else None,
        "total": len(excluded),
        "by_category": dict(sorted(by_category.items())),
    }


def _classify_testcase(element: ET.Element, path: Path, patterns: Sequence[str]) -> GateCase:
    name = element.get("name", "")
    classname = element.get("classname", "")
    kind = "passed"
    message: str | None = None
    for child_kind, tag in (("failure", "failure"), ("error", "error"), ("skipped", "skipped")):
        child = element.find(tag)
        if child is not None:
            kind = child_kind
            message = (child.get("message") or child.get("type") or (child.text or "")).strip() or None
            break
    combined = f"{classname}::{name}"
    waived_by = next((pattern for pattern in patterns if _waiver_matches(combined, pattern)), None)
    return GateCase(name=name, classname=classname, source=str(path), kind=kind, message=message, waived_by=waived_by)


def judge(results_dir: Path, waiver_patterns: Sequence[str], blocked_path: Path | None) -> GateReport:
    if not results_dir.is_dir():
        raise GateCannotJudge(f"results directory not found: {results_dir}")
    report = GateReport(results_dir=results_dir, scanned_files=0, waiver_patterns=list(waiver_patterns))
    for path in sorted(results_dir.rglob("*.xml")):
        if path.name == MERGED_JUNIT_NAME:
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise GateCannotJudge(f"corrupt XML in {path}: {exc}") from exc
        report.scanned_files += 1
        report.parsed_roots.append((path, root))
        for element in root.iter("testcase"):
            report.cases.append(_classify_testcase(element, path, waiver_patterns))

    if not report.cases:
        report.zero_cases_reason = (
            f"no <testcase> entries found in {report.scanned_files} XML file(s) under {results_dir}; " "a crashed suite or a mis-assembled selection also looks like zero failures"
        )
    report.unwaived_failures = [case for case in report.cases if case.kind in FAIL_KINDS and case.waived_by is None]
    report.waived = [case for case in report.cases if case.kind in FAIL_KINDS and case.waived_by is not None]
    report.stale_waivers = [(case.waived_by or "", case) for case in report.cases if case.kind == "passed" and case.waived_by is not None]
    report.blocked_report = load_blocked_report(blocked_path)
    return report


def build_summary(report: GateReport, waiver_file: str | None) -> dict[str, object]:
    return {
        "schema": SUMMARY_SCHEMA,
        "results_dir": str(report.results_dir),
        "scanned_files": report.scanned_files,
        "total": len(report.cases),
        "passed": report.passed,
        "failed": report.failed,
        "errors": report.errors,
        "skipped": report.skipped,
        "waived": len(report.waived),
        "stale_waivers": [{"pattern": pattern, "name": case.name, "classname": case.classname} for pattern, case in report.stale_waivers],
        "failures": [{"name": case.name, "classname": case.classname, "kind": case.kind, "message": case.message, "source": case.source} for case in report.unwaived_failures],
        "blocked_excluded": report.blocked_report,
        "known_failures_file": waiver_file,
        "zero_cases_reason": report.zero_cases_reason,
        "verdict": report.verdict,
    }


def write_merged_junit(report: GateReport, out_path: Path) -> None:
    suites_el = ET.Element("testsuites")
    suite_el = ET.SubElement(
        suites_el,
        "testsuite",
        {
            "name": "e2e-gate-merged",
            "tests": str(len(report.cases)),
            "failures": str(report.failed),
            "errors": str(report.errors),
            "skipped": str(report.skipped),
        },
    )
    props = ET.SubElement(suite_el, "properties")
    for prop_name, prop_value in (
        ("gate.verdict", report.verdict),
        ("gate.waived", str(len(report.waived))),
        ("gate.stale_waivers", str(len(report.stale_waivers))),
        ("blocked.excluded", str(report.blocked_report.get("total", 0)) if report.blocked_report.get("available") else "unknown"),
    ):
        ET.SubElement(props, "property", {"name": prop_name, "value": prop_value})
    for _, root in report.parsed_roots:
        for element in root.iter("testcase"):
            cloned = copy.deepcopy(element)
            case = _classify_testcase(element, Path("."), report.waiver_patterns)
            if case.waived_by is not None:
                case_props = ET.Element("properties")
                ET.SubElement(case_props, "property", {"name": "waived-by-known-failure", "value": case.waived_by})
                cloned.insert(0, case_props)
            suite_el.append(cloned)
    ET.indent(suites_el, space="  ")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suites_el).write(out_path, encoding="utf-8", xml_declaration=True)


def _print_report(report: GateReport) -> None:
    print(f"e2e-gate: results dir: {report.results_dir}")
    print(f"e2e-gate: scanned {report.scanned_files} XML file(s), {len(report.cases)} case(s): " f"{report.passed} passed, {report.failed} failed, {report.errors} error(s), {report.skipped} skipped")
    print(f"e2e-gate: waived failure(s): {len(report.waived)}; stale waiver(s): {len(report.stale_waivers)}")
    if report.zero_cases_reason:
        print(f"e2e-gate: {report.zero_cases_reason}")
    blocked = report.blocked_report
    if blocked.get("available"):
        print(f"e2e-gate: blocked/excluded cases reported: {blocked.get('total', 0)} (by category: {blocked.get('by_category', {})})")
    else:
        print("e2e-gate: no blocked.json found next to the results dir; excluded-case counts unknown")
    for case in report.unwaived_failures:
        print(f"e2e-gate: FAILURE {case.classname}::{case.name} [{case.kind}] {case.message or ''}")
    for pattern, case in report.stale_waivers:
        print(f"e2e-gate: STALE WAIVER {pattern!r} matches passing case {case.classname}::{case.name}; delete the waiver")
    print(f"e2e-gate: VERDICT: {report.verdict.upper()}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    results_dir = Path(args.results_dir) if args.results_dir else Path(args.run_root) / args.version / "results"
    blocked_path = Path(args.blocked) if args.blocked else results_dir.parent / BLOCKED_FILENAME
    waiver_path = Path(args.known_failures) if args.known_failures else None

    try:
        report = judge(results_dir, load_waiver_patterns(waiver_path), blocked_path)
    except GateCannotJudge as exc:
        print(f"e2e-gate: could not judge: {exc}", file=sys.stderr)
        return 2

    summary_path = Path(args.summary_out) if args.summary_out else results_dir.parent / SUMMARY_FILENAME
    junit_path = Path(args.junit_out) if args.junit_out else results_dir.parent / MERGED_JUNIT_NAME
    summary = build_summary(report, str(waiver_path) if waiver_path and waiver_path.is_file() else None)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_merged_junit(report, junit_path)

    _print_report(report)
    print(f"e2e-gate: summary: {summary_path}")
    print(f"e2e-gate: merged junit: {junit_path}")
    return 0 if report.verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
