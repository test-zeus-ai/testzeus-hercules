"""Offline tests for scripts/e2e_gate.py.

The gate is fed hand-written JUnit XML fixtures in tmp directories; no runner
is invoked. Asserts on exit codes, e2e-summary.json contents and the merged
e2e-junit.xml.
"""

import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(script_name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{script_name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e_gate = _load_script("e2e_gate", "e2e_gate_test")


def _junit(testcases: str, tests: int | None = None) -> str:
    count = tests if tests is not None else testcases.count("<testcase")
    return f'<?xml version="1.0" encoding="utf-8"?>\n<testsuite name="suite" tests="{count}">\n{testcases}\n</testsuite>\n'


PASS_XML = _junit('<testcase name="CASE-A" classname="api-auth"/>\n<testcase name="CASE-B" classname="web-smoke"/>')
FAIL_XML = _junit('<testcase name="CASE-A" classname="api-auth">\n  <failure message="status was 500">stack</failure>\n</testcase>')
ERROR_XML = _junit('<testcase name="CASE-A" classname="api-auth">\n  <error message="kaput" type="RuntimeError">boom</error>\n</testcase>')
SKIPPED_XML = _junit('<testcase name="CASE-A" classname="api-auth">\n  <skipped message="not ready"/>\n</testcase>')
ZERO_XML = _junit("", tests=0)


def _run_gate(tmp_path: Path, xml: str | None, known_failures: str | None = None, blocked: dict | None = None, file_name: str = "results.xml"):
    results_dir = tmp_path / "10.0" / "results"
    results_dir.mkdir(parents=True)
    if xml is not None:
        (results_dir / file_name).write_text(xml, encoding="utf-8")
    known_path = tmp_path / "known_failures.txt"
    if known_failures is not None:
        known_path.write_text(known_failures, encoding="utf-8")
    if blocked is not None:
        (tmp_path / "10.0" / "blocked.json").write_text(json.dumps(blocked), encoding="utf-8")
    argv = ["--results-dir", str(results_dir), "--known-failures", str(known_path)]
    code = e2e_gate.main(argv)
    summary = json.loads((tmp_path / "10.0" / "e2e-summary.json").read_text(encoding="utf-8"))
    merged = ET.parse(tmp_path / "10.0" / "e2e-junit.xml").getroot()
    return code, summary, merged


def test_pass_tree_exits_zero(tmp_path: Path) -> None:
    code, summary, merged = _run_gate(tmp_path, PASS_XML)
    assert code == 0
    assert summary["schema"] == "case-library-e2e-summary/v1"
    assert summary["verdict"] == "pass"
    assert summary["total"] == 2 and summary["passed"] == 2
    assert summary["failed"] == 0 and summary["errors"] == 0 and summary["skipped"] == 0
    assert summary["waived"] == 0 and summary["stale_waivers"] == []
    assert summary["failures"] == []
    assert merged.find("testsuite").get("tests") == "2"


def test_failure_element_fails_gate(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, FAIL_XML)
    assert code == 1
    assert summary["verdict"] == "fail"
    assert summary["failed"] == 1
    assert summary["failures"][0]["name"] == "CASE-A"
    assert summary["failures"][0]["message"] == "status was 500"


def test_error_element_fails_gate(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, ERROR_XML)
    assert code == 1
    assert summary["errors"] == 1 and summary["verdict"] == "fail"


def test_skipped_element_fails_gate(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, SKIPPED_XML)
    assert code == 1
    assert summary["skipped"] == 1 and summary["verdict"] == "fail"


def test_zero_scanned_cases_fails_gate(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, ZERO_XML)
    assert code == 1
    assert summary["verdict"] == "fail"
    assert summary["total"] == 0
    assert "zero" in (summary["zero_cases_reason"] or "").lower()


def test_waived_failure_passes_and_is_counted_separately(tmp_path: Path) -> None:
    code, summary, merged = _run_gate(tmp_path, FAIL_XML, known_failures="# flaky infra\nCASE-A\n")
    assert code == 0
    assert summary["verdict"] == "pass"
    assert summary["waived"] == 1
    assert summary["failures"] == []
    waived_props = merged.findall(".//properties/property[@name='waived-by-known-failure']")
    assert [prop.get("value") for prop in waived_props] == ["CASE-A"]


def test_waiver_needs_token_boundary_after_match(tmp_path: Path) -> None:
    # Waiver "AUTH" must not green an unrelated "AUTH-OTHER-99" failure: the
    # character after the match ("-" here) may not be a letter, digit, or hyphen.
    xml = _junit('<testcase name="AUTH-OTHER-99" classname="api-auth">\n  <failure message="denied">x</failure>\n</testcase>')
    code, summary, _ = _run_gate(tmp_path, xml, known_failures="AUTH\n")
    assert code == 1
    assert summary["verdict"] == "fail"
    assert summary["waived"] == 0
    assert summary["failures"][0]["name"] == "AUTH-OTHER-99"


def test_waiver_exact_id_and_classname_prefix_still_match(tmp_path: Path) -> None:
    xml = _junit(
        '<testcase name="AUTH-LOGIN-01" classname="api-auth">\n  <failure message="denied">x</failure>\n</testcase>\n'
        '<testcase name="CASE-B" classname="web-smoke">\n  <failure message="down">x</failure>\n</testcase>'
    )
    # "AUTH-LOGIN-01" is an exact id; "web-smoke" is followed by the "::" separator.
    code, summary, _ = _run_gate(tmp_path, xml, known_failures="AUTH-LOGIN-01\nweb-smoke\n")
    assert code == 0
    assert summary["verdict"] == "pass"
    assert summary["waived"] == 2


def test_stale_waiver_on_passing_case_fails_gate(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, PASS_XML, known_failures="CASE-A\n")
    assert code == 1
    assert summary["verdict"] == "fail"
    assert summary["stale_waivers"] == [{"pattern": "CASE-A", "name": "CASE-A", "classname": "api-auth"}]


def test_corrupt_xml_exits_2_and_writes_no_summary(tmp_path: Path) -> None:
    results_dir = tmp_path / "10.0" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "broken.xml").write_text("<testsuite><testcase>", encoding="utf-8")
    code = e2e_gate.main(["--results-dir", str(results_dir), "--known-failures", str(tmp_path / "absent.txt")])
    assert code == 2
    assert not (tmp_path / "10.0" / "e2e-summary.json").exists()


def test_missing_results_dir_exits_2(tmp_path: Path) -> None:
    code = e2e_gate.main(["--results-dir", str(tmp_path / "nope"), "--known-failures", str(tmp_path / "absent.txt")])
    assert code == 2


def test_blocked_json_counts_reported_in_summary(tmp_path: Path) -> None:
    blocked = {
        "schema": "case-library-blocked/v1",
        "target": "10.0",
        "count": 2,
        "excluded": [
            {"category": "fixture", "scenario": "A"},
            {"category": "tooling", "scenario": "B"},
        ],
    }
    code, summary, _ = _run_gate(tmp_path, PASS_XML, blocked=blocked)
    assert code == 0
    assert summary["blocked_excluded"] == {"available": True, "schema": "case-library-blocked/v1", "total": 2, "by_category": {"fixture": 1, "tooling": 1}}


def test_missing_blocked_json_reported_as_unavailable(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, PASS_XML)
    assert code == 0
    assert summary["blocked_excluded"] == {"available": False}


def test_corrupt_blocked_json_exits_2(tmp_path: Path) -> None:
    results_dir = tmp_path / "10.0" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "results.xml").write_text(PASS_XML, encoding="utf-8")
    (tmp_path / "10.0" / "blocked.json").write_text("{not json", encoding="utf-8")
    code = e2e_gate.main(["--results-dir", str(results_dir), "--known-failures", str(tmp_path / "absent.txt")])
    assert code == 2


