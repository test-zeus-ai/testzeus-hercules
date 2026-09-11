"""End-to-end ONLINE verification of the Hercules TestPlan pipeline.

Runs the full compositional MCP flow against live services:
    configure_session → generate_code_summary → generate_standardized_prd
    → generate_test_plan → generate_and_execute (subset) → get_run_results

Usage:
    1. Provide an LLM: either fill MODEL_NAME + MODEL_API_KEY in a `.env` file
       at the repo root, or set AGENTS_LLM_CONFIG_FILE (+ REF key) yourself.
    2. uv run --no-sync python .hercules/run_online_verification.py [base_url]

The script boots the target API app (port 5055) and the MCP server (port 8000)
if they are not already running, and shuts down only what it started.
"""

import asyncio
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_PORT = 5055
MCP_PORT = 8000
BASE_URL = f"http://127.0.0.1:{MCP_PORT}/mcp"
APP_URL = f"http://127.0.0.1:{APP_PORT}"
PRD_PATH = REPO / ".hercules" / "api_app_prd.md"
CONFIG_PATH = REPO / "agents_llm_config.json"
EXECUTE_TEST_IDS = 3  # keep live execution cost small

_started: list[subprocess.Popen] = []


def preflight_llm() -> str:
    """Ensure an LLM config exists; write one from .env-style env when possible."""
    if CONFIG_PATH.exists() or os.getenv("AGENTS_LLM_CONFIG_FILE"):
        return "using existing agents config"
    model = os.getenv("MODEL_NAME")
    key = os.getenv("MODEL_API_KEY")
    if not model or not key:
        print(
            "PREFLIGHT FAIL: no LLM configured.\n"
            "  Fill MODEL_NAME and MODEL_API_KEY in .env at the repo root, e.g.:\n"
            "    MODEL_NAME=gpt-5-mini\n"
            "    MODEL_API_KEY=sk-...\n"
            "  then re-run this script."
        )
        sys.exit(1)
    stanza = {
        "litellm": {
            role: {
                "model_name": model,
                "model_api_key": key,
                "llm_config_params": {"cache_seed": None, "temperature": 0.0, "seed": 12345},
            }
            for role in ("planner_agent", "nav_agent", "helper_agent")
        }
    }
    CONFIG_PATH.write_text(json.dumps(stanza, indent=2))
    print(f"PREFLIGHT: wrote {CONFIG_PATH.name} for model {model} (all 3 roles)")
    return "generated"


