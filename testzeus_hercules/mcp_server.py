"""
TestZeus Hercules MCP Server (streamable-http transport)
Compatible with: Claude Code, Cursor, Windsurf, any MCP-over-HTTP client.

Run:
    python mcp_server.py

Config block for mcp_servers (e.g. agents_llm_config.json):
    {
        "mcpServers": {
            "testzeus-hercules": {
                "transport": "streamable-http",
                "url": "http://localhost:8000/mcp"
            }
        }
    }

Environment variables (all optional):
    TESTZEUS_ROOT     – repo root, default cwd
    TESTZEUS_PYTHON   – python executable, default sys.executable
    MCP_HOST          – bind host,  default 0.0.0.0
    MCP_PORT          – bind port,  default 8000
    MCP_PATH          – URL path,   default /mcp
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ── configuration ─────────────────────────────────────────────────────────────

def _hercules_root() -> str:
    return os.getenv("TESTZEUS_ROOT", os.getcwd())

def _hercules_python() -> str:
    return os.getenv("TESTZEUS_PYTHON", sys.executable)

def _feature_path() -> Path:
    return Path(_hercules_root()) / "opt" / "input" / "test.feature"

def _result_dir() -> Path:
    return Path(_hercules_root()) / "opt" / "output"

def _mcp_host() -> str:
    return os.getenv("MCP_HOST", "0.0.0.0")

def _mcp_port() -> int:
    return int(os.getenv("MCP_PORT", "8000"))

def _mcp_path() -> str:
    path = os.getenv("MCP_PATH", "/mcp")
    return path if path.startswith("/") else f"/{path}"

# ── FastMCP server ─────────────────────────────────────────────────────────────

mcp = FastMCP(
    "testzeus-hercules",
    instructions=(
        "TestZeus Hercules test automation server. "
        "Use generate_gherkin to turn plain English into a Gherkin feature file, "
        "run_test to execute tests, and get_test_results to fetch results."
    ),
)

# ── tool: generate_gherkin ────────────────────────────────────────────────────

@mcp.tool()
async def generate_gherkin(description: str) -> str:
    """
    Convert a plain-English test description into a Gherkin .feature file.
    Returns the generated Gherkin text without running it.

    Args:
        description: Plain-English description of what to test.
                     Include the URL, steps, and expected outcome.
                     Example: "Go to google.com, search for TestZeus, verify results appear"
    """
    script = f"""
import asyncio, sys, os
sys.path.insert(0, {json.dumps(_hercules_root())})
os.chdir({json.dumps(_hercules_root())})
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.llm_helper import create_chat_model
from testzeus_hercules.utils.test_builder import generate_gherkin, collect_requirements

cfg = get_global_conf()
llm = create_chat_model(cfg.get_langchain_cfg(), cfg.get_adapted_llm_params())
req = collect_requirements({json.dumps(description)})

async def run():
    return await generate_gherkin(req, llm)

