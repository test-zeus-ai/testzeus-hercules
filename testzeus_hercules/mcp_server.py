"""
TestZeus Hercules MCP Server (streamable-http transport)
Compatible with: Claude Code, Cursor, Windsurf, any MCP-over-HTTP client.

Runs Hercules in-process (no subprocess): tools operate on a session that
groups the app under test, the generated artifacts (code summary, standardized
PRD, test plan) and run results.

Run:
    testzeus-hercules-mcp   (or)   python -m testzeus_hercules.mcp_server

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
    TESTZEUS_PYTHON   – reserved, currently unused
    MCP_HOST          – bind host,  default 127.0.0.1
    MCP_PORT          – bind port,  default 8000
    MCP_PATH          – URL path,   default /mcp
    MCP_AUTH_TOKEN    – bearer token required on every request when set; REQUIRED
                        (>= 16 chars) to bind a non-loopback MCP_HOST
    MCP_ALLOWED_HOSTS – extra comma-separated Host header names to accept
    HERCULES_MCP_EXEC_TIMEOUT – per-run execution deadline in seconds, default 1800
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import weakref
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Module-level imports for every collaborator the tools use, so tests (and
# callers) can monkeypatch symbols in THIS namespace, e.g.
# ``monkeypatch.setattr(mcp_server, "execute_feature_file", fake)``.
# Only the very heavy core.runner/__main__ chain stays lazy (imported inside
# orchestrator.runner_bridge.execute_feature_file).
from testzeus_hercules.intelligence.coverage import coverage_matrix
from testzeus_hercules.intelligence.normalized_prd import (
    NormalizedPRD,
    prd_from_json,
    prd_to_json,
)
from testzeus_hercules.intelligence.prd_parser import build_normalized_prd
from testzeus_hercules.intelligence.test_plan import (
    generate_test_plan,
    plan_from_json,
    plan_to_json,
)
from testzeus_hercules.orchestrator.code_scan.code_summary import (
    generate_code_summary,
    save_summary,
)
from testzeus_hercules.orchestrator.runner_bridge import (
    describe_run,
    execute_feature_file,
    write_plan_scenarios_feature,
)
from testzeus_hercules.orchestrator.session import (
    MAX_RUN_SUMMARIES,
    SessionState,
    get_session_manager,
    redact_url,
)
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.gherkin_generator import generate_gherkin_from_description
from testzeus_hercules.utils.http_security import (
    BearerTokenMiddleware,
    InsecureBindError,
    build_host_allowlist,
    enforce_bind_policy,
    normalize_token,
    parse_csv,
)
from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model
from testzeus_hercules.utils.path_guard import PathOutsideRootsError, resolve_within

# ── configuration ─────────────────────────────────────────────────────────────


def _hercules_root() -> str:
    return os.getenv("TESTZEUS_ROOT", os.getcwd())


def _feature_path() -> Path:
    return Path(_hercules_root()) / "opt" / "input" / "test.feature"


def _run_feature_path() -> Path:
    """Unique feature file for a single hercules_generate_and_execute run.

    A fresh name per call keeps concurrent runs from clobbering each other's
    input file (each run is additionally serialized by ``_EXEC_LOCK``).
    """
    return Path(_hercules_root()) / "opt" / "input" / f"hercules_mcp_run_{uuid4().hex[:8]}.feature"


def _result_dir() -> Path:
    return Path(_hercules_root()) / "opt" / "output"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _allowed_roots() -> list[str]:
    """Directories MCP clients may point project_path/prd_path at.

    The Hercules root (TESTZEUS_ROOT or cwd), the configured PROJECT_SOURCE_ROOT /
    TEST_DATA_PATH, and any entries of ``HERCULES_MCP_ALLOWED_ROOTS``
    (``os.pathsep``-separated). Client-supplied paths outside these are refused.
    """
    roots = [_hercules_root()]
    try:
        from testzeus_hercules.config import get_global_conf

        conf = get_global_conf()
        roots += [conf.get_project_source_root(), conf.get_test_data_path()]
    except Exception:  # config import is best-effort; the Hercules root still applies
        pass
    roots += [item for item in os.getenv("HERCULES_MCP_ALLOWED_ROOTS", "").split(os.pathsep) if item.strip()]
    return roots


def _confine_path(path: str, label: str, extra_roots: tuple[str, ...] = ()) -> str:
    """Resolve a client-supplied local path inside the allowed roots (realpath + prefix check)."""
    return resolve_within(path, [*_allowed_roots(), *extra_roots], label=label)


def _display_path(path: str | Path) -> str:
    """Render a filesystem path for tool error messages.

    Absolute paths in error strings leak the operator's directory layout to
    remote MCP clients, so errors show the path relative to the Hercules root
    (e.g. ``opt/output``) — or, for paths outside it, just the basename.
    """
    try:
        return str(Path(path).resolve().relative_to(Path(_hercules_root()).resolve()))
    except ValueError:
        return Path(path).name


def _state_dir() -> Path:
    """Root state directory (``.hercules``): sessions.json plus per-session artifact dirs."""
    return Path(_hercules_root()) / ".hercules"


def _session_state_dir(state: SessionState) -> Path:
    """Per-session artifact directory ``.hercules/<session_id>`` (created on demand).

    standard_prd.json, test_plan.json and code_summary.json are namespaced per
    session so concurrent or fresh sessions can neither overwrite nor silently
    consume another session's artifacts.
    """
    if not _RUN_ID_RE.fullmatch(state.session_id) or state.session_id in {".", ".."}:
        raise ValueError(f"Invalid session_id {state.session_id!r}: cannot map to a session artifact directory.")
    directory = _state_dir() / state.session_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _mcp_host() -> str:
    # Loopback by default (matches the dashboard's DEFAULT_HOST): binding all
    # interfaces out of the box would expose an unauthenticated execution server.
    return os.getenv("MCP_HOST", "127.0.0.1")


def _mcp_port() -> int:
    return int(os.getenv("MCP_PORT", "8000"))


def _mcp_path() -> str:
    path = os.getenv("MCP_PATH", "/mcp")
    return path if path.startswith("/") else f"/{path}"


def _error(message: str, **extra: Any) -> dict:
    """Build a uniform error dict returned by tools instead of raising."""
    return {"error": message, **extra}


# ── run-safety constants & helpers ────────────────────────────────────────────

# In-process runs must not overlap: execute_feature_file mutates the process-wide
# SingletonConfigManager global config and registers listeners on the shared
# notification hub, so two concurrent runs would clobber each other's state.
# One lock per running event loop: an asyncio.Lock binds to the loop that first
# awaits it, so a single module-level instance breaks when several loops use
# this module across a process's lifetime (tests, embedded servers).
_EXEC_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _exec_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _EXEC_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _EXEC_LOCKS[loop] = lock
    return lock


_DEFAULT_EXEC_TIMEOUT_S = 1800.0  # per-run deadline; override via HERCULES_MCP_EXEC_TIMEOUT
_GHERKIN_GENERATION_TIMEOUT_S = 120.0  # deadline for description->Gherkin LLM generation held under _EXEC_LOCK (a hung LLM call must not pin the run lock for the full run timeout)
_MODEL_PING_TIMEOUT_S = 60.0  # deadline for the hercules_check_config LLM ping
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _exec_timeout() -> float:
    """Execution deadline (seconds) for the run tools; HERCULES_MCP_EXEC_TIMEOUT, default 1800."""
    raw = os.getenv("HERCULES_MCP_EXEC_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_EXEC_TIMEOUT_S
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_EXEC_TIMEOUT_S


def _run_id_error(run_id: str) -> str | None:
    """Return an error message when ``run_id`` could escape the output dir, else None."""
    if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        return f"Invalid run_id {run_id!r}: expected a run folder name (letters, digits, '.', '_', '-')."
    return None


def _recorded_feature_error(feature_path: str) -> str | None:
    """Return an error message when a recorded feature file must not be re-executed, else None.

    Only paths ending in ``.feature`` inside this root's ``opt/input`` directory
    are accepted (that covers the unique ``hercules_mcp_run_<id>.feature`` files
    and the legacy ``opt/input/hercules_mcp_run.feature``); anything else is
    treated as stale/tampered session state.
    """
    message = "recorded feature file is no longer available; run a test first"
    if not feature_path.endswith(".feature"):
        return message
    expected_dir = os.path.realpath(_feature_path().parent)
    candidate = os.path.realpath(feature_path)
    try:
        if os.path.commonpath([candidate, expected_dir]) != expected_dir:
            return message
    except ValueError:  # e.g. relative paths on different Windows drives
        return message
    return None


def _list_subdirs(directory: Path) -> list[Path]:
    """List subdirectories of ``directory``, skipping entries that vanish mid-enumeration."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    subdirs: list[Path] = []
    for entry in entries:
        try:
            if entry.is_dir():
                subdirs.append(entry)
        except OSError:
            continue
    return subdirs