def probe(url: str, timeout: float = 3.0) -> bool:
    """Alive = anything answers, incl. HTTP 4xx (service up, route rejects GET)."""
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def ensure_app_up() -> None:
    if probe(f"{APP_URL}/docs"):
        print(f"PREFLIGHT: target app already running at {APP_URL}")
        return
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "fastapi",
            "--with",
            "fastapi-users[sqlalchemy]",
            "--with",
            "aiosqlite",
            "--with",
            "sqlalchemy",
            "--with",
            "uvicorn",
            "--with",
            "greenlet",
            "python",
            "-c",
            (
                "import uvicorn, importlib.util\n"
                f"spec = importlib.util.spec_from_file_location('apiapp', '{REPO / 'tests' / 'api_testing_app' / 'main.py'}')\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                f"uvicorn.run(m.app, host='127.0.0.1', port={APP_PORT}, log_level='warning')\n"
            ),
        ],
        cwd=str(REPO / "tests" / "api_testing_app"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _started.append(proc)
    for _ in range(30):
        if probe(f"{APP_URL}/docs"):
            print(f"PREFLIGHT: started target app at {APP_URL} (pid {proc.pid})")
            return
        time.sleep(1)
    print("PREFLIGHT FAIL: target API app did not come up.")
    sys.exit(1)


def ensure_mcp_up() -> None:
    if probe(BASE_URL):
        print("PREFLIGHT: MCP server already running")
        return
    env = dict(os.environ, MCP_HOST="127.0.0.1", MCP_PORT=str(MCP_PORT))
    proc = subprocess.Popen(
        ["uv", "run", "--no-sync", "python", "-m", "testzeus_hercules.mcp_server"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _started.append(proc)
    for _ in range(30):
        if probe(BASE_URL):
            print(f"PREFLIGHT: started MCP server at {BASE_URL} (pid {proc.pid})")
            return
        time.sleep(1)
    print("PREFLIGHT FAIL: MCP server did not come up.")
    sys.exit(1)


async def mcp_call(session, name: str, args: dict):
    from mcp.types import TextContent

    result = await session.call_tool(name, args)
    raw = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}


async def run_flow() -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    failures: list[str] = []
    async with streamablehttp_client(BASE_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"STEP 1 connect: OK ({len((await session.list_tools()).tools)} tools)")

            check = await mcp_call(session, "hercules_check_config", {"ping_model": True})
            llm_ok = check.get("ok") is True or check.get("checks", {}).get("litellm_model", {}).get("ok") is True
            print(f"STEP 2 check_config(ping): {'OK' if llm_ok else 'FAIL → ' + json.dumps(check)[:300]}")
            if not llm_ok:
                return 1

            cfg = await mcp_call(
                session,
                "hercules_configure_session",
                {
                    "app_url": APP_URL,
                    "project_path": str(REPO / "tests" / "api_testing_app"),
                    "test_type": "backend",
                    "prd_path": str(PRD_PATH),
                },
            )
            sid = cfg.get("session_id", "")
            print(f"STEP 3 configure_session: session={sid} reachable={cfg.get('app_url_reachable')}")
            if not sid:
                failures.append("configure_session")

            prd = await mcp_call(session, "hercules_generate_standardized_prd", {"session_id": sid})
            n_req = prd.get("requirement_count", 0)
            print(f"STEP 4 standardized_prd: {n_req} requirements ({prd.get('standard_prd_path', '')})")
            if not n_req:
                failures.append(f"standardized_prd: {json.dumps(prd)[:200]}")

            plan = await mcp_call(session, "hercules_generate_test_plan", {"session_id": sid, "cases_per_requirement": 3})
            cases = plan.get("test_cases") or []
            cats = sorted({c.get("category", "?") for c in cases})
            cov = (plan.get("coverage") or {}).get("totals", {})
            print(f"STEP 5 test_plan: {plan.get('case_count', len(cases))} TCs, categories={cats}, coverage={cov}")
            print("        " + ", ".join(f"{c.get('id')}:{c.get('title')}" for c in cases[:10]))
            if len(cases) < 6:
                failures.append(f"test_plan only {len(cases)} cases")

            subset = [c["id"] for c in cases[:EXECUTE_TEST_IDS]]
            exe = await mcp_call(session, "hercules_generate_and_execute", {"session_id": sid, "test_ids": subset})
            total = exe.get("summaries", [{}])[0] if exe.get("summaries") else {}
            print(
                f"STEP 6 execute({subset}): success={exe.get('success')} "
                f"total={total.get('total')} passed={total.get('passed')} failed={total.get('failed')} "
                f"errors={total.get('errors')} steps={exe.get('steps_completed')}"
            )
            print(f"        report={total.get('report_md')} bundles={len(total.get('bundles') or [])}")
            if not exe.get("success"):
                failures.append(f"execute: {json.dumps(exe)[:300]}")

            res = await mcp_call(session, "hercules_get_run_results", {"session_id": sid})
            print(f"STEP 7 get_run_results: {json.dumps({k: res.get(k) for k in ('run_id', 'total', 'passed', 'failed', 'errors')})}")

    print("\n=== VERDICT:", "PASS ✅" if not failures else f"FAIL ❌ → {failures}", "===")
    return 0 if not failures else 1


def main() -> int:
    preflight_llm()
    ensure_app_up()
    ensure_mcp_up()
    try:
        return asyncio.run(run_flow())
    finally:
        for proc in _started:
            proc.send_signal(signal.SIGINT)
        for proc in _started:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if _started:
            print(f"cleanup: stopped {len(_started)} service(s) this script started")


if __name__ == "__main__":
    raise SystemExit(main())
