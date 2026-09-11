"""Resume the online verification at the EXECUTE step using an existing session.

Usage: uv run --no-sync python .hercules/resume_execute.py <session_id> [TC001,TC002,...]
"""

import asyncio
import json
import sys

MCP_URL = "http://127.0.0.1:8000/mcp"


async def mcp_call(session, name: str, args: dict):
    from mcp.types import TextContent

    result = await session.call_tool(name, args)
    raw = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}


async def main() -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    session_id = sys.argv[1]
    test_ids = sys.argv[2].split(",") if len(sys.argv) > 2 else ["TC001", "TC002", "TC003"]

    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            exe = await mcp_call(session, "hercules_generate_and_execute", {"session_id": session_id, "test_ids": test_ids})
            total = exe.get("summaries", [{}])[0] if exe.get("summaries") else {}
            print(
                "EXECUTE:",
                json.dumps(
                    {
                        "success": exe.get("success"),
                        "error": exe.get("error"),
                        "duration_s": exe.get("duration_s"),
                        "total": total.get("total"),
                        "passed": total.get("passed"),
                        "failed": total.get("failed"),
                        "errors": total.get("errors"),
                        "steps_completed": exe.get("steps_completed"),
                        "report_md": total.get("report_md"),
                        "bundles": len(total.get("bundles") or []),
                        "classifications": total.get("classifications"),
                        "run_id": total.get("run_id"),
                    },
                    indent=1,
                ),
            )
            res = await mcp_call(session, "hercules_get_run_results", {"session_id": session_id})
            print("RUN_RESULTS:", json.dumps(res.get("test_cases", res), indent=1)[:2000])
            return 0 if exe.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