def _sorted_newest_first(paths: list[Path]) -> list[Path]:
    """Sort paths by mtime, newest first; entries that vanished sort last instead of raising."""

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return float("-inf")

    return sorted(paths, key=_mtime, reverse=True)


# ── FastMCP server ─────────────────────────────────────────────────────────────

mcp = FastMCP(
    "testzeus-hercules",
    instructions=(
        "TestZeus Hercules end-to-end test automation server (in-process execution). "
        "Compositional flow: (1) hercules_configure_session — register the app under test, project path and optional PRD; "
        "(2) hercules_generate_code_summary — scan the codebase for frameworks/routes/entry points; "
        "(3) hercules_generate_standardized_prd — turn a PRD document into normalized requirements; "
        "(4) hercules_generate_test_plan — generate test cases with requirement coverage; "
        "(5) hercules_generate_and_execute — run raw Gherkin, a plain-English description, or a reviewed subset of the plan (test_ids + approve_plan=true); "
        "(6) hercules_get_run_results — fetch per-scenario statuses, markdown report and failure bundles. "
        "Utilities: hercules_check_config (diagnose LLM config / browser setup), hercules_rerun (re-run plan cases or last run), "
        "hercules_open_dashboard (locate the latest report). "
        "Every tool takes an optional session_id; when omitted the most recent session is used. "
        "Sessions persist (app_url userinfo redacted; credentials are never stored) under .hercules/sessions.json."
    ),
)


# ── shared helpers ────────────────────────────────────────────────────────────


async def _check_app_url(url: str, timeout: float = 5.0) -> bool | None:
    """Best-effort reachability probe; True/False, or None when not checkable."""
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for method in (client.head, client.get):
                try:
                    response = await method(url)
                    if response.status_code < 500:
                        return True
                except httpx.HTTPError:
                    continue
            return False
    except Exception:
        return False


def _resolve_session(session_id: str = "") -> SessionState:
    """Resolve a session, raising KeyError/ValueError with tool-friendly messages."""
    return get_session_manager().resolve_session(session_id)


def _resolve_prd_source(state: SessionState, prd_path: str) -> str | None:
    """Pick a PRD source: explicit path > session PRD > project README.md."""
    source = prd_path or state.prd_path
    if source:
        return source
    if state.project_path:
        readme = os.path.join(state.project_path, "README.md")
        if os.path.isfile(readme):
            return readme
    return None


