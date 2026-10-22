import datetime
import hashlib
import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import aiofiles
from junitparser import Failure, JUnitXml, Property, TestCase, TestSuite
from junitparser.junitparser import Properties, SystemOut
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.env_flags import strict_verdict_enabled
from testzeus_hercules.utils.gherkin_helper import _sanitize_scenario_filename

logger = logging.getLogger(__name__)

# Control characters lxml cannot serialize into XML (keeps \t \n \r). This also
# covers U+007F-U+009F (C1 controls, invalid in XML 1.0) and lone surrogates
# (U+D800-U+DFFF), which cannot even be encoded to UTF-8.
_XML_INCOMPATIBLE_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]")

# Honest-reporting fallbacks: used only when the scenario result carries no
# summary text at all, so a verdict is never reported without a reason.
_NO_SCENARIO_RESULT_MESSAGE = "No scenario result was produced by the harness."
_MISSING_FAILURE_DETAIL_MESSAGE = "Scenario did not record a verified pass and provided no failure detail."


# ── assertion audit ledger ────────────────────────────────────────────────────
# Deterministic assertion tools append one JSON object per verdict (PASSED
# included) to <proof>/assertion_logs.log. The report side reads that ledger so
# the junit verdict cannot contradict the recorded evidence:
# - counts are always reported as case properties when a ledger exists;
# - a case that claims a pass while the ledger holds an evaluable mismatch
#   (verdict "FAILED") or an unresolved check (verdict "ERROR") is converted
#   to a Failure — evaluation mismatches are never forgivable;
# - a pass backed by zero assertions is only *marked* (property
#   "Assertions Missing"): it is model-narrated, and the strict verdict policy
#   already keeps it honest. Tightening that mark into a failure is a separate,
#   explicit migration step.
# Notices (informational ledger entries) and unrecognized verdict values are
# counted but never turn a case red.
ASSERTION_LEDGER_FILE_NAME = "assertion_logs.log"

_LEDGER_FAILED = "failed"  # evaluable mismatch — never forgivable
_LEDGER_UNRESOLVED = "unresolved"  # could not be evaluated (bad path, ...)
_LEDGER_PASSED = "passed"
_LEDGER_NOTICE = "notice"  # informational only — never red
_LEDGER_OTHER = "other"  # unrecognized verdict — counted in Total only

_LEDGER_CLASSIFICATIONS = {
    "FAILED": _LEDGER_FAILED,
    "ERROR": _LEDGER_UNRESOLVED,
    "PASSED": _LEDGER_PASSED,
    "NOTICE": _LEDGER_NOTICE,
}

# Failure-message fields are truncated so one huge payload cannot bloat the XML.
_LEDGER_FIELD_TRUNCATE_LIMIT = 120
_LEDGER_NAMED_OFFENDERS = 3


def _classify_ledger_verdict(verdict: Any) -> str:
    """Map a raw ledger verdict to its report classification (unknown → other, never red)."""
    return _LEDGER_CLASSIFICATIONS.get(str(verdict or "").strip().upper(), _LEDGER_OTHER)


def _ledger_freeze(value: Any) -> str:
    """Render a ledger field hashable for dedupe (JSON text for non-strings)."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def _ledger_dedupe_key(entry: Dict[str, Any]) -> tuple:
    """Identity of one assertion target: retries of the same target share it."""
    return (
        _ledger_freeze(entry.get("subject")),
        _ledger_freeze(entry.get("path")),
        _ledger_freeze(entry.get("operator")),
        _ledger_freeze(entry.get("expected")),
    )


def _dedupe_ledger_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep the LAST verdict per assertion target (retry semantics) while
    preserving first-seen order, so reports are stable across retries.
    """
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for entry in entries:
        deduped[_ledger_dedupe_key(entry)] = entry
    return list(deduped.values())


