"""Offline tests for the observer invariants (core.invariants) and their
SimpleHercules hooks.

Covered:
1. severity classification from the JSONL proof artifacts — HTTP 5xx and
   uncaught frontend exceptions are BINDING; HTTP 4xx and other console
   errors are NOTICE; anything not judgeable is skipped ("rather miss a red
   than raise a false red");
2. incremental polling — byte offsets persist across polls, a half-written
   trailing line stays pending until complete, a truncated/recreated file is
   re-read, corrupt lines are skipped;
3. the assertion-ledger line format written for findings (same uniform shape
   the deterministic assertion tools emit) and ledger-write containment;
4. the graph hooks — the post-step poll records findings, binding findings
   mark the final result failed while notice findings never do, and a poll
   failure can never break the run.

No network, no LLM, no browser: proof artifacts are tmp JSONL files and the
LLMs are scripted stubs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core import invariants as invariants_module
from testzeus_hercules.core import simple_hercules as simple_hercules_module
from testzeus_hercules.core.invariants import (
    poll_invariants,
    record_invariant_findings,
    reset_invariant_offsets,
)
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils.llm_helper import parse_agent_response

TEST_ID = "invariant-observer"


@pytest.fixture(autouse=True)
def proof_env(tmp_path, monkeypatch):
    """Point the global conf at tmp_path for proof + ledger writes."""
    reset_invariant_offsets()
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id(TEST_ID)
    yield conf
    reset_invariant_offsets()
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


def _proof_dir(conf) -> str:
    path = conf.get_proof_path(TEST_ID)
    os.makedirs(path, exist_ok=True)
    return path


def _write_jsonl(path: str, entries: list[dict[str, Any]], trailing_newline: bool = True) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + ("\n" if trailing_newline else ""))


def _ledger_lines(conf) -> list[dict[str, Any]]:
    log_path = os.path.join(conf.get_proof_path(TEST_ID), "assertion_logs.log")
    if not os.path.isfile(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ── 1. severity classification ────────────────────────────────────────────────


def test_http_5xx_is_binding_and_4xx_is_notice(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(
        os.path.join(proofs, "network_logs.json"),
        [
            {"type": "request", "timestamp": 1.0, "method": "GET", "url": "https://api.test/ping"},
            {"type": "response", "timestamp": 2.0, "status": 502, "url": "https://api.test/ping"},
            {"type": "response", "timestamp": 3.0, "status": 404, "url": "https://api.test/missing"},
            {"type": "response", "timestamp": 4.0, "status": 200, "url": "https://api.test/ok"},
            {"type": "response", "timestamp": 5.0, "url": "https://api.test/nostatus"},
        ],
    )

    findings = poll_invariants(TEST_ID, proof_path=proofs)

    assert len(findings["binding"]) == 1
    binding = findings["binding"][0]
    assert binding["kind"] == "http_5xx"
    assert binding["status"] == 502
    assert "502" in binding["detail"] and "https://api.test/ping" in binding["detail"]
    assert binding["source"] == "network_logs.json"
    assert isinstance(binding["offset"], int)

    assert len(findings["notice"]) == 1
    notice = findings["notice"][0]
    assert notice["kind"] == "http_4xx"
    assert notice["status"] == 404


def test_uncaught_console_exceptions_are_binding_other_console_errors_are_notice(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(
        os.path.join(proofs, "console_logs.json"),
        [
            {"type": "console", "level": "error", "timestamp": 1.0, "text": "Uncaught TypeError: Cannot read properties of null (reading 'map')"},
            {"type": "console", "level": "error", "timestamp": 2.0, "text": "Failed to load resource: the server responded with a status of 400"},
            {"type": "console", "level": "warning", "timestamp": 3.0, "text": "deprecated API used"},
            {"type": "console", "level": "info", "timestamp": 4.0, "text": "app booted"},
        ],
    )

    findings = poll_invariants(TEST_ID, proof_path=proofs)

    assert len(findings["binding"]) == 1
    binding = findings["binding"][0]
    assert binding["kind"] == "uncaught_exception"
    assert "Uncaught TypeError" in binding["detail"]
    assert binding["source"] == "console_logs.json"

    assert len(findings["notice"]) == 1
    assert findings["notice"][0]["kind"] == "console_error"


def test_uncaught_marker_beyond_head_of_long_text_is_binding(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(
        os.path.join(proofs, "console_logs.json"),
        [
            {"type": "console", "level": "error", "timestamp": 1.0, "text": "x" * 250 + " Uncaught TypeError: boom"},
            {"type": "console", "level": "error", "timestamp": 2.0, "text": "y" * 300},
        ],
    )

    findings = poll_invariants(TEST_ID, proof_path=proofs)

    # The classification markers are matched against the full text, so an
    # exception past the head of a long line is still BINDING...
    assert len(findings["binding"]) == 1
    binding = findings["binding"][0]
    assert binding["kind"] == "uncaught_exception"
    assert "Uncaught TypeError" in binding["detail"]
    # ...while the stored detail stays a bounded snippet around the marker.
    assert len(binding["detail"]) <= 220

    assert len(findings["notice"]) == 1
    assert findings["notice"][0]["kind"] == "console_error"
    assert findings["notice"][0]["detail"] == "y" * 200


def test_unjudgeable_entries_are_skipped(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(os.path.join(proofs, "network_logs.json"), [{"type": "response", "status": "500", "url": "https://api.test/x"}])
    _write_jsonl(
        os.path.join(proofs, "console_logs.json"),
        [{"level": "warning", "text": "deprecated API used"}, {"unexpected": "shape"}],
    )

    findings = poll_invariants(TEST_ID, proof_path=proofs)

    # A string status cannot be judged confidently — no false red, no notice.
    assert findings == {"binding": [], "notice": []}


def test_corrupt_lines_are_skipped(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "network_logs.json")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{this is not json\n")
        handle.write(json.dumps({"type": "response", "status": 500, "url": "https://api.test/after"}) + "\n")

    findings = poll_invariants(TEST_ID, proof_path=proofs)

    assert len(findings["binding"]) == 1
    assert findings["binding"][0]["status"] == 500


# ── 2. incremental polling ────────────────────────────────────────────────────


def test_polls_are_incremental_by_byte_offset(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "network_logs.json")
    _write_jsonl(path, [{"type": "response", "status": 500, "url": "https://api.test/first"}])

    first = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["status"] for finding in first["binding"]] == [500]

    # Nothing new — the second poll reports no findings again.
    assert poll_invariants(TEST_ID, proof_path=proofs) == {"binding": [], "notice": []}

    # New lines appended later are picked up exactly once.
    _write_jsonl(
        path,
        [
            {"type": "response", "status": 404, "url": "https://api.test/later"},
            {"type": "response", "status": 503, "url": "https://api.test/later2"},
        ],
    )
    third = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["status"] for finding in third["binding"]] == [503]
    assert [finding["status"] for finding in third["notice"]] == [404]

    assert poll_invariants(TEST_ID, proof_path=proofs) == {"binding": [], "notice": []}


def test_half_written_trailing_line_stays_pending(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "console_logs.json")
    _write_jsonl(path, [{"type": "console", "level": "error", "text": "Uncaught RangeError: bad"}], trailing_newline=False)

    # No trailing newline yet: the line is incomplete and must not be judged.
    assert poll_invariants(TEST_ID, proof_path=proofs) == {"binding": [], "notice": []}

    # The producer finishes the line — the next poll sees it.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n")
    findings = poll_invariants(TEST_ID, proof_path=proofs)
    assert len(findings["binding"]) == 1
    assert findings["binding"][0]["kind"] == "uncaught_exception"


def test_truncated_file_is_reread_from_start(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "network_logs.json")
    _write_jsonl(path, [{"type": "response", "status": 500, "url": "https://api.test/old"}] * 5)
    assert len(poll_invariants(TEST_ID, proof_path=proofs)["binding"]) == 5

    # The producer truncated/recreated the file (new run for the same id).
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "response", "status": 500, "url": "https://api.test/new"}) + "\n")

    findings = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["url"] for finding in findings["binding"]] == ["https://api.test/new"]


def test_same_size_recreation_is_reread(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "network_logs.json")
    first_entry = {"type": "response", "status": 500, "url": "https://api.test/old"}
    _write_jsonl(path, [first_entry])
    assert [finding["url"] for finding in poll_invariants(TEST_ID, proof_path=proofs)["binding"]] == ["https://api.test/old"]

    # The producer rewrites the file with a different entry of exactly the
    # same byte length: the stored offset matches the new size, but the new
    # content must still be judged (file identity changed).
    second_entry = {"type": "response", "status": 503, "url": "https://api.test/nww"}
    first_line = json.dumps(first_entry) + "\n"
    second_line = json.dumps(second_entry) + "\n"
    assert len(second_line) == len(first_line)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(second_line)

    findings = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["url"] for finding in findings["binding"]] == ["https://api.test/nww"]
    assert [finding["status"] for finding in findings["binding"]] == [503]


def test_recreated_larger_file_is_fully_judged_once(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "network_logs.json")
    old_entry = {"type": "response", "status": 500, "url": "https://api.test/" + "a" * 200}
    _write_jsonl(path, [old_entry])
    assert len(poll_invariants(TEST_ID, proof_path=proofs)["binding"]) == 1
    stored_offset = len(json.dumps(old_entry)) + 1

    # The file is recreated (removed and written anew) with four entries whose
    # total size exceeds the stored offset: resuming mid-file would skip the
    # earlier recreated lines, so the recreation must re-read from the start.
    new_entries = [{"type": "response", "status": 500 + i, "url": f"https://api.test/new{i}"} for i in range(4)]
    os.unlink(path)
    with open(path, "w", encoding="utf-8") as handle:
        for entry in new_entries:
            handle.write(json.dumps(entry) + "\n")
    assert os.path.getsize(path) > stored_offset

    findings = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["url"] for finding in findings["binding"]] == [f"https://api.test/new{i}" for i in range(4)]
    # Nothing is re-reported on the next poll.
    assert poll_invariants(TEST_ID, proof_path=proofs) == {"binding": [], "notice": []}


def test_file_touched_without_content_change_is_reread_once(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    path = os.path.join(proofs, "console_logs.json")
    _write_jsonl(path, [{"type": "console", "level": "error", "text": "Uncaught TypeError: boom"}])
    assert len(poll_invariants(TEST_ID, proof_path=proofs)["binding"]) == 1

    # Touch the file: mtime changes (tracked identity), bytes stay identical.
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    # Documented actual behavior: an identity change re-reads the file, so the
    # identical content is reported once more. That is honest at the observer
    # level (a touched file might equally have been rewritten) and downstream
    # accumulators dedupe; a missed red would not be recoverable.
    findings = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["kind"] for finding in findings["binding"]] == ["uncaught_exception"]

    # After the re-read, appends are picked up exactly once again.
    _write_jsonl(path, [{"type": "console", "level": "error", "text": "some other app error"}])
    again = poll_invariants(TEST_ID, proof_path=proofs)
    assert [finding["kind"] for finding in again["notice"]] == ["console_error"]
    assert again["binding"] == []


def test_missing_proof_files_are_quiet(proof_env, tmp_path) -> None:
    findings = poll_invariants(TEST_ID, proof_path=str(tmp_path / "proofs" / "nowhere"))
    assert findings == {"binding": [], "notice": []}


def test_offsets_are_scoped_per_test_id(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(os.path.join(proofs, "network_logs.json"), [{"type": "response", "status": 500, "url": "https://api.test/x"}])

    assert len(poll_invariants(TEST_ID, proof_path=proofs)["binding"]) == 1
    # A different scenario id starts from its own offset zero...
    assert len(poll_invariants("other-scenario", proof_path=proofs)["binding"]) == 1
    # ...while the original id still sees nothing new.
    assert poll_invariants(TEST_ID, proof_path=proofs) == {"binding": [], "notice": []}


# ── 3. ledger line format ─────────────────────────────────────────────────────


def test_ledger_lines_use_the_uniform_assertion_format(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    findings = {
        "binding": [{"kind": "http_5xx", "detail": "HTTP 500 response from https://api.test/x", "status": 500, "url": "https://api.test/x", "source": "network_logs.json", "offset": 42}],
        "notice": [{"kind": "console_error", "detail": "something logged an error", "text": "something logged an error", "source": "console_logs.json", "offset": 7}],
    }

    record_invariant_findings(TEST_ID, findings)

    lines = _ledger_lines(proof_env)
    assert len(lines) == 2

    binding = lines[0]
    assert binding["source"] == "invariant"
    assert binding["test_id"] == TEST_ID
    assert re.fullmatch(r"[0-9a-f]{12}", binding["subject"])
    assert binding["path"] == "https://api.test/x"
    assert binding["operator"] == "http_5xx"
    assert binding["expected"] == "no HTTP 5xx responses"
    assert binding["actual"] == "HTTP 500 response from https://api.test/x"
    assert binding["verdict"] == "FAILED"
    assert "network_logs.json" in binding["reason"] and "42" in binding["reason"]

    notice = lines[1]
    assert notice["verdict"] == "notice"
    assert notice["operator"] == "console_error"
    assert notice["expected"] == "no console errors"


def test_ledger_write_failure_is_swallowed(proof_env, tmp_path, monkeypatch) -> None:
    def exploding_logger(_line: str) -> None:
        raise OSError("ledger unavailable")

    monkeypatch.setattr(invariants_module, "assertion_logger", exploding_logger)
    # Must not raise: the observer never breaks the run it watches.
    record_invariant_findings(
        TEST_ID,
        {"binding": [{"kind": "http_5xx", "detail": "HTTP 500", "url": "https://api.test/x", "source": "network_logs.json", "offset": 1}], "notice": []},
    )


# ── 4. graph hooks ────────────────────────────────────────────────────────────


class _PlannerLLMStub:
    """Planner LLM stub playing scripted payloads."""

    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls += 1
        return AIMessage(content=self.payloads.pop(0))


class _NavLLM:
    """Nav-agent LLM stub that terminates immediately."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> "_NavLLM":
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        return AIMessage(content="current_output: done\n##TERMINATE TASK##")


