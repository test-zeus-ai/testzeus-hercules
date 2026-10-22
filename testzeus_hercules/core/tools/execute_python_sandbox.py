"""
Python Sandbox Execution Tool

Execute agent-authored Python scripts. Two execution modes (``SANDBOX_MODE``):

``inprocess`` (default, historical behaviour)
    The script is exec'd inside the agent process with the LIVE Playwright objects
    (``page``, ``browser``, ``context``, ``playwright_manager``) injected, stdout
    captured and the whole run bounded by ``asyncio.wait_for``. Synchronous
    infinite loops cannot be interrupted in this mode.

``subprocess`` (opt-in isolation)
    Each execution launches a fresh child process (`sys.executable -c` bootstrap)
    that runs the script with the module's standard injections and is destroyed
    afterwards, under a hard wall-clock timeout (capped at ``_MAX_TIMEOUT_SECONDS``)
    plus POSIX rlimits (CPU time and address space). On timeout the whole process
    group is SIGKILLed. Live Playwright objects cannot cross the process boundary:
    the child gets placeholders unless CDP_ENDPOINT_URL is set, in which case it
    re-attaches to the browser over CDP.

Neither mode is a security boundary: the executed code runs with a full Python
runtime (os, sys, __import__, network, filesystem all reachable). Do not run
untrusted code through this tool.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Tuple

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

# Hard upper bound for agent-supplied timeout_seconds (default equals the cap).
_MAX_TIMEOUT_SECONDS = 300.0
# Address-space cap applied inside the child via RLIMIT_AS (POSIX, best effort).
_SANDBOX_MEMORY_LIMIT_BYTES = 1024**3
# RLIMIT_CPU is set slightly above the wall-clock timeout so an infinite CPU
# loop is killed by the kernel even if the parent's timeout bookkeeping hiccups.
_CPU_LIMIT_GRACE_SECONDS = 2
# Extra wall-clock slack the parent allows beyond the effective timeout before
# it stops waiting on the (already killed) child.
_HARD_KILL_GRACE_SECONDS = 15.0
# Per-stream cap on captured child stdout/stderr.
_MAX_OUTPUT_CHARS = 256 * 1024
_OUTPUT_TRUNCATION_MARKER = f"\n...[sandbox output truncated at {_MAX_OUTPUT_CHARS} characters]..."


class ExecutionTimeout(Exception):
    """Exception raised when execution times out."""


@tool(
    agent_names=["executor_nav_agent"],
    description="""Execute Python code from a file with access to common injected modules.
    By default the script runs in-process with the live browser objects (page, browser, context, playwright_manager) plus asyncio, logger and configured module injections.
    When the operator sets SANDBOX_MODE=subprocess the script instead runs in a disposable child process with CPU/memory limits and a hard wall-clock timeout (capped at 300s); there the live browser objects are only available if CDP_ENDPOINT_URL is configured.
    This is NOT a security boundary: executed code has full Python capabilities (imports, network, filesystem). Only run trusted scripts.
    The code file can contain both synchronous and asynchronous functions.
    Supports multi-tenant injections configured via environment variables.""",
    name="execute_python_sandbox",
)
async def execute_python_sandbox(
    file_path: Annotated[str, "Path to Python file to execute"],
    timeout_seconds: Annotated[float, "Maximum execution time in seconds (hard capped at 300s)"] = 300.0,
    function_name: Annotated[str, "Optional: Specific function to call (defaults to main or full script execution)"] = "",
    function_args: Annotated[str, 'Optional: JSON string of arguments to pass to function (e.g., \'{"arg1": "value", "arg2": 123}\')'] = "{}",
) -> str:
    """
    Execute Python code from a file, in-process (default) or in a child process (SANDBOX_MODE=subprocess).

    Args:
        file_path: Path to a .py file (relative to the workspace root, the test-data directory or the cwd,
            or absolute); it must resolve inside the workspace root (PROJECT_SOURCE_ROOT) or the
            test-data directory (TEST_DATA_PATH), otherwise it is rejected
        timeout_seconds: Maximum execution time (default 300s; hard capped at 300s in subprocess mode)
        function_name: Optional specific function to call
        function_args: Optional JSON string of function arguments

    Returns:
        JSON string with execution results

    Note:
        Tenant ID and custom injections are automatically determined from configuration:
        - SANDBOX_TENANT_ID: Determines tenant-specific module access
        - SANDBOX_CUSTOM_INJECTIONS: JSON string with custom modules/objects to inject

        SANDBOX_MODE=inprocess (default) runs the code in the agent process with live
        browser objects: the timeout is cooperative (asyncio.wait_for), with no process
        kill and no resource limits. SANDBOX_MODE=subprocess runs it in a disposable child
        process with resource limits and a hard, killing timeout. Neither is a security
        boundary against hostile code — do not run untrusted code.
    """
    logger.info("Executing Python sandbox: file=%s, timeout=%ss", file_path, timeout_seconds)
    add_event(EventType.INTERACTION, EventData(detail="execute_python_sandbox"))

    # Get sandbox configuration
    config = get_global_conf()
    tenant_id = config.get_sandbox_tenant_id()
    custom_injections = config.get_sandbox_custom_injections()

    logger.info(f"Using sandbox tenant: {tenant_id if tenant_id else 'default (no tenant)'}")
    if custom_injections and custom_injections != "{}":
        logger.info("Custom injections configured from environment")

    # Initialize PlaywrightManager and get the active browser page.
    # Imported lazily: it is heavy and must not load inside the sandbox child.
    from testzeus_hercules.core.playwright_manager import PlaywrightManager

    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()

    if not page:
        error_msg = "No active browser page available for sandbox execution"
        logger.error(error_msg)
        return _format_error_result(error_msg)

    # Get browser context
    try:
        context = await browser_manager.get_browser_context()
        browser = context.browser
    except Exception as e:
        logger.warning(f"Could not get browser/context: {e}. They will be None in sandbox.")
        context = None
        browser = None

    # Resolve file path (constrained to the workspace root)
    try:
        resolved_path = _resolve_file_path(file_path)
        logger.info(f"Resolved file path: {resolved_path}")
    except (FileNotFoundError, PermissionError) as e:
        error_msg = f"Cannot access script: {file_path} - {str(e)}"
        logger.error(error_msg)
        return _format_error_result(error_msg)

    # Take screenshot before execution
    try:
        # Get proofs directory from config
        proofs_dir = os.path.join(config.get_project_source_root(), "opt", "proofs")
        os.makedirs(proofs_dir, exist_ok=True)

        screenshot_before = os.path.join(proofs_dir, f"sandbox_before_{int(time.time())}.png")
        await page.screenshot(path=screenshot_before)
        logger.info(f"Screenshot before execution: {screenshot_before}")
    except Exception as e:
        logger.warning(f"Could not take screenshot before execution: {e}")
        screenshot_before = None

    # Execute the sandbox
    start_time = time.time()
    try:
        result = await _execute_in_sandbox(
            page=page,
            browser=browser,
            context=context,
            browser_manager=browser_manager,
            file_path=resolved_path,
            timeout_seconds=timeout_seconds,
            function_name=function_name,
            function_args=function_args,
            tenant_id=tenant_id,
            custom_injections=custom_injections,
        )

        # Take screenshot after execution
        try:
            # Get proofs directory from config
            proofs_dir = os.path.join(config.get_project_source_root(), "opt", "proofs")
            os.makedirs(proofs_dir, exist_ok=True)

            screenshot_after = os.path.join(proofs_dir, f"sandbox_after_{int(time.time())}.png")
            await page.screenshot(path=screenshot_after)
            logger.info(f"Screenshot after execution: {screenshot_after}")
            result["screenshots"] = {"before": screenshot_before, "after": screenshot_after}
        except Exception as e:
            logger.warning(f"Could not take screenshot after execution: {e}")

        execution_time = time.time() - start_time
        result["execution_time"] = round(execution_time, 3)

        logger.info(f"Sandbox execution completed in {execution_time:.2f}s")
        return json.dumps(result, indent=2)

    except ExecutionTimeout as e:
        execution_time = time.time() - start_time
        error_msg = str(e)
        logger.error(error_msg)
        return _format_error_result(error_msg, execution_time=execution_time)

    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"Sandbox execution failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return _format_error_result(error_msg, execution_time=execution_time)


def _format_error_result(error_msg: str, execution_time: float = 0.0) -> str:
    """Format error result as JSON string."""
    return json.dumps({"success": False, "error": error_msg, "execution_time": round(execution_time, 3)}, indent=2)


def _get_base_injections() -> Dict[str, Any]:
    """
    Get base injections available to all sandbox executions.
    These are always safe and universally useful.
    """
    import datetime
    import pathlib
    import re
    import uuid

    return {
        # Standard library (always safe)
        "re": re,
        "datetime": datetime,
        "pathlib": pathlib,
        "uuid": uuid,
    }


def _get_tenant_specific_injections(tenant_id: str, config: Any) -> Dict[str, Any]:
    """
    Get tenant-specific injections based on tenant_id.
    Allows multi-tenant sandbox with different capabilities per tenant.

    Args:
        tenant_id: Identifier for the tenant/caller
        config: Global configuration

    Returns:
        Dictionary of tenant-specific modules/objects to inject
    """
    injections = {}

    # Define tenant-specific injection policies
    tenant_policies = {
        "executor_agent": {
            # Full access to automation tools
            "requests": True,
            "pandas": True,
            "numpy": True,
            "bs4": True,
        },
        "data_agent": {
            # Data processing only
            "pandas": True,
            "numpy": True,
            "requests": False,
        },
        "api_agent": {
            # API interactions only
            "requests": True,
            "httpx": True,
        },
        "restricted_agent": {
            # Minimal access
        },
    }

    # Get policy for this tenant (default to restricted)
    policy = tenant_policies.get(tenant_id, {})

    # Load allowed modules based on policy
    for module_name, allowed in policy.items():
        if allowed:
            try:
                if module_name == "bs4":
                    # Special handling for beautifulsoup4
                    injections["BeautifulSoup"] = __import__("bs4", fromlist=["BeautifulSoup"]).BeautifulSoup
                else:
                    injections[module_name] = __import__(module_name)
                logger.debug(f"Injected {module_name} for tenant {tenant_id}")
            except ImportError:
                logger.warning(f"Module {module_name} not available for tenant {tenant_id}")

    # Add tenant-specific utilities from project
    try:
        if tenant_id == "executor_agent":
            from testzeus_hercules import utils

            injections["hercules_utils"] = utils
    except ImportError:
        pass

    return injections


def _get_config_driven_injections(config: Any) -> Dict[str, Any]:
    """
    Get injections defined in configuration.
    Allows dynamic configuration of available modules.

    Args:
        config: Global configuration

    Returns:
        Dictionary of configured modules to inject
    """
    injections = {}

    # Read from config: SANDBOX_PACKAGES="requests,pandas,numpy"
    sandbox_packages = config.get_config().get("SANDBOX_PACKAGES", "").split(",")

    for package_name in sandbox_packages:
        package_name = package_name.strip()
        if package_name:
            try:
                injections[package_name] = __import__(package_name)
                logger.info(f"Config-injected package: {package_name}")
            except ImportError:
                logger.warning(f"Could not import configured package: {package_name}")

    return injections


def _parse_custom_injections(custom_injections_json: str) -> Dict[str, Any]:
    """
    Parse and load custom injections from JSON string.
    Allows per-call injection customization.

    Args:
        custom_injections_json: JSON string with custom injection specifications

    Returns:
        Dictionary of custom modules/objects to inject

    Example JSON:
        {
            "modules": ["requests", "pandas"],
            "custom_objects": {
                "my_constant": 42,
                "my_config": {"key": "value"}
            }
        }
    """
    import json

    injections: Dict[str, Any] = {}

    if not custom_injections_json or custom_injections_json == "{}":
        return injections

    try:
        custom_spec = json.loads(custom_injections_json)

        # Handle module list
        modules = custom_spec.get("modules", [])
        for module_name in modules:
            try:
                injections[module_name] = __import__(module_name)
                logger.debug(f"Custom-injected module: {module_name}")
            except ImportError:
                logger.warning(f"Could not import custom module: {module_name}")

        # Handle custom objects (constants, configs, etc.)
        custom_objects = custom_spec.get("custom_objects", {})
        injections.update(custom_objects)

        # Handle custom Python code (advanced - be careful!)
        if "python_code" in custom_spec:
            # Execute custom setup code
            setup_code = custom_spec["python_code"]
            setup_globals: Dict[str, Any] = {}
            exec(setup_code, setup_globals)  # noqa: S102
            # Extract non-builtin items
            for key, value in setup_globals.items():
                if not key.startswith("__"):
                    injections[key] = value

    except json.JSONDecodeError as e:
        logger.error(f"Invalid custom_injections JSON: {e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error processing custom injections: {e}")

    return injections


def _build_sandbox_injections(
    config: Any,
    tenant_id: str = "",
    custom_injections_json: str = "{}",
) -> Dict[str, Any]:
    """
    Build complete set of sandbox injections from multiple sources.
    Merges injections in priority order: base -> config -> tenant -> custom

    Args:
        config: Global configuration
        tenant_id: Tenant/caller identifier
        custom_injections_json: JSON string with custom injections

    Returns:
        Complete dictionary of all injections
    """
    # Start with base (always available)
    injections = _get_base_injections()

    # Add config-driven injections
    injections.update(_get_config_driven_injections(config))

    # Add tenant-specific injections
    if tenant_id:
        injections.update(_get_tenant_specific_injections(tenant_id, config))

    # Add custom per-call injections (highest priority)
    injections.update(_parse_custom_injections(custom_injections_json))

    logger.info(f"Built sandbox with {len(injections)} injected modules/objects")
    logger.debug(f"Injected keys: {list(injections.keys())}")

    return injections


_ALLOWED_SCRIPT_SUFFIX = ".py"


def _allowed_script_roots() -> List[Path]:
    """Roots a sandbox script may live under: the workspace (PROJECT_SOURCE_ROOT) and the test-data directory."""
    config = get_global_conf()
    roots = [Path(config.get_project_source_root()).resolve()]
    try:
        test_data = Path(config.get_test_data_path()).resolve()
        if test_data not in roots:
            roots.append(test_data)
    except Exception:  # test-data path is optional here; the workspace root still applies
        pass
    return roots


def _resolve_file_path(file_path: str) -> str:
    """
    Resolve the file path to an absolute path, confined to the workspace/test-data roots.

    Absolute paths INSIDE an allowed root remain allowed; anything resolving
    outside them is rejected, including via symlinks (paths are resolved
    before the containment check).

    Args:
        file_path: Original file path (absolute or relative)

    Returns:
        Absolute path to the file

    Raises:
        FileNotFoundError: If file doesn't exist
        PermissionError: If the resolved path is outside the allowed roots or is not a ``.py`` file
    """
    roots = _allowed_script_roots()
    workspace_root = roots[0]

    def _confine(candidate: Path) -> Path:
        resolved = candidate.resolve()
        if not any(resolved == root or root in resolved.parents for root in roots):
            raise PermissionError(f"access denied: '{file_path}' resolves to '{resolved}', which is outside the workspace root ({workspace_root})")
        # Only real Python sources may run: other tools (e.g. persist_findings) can write
        # .txt/.log/.json/.yaml files into the workspace, which must not become executable.
        if resolved.suffix.lower() != _ALLOWED_SCRIPT_SUFFIX:
            raise PermissionError(f"access denied: only '{_ALLOWED_SCRIPT_SUFFIX}' scripts can be executed (got '{resolved.name}')")
        return resolved

    path = Path(file_path)

    # If already absolute and exists
    if path.is_absolute():
        if path.exists():
            return str(_confine(path))
        raise FileNotFoundError(f"File not found: {file_path}")

    # Try relative to workspace root, then the other allowed roots
    for root in roots:
        full_path = root / path
        if full_path.exists():
            return str(_confine(full_path))

    # Try relative to current working directory
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return str(_confine(cwd_path))

    raise FileNotFoundError(f"File not found in project root or cwd: {file_path}")


def _truncate_output(data: bytes, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """
    Decode captured child output and cap it at ``limit`` characters.

    Oversized output is truncated with an explicit marker so a runaway
    ``print`` loop cannot blow up the agent's context or the result JSON.
    """
    text = data.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit] + _OUTPUT_TRUNCATION_MARKER


def _apply_child_resource_limits(cpu_seconds: int, memory_bytes: int) -> None:
    """
    Apply POSIX resource limits inside the sandbox child process.

    Best-effort by design: individual failures (e.g. unsupported rlimit,
    restricted container) are swallowed so the child can still start; the
    parent-side hard timeout remains the backstop in that case.
    """
    try:
        import resource
    except ImportError:
        # Not POSIX (e.g. Windows) — parent-side hard timeout still applies.
        return

    for rlimit, value in ((resource.RLIMIT_CPU, int(cpu_seconds)), (resource.RLIMIT_AS, int(memory_bytes))):
        try:
            resource.setrlimit(rlimit, (value, value))
        except (OSError, ValueError):
            logger.warning(f"Could not apply rlimit {rlimit}={value} in sandbox child")


class _UnavailableSandboxObject:
    """Placeholder for live objects that cannot cross into the child process."""

    def __init__(self, name: str, reason: str) -> None:
        self._name = name
        self._reason = reason

    def __getattr__(self, item: str) -> Any:
        raise RuntimeError(f"'{self._name}' is not available in the sandbox child process: {self._reason}")


def _prepare_sandbox_globals(file_path: str, spec: Dict[str, Any], live_objects: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build the sandbox globals the script is executed against.

    ``live_objects`` (in-process mode) maps ``page``/``browser``/``context``/
    ``playwright_manager`` to the real Playwright objects. Without it (subprocess
    mode) those names are placeholders because live objects cannot cross the
    process boundary; ``_attach_browser_bridge`` may replace them via CDP.
    """
    config = get_global_conf()
    unavailable_reason = "live Playwright objects cannot be shared with the sandbox child process; " "set CDP_ENDPOINT_URL to re-attach the browser from inside sandbox scripts"
    browser_objects = live_objects or {name: _UnavailableSandboxObject(name, unavailable_reason) for name in ("page", "browser", "context", "playwright_manager")}

    sandbox_globals: Dict[str, Any] = {
        # Core components (always available)
        "__builtins__": __builtins__,
        "__name__": "__sandbox__",
        "__file__": file_path,
        # Playwright components (live in-process; placeholders / CDP re-attach in the child)
        "page": browser_objects["page"],
        "browser": browser_objects["browser"],
        "context": browser_objects["context"],
        "playwright_manager": browser_objects["playwright_manager"],
        # Async support
        "asyncio": asyncio,
        # Logging
        "logger": logger,
        # Configuration
        "config": config,
        # Basic utilities (always available)
        "os": os,
        "sys": sys,
        "json": json,
        "time": time,
        # Result container
        "_sandbox_result": None,
    }

    # Merge multi-tenant injections
    additional_injections = _build_sandbox_injections(
        config=config,
        tenant_id=spec.get("tenant_id", ""),
        custom_injections_json=spec.get("custom_injections", "{}"),
    )
    sandbox_globals.update(additional_injections)

    return sandbox_globals