print(asyncio.run(run()))
"""
    proc = await asyncio.create_subprocess_exec(
        _hercules_python(), "-c", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_hercules_root(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        return "Error: Gherkin generation timed out after 60 seconds."

    result = stdout.decode().strip()
    if not result:
        err = stderr.decode().strip()
        return f"Error generating Gherkin:\n{err}"
    return result


# ── tool: run_test ────────────────────────────────────────────────────────────

@mcp.tool()
async def run_test(gherkin: str = "", description: str = "") -> str:
    """
    Run a test via TestZeus Hercules.

    Args:
        gherkin: Gherkin feature file content to run directly.
                 If omitted, uses the existing test.feature file.
        description: Plain-English description — will auto-generate Gherkin and run it.
                     Ignored if gherkin is provided.
    """
    feature_file = _feature_path()
    feature_file.parent.mkdir(parents=True, exist_ok=True)

    if gherkin:
        feature_file.write_text(gherkin)
    elif description:
        generated = await generate_gherkin(description)
        if generated.startswith("Error"):
            return generated
        feature_file.write_text(generated)
        gherkin = generated
    elif not feature_file.exists():
        return "No test to run. Provide gherkin or description, or ensure test.feature exists."

    preview = f"Running:\n{feature_file.read_text()}\n{'='*50}\n"

    cmd = [_hercules_python(), "-u", "-m", "testzeus_hercules"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_hercules_root(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        return preview + "Test timed out after 5 minutes."

    output = stdout.decode() + stderr.decode()
    return preview + _summarize_output(output)


def _summarize_output(output: str) -> str:
    keywords = ("run completed", "passed", "failed", "error", "final_response",
                 "is_passed", "assert_summary", "testcase", "results published")
    important = [
        line.strip() for line in output.splitlines()
        if any(k in line.lower() for k in keywords)
    ]
    return "\n".join(important) if important else "\n".join(output.splitlines()[-20:])


# ── tool: get_test_results ────────────────────────────────────────────────────

@mcp.tool()
async def get_test_results(run_id: str = "") -> str:
    """
    Retrieve test results from the most recent (or specified) run.

    Args:
        run_id: Specific run folder name e.g. run_20260609_132411.
                Omit to get the latest run's results.
    """
    output_dir = _result_dir()
    if not output_dir.exists():
        return "No results found. Run a test first."

    if run_id:
        run_dir = output_dir / run_id
        if not run_dir.exists():
            return f"Run '{run_id}' not found in {output_dir}."
    else:
        runs = sorted(
            [r for r in output_dir.iterdir() if r.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not runs:
            return "No test runs found."
        run_dir = runs[0]

    results: dict[str, Any] = {
        "run_id": run_dir.name,
        "files": [f.name for f in run_dir.iterdir()],
    }

    xml_files = list(run_dir.glob("*.xml"))
    if xml_files:
        try:
            tree = ET.parse(xml_files[0])
            root = tree.getroot()
            failures = root.attrib.get("failures", "0")
            errors = root.attrib.get("errors", "0")
            results.update({
                "status": "PASSED" if failures == "0" and errors == "0" else "FAILED",
                "tests": root.attrib.get("tests", "?"),
                "failures": failures,
                "errors": errors,
                "time_seconds": root.attrib.get("time", "?"),
                "test_cases": [
                    {
                        "name": tc.attrib.get("name", ""),
                        "time": tc.attrib.get("time", ""),
                        **({"failure": (tc.find("failure").attrib.get("message", "") or "")}
                           if tc.find("failure") is not None else {}),
                    }
                    for tc in root.iter("testcase")
                ],
            })
        except Exception as e:
            results["xml_error"] = str(e)

    html_files = list(run_dir.glob("*.html"))
    if html_files:
        results["html_report_path"] = str(html_files[0])

    return json.dumps(results, indent=2)


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = _mcp_host()
    port = _mcp_port()
    path = _mcp_path()
    print(f"Starting TestZeus Hercules MCP server on http://{host}:{port}{path}")
    print(f"Add to your mcp_servers config:")
    print(json.dumps({
        "mcpServers": {
            "testzeus-hercules": {
                "transport": "streamable-http",
                "url": f"http://{host}:{port}{path}",
            }
        }
    }, indent=4))
    mcp.run(transport="streamable-http")


def main() -> None:
    """Entrypoint for the testzeus-hercules-mcp console script."""
    host = _mcp_host()
    port = _mcp_port()
    path = _mcp_path()
    print(f"Starting TestZeus Hercules MCP server on http://{host}:{port}{path}")
    print("Add to your mcp_servers config:")
    import json as _json
    print(_json.dumps({
        "mcpServers": {
            "testzeus-hercules": {
                "transport": "streamable-http",
                "url": f"http://{host}:{port}{path}",
            }
        }
    }, indent=4))
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
