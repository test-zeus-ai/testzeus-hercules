# Using MCP with Hercules

This guide explains how to connect Hercules to an MCP server and execute tools, with Composio as a ready-to-use provider.

## Overview
- Hercules ships with an MCP Navigation Agent and MCP tooling.
- Supports `stdio`, `sse`, and `streamable-http` transports.
- Optimized for servers that expose tools but may not expose resources.

## Pre-requisites
- Hercules installed and running (PyPI, Docker, or source).
- A valid MCP server URL. For Composio, use the generated server URL which embeds user scoping.
  - Pattern: `https://mcp.composio.dev/composio/server/<SERVER_UUID>/mcp?user_id=<USER_EMAIL>`
  - Composio docs: https://docs.composio.dev/docs/mcp-developers

## Configure Hercules

1) Create `mcp_servers.json` in the repository root:

```json
{
  "mcpServers": {
    "server_name": {
      "transport": "streamable-http",
      "url": "https://mcp.composio.dev/composio/server/<SERVER_UUID>/mcp?user_id=<USER_EMAIL>"
    }
  }
}
```

- `server_name`: Logical name used to reference this server in tools.
- `transport`: Use `streamable-http` for Composio’s HTTP streaming transport. `stdio` and `sse` are also supported if your server provides them.
- `url`: Your server URL; replace placeholders with your actual values.

2) Enable MCP in your environment (e.g., `.env`):

```
MCP_ENABLED=true
MCP_SERVERS=mcp_servers.json
# Optional timeout in seconds
MCP_TIMEOUT=30
```



Hercules manages client contexts and sessions, and registers MCP tools for the agent to call.

## Transport Notes
- `streamable-http`: Recommended for Composio; avoids websocket restrictions and supports streaming.
- `sse`: Use if your server provides an SSE endpoint.
- `stdio`: Use for local MCP servers launched as subprocesses.

## Troubleshooting
- “Method not found” during connect
  - Cause: Server doesn’t implement resource endpoints.
  - Resolution: Hercules initializes the MCP toolkit with resources disabled by default and tools enabled. Ensure you are on a version with this behavior.
- `Server not found in connected toolkits`
  - Ensure `MCP_ENABLED=true`, `MCP_SERVERS` points to `mcp_servers.json`, and `initialize_mcp_connections` has been called.
- `Connection failed`
  - Verify the URL (UUID, user email) and that the server is reachable over HTTPS.

## Security Notes
- Do not commit user emails or tokens. Keep server URLs and environment in private configuration.
- Composio server URLs handle authentication and tool access based on your server configuration.

## References
- Composio MCP: https://docs.composio.dev/docs/mcp-developers

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