async def _build_standard_prd(state: SessionState, prd_path: str) -> tuple[NormalizedPRD, str]:
    """Build a normalized PRD from ``prd_path``/session context and save it under the session's artifact dir."""
    source = _resolve_prd_source(state, prd_path)
    if not source:
        raise ValueError("No PRD source available. Pass prd_path (file or URL), configure prd_path via " "hercules_configure_session, or set the session's project_path so README.md can be used.")
    if not source.startswith(("http://", "https://")):
        # Local PRD sources are read and sent to the LLM: confine them (realpath) to the
        # allowed roots plus this session's project_path. URLs go through the SSRF guard in prd_parser.
        source = _confine_path(source, "prd_path", (state.project_path,) if state.project_path else ())
    prd = await build_normalized_prd(source)
    out_path = os.path.join(str(_session_state_dir(state)), "standard_prd.json")
    prd_to_json(prd, out_path)
    state.standard_prd_path = out_path
    get_session_manager().save_session_state(state)
    return prd, out_path


async def _ensure_standard_prd(state: SessionState, prd_path: str) -> tuple[NormalizedPRD, str]:
    """Load the session's cached standardized PRD; ``prd_path`` forces a rebuild.

    Only the session's recorded path is trusted: there is deliberately NO
    fallback to a global artifact file, which would leak another session's PRD
    into this one. Raises ValueError when the session has none.
    """
    if not prd_path:
        cached = state.standard_prd_path
        if cached and os.path.isfile(cached):
            return prd_from_json(cached), cached
        raise ValueError("No standardized PRD found for this session; call hercules_generate_standardized_prd first.")
    return await _build_standard_prd(state, prd_path)


def _execute_and_record(
    state: SessionState,
    feature_path: str,
    outcome: Any,
) -> dict:
    """Record an ExecutionOutcome on the session and build the tool result dict."""
    state.last_run_summaries.extend(outcome.summaries)
    # Bound the in-memory list too (persistence trims separately on serialize):
    # long-running sessions must not accumulate summaries without limit.
    if len(state.last_run_summaries) > MAX_RUN_SUMMARIES:
        del state.last_run_summaries[:-MAX_RUN_SUMMARIES]
    get_session_manager().save_session_state(state)
    described = describe_run(outcome.summaries)
    result: dict[str, Any] = {
        "success": outcome.success,
        "duration_s": round(outcome.duration_s, 3),
        "summaries": outcome.summaries,
        "run_report_md": described["report_md"],
        "bundles_count": len(described["bundles"]),
        "progress_events": outcome.progress_events[-20:],
        # Authoritative whole-run counter from the runner bridge (the event
        # buffer above is capped and would undercount long runs).
        "steps_completed": outcome.steps_completed,
        "feature_file": feature_path,
    }
    if outcome.error:
        result["error"] = outcome.error
    return result


# ── tool 1: hercules_configure_session ────────────────────────────────────────


@mcp.tool()
async def hercules_configure_session(
    app_url: str,
    project_path: str = "",
    test_type: str = "frontend",
    test_scope: str = "codebase",
    credentials: dict | None = None,
    prd_path: str = "",
) -> dict:
    """Register a Hercules testing session: the app under test plus project/PRD context.

    Call this FIRST. All other hercules_* tools accept a session_id and fall
    back to the most recent session when it is omitted. Sessions persist under
    .hercules/sessions.json (app_url userinfo is redacted there).

    Args:
        app_url: Base URL of the application under test, e.g. https://staging.example.com. Recorded and probed for reachability only; the URL a run actually visits comes from the Gherkin you execute.
        project_path: Path of the project source tree to scan. Must be inside the Hercules root (TESTZEUS_ROOT/cwd), PROJECT_SOURCE_ROOT/TEST_DATA_PATH or HERCULES_MCP_ALLOWED_ROOTS.
        test_type: Informational label: "frontend", "backend" or "mixed". Recorded on the session; it does not change what runs.
        test_scope: Only "codebase" is supported. "diff" is rejected (diff-scoped testing is not implemented).
        credentials: DEPRECATED and ignored: credentials are never stored or echoed. Put login steps/secrets in the Gherkin (or environment) instead.
        prd_path: Optional PRD file (inside the allowed roots) or http(s) URL (public hosts only) for hercules_generate_standardized_prd.

    Returns:
        {"session_id", "app_url" (userinfo-redacted), "app_url_reachable", "project_path",
        "test_type", "test_scope", "prd_path", "next_action", optional "warnings"}.
        app_url_reachable is true/false (HEAD/GET probe) or null when not checkable.
        In-memory session state keeps the full URL; it is never echoed.
    """
    if test_type not in ("frontend", "backend", "mixed"):
        return _error(f"Invalid test_type {test_type!r}; use 'frontend', 'backend' or 'mixed'.")
    if test_scope == "diff":
        return _error("test_scope='diff' is not supported yet; use 'codebase'.")
    if test_scope != "codebase":
        return _error(f"Invalid test_scope {test_scope!r}; only 'codebase' is supported.")

    warnings: list[str] = []
    if credentials:
        warnings.append("credentials are ignored (never stored); provide login steps in the Gherkin instead.")
    try:
        resolved_project = _confine_path(project_path, "project_path") if project_path else ""
        resolved_prd = prd_path
        if prd_path and not prd_path.startswith(("http://", "https://")):
            resolved_prd = _confine_path(prd_path, "prd_path", (resolved_project,) if resolved_project else ())
    except PathOutsideRootsError as exc:
        return _error(str(exc))

    reachable = await _check_app_url(app_url)
    state = get_session_manager().create_session(
        app_url=app_url,
        project_path=resolved_project,
        test_type=test_type,
        test_scope=test_scope,
        prd_path=resolved_prd,
    )
    state.app_url_reachable = reachable
    get_session_manager().save_session_state(state)

    next_action = "Call hercules_generate_code_summary to scan the project" if state.project_path else "Call hercules_generate_standardized_prd (pass prd_path) to normalize requirements"
    if reachable is False:
        # Redact userinfo here too: the warning may surface credentials otherwise.
        next_action += f" (warning: app_url was not reachable — verify {redact_url(app_url)!r} before executing tests)"
    result = {
        "session_id": state.session_id,
        # Echo the redacted URL; the raw URL stays on the in-memory session state.
        "app_url": redact_url(state.app_url),
        "app_url_reachable": reachable,
        "project_path": state.project_path,
        "test_type": state.test_type,
        "test_scope": state.test_scope,
        "prd_path": state.prd_path,
        "next_action": next_action,
    }
    if warnings:
        result["warnings"] = warnings
    return result


