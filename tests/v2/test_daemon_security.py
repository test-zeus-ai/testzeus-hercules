"""Daemon security: token auth, Host/Origin checks, UI login cookie, loopback binding and daemon.json."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from testzeus_hercules.daemon.discovery import DaemonInfo, new_token, read_daemon_file, remove_daemon_file, write_daemon_file
from testzeus_hercules.daemon.server import DaemonStartError, check_bind
from tests.v2.fixtures.daemon_support import TOKEN, client_for, make_config


@pytest.fixture()
def config(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_config(tmp_path)


def test_missing_token_is_401(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        response = client.get("/api/health")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert TOKEN not in response.text


@pytest.mark.parametrize("header", ["Bearer wrong", f"Bearer {TOKEN}x", f"Basic {TOKEN}", TOKEN, "Bearer "])
def test_wrong_token_is_401(config, header: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        response = client.get("/api/runs", headers={"Authorization": header})
    assert response.status_code == 401
    assert TOKEN not in response.text


def test_right_token_is_accepted_and_never_echoed(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        health = client.get("/api/health")
        models = client.get("/api/models")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    for response in (health, models):
        assert TOKEN not in response.text


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "localhost.attacker.test:7373", "192.168.1.5:7373"])
def test_foreign_host_header_is_rejected(config, host: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get("/api/health", headers={"Host": host})
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:7373", "localhost:7373", "[::1]:7373", "localhost"])
def test_loopback_host_headers_are_allowed(config, host: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get("/api/health", headers={"Host": host})
    assert response.status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example", "file://"])
def test_foreign_origin_is_rejected(config, origin: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.post("/api/runs", json={}, headers={"Origin": origin})
    assert response.status_code == 403


def test_loopback_origin_is_allowed(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get("/api/health", headers={"Origin": "http://127.0.0.1:7373"})
    assert response.status_code == 200


def test_one_time_login_url_sets_a_strict_httponly_cookie(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        login = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"]
        assert login.startswith("http://127.0.0.1:7373/login?code=")
        code = login.split("code=", 1)[1]
        assert TOKEN not in login
        assert client.get("/api/health").status_code == 401

        first = client.get(f"/login?code={code}", follow_redirects=False)
        assert first.status_code == 303
        cookie = first.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
        assert TOKEN.lower() not in cookie
        assert client.get("/api/health").status_code == 200

        client.cookies.clear()
        assert client.get(f"/login?code={code}", follow_redirects=False).status_code == 401
        assert client.get("/api/health").status_code == 401


def test_bad_login_code_is_rejected(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        assert client.get("/login?code=nope", follow_redirects=False).status_code == 401
        assert client.get("/login", follow_redirects=False).status_code == 401


def test_check_bind_refuses_non_loopback_without_allow_remote() -> None:
    with pytest.raises(DaemonStartError, match="loopback"):
        check_bind("0.0.0.0", allow_remote=False, token_from_env=None)
    with pytest.raises(DaemonStartError, match="loopback"):
        check_bind("192.168.1.10", allow_remote=False, token_from_env="x" * 40)


def test_check_bind_remote_needs_a_token() -> None:
    with pytest.raises(DaemonStartError, match="HERCULES_DAEMON_TOKEN"):
        check_bind("0.0.0.0", allow_remote=True, token_from_env=None)
    with pytest.raises(DaemonStartError, match="at least 32"):
        check_bind("0.0.0.0", allow_remote=True, token_from_env="short")
    check_bind("0.0.0.0", allow_remote=True, token_from_env="x" * 40)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_check_bind_accepts_loopback(host: str) -> None:
    check_bind(host, allow_remote=False, token_from_env=None)


def test_daemon_file_is_private_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "home" / "daemon.json"
    token = new_token()
    assert len(token) >= 43
    info = DaemonInfo(host="127.0.0.1", port=7373, pid=os.getpid(), token=token, started_at="2026-10-26T00:00:00+00:00", project_dir=str(tmp_path))

    write_daemon_file(path, info)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert set(json.loads(path.read_text())) >= {"port", "pid", "token", "started_at"}
    assert read_daemon_file(path) == info


def test_daemon_file_replaces_a_world_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "daemon.json"
    path.write_text("{}")
    path.chmod(0o644)
    write_daemon_file(path, DaemonInfo(host="127.0.0.1", port=1, pid=1, token=new_token(), started_at="x"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"port": "x"}', '{"port": 1, "pid": 1}'])
def test_unreadable_daemon_file_is_ignored(tmp_path: Path, text: str) -> None:
    path = tmp_path / "daemon.json"
    path.write_text(text)
    assert read_daemon_file(path) is None


def test_remove_daemon_file_only_for_its_owner(tmp_path: Path) -> None:
    path = tmp_path / "daemon.json"
    write_daemon_file(path, DaemonInfo(host="127.0.0.1", port=1, pid=4242, token=new_token(), started_at="x"))
    remove_daemon_file(path, pid=1)
    assert path.exists()
    remove_daemon_file(path, pid=4242)
    assert not path.exists()