def _truncate_ledger_field(value: Any, limit: int = _LEDGER_FIELD_TRUNCATE_LIMIT) -> str:
    """Render one ledger field on a single, truncated line."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _ledger_case_stats(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count deduped ledger entries per classification and collect offenders."""
    counts = {_LEDGER_FAILED: 0, _LEDGER_UNRESOLVED: 0, _LEDGER_PASSED: 0, _LEDGER_NOTICE: 0, _LEDGER_OTHER: 0}
    offenders: List[Dict[str, Any]] = []
    for entry in entries:
        classification = _classify_ledger_verdict(entry.get("verdict"))
        counts[classification] += 1
        if classification in (_LEDGER_FAILED, _LEDGER_UNRESOLVED):
            offenders.append(entry)
    return {
        "total": len(entries),
        "failed": counts[_LEDGER_FAILED],
        "unresolved": counts[_LEDGER_UNRESOLVED],
        "passed": counts[_LEDGER_PASSED],
        "notices": counts[_LEDGER_NOTICE],
        "offenders": offenders,
    }


def _ledger_override_message(stats: Dict[str, Any]) -> str:
    """
    Failure message for a planner pass contradicted by the ledger: the first
    offending assertions (path/operator/expected/actual) plus total counts.
    """
    offenders: List[Dict[str, Any]] = stats["offenders"]
    lines = ["Assertion audit ledger contradicts the reported pass: " f"{stats['failed']} failed and {stats['unresolved']} unresolved assertion(s) " f"out of {stats['total']} recorded."]
    for index, offender in enumerate(offenders[:_LEDGER_NAMED_OFFENDERS], 1):
        lines.append(
            f"{index}. [{offender.get('verdict') or 'UNKNOWN'}] "
            f"path={_truncate_ledger_field(offender.get('path'))} "
            f"operator={_truncate_ledger_field(offender.get('operator'))} "
            f"expected={_truncate_ledger_field(offender.get('expected'))} "
            f"actual={_truncate_ledger_field(offender.get('actual'))}"
        )
    remaining = len(offenders) - _LEDGER_NAMED_OFFENDERS
    if remaining > 0:
        lines.append(f"...and {remaining} more offending assertion(s).")
    return "\n".join(lines)


def _strip_xml_incompatible_chars(value: Any) -> Any:
    """
    Remove control characters that make lxml raise
    "All strings must be XML compatible" during XML construction.

    Non-string inputs are returned unchanged so optional values (e.g. a None
    assert_summary) keep their previous behavior.
    """
    if isinstance(value, str):
        return _XML_INCOMPATIBLE_CHARS.sub("", value)
    return value


def _format_failure_record(label: str, failure: Dict[str, Any]) -> str:
    """
    Render a structured failure record as a single readable line.

    Structured records (e.g. a harness execution/cleanup failure) carry a
    human message plus optional context fields; the context is appended only
    when present so a thin record still reads naturally.

    Args:
        label (str): Short label describing which stage failed.
        failure (Dict[str, Any]): The failure record dict.

    Returns:
        str: "Label: message [category=...; stage=...; type=...; evidence=...]".
    """
    message = str(failure.get("message") or "").strip() or "no detail recorded"
    fields = []
    if failure.get("category"):
        fields.append(f"category={failure['category']}")
    if failure.get("stage"):
        fields.append(f"stage={failure['stage']}")
    if failure.get("error_type"):
        fields.append(f"type={failure['error_type']}")
    if failure.get("evidence_path"):
        fields.append(f"evidence={failure['evidence_path']}")
    suffix = f" [{'; '.join(fields)}]" if fields else ""
    return f"{label}: {message}{suffix}"


