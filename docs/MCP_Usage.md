# Using MCP with Hercules

This guide explains how to connect Hercules to an MCP server and execute tools, with Composio as a ready-to-use provider.

## Overview
- Hercules ships with an MCP Navigation Agent and MCP tooling.
- Supports `stdio`, `sse`, and `streamable-http` transports.
- Optimized for servers that expose tools but may not expose resources.

## Pre-requisites
- Hercules installed and running (PyPI, Docker, or source).
- A valid MCP server URL. For Composio v3, create a session for your user and
  use `session.mcp.url` plus any `session.mcp.headers` your session returns.
  Do not hardcode user emails, tokens, or server UUIDs into committed config.
  - Composio docs: https://docs.composio.dev/docs/quickstart

## Configure Hercules

1) Create `mcp_servers.json` in the repository root:

```json
{
  "mcpServers": {
    "server_name": {
      "transport": "streamable-http",
      "url": "<session.mcp.url>",
      "headers": {
        "Authorization": "Bearer <token-if-required>"
      }
    }
  }
}
```

- `server_name`: Logical name used to reference this server in tools.
- `transport`: Use `streamable-http` for Composio’s HTTP streaming transport. `stdio` and `sse` are also supported if your server provides them.
- `url`: Your MCP endpoint URL.
- `headers`: Optional request headers. Include them only when your MCP provider
  requires them.

2) Enable MCP in your environment (e.g., `.env`):

```
MCP_ENABLED=true
MCP_SERVERS=mcp_servers.json
# Optional timeout in seconds
MCP_TIMEOUT=30
```



Hercules manages client contexts and sessions, and registers MCP tools for the agent to call.
The canonical checked-in example filename is `mcp_servers.example.json`.

## Transport Notes
- `streamable-http`: Recommended for Composio; avoids websocket restrictions and supports streaming.
- `sse`: Use if your server provides an SSE endpoint.
- `stdio`: Use for local MCP servers launched as subprocesses.

## Troubleshooting
- “Method not found” during connect
  - Cause: Server doesn’t implement resource endpoints.
  - Resolution: Hercules initializes the MCP toolkit with resources disabled by default and tools enabled. Ensure you are on a version with this behavior.
- `Server not found in connected toolkits`
  - Ensure `MCP_ENABLED=true` and `MCP_SERVERS` points to the config file before the run starts. Hercules initializes MCP connections during helper readiness.
- `Connection failed`
  - Verify the endpoint URL, required headers, and network reachability.

## Security Notes
- Do not commit user emails or tokens. Keep server URLs and environment in private configuration.
- Composio sessions handle user scoping and tool access. Store session IDs,
  URLs, and headers as private runtime configuration.

## References
- Composio quickstart and MCP session guidance: https://docs.composio.dev/docs/quickstart

## Running Hercules as an MCP Server

Hercules can also expose its own tools to other MCP clients:

```bash
testzeus-hercules-mcp
```

By default this starts a streamable HTTP server at `http://0.0.0.0:8000/mcp`
with tools such as `generate_gherkin`, `run_test`, and `get_test_results`.

Server environment variables:

- `TESTZEUS_ROOT`: repo/project root for generated features and test output
- `TESTZEUS_PYTHON`: Python executable to use when invoking Hercules
- `MCP_HOST`: bind host, default `0.0.0.0`
- `MCP_PORT`: bind port, default `8000`
- `MCP_PATH`: HTTP path, default `/mcp`

For local stdio-style client configuration, see `mcp_hercules.example.json`.

## Example Testcase (Simple)

```gherkin
Feature: Read emails from Gmail

  Scenario: Retrieve OTP from Gmail using MCP
    Given Gmail is configured using MCP
    When I connect to Gmail
    And I read the email with OTP
```

## Concrete OTP Retrieval Scenario

The following example demonstrates a full flow using MCP tools to search, fetch, and extract an OTP from an email. Replace tool names and fields to match your MCP server’s tool schema.

```gherkin
Feature: Retrieve OTP via MCP Gmail

  Background:
    Given MCP is enabled and the "mail" server is configured
    And I initialize MCP connections
    And MCP server "mail" is connected

  Scenario: Read OTP from most recent email
    When I execute MCP tool "GMAIL_FETCH_EMAILS" on "mail" with:
      """
      {"query": "subject:(OTP OR verification code) newer_than:15m", "max_results": 5}
      """
    Then I store the first result's "id" as "message_id"

    When I execute MCP tool "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID" on "mail" with:
      """
      {"id": "${message_id}"}
      """
    Then I read OTP from the email body
    And I should see an OTP
```

Notes:
- Typical tool names on Composio MCP servers:
  - 'GMAIL_FETCH_EMAILS'
  - 'GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID'
  - 'GMAIL_FETCH_MESSAGE_BY_THREAD_ID', 
- Typical request fields:
  - For search: query, max_results
  - For get message: id or message_id
