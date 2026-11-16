"""The request guard against requests a browser makes for another page: other localhost ports, text/plain bodies,
foreign WebSockets, oversized bodies, odd cookies, and the remote-access Host list."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from starlette.websockets import WebSocketDisconnect

from testzeus_hercules.daemon import security
from testzeus_hercules.daemon.security import MAX_SESSIONS, SessionBook
from tests.v2.fixtures.daemon_support import TOKEN, case_dict, client_for, make_config, submit, wait_for

OWN = "http://127.0.0.1:7373"
WS = "ws://127.0.0.1:7373"
ENV = {"name": "staging", "base_url": "http://127.0.0.1:9"}


def _login(client: Any) -> None:
    url = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"]
    assert client.get(url.replace(OWN, ""), follow_redirects=False).status_code == 303


@pytest.fixture()
def browser(tmp_path: Path) -> Iterator[Any]:
    """A client holding only the UI session cookie (as the browser does)."""
    with client_for(make_config(tmp_path), auth=False) as client:
        _login(client)
        yield client


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:7373", "http://[::1]:7373", "https://127.0.0.1:7373", "http://127.0.0.1"])
def test_another_loopback_origin_cannot_post(browser: Any, origin: str) -> None:
    response = browser.post("/api/environments", json=ENV, headers={"Origin": origin})
    assert response.status_code == 403
    assert browser.get("/api/environments").json()["environments"] == []


def test_the_daemons_own_origin_can_post(browser: Any) -> None:
    response = browser.post("/api/environments", json=ENV, headers={"Origin": OWN})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("path", ["/api/environments", "/api/runs"])
def test_a_text_plain_body_is_415_even_from_the_own_origin(browser: Any, path: str) -> None:
    response = browser.post(path, content=json.dumps(ENV), headers={"Origin": OWN, "Content-Type": "text/plain"})
    assert response.status_code == 415


def test_a_text_plain_body_is_415_with_the_token_too(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        response = client.post("/api/environments", content=json.dumps(ENV), headers={"Content-Type": "text/plain"})
        assert response.status_code == 415
        assert client.post("/api/environments", content=json.dumps(ENV), headers={"Content-Type": "application/json; charset=utf-8"}).status_code == 201


def test_a_cookie_post_without_origin_is_refused(browser: Any) -> None:
    assert browser.post("/api/environments", json=ENV).status_code == 403
    assert browser.post("/api/runs/r_x_000/cancel").status_code == 403


def test_a_body_less_post_works_with_the_own_origin_or_the_token(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict(status="draft"))
        _login(client)
        approved = client.post(f"/api/runs/{run_id}/approve", headers={"Authorization": "", "Origin": OWN})
        assert approved.status_code == 200, approved.text
        wait_for(client, run_id, {"needs_plan_approval"})
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200  # the token, no Origin
        assert wait_for(client, run_id)["status"] == "cancelled"


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_cookie_requests_another_site_made_are_refused(browser: Any, site: str) -> None:
    assert browser.get("/api/runs", headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "cors"}).status_code == 403
    assert browser.post("/api/environments", json=ENV, headers={"Origin": OWN, "Sec-Fetch-Site": site}).status_code == 403
    assert browser.get("/api/runs", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_link_from_elsewhere_still_opens_the_ui(browser: Any) -> None:
    page = browser.get("/runs/r_x_000", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
    assert page.status_code != 403


def test_bodies_over_the_limit_are_413(tmp_path: Path) -> None:
    big = b'{"name": "' + b"x" * (security.MAX_BODY_BYTES + 10) + b'"}'
    with client_for(make_config(tmp_path)) as client:
        declared = client.post("/api/environments", content=big, headers={"Content-Type": "application/json"})
        assert declared.status_code == 413

        def chunks() -> Iterator[bytes]:
            for start in range(0, len(big), 256 * 1024):
                yield big[start : start + 256 * 1024]

        streamed = client.post("/api/environments", content=chunks(), headers={"Content-Type": "application/json"})
        assert streamed.status_code == 413


def test_screencast_websocket_refuses_other_origins_and_sites(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case_dict())
        wait_for(client, run_id)
        _login(client)
        del client.headers["Authorization"]
        for headers in ({"Origin": "http://127.0.0.1:3000"}, {"Origin": "http://localhost:7373"}, {"Origin": OWN, "Sec-Fetch-Site": "same-site"}):
            with pytest.raises(WebSocketDisconnect) as refused:
                with client.websocket_connect(f"{WS}/api/runs/{run_id}/screencast", headers=headers) as ws:
                    ws.receive()
            assert refused.value.code == 1008
        with client.websocket_connect(f"{WS}/api/runs/{run_id}/screencast", headers={"Origin": OWN, "Sec-Fetch-Site": "same-origin"}) as ws:
            assert json.loads(ws.receive_text())["type"] == "end"


@pytest.mark.parametrize("cookie", ["hercules_session=café", "hercules_session=ÿþ", "hercules_session="])
def test_a_non_ascii_session_cookie_is_401_not_500(tmp_path: Path, cookie: str) -> None:
    with client_for(make_config(tmp_path), auth=False) as client:
        _login(client)  # a real session exists, so the cookie is compared with it
        client.cookies.clear()
        response = client.get("/api/health", headers={"Cookie": cookie.encode("utf-8")})
        assert response.status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"{WS}/api/runs/r_x_000/screencast", headers={"Origin": OWN, "Cookie": cookie.encode("utf-8")}) as ws:
                ws.receive()


def test_sessions_expire_and_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    book = SessionBook()
    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    first = book.redeem(book.new_code())
    assert first is not None and book.valid(first)
    clock[0] += security.SESSION_TTL_S + 1
    assert not book.valid(first)
    sessions = [book.redeem(book.new_code()) for _ in range(MAX_SESSIONS + 5)]
    assert len(book.sessions) == MAX_SESSIONS
    assert not book.valid(str(sessions[0])) and book.valid(str(sessions[-1]))


def test_the_login_cookie_has_a_max_age(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path), auth=False) as client:
        url = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"]
        cookie = client.get(url.replace(OWN, ""), follow_redirects=False).headers["set-cookie"].lower()
    assert f"max-age={int(security.SESSION_TTL_S)}" in cookie and "secure" not in cookie


def _remote(tmp_path: Path, **env: str) -> Any:
    from tests.v2.fixtures.daemon_support import isolated_env

    return make_config(tmp_path, env=isolated_env(tmp_path, **env), host="10.0.0.5", allow_remote=True)


@pytest.mark.parametrize("host,status", [("evil.example", 403), ("10.0.0.6:7373", 403), ("10.0.0.5:7373", 200), ("hercules.internal.test", 200), ("127.0.0.1:7373", 200)])
def test_remote_access_still_checks_host(tmp_path: Path, host: str, status: int) -> None:
    config = _remote(tmp_path, HERCULES_DAEMON_ALLOWED_HOSTS="hercules.internal.test, other.test")
    with client_for(config) as client:
        assert client.get("/api/health", headers={"Host": host}).status_code == status


def test_remote_access_behind_tls_sets_a_secure_cookie(tmp_path: Path) -> None:
    config = _remote(tmp_path, HERCULES_DAEMON_ALLOWED_HOSTS="hercules.internal.test")
    with client_for(config, auth=False) as client:
        code = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"].split("code=", 1)[1]
        headers = {"Host": "hercules.internal.test", "X-Forwarded-Proto": "https"}
        cookie = client.get(f"/login?code={code}", headers=headers, follow_redirects=False).headers["set-cookie"]
        assert "secure" in cookie.lower()
        session = cookie.split(";", 1)[0].split("=", 1)[1]
        own = {**headers, "Cookie": f"hercules_session={session}"}
        assert client.post("/api/environments", json=ENV, headers={**own, "Origin": "https://hercules.internal.test"}).status_code == 201
        assert client.post("/api/environments", json={**ENV, "name": "b"}, headers={**own, "Origin": "http://hercules.internal.test"}).status_code == 403


def test_x_forwarded_proto_is_ignored_on_a_loopback_daemon(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path), auth=False) as client:
        code = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"].split("code=", 1)[1]
        cookie = client.get(f"/login?code={code}", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False).headers["set-cookie"].lower()
    assert "secure" not in cookie
