"""Offline tests for the markdown report generator."""

import os
import urllib.parse

from testzeus_hercules.reporting.markdown_report import (
    NO_METRIC_LABEL,
    REPORT_FILE_NAME,
    _escape_cell,
    _format_cost,
    _format_tokens,
    generate_latest_report,
    generate_markdown_report,
    parse_run_results,
)

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="Feature: Bike Filter" tests="2" failures="1" errors="0" time="12.5" timestamp="2026-09-09T10:00:00">
    <properties>
      <property name="total_execution_cost" value="0.25"/>
      <property name="total_token_used" value="100"/>
    </properties>
    <testcase name="Scenario: filter bikes by max price" classname="Feature: Bike Filter" time="5.0">
      <properties>
        <property name="Terminate" value="yes"/>
        <property name="Proofs Screenshot" value="/tmp/run/screenshots"/>
        <property name="Proofs Video" value="/tmp/run/video.webm"/>
        <property name="Network Logs" value="/tmp/run/network.jsonl"/>
        <property name="Agents Internal Logs" value="/tmp/run/agent_logs"/>
        <property name="Planner Thoughts" value="/tmp/run/thoughts.json"/>
        <property name="usage_including_cached_inference.total_cost" value="0.12"/>
      </properties>
      <system-out>Final Response: Two bikes were displayed as expected.</system-out>
    </testcase>
    <testcase name="Scenario: filter bikes above budget" classname="Feature: Bike Filter" time="7.5">
      <failure message="Assertion failed: expected 2 bikes, got 0">Traceback (most recent call last): ...</failure>
      <properties>
        <property name="Terminate" value="yes"/>
        <property name="Proofs Screenshot" value="/tmp/run/fail_screenshots"/>
        <property name="usage_including_cached_inference.total_cost" value="0.13"/>
      </properties>
      <system-out>Final Response: No bikes were displayed, the filter did not apply.</system-out>
    </testcase>
  </testsuite>
