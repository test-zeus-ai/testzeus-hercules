"""``hercules mcp``: MCP over stdio for coding agents, backed by the daemon's HTTP API.

Startup only builds the tool list (no daemon contact), so ``initialize`` answers in
well under a second. The daemon is found or started on the first tool call.
Imports stay small: the MCP SDK's stdio server (without its HTTP stack, see
:mod:`testzeus_hercules.mcp._lazy`), ``httpx`` and local modules; never Starlette,
Playwright, LangGraph or LangChain.
"""

from __future__ import annotations

from testzeus_hercules.mcp import _lazy

_lazy.install()

import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Iterable, Mapping, TextIO  # noqa: E402

import anyio  # noqa: E402
import httpx  # noqa: E402
import mcp.types as types  # noqa: E402
from mcp.server.lowlevel.helper_types import ReadResourceContents  # noqa: E402
from mcp.server.lowlevel.server import NotificationOptions, Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from pydantic import AnyUrl  # noqa: E402

from testzeus_hercules.mcp.daemon_client import DaemonClient, DaemonUnavailable  # noqa: E402
from testzeus_hercules.mcp.specs import INSTRUCTIONS, RESOURCE_TEMPLATES, TOOLS  # noqa: E402
from testzeus_hercules.mcp.tools import HerculesTools, ToolError  # noqa: E402

SERVER_NAME = "hercules"
_RESOURCE = re.compile(r"^hercules://runs/([A-Za-z0-9_.-]{1,128})/([A-Za-z0-9._/-]{1,512})$")
_RECENT_RUNS = 10


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("testzeus-hercules")
    except PackageNotFoundError:
        return "0+unknown"


def _agent_of(session: Any) -> str | None:
    """The calling coding agent's fixed name, from the client name it sent in ``initialize``."""
    from testzeus_hercules.kernel.telemetry import agent_client_from_name

    params = getattr(session, "client_params", None)
    info = getattr(params, "clientInfo", None)
    return agent_client_from_name(getattr(info, "name", None))


def build_server(client: DaemonClient) -> Server[Any, Any]:
    server: Server[Any, Any] = Server(SERVER_NAME, version=_version(), instructions=INSTRUCTIONS)
    tools = HerculesTools(client)
    definitions = [types.Tool.model_validate(spec) for spec in TOOLS]

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return definitions

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        context = server.request_context
        if client.agent is None:
            client.agent = _agent_of(context.session)
        token = context.meta.progressToken if context.meta is not None else None

        async def progress(value: float, total: float | None, message: str | None) -> None:
            if token is not None:
                await context.session.send_progress_notification(token, value, total, message, related_request_id=context.request_id)

        async def elicit(message: str, schema: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
            with anyio.move_on_after(timeout_s):
                result = await context.session.elicit(message, schema, related_request_id=context.request_id)
                return dict(result.content or {}) if result.action == "accept" else None
            return None

        supports_elicitation = context.session.check_client_capability(types.ClientCapabilities(elicitation=types.ElicitationCapability()))
        try:
            output = await tools.call(name, arguments, progress, elicit if supports_elicitation else None)
        except ToolError as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], isError=True)
        return types.CallToolResult(content=output.content(), structuredContent=output.structured, isError=False)

    @server.list_resource_templates()
    async def list_templates() -> list[types.ResourceTemplate]:
        return [types.ResourceTemplate.model_validate(template) for template in RESOURCE_TEMPLATES]

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        try:
            response = await client.request("GET", "/api/runs", params={"limit": _RECENT_RUNS}, start=False)
        except (DaemonUnavailable, OSError, httpx.HTTPError):
            return []
        if response.status_code != 200:
            return []
        found = []
        for run in response.json().get("runs", []):
            if run.get("finished"):
                uri = f"hercules://runs/{run['run_id']}/run.json"
                found.append(types.Resource(uri=AnyUrl(uri), name=f"{run['run_id']} ({run['status']})", mimeType="application/json", description=run.get("title")))
        return found

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
        parsed = _RESOURCE.match(str(uri))
        if parsed is None or ".." in str(uri):
            raise ValueError("unknown resource: use hercules://runs/<run_id>/<file>")
        run_id, relative = parsed.groups()
        response = await client.request("GET", f"/api/runs/{run_id}/evidence/{relative}")
        if response.status_code != 200:
            raise ValueError(f"resource not found: {uri}")
        mime = response.headers.get("content-type", "application/octet-stream").split(";")[0]
        textual = mime.startswith("text/") or mime in ("application/json", "application/x-ndjson", "application/xml")
        return [ReadResourceContents(content=response.text if textual else response.content, mime_type=mime)]

    return server


async def serve_stdio(env: Mapping[str, str] | None = None, project_dir: Path | None = None) -> None:
    client = DaemonClient(env=env, project_dir=project_dir)
    server = build_server(client)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options(NotificationOptions()))
    finally:
        await client.aclose()


def running_notice(project_dir: Path) -> str:
    """What ``hercules mcp`` says when a person starts it in a terminal (an MCP client never sees it)."""
    return (
        f"Hercules MCP server {_version()} is running on stdio ({len(TOOLS)} tools, project {project_dir}).\n"
        "It waits for an MCP client such as Claude Code or Cursor, which starts it from its MCP config. Press Ctrl+C to stop.\n"
    )


def main(env: Mapping[str, str] | None = None, *, project_dir: Path | None = None, stdin: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Entry point of ``hercules mcp``. Started by hand in a terminal, it says on stderr that it is running (stdout carries
    only the protocol); started by an MCP client, whose stdin is a pipe, it prints nothing."""
    project_dir = Path(os.path.abspath(project_dir)) if project_dir is not None else Path.cwd()
    interactive = (stdin or sys.stdin).isatty()
    err = stderr or sys.stderr
    if interactive:
        err.write(running_notice(project_dir))
        err.flush()
    try:
        anyio.run(serve_stdio, dict(os.environ if env is None else env), project_dir)
    except KeyboardInterrupt:
        if interactive:
            err.write("Hercules MCP server stopped.\n")
    return 0