def _make_hercules(planner_payloads: list[str]) -> SimpleHercules:
    hercules = SimpleHercules(stake_id=TEST_ID, planner_max_chat_round=10, browser_nav_max_chat_round=2)
    agents: dict[str, Any] = {
        "planner_agent": SimpleNamespace(
            system_message="planner system",
            llm=_PlannerLLMStub(planner_payloads),
            on_planner_message=lambda _content: None,
        )
    }
    for name in ("browser_nav_agent", "api_nav_agent", "sec_nav_agent", "sql_nav_agent", "time_keeper_nav_agent", "mcp_nav_agent", "executor_nav_agent"):
        agents[name] = SimpleNamespace(agent_name=name, system_message=f"{name} system", llm=_NavLLM(), tools=[])
    hercules.agents_map = agents
    hercules._graph = hercules._build_graph()
    return hercules


_STEP = json.dumps({"next_step": "open the app", "target_helper": "browser", "terminate": "no"})
_PASS = json.dumps({"terminate": "yes", "final_response": "all good", "is_assert": True, "is_passed": True, "assert_summary": "verified"})
_FAIL = json.dumps({"terminate": "yes", "final_response": "assertion failed", "is_assert": True, "is_passed": False, "assert_summary": "expected x got y"})


def test_binding_finding_marks_a_claimed_pass_as_failed(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(os.path.join(proofs, "network_logs.json"), [{"type": "response", "status": 500, "url": "https://api.test/orders"}])

    result = asyncio.run(_make_hercules([_STEP, _PASS]).process_command("check the orders page"))

    assert result is not None and result.terminate == "yes"
    parsed = parse_agent_response(result.summary)
    # The planner claimed a pass; the binding invariant overrules it.
    assert parsed["is_passed"] is False
    assert "Observer invariants flagged 1 binding finding(s)" in (parsed["assert_summary"] or "")
    assert "HTTP 500 response from https://api.test/orders" in (parsed["assert_summary"] or "")
    assert "Observer invariants flagged" in (parsed["final_response"] or "")

    # The terminal summary JSON is consistent with the verdict.
    terminal = json.loads(result.chat_history[-1]["content"])
    assert terminal["is_passed"] is False and terminal["terminate"] == "yes"

    # The finding was recorded in the assertion ledger as FAILED.
    lines = _ledger_lines(proof_env)
    assert any(line["source"] == "invariant" and line["verdict"] == "FAILED" for line in lines)


def test_binding_finding_appends_to_an_already_failed_result(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(os.path.join(proofs, "console_logs.json"), [{"type": "console", "level": "error", "text": "Uncaught ReferenceError: x is not defined"}])

    result = asyncio.run(_make_hercules([_STEP, _FAIL]).process_command("check the page"))

    parsed = parse_agent_response(result.summary)
    assert parsed["is_passed"] is False
    # The planner's own reason is preserved and the invariant line appended.
    assert parsed["assert_summary"].startswith("expected x got y")
    assert "Observer invariants flagged 1 binding finding(s)" in parsed["assert_summary"]


def test_notice_findings_never_fail_the_scenario(proof_env, tmp_path) -> None:
    proofs = _proof_dir(proof_env)
    _write_jsonl(
        os.path.join(proofs, "network_logs.json"),
        [
            {"type": "response", "status": 404, "url": "https://api.test/missing"},
        ],
    )
    _write_jsonl(os.path.join(proofs, "console_logs.json"), [{"type": "console", "level": "error", "text": "some app error"}])

    result = asyncio.run(_make_hercules([_STEP, _PASS]).process_command("exercise the 404 path"))

    parsed = parse_agent_response(result.summary)
    assert parsed["is_passed"] is True, "notices are recorded but never fail the scenario"

    lines = _ledger_lines(proof_env)
    assert any(line["source"] == "invariant" and line["verdict"] == "notice" for line in lines)
    assert not any(line["source"] == "invariant" and line["verdict"] == "FAILED" for line in lines)


def test_poll_failure_is_swallowed_and_never_breaks_the_run(proof_env, tmp_path, monkeypatch) -> None:
    def exploding_poll(*args, **kwargs):
        raise RuntimeError("proof folder vanished")

    monkeypatch.setattr(simple_hercules_module, "poll_invariants", exploding_poll)

    result = asyncio.run(_make_hercules([_STEP, _PASS]).process_command("check things normally"))

    assert result is not None
    parsed = parse_agent_response(result.summary)
    assert parsed["is_passed"] is True, "a failed observer poll must not change the verdict"
    assert "Observer invariants" not in (parsed["assert_summary"] or "")


def test_run_without_any_proof_artifacts_stays_clean(proof_env, tmp_path) -> None:
    _proof_dir(proof_env)  # empty proofs dir, no JSONL files

    result = asyncio.run(_make_hercules([_STEP, _PASS]).process_command("check a healthy app"))

    parsed = parse_agent_response(result.summary)
    assert parsed["is_passed"] is True
    assert _ledger_lines(proof_env) == []
