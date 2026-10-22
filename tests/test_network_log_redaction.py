"""Unit tests for network-log redaction in testzeus_hercules.core.playwright_manager.

log_request/log_response write request/response headers and post data into
network_logs.json, which is copied verbatim into shareable failure bundles.
Sensitive headers (Authorization, Cookie, tokens, API keys) and secret-bearing
post-data fields must be redacted at the source. No browser is launched.
"""

import asyncio
import json

from testzeus_hercules.core.playwright_manager import (
    REDACTED_PLACEHOLDER,
    PlaywrightManager,
    _redact_post_data,
    _redact_sensitive_headers,
)

# ── header redaction ──────────────────────────────────────────────────────────


def test_sensitive_headers_are_redacted_case_insensitively() -> None:
    headers = {
        "authorization": "Bearer super-secret-token",
        "Authorization": "Basic dXNlcjpwYXNz",
        "COOKIE": "session=abcdef",
        "set-cookie": "sid=123456",
        "proxy-authorization": "Basic qm90-ppc",
        "X-Api-Key": "key123",
        "x-api_key": "key456",
        "Session-Token": "tok789",
        "client-secret": "shhh",
    }
    redacted = _redact_sensitive_headers(headers)
    for key in headers:
        assert redacted[key] == REDACTED_PLACEHOLDER


def test_benign_headers_pass_through_unmodified() -> None:
    headers = {"content-type": "application/json", "user-agent": "hercules", "accept-language": "en"}
    redacted = _redact_sensitive_headers(headers)
    assert redacted == headers


def test_header_redaction_does_not_mutate_input() -> None:
    headers = {"authorization": "Bearer x", "accept": "text/html"}
    redacted = _redact_sensitive_headers(headers)
    assert headers["authorization"] == "Bearer x"
    assert redacted["authorization"] == REDACTED_PLACEHOLDER


def test_non_dict_headers_returned_as_is() -> None:
    assert _redact_sensitive_headers(None) is None
    assert _redact_sensitive_headers(["a", "b"]) == ["a", "b"]


# ── post-data redaction ───────────────────────────────────────────────────────


def test_json_post_data_redacts_sensitive_keys_at_any_name_case() -> None:
    body = json.dumps({"Username": "alice", "PASSword": "hunter2", "AuthToken": "tok", "role": "admin"})
    redacted = _redact_post_data(body, "application/json")
    parsed = json.loads(redacted)
    assert parsed["Username"] == "alice"
    assert parsed["PASSword"] == REDACTED_PLACEHOLDER
    assert parsed["AuthToken"] == REDACTED_PLACEHOLDER
    assert parsed["role"] == "admin"
    assert "hunter2" not in redacted


def test_json_post_data_redacts_one_nesting_level() -> None:
    body = json.dumps({"credentials": {"password": "hunter2", "user": "alice"}, "note": "ok"})
    redacted = _redact_post_data(body, "application/json")
    parsed = json.loads(redacted)
    assert parsed["credentials"]["password"] == REDACTED_PLACEHOLDER
    assert parsed["credentials"]["user"] == "alice"
    assert parsed["note"] == "ok"


def test_json_post_data_redaction_is_depth_bounded() -> None:
    # Two levels deep is beyond the bounded effort; values there stay untouched.
    body = json.dumps({"a": {"b": {"password": "deep-secret"}}})
    parsed = json.loads(_redact_post_data(body, "application/json"))
    assert parsed["a"]["b"]["password"] == "deep-secret"


def test_json_post_data_redacts_sensitive_keys_inside_nested_lists() -> None:
    body = json.dumps({"items": [{"name": "x", "api_key": "k"}], "count": 1})
    parsed = json.loads(_redact_post_data(body, "application/json"))
    assert parsed["items"][0]["name"] == "x"
    assert parsed["items"][0]["api_key"] == REDACTED_PLACEHOLDER


def test_json_array_post_data_redacts_sensitive_keys() -> None:
    """A top-level JSON array body ([{"password": "..."}]) must not bypass redaction."""
    body = json.dumps([{"username": "alice", "password": "hunter2"}, {"api_key": "k9"}, {"note": "ok"}])
    redacted = _redact_post_data(body, "application/json")
    parsed = json.loads(redacted)
    assert isinstance(parsed, list)
    assert parsed[0]["username"] == "alice"
    assert parsed[0]["password"] == REDACTED_PLACEHOLDER
    assert parsed[1]["api_key"] == REDACTED_PLACEHOLDER
    assert parsed[2] == {"note": "ok"}
    assert "hunter2" not in redacted


