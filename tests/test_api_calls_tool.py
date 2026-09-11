"""Unit tests for the HTTP API tool hardening (core.tools.api_calls).

Covers: log_response capture cap (no full-body buffering just for logging),
tool-result truncation, JSON payload integrity (no quote mangling) and the
duration folded into the returned string instead of a tuple. A MockTransport
serves responses; no network is involved.
"""

import asyncio
import json
import os

import httpx
import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import api_calls
from testzeus_hercules.core.tools.api_calls import generic_http_api, log_response


def _capture_api_logs(monkeypatch) -> list:
    captured: list = []
    monkeypatch.setattr(api_calls, "file_logger", captured.append)
    return captured


def _install_transport(monkeypatch, handler) -> None:
    """Route api_calls' own AsyncClient through a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(api_calls.httpx, "AsyncClient", client_factory)


def _run(coro):
    return asyncio.run(coro)


# ── finding 5a: log_response capture cap ────────────────────────────────────


def test_log_response_skips_read_when_content_length_exceeds_cap(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    stream_reads: list = []

    def stream():
        stream_reads.append(1)
        yield b"secret-payload"

    response = httpx.Response(200, content=stream(), headers={"content-length": str(5 * 1024 * 1024 * 1024)})

    _run(log_response(response))

    assert stream_reads == []  # the hook must not read the announced-huge body at all
    assert len(captured) == 1
    entry = json.loads(captured[0])["response_data"]
    assert "not captured" in entry["body"]
    assert str(5 * 1024 * 1024 * 1024) in entry["body"]
    assert "secret-payload" not in captured[0]


def test_log_response_slices_large_body_without_content_length(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    big_chunk = b"x" * (api_calls.MAX_CAPTURE_BYTES + 500_000)

    async def body_stream():
        yield big_chunk

    response = httpx.Response(200, content=body_stream())  # async iterator body => chunked, no content-length

    _run(log_response(response))

    assert len(captured) == 1
    entry = json.loads(captured[0])["response_data"]
    assert "[truncated" in entry["body"]
    assert len(entry["body"]) < api_calls.MAX_CAPTURE_BYTES + 200


def test_log_response_small_body_logged_unchanged(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    response = httpx.Response(200, content=b'{"ok": true}')

    _run(log_response(response))

    entry = json.loads(captured[0])["response_data"]
    assert entry["body"] == '{"ok": true}'


# ── finding 5b / 7: tool result caps and payload integrity ──────────────────


def test_generic_http_api_returns_json_string_with_duration(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "abc", "note": "it's 'quoted' and \"double\""})

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("GET", "https://api.test/thing"))

    assert isinstance(result, str)
    assert result.startswith("{")
    assert '"status_code":200' in result
    assert "'status_code'" not in result  # the old .replace('"', "'") corruption is gone
    assert '"note":"it\'s \'quoted\' and \\"double\\""' in result  # string values survive intact
    import re

    assert re.search(r"\[duration: \d+\.\d{2}s\]$", result)
    # The JSON prefix (everything before the duration marker) parses.
    payload = result[: result.rindex(" [duration:")]
    parsed = json.loads(payload)
    assert parsed["body"]["token"] == "abc"


def test_generic_http_api_truncates_large_response(monkeypatch) -> None:
    big_text = "y" * 500_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"blob": big_text})

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("GET", "https://api.test/big"))

    assert isinstance(result, str)
    assert len(result) < api_calls.MAX_RESULT_CHARS + 200
    assert "[truncated" in result
    assert "[duration:" in result


def test_error_response_returns_payload_string_with_duration(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("GET", "https://api.test/fail"))

    assert isinstance(result, str)
    assert '"status_code":500' in result
    assert '"status_type":"server_error"' in result
    assert "[duration:" in result


def test_transport_failure_returns_payload_string(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("GET", "https://api.test/unreachable"))

    assert isinstance(result, str)
    parsed = json.loads(result[: result.rindex(" [duration:")])
    assert parsed["status_type"] == "failure"
    assert parsed["status_code"] is None


# ── inline URL query must survive (httpx params= replaces it) ────────────────


def test_inline_url_query_survives_without_query_params(monkeypatch) -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    _install_transport(monkeypatch, handler)
    _run(generic_http_api("GET", "http://host.test/echo?status=paid&x=1"))

    assert seen == ["http://host.test/echo?status=paid&x=1"]


def test_inline_url_query_merges_with_explicit_params_explicit_wins(monkeypatch) -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    _install_transport(monkeypatch, handler)
    _run(generic_http_api("GET", "http://host.test/echo?status=paid&x=1", query_params={"page": "2", "status": "refunded"}))

    from urllib.parse import parse_qsl, urlsplit

    query = dict(parse_qsl(urlsplit(seen[0]).query))
    assert query == {"status": "refunded", "x": "1", "page": "2"}


def test_empty_query_params_sends_none_and_leaves_url_untouched(monkeypatch) -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    _install_transport(monkeypatch, handler)
    _run(generic_http_api("GET", "http://host.test/echo"))

    assert seen == ["http://host.test/echo"]  # no stray "?" or empty params


# ── falsy bodies must still be sent (body={} / body=0) ───────────────────────


def test_falsy_json_body_reaches_the_server(monkeypatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"echo": True})

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("POST", "https://api.test/echo", body={}, body_mode="json"))

    parsed = json.loads(result[: result.rindex(" [duration:")])
    assert parsed["status_code"] == 200
    assert seen["body"] == b"{}"  # the 2-byte body was actually sent
    assert seen["content_type"] == "application/json"


def test_falsy_raw_body_does_not_vanish_silently(monkeypatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"echo": True})

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("POST", "https://api.test/echo", body=0, body_mode="raw"))

    parsed = json.loads(result[: result.rindex(" [duration:")])
    # httpx cannot send a bare int as the body; the fix surfaces that as a
    # structured failure instead of silently dropping the falsy body.
    assert parsed["status_type"] == "failure"
    assert seen == {}  # nothing was sent behind the caller's back


# ── multipart encoding (the old httpx.FormData path never worked) ────────────


def test_multipart_mode_sends_real_multipart_and_drops_caller_content_type(monkeypatch, tmp_path) -> None:
    upload = tmp_path / "report.bin"
    upload.write_bytes(b"file-content-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        return httpx.Response(
            200,
            json={"content_type": request.headers.get("content-type", ""), "body_text": request.content.decode("utf-8", errors="ignore")},
        )

    _install_transport(monkeypatch, handler)
    result = _run(
        generic_http_api(
            "POST",
            "https://api.test/upload",
            body={"note": "hello", "payload": {"file_path": str(upload)}},
            body_mode="multipart",
            headers={"Content-Type": "text/plain"},  # must be dropped: boundary must survive
        )
    )

    parsed = json.loads(result[: result.rindex(" [duration:")])
    body = parsed["body"]
    assert body["content_type"].startswith("multipart/form-data; boundary=")
    assert 'name="note"' in body["body_text"]
    assert "hello" in body["body_text"]
    assert 'name="payload"' in body["body_text"]
    assert "file-content-bytes" in body["body_text"]
    assert "report.bin" in body["body_text"]


def test_multipart_missing_file_returns_structured_error(monkeypatch) -> None:
    _capture_api_logs(monkeypatch)
    result = _run(
        generic_http_api(
            "POST",
            "https://api.test/upload",
            body={"payload": {"file_path": "/nonexistent/dir/x.bin"}},
            body_mode="multipart",
        )
    )

    parsed = json.loads(result[: result.rindex(" [duration:")])
    assert "Invalid multipart body" in parsed["error"]


# ── request timeout comes from API_REQUEST_TIMEOUT ───────────────────────────


def test_timeout_returns_named_error_with_configured_seconds(monkeypatch) -> None:
    monkeypatch.setitem(get_global_conf().get_config(), "API_REQUEST_TIMEOUT", "2")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Connection timed out")

    _install_transport(monkeypatch, handler)
    result = _run(generic_http_api("GET", "https://api.test/slow"))

    parsed = json.loads(result[: result.rindex(" [duration:")])
    assert parsed["status_type"] == "timeout"
    assert parsed["status_code"] is None
    assert "Request timed out after 2s" in parsed["error"]
    assert "API_REQUEST_TIMEOUT" in parsed["error"]
    assert parsed["timeout_seconds"] == 2.0


# ── request-body log truncation ──────────────────────────────────────────────


def test_log_request_truncates_huge_body(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    request = httpx.Request("POST", "https://api.test/x", content="z" * (api_calls.MAX_LOG_BODY_CHARS + 1000))

    _run(api_calls.log_request(request))

    entry = json.loads(captured[0])["request_data"]
    assert "[truncated" in entry["body"]
    assert len(entry["body"]) < api_calls.MAX_LOG_BODY_CHARS + 200
    assert entry["body"].startswith("zzz")


def test_log_request_redaction_and_url_redaction_still_apply(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    request = httpx.Request(
        "POST",
        "https://user:supersecret@api.test/login",
        json={"password": "hunter2"},
        headers={"Authorization": "Bearer topsecret"},
    )

    _run(api_calls.log_request(request))

    logged = captured[0]
    assert "hunter2" not in logged
    assert "topsecret" not in logged
    assert "supersecret" not in logged


def test_multipart_request_body_is_captured_and_redacted_in_log(monkeypatch, tmp_path) -> None:
    captured = _capture_api_logs(monkeypatch)
    upload = tmp_path / "report.bin"
    upload.write_bytes(b"file-content-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, handler)
    _run(
        generic_http_api(
            "POST",
            "https://api.test/upload",
            body={"note": "hello", "api_key": "topsecret", "payload": {"file_path": str(upload)}},
            body_mode="multipart",
        )
    )

    request_entry = json.loads(next(line for line in captured if '"request_data"' in line))["request_data"]
    assert "not captured" not in request_entry["body"]  # streamed bodies must be read for the log
    assert 'name="payload"' in request_entry["body"]
    assert "report.bin" in request_entry["body"]
    assert "file-content-bytes" in request_entry["body"]
    assert "hello" in request_entry["body"]
    # the sensitive multipart field is redacted, everything else intact
    assert "topsecret" not in request_entry["body"]
    assert "[REDACTED]" in request_entry["body"]


# ── redaction happens before truncation (no secret fragments at the cap) ─────


def _straddling_body(cap: int, secret: str) -> str:
    """JSON body whose password value straddles the `cap` character boundary."""
    padding = cap - 40
    prefix = '{"pad": "' + "a" * padding + '", "password": "'
    assert len(prefix) < cap < len(prefix) + len(secret)  # the secret straddles the cap
    return prefix + secret + '"}'


def test_log_request_redacts_before_truncating_body(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    secret = "boundary-straddling-hunter2"
    request = httpx.Request("POST", "https://api.test/x", content=_straddling_body(api_calls.MAX_LOG_BODY_CHARS, secret))

    _run(api_calls.log_request(request))

    logged = captured[0]
    entry = json.loads(logged)["request_data"]
    # Truncating first would leave the first characters of the secret in the
    # log; redacting the full body first removes every trace of it.
    assert secret not in logged
    assert secret[:10] not in logged
    assert "[REDACTED]" in entry["body"]


def test_log_response_redacts_before_capping_large_body(monkeypatch) -> None:
    captured = _capture_api_logs(monkeypatch)
    secret = "boundary-straddling-hunter2"
    body_text = _straddling_body(api_calls.MAX_CAPTURE_BYTES, secret)

    async def body_stream():
        yield body_text.encode("utf-8")

    response = httpx.Response(200, content=body_stream())  # chunked, no content-length

    _run(log_response(response))

    logged = captured[0]
    entry = json.loads(logged)["response_data"]
    assert "[truncated" in entry["body"]  # the capture cap still applies
    assert len(entry["body"]) < api_calls.MAX_CAPTURE_BYTES + 200
    assert secret not in logged
    assert secret[:10] not in logged
    assert "[REDACTED]" in entry["body"]


# ── invalid schemas are ERROR, not garbage per-instance violations ───────────


def test_invalid_schema_returns_error_verdict_not_garbage_violations() -> None:
    result = _run(api_calls.validate_response_schema({"a": 1}, {"type": "object", "required": "notalist"}))

    parsed = json.loads(result)
    assert parsed["assertion"] == "ERROR"
    assert "Invalid JSON Schema" in parsed["reason"]
    assert "'notalist' is not of type 'array'" in parsed["reason"]
    assert parsed.get("violations") is None  # no nonsense per-instance errors


def test_valid_schema_still_validates_instances() -> None:
    schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}

    passed = _run(api_calls.validate_response_schema({"id": 1}, schema))
    assert json.loads(passed)["assertion"] == "PASSED"

    failed = _run(api_calls.validate_response_schema({"id": "not-an-int"}, schema))
    parsed = json.loads(failed)
    assert parsed["assertion"] == "FAILED"
    assert parsed["violation_count"] == 1