# ── tool 2: hercules_generate_code_summary ────────────────────────────────────


@mcp.tool()
async def hercules_generate_code_summary(session_id: str = "", project_path: str = "") -> dict:
    """Scan the project source tree and produce a heuristic codebase summary (no LLM required).

    Detects languages, frameworks, entry points and HTTP routes, then saves the
    full summary to the session's artifact dir (.hercules/<session_id>/code_summary.json)
    and records it on the session.

    Args:
        session_id: Session to attach the summary to (omit for the most recent session).
        project_path: Project source tree to scan (inside the allowed roots); falls back to the session's project_path.

    Returns:
        {"summary": {frameworks, languages (top 5), entry_points_count, routes_count},
        "total_files", "code_summary_path", optional "llm_notes", "next_action"}.
    """
    try:
        state = _resolve_session(session_id)
    except (KeyError, ValueError) as exc:
        return _error(str(exc))

    try:
        path = _confine_path(project_path, "project_path") if project_path else state.project_path
    except PathOutsideRootsError as exc:
        return _error(str(exc))
    if not path or not os.path.isdir(path):
        return _error(f"project_path {path!r} is not a directory. Pass project_path or set it via hercules_configure_session.")

    # The scan walks the whole source tree and the save does file I/O; keep both
    # off the event loop so the server stays responsive during big scans.
    try:
        summary = await asyncio.to_thread(generate_code_summary, path)
        out_path = os.path.join(str(_session_state_dir(state)), "code_summary.json")
        await asyncio.to_thread(save_summary, summary, out_path)
    except Exception as exc:  # scan/save failures surface as error dicts, never raw exceptions
        return _error(f"{type(exc).__name__}: {exc}")
    state.code_summary_path = out_path
    get_session_manager().save_session_state(state)

    languages = sorted(summary.get("languages", {}).items(), key=lambda item: item[1], reverse=True)[:5]
    result: dict[str, Any] = {
        "summary": {
            "frameworks": summary.get("frameworks", []),
            "languages": [language for language, _count in languages],
            "entry_points_count": len(summary.get("entry_points", [])),
            "routes_count": len(summary.get("routes", [])),
        },
        "total_files": summary.get("total_files", 0),
        "code_summary_path": out_path,
        "next_action": "Call hercules_generate_standardized_prd to normalize a PRD into requirements.",
    }
    if summary.get("llm_notes"):
        result["llm_notes"] = summary["llm_notes"]
    return result


# ── tool 3: hercules_generate_standardized_prd ────────────────────────────────


@mcp.tool()
async def hercules_generate_standardized_prd(session_id: str = "", prd_path: str = "") -> dict:
    """Normalize a PRD document (markdown/pdf/docx/URL) into structured requirements via LLM.

    Source resolution: prd_path argument > session prd_path > README.md in the
    session's project_path. The result is saved to the session's artifact dir
    (.hercules/<session_id>/standard_prd.json) and recorded on the session for
    hercules_generate_test_plan.

    Args:
        session_id: Session to attach the PRD to (omit for the most recent session).
        prd_path: Path or URL of the PRD document. Falls back to session context when empty.

    Returns:
        {"requirement_count", "project_name", "standard_prd_path",
        "requirements": [{id, title} (max 50)], "next_action",
        optional "warning" (present when some PRD chunks failed extraction; the
        requirement set may be incomplete)}.
    """
    try:
        state = _resolve_session(session_id)
        prd, out_path = await _build_standard_prd(state, prd_path)
    except Exception as exc:  # LLM (e.g. AgentsLLMConfigManager init) and IO failures surface as error dicts, never raw exceptions
        return _error(str(exc))

    failed_chunks = int((prd.source_metadata or {}).get("failed_chunks") or 0)
    result = {
        "requirement_count": len(prd.requirements),
        "project_name": prd.project_name,
        "standard_prd_path": out_path,
        "requirements": [{"id": requirement.id, "title": requirement.title} for requirement in prd.requirements[:50]],
        "next_action": "Call hercules_generate_test_plan to turn these requirements into test cases.",
    }
    if failed_chunks > 0:
        # Partial extraction: some PRD chunks failed to parse/extract — surface it so
        # callers can re-check coverage instead of trusting a silently shrunken set.
        result["warning"] = f"{failed_chunks} PRD chunk(s) failed extraction; the requirement set may be incomplete " f"(details in {out_path} source_metadata.failed_chunks)."
    return result


# ── tool 4: hercules_generate_test_plan ───────────────────────────────────────


