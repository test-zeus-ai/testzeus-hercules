"""Unit tests for the shared redaction helpers and the log sites that use them.

Covers testzeus_hercules.utils.redact (URL userinfo strip + header/post-data
secret redaction, shared by orchestrator.session, core.playwright_manager and
core.tools.api_calls), the api_calls api_logs.log writer, the playwright
network jsonl writer and the BROWSER_COOKIES log line in config.py. No
browser, network or LLM is involved.
"""

import asyncio
import json
import logging

import httpx
from testzeus_hercules.config import NonSingletonConfigManager
from testzeus_hercules.core.playwright_manager import (
    REDACTED_PLACEHOLDER,
    PlaywrightManager,
)
from testzeus_hercules.core.tools import api_calls
from testzeus_hercules.utils.redact import (
    _redact_post_data,
    _redact_sensitive_headers,
    redact_url,
)

# ── redact_url (moved from orchestrator.session) ────────────────────────────


def test_redact_url_strips_userinfo() -> None:
    assert redact_url("https://user:tok3n@secret-host.example.com/api?x=1#frag") == "https://secret-host.example.com/api?x=1#frag"
    assert redact_url("https://user:pass@[::1]:8443/x") == "https://[::1]:8443/x"


def test_redact_url_keeps_urls_without_userinfo() -> None:
    assert redact_url("https://plain-host.example.com/a/b?q=1") == "https://plain-host.example.com/a/b?q=1"
    assert redact_url("") == ""


def test_redact_url_redacts_sensitive_query_values() -> None:
    assert redact_url("https://host.example.com/api?token=abc&page=1") == "https://host.example.com/api?token=[REDACTED]&page=1"
    assert redact_url("https://host.example.com/api?API_KEY=abc&x=1") == "https://host.example.com/api?API_KEY=[REDACTED]&x=1"
    # URL-encoded values are replaced wholesale; non-sensitive params stay verbatim
    assert redact_url("https://host.example.com/api?access_token=a%20b&next=%2Flogin") == "https://host.example.com/api?access_token=[REDACTED]&next=%2Flogin"
    assert "sekret" not in redact_url("https://host.example.com/api?Signature=sekret&signature=also-sekret")


def test_redact_url_strips_userinfo_and_redacts_query_together() -> None:
    assert redact_url("https://user:pass@host.example.com/login?password=hunter2&page=1") == "https://host.example.com/login?password=[REDACTED]&page=1"


def test_redact_url_fallback_redacts_query_on_unparsable_urls() -> None:
    redacted = redact_url("https://user:pass@[::1/path?token=sekret&page=1")
    assert "user:pass" not in redacted
    assert "sekret" not in redacted
    assert "token=[REDACTED]" in redacted
    assert "page=1" in redacted


def test_redact_url_malformed_urls_never_leak_userinfo() -> None:
    for url in [
        "https://user:pass@[::1",
        "https://user:pass@[::1/path?x=1#frag",
        "https://user:pass@\uff03host",  # fullwidth '#': urlsplit raises under NFKC
    ]:
        redacted = redact_url(url)
        assert "user:pass" not in redacted
        assert redacted != url


# ── header / post-data helpers (moved from core.playwright_manager) ─────────


def test_sensitive_headers_basic_bearer_and_api_key_are_redacted() -> None:
    headers = {
        "Authorization": "Basic dXNlcjpwYXNz",
        "authorization": "Bearer super-secret-token",
        "x-api-key": "key123",
        "x-api_key": "key456",
        "content-type": "application/json",
    }
    redacted = _redact_sensitive_headers(headers)
    assert redacted["Authorization"] == REDACTED_PLACEHOLDER
    assert redacted["authorization"] == REDACTED_PLACEHOLDER
    assert redacted["x-api-key"] == REDACTED_PLACEHOLDER
    assert redacted["x-api_key"] == REDACTED_PLACEHOLDER
    assert redacted["content-type"] == "application/json"


def test_password_json_body_is_redacted() -> None:
    body = json.dumps({"username": "alice", "password": "hunter2", "role": "admin"})
    redacted = _redact_post_data(body, "application/json")
    parsed = json.loads(redacted)
    assert parsed["username"] == "alice"
    assert parsed["password"] == REDACTED_PLACEHOLDER
    assert "hunter2" not in redacted


def test_password_urlencoded_body_is_redacted() -> None:
    redacted = _redact_post_data("username=alice&password=hunter2", "application/x-www-form-urlencoded")
    assert "hunter2" not in redacted
    assert "username=alice" in redacted


def test_separator_free_api_key_json_and_multipart_bodies_are_redacted() -> None:
    # "apiKey" carries no dash/underscore: the camel-case form must be
    # redacted in JSON bodies and multipart form fields alike.
    body = json.dumps({"username": "alice", "apiKey": "sekret"})
    redacted = _redact_post_data(body, "application/json")
    parsed = json.loads(redacted)
    assert parsed["username"] == "alice"
    assert parsed["apiKey"] == REDACTED_PLACEHOLDER
    assert "sekret" not in redacted

    multipart = "--boundary\r\n" 'Content-Disposition: form-data; name="apiKey"\r\n' "\r\n" "sekret\r\n" "--boundary--\r\n"
    redacted_multipart = _redact_post_data(multipart, "multipart/form-data; boundary=--boundary")
    assert "sekret" not in redacted_multipart
    assert REDACTED_PLACEHOLDER in redacted_multipart


