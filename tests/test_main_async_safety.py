"""Async-safety regression tests for the ``python -m testzeus_hercules`` entrypoint.

Covers: sequential_process offloads its blocking reporting/DB work to worker
threads (asyncio.to_thread), the in-process background task is kept strongly
referenced, and bundle.md classification writes are atomic
(temp file + os.replace). All blocking collaborators are stubbed; nothing real
is executed.
"""

from __future__ import annotations

import asyncio
import os
import threading

import pytest
import testzeus_hercules.__main__ as main_module
from testzeus_hercules.reporting.bundle import UNCLASSIFIED_TEXT

FEATURE_TEXT = """Feature: Offline Demo

  Scenario: Passing scenario
    Given the user is on the home page
"""


class _FakeConf:
    """Minimal stand-in for the global config used by __main__."""

    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "output"
        self.junit_base.mkdir(parents=True, exist_ok=True)
        input_dir = tmp_path / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        self.input_path = input_dir / "test.feature"
        self.input_path.write_text(FEATURE_TEXT, encoding="utf-8")
        self.tmp_dir = str(tmp_path)

    def get_dont_close_browser(self) -> bool:
        return False

    def get_input_gherkin_file_path(self) -> str:
        return str(self.input_path)

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_proof_path(self, test_id=None) -> str:
        return os.path.join(self.tmp_dir, "proofs", test_id or "default")

    def get_source_log_folder_path(self, test_id=None) -> str:
        return os.path.join(self.tmp_dir, "log_files", test_id or "default")

    def get_token_verbose(self) -> bool:
        return False

    def set_default_test_id(self, test_id: str = "default") -> None:
        return None


class _FakeBrowserManager:
    def __init__(self, stake_id: str, tmp_dir: str) -> None:
        self.stake_id = stake_id
        self._screenshots_dir = os.path.join(tmp_dir, "screenshots")
        self.request_response_log_file = ""

    def get_latest_video_path(self) -> str:
        return ""


def _make_fake_runner(tmp_dir: str):
    class _FakeRunner:
        def __init__(self, stake_id: str, command: str, dont_terminate_browser_after_run: bool = False) -> None:
            self.stake_id = stake_id
            self.command = command
            self.result = None
            self.execution_time = 1.0
            self.browser_manager = _FakeBrowserManager(stake_id, tmp_dir)

        async def start(self) -> None:
            return None

    return _FakeRunner


class _FakeJUnitGenerator:
    @staticmethod
    async def merge_junit_xml(result_of_tests, out_path: str) -> None:
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write("<?xml version='1.0'?><testsuites/>")


@pytest.fixture()
def fake_run_env(monkeypatch, tmp_path):
    """Mocked run environment recording which blocking helpers left the main thread."""
    conf = _FakeConf(tmp_path)
    main_thread = threading.get_ident()
    offloaded: list[str] = []

    def _record(name: str):
        def _inner(*args, **kwargs):
            if threading.get_ident() != main_thread:
                offloaded.append(name)
            return None

        return _inner

    async def fake_process_feature_file(dont_append_header: bool = False):
        return [
            {
                "output_file": str(tmp_path / "passing_scenario.feature"),
                "feature": "Feature: Offline Demo",
                "scenario": "Scenario: Passing scenario",
            }
        ]

    async def fake_serialize_feature_file(file_path: str) -> str:
        return "command text"

    async def fake_build_junit_xml(*args, **kwargs):
        return {"case": "stub"}

    async def fake_create_failure_bundles(junit_xml_path: str, output_dir=None, max_screenshots=3):
        if threading.get_ident() != main_thread:
            offloaded.append("create_failure_bundles")
        return []

    def fake_parse_junit_testcases(junit_xml_path: str) -> list[dict]:
        if threading.get_ident() != main_thread:
            offloaded.append("parse_junit_testcases")
        return []

    def fake_record_run_summary(summary: dict) -> str:
        if threading.get_ident() != main_thread:
            offloaded.append("record_run_summary")
        return "run_stub"

    monkeypatch.setattr(main_module, "get_global_conf", lambda: conf)
    monkeypatch.setattr(main_module, "process_feature_file", fake_process_feature_file)
    monkeypatch.setattr(main_module, "serialize_feature_file", fake_serialize_feature_file)
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", _make_fake_runner(str(tmp_path)))
    monkeypatch.setattr(main_module, "build_junit_xml", fake_build_junit_xml)
    monkeypatch.setattr(main_module, "JUnitXMLGenerator", _FakeJUnitGenerator)
    monkeypatch.setattr(main_module, "prepare_html", _record("prepare_html"))
    monkeypatch.setattr(main_module, "parse_junit_testcases", fake_parse_junit_testcases)
    monkeypatch.setattr(main_module, "generate_markdown_report", _record("generate_markdown_report"))
    monkeypatch.setattr(main_module, "create_failure_bundles", fake_create_failure_bundles)
    monkeypatch.setattr("testzeus_hercules.reporting.run_db.record_run_summary", fake_record_run_summary)
    return conf, offloaded