@mcp.tool()
async def hercules_generate_test_plan(session_id: str = "", prd_path: str = "", cases_per_requirement: int = 3) -> dict:
    """Generate a structured test plan from the standardized PRD via LLM.

    Reuses the session's standardized PRD produced by
    hercules_generate_standardized_prd; pass prd_path to (re)build the PRD from
    that source first. Errors when the session has no standardized PRD —
    sessions never fall back to another session's artifact. The plan is saved
    to the session's artifact dir (.hercules/<session_id>/test_plan.json) and
    recorded on the session.

    Args:
        session_id: Session to attach the plan to (omit for the most recent session).
        prd_path: Pass to force rebuilding the PRD from this source; otherwise the session's cached PRD is used.
        cases_per_requirement: Target number of test cases per requirement (default 3).

    Returns:
        {"test_plan_path", "case_count", "coverage": {requirements, totals},
        "test_cases": [{id, title, category, priority, tags}], "next_action"}.
    """
    try:
        state = _resolve_session(session_id)
        prd, _prd_file = await _ensure_standard_prd(state, prd_path)
        plan = await generate_test_plan(prd, cases_per_requirement=cases_per_requirement)
        out_path = os.path.join(str(_session_state_dir(state)), "test_plan.json")
        plan_to_json(plan, out_path)
    except Exception as exc:  # LLM and write failures surface as error dicts, never raw exceptions
        return _error(str(exc))

    state.test_plan_path = out_path
    state.plan_approved = False  # a freshly generated (LLM/PRD-derived) plan must be re-approved
    get_session_manager().save_session_state(state)

    return {
        "test_plan_path": out_path,
        "case_count": len(plan.test_cases),
        "coverage": coverage_matrix(plan),
        "test_cases": [{"id": case.id, "title": case.title, "category": case.category, "priority": case.priority, "tags": case.tags} for case in plan.test_cases],
        "next_action": "Review the plan at test_plan_path, then call hercules_generate_and_execute with approve_plan=true (optionally with test_ids) to run selected cases.",
    }


# ── tool 5: hercules_generate_and_execute ─────────────────────────────────────


@mcp.tool()
async def hercules_generate_and_execute(
    session_id: str = "",
    test_ids: list[str] | None = None,
    gherkin: str = "",
    description: str = "",
    additional_instructions: str = "",
    approve_plan: bool = False,
) -> dict:
    """Generate a feature file and execute it IN-PROCESS via the Hercules runner.

    This tool EXECUTES what you pass (gherkin / description) with the configured
    browser and tools. A plan generated from a PRD is untrusted LLM output, so
    running it requires ``approve_plan=true`` (or HERCULES_MCP_AUTO_APPROVE_PLAN=true)
    after you have reviewed the plan file.

    Source priority: gherkin > description (LLM-generated Gherkin) > session
    test plan subset (requires hercules_generate_test_plan first; test_ids
    selects cases, all cases when omitted). Each run gets its own feature file
    under opt/input (hercules_mcp_run_<id>.feature); runs are serialized
    process-wide and bounded by HERCULES_MCP_EXEC_TIMEOUT (default 1800s).
    The description->Gherkin LLM generation is bounded separately by
    _GHERKIN_GENERATION_TIMEOUT_S (120s; a module constant rather than an env
    var because one LLM call needs a much shorter leash than a full run). A
    step-level progress stream is captured from the notification hub.

    Args:
        session_id: Session to record the run on (omit for the most recent session).
        test_ids: Restrict a plan-based run to these test case ids (e.g. ["TC001","TC003"]); ignored when gherkin or description is provided.
        gherkin: Raw Gherkin feature content to run directly (highest priority).
        description: Plain-English description; Gherkin is generated from it via LLM.
        additional_instructions: Extra hints for the agent, appended as Gherkin comments in the feature file.
        approve_plan: Confirm you reviewed the PRD-derived test plan and want it executed (only needed for plan-based runs; remembered for hercules_rerun until the plan is regenerated).

    Returns:
        {"success", "duration_s", "summaries", "run_report_md", "bundles_count",
        "progress_events" (last 20), "steps_completed", "feature_file", "source",
        "next_action", optional "error", optional "additional_instructions_note"}.
    """
    try:
        state = _resolve_session(session_id)
    except (KeyError, ValueError) as exc:
        return _error(str(exc))

    feature_path = _run_feature_path()
    feature_path.parent.mkdir(parents=True, exist_ok=True)

    # One run at a time (see _EXEC_LOCK); wait_for bounds the run so a hung
    # execution cannot pin the lock (and the server) forever.
    timeout_s = _exec_timeout()
    try:
        async with _exec_lock():
            if gherkin.strip():
                source_kind = "gherkin"
                feature_path.write_text(gherkin, encoding="utf-8")
            elif description.strip():
                source_kind = "description"
                # Bounded separately from the run deadline so a hung LLM call
                # returns an error (and releases _EXEC_LOCK) after 120s instead
                # of holding it for the full HERCULES_MCP_EXEC_TIMEOUT.
                try:
                    content = await asyncio.wait_for(generate_gherkin_from_description(description), timeout=_GHERKIN_GENERATION_TIMEOUT_S)
                except asyncio.TimeoutError:
                    return _error(f"Gherkin generation timed out after {_GHERKIN_GENERATION_TIMEOUT_S:g}s; the run was cancelled.")
                feature_path.write_text(content, encoding="utf-8")
            elif state.test_plan_path and os.path.isfile(state.test_plan_path):
                source_kind = "test_plan"
                if not (approve_plan or state.plan_approved or _env_flag("HERCULES_MCP_AUTO_APPROVE_PLAN")):
                    return _error(
                        "The test plan was generated by an LLM from a PRD and has not been approved. Review it at "
                        f"{_display_path(state.test_plan_path)} and call again with approve_plan=true (or pass gherkin explicitly)."
                    )
                if approve_plan and not state.plan_approved:
                    state.plan_approved = True
                    get_session_manager().save_session_state(state)
                plan = plan_from_json(state.test_plan_path)
                if test_ids:
                    valid_ids = {case.id for case in plan.test_cases}
                    unknown = [str(test_id) for test_id in test_ids if str(test_id) not in valid_ids]
                    if unknown:
                        return _error(f"Unknown test_ids {unknown}; the session plan contains: {sorted(valid_ids)}.")
                write_plan_scenarios_feature(plan, [str(test_id) for test_id in test_ids] if test_ids else None, str(feature_path))
            else:
                return _error("Nothing to run: provide gherkin or description, or generate a test plan first " "via hercules_generate_test_plan.")

            if additional_instructions.strip():
                comment = "\n".join(f"# {line.strip()}" for line in additional_instructions.strip().splitlines() if line.strip())
                existing = feature_path.read_text(encoding="utf-8")
                feature_path.write_text(comment + "\n\n" + existing, encoding="utf-8")

            outcome = await asyncio.wait_for(execute_feature_file(str(feature_path)), timeout=timeout_s)
    except asyncio.TimeoutError:
        return _error(
            f"Execution timed out after {timeout_s:g}s (HERCULES_MCP_EXEC_TIMEOUT); the run was cancelled.",
            feature_file=str(feature_path),
            timeout_s=timeout_s,
        )
    except SystemExit as exc:
        # BaseException from the runner bridge (exit codes != 1) — kept out of
        # the tool result contract; `except Exception` would not catch it.
        return _error(f"SystemExit: {exc.code}")
    except Exception as exc:  # LLM/IO failures surface as error dicts, never raw exceptions
        return _error(str(exc))

    result = _execute_and_record(state, str(feature_path), outcome)
    result["source"] = source_kind
    if additional_instructions.strip():
        result["additional_instructions_note"] = "Appended as Gherkin comment(s) at the top of the feature file."
    if "error" not in result:
        result["next_action"] = "Call hercules_get_run_results for per-scenario details, or hercules_rerun to re-run."
    return result


