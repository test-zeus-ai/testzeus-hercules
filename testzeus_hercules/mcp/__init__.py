"""``hercules mcp``: the stdio MCP companion for coding agents (Claude Code, Codex, Cursor).

A thin shim: it imports only the ``mcp`` SDK, ``httpx`` and a few small local
modules, and maps MCP tools onto the daemon's HTTP API. Keep this file free of
imports.
"""