async def _invoke_entrypoint(sandbox_globals: Dict[str, Any], sandbox_locals: Dict[str, Any], function_name: str, args_dict: Dict[str, Any]) -> Any:
    """Resolve and call the script entry point: function_name -> main -> _sandbox_result -> None."""
    if function_name:
        if function_name not in sandbox_locals and function_name not in sandbox_globals:
            raise ValueError(f"Function '{function_name}' not found in script")
        func = sandbox_locals.get(function_name) or sandbox_globals.get(function_name)
        if asyncio.iscoroutinefunction(func):
            return await func(**args_dict)
        return func(**args_dict)

    if "main" in sandbox_locals or "main" in sandbox_globals:
        main_func = sandbox_locals.get("main") or sandbox_globals.get("main")
        if asyncio.iscoroutinefunction(main_func):
            return await main_func()
        return main_func()

    if "_sandbox_result" in sandbox_locals:
        return sandbox_locals["_sandbox_result"]
    if "_sandbox_result" in sandbox_globals:
        return sandbox_globals["_sandbox_result"]
    return None


async def _attach_browser_bridge(sandbox_globals: Dict[str, Any], spec: Dict[str, Any]) -> None:
    """
    Re-attach the browser from inside the child over CDP when possible.

    Only attempted when a CDP endpoint is configured; on any failure the
    placeholders from ``_prepare_sandbox_globals`` stay in place so scripts get
    a clear error instead of a crashed child.
    """
    endpoint = spec.get("cdp_endpoint", "")
    if not endpoint:
        return

    try:
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        browser = await playwright.chromium.connect_over_cdp(endpoint, timeout=15000)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        sandbox_globals["page"] = page
        sandbox_globals["browser"] = browser
        sandbox_globals["context"] = context
        logger.info("Sandbox child process attached to browser over CDP")
    except Exception as e:  # noqa: BLE001 - degrade to placeholders, never crash the child on attach
        logger.warning(f"Sandbox child could not attach to browser over CDP: {e}")