# ── api_calls log_request / log_response (api_logs.log writer) ──────────────


def _capture_api_logs(monkeypatch) -> list:
    captured: list = []
    monkeypatch.setattr(api_calls, "file_logger", captured.append)
    return captured


def test_api_log_request_redacts_headers_url_and_body(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    request = httpx.Request(
        "POST",
        "https://user:pass@api.example.com/login?next=/",
        json={"username": "alice", "password": "hunter2"},
        headers={"Authorization": "Basic dXNlcjpwYXNz", "x-api-key": "key123"},
    )

    asyncio.run(api_calls.log_request(request))

    assert len(captured) == 1
    entry = json.loads(captured[0])["request_data"]
    assert "user:pass" not in entry["url"]
    assert entry["url"] == "https://api.example.com/login?next=/"
    # httpx normalizes header names to lowercase in its headers mapping.
    assert entry["headers"]["authorization"] == REDACTED_PLACEHOLDER
    assert entry["headers"]["x-api-key"] == REDACTED_PLACEHOLDER
    body = json.loads(entry["body"])
    assert body["username"] == "alice"
    assert body["password"] == REDACTED_PLACEHOLDER
    assert "hunter2" not in captured[0]
    assert "dXNlcjpwYXNz" not in captured[0]


def test_api_log_response_redacts_headers_and_body(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    request = httpx.Request("POST", "https://api.example.com/login")
    response = httpx.Response(
        200,
        json={"token": "secret-token-value", "note": "ok"},
        headers={"Set-Cookie": "sid=secret-sid", "content-type": "application/json"},
        request=request,
    )

    asyncio.run(api_calls.log_response(response))

    assert len(captured) == 1
    entry = json.loads(captured[0])["response_data"]
    assert entry["status_code"] == 200
    assert entry["headers"]["set-cookie"] == REDACTED_PLACEHOLDER
    body = json.loads(entry["body"])
    assert body["token"] == REDACTED_PLACEHOLDER
    assert body["note"] == "ok"


# ── playwright network jsonl writer (log_request / log_response) ────────────


class _FakePlaywrightRequest:
    method = "POST"
    url = "https://user:pass@example.test/login"
    headers = {"content-type": "application/json", "authorization": "Bearer abc"}
    post_data = json.dumps({"username": "alice", "password": "hunter2"})


class _FakePlaywrightResponse:
    status = 200
    url = "https://user:pass@example.test/login"
    headers = {"content-type": "application/json"}


def _bare_manager(captured: list) -> PlaywrightManager:
    """Instance without __init__ (no config/browser); log writes are captured."""

    async def fake_write(entry, log_file):
        captured.append(entry)

    manager = object.__new__(PlaywrightManager)
    manager._write_log_entry_to_file = fake_write
    manager.request_response_log_file = "unused.jsonl"
    return manager


def test_playwright_log_request_jsonl_has_no_userinfo() -> None:
    captured: list = []
    manager = _bare_manager(captured)

    async def drive():
        # log_request is sync and uses asyncio.ensure_future, so it must run
        # inside a live loop; yield once so the scheduled write task executes.
        manager.log_request(_FakePlaywrightRequest())
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert len(captured) == 1
    entry = captured[0]
    assert "user:pass" not in entry["url"]
    assert entry["url"] == "https://example.test/login"
    assert entry["headers"]["authorization"] == REDACTED_PLACEHOLDER


def test_playwright_log_response_jsonl_has_no_userinfo() -> None:
    captured: list = []
    manager = _bare_manager(captured)

    async def drive():
        manager.log_response(_FakePlaywrightResponse())
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert len(captured) == 1
    entry = captured[0]
    assert "user:pass" not in entry["url"]
    assert entry["url"] == "https://example.test/login"


# ── config BROWSER_COOKIES log line (finding: raw cookies in the log file) ──


def test_browser_cookies_log_contains_names_but_not_values(caplog) -> None:
    cookies = [{"name": "session_id", "value": "TOPSECRETVALUE", "domain": "example.com"}, {"name": "tracker", "value": "ALSOSECRET"}]
    manager = NonSingletonConfigManager({"BROWSER_COOKIES": json.dumps(cookies)}, ignore_env=True)

    with caplog.at_level(logging.INFO, logger="testzeus_hercules.utils.logger"):
        parsed = manager.get_browser_cookies()

    assert parsed == cookies  # the run still gets the real cookie list
    messages = [record.getMessage() for record in caplog.records]
    cookie_lines = [m for m in messages if "BROWSER_COOKIES" in m]
    assert cookie_lines, messages
    joined = "\n".join(cookie_lines)
    assert "session_id" in joined and "tracker" in joined
    assert "2 cookie(s)" in joined
    assert "TOPSECRETVALUE" not in joined and "ALSOSECRET" not in joined


def test_browser_cookies_invalid_json_logs_error_without_values() -> None:
    manager = NonSingletonConfigManager({"BROWSER_COOKIES": "not-json{", "LOG_LEVEL": "INFO"}, ignore_env=True)
    assert manager.get_browser_cookies() is None


def test_separator_free_api_key_header_is_redacted() -> None:
    from testzeus_hercules.utils.redact import _redact_sensitive_headers

    headers = {"apiKey": "sekret", "X-API-KEY": "also-sekret", "Content-Type": "application/json"}

    redacted = _redact_sensitive_headers(headers)

    assert redacted["apiKey"] == "[REDACTED]"
    assert redacted["X-API-KEY"] == "[REDACTED]"
    assert redacted["Content-Type"] == "application/json"


# ── redact_value / is_sensitive_name (assertion ledgers) ─────────────────────


def test_redact_value_redacts_by_key_hint_and_secret_values() -> None:
    from testzeus_hercules.utils.redact import is_sensitive_name, redact_value

    assert is_sensitive_name("$.body.password")
    assert is_sensitive_name("api_token")
    assert not is_sensitive_name("$.body.name")
    assert redact_value("hunter2", "$.body.password") == "[REDACTED]"
    assert redact_value("keep", "$.body.name") == "keep"
    assert redact_value("abc hunter2 def", "", ["hunter2"]) == "abc [REDACTED] def"
    assert redact_value({"password": "x", "n": 1}) == {"password": "[REDACTED]", "n": 1}
    assert redact_value(1234, "$.body.pin_secret") == "[REDACTED]"  # a numeric secret is still a secret
    assert redact_value(3, "$.body.total") == 3
    assert redact_value("see https://u:pw@h.test/p?token=zzz", "") == "see https://h.test/p?token=[REDACTED]"
    assert redact_value(None) is None


# ── query-name substring matching, X-Form-Login, bounded deep JSON walk ─────


def test_redact_url_matches_secret_name_fragments_not_just_exact_names() -> None:
    from testzeus_hercules.utils.redact import redact_url

    redacted = redact_url("https://h/x?client_secret=a1&refresh_token=b2&id_token=c3&keyword=shoes&monitor=1")
    assert "a1" not in redacted and "b2" not in redacted and "c3" not in redacted
    assert "keyword=shoes" in redacted and "monitor=1" in redacted


def test_form_login_header_is_redacted() -> None:
    assert _redact_sensitive_headers({"X-Form-Login": "user:pw"})["X-Form-Login"] == "[REDACTED]"


def test_deep_mode_redacts_nested_secrets_and_default_mode_is_unchanged() -> None:
    from testzeus_hercules.utils.redact import API_LOG_REDACTION_MAX_DEPTH

    body = json.dumps({"a": {"b": {"c": {"password": "deep"}}}, "token": "top", "l": [{"secret": "one"}]})
    deep = _redact_post_data(body, "application/json", API_LOG_REDACTION_MAX_DEPTH)
    assert "deep" not in deep and "top" not in deep and "one" not in deep
    shallow = json.loads(_redact_post_data(body, "application/json"))
    assert shallow["token"] == "[REDACTED]" and shallow["l"][0]["secret"] == "[REDACTED]"


def test_deep_mode_budget_never_skips_the_default_levels(monkeypatch) -> None:
    from testzeus_hercules.utils import redact

    monkeypatch.setattr(redact, "API_LOG_REDACTION_MAX_NODES", 0)
    body = json.dumps({"x": {"password": "lvl1"}, "y": [{"api_key": "lvl1b"}]})
    redacted = _redact_post_data(body, "application/json", redact.API_LOG_REDACTION_MAX_DEPTH)
    assert "lvl1" not in redacted and "lvl1b" not in redacted


def test_one_url_in_text_helper_serves_api_sql_and_webhook_scrubbers() -> None:
    import time

    from testzeus_hercules.core.tools import api_calls, sql_calls
    from testzeus_hercules.utils import redact

    text = 'connect failed postgresql://user:pw@db.internal:5432/app?token=abc, retry https://svc/x?sid=1 "ok" <http://h/p>'
    expected = redact.redact_text_urls(text)
    assert "pw" not in expected and "abc" not in expected
    assert api_calls._redacted_error_text(Exception(text)) == expected
    assert "pw" not in sql_calls._scrub_error(Exception(text))
    assert not hasattr(redact, "_URL_TEXT_RE") and not hasattr(api_calls, "_URL_IN_TEXT_RE") and not hasattr(sql_calls, "_URL_TOKEN_RE")
    # a long run of scheme-like characters without "://" must stay linear
    started = time.monotonic()
    assert redact.redact_text_urls("a-" * 500_000) == "a-" * 500_000
    assert time.monotonic() - started < 2.0
    # scheme boundaries: leading digits/dashes are not part of the scheme
    assert redact.redact_text_urls("1-https://u:p@h/x") == "1-https://h/x"
    assert redact.redact_webhook_text("boom at https://hooks.slack.com/services/T/B/SECRET now").endswith("https://hooks.slack.com/[REDACTED] now")
