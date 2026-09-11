"""Live smoke-test client for the Hercules MCP server (streamable-http).

Drives the LLM-free tools over a real MCP round-trip:
  1. hercules_check_config
  2. hercules_configure_session
  3. hercules_generate_code_summary
  4. hercules_generate_standardized_prd  (expected: graceful error without an LLM key)
  5. hercules_get_run_results            (expected: no-runs notice)

Usage: python mcp_smoke_client.py [base_url]
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def call_tool(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    texts = [block.text for block in result.content if getattr(block, "text", None)]
    raw = "\n".join(texts)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}


async def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
    async with streamablehttp_client(base_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"[1] CONNECTED — {len(tools.tools)} tools:", sorted(t.name for t in tools.tools))

            check = await call_tool(session, "hercules_check_config", {"ping_model": False})
            print(f"[2] hercules_check_config → {json.dumps(check, indent=2)[:800]}")

            cfg = await call_tool(
                session,
                "hercules_configure_session",
                {"app_url": "http://127.0.0.1:5055", "project_path": "/home/wxsys/Project/testzeus-hercules/tests/api_testing_app", "test_type": "backend"},
            )
            print(f"[3] hercules_configure_session → {json.dumps(cfg, indent=2)[:600]}")
            session_id = cfg.get("session_id", "")

            summary = await call_tool(session, "hercules_generate_code_summary", {"session_id": session_id})
            print(f"[4] hercules_generate_code_summary → {json.dumps(summary, indent=2)[:900]}")

            prd = await call_tool(session, "hercules_generate_standardized_prd", {"session_id": session_id})
            ok_graceful = "error" in prd
            print(f"[5] hercules_generate_standardized_prd → graceful-error={ok_graceful}: {str(prd)[:400]}")

            results = await call_tool(session, "hercules_get_run_results", {"session_id": session_id})
            print(f"[6] hercules_get_run_results → {str(results)[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
