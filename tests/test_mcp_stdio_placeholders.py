"""Offline tests for MCP stdio launch configuration.

Covers the per-run ``${placeholder}`` expansion and the env passthrough for
stdio MCP servers: a stdio server is a separate process launched from a fixed
config, so it cannot know the current run's output locations, and the MCP SDK
hands stdio processes a scrubbed environment (configured env vars would be
dropped on the floor without an explicit merge).

No real MCP server or subprocess is launched: ``StdioServerParameters``,
``stdio_client`` and ``ClientSession`` are stubbed and the constructed
parameters are captured for inspection.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

from testzeus_hercules.utils import mcp_help
from testzeus_hercules.utils.mcp_help import MCPHelper


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class _FakeConf:
    """Stub for the pieces of the global conf the stdio launcher reads."""

    def __init__(self, tmp_dir: str) -> None:
        self._tmp_dir = tmp_dir

    def get_mcp_timeout(self) -> int:
        return 30

    def get_default_test_id(self) -> str:
        return "scenario-42"

    def get_proof_path(self, test_id: Any = None) -> str:
        return f"{self._tmp_dir}/proofs/{test_id}"

    def get_source_log_folder_path(self, test_id: Any = None) -> str:
        return f"{self._tmp_dir}/logs/{test_id}"

    def get_project_source_root(self) -> str:
        return f"{self._tmp_dir}/project"


class _ParamRecorder:
    """Captures the StdioServerParameters the launcher constructs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


def _install_stubs(monkeypatch, tmp_dir: str) -> tuple[_ParamRecorder, list]:
    conf = _FakeConf(str(tmp_dir))
    recorder = _ParamRecorder()
    sessions: list = []

    class _FakeSession:
        def __init__(self, read: Any, write: Any, read_timeout_seconds: Any = None) -> None:
            sessions.append(self)
            self.initialized = False

        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, *exc_info: Any) -> bool:
            return False

        async def initialize(self) -> None:
            self.initialized = True

        async def list_tools(self) -> Any:
            return SimpleNamespace(tools=[])

    class _FakeStdioClient:
        """Stands in for the async context manager stdio_client() returns."""

        def __init__(self, server_params: Any) -> None:
            self.params = server_params

        async def __aenter__(self) -> tuple[None, None]:
            return (None, None)

        async def __aexit__(self, *exc_info: Any) -> bool:
            return False

    def fake_stdio_client(server_params: Any) -> _FakeStdioClient:
        return _FakeStdioClient(server_params)

    monkeypatch.setattr(mcp_help, "get_global_conf", lambda: conf)
    monkeypatch.setattr(mcp_help, "StdioServerParameters", recorder)
    monkeypatch.setattr(mcp_help, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp_help, "ClientSession", _FakeSession)
    return recorder, sessions


def test_stdio_args_and_env_get_placeholders_expanded(monkeypatch, tmp_path) -> None:
    recorder, sessions = _install_stubs(monkeypatch, tmp_path)
    helper = MCPHelper()

    config = {
        "transport": "stdio",
        "command": "python",
        "args": [
            "--proof",
            "${proof_path}",
            "--test",
            "${test_id}",
            "--logs",
            "${log_path}/mcp.log",
            "--base",
            "${project_base}",
            "--keep-unknown",
            "${definitely_not_a_placeholder}",
        ],
        "env": {"MY_RUN_ID": "run-${test_id}", "MY_PROOF": "${proof_path}"},
    }

    assert run(helper.connect_mcp_server("srv", config)) is True
    assert recorder.calls, "StdioServerParameters must be constructed for stdio transport"

    params = recorder.calls[0]
    test_id = "scenario-42"
    proof = f"{tmp_path}/proofs/{test_id}"
    assert params["args"] == [
        "--proof",
        proof,
        "--test",
        test_id,
        "--logs",
        f"{tmp_path}/logs/{test_id}/mcp.log",
        "--base",
        f"{tmp_path}/project",
        "--keep-unknown",
        "${definitely_not_a_placeholder}",
    ]

    env = params["env"]
    # Configured env vars must survive: the SDK default environment is the base
    # and the configured values are merged over it, expanded and stringified.
    assert env["MY_RUN_ID"] == f"run-{test_id}"
    assert env["MY_PROOF"] == proof
    for key, value in mcp_help.get_default_environment().items():
        assert env.get(key) == value, f"default env var {key} must be passed through to the stdio server"
    assert sessions and sessions[0].initialized


def test_stdio_without_env_block_still_gets_default_environment(monkeypatch, tmp_path) -> None:
    recorder, _sessions = _install_stubs(monkeypatch, tmp_path)
    helper = MCPHelper()

    config = {"transport": "stdio", "command": "python", "args": []}
    assert run(helper.connect_mcp_server("srv", config)) is True

    env = recorder.calls[0]["env"]
    assert env == mcp_help.get_default_environment()


def test_expand_leaves_unknown_names_and_non_strings_alone() -> None:
    placeholders = {"test_id": "t1", "proof_path": "/proofs/t1"}

    assert MCPHelper._expand("--proof ${proof_path}", placeholders) == "--proof /proofs/t1"
    assert MCPHelper._expand("${unknown_name}", placeholders) == "${unknown_name}"
    assert MCPHelper._expand("no placeholders here", placeholders) == "no placeholders here"
    assert MCPHelper._expand(42, placeholders) == 42
    assert MCPHelper._expand(None, placeholders) is None


def test_run_placeholders_expose_run_locations(monkeypatch, tmp_path) -> None:
    conf = _FakeConf(str(tmp_path))
    monkeypatch.setattr(mcp_help, "get_global_conf", lambda: conf)

    placeholders = MCPHelper()._run_placeholders()

    assert placeholders == {
        "test_id": "scenario-42",
        "proof_path": f"{tmp_path}/proofs/scenario-42",
        "log_path": f"{tmp_path}/logs/scenario-42",
        "project_base": f"{tmp_path}/project",
    }
