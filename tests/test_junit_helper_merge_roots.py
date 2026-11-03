"""Offline tests for merge_junit_xml root-element normalization.

``JUnitXml.fromfile`` returns a bare ``TestSuite`` when a junit file's root
element is ``<testsuite>`` instead of ``<testsuites>``. ``merge_junit_xml``
must treat it as a suite (not iterate its testcases as if they were suites),
so such reports merge cleanly instead of aborting or corrupting the merged
XML.
"""

import asyncio
import os

import pytest


class _FakeConf:
    """Minimal stand-in for the global config used by junit_helper."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "junit"
        self.junit_base.mkdir(parents=True, exist_ok=True)

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_mode(self) -> str:
        return "prod"


@pytest.fixture()
def junit_env(monkeypatch, tmp_path):
    conf = _FakeConf(tmp_path)
    # merge_junit_xml resolves MODE through this module-level name.
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)
    return conf


def _write_file(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _merged_path(tmp_path) -> str:
    # The merge API writes into an existing directory (run_manager creates it
    # at its call site), so create it here.
    merged_dir = tmp_path / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    return str(merged_dir / "merged_result.xml")


SUITES_ROOT_XML = (
    '<testsuites><testsuite name="Feature: one" tests="1" failures="0" errors="0" time="0.1">' '<testcase name="Scenario: s1" classname="Feature: one" time="0.1"/>' "</testsuite></testsuites>"
)
SUITE_ROOT_XML = '<testsuite name="Feature: two" tests="1" failures="0" errors="0" time="0.2">' '<testcase name="Scenario: s2" classname="Feature: two" time="0.2"/>' "</testsuite>"


def test_merge_junit_xml_accepts_testsuite_root(junit_env, tmp_path) -> None:
    from testzeus_hercules.reporting.bundle import parse_junit_testcases
    from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator

    suites_root = _write_file(str(tmp_path / "suites_root.xml"), SUITES_ROOT_XML)
    suite_root = _write_file(str(tmp_path / "suite_root.xml"), SUITE_ROOT_XML)
    merged = _merged_path(tmp_path)

    # A <testsuite>-root file must not abort (or corrupt) the whole merge.
    asyncio.run(JUnitXMLGenerator.merge_junit_xml([suites_root, suite_root], merged))

    cases = parse_junit_testcases(merged)
    assert sorted(case["name"] for case in cases) == ["Scenario: s1", "Scenario: s2"]
    # Both inputs stay real suites under the <testsuites> root.
    with open(merged, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert '<testsuite name="Feature: one"' in content
    assert '<testsuite name="Feature: two"' in content


def test_merge_junit_xml_merges_same_name_from_testsuite_root(junit_env, tmp_path) -> None:
    # Collision path: a <testsuite>-root file whose suite name matches an
    # earlier input used to store a TestCase as a "suite" and crash on
    # add_testcase; it must merge into the existing suite instead.
    from testzeus_hercules.reporting.bundle import parse_junit_testcases
    from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator

    first = _write_file(str(tmp_path / "first.xml"), SUITE_ROOT_XML)
    second = _write_file(str(tmp_path / "second.xml"), SUITE_ROOT_XML.replace("Scenario: s2", "Scenario: s3"))
    merged = _merged_path(tmp_path)

    asyncio.run(JUnitXMLGenerator.merge_junit_xml([first, second], merged))

    cases = parse_junit_testcases(merged)
    assert sorted(case["name"] for case in cases) == ["Scenario: s2", "Scenario: s3"]
    with open(merged, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert '<testsuite name="Feature: two"' in content