def _as_json_safe(value: Any) -> Any:
    """Round-trip a return value through JSON; fall back to repr for exotic types."""
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return {"__unserializable_result__": repr(value)}


def _run_agent_code(code_file: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the agent-authored code. Executes INSIDE the sandbox child process.

    Reuses the module's injection builders so the child gets exactly the same
    globals the old in-process exec() built, and preserves the historical
    return-value contract (function_name -> main -> _sandbox_result -> None).
    """
    with open(code_file, "r", encoding="utf-8") as f:
        code = f.read()

    function_name = spec.get("function_name", "")
    function_args = spec.get("function_args", "{}")
    try:
        args_dict = json.loads(function_args) if function_args else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid function_args JSON: {e}")

    sandbox_globals = _prepare_sandbox_globals(code_file, spec)
    sandbox_locals: Dict[str, Any] = {}

    async def _drive() -> Any:
        await _attach_browser_bridge(sandbox_globals, spec)

        exec(code, sandbox_globals, sandbox_locals)  # noqa: S102 - runs in the disposable child process

        return await _invoke_entrypoint(sandbox_globals, sandbox_locals, function_name, args_dict)

    result = asyncio.run(_drive())
    return {"success": True, "result": _as_json_safe(result)}


def _child_main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point running INSIDE the sandbox child process.

    Reads the spec, executes the agent code, and writes a JSON result envelope
    to ``result_file`` so the parent can distinguish "script failed" from
    "child died" (crash, rlimit kill, timeout kill leaves no envelope).
    """
    args = list(sys.argv[1:]) if argv is None else list(argv)

    envelope: Dict[str, Any] = {"success": False, "error": "Sandbox child process failed without a result"}
    try:
        if len(args) < 3:
            raise ValueError("sandbox child bootstrap requires <code_file> <spec_file> <result_file>")
        code_file, spec_file, result_file = args[0], args[1], args[2]

        with open(spec_file, "r", encoding="utf-8") as f:
            spec = json.load(f)

        envelope = _run_agent_code(code_file, spec)
    except BaseException as e:  # noqa: BLE001 - the child must always report back to the parent
        envelope = {"success": False, "error": f"Execution error: {str(e)}\n{traceback.format_exc()}"}

    try:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(envelope, f, default=str)
    except OSError:
        # Last resort: parent will report the child as crashed; emit for post-mortem.
        print(json.dumps(envelope, default=str), file=sys.stderr)

    return 0 if envelope.get("success") else 1


_CHILD_BOOTSTRAP_SOURCE = """\
import os
import sys
import types

# The sandbox child only needs config + the injection builders from the sandbox
# module. Pre-seed a stub for the telemetry package so importing it does not
# drag in the heavyweight LLM stack (langchain/torch); nothing in the child
# calls telemetry.
if "testzeus_hercules.telemetry" not in sys.modules:
    _telemetry_stub = types.ModuleType("testzeus_hercules.telemetry")
    _telemetry_stub.EventData = dict
    _telemetry_stub.EventType = type("EventType", (), {})
    _telemetry_stub.add_event = lambda *args, **kwargs: None
    sys.modules["testzeus_hercules.telemetry"] = _telemetry_stub

# Skip the eager pkgutil import of every tool module (see core/tools/__init__.py);
# the child only needs execute_python_sandbox itself.
os.environ.setdefault("HERCULES_SANDBOX_CHILD", "1")

from testzeus_hercules.core.tools.execute_python_sandbox import (  # noqa: E402
    _apply_child_resource_limits,
    _child_main,
)

_apply_child_resource_limits(__CPU_LIMIT__, __MEMORY_LIMIT__)
sys.exit(_child_main(sys.argv[1:]))
"""


def _build_child_command(code_file: Path, spec_file: Path, result_file: Path, effective_timeout: float) -> List[str]:
    """Build the `python -c <bootstrap> <code> <spec> <result>` command line."""
    bootstrap = _CHILD_BOOTSTRAP_SOURCE.replace("__CPU_LIMIT__", str(int(effective_timeout) + _CPU_LIMIT_GRACE_SECONDS)).replace("__MEMORY_LIMIT__", str(_SANDBOX_MEMORY_LIMIT_BYTES))
    return [
        sys.executable,
        "-c",
        bootstrap,
        str(code_file),
        str(spec_file),
        str(result_file),
    ]


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """
    SIGKILL the child and its whole process group (start_new_session makes the
    child its own group leader, so spawned grandchildren die with it).
    """
    if proc.poll() is not None:
        return
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _communicate_with_hard_kill(proc: subprocess.Popen, timeout: float) -> Tuple[str, bytes, bytes]:
    """
    Blocking helper (runs in an executor thread): wait for the child up to
    ``timeout`` seconds, then SIGKILL the process group and reap it.
    """
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        return "ok", stdout_bytes or b"", stderr_bytes or b""
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        stdout_bytes, stderr_bytes = proc.communicate()
        return "timeout", stdout_bytes or b"", stderr_bytes or b""


def _make_sandbox_run_dir(config: Any) -> Path:
    """Create a scratch dir for this run; workspace temp first, system temp as fallback."""
    workspace_root = Path(config.get_project_source_root())
    for base in (workspace_root / "temp", None):
        try:
            if base is not None:
                base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix="hercules_sandbox_", dir=str(base) if base else None))
        except OSError as e:
            logger.warning(f"Could not create sandbox run dir under {base}: {e}")
    raise RuntimeError("Could not create a temporary directory for sandbox execution")