def test_json_array_post_data_non_dict_elements_pass_through() -> None:
    body = '[1, "two", null, {"token": "t"}]'
    parsed = json.loads(_redact_post_data(body, "application/json"))
    assert parsed[:3] == [1, "two", None]
    assert parsed[3]["token"] == REDACTED_PLACEHOLDER


def test_json_array_post_data_redaction_is_depth_bounded() -> None:
    # Same bounds as objects: redacted at element level + one nesting level only.
    body = json.dumps([{"credentials": {"password": "hunter2"}}, {"deep": {"deeper": {"password": "deep-secret"}}}])
    parsed = json.loads(_redact_post_data(body, "application/json"))
    assert parsed[0]["credentials"]["password"] == REDACTED_PLACEHOLDER
    assert parsed[1]["deep"]["deeper"]["password"] == "deep-secret"


def test_broken_array_json_post_data_returned_unchanged() -> None:
    broken = "[not-json"
    assert _redact_post_data(broken, "application/json") == broken


def test_urlencoded_form_post_data_redacts_password_like_fields() -> None:
    body = "username=alice&password=hunter2&confirm_password=hunter2&keep=1"
    redacted = _redact_post_data(body, "application/x-www-form-urlencoded")
    assert "hunter2" not in redacted
    assert "username=alice" in redacted
    assert "keep=1" in redacted
    assert "password=%5BREDACTED%5D" in redacted or "password=[REDACTED]" in redacted


def test_multipart_form_post_data_redacts_password_fields() -> None:
    body = (
        "--BOUNDARY\r\n"
        'Content-Disposition: form-data; name="username"\r\n\r\n'
        "alice\r\n"
        "--BOUNDARY\r\n"
        'Content-Disposition: form-data; name="password"\r\n\r\n'
        "hunter2\r\n"
        "--BOUNDARY--\r\n"
    )
    redacted = _redact_post_data(body, "multipart/form-data; boundary=BOUNDARY")
    assert "hunter2" not in redacted
    assert "alice" in redacted
    assert REDACTED_PLACEHOLDER in redacted


def test_non_secret_post_data_returned_unchanged() -> None:
    body = json.dumps({"query": "bikes", "limit": 10})
    assert _redact_post_data(body, "application/json") == body
    assert _redact_post_data("plain text body", "text/plain") == "plain text body"
    assert _redact_post_data(None, "application/json") is None
    assert _redact_post_data("", "application/json") == ""
    # Invalid JSON starting with "{" must come back untouched, never raise.
    broken = "{not-json"
    assert _redact_post_data(broken, "application/json") == broken


# ── end-to-end through log_request/log_response ───────────────────────────────


class _FakeRequest:
    method = "POST"
    url = "https://example.test/login"
    headers = {"content-type": "application/json", "authorization": "Bearer abc", "accept": "application/json"}
    post_data = json.dumps({"username": "alice", "password": "hunter2"})


class _FakeResponse:
    status = 200
    url = "https://example.test/login"
    headers = {"content-type": "application/json", "set-cookie": "sid=secret-sid"}


def _bare_manager(captured: list) -> PlaywrightManager:
    """Instance without __init__ (no config/browser); log writes are captured.

    object.__new__ bypasses the singleton __new__ so no class-level state is
    touched and no config/browser stack is initialized.
    """

    async def fake_write(entry, log_file):
        captured.append(entry)

    manager = object.__new__(PlaywrightManager)
    manager._write_log_entry_to_file = fake_write
    manager.request_response_log_file = "unused.jsonl"
    return manager


def test_log_request_redacts_headers_and_post_data() -> None:
    captured: list = []
    manager = _bare_manager(captured)

    async def drive():
        # log_request is sync and uses asyncio.ensure_future, so it must run
        # inside a live loop; yield once so the scheduled write task executes.
        manager.log_request(_FakeRequest())
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert len(captured) == 1
    entry = captured[0]
    assert entry["headers"]["authorization"] == REDACTED_PLACEHOLDER
    assert entry["headers"]["accept"] == "application/json"
    body = json.loads(entry["post_data"])
    assert body["username"] == "alice"
    assert body["password"] == REDACTED_PLACEHOLDER


def test_log_response_redacts_set_cookie() -> None:
    captured: list = []
    manager = _bare_manager(captured)

    async def drive():
        manager.log_response(_FakeResponse())
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert len(captured) == 1
    entry = captured[0]
    assert entry["headers"]["set-cookie"] == REDACTED_PLACEHOLDER
    assert entry["headers"]["content-type"] == "application/json"