</testsuites>
"""


def _write_sample_run(tmp_path, dir_name: str = "20260909_100000") -> str:
    run_dir = tmp_path / "output" / dir_name
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "bike_filter_result.xml"
    xml_path.write_text(SAMPLE_XML, encoding="utf-8")
    return str(xml_path)


def test_parse_run_results_extracts_statuses_and_costs(tmp_path) -> None:
    xml_path = _write_sample_run(tmp_path)

    data = parse_run_results(xml_path)

    assert data["run_id"] == "20260909_100000"
    assert data["xml_path"] == xml_path
    assert data["suite"]["tests"] == 2
    assert data["suite"]["failures"] == 1
    assert data["suite"]["errors"] == 0
    assert data["suite"]["time"] == 12.5
    assert data["suite"]["total_cost"] == 0.25
    assert data["suite"]["total_tokens"] == 100

    cases = data["cases"]
    assert [case["status"] for case in cases] == ["passed", "failed"]
    assert cases[0]["cost"] == 0.12
    assert cases[0]["final_response"] == "Two bikes were displayed as expected."
    assert cases[0]["feature"] == "Feature: Bike Filter"
    assert cases[1]["failure_message"].startswith("Assertion failed: expected 2 bikes, got 0")
    assert cases[1]["final_response"] == "No bikes were displayed, the filter did not apply."
    assert cases[1]["cost"] == 0.13


def test_generate_markdown_report_writes_file_next_to_xml(tmp_path) -> None:
    xml_path = _write_sample_run(tmp_path)

    report_text = generate_markdown_report(xml_path, run_meta={"Feature file": "bike_filter.feature", "Mode": "test"})

    report_path = os.path.join(os.path.dirname(xml_path), REPORT_FILE_NAME)
    assert os.path.exists(report_path)
    written = open(report_path, encoding="utf-8").read()
    assert written == report_text

    assert "# Hercules Test Report" in report_text
    assert "- Run ID: 20260909_100000" in report_text
    assert "- Feature file: bike_filter.feature" in report_text
    assert "Overall status: FAILED ❌" in report_text
    assert "- Total tests: 2" in report_text
    assert "- Passed: 1" in report_text
    assert "- Failed: 1" in report_text
    assert "- Total cost: 0.25" in report_text
    assert "- Total tokens: 100" in report_text
    assert "Scenario: filter bikes by max price" in report_text
    assert "Scenario: filter bikes above budget" in report_text
    assert "Assertion failed: expected 2 bikes, got 0" in report_text
    assert "file:///tmp/run/fail_screenshots" in report_text
    assert "- Feature: Bike Filter" in report_text


def test_markdown_report_escapes_pipes_in_table_cells(tmp_path) -> None:
    xml = SAMPLE_XML.replace("Scenario: filter bikes by max price", "Scenario: bikes | max | price")
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "pipe_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    report_text = generate_markdown_report(str(xml_path))

    assert "| ✅ | Scenario: bikes \\| max \\| price |" in report_text


def test_generate_latest_report_picks_newest_run(tmp_path) -> None:
    old_xml = _write_sample_run(tmp_path, "20260909_100000")
    new_xml = _write_sample_run(tmp_path, "20260909_110000")
    # Force distinct mtimes so "newest" is deterministic regardless of write order.
    os.utime(old_xml, (1000000000, 1000000000))
    os.utime(new_xml, (2000000000, 2000000000))

    report_path = generate_latest_report(str(tmp_path))

    assert report_path is not None
    assert os.path.basename(report_path) == REPORT_FILE_NAME
    assert os.path.dirname(report_path) == os.path.dirname(new_xml)
    assert os.path.exists(report_path)
    with open(report_path, encoding="utf-8") as handle:
        assert "- Run ID: 20260909_110000" in handle.read()


def test_generate_latest_report_returns_none_without_runs(tmp_path) -> None:
    assert generate_latest_report(str(tmp_path)) is None


def test_failure_fence_survives_embedded_backticks(tmp_path) -> None:
    """A failure message containing ``` must not break out of its code fence."""
    evil_message = "boom ```\nrm -rf /\n``` done"
    xml = SAMPLE_XML.replace("Traceback (most recent call last): ...", evil_message)
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "fence_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    report_text = generate_markdown_report(str(xml_path))

    lines = report_text.splitlines()
    fence_positions = [index for index, line in enumerate(lines) if line == "````"]
    # Exactly one negotiated (4-backtick) fence: opening + closing.
    assert len(fence_positions) == 2
    assert evil_message in "\n".join(lines[fence_positions[0] + 1 : fence_positions[1]])


def test_final_response_is_pipe_escaped(tmp_path) -> None:
    """final_response is interpolated inline, so it must be escaped, not raw."""
    evil_response = "oops | rm -rf ```x```"
    xml = SAMPLE_XML.replace("No bikes were displayed, the filter did not apply.", evil_response)
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "response_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    report_text = generate_markdown_report(str(xml_path))

    assert f"**Final response:** oops \\| rm -rf ```x```" in report_text


def test_negative_and_nan_durations_render_as_zero(tmp_path) -> None:
    xml = SAMPLE_XML.replace('time="12.5"', 'time="-1.5"').replace('time="5.0"', 'time="-5"').replace('time="7.5"', 'time="NaN"')
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "durations_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    data = parse_run_results(str(xml_path))
    assert [case["time"] for case in data["cases"]] == [0.0, 0.0]
    assert data["suite"]["time"] == 0.0

    report_text = generate_markdown_report(str(xml_path))
    assert "- Duration: 0.00s" in report_text
    assert "| 0.00 |" in report_text
    assert "-5.00" not in report_text
    assert "nan" not in report_text


def test_evidence_links_are_quoted_and_absolute(tmp_path) -> None:
    raw_path = "proofs/my run/fail (1).png"
    xml = SAMPLE_XML.replace("/tmp/run/fail_screenshots", raw_path)
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "evidence_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    report_text = generate_markdown_report(str(xml_path))

    # Raw path stays the visible label; the destination is absolutized and quoted.
    expected_destination = urllib.parse.quote(os.path.abspath(raw_path), safe="/:")
    assert f"[{raw_path}](file://{expected_destination})" in report_text
    assert "(file://proofs/" not in report_text


def test_generate_latest_report_breaks_mtime_ties_by_path(tmp_path) -> None:
    old_xml = _write_sample_run(tmp_path, "20260909_100000")
    new_xml = _write_sample_run(tmp_path, "20260909_100001")
    # Identical mtimes: the chronological run-dir name must break the tie.
    identical = 1_500_000_000.0
    os.utime(old_xml, (identical, identical))
    os.utime(new_xml, (identical, identical))

    report_path = generate_latest_report(str(tmp_path))

    assert report_path is not None
    assert os.path.dirname(report_path) == os.path.dirname(new_xml)
    with open(report_path, encoding="utf-8") as handle:
        assert "- Run ID: 20260909_100001" in handle.read()


# ── hardening: junk metrics, lone CR, infinite durations, malformed newest XML ──


def test_non_finite_tokens_and_cost_render_as_placeholder() -> None:
    """Junk junit metric properties ("nan", "inf") must not abort report generation."""
    assert _format_tokens(float("nan")) == NO_METRIC_LABEL
    assert _format_tokens(float("inf")) == NO_METRIC_LABEL
    assert _format_tokens(float("1e999")) == NO_METRIC_LABEL
    assert _format_tokens(100) == "100"
    assert _format_cost(float("inf")) == NO_METRIC_LABEL
    assert _format_cost(0.25) == "0.25"


def test_junit_with_non_finite_metric_properties_generates_report(tmp_path) -> None:
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "junk_metrics_result.xml"
    xml_path.write_text(SAMPLE_XML.replace('value="100"', 'value="nan"').replace('value="0.25"', 'value="inf"'), encoding="utf-8")

    report_text = generate_markdown_report(str(xml_path))

    assert f"- Total tokens: {NO_METRIC_LABEL}" in report_text
    assert f"- Total cost: {NO_METRIC_LABEL}" in report_text


def test_escape_cell_strips_lone_carriage_return() -> None:
    """A lone CR is a line break in CommonMark and would silently split table rows."""
    assert _escape_cell("a\rb") == "a b"
    assert "\r" not in _escape_cell("a\r\nb\nc\rd")


def test_report_rows_survive_carriage_return_in_names(tmp_path) -> None:
    xml = SAMPLE_XML.replace("Scenario: filter bikes by max price", "Scenario: bikes&#13;max price")
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "cr_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    report_text = generate_markdown_report(str(xml_path))

    assert "\r" not in report_text
    assert "| ✅ | Scenario: bikes max price |" in report_text


def test_infinite_durations_render_as_zero(tmp_path) -> None:
    """Positive-infinity junit times must clamp to 0 exactly like NaN, not render "infs"."""
    xml = SAMPLE_XML.replace('time="12.5"', 'time="inf"').replace('time="5.0"', 'time="inf"').replace('time="7.5"', 'time="Infinity"')
    run_dir = tmp_path / "output" / "20260909_100000"
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "inf_duration_feature_result.xml"
    xml_path.write_text(xml, encoding="utf-8")

    data = parse_run_results(str(xml_path))
    assert data["suite"]["time"] == 0.0
    assert [case["time"] for case in data["cases"]] == [0.0, 0.0]

    report_text = generate_markdown_report(str(xml_path))
    assert "- Duration: 0.00s" in report_text
    assert "inf" not in report_text.lower()


def _write_malformed_run(tmp_path, dir_name: str) -> str:
    run_dir = tmp_path / "output" / dir_name
    run_dir.mkdir(parents=True)
    xml_path = run_dir / "broken_result.xml"
    xml_path.write_text("<testsuites><testsuite><testcase broken", encoding="utf-8")
    return str(xml_path)


def test_generate_latest_report_skips_malformed_newest_xml(tmp_path) -> None:
    """A malformed newest *_result.xml degrades to the newest parseable run."""
    old_xml = _write_sample_run(tmp_path, "20260909_100000")
    bad_xml = _write_malformed_run(tmp_path, "20260909_110000")
    os.utime(old_xml, (1000000000, 1000000000))
    os.utime(bad_xml, (2000000000, 2000000000))

    report_path = generate_latest_report(str(tmp_path))

    assert report_path is not None
    assert os.path.dirname(report_path) == os.path.dirname(old_xml)
    with open(report_path, encoding="utf-8") as handle:
        assert "- Run ID: 20260909_100000" in handle.read()


def test_generate_latest_report_returns_none_when_all_xml_malformed(tmp_path) -> None:
    bad_xml = _write_malformed_run(tmp_path, "20260909_100000")
    os.utime(bad_xml, (1000000000, 1000000000))

    assert generate_latest_report(str(tmp_path)) is None
