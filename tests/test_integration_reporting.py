"""Offline integration tests: run history + failure classification wired into sequential_process.

Runs ``sequential_process`` end-to-end with the browser runner mocked out (same pattern
as test_exit_code) for one FAILED scenario, then asserts that the summary carries the
run_id and classification counts, the run-history DB (via ``$HERCULES_HISTORY_DB``)
sees the run, its case and the classification healing event, and the failure bundle's
bundle.md was annotated in place of the "Unclassified" placeholder. Also unit-tests the
BrowserLogger selector-store recording hook. No network, no LLM, no browser.
"""

import asyncio
import json
import os

import pytest
import testzeus_hercules.__main__ as main_module
from testzeus_hercules.__main__ import sequential_process
from testzeus_hercules.core.browser_logger import BrowserLogger
from testzeus_hercules.healing.selector_store import SelectorStore
from testzeus_hercules.reporting import run_db
from testzeus_hercules.reporting.bundle import sanitize_name
from testzeus_hercules.reporting.run_db import RunHistory

SCENARIO_NAME = "Scenario: Failing scenario"

# assert_summary becomes the junit failure message; it must classify as product_bug
# ("assertion failed" + "expected ... but" signals, no environment/contract signals).
PLANNER_FAILURE_SUMMARY = json.dumps(
    {
        "terminate": "yes",
        "is_passed": False,
        "final_response": "The page loaded and the dashboard was shown",
        "is_assert": True,
        "assert_summary": "Assertion failed: Expected 'Welcome' but page showed 'Error: invalid data'",
    }
)

FEATURE_TEXT = """Feature: Offline Demo

  Scenario: Failing scenario
    Given the user is on the home page
"""

CLASSIFICATION_CATEGORY_KEYS = {"product_bug", "test_fragility", "environment", "contract_violation", "unclassified"}


class _FakeResult:
    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.cost = {}
        self.terminate = "yes"


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
            self.result = _FakeResult(PLANNER_FAILURE_SUMMARY)
            self.execution_time = 1.23
            self.browser_manager = _FakeBrowserManager(stake_id, tmp_dir)

        async def start(self) -> None:
            return None

    return _FakeRunner


class _FakeConf:
    """Minimal stand-in for the global config used by __main__ and junit_helper."""

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

    def get_mode(self) -> str:
        return "prod"

    def set_default_test_id(self, test_id: str = "default") -> None:
        return None


@pytest.fixture()
def failed_run_env(monkeypatch, tmp_path):
    """Mocked single-failure run environment bound to a tmp run-history DB."""
    conf = _FakeConf(tmp_path)
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_url = f"sqlite:///{history_dir / 'hercules_history.db'}"
    monkeypatch.setenv("HERCULES_HISTORY_DB", history_url)
    run_db.reset_default_history()

    async def fake_process_feature_file(dont_append_header: bool = False):
        return [
            {
                "output_file": str(tmp_path / "gherkin_files" / "failing_scenario.feature"),
                "feature": "Feature: Offline Demo",
                "scenario": SCENARIO_NAME,
            }
        ]

    async def fake_serialize_feature_file(file_path: str) -> str:
        return "command text"

    monkeypatch.setattr(main_module, "get_global_conf", lambda: conf)
    monkeypatch.setattr(main_module, "process_feature_file", fake_process_feature_file)
    monkeypatch.setattr(main_module, "serialize_feature_file", fake_serialize_feature_file)
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", _make_fake_runner(str(tmp_path)))
    # build_junit_xml / merge_junit_xml resolve the junit base path through this
    # module-level name, so patch it to the same fake conf.
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: conf)

    yield conf

    run_db.reset_default_history()