def _read_result_envelope(result_file: Path) -> Optional[Dict[str, Any]]:
    """Read the child's JSON result envelope; None means the child never reported."""
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _sandbox_mode() -> str:
    """``inprocess`` (default, legacy) or ``subprocess``; read from config/env ``SANDBOX_MODE``."""
    raw = ""
    try:
        raw = str(get_global_conf().get_config().get("SANDBOX_MODE", "") or "")
    except Exception:  # config is best-effort here; env still applies
        raw = ""
    mode = (raw or os.getenv("SANDBOX_MODE", "")).strip().lower()
    if mode in ("subprocess", "isolated", "child"):
        return "subprocess"
    if mode and mode not in ("inprocess", "in-process", "in_process"):
        logger.warning(f"Unknown SANDBOX_MODE={mode!r}; using 'inprocess'")
    return "inprocess"


async def _execute_in_process(
    page: Any,
    browser: Any,
    context: Any,
    browser_manager: Any,
    file_path: str,
    timeout_seconds: float,
    function_name: str = "",
    function_args: str = "{}",
    tenant_id: str = "",
    custom_injections: str = "{}",
) -> Dict[str, Any]:
    """Legacy mode: exec the script inside the agent process with the LIVE Playwright objects injected."""
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    try:
        args_dict = json.loads(function_args) if function_args else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid function_args JSON: {e}")

    import io

    stdout_capture = io.StringIO()
    sandbox_globals = _prepare_sandbox_globals(
        file_path,
        {"tenant_id": tenant_id, "custom_injections": custom_injections},
        live_objects={"page": page, "browser": browser, "context": context, "playwright_manager": browser_manager},
    )
    sandbox_locals: Dict[str, Any] = {}

    async def execute_with_timeout() -> Any:
        old_stdout = sys.stdout
        sys.stdout = stdout_capture
        try:
            exec(code, sandbox_globals, sandbox_locals)  # noqa: S102 - historical in-process mode (not a security boundary)
            return await _invoke_entrypoint(sandbox_globals, sandbox_locals, function_name, args_dict)
        finally:
            sys.stdout = old_stdout

    try:
        result = await asyncio.wait_for(execute_with_timeout(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise ExecutionTimeout(f"Execution exceeded {timeout_seconds} seconds")
    except Exception as e:
        logger.error(f"Execution error: {str(e)}\n{traceback.format_exc()}")
        raise

    return {"success": True, "result": result, "stdout": _truncate_output(stdout_capture.getvalue().encode("utf-8")), "execution_time": 0.0}


async def _execute_in_sandbox(
    page: Any,
    browser: Any,
    context: Any,
    browser_manager: Any,
    file_path: str,
    timeout_seconds: float,
    function_name: str = "",
    function_args: str = "{}",
    tenant_id: str = "",
    custom_injections: str = "{}",
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch to the in-process (default) or subprocess executor according to ``SANDBOX_MODE``."""
    runner = _execute_in_subprocess if (mode or _sandbox_mode()) == "subprocess" else _execute_in_process
    return await runner(
        page=page,
        browser=browser,
        context=context,
        browser_manager=browser_manager,
        file_path=file_path,
        timeout_seconds=timeout_seconds,
        function_name=function_name,
        function_args=function_args,
        tenant_id=tenant_id,
        custom_injections=custom_injections,
    )


async def _execute_in_subprocess(
    page: Any,
    browser: Any,
    context: Any,
    browser_manager: Any,
    file_path: str,
    timeout_seconds: float,
    function_name: str = "",
    function_args: str = "{}",
    tenant_id: str = "",
    custom_injections: str = "{}",
) -> Dict[str, Any]:
    """
    Execute Python code in a disposable child process.

    The child is launched via `sys.executable -c` bootstrap, runs the code with
    the module's standard injections, and writes a JSON result envelope back.
    It runs under a hard wall-clock timeout (capped at ``_MAX_TIMEOUT_SECONDS``)
    and POSIX rlimits (CPU, address space); on timeout the whole process group
    is SIGKILLed. stdout/stderr are captured from the child and truncated, so
    sandbox output can no longer corrupt the agent's own stdout.

    Args:
        page: Parent-side Playwright page (unused here; live objects cannot
            cross the process boundary, the child re-attaches via CDP if configured)
        browser: Parent-side browser (unused here, see above)
        context: Parent-side browser context (unused here, see above)
        browser_manager: PlaywrightManager (unused here, see above)
        file_path: Absolute path to the Python file to execute
        timeout_seconds: Requested execution timeout (hard capped)
        function_name: Optional function to call
        function_args: Optional function arguments (JSON string)
        tenant_id: Tenant identifier for tenant-specific injections
        custom_injections: Optional JSON string with custom injections

    Returns:
        Dictionary with execution results: {success, result, stdout, stderr,
        execution_time}

    Raises:
        ValueError: On invalid arguments or a failing script
        RuntimeError: If the child died without reporting a result
        ExecutionTimeout: If the child exceeded the effective timeout
    """
    start_time = time.time()

    if timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}")
    effective_timeout = min(float(timeout_seconds), _MAX_TIMEOUT_SECONDS)
    if effective_timeout < float(timeout_seconds):
        logger.warning(f"Sandbox timeout capped at {_MAX_TIMEOUT_SECONDS}s (requested {timeout_seconds}s)")

    # Read the Python code
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Validate function arguments up-front
    try:
        json.loads(function_args) if function_args else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid function_args JSON: {e}")

    config = get_global_conf()
    cdp_config = config.get_cdp_config() or {}
    spec = {
        "file_path": file_path,
        "function_name": function_name,
        "function_args": function_args or "{}",
        "tenant_id": tenant_id,
        "custom_injections": custom_injections or "{}",
        "cdp_endpoint": cdp_config.get("endpoint_url", ""),
    }

    # Stage the code + spec under the workspace so the child picks them up
    run_dir = _make_sandbox_run_dir(config)
    code_file = run_dir / "agent_code.py"
    spec_file = run_dir / "spec.json"
    result_file = run_dir / "result.json"
    code_file.write_text(code, encoding="utf-8")
    spec_file.write_text(json.dumps(spec), encoding="utf-8")

    command = _build_child_command(code_file, spec_file, result_file, effective_timeout)
    logger.info(f"Launching sandbox child process (timeout={effective_timeout}s, run_dir={run_dir})")

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
    except OSError as e:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to launch sandbox child process: {e}")

    status = "error"
    stdout_bytes = b""
    stderr_bytes = b""
    envelope: Optional[Dict[str, Any]] = None
    try:
        # communicate() blocks, so run it in an executor thread; the event loop
        # stays responsive and wait_for bounds the total wait (timeout + grace).
        loop = asyncio.get_running_loop()
        try:
            status, stdout_bytes, stderr_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, _communicate_with_hard_kill, proc, effective_timeout),
                timeout=effective_timeout + _HARD_KILL_GRACE_SECONDS,
            )
        except asyncio.TimeoutError:
            # Even the grace window was exceeded (kill failed / pipe wedged):
            # stop waiting so the agent event loop can never be stuck here.
            _kill_process_tree(proc)
            status = "timeout"

        envelope = _read_result_envelope(result_file)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    stdout_output = _truncate_output(stdout_bytes)
    stderr_output = _truncate_output(stderr_bytes)

    if status == "timeout":
        raise ExecutionTimeout(f"Execution timed out after {effective_timeout}s")

    if envelope is None:
        raise RuntimeError(f"Sandbox child process exited unexpectedly (exit code {proc.returncode}) without a result. stderr tail: {stderr_output[-2000:]}")

    if not envelope.get("success"):
        raise ValueError(envelope.get("error") or "Execution error in sandbox child process")

    return {
        "success": True,
        "result": envelope.get("result"),
        "stdout": stdout_output,
        "stderr": stderr_output,
        "execution_time": round(time.time() - start_time, 3),
    }