def test_sequential_process_offloads_blocking_work_to_threads(fake_run_env, monkeypatch) -> None:
    # The reporting steps are opt-in; enable them all to cover their offloading.
    for flag in ("ENABLE_RUN_REPORTING", "ENABLE_FAILURE_BUNDLES", "ENABLE_RUN_HISTORY"):
        monkeypatch.setenv(flag, "true")
    _conf, offloaded = fake_run_env
    summary = asyncio.run(main_module.sequential_process())

    expected = {"prepare_html", "parse_junit_testcases", "generate_markdown_report", "create_failure_bundles", "record_run_summary"}
    assert expected <= set(offloaded), f"blocking calls must run off the event loop; offloaded={offloaded}"
    assert summary["run_id"] == "run_stub"
    assert summary["bundles"] == []


def test_main_keeps_background_task_referenced(monkeypatch) -> None:
    started = asyncio.Event()

    async def fake_a_main():
        started.set()
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(main_module, "a_main", fake_a_main)

    async def scenario():
        main_module.main()  # in-process branch: schedules a_main on the running loop
        assert len(main_module._background_tasks) == 1, "task must stay referenced"
        await started.wait()
        await asyncio.sleep(0.1)
        assert not main_module._background_tasks, "done callback must discard finished tasks"

    asyncio.run(scenario())


# ── atomic bundle.md classification writes ────────────────────────────────────


def test_write_classification_into_bundle_replaces_placeholder_atomically(tmp_path) -> None:
    bundle_md = tmp_path / "bundle.md"
    bundle_md.write_text(f"# Failure evidence\n\n{UNCLASSIFIED_TEXT}\n", encoding="utf-8")

    main_module._write_classification_into_bundle(str(bundle_md), "product_bug (0.90): assertion failed")

    content = bundle_md.read_text(encoding="utf-8")
    assert "product_bug (0.90): assertion failed" in content
    assert UNCLASSIFIED_TEXT not in content
    assert list(tmp_path.glob("*.tmp")) == [], "temp file must be replaced, not left behind"


def test_write_classification_into_bundle_appends_without_placeholder(tmp_path) -> None:
    bundle_md = tmp_path / "bundle.md"
    bundle_md.write_text("# Failure evidence\n", encoding="utf-8")

    main_module._write_classification_into_bundle(str(bundle_md), "environment (0.50): timeout")

    content = bundle_md.read_text(encoding="utf-8")
    assert "## Classification" in content
    assert "environment (0.50): timeout" in content
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_classification_into_bundle_preserves_original_on_failure(tmp_path, monkeypatch) -> None:
    bundle_md = tmp_path / "bundle.md"
    original = "# Failure evidence\n"
    bundle_md.write_text(original, encoding="utf-8")

    def broken_replace(src: str, dst: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(main_module.os, "replace", broken_replace)
    main_module._write_classification_into_bundle(str(bundle_md), "classified")

    assert bundle_md.read_text(encoding="utf-8") == original, "failed replace must not truncate the original"
    assert list(tmp_path.glob("*.tmp")) == [], "temp file must be cleaned up"