# ── tool 6: hercules_get_run_results ──────────────────────────────────────────


@mcp.tool()
async def hercules_get_run_results(session_id: str = "", run_id: str = "") -> dict:
    """Fetch results of the newest (or a specific) Hercules run.

    Parses the run's merged ``*_result.xml`` JUnit file and returns per-scenario
    statuses plus the markdown report and failure-bundle directories.

    Args:
        session_id: Kept for symmetry; results are read from opt/output.
        run_id: Specific run folder name (e.g. run_20260609_132411); omit for the newest run.

    Returns:
        {"run_id", "junit_xml", "total", "passed", "failed", "errors", "report_md",
        "html", "bundles", "test_cases": [{name, status, time, failure_message?}]}.
    """
    output_dir = _result_dir()
    if run_id:
        invalid = _run_id_error(run_id)
        if invalid:
            return _error(invalid)
        run_dir = output_dir / run_id
        if not run_dir.is_dir():
            return _error(f"Run '{run_id}' not found in {_display_path(output_dir)}.")
    else:
        runs = _sorted_newest_first(_list_subdirs(output_dir))
        if not runs:
            return _error("No results found. Run a test first via hercules_generate_and_execute.")
        run_dir = runs[0]

    try:
        xml_files = _sorted_newest_first(list(run_dir.glob("*_result.xml")))
    except OSError:
        xml_files = []  # run vanished mid-enumeration
    cases: list[dict] = []
    if xml_files:
        try:
            cases = parse_junit_testcases(str(xml_files[0]))
        except Exception as exc:
            return _error(f"Could not parse JUnit XML {_display_path(xml_files[0])}: {exc}")

    statuses = [case["status"] for case in cases]
    report_md = run_dir / "Hercules_Test_Report.md"
    bundles_dir = run_dir / "bundles"
    try:
        html_files = sorted(run_dir.glob("*.html"))
    except OSError:
        html_files = []  # run vanished mid-enumeration

    return {
        "run_id": run_dir.name,
        "junit_xml": str(xml_files[0]) if xml_files else None,
        "total": len(cases),
        "passed": statuses.count("passed"),
        "failed": statuses.count("failed"),
        "errors": statuses.count("error"),
        "report_md": str(report_md) if report_md.exists() else None,
        "html": str(html_files[0]) if html_files else None,
        "bundles": sorted(str(path) for path in _list_subdirs(bundles_dir)),
        "test_cases": [
            {
                "name": case["name"],
                "status": case["status"],
                "time": case["time"],
                **({"failure_message": case["failure_message"]} if case.get("failure_message") else {}),
            }
            for case in cases
        ],
    }


# ── tool 7: hercules_open_dashboard ───────────────────────────────────────────


@mcp.tool()
async def hercules_open_dashboard(session_id: str = "") -> dict:
    """Locate the latest Hercules test report (the web dashboard arrives in a later phase).

    Returns a file:// URL of the newest Hercules_Test_Report.md (or the run's
    HTML report) plus a hint for serving the web dashboard.

    Args:
        session_id: Kept for symmetry; the report is located under opt/output.

    Returns:
        {"dashboard": "not_running", "report_url": file://... | None, "hint"}.
    """
    report_url: str | None = None
    output_dir = _result_dir()
    for run_dir in _sorted_newest_first(_list_subdirs(output_dir)):
        report_md = run_dir / "Hercules_Test_Report.md"
        try:
            if report_md.exists():
                report_url = report_md.as_uri()
                break
            html_files = sorted(run_dir.glob("*.html"))
        except OSError:
            continue  # run vanished mid-enumeration
        if html_files:
            report_url = html_files[0].as_uri()
            break
    return {
        "dashboard": "not_running",
        "report_url": report_url,
        "hint": "start `hercules-dashboard` to serve the web dashboard",
    }