def test_sequential_process_failed_case_records_history_and_classification(failed_run_env) -> None:
    summary = asyncio.run(sequential_process())

    # Baseline summary keys are intact and the case is counted as failed.
    assert summary["failed"] == 1
    assert summary["passed"] == 0
    assert summary["total"] == 1
    assert summary["feature_file"] == failed_run_env.get_input_gherkin_file_path()

    # (a) run_id recorded into the summary.
    assert summary.get("run_id")

    # (b) classification counts: product_bug >= 1, all five category keys present.
    classifications = summary["classifications"]
    assert set(classifications) == CLASSIFICATION_CATEGORY_KEYS
    assert all(isinstance(count, int) for count in classifications.values())
    assert classifications["product_bug"] >= 1
    assert sum(classifications.values()) == 1

    # (c) the run-history DB (bound via HERCULES_HISTORY_DB) sees run, case and event.
    history = RunHistory(db_url=os.environ["HERCULES_HISTORY_DB"])
    run = history.get_run(summary["run_id"])
    assert run["status"] == "failed"
    assert len(run["cases"]) == 1
    case = run["cases"][0]
    assert case["case_key"] == SCENARIO_NAME
    assert case["status"] == "failed"

    classification_events = [event for event in case["healing_events"] if event["kind"] == "classification"]
    assert len(classification_events) == 1
    event = classification_events[0]
    assert event["status"] == "product_bug"
    assert "product_bug" in event["detail"]

    # (d) the failure bundle was annotated in place of the placeholder.
    bundle_md = os.path.join(
        failed_run_env.get_junit_xml_base_path(),
        "bundles",
        sanitize_name(SCENARIO_NAME),
        "bundle.md",
    )
    assert os.path.isfile(bundle_md)
    with open(bundle_md, encoding="utf-8") as md_file:
        md = md_file.read()
    assert "## Classification" in md
    assert "product_bug" in md
    assert "Unclassified" not in md


def test_hercules_history_db_env_var_and_reset(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "env_history.db"
    monkeypatch.setenv("HERCULES_HISTORY_DB", f"sqlite:///{db_path}")
    run_db.reset_default_history()
    try:
        history = run_db.get_default_history()
        assert history.db_url == f"sqlite:///{db_path}"
        assert run_db.get_default_history() is history  # cached

        history.start_run()
        assert db_path.is_file()
    finally:
        run_db.reset_default_history()

    assert run_db.get_default_history() is not history  # cache was cleared


def test_canonical_name_from_attributes() -> None:
    assert BrowserLogger._canonical_name_from_attributes({"role": "button", "aria-label": "Submit"}) == "role=button|name=Submit"
    # name attribute wins over id when no aria-label is present.
    assert BrowserLogger._canonical_name_from_attributes({"name": "email", "id": "email-field"}) == "name=email"
    assert BrowserLogger._canonical_name_from_attributes({"id": "only-id"}) == "name=only-id"
    assert BrowserLogger._canonical_name_from_attributes({"role": "dialog"}) == "role=dialog"
    assert BrowserLogger._canonical_name_from_attributes({}) is None
    assert BrowserLogger._canonical_name_from_attributes(None) is None  # type: ignore[arg-type]


def test_record_selectors_persists_into_default_store(monkeypatch, tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "selector_store.json"))
    monkeypatch.setattr("testzeus_hercules.healing.selector_store.get_default_store", lambda: store)

    browser_logger = BrowserLogger()
    browser_logger._record_selectors("role=button|name=Submit", {"xpath": "//button[@id='submit']", "aria": "Submit"})

    entry = store.lookup("role=button|name=Submit")
    assert entry is not None
    assert entry["aria"] == "Submit"
    assert entry["xpath"] == "//button[@id='submit']"
    assert entry["source"] == "observed"
    # The store persists what was recorded (lookup hits stay in memory until an explicit save).
    store.save()
    assert SelectorStore(str(tmp_path / "selector_store.json")).entries() == store.entries()


def test_record_selectors_noop_without_name_or_selectors(monkeypatch, tmp_path) -> None:
    store = SelectorStore(str(tmp_path / "selector_store.json"))
    monkeypatch.setattr("testzeus_hercules.healing.selector_store.get_default_store", lambda: store)

    browser_logger = BrowserLogger()
    browser_logger._record_selectors("", {"xpath": "//x"})
    browser_logger._record_selectors("role=button|name=X", {})

    assert store.entries() == {}
