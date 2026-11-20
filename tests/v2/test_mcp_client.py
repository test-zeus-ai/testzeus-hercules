"""The MCP shim's daemon client uses only a daemon serving its own project."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from testzeus_hercules.mcp.daemon_client import DaemonClient, DaemonUnavailable
from tests.v2.fixtures.daemon_support import daemon_thread, make_config


async def _ensure(env: dict[str, str], project: Path) -> str:
    client = DaemonClient(env=env, project_dir=project, autostart=False)
    try:
        info = await client.ensure()
        return str(info.project_dir)
    finally:
        await client.aclose()


def test_a_daemon_of_another_project_is_refused_with_a_clear_error(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    other = tmp_path / "other-project"
    other.mkdir()
    with daemon_thread(config):
        with pytest.raises(DaemonUnavailable, match="serves another project"):
            asyncio.run(_ensure(dict(config.env), other))
        assert asyncio.run(_ensure(dict(config.env), config.settings.project_dir)) == str(config.settings.project_dir)


def test_an_environment_update_reads_again_when_the_ui_changed_it_meanwhile(tmp_path: Path) -> None:
    """hercules_update_environment saves with If-Match; when a tester's edit lands between its read and its save, it
    reads again and applies its change onto theirs instead of overwriting it."""
    from testzeus_hercules.mcp.tools import HerculesTools

    config = make_config(tmp_path)

    async def scenario() -> dict[str, object]:
        client = DaemonClient(env=dict(config.env), project_dir=config.settings.project_dir, autostart=False)
        send = client.request
        edits: list[str] = []

        async def request(method: str, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
            headers = kwargs.get("headers") or {}
            if method == "POST" and "If-Match" in headers and not edits:  # type: ignore[operator]
                edits.append("ui")
                await send("POST", "/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9", "device": "iPhone 15", "overwrite": True})
            return await send(method, path, **kwargs)  # type: ignore[arg-type]

        client.request = request  # type: ignore[method-assign]
        tools = HerculesTools(client)
        try:
            await send("POST", "/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9"})
            out = await tools.call("hercules_update_environment", {"name": "shop", "viewport": "1280x720"})
            return out.structured["environment"]  # type: ignore[return-value]
        finally:
            await client.aclose()

    with daemon_thread(config):
        env = asyncio.run(scenario())
    assert env["viewport"] == "1280x720" and env["device"] == "iPhone 15", env


def test_folder_tools_ask_for_a_restart_when_the_daemon_is_older_than_dry_run() -> None:
    import httpx

    from testzeus_hercules.mcp.tools import HerculesTools, ToolError

    class OldDaemon:
        async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
            if path == "/api/runs/batch":
                return httpx.Response(422, json={"error": "dry_run: Extra inputs are not permitted"})
            return httpx.Response(200, json={"cases": [], "folders": [], "truncated": False})

    with pytest.raises(ToolError, match="older than these tools: restart it"):
        asyncio.run(HerculesTools(OldDaemon()).call("hercules_list_folders", {}))  # type: ignore[arg-type]
