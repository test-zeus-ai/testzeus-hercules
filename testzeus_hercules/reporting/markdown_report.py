"""Markdown report generation from merged JUnit XML run results.

A Hercules run produces a merged JUnit XML at
``<PROJECT_SOURCE_ROOT>/output/<timestamp>/<feature>_result.xml`` (built by
``testzeus_hercules.utils.junit_helper``). This module parses that XML and
renders a human-readable ``Hercules_Test_Report.md`` next to it.
"""

import glob
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from testzeus_hercules.utils.logger import logger

REPORT_FILE_NAME = "Hercules_Test_Report.md"
NO_METRIC_LABEL = "n/a (provider did not report)"

# Case-level property names holding evidence artifact paths, mapped to report labels.
EVIDENCE_PROPERTIES: List[Tuple[str, str]] = [
    ("Screenshot folder", "Proofs Screenshot"),
    ("Video", "Proofs Video"),
    ("Network logs", "Network Logs"),
    ("Agent logs", "Agents Internal Logs"),
    ("Planner thoughts", "Planner Thoughts"),
]

STATUS_EMOJI: Dict[str, str] = {"passed": "✅", "failed": "❌", "error": "⚠️"}
# Failures (and errors) sort before passes in the results table.
_STATUS_SORT_RANK: Dict[str, int] = {"error": 0, "failed": 1, "passed": 2}

_FINAL_RESPONSE_RE = re.compile(r"Final Response:\s*(.+)", re.IGNORECASE)


def _to_int(raw: Optional[str], default: int = 0) -> int:
    """Best-effort integer conversion, returning ``default`` on failure."""
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_float(raw: Optional[str]) -> Optional[float]:
    """Best-effort float conversion, returning ``None`` on failure."""
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _safe_duration(raw: Optional[str]) -> float:
    """Duration in seconds, clamped to 0.0 unless a positive, finite number.

    Negative, NaN or infinite junit ``time`` attributes are junk data; rendering
    them verbatim would produce "- Duration: -5.00s", "nan" or "infs" in the report.
    """
    value = _to_float(raw)
    if value is None or not math.isfinite(value) or value <= 0:
        return 0.0
    return value