# ── tool 8: hercules_check_config ─────────────────────────────────────────────


@mcp.tool()
async def hercules_check_config(session_id: str = "", ping_model: bool = False) -> dict:
    """Diagnose the Hercules setup: LLM config file, model construction, Playwright install.

    Args:
        session_id: Kept for symmetry; configuration is process-wide.
        ping_model: When true, send a tiny "Reply with OK" prompt to the planner model and report latency.

    Returns:
        {"ok": bool, "checks": {"llm_config": {...}, "litellm_model": {...}, "playwright": {...},
        optional "model_ping": {ok, latency_s, error?}}}.
    """
    checks: dict[str, Any] = {}

    # Anchor the default at the Hercules root (TESTZEUS_ROOT or cwd) like every
    # other path in this module; AGENTS_LLM_CONFIG_FILE keeps override semantics.
    config_file = os.getenv("AGENTS_LLM_CONFIG_FILE", str(Path(_hercules_root()) / "agents_llm_config.json"))
    checks["llm_config"] = {"path": os.path.abspath(config_file), "exists": os.path.isfile(config_file)}

    model = None
    model_error: str | None = None
    try:
        model = get_litellm_chat_model("planner_agent")
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"
    checks["litellm_model"] = {"ok": model is not None, **({"error": model_error} if model_error else {})}

    playwright_error: str | None = None
    try:
        import playwright.async_api  # noqa: F401

        playwright_ok = True
    except Exception as exc:
        playwright_ok = False
        playwright_error = f"{type(exc).__name__}: {exc}"
    checks["playwright"] = {"ok": playwright_ok, **({"error": playwright_error} if playwright_error else {})}

    if ping_model:
        ping: dict[str, Any] = {"ok": False, "latency_s": None}
        if model is None:
            ping["error"] = "model could not be constructed; see litellm_model check"
        else:
            start = time.monotonic()
            try:
                await asyncio.wait_for(model.ainvoke("Reply with OK"), timeout=_MODEL_PING_TIMEOUT_S)
                ping = {"ok": True, "latency_s": round(time.monotonic() - start, 2)}
            except asyncio.TimeoutError:
                ping = {"ok": False, "latency_s": round(time.monotonic() - start, 2), "error": f"model ping timed out after {_MODEL_PING_TIMEOUT_S:g}s"}
            except Exception as exc:
                ping = {"ok": False, "latency_s": round(time.monotonic() - start, 2), "error": f"{type(exc).__name__}: {exc}"}
        checks["model_ping"] = ping

    ok = checks["llm_config"]["exists"] and checks["litellm_model"]["ok"] and checks["playwright"]["ok"]
    return {"ok": ok, "checks": checks}


# ── tool 9: hercules_rerun ────────────────────────────────────────────────────


@mcp.tool()
async def hercules_rerun(session_id: str = "", test_ids: list[str] | None = None, approve_plan: bool = False) -> dict:
    """Re-run a previous test: the session test-plan subset (test_ids) or the last executed feature file.

    Args:
        session_id: Session whose plan/last run should be re-executed (omit for the most recent session).
        test_ids: Restrict the re-run to these test case ids; ignored when no test plan exists.
        approve_plan: Approve the session's PRD-derived test plan for execution (see hercules_generate_and_execute).

    Returns:
        Same shape as hercules_generate_and_execute.
    """
    try:
        state = _resolve_session(session_id)
    except (KeyError, ValueError) as exc:
        return _error(str(exc))

    if state.test_plan_path and os.path.isfile(state.test_plan_path):
        return await hercules_generate_and_execute(session_id=state.session_id, test_ids=test_ids, approve_plan=approve_plan)

    last_feature: str | None = None
    saw_recorded = False
    for summary in reversed(state.last_run_summaries):
        candidate = summary.get("feature_file")
        if not candidate:
            continue
        saw_recorded = True
        # Recorded paths are untrusted state: only this root's opt/input/*.feature
        # may be re-executed (covers hercules_mcp_run_<id>.feature and the legacy
        # opt/input/hercules_mcp_run.feature).
        if _recorded_feature_error(candidate) or not os.path.isfile(candidate):
            continue
        last_feature = candidate
        break
    if not last_feature:
        if saw_recorded:
            return _error("recorded feature file is no longer available; run a test first")
        return _error("Nothing to re-run: the session has no test plan and no previous feature file.")

    timeout_s = _exec_timeout()
    try:
        async with _exec_lock():
            outcome = await asyncio.wait_for(execute_feature_file(last_feature), timeout=timeout_s)
    except asyncio.TimeoutError:
        return _error(
            f"Execution timed out after {timeout_s:g}s (HERCULES_MCP_EXEC_TIMEOUT); the run was cancelled.",
            feature_file=last_feature,
            timeout_s=timeout_s,
        )
    except SystemExit as exc:
        # BaseException from the runner bridge (exit codes != 1) must not escape.
        return _error(f"SystemExit: {exc.code}")
    except Exception as exc:  # runner failures surface as error dicts, never raw exceptions
        return _error(str(exc))
    result = _execute_and_record(state, last_feature, outcome)
    result["source"] = "last_run"
    return result


