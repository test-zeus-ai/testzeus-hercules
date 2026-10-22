"""Offline tests for HTTP hardening shared by the MCP server and the dashboard."""

import base64

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from testzeus_hercules import mcp_server
from testzeus_hercules.dashboard import app as dashboard_app
from testzeus_hercules.orchestrator import session as session_module
from testzeus_hercules.utils import http_security as hs

TOKEN = "t" * 24


def _ping_app() -> Starlette:
    app = Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])
    return app


# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.1.2.3", "app.localhost"])
def test_loopback_hosts(host) -> None:
    assert hs.is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.com", ""])
def test_non_loopback_hosts(host) -> None:
    assert not hs.is_loopback_host(host)


def test_bind_policy_allows_loopback_without_token() -> None:
    hs.enforce_bind_policy("svc", "127.0.0.1", None, "X_TOKEN")


def test_bind_policy_refuses_remote_without_token() -> None:
    with pytest.raises(hs.InsecureBindError, match="X_TOKEN"):
        hs.enforce_bind_policy("svc", "0.0.0.0", None, "X_TOKEN")


def test_bind_policy_refuses_short_token() -> None:
    with pytest.raises(hs.InsecureBindError, match="too short"):
        hs.enforce_bind_policy("svc", "0.0.0.0", "short", "X_TOKEN")


def test_bind_policy_accepts_remote_with_long_token() -> None:
    hs.enforce_bind_policy("svc", "0.0.0.0", TOKEN, "X_TOKEN")


def test_host_allowlist_variants() -> None:
    assert hs.build_host_allowlist("127.0.0.1") == {"localhost", "127.0.0.1", "::1"}
    assert "10.0.0.5" in hs.build_host_allowlist("10.0.0.5")
    assert hs.build_host_allowlist("0.0.0.0") is None
    assert "dash.example.com" in hs.build_host_allowlist("0.0.0.0", ["dash.example.com:8001"])


# ── middleware ────────────────────────────────────────────────────────────────


def _with_bearer(allow_basic: bool = False) -> TestClient:
    app = _ping_app()
    app.add_middleware(hs.BearerTokenMiddleware, token=TOKEN, allow_basic=allow_basic)
    return TestClient(app)


def test_bearer_rejects_missing_and_wrong_token() -> None:
    client = _with_bearer()
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/", headers={"Authorization": f"Basic {base64.b64encode(f'u:{TOKEN}'.encode()).decode()}"}).status_code == 401


def test_bearer_accepts_correct_token() -> None:
    assert _with_bearer().get("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_basic_auth_accepted_only_when_enabled() -> None:
    creds = base64.b64encode(f"anyone:{TOKEN}".encode()).decode()
    assert _with_bearer(allow_basic=True).get("/", headers={"Authorization": f"Basic {creds}"}).status_code == 200
    bad = base64.b64encode(b"anyone:wrong").decode()
    response = _with_bearer(allow_basic=True).get("/", headers={"Authorization": f"Basic {bad}"})
    assert response.status_code == 401
    assert any("basic" in value.lower() for value in response.headers.get_list("www-authenticate"))


def test_host_allowlist_blocks_rebinding_host() -> None:
    app = _ping_app()
    app.add_middleware(hs.HostAllowlistMiddleware, allowed_hosts={"localhost", "127.0.0.1"})
    client = TestClient(app)
    assert client.get("/", headers={"Host": "localhost:8001"}).status_code == 200
    assert client.get("/", headers={"Host": "127.0.0.1"}).status_code == 200
    assert client.get("/", headers={"Host": "evil.example.com:8001"}).status_code == 421


# ── dashboard wiring ──────────────────────────────────────────────────────────


def test_dashboard_create_app_default_is_unauthenticated(tmp_path) -> None:
    app = dashboard_app.create_app(db_url=f"sqlite:///{tmp_path / 'h.db'}", project_root=str(tmp_path))
    assert TestClient(app).get("/api/runs").status_code == 200


def test_dashboard_create_app_enforces_token_and_host(tmp_path) -> None:
    app = dashboard_app.create_app(
        db_url=f"sqlite:///{tmp_path / 'h.db'}",
        project_root=str(tmp_path),
        auth_token=TOKEN,
        allowed_hosts=hs.build_host_allowlist("127.0.0.1"),
    )
    client = TestClient(app, base_url="http://localhost:8001")
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/api/runs", headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.test"}).status_code == 421


def test_dashboard_main_refuses_remote_bind_without_token(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        dashboard_app.main(["--host", "0.0.0.0"])
    assert "DASHBOARD_AUTH_TOKEN" in str(excinfo.value)


# ── MCP server wiring ─────────────────────────────────────────────────────────


@pytest.fixture
def restore_mcp_settings():
    settings = mcp_server.mcp.settings
    saved = (settings.host, settings.port, settings.streamable_http_path, settings.transport_security)
    yield
    settings.host, settings.port, settings.streamable_http_path, settings.transport_security = saved
    mcp_server.mcp._session_manager = None
    session_module.set_require_explicit_session_id(None)


def test_mcp_build_http_app_refuses_remote_bind_without_token(restore_mcp_settings) -> None:
    with pytest.raises(hs.InsecureBindError):
        mcp_server.build_http_app(host="0.0.0.0", port=8123, path="/mcp", token=None)


def test_mcp_build_http_app_applies_host_port_path(restore_mcp_settings) -> None:
    mcp_server.build_http_app(host="127.0.0.1", port=8123, path="/custom", token=None)
    settings = mcp_server.mcp.settings
    assert (settings.host, settings.port, settings.streamable_http_path) == ("127.0.0.1", 8123, "/custom")
    assert settings.transport_security.enable_dns_rebinding_protection is True


def test_mcp_http_app_requires_bearer_token_and_checks_host(restore_mcp_settings) -> None:
    app = mcp_server.build_http_app(host="127.0.0.1", port=8123, path="/mcp", token=TOKEN)
    with TestClient(app, base_url="http://localhost:8123") as client:
        assert client.post("/mcp", json={}).status_code == 401
        rebinding = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.test", "Content-Type": "application/json"})
        assert rebinding.status_code == 421
        # Authorized request reaches the MCP transport (which rejects the empty JSON-RPC body, not auth).
        authorized = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"})
        assert authorized.status_code not in (401, 421)


def test_mcp_wildcard_bind_with_token_disables_host_check_only(restore_mcp_settings) -> None:
    mcp_server.build_http_app(host="0.0.0.0", port=8123, path="/mcp", token=TOKEN)
    assert mcp_server.mcp.settings.transport_security.enable_dns_rebinding_protection is False


def test_mcp_session_id_requirement_follows_the_enforced_token(restore_mcp_settings, monkeypatch) -> None:
    # The token passed to build_http_app (not just the env var) decides whether
    # the most-recent-session shortcut is disabled.
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    manager = session_module.SessionManager.__new__(session_module.SessionManager)
    manager._sessions = {}
    mcp_server.build_http_app(host="127.0.0.1", port=8123, path="/mcp", token=TOKEN)
    with pytest.raises(ValueError, match="session_id is required"):
        manager.resolve_session("")

    # ...and an app built without a token keeps the loopback shortcut even if the env var is set.
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    mcp_server.build_http_app(host="127.0.0.1", port=8123, path="/mcp", token="")
    with pytest.raises(ValueError, match="No session"):
        manager.resolve_session("")