def flatten_dict(d: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """
    Flatten a nested dictionary.

    Args:
        d (Dict[str, Any]): The dictionary to flatten.
        parent_key (str): The base key to use for the flattened keys.
        sep (str): The separator to use between keys.

    Returns:
        Dict[str, Any]: The flattened dictionary.
    """
    items: List[tuple[str, Any]] = []
    for key, value in d.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            items.extend(flatten_dict(value, new_key, sep=sep).items())
        else:
            items.append((new_key, value))
    return dict(items)


class JUnitXMLGenerator:
    def __init__(
        self,
        suite_name: str,
        feature_file_path: str,
        output_file_path: str,
        proofs_path: str,
        proofs_video_path: str,
        proofs_screenshot_path: str,
        network_logs_path: str,
        logs_path: str,
        planner_thoughts_path: str,
    ) -> None:
        """
        Initialize the JUnitXMLGenerator.

        Args:
            suite_name (str): The name of the test suite.
        """
        self.suite_name = suite_name
        self.suite = TestSuite(_strip_xml_incompatible_chars(suite_name))
        self.total_execution_cost = 0.0
        self.total_token_used = 0
        self.total_time = 0.0
        self.feature_file_path = feature_file_path
        self.output_file_path = output_file_path
        self.proofs_path = proofs_path
        self.proofs_video_path = proofs_video_path
        self.proofs_screenshot_path = proofs_screenshot_path
        self.logs_path = logs_path
        self.planner_thoughts_path = planner_thoughts_path
        self.network_logs_path = network_logs_path

    def _load_assertion_ledger(self) -> Optional[List[Dict[str, Any]]]:
        """
        Read this case's assertion audit ledger, or None when there is none.

        The ledger lives at ``<proofs_path>/assertion_logs.log`` (one JSON
        object per line). A missing or unreadable file switches the feature
        silently off (no properties, no verdict override); malformed lines are
        skipped with a debug log so one bad producer cannot break the report.
        """
        if not self.proofs_path:
            return None
        ledger_path = os.path.join(self.proofs_path, ASSERTION_LEDGER_FILE_NAME)
        if not os.path.isfile(ledger_path):
            return None
        entries: List[Dict[str, Any]] = []
        try:
            with open(ledger_path, "r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, 1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except ValueError as exc:
                        logger.debug("Skipping malformed assertion ledger line %d in %s: %s", line_number, ledger_path, exc)
                        continue
                    if isinstance(parsed, dict):
                        entries.append(parsed)
                    else:
                        logger.debug("Skipping non-object assertion ledger line %d in %s", line_number, ledger_path)
        except OSError as exc:
            logger.debug("Could not read assertion ledger %s: %s", ledger_path, exc)
            return None
        return entries

    @staticmethod
    def _resolve_case_test_id(json_data: Dict[str, Any]) -> Optional[str]:
        """Best-effort test id for the case being reported (None when unknown)."""
        test_id = json_data.get("test_id")
        if test_id:
            return str(test_id)
        try:
            test_id = get_global_conf().get_default_test_id()
        except Exception:
            # Minimal config stand-ins (tests) may not expose a test id at all.
            return None
        return str(test_id) if test_id else None

    def _case_ledger_entries(self, json_data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Ledger entries attributed to this case, or None when no ledger exists.

        Attribution rule: ``build_junit_xml`` creates one generator per
        scenario and passes that scenario's proof folder, so
        ``<proofs_path>/assertion_logs.log`` is the scenario's own ledger and
        every entry belongs to the single case this generator reports. As a
        defense for callers that share one proof folder across cases, entries
        tagged with a ``test_id`` different from this case's own test id (see
        ``_resolve_case_test_id``: explicit ``json_data["test_id"]`` first,
        else the harness default test id) are dropped; untagged entries are
        kept (producers may predate tagging). When no test id is discoverable
        the whole ledger is attributed (one generator instance reports exactly
        one case). Entries are deduped per (subject, path, operator, expected)
        keeping the last verdict.
        """
        entries = self._load_assertion_ledger()
        if entries is None:
            return None
        case_test_id = self._resolve_case_test_id(json_data)
        if case_test_id and any(entry.get("test_id") for entry in entries):
            entries = [entry for entry in entries if not entry.get("test_id") or str(entry.get("test_id")) == case_test_id]
        return _dedupe_ledger_entries(entries)

    def add_test_case(
        self,
        scenario: str,
        feature: str,
        json_data: Dict[str, Any],
        execution_time: float,
        cost_metric: Dict[str, Any],
    ) -> None:
        """
        Add a test case to the test suite.

        Args:
            scenario (str): The scenario name.
            feature (str): The feature name.
            json_data (Dict[str, Any]): The JSON data containing test details.
            execution_time (float): The execution time of the test case.
            cost_metric (Dict[str, Any]): The cost metrics associated with the test case.
        """
        test_case = TestCase(
            name=_strip_xml_incompatible_chars(scenario),
            classname=_strip_xml_incompatible_chars(feature),
            time=execution_time,
        )

        final_response = json_data.get("final_response")
        terminate = json_data.get("terminate", "yes")
        assert_summary = json_data.get("assert_summary")
        is_assert = json_data.get("is_assert", False)
        is_passed = json_data.get("is_passed", False)

        execution_failure = json_data.get("execution_failure")
        cleanup_failure = json_data.get("cleanup_failure")

        if not json_data:
            # A missing scenario result is a harness-level failure, not a pass.
            test_case.result = Failure(message=_NO_SCENARIO_RESULT_MESSAGE)
        elif execution_failure or cleanup_failure:
            # A structured harness failure is never dressed up as a test
            # outcome, even when the scenario result itself claims a pass.
            parts = []
            if execution_failure:
                parts.append(_format_failure_record("Execution failure", execution_failure))
            if cleanup_failure:
                parts.append(_format_failure_record("Cleanup failure", cleanup_failure))
            test_case.result = Failure(message=_strip_xml_incompatible_chars("\n".join(parts)))
        else:
            # Verdict policy. Default (legacy): an asserting scenario passes when
            # is_passed is truthy; a scenario without an explicit assertion passes
            # unless it did not terminate. With STRICT_VERDICT=true (honest
            # reporting) green requires the scenario to have terminated cleanly AND
            # recorded an explicit pass (is_passed is True) — an aborted or
            # unproven scenario must not come out green.
            strict_verdict = strict_verdict_enabled()
            failure_detail = str(assert_summary or final_response or _MISSING_FAILURE_DETAIL_MESSAGE)
            if is_assert:
                add_event(
                    EventType.ASSERT,
                    EventData(detail=f"Assertion with result: {is_passed}"),
                )
                assertion_failed = (is_passed is not True or terminate != "yes") if strict_verdict else not is_passed
                if assertion_failed:
                    test_case.result = Failure(message=_strip_xml_incompatible_chars(failure_detail))
                elif assert_summary:
                    test_case.system_out = _strip_xml_incompatible_chars(assert_summary)
            else:
                # No explicit assertion: legacy only fails when the task did not
                # terminate; strict mode also requires an explicit pass.
                unproven = (terminate != "yes" or is_passed is not True) if strict_verdict else terminate == "no"
                if unproven:
                    test_case.result = Failure(message=_strip_xml_incompatible_chars(failure_detail))

        # Assertion audit ledger: report the recorded assertion counts and,
        # when the scenario result claims a pass that the recorded evidence
        # contradicts (an evaluable mismatch or an unresolved check), convert
        # the case to a Failure. An existing Failure (planner fail, harness
        # failure, aborted run) is left untouched — the ledger never softens
        # a verdict, it only hardens a false green.
        claimed_pass = bool(json_data) and not execution_failure and not cleanup_failure and is_passed is True and terminate == "yes"
        ledger_entries = self._case_ledger_entries(json_data)
        ledger_stats = _ledger_case_stats(ledger_entries) if ledger_entries is not None else None
        if ledger_stats and ledger_stats["offenders"] and claimed_pass:
            test_case.result = Failure(message=_strip_xml_incompatible_chars(_ledger_override_message(ledger_stats)))

        opt_list = []
        test_props = Properties()
        test_case.append(test_props)
        test_props.add_property(Property(name="Terminate", value=_strip_xml_incompatible_chars(str(terminate))))
        # These paths embed the scenario name (stake_id only replaces a few
        # characters), so a control character or lone surrogate in the name can
        # survive into them — sanitize name and value like every other
        # property, or lxml aborts XML construction and the scenario is
        # misreported as a generic report-generation failure.
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Feature File"), value=_strip_xml_incompatible_chars(str(self.feature_file_path))))
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Output File"), value=_strip_xml_incompatible_chars(str(self.output_file_path))))
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Proofs Video"), value=_strip_xml_incompatible_chars(str(self.proofs_video_path))))
        test_props.add_property(
            Property(
                name=_strip_xml_incompatible_chars("Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs"),
                value=_strip_xml_incompatible_chars(str(self.proofs_path)),
            )
        )
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Proofs Screenshot"), value=_strip_xml_incompatible_chars(str(self.proofs_screenshot_path))))
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Network Logs"), value=_strip_xml_incompatible_chars(str(self.network_logs_path))))
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Agents Internal Logs"), value=_strip_xml_incompatible_chars(str(self.logs_path))))
        test_props.add_property(Property(name=_strip_xml_incompatible_chars("Planner Thoughts"), value=_strip_xml_incompatible_chars(str(self.planner_thoughts_path))))
        if ledger_stats is not None:
            test_props.add_property(Property(name="Assertions Total", value=str(ledger_stats["total"])))
            test_props.add_property(Property(name="Assertions Passed", value=str(ledger_stats["passed"])))
            test_props.add_property(Property(name="Assertions Failed", value=str(ledger_stats["failed"])))
            test_props.add_property(Property(name="Assertions Unresolved", value=str(ledger_stats["unresolved"])))
            test_props.add_property(Property(name="Assertions Notices", value=str(ledger_stats["notices"])))
            if claimed_pass and ledger_stats["total"] == 0:
                # Reported only: a pass backed by zero assertions is
                # model-narrated, not verified (see the ledger notes above).
                test_props.add_property(Property(name="Assertions Missing", value="true"))
        opt_list.append(f"Final Response: {final_response}")

        for key, value in json_data.items():
            if key not in [
                "is_assert",
                "is_passed",
                "final_response",
                "assert_summary",
                "execution_failure",
                "cleanup_failure",
            ]:
                # Summary payloads come straight from the (LLM-produced)
                # scenario result: sanitize name and value, or a single control
                # character aborts the whole XML construction — and, without
                # per-scenario containment, the entire batch report.
                prop = Property(name=_strip_xml_incompatible_chars(str(key)), value=_strip_xml_incompatible_chars(str(value)))
                test_props.add_property(prop)

        res_d = flatten_dict(cost_metric)
        res = [f"{k}: {v}" for k, v in res_d.items()]
        opt_list.append(", ".join(res))

        for opt_item in opt_list:
            test_case.append(SystemOut(_strip_xml_incompatible_chars(opt_item)))

        flat_cost_metric = flatten_dict(cost_metric)
        for key, value in flat_cost_metric.items():
            test_props.add_property(Property(name=_strip_xml_incompatible_chars(str(key)), value=_strip_xml_incompatible_chars(str(value))))

        total_cost_value = flat_cost_metric.get("total_cost")
        if total_cost_value is None:
            total_cost_value = flat_cost_metric.get("usage_including_cached_inference.total_cost")
        if total_cost_value is not None:
            self.total_execution_cost += float(total_cost_value)

        token_keys = [
            key
            for key in flat_cost_metric
            if key.endswith(".total_tokens") and (key.startswith("usage_including_cached_inference.") or not any(k.startswith("usage_including_cached_inference.") for k in flat_cost_metric))
        ]
        for key in token_keys:
            self.total_token_used += int(flat_cost_metric[key])

        self.total_time += float(execution_time)
        self.suite.add_testcase(test_case)

    async def write_xml(self, output_file: str) -> None:
        """
        Write the test suite to a JUnit XML file asynchronously.

        Args:
            output_file (str): The path to the output XML file.
        """
        self.suite.add_property("total_execution_cost", str(self.total_execution_cost))
        self.suite.add_property("total_token_used", str(self.total_token_used))
        self.suite.time = self.total_time
        self.suite.timestamp = datetime.datetime.now().isoformat()
        self.suite.update_statistics()

        xml = JUnitXml()
        xml.add_testsuite(self.suite)

        dir_name = os.path.dirname(output_file)
        if dir_name:
            # Belt and braces: the caller normally ensures the directory exists.
            os.makedirs(dir_name, exist_ok=True)

        # Write to temp file first
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                tmp_path = tmp.name
                xml.write(tmp.name)

            # Read from temp and write to final destination asynchronously
            # (junitparser wrote UTF-8; the locale codec would mojibake or
            # crash on non-Latin-1 testcase names).
            async with aiofiles.open(tmp_path, "r", encoding="utf-8") as src, aiofiles.open(output_file, "w", encoding="utf-8") as dest:
                content = await src.read()
                await dest.write(content)
        finally:
            # Never leak the temp file, even if writing failed.
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @staticmethod
    async def merge_junit_xml(files: List[str], output_file: str) -> None:
        """
        Merge multiple JUnit XML files into one asynchronously.

        Args:
            files (List[str]): List of file paths to JUnit XML files.
            output_file (str): Path to the output merged JUnit XML file.
        """
        merged_xml = JUnitXml()
        suite_dict: Dict[str, TestSuite] = {}
        # Files that parsed successfully; only these may be consumed (deleted).
        parsed_files: List[str] = []

        for file in files:
            try:
                xml = JUnitXml.fromfile(file)
            except Exception as exc:
                # One malformed/missing report must not abort the merge (and
                # must never cause deletion of the reports that did parse).
                logger.error("Skipping unreadable junit result %s during merge: %s", file, exc)
                continue
            parsed_files.append(file)
            # JUnitXml.fromfile returns a TestSuite when the root element is
            # <testsuite>; iterating that directly would walk TestCases (and
            # store them as suites), so normalize like parse_junit_testcases.
            suites = xml if isinstance(xml, JUnitXml) else [xml]
            for suite in suites:
                suite_name = suite.name
                if suite_name in suite_dict:
                    existing_suite = suite_dict[suite_name]
                    for testcase in suite:
                        existing_suite.add_testcase(testcase)
                    existing_suite.time = float(existing_suite.time or 0.0) + float(suite.time or 0.0)

                    for prop in suite.properties():
                        is_existing_prop = False
                        for existing_prop in existing_suite.properties():
                            if existing_prop.name.strip() == prop.name.strip():
                                try:
                                    existing_value = float(existing_prop.value)
                                    new_value = float(prop.value)
                                    existing_prop.value = existing_value + new_value
                                except ValueError:
                                    pass
                                is_existing_prop = True

                        existing_suite.update_statistics()

                        if not is_existing_prop:
                            existing_suite.add_property(name=prop.name, value=prop.value)
                else:
                    suite_dict[suite_name] = suite

        for suite in suite_dict.values():
            merged_xml.add_testsuite(suite)

        # Write merged XML to temp file first
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                tmp_path = tmp.name
                merged_xml.write(tmp.name)

            # Read from temp and write to final destination asynchronously
            # (junitparser wrote UTF-8; the locale codec would mojibake or
            # crash on non-Latin-1 testcase names).
            async with aiofiles.open(tmp_path, "r", encoding="utf-8") as src, aiofiles.open(output_file, "w", encoding="utf-8") as dest:
                content = await src.read()
                await dest.write(content)
        finally:
            # Never leak the temp file, even if writing failed.
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Delete individual test files only after the merged XML was written
        # successfully, so a failure above cannot destroy the only copies of
        # the per-scenario results. Files that failed to parse are kept.
        if get_global_conf().get_mode() not in ["debug"]:
            for file in parsed_files:
                if os.path.exists(file):
                    os.remove(file)


def _results_file_path(junit_base_path: str, feature: str, scenario: str) -> str:
    """
    Build the per-scenario results file path.

    The sanitized names keep the path readable, while a short hash of the
    UNSANITIZED feature/scenario names keeps distinct scenarios distinct:
    names that sanitize to the same stem ("transfer/funds" vs "transfer_funds")
    must not share one results file, or the second build overwrites the first
    and a failed case silently vanishes from the merged report (flipping the
    exit code green).
    """
    # Spaces/colons are dropped first (legacy naming), then the full path-component
    # sanitizer keeps "/", "\\" and other unsafe characters out of the file name.
    feature_r = _sanitize_scenario_filename(feature.replace(" ", "_").replace(":", ""))
    scenario_r = _sanitize_scenario_filename(scenario.replace(" ", "_").replace(":", ""))
    # surrogatepass: hostile names may carry lone surrogates that plain UTF-8 refuses to encode.
    name_hash = hashlib.sha1("\x00".join((feature, scenario)).encode("utf-8", "surrogatepass"), usedforsecurity=False).hexdigest()[:8]
    return f"{junit_base_path}/{feature_r}_{scenario_r}_{name_hash}_results.xml"


async def build_junit_xml(
    json_data: Dict[str, Any],
    execution_time: float,
    cost_metric: Dict[str, Any],
    feature: str,
    scenario: str,
    feature_file_path: str = None,
    output_file_path: str = None,
    proofs_path: str = None,
    proofs_video_path: str = None,
    proofs_screenshot_path: str = None,
    logs_path: str = None,
    network_logs_path: str = None,
    planner_thoughts_path: str = None,
) -> str:
    """
    Build a JUnit XML file from test data.

    Args:
        json_data (Dict[str, Any]): The JSON data containing test details.
        execution_time (float): The execution time of the test case.
        cost_metric (Dict[str, Any]): The cost metrics associated with the test case.
        feature (str): The feature name.
        scenario (str): The scenario name.

    Returns:
        str: The path to the generated JUnit XML file.
    """
    file_path = _results_file_path(get_global_conf().get_junit_xml_base_path(), feature, scenario)

    generator = JUnitXMLGenerator(
        feature,
        feature_file_path,
        file_path or output_file_path,
        proofs_path,
        proofs_video_path,
        proofs_screenshot_path,
        network_logs_path,
        logs_path,
        planner_thoughts_path,
    )
    generator.add_test_case(scenario, feature, json_data, execution_time, cost_metric)
    await generator.write_xml(file_path)
    return file_path


async def build_fallback_junit_xml(feature: str, scenario: str, error: BaseException) -> str:
    """
    Build a minimal red testcase entry when normal junit generation failed.

    Reporting must never hide a run: when ``build_junit_xml`` raises for one
    scenario, the batch loop synthesizes this failed entry (carrying the reason)
    so the merged report still records — and fails — that scenario instead of
    dropping it entirely.
    """
    file_path = _results_file_path(get_global_conf().get_junit_xml_base_path(), feature, scenario)
    reason = f"JUnit report generation failed: {type(error).__name__}: {error}"
    generator = JUnitXMLGenerator(feature, "", file_path, None, None, None, None, None, None)
    generator.add_test_case(
        scenario,
        feature,
        {
            "terminate": "yes",
            "is_assert": True,
            "is_passed": False,
            "final_response": reason,
            "assert_summary": f"EXPECTED RESULT: the scenario result is reported.\nACTUAL RESULT: {reason}",
        },
        0.0,
        {},
    )
    await generator.write_xml(file_path)
    return file_path


def run_test() -> None:
    """
    Run a test and generate JUnit XML files.
    """
    json_data = {
        "terminate": "yes",
        "final_response": "The number of bikes displayed when the Max price is set to 1600 is 2, which matches the expected result.",
        "is_assert": True,
        "is_passed": True,
        "assert_summary": "The bike count with Max price 1600 is 2, matching the expected result.",
    }

    execution_time = 30.04
    cost_metric = {
        "usage_including_cached_inference": {
            "total_cost": 0.12923500000000002,
            "gpt-4o-2024-08-06": {
                "cost": 0.12923500000000002,
                "prompt_tokens": 45362,
                "completion_tokens": 1583,
                "total_tokens": 46945,
            },
        },
        "usage_excluding_cached_inference": {
            "total_cost": 0.11612000000000003,
            "gpt-4o-2024-08-06": {
                "cost": 0.11612000000000003,
                "prompt_tokens": 41220,
                "completion_tokens": 1307,
                "total_tokens": 42527,
            },
        },
    }

    feature = "Feature: Filter Bikes by Max Price"
    scenario = "Scenario: User filters bikes with Max Price of 1600"

    f1_path = build_junit_xml(
        json_data,
        execution_time,
        cost_metric,
        feature,
        scenario,
    )

    json_data_fail = {
        "terminate": "yes",
        "final_response": "No bikes are displayed when the Max price is set to 1600, which does not match the expected result.",
        "is_assert": True,
        "is_passed": False,
        "assert_summary": "No bikes are displayed when the Max price is set to 1600, which does not match the expected result.",
    }
    f2_path = build_junit_xml(
        json_data_fail,
        execution_time,
        cost_metric,
        feature + "1",
        scenario + "1",
    )

    f2_path = build_junit_xml(json_data_fail, execution_time, cost_metric, feature + "1", scenario + "1")

    JUnitXMLGenerator.merge_junit_xml(
        [f1_path, f2_path],
        f"{get_global_conf().get_junit_xml_base_path()}/final_results.xml",
    )


# # Example usage
# run_test()