def _escape_cell(text: Any) -> str:
    """Make arbitrary text safe inside a markdown table cell."""
    return str(text).replace("|", "\\|").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _code_fence(content: str) -> str:
    """Pick a backtick fence that cannot be broken by the fenced content.

    One longer than the longest backtick run inside the content (minimum 3).
    """
    longest_run = max((len(run) for run in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest_run + 1)


def _format_cost(value: Optional[float]) -> str:
    """Format a cost value, or a placeholder when the provider did not report one."""
    if value is None or value == 0 or not math.isfinite(value):
        return NO_METRIC_LABEL
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _format_tokens(value: Optional[float]) -> str:
    """Format a token count, or a placeholder when the provider did not report one.

    Junk junit properties ("nan", "inf") are not convertible to int; they fall
    back to the missing-metric placeholder instead of aborting the report.
    """
    if value is None or value == 0 or not math.isfinite(value):
        return NO_METRIC_LABEL
    return str(int(value))


def _result_message(result_elem: ET.Element) -> str:
    """Combine the message attribute and body text of a ``<failure>``/``<error>`` element."""
    message = (result_elem.get("message") or "").strip()
    body = (result_elem.text or "").strip()
    if body and body != message:
        message = f"{message}\n\n{body}" if message else body
    return message


def _extract_case_cost(properties: Dict[str, str]) -> Optional[float]:
    """Extract the case cost from flattened cost properties ending in ``.total_cost``."""
    preferred = "usage_including_cached_inference.total_cost"
    candidate_keys: List[str] = []
    if preferred in properties:
        candidate_keys.append(preferred)
    candidate_keys.extend(key for key in properties if key.endswith(".total_cost") and key != preferred)
    if "total_cost" in properties:
        candidate_keys.append("total_cost")
    for key in candidate_keys:
        value = _to_float(properties.get(key))
        if value is not None:
            return value
    return None


def _parse_case(case_elem: ET.Element) -> Dict[str, Any]:
    """Parse a single ``<testcase>`` element into a report-friendly dict."""
    status = "passed"
    failure_message = ""
    failure_elem = case_elem.find("failure")
    error_elem = case_elem.find("error")
    if failure_elem is not None:
        status = "failed"
        failure_message = _result_message(failure_elem)
    elif error_elem is not None:
        status = "error"
        failure_message = _result_message(error_elem)

    properties: Dict[str, str] = {}
    for prop_elem in case_elem.findall("./properties/property"):
        prop_name = prop_elem.get("name", "")
        if prop_name:
            properties[prop_name] = prop_elem.get("value", "")

    system_out = "\n".join((elem.text or "") for elem in case_elem.findall("system-out"))
    final_response = ""
    match = _FINAL_RESPONSE_RE.search(system_out)
    if match:
        final_response = match.group(1).strip()

    return {
        "name": case_elem.get("name", ""),
        "feature": case_elem.get("classname", ""),
        "status": status,
        "time": _safe_duration(case_elem.get("time")),
        "failure_message": failure_message,
        "final_response": final_response,
        "properties": properties,
        "cost": _extract_case_cost(properties),
    }


def _merge_suite_property(existing: Dict[str, str], name: str, value: str) -> None:
    """Merge a suite property across merged suites, summing numeric values."""
    if name not in existing:
        existing[name] = value
        return
    old_value = _to_float(existing[name])
    new_value = _to_float(value)
    if old_value is not None and new_value is not None:
        existing[name] = str(old_value + new_value)
    else:
        existing[name] = value


def parse_run_results(junit_xml_path: str) -> Dict[str, Any]:
    """Parse a (merged) JUnit XML run result into a structured dict.

    Args:
        junit_xml_path (str): Path to the ``<feature>_result.xml`` file.

    Returns:
        Dict[str, Any]: Dict with ``run_id``, ``xml_path``, ``suite`` summary
        (name, tests, failures, errors, time, total_cost, total_tokens, timestamp,
        properties) and a ``cases`` list with per-scenario details.
    """
    root = ET.parse(junit_xml_path).getroot()

    suite: Dict[str, Any] = {
        "name": "",
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "time": 0.0,
        "timestamp": "",
        "properties": {},
        "total_cost": None,
        "total_tokens": None,
    }
    cases: List[Dict[str, Any]] = []

    for suite_elem in root.iter("testsuite"):
        if not suite["name"]:
            suite["name"] = suite_elem.get("name", "")
        if not suite["timestamp"]:
            suite["timestamp"] = suite_elem.get("timestamp", "")
        suite["tests"] += _to_int(suite_elem.get("tests"))
        suite["failures"] += _to_int(suite_elem.get("failures"))
        suite["errors"] += _to_int(suite_elem.get("errors"))
        suite["time"] += _safe_duration(suite_elem.get("time"))
        for prop_elem in suite_elem.findall("./properties/property"):
            prop_name = prop_elem.get("name", "")
            if prop_name:
                _merge_suite_property(suite["properties"], prop_name, prop_elem.get("value", ""))
        for case_elem in suite_elem.findall("testcase"):
            cases.append(_parse_case(case_elem))

    suite["total_cost"] = _to_float(suite["properties"].get("total_execution_cost"))
    suite["total_tokens"] = _to_float(suite["properties"].get("total_token_used"))

    return {
        "run_id": os.path.basename(os.path.dirname(os.path.abspath(junit_xml_path))),
        "xml_path": junit_xml_path,
        "suite": suite,
        "cases": cases,
    }


def _sorted_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort cases with errors first, then failures, then passes, each by name."""
    return sorted(cases, key=lambda case: (_STATUS_SORT_RANK.get(case["status"], 3), case["name"]))


def _evidence_rows(properties: Dict[str, str]) -> List[Tuple[str, str]]:
    """Collect (label, path) evidence rows that actually have a value."""
    rows: List[Tuple[str, str]] = []
    for label, prop_name in EVIDENCE_PROPERTIES:
        value = (properties.get(prop_name) or "").strip()
        if value:
            rows.append((label, value))
    return rows


def generate_markdown_report(junit_xml_path: str, output_path: Optional[str] = None, run_meta: Optional[Dict[str, Any]] = None) -> str:
    """Generate a markdown report from a merged JUnit XML run result.

    Args:
        junit_xml_path (str): Path to the ``<feature>_result.xml`` file.
        output_path (Optional[str]): Where to write the report. Defaults to
            ``Hercules_Test_Report.md`` next to the XML file.
        run_meta (Optional[Dict[str, Any]]): Extra run metadata (e.g. feature file,
            mode) rendered as key/value lines in the report header.

    Returns:
        str: The generated markdown report text.
    """
    data = parse_run_results(junit_xml_path)
    suite = data["suite"]
    cases: List[Dict[str, Any]] = data["cases"]

    passed = max(suite["tests"] - suite["failures"] - suite["errors"], 0)
    if suite["errors"]:
        overall_status = "ERRORS ⚠"
    elif suite["failures"]:
        overall_status = "FAILED ❌"
    else:
        overall_status = "PASSED ✅"

    lines: List[str] = []
    lines.append("# Hercules Test Report")
    lines.append("")
    lines.append(f"- Run ID: {_escape_cell(data['run_id'])}")
    if suite["timestamp"]:
        lines.append(f"- Timestamp: {_escape_cell(suite['timestamp'])}")
    for key, value in (run_meta or {}).items():
        lines.append(f"- {_escape_cell(key)}: {_escape_cell(value)}")

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"Overall status: {overall_status}")
    lines.append("")
    lines.append(f"- Total tests: {suite['tests']}")
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {suite['failures']}")
    lines.append(f"- Errors: {suite['errors']}")
    lines.append(f"- Duration: {suite['time']:.2f}s")
    lines.append(f"- Total cost: {_format_cost(suite['total_cost'])}")
    lines.append(f"- Total tokens: {_format_tokens(suite['total_tokens'])}")

    lines.append("")
    lines.append("## Results")
    lines.append("")
    if cases:
        lines.append("| Status | Scenario | Feature | Duration (s) | Cost |")
        lines.append("| --- | --- | --- | --- | --- |")
        for case in _sorted_cases(cases):
            lines.append(
                f"| {STATUS_EMOJI.get(case['status'], case['status'])} | {_escape_cell(case['name'])} | {_escape_cell(case['feature'])} " f"| {case['time']:.2f} | {_format_cost(case['cost'])} |"
            )
    else:
        lines.append("No scenarios recorded in this run.")

    lines.append("")
    lines.append("## Failures")
    lines.append("")
    failed_cases = [case for case in cases if case["status"] in ("failed", "error")]
    if not failed_cases:
        lines.append("No failures recorded for this run.")
    for case in _sorted_cases(failed_cases):
        lines.append("")
        lines.append(f"### {_escape_cell(case['name'])}")
        lines.append("")
        lines.append(f"- Feature: {_escape_cell(case['feature'])}")
        lines.append(f"- Status: {case['status']}")
        if case["failure_message"]:
            lines.append("")
            lines.append("**Failure message:**")
            lines.append("")
            fence = _code_fence(case["failure_message"])
            lines.append(fence)
            lines.append(case["failure_message"])
            lines.append(fence)
        if case["final_response"]:
            lines.append("")
            lines.append(f"**Final response:** {_escape_cell(case['final_response'])}")
        evidence = _evidence_rows(case["properties"])
        if evidence:
            lines.append("")
            lines.append("**Evidence:**")
            lines.append("")
            lines.append("| Evidence | Path |")
            lines.append("| --- | --- |")
            for label, path in evidence:
                # Quote and absolutize the destination (spaces/parens break raw
                # markdown links; relative paths make invalid file:// URLs) while
                # keeping the raw value as the visible label.
                display = _escape_cell(path)
                destination = urllib.parse.quote(os.path.abspath(str(path)), safe="/:")
                lines.append(f"| {label} | [{display}](file://{destination}) |")

    lines.append("")
    lines.append("## Coverage notes")
    lines.append("")
    features = sorted({case["feature"] for case in cases if case["feature"]})
    if features:
        lines.append("Distinct features exercised in this run:")
        lines.append("")
        for feature in features:
            lines.append(f"- {_escape_cell(feature)}")
    else:
        lines.append("No feature information available in this run.")
    lines.append("")

    report_text = "\n".join(lines).rstrip("\n") + "\n"

    if output_path is None:
        output_path = os.path.join(os.path.dirname(os.path.abspath(junit_xml_path)), REPORT_FILE_NAME)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(report_text)
    logger.info(f"Markdown report written to: {output_path}")

    return report_text


def generate_latest_report(project_source_root: str = "./opt") -> Optional[str]:
    """Generate a markdown report for the most recent run under a project root.

    Args:
        project_source_root (str): Project root containing ``output/<timestamp>/`` folders.

    Returns:
        Optional[str]: Path to the generated report, or ``None`` when no runs exist.
    """
    pattern = os.path.join(project_source_root, "output", "*", "*_result.xml")
    candidates = glob.glob(pattern)
    if not candidates:
        logger.info(f"No run result XML files found under: {project_source_root}")
        return None

    # Newest file first; the path breaks mtime ties deterministically so
    # run_YYYYMMDD_HHMMSS names resolve chronologically.
    candidates.sort(key=lambda path: (os.path.getmtime(path), path), reverse=True)
    for latest_xml in candidates:
        output_path = os.path.join(os.path.dirname(os.path.abspath(latest_xml)), REPORT_FILE_NAME)
        try:
            generate_markdown_report(latest_xml, output_path=output_path)
        except ET.ParseError as exc:
            # One malformed result file must not abort reporting when readable runs exist.
            logger.warning("Skipping malformed run result XML %s: %s", latest_xml, exc)
            continue
        return output_path

    logger.warning("No parseable run result XML files found under: %s", project_source_root)
    return None
