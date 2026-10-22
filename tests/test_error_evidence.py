"""Offline tests for the redacted error-evidence layer (utils.error_evidence)
and its hook in SimpleHercules._failed_graph_result.

Covered:
1. redaction matrix — bearer tokens, sk- style keys, password/secret/token
   assignments (plain text and JSON shapes) are masked; ordinary text is not;
2. _safe_tool_args — sensitive argument keys are masked recursively, other
   arguments survive;
3. persist_error_evidence — file layout under <log_dir>/errors/, contents
   (category/stage/error type/redacted message/traceback/extra context);
4. the _failed_graph_result hook — the failed result carries a structured
   execution_failure record whose evidence_path exists and whose message is
   redacted, evidence capture failure never masks the original failure, and
   the junit renderer displays the record via build_junit_xml.

No network, no LLM, no browser.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import pytest
from testzeus_hercules.core import simple_hercules as simple_hercules_module
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.error_evidence import (
    _redact_error_text,
    _safe_tool_args,
    persist_error_evidence,
)
from testzeus_hercules.utils.junit_helper import build_junit_xml
from testzeus_hercules.utils.llm_helper import parse_agent_response

# ── 1. redaction matrix ───────────────────────────────────────────────────────


def test_bearer_tokens_are_redacted() -> None:
    redacted = _redact_error_text("request failed with bearer xyz_123-token")
    assert "Bearer ***" in redacted
    assert "xyz_123-token" not in redacted

    # A header-style line collapses to a single marker: the whole value
    # (keyword included) is replaced once the token itself is redacted.
    redacted_header = _redact_error_text("Authorization: Bearer eyJhbGciOi.J9.abc")
    assert "eyJhbGciOi" not in redacted_header
    assert "***" in redacted_header


def test_sk_style_keys_are_redacted() -> None:
    redacted = _redact_error_text("Authentication failed for key sk-abc123def456ghi")
    assert "sk-abc123def456ghi" not in redacted
    assert "sk-***" in redacted


def test_credential_assignments_are_redacted() -> None:
    cases = {
        "password=hunter2": "password=***",
        "db_password: hunter2": "db_password: ***",  # word match inside compound key
        '"token": "super-secret-value"': '"token": "***"',
        "secret: 'shh'": "secret: ***",
        "api_key = abc123": "api_key = ***",
    }
    for text, _ in cases.items():
        redacted = _redact_error_text(text)
        assert "hunter2" not in redacted and "super-secret-value" not in redacted and "shh" not in redacted and "abc123" not in redacted, text


def test_authorization_header_values_are_fully_redacted() -> None:
    # The whole remainder of an authorization-style header line is the
    # credential: "Basic dXNlcjpwYXNz" carries the base64 pair after a space,
    # so redacting only the first token would leak "dXNlcjpwYXNz".
    redacted = _redact_error_text("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in redacted
    assert "Basic" not in redacted
    # The key name survives for readability.
    assert redacted.startswith("Authorization: ***")

    redacted_proxy = _redact_error_text("Proxy-Authorization: NTLM dG9rZW4gdG9rZW4=")
    assert "dG9rZW4" not in redacted_proxy
    assert redacted_proxy.startswith("Proxy-Authorization: ***")

    # Any *-token header style collapses the whole line value too.
    redacted_token = _redact_error_text("X-Auth-Token: abc123 def456")
    assert "abc123" not in redacted_token and "def456" not in redacted_token

    assert _redact_error_text(redacted) == redacted, "redaction must be idempotent"


def test_traceback_prefixed_authorization_header_is_fully_redacted() -> None:
    # Regression: the header pattern used to require the keyword before any
    # colon, so a traceback prefix ("RuntimeError: Authorization: ...") never
    # triggered whole-value redaction and only the scheme word "Basic" was
    # redacted, leaking the base64 credential pair.
    redacted = _redact_error_text("RuntimeError: Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in redacted
    assert "Basic" not in redacted
    # The key name survives for readability.
    assert "Authorization: ***" in redacted
    assert _redact_error_text(redacted) == redacted, "redaction must be idempotent"


def test_json_inline_authorization_value_is_redacted() -> None:
    # The quoted JSON shape is owned by the assignment pass; the header-line
    # pass must redact (not leak) it regardless of which pass wins.
    redacted = _redact_error_text('{"Authorization": "Basic dXNlcjpwYXNz"}')
    assert "dXNlcjpwYXNz" not in redacted
    assert "Authorization" in redacted
    assert _redact_error_text(redacted) == redacted, "redaction must be idempotent"


def test_url_userinfo_is_redacted() -> None:
    redacted = _redact_error_text("could not connect to postgres://admin:s3cr3t@db.host/prod")
    assert "s3cr3t" not in redacted and "admin" not in redacted
    assert "db.host/prod" in redacted
    assert _redact_error_text(redacted) == redacted, "redaction must be idempotent"


def test_private_key_assignments_are_redacted() -> None:
    redacted = _redact_error_text("loaded private_key = MIIEpAIBAAKCAQEA")
    assert "MIIEpAIBAAKCAQEA" not in redacted
    assert "private_key = ***" in redacted
    assert _redact_error_text(redacted) == redacted, "redaction must be idempotent"


def test_redaction_does_not_mangle_ordinary_text() -> None:
    text = "Connection to database failed after 3 retries (timeout=30s, max_tokens=4096)"
    assert _redact_error_text(text) == text


def test_safe_tool_args_masks_sensitive_keys_recursively() -> None:
    args = {
        "url": "https://example.test/login",
        "password": "hunter2",
        "api-key": "abcd1234",
        "headers": {"Authorization": "Bearer xyz", "Accept": "application/json"},
        "nested": {"access_token": "t0k3n", "visible": "keep me"},
        "items": [{"cookie": "session=1"}, "plain"],
    }
    safe = _safe_tool_args(args)

    assert safe["url"] == "https://example.test/login"
    assert safe["password"] == "***"
    assert safe["api-key"] == "***"
    assert safe["headers"]["Authorization"] == "***"
    assert safe["headers"]["Accept"] == "application/json"
    assert safe["nested"]["access_token"] == "***"
    assert safe["nested"]["visible"] == "keep me"
    assert safe["items"][0]["cookie"] == "***"
    assert safe["items"][1] == "plain"
    # The input is not mutated.
    assert args["password"] == "hunter2"


def test_safe_tool_args_passthrough_non_containers() -> None:
    assert _safe_tool_args("plain") == "plain"
    assert _safe_tool_args(42) == 42
    assert _safe_tool_args(None) is None


def test_safe_tool_args_renders_cyclic_references() -> None:
    cyclic = {"name": "root"}
    cyclic["self"] = cyclic
    safe = _safe_tool_args(cyclic)
    assert safe["name"] == "root"
    assert safe["self"] == "<circular>"


def test_persist_error_evidence_survives_cyclic_extra_context(tmp_path) -> None:
    cyclic = {"run": {"command": "verify"}}
    cyclic["run"]["parent"] = cyclic

    evidence_path = persist_error_evidence(str(tmp_path), ValueError("boom"), category="cycle", stage="stage", extra_context=cyclic)

    with open(evidence_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert "<circular>" in content
    assert '"command": "verify"' in content


# ── 2. evidence file contents ─────────────────────────────────────────────────


def test_persist_error_evidence_writes_redacted_file(tmp_path) -> None:
    log_dir = str(tmp_path / "log_files" / "scenario-a")

    try:
        raise RuntimeError("login failed with password=hunter2 and sk-abc123def456")
    except RuntimeError as error:
        evidence_path = persist_error_evidence(
            log_dir,
            error,
            category="agent_chain_failure",
            stage="scenario_execution",
            extra_context={"command": "verify login", "tool_args": {"password": "hunter2", "selector": "#submit"}},
        )

    assert os.path.isfile(evidence_path)
    assert os.path.dirname(evidence_path) == os.path.join(log_dir, "errors")
    assert re.fullmatch(r"agent_chain_failure_[0-9a-f]{12}\.log", os.path.basename(evidence_path))

    with open(evidence_path, "r", encoding="utf-8") as handle:
        content = handle.read()

    assert "category: agent_chain_failure" in content
    assert "stage: scenario_execution" in content
    assert "error_type: RuntimeError" in content
    assert "error_id: " in content
    assert "Traceback (most recent call last)" in content
    assert "RuntimeError: login failed" in content
    # Redaction applies to message, traceback and extra context alike.
    assert "hunter2" not in content
    assert "sk-abc123def456" not in content
    assert "password=***" in content
    assert '"password": "***"' in content
    assert '"selector": "#submit"' in content
    assert '"command": "verify login"' in content


def test_persist_error_evidence_redacts_traceback_prefixed_header(tmp_path) -> None:
    # The traceback section renders the message as "RuntimeError: <message>",
    # so a header-style message leaks through that traceback-prefixed line;
    # the whole value (base64 credential pair included) must be redacted.
    try:
        raise RuntimeError("Authorization: Basic dXNlcjpwYXNz")
    except RuntimeError as error:
        evidence_path = persist_error_evidence(str(tmp_path), error, category="leak", stage="stage")

    with open(evidence_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert "dXNlcjpwYXNz" not in content
    assert "RuntimeError: Authorization: ***" in content


def test_persist_error_evidence_without_traceback_and_context(tmp_path) -> None:
    error = ValueError("plain failure")
    evidence_path = persist_error_evidence(str(tmp_path), error, category="odd category!", stage="stage_x")

    with open(evidence_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert "(no traceback captured)" in content
    assert "extra context:" not in content
    # Awkward category characters are sanitized into the file name.
    assert os.path.basename(evidence_path).startswith("odd_category_")


def test_persist_error_evidence_caps_huge_sections(tmp_path) -> None:
    huge_message = "password=hunter2 " + "x" * (1024 * 1024)
    try:
        raise RuntimeError(huge_message)
    except RuntimeError as error:
        evidence_path = persist_error_evidence(str(tmp_path), error, category="huge", stage="stage")

    # The message appears twice in the evidence (message section + traceback
    # line); both sections are capped so the file stays bounded.
    size = os.path.getsize(evidence_path)
    assert size <= 250_000, f"evidence file grew to {size} bytes"

    with open(evidence_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert "[truncated" in content
    # The included portion is still redacted.
    assert "hunter2" not in content
    assert "password=***" in content


def test_persist_error_evidence_caps_huge_extra_context(tmp_path) -> None:
    blob = {"blob": "y" * (2 * 1024 * 1024), "password": "hunter2"}
    evidence_path = persist_error_evidence(str(tmp_path), ValueError("boom"), category="huge_ctx", stage="stage", extra_context=blob)

    # Message and traceback are capped per section; the rendered extra
    # context must be capped too or a 2MB blob balloons the evidence file.
    size = os.path.getsize(evidence_path)
    assert size <= 250_000, f"evidence file grew to {size} bytes"

    with open(evidence_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert "[truncated" in content
    assert "hunter2" not in content


# ── 3. the _failed_graph_result hook ──────────────────────────────────────────


class _Conf:
    """Minimal conf supplying the per-test log folder used for evidence."""

    def __init__(self, tmp_path) -> None:
        self.tmp_dir = str(tmp_path)

    def get_source_log_folder_path(self, test_id=None) -> str:
        path = os.path.join(self.tmp_dir, "log_files", test_id or "default")
        os.makedirs(path, exist_ok=True)
        return path


def test_failed_graph_result_attaches_execution_failure_with_existing_evidence(tmp_path, monkeypatch) -> None:
    conf = _Conf(tmp_path)
    monkeypatch.setattr(simple_hercules_module, "get_global_conf", lambda: conf)
    hercules = SimpleHercules(stake_id="evidence-hook")

    error = RuntimeError("provider dropped mid-run (token=abc123, key sk-abc123def456)")
    result = hercules._failed_graph_result("verify the checkout flow", error)

    # The graph result stays terminal/failed exactly as before.
    assert result.terminate == "yes"
    parsed = parse_agent_response(result.summary)
    assert parsed["is_passed"] is False
    assert parsed["terminate"] == "yes"
    assert "verify the checkout flow" in (parsed["final_response"] or "")

    record = getattr(result, "execution_failure", None)
    assert isinstance(record, dict)
    assert set(record) == {"category", "stage", "error_type", "message", "evidence_path"}
    assert record["category"] == "agent_chain_failure"
    assert record["stage"] == "scenario_execution"
    assert record["error_type"] == "RuntimeError"
    # The record message and the summary text (final_response/assert_summary) are redacted.
    assert "token=abc123" not in record["message"] and "sk-abc123def456" not in record["message"]
    for text in (parsed["final_response"], parsed["assert_summary"]):
        assert "token=abc123" not in text and "sk-abc123def456" not in text, text
    assert "token=***" in record["message"]
    assert os.path.isfile(record["evidence_path"]), record["evidence_path"]

    # The terminal summary JSON carries the same structured record.
    terminal_payload = json.loads(result.chat_history[-1]["content"])
    assert terminal_payload["execution_failure"]["evidence_path"] == record["evidence_path"]

    # The evidence file itself is redacted.
    with open(record["evidence_path"], "r", encoding="utf-8") as handle:
        evidence_content = handle.read()
    assert "token=abc123" not in evidence_content and "sk-abc123def456" not in evidence_content


def test_failed_graph_result_survives_evidence_capture_failure(tmp_path, monkeypatch) -> None:
    conf = _Conf(tmp_path)
    monkeypatch.setattr(simple_hercules_module, "get_global_conf", lambda: conf)

    def exploding_persist(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(simple_hercules_module, "persist_error_evidence", exploding_persist)
    hercules = SimpleHercules(stake_id="evidence-hook-fail")

    result = hercules._failed_graph_result("failing command", ValueError("original boom"))

    # The original failure still produces a terminal failed result.
    assert result.terminate == "yes"
    assert getattr(result, "execution_failure", None) is None
    terminal_payload = json.loads(result.chat_history[-1]["content"])
    assert terminal_payload["is_passed"] is False
    assert "failing command" in terminal_payload["final_response"]
    assert "original boom" in terminal_payload["final_response"]


# ── 4. junit rendering of the structured record ───────────────────────────────


class _JunitConf:
    def __init__(self, tmp_path) -> None:
        self.junit_base = tmp_path / "junit"
        self.junit_base.mkdir(parents=True, exist_ok=True)

    def get_junit_xml_base_path(self) -> str:
        return str(self.junit_base)

    def get_mode(self) -> str:
        return "prod"


def test_junit_renders_execution_failure_record(tmp_path, monkeypatch) -> None:
    import testzeus_hercules.utils.junit_helper as junit_helper

    monkeypatch.setattr(junit_helper, "get_global_conf", lambda: _JunitConf(tmp_path))

    evidence_path = persist_error_evidence(str(tmp_path), RuntimeError("agent chain collapsed"), category="agent_chain_failure", stage="scenario_execution")
    record = {
        "category": "agent_chain_failure",
        "stage": "scenario_execution",
        "error_type": "RuntimeError",
        "message": "agent chain collapsed",
        "evidence_path": evidence_path,
    }

    path = asyncio.run(
        build_junit_xml(
            {
                "terminate": "yes",
                "is_assert": True,
                "is_passed": False,
                "final_response": "Command failed",
                "assert_summary": "EXPECTED RESULT: the command completes.\nACTUAL RESULT: it failed.",
                "execution_failure": record,
            },
            execution_time=1.0,
            cost_metric={},
            feature="Feature: Evidence",
            scenario="Scenario: harness failure",
        )
    )

    cases = parse_junit_testcases(path)
    assert len(cases) == 1
    case = cases[0]
    assert case["status"] == "failed"
    message = case["failure_message"] or ""
    assert "Execution failure: agent chain collapsed" in message
    assert "category=agent_chain_failure" in message
    assert "stage=scenario_execution" in message
    assert "type=RuntimeError" in message
    assert f"evidence={evidence_path}" in message