def test_version_flag_resolves_results_dir(tmp_path: Path) -> None:
    results_dir = tmp_path / "opt" / ".run" / "10.0" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "results.xml").write_text(PASS_XML, encoding="utf-8")
    code = e2e_gate.main(["--run-root", str(tmp_path / "opt" / ".run"), "--version", "10.0", "--known-failures", str(tmp_path / "absent.txt")])
    assert code == 0
    assert (tmp_path / "opt" / ".run" / "10.0" / "e2e-summary.json").is_file()


def test_gate_module_level_helpers(tmp_path: Path) -> None:
    assert e2e_gate.load_waiver_patterns(None) == []
    missing = tmp_path / "missing.txt"
    assert e2e_gate.load_waiver_patterns(missing) == []
    waiver = tmp_path / "kf.txt"
    waiver.write_text("# comment\n\nPATTERN-1\n", encoding="utf-8")
    assert e2e_gate.load_waiver_patterns(waiver) == ["PATTERN-1"]
    with pytest.raises(e2e_gate.GateCannotJudge):
        e2e_gate.judge(tmp_path / "nothing", [], None)


def test_non_junit_xml_is_not_scanned(tmp_path: Path) -> None:
    results_dir = tmp_path / "10.0" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "junit.xml").write_text(PASS_XML, encoding="utf-8")
    (results_dir / "hurl-report.xml").write_text("<report><entry name='x'/></report>", encoding="utf-8")
    assert e2e_gate.main(["--results-dir", str(results_dir), "--known-failures", str(tmp_path / "none.txt")]) == 0
    summary = json.loads((tmp_path / "10.0" / "e2e-summary.json").read_text(encoding="utf-8"))
    assert summary["scanned_files"] == 1


def test_waiver_matching_no_case_is_reported_without_failing(tmp_path: Path) -> None:
    code, summary, _ = _run_gate(tmp_path, PASS_XML, known_failures="REMOVED-CASE-99\n")
    assert code == 0
    assert summary["unmatched_waivers"] == ["REMOVED-CASE-99"]
