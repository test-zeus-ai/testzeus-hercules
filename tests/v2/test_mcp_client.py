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