# ── legacy tools (deprecated aliases) ─────────────────────────────────────────


@mcp.tool()
async def generate_gherkin(description: str) -> str:
    """DEPRECATED: use hercules_generate_and_execute(description=...) to generate AND run a test.

    Kept for backwards compatibility: converts a plain-English test description
    into Gherkin text via LLM without executing it.
    """
    return await generate_gherkin_from_description(description)


@mcp.tool()
async def run_test(gherkin: str = "", description: str = "", additional_instructions: str = "") -> str:
    """DEPRECATED: use hercules_configure_session + hercules_generate_and_execute(gherkin=..., description=...).

    Thin wrapper: creates an ephemeral session when none exists (only when
    there is something to run, and it never becomes the "most recent" session),
    generates Gherkin from ``description`` when needed, executes it in-process
    and returns a compact text summary.

    Args:
        gherkin: Raw Gherkin feature content to run directly (highest priority).
        description: Plain-English description; Gherkin is generated from it via LLM.
        additional_instructions: Extra hints for the agent, appended as Gherkin comments in the feature file.
    """
    manager = get_session_manager()
    try:
        session_id = manager.resolve_session().session_id
    except (KeyError, ValueError):
        if not gherkin.strip() and not description.strip():
            return "Error: No session. Call hercules_configure_session first."
        # Synthetic session for this one-off run; touch_recent=False keeps it
        # from hijacking session_id="" resolution for later calls.
        session_id = manager.create_session(app_url="", touch_recent=False).session_id
    result = await hercules_generate_and_execute(session_id=session_id, gherkin=gherkin, description=description, additional_instructions=additional_instructions)
    if result.get("error"):
        return f"Error: {result['error']}"
    lines = [
        f"success={result['success']}",
        f"duration_s={result['duration_s']}",
        f"steps_completed={result['steps_completed']}",
        f"report_md={result['run_report_md']}",
    ]
    for summary in result["summaries"]:
        lines.append(f"passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} errors={summary.get('errors', 0)}")
    return "\n".join(lines)


@mcp.tool()
async def get_test_results(run_id: str = "") -> str:
    """DEPRECATED: use hercules_get_run_results(run_id=...) for the richer dict result.

    Thin wrapper returning the latest (or given) run's results as JSON text;
    lookup failures return "Error: <message>" text, consistent with run_test.
    """
    result = await hercules_get_run_results(run_id=run_id)
    if result.get("error"):
        return f"Error: {result['error']}"
    return json.dumps(result, indent=2, default=str)


# ── entrypoint ────────────────────────────────────────────────────────────────


def _mcp_auth_token() -> str | None:
    return normalize_token(os.getenv("MCP_AUTH_TOKEN"))


def _transport_security(allowlist: set[str] | None) -> TransportSecuritySettings:
    """FastMCP DNS-rebinding settings for a Host allowlist (None = wildcard bind, token-gated)."""
    if allowlist is None:
        # Wildcard bind (the bearer token is mandatory there): any Host name may reach this machine.
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts: list[str] = []
    origins: list[str] = []
    for name in sorted(allowlist):
        display = f"[{name}]" if ":" in name else name
        hosts += [display, f"{display}:*"]
        origins += [f"http://{display}", f"http://{display}:*", f"https://{display}", f"https://{display}:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


def build_http_app(host: str | None = None, port: int | None = None, path: str | None = None, token: str | None = None):
    """Build the hardened streamable-http ASGI app for this server.

    Applies host/port/path to the FastMCP settings (they are NOT read from the
    environment by FastMCP itself), enforces the bind policy (non-loopback
    requires ``MCP_AUTH_TOKEN``), configures DNS-rebinding protection and wraps
    the app in bearer-token auth when a token is configured.

    Raises:
        InsecureBindError: non-loopback ``host`` without a sufficiently long token.
    """
    host = host if host is not None else _mcp_host()
    port = port if port is not None else _mcp_port()
    path = path if path is not None else _mcp_path()
    token = token if token is not None else _mcp_auth_token()
    enforce_bind_policy("the Hercules MCP server", host, token, "MCP_AUTH_TOKEN")

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path

    mcp.settings.transport_security = _transport_security(build_host_allowlist(host, parse_csv(os.getenv("MCP_ALLOWED_HOSTS"))))
    # The session manager is created lazily by streamable_http_app(); drop any one
    # built with earlier settings so the new transport security applies.
    mcp._session_manager = None  # noqa: SLF001

    app = mcp.streamable_http_app()
    if token:
        app.add_middleware(BearerTokenMiddleware, token=token, realm="hercules-mcp")
    return app


def main() -> None:
    """Entrypoint for the testzeus-hercules-mcp console script."""
    import uvicorn

    host = _mcp_host()
    port = _mcp_port()
    path = _mcp_path()
    token = _mcp_auth_token()
    try:
        app = build_http_app(host, port, path, token)
    except InsecureBindError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Starting TestZeus Hercules MCP server on http://{host}:{port}{path}")
    if token:
        print("Bearer-token auth enabled (MCP_AUTH_TOKEN): send 'Authorization: Bearer <token>'.")
    else:
        print("No MCP_AUTH_TOKEN set: auth disabled (loopback bind only).")
    print("Add to your mcp_servers config:")
    client_entry: dict[str, Any] = {"transport": "streamable-http", "url": f"http://{host}:{port}{path}"}
    if token:
        client_entry["headers"] = {"Authorization": "Bearer <MCP_AUTH_TOKEN>"}
    print(json.dumps({"mcpServers": {"testzeus-hercules": client_entry}}, indent=4))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
