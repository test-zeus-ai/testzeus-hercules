"""Redaction of the scanner's command line and output (scan.redact); ported from the v1 scan tests, behaviour unchanged."""

from __future__ import annotations

import time

import pytest

from testzeus_hercules.scan.redact import credential_header_values, redact_command_for_log, redact_tool_output

SECRET = "hunter2-secret"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"


@pytest.mark.parametrize(
    "payload",
    ["token" * 200_000, "a-" * 500_000, "x: " + " " * 1_000_000, "token:[REDACTED]" * 60_000, ("Authorization: a" + "b" * 60 + "\n") * 15_000],
    ids=["unbroken-token", "dashes", "whitespace", "redacted-colons", "many-headers"],
)
def test_redaction_is_linear_on_pathological_input(payload: str) -> None:
    for xml in (False, True):
        started = time.monotonic()
        redact_tool_output(payload, xml=xml)
        assert time.monotonic() - started < 3.0, "redaction must not be quadratic"


def test_xml_redaction_is_linear_on_a_long_line_with_many_matches() -> None:
    payload = 'token:x" ' * 250_000
    started = time.monotonic()
    redacted = redact_tool_output(payload, xml=True)
    assert time.monotonic() - started < 2.0, "xml-mode redaction must not be quadratic"
    assert "token:[REDACTED]" in redacted and "token:x" not in redacted


NUCLEI_FINDINGS = [
    '[jwt-token-exposure:bearer] [http] [high] https://api.example.com/v1/users ["eyJhbGciOiJIUzI1NiJ9.e30.abc"]',
    "[cookies-without-secure:cookie] [http] [info] https://api.example.com/login",
    "[exposed-api-secret:regex] [http] [medium] https://api.example.com:8443/v2/token:refresh",
    "[generic-secret-detect] [http] [low] https://api.example.com/",
]


@pytest.mark.parametrize("line", NUCLEI_FINDINGS)
def test_finding_lines_with_token_like_template_ids_keep_severity_and_url(line: str) -> None:
    # A template or matcher id that merely contains "token"/"secret"/"cookie" is not a header.
    assert redact_tool_output(line) == line
    assert redact_tool_output(line, xml=True) == line


def test_header_values_are_redacted_next_to_a_finding_line() -> None:
    text = f"[jwt-token-exposure:bearer] [http] [high] https://api.example.com/v1\n> Authorization: Bearer {SECRET}\nfailed: sent Authorization: Bearer {SECRET} to target"
    redacted = redact_tool_output(text)
    assert SECRET not in redacted
    assert redacted.splitlines()[0] == text.splitlines()[0]


@pytest.mark.parametrize("name", ["X-Auth-Tok​en", "Author‍ization", "Coo\x00kie", "X-Api-­Key", "Authori﻿zation"])
def test_invisible_characters_inside_a_header_name_do_not_evade_redaction(name: str) -> None:
    assert SECRET not in redact_tool_output(f"> {name}: {SECRET}")


def test_known_secret_values_and_url_credentials_are_masked_anywhere() -> None:
    text = f"raw {SECRET} and https://user:pw-123456@app.test/a?token=abc123&page=2"
    redacted = redact_tool_output(text, [SECRET])
    assert SECRET not in redacted and "pw-123456" not in redacted and "abc123" not in redacted
    assert "page=2" in redacted


def test_command_log_masks_header_values_and_url_credentials_but_keeps_the_rest() -> None:
    command = ["nuclei", "-tags", "cve", "-H", f"Authorization: Bearer {JWT}", "-H", "X-Custom: plain-secret", "-u", "https://user:hunter2@example.test/path?token=abc123"]
    shown = redact_command_for_log(command)
    assert JWT not in shown and "plain-secret" not in shown and "hunter2" not in shown and "abc123" not in shown
    assert "Authorization: [REDACTED]" in shown and "X-Custom: [REDACTED]" in shown and "-tags cve" in shown
    assert command[4] == f"Authorization: Bearer {JWT}"  # the command itself is never changed


def test_command_log_handles_a_header_without_colon_and_a_trailing_flag() -> None:
    assert redact_command_for_log(["nuclei", "-H", "opaquevalue"]) == "nuclei -H [REDACTED]"
    assert redact_command_for_log(["nuclei", "-H"]) == "nuclei -H"


def test_only_credential_headers_count_as_secrets() -> None:
    headers = [("Accept", "application/json"), ("User-Agent", "Mozilla/5.0 (X11; Linux)"), ("Authorization", f"Bearer {SECRET}"), ("X-Api-Key", "k-12345")]
    assert credential_header_values(headers) == [f"Bearer {SECRET}", SECRET, "k-12345"]
