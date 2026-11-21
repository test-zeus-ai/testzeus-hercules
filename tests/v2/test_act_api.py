"""The ``http_request`` tool (``act.api``): request building, body modes, auth, limits and redacted evidence.

Offline: requests go to an ``httpx.MockTransport``. Ported from the v1 ``generic_http_api`` tests.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from testzeus_hercules.act.api import HttpRequest, HttpResponse, auth_headers, exchange_record, send, text_content_type
from testzeus_hercules.act.redact import REDACTED, redact_body_text, redact_text, redact_url


def wire(responder: Callable[[httpx.Request], httpx.Response] | None = None) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return responder(request) if responder else httpx.Response(200, json={"ok": True})

    return seen, httpx.MockTransport(handler)


def run(request: HttpRequest, transport: httpx.MockTransport, **kwargs: Any) -> HttpResponse:
    return asyncio.run(send(request, transport=transport, **kwargs))


# ── responses ────────────────────────────────────────────────────────────────


def test_json_response_with_status_headers_and_duration() -> None:
    _, transport = wire(lambda request: httpx.Response(200, json={"id": 7}, headers={"X-Trace": "abc"}))
    response = run(HttpRequest("get", "http://api.test/pets/7"), transport)
    assert response.ok and response.status == 200 and response.reason == "OK"
    assert response.json() == {"id": 7}
    assert response.header("x-trace") == "abc" and "json" in response.content_type
    assert response.duration_ms >= 0


def test_error_statuses_are_responses_not_failures() -> None:
    _, transport = wire(lambda request: httpx.Response(404, json={"detail": "no such pet"}))
    response = run(HttpRequest("GET", "http://api.test/pets/9"), transport)
    assert response.ok and response.status == 404 and response.json() == {"detail": "no such pet"}


def test_redirects_are_not_followed() -> None:
    _, transport = wire(lambda request: httpx.Response(302, headers={"Location": "http://elsewhere.test/"}))
    assert run(HttpRequest("GET", "http://api.test/old"), transport).status == 302


def test_non_json_body_raises_on_json() -> None:
    _, transport = wire(lambda request: httpx.Response(200, text="plain"))
    response = run(HttpRequest("GET", "http://api.test/"), transport)
    with pytest.raises(ValueError, match="not JSON"):
        response.json()
    _, empty = wire(lambda request: httpx.Response(204))
    with pytest.raises(ValueError, match="empty"):
        run(HttpRequest("GET", "http://api.test/"), empty).json()


def test_transport_failure_is_a_redacted_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    response = run(HttpRequest("GET", "http://bob:pw-123@api.test/x?token=tok-999&page=2"), httpx.MockTransport(refuse))
    assert not response.ok and response.status is None and response.error_kind == "transport"
    assert "pw-123" not in response.error and "tok-999" not in response.error and "page=2" in response.error


def test_timeout_names_the_limit() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    response = run(HttpRequest("GET", "http://api.test/slow"), httpx.MockTransport(slow), timeout_s=2.5)
    assert response.error_kind == "timeout" and "2.5 s" in (response.error or "")


def test_large_bodies_are_refused_announced_or_streamed() -> None:
    _, announced = wire(lambda request: httpx.Response(200, content=b"x" * 50, headers={"content-length": "50"}))
    assert run(HttpRequest("GET", "http://api.test/"), announced, max_bytes=10).error_kind == "too_large"

    async def chunks() -> Any:
        yield b"x" * 8
        yield b"y" * 8

    _, streamed = wire(lambda request: httpx.Response(200, content=chunks()))
    assert run(HttpRequest("GET", "http://api.test/"), streamed, max_bytes=10).error_kind == "too_large"


# ── query strings ────────────────────────────────────────────────────────────


def _query(request: httpx.Request) -> list[tuple[str, str]]:
    return urllib.parse.parse_qsl(request.url.query.decode(), keep_blank_values=True)


def test_inline_query_survives_and_merges_with_explicit_parameters() -> None:
    seen, transport = wire()
    run(HttpRequest("GET", "http://api.test/orders?status=paid&x=1"), transport)
    run(HttpRequest("GET", "http://api.test/orders?status=paid&x=1", query=(("x", "2"), ("y", "3"))), transport)
    run(HttpRequest("GET", "http://api.test/items?tag=a&tag=b"), transport)
    assert _query(seen[0]) == [("status", "paid"), ("x", "1")]
    assert _query(seen[1]) == [("status", "paid"), ("x", "2"), ("y", "3")]
    assert _query(seen[2]) == [("tag", "a"), ("tag", "b")]


def test_list_parameters_are_repeated_keys() -> None:
    seen, transport = wire()
    run(HttpRequest("GET", "http://api.test/items", query=(("tag", ["a", "b"]),)), transport)  # type: ignore[arg-type]
    assert _query(seen[0]) == [("tag", "a"), ("tag", "b")]


# ── bodies ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [0, False, [], {}])
def test_falsy_json_bodies_reach_the_server(body: Any) -> None:
    seen, transport = wire()
    run(HttpRequest("POST", "http://api.test/x", body=body), transport)
    assert json.loads(seen[0].content) == body
    assert seen[0].headers["content-type"] == "application/json"


def test_text_bodies_are_typed_by_content() -> None:
    seen, transport = wire()
    run(HttpRequest("POST", "http://api.test/x", body='{"name": "x"}'), transport)
    run(HttpRequest("POST", "http://api.test/x", body="[1, 2]"), transport)
    run(HttpRequest("POST", "http://api.test/x", body="42"), transport)
    run(HttpRequest("POST", "http://api.test/x", body='{"a": 1}', headers=(("Content-Type", "application/vnd.x+json"),)), transport)
    run(HttpRequest("POST", "http://api.test/x", body="raw text", body_mode="RAW"), transport)
    run(HttpRequest("POST", "http://api.test/x", body=""), transport)
    types = [request.headers.get("content-type") for request in seen]
    assert types == ["application/json", "application/json", "text/plain", "application/vnd.x+json", None, "text/plain"]
    assert seen[0].content == b'{"name": "x"}'  # sent verbatim
    assert text_content_type("true") == "text/plain"


def test_urlencoded_and_binary_bodies() -> None:
    seen, transport = wire()
    run(HttpRequest("POST", "http://api.test/x", body={"a": "1", "b": "two"}, body_mode="urlencoded"), transport)
    run(HttpRequest("POST", "http://api.test/x", body=b"\x00\x01", body_mode="binary"), transport)
    assert seen[0].content == b"a=1&b=two" and seen[0].headers["content-type"] == "application/x-www-form-urlencoded"
    assert seen[1].content == b"\x00\x01" and seen[1].headers["content-type"] == "application/octet-stream"


def test_unknown_body_mode_is_an_error_and_nothing_is_sent() -> None:
    seen, transport = wire()
    response = run(HttpRequest("POST", "http://api.test/x", body={"a": 1}, body_mode="xml"), transport)
    assert response.error_kind == "invalid_request" and "json, raw, urlencoded, multipart, binary" in (response.error or "")
    assert not seen


def test_no_body_means_no_body_whatever_the_mode() -> None:
    seen, transport = wire()
    run(HttpRequest("GET", "http://api.test/x", body_mode="json"), transport)
    assert seen[0].content == b"" and "content-type" not in seen[0].headers


def test_multipart_sends_real_parts_and_drops_the_callers_content_type(tmp_path: Path) -> None:
    upload = tmp_path / "photo.txt"
    upload.write_text("file-bytes")
    seen, transport = wire()
    body = {"title": "Rex", "photo": {"file_path": "photo.txt", "content_type": "text/plain"}}
    response = run(HttpRequest("POST", "http://api.test/x", body=body, body_mode="multipart", headers=(("Content-Type", "application/json"),)), transport, roots=(tmp_path,))
    assert response.ok
    content_type = seen[0].headers["content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="title"' in seen[0].content and b"file-bytes" in seen[0].content and b'filename="photo.txt"' in seen[0].content


def test_multipart_files_outside_the_project_or_secret_files_are_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "outside.txt").write_text("x")
    (project / ".env").write_text("SECRET=1")
    (project / "link.txt").symlink_to(tmp_path / "outside.txt")
    seen, transport = wire()
    for name in (str(tmp_path / "outside.txt"), "../outside.txt", ".env", "link.txt", "missing.txt"):
        response = run(HttpRequest("POST", "http://api.test/x", body={"f": {"file_path": name}}, body_mode="multipart"), transport, roots=(project,))
        assert response.error_kind == "invalid_request", name
    assert not seen


# ── authentication ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "value", "header", "expected"),
    [
        ("bearer", "t-1", "Authorization", "Bearer t-1"),
        ("jwt", "t-2", "Authorization", "JWT t-2"),
        ("api_key", "k-3", "X-API-Key", "k-3"),
        ("form_login", "f-4", "X-Form-Login", "f-4"),
        ("basic", "ada:pw", "Authorization", "Basic YWRhOnB3"),
        ("BASIC", ["ada", "pw"], "Authorization", "Basic YWRhOnB3"),
    ],
)
def test_auth_headers(kind: str, value: Any, header: str, expected: str) -> None:
    headers, problem = auth_headers(kind, value)
    assert problem is None and headers == {header: expected}


@pytest.mark.parametrize(("kind", "value", "fragment"), [("oauth", "x", "Unknown authentication"), ("bearer", "", "needs a value"), ("basic", "no-colon", "username:password")])
def test_bad_auth_is_a_clear_error(kind: str, value: Any, fragment: str) -> None:
    assert fragment in (auth_headers(kind, value)[1] or "")


@pytest.mark.parametrize("kind", [None, "", "none", "None"])
def test_no_auth_type_means_no_auth(kind: str | None) -> None:
    assert auth_headers(kind, "x") == ({}, None)


# ── evidence ─────────────────────────────────────────────────────────────────


def test_exchange_record_masks_credentials_by_name_and_caps_bodies() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "resp-tok-1", "name": "Rex", "nested": {"password": "pw-2"}, "big": "z" * 100_000}, headers={"Set-Cookie": "sid=abc"})

    _, transport = wire(respond)
    request = HttpRequest(
        "POST",
        "http://ada:pw-0@api.test/login?api_key=k-1&page=2",
        headers=(("Authorization", "Bearer b-1"), ("X-Api-Key", "k-2"), ("Accept", "application/json")),
        body={"user": "ada", "password": "pw-3", "deep": [{"client_secret": "s-4"}]},
    )
    response = run(request, transport)
    record = exchange_record(request, response)
    text = json.dumps(record)
    for secret in ("pw-0", "k-1", "b-1", "k-2", "pw-2", "pw-3", "s-4", "resp-tok-1", "sid=abc"):
        assert secret not in text, secret
    assert "page=2" in record["request"]["url"] and ["Accept", "application/json"] in record["request"]["headers"]
    assert '"Rex"' in record["response"]["body"] and "truncated" in record["response"]["body"]
    assert record["response"]["status"] == 200 and record["response"]["bytes"] > 100_000


def test_exchange_record_of_a_failed_request_has_the_error_only() -> None:
    request = HttpRequest("GET", "http://api.test/")
    record = exchange_record(request, HttpResponse(status=None, url="http://api.test/", duration_ms=3, error="the request failed", error_kind="transport"))
    assert record["error"] == "the request failed" and "response" not in record


# ── redaction helpers ────────────────────────────────────────────────────────


def test_url_and_text_redaction() -> None:
    assert redact_url("https://u:p@h.test/a;sessionid=1/b?sort_key=x&token=t#access_token=y") == f"https://h.test/a;sessionid={REDACTED}/b?sort_key=x&token={REDACTED}#access_token={REDACTED}"
    assert redact_url("https://u:p@[::1") == "https://[::1"
    message = redact_text("failed for postgresql://bob:s3cret@db.test:5432/x with password=hunter2", ["hunter"])
    assert "s3cret" not in message and "hunter2" not in message and "db.test" in message


def test_body_redaction_by_content_type() -> None:
    assert json.loads(redact_body_text('[{"password": "x", "a": 1}]')) == [{"password": REDACTED, "a": 1}]
    assert redact_body_text("user=ada&pwd=x", "application/x-www-form-urlencoded") == f"user=ada&pwd={urllib.parse.quote(REDACTED)}"
    multipart = '--b\r\nContent-Disposition: form-data; name="api_key"\r\n\r\nk-9\r\n--b--'
    assert "k-9" not in redact_body_text(multipart, "multipart/form-data; boundary=b")
    assert redact_body_text("not json {", "application/json") == "not json {"


@pytest.mark.parametrize(
    ("base", "path", "url"),
    [
        ("http://localhost:8000/index.html", "/api/health", "http://localhost:8000/api/health"),
        ("http://host/app", "/api/x", "http://host/app/api/x"),
        ("http://host/app/", "api/x", "http://host/app/api/x"),
        ("http://127.0.0.1:8000", "/api/x", "http://127.0.0.1:8000/api/x"),
        ("http://127.0.0.1:8000/v1.2", "orders", "http://127.0.0.1:8000/v1.2/orders"),
        ("https://shop.test/store/home.PHP", "api/cart", "https://shop.test/store/api/cart"),
    ],
)
def test_an_api_path_goes_under_the_base_urls_folder(base: str, path: str, url: str) -> None:
    from testzeus_hercules.act.api import api_url

    assert api_url(base, path) == url
