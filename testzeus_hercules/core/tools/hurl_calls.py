"""
LLM-free regression channel: run a pre-written Hurl suite (.hurl files) as a
deterministic check.

Hurl is an external binary (https://hurl.dev); when it is not installed the
tool returns a structured ERROR result instead of failing. The JUnit report is
parsed with the stdlib (ElementTree) so no extra dependency is involved.
"""

import asyncio
import glob
import json
import os
import re
import shutil
import signal
import tempfile
import time
import uuid
import xml.etree.ElementTree as ElementTree
from contextlib import suppress
from typing import Annotated, Any, Dict, List, Optional

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    interpolate_value,
    stringify_interpolated_value,
    truncate_for_log,
)
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.path_guard import PathConfinementError, resolve_in_roots
from testzeus_hercules.utils.redact import (
    REDACTED_PLACEHOLDER,
    is_sensitive_name,
    redact_url,
    scrub_secret_values,
)

HURL_BINARY = "hurl"

# Variables reach hurl as HURL_VARIABLE_<name> environment entries (never as
# --variable argv, which is visible to every local user via the process list).
_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

# Environment variables hurl may legitimately need. Everything else in the
# parent's environment (API keys, cloud credentials, tokens) stays out of the
# subprocess; LC_* names and HURL_VARIABLE_* entries are handled separately.
_HURL_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LANGUAGE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SYSTEMROOT",
        "USERPROFILE",
    }
)


def _hurl_environment(variables: Dict[str, str]) -> Dict[str, str]:
    """Minimal environment for the hurl subprocess plus the HURL_VARIABLE_* entries set by this tool."""
    env = {key: value for key, value in os.environ.items() if key.upper() in _HURL_ENV_ALLOWLIST or key.startswith("LC_")}
    env.update({f"HURL_VARIABLE_{name}": value for name, value in variables.items()})
    return env


_SECRET_HEADER_LINE_RE = re.compile(r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key)\s*:\s*).+$")
_URL_TOKEN_RE = re.compile(r"https?://[^\s'\"<>]+")

# Characters of hurl's raw stdout/stderr echoed back in the result payload.
MAX_OUTPUT_EXCERPT_CHARS = 8 * 1024

# Cap on the raw output appended to the proof log, so adversarial (e.g. very
# large) output cannot balloon the log file.
MAX_LOG_APPEND_BYTES = 1024 * 1024

# Grace period for draining the pipes after a timeout kill.
POST_KILL_DRAIN_SECONDS = 5


def _minified(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _redact_output(text: str, secret_values: List[str]) -> str:
    """Scrub secrets from raw hurl output: sensitive variable values, auth/cookie header lines and URL credentials."""
    text = scrub_secret_values(text, secret_values)
    text = _SECRET_HEADER_LINE_RE.sub(lambda match: match.group(1) + REDACTED_PLACEHOLDER, text)
    return _URL_TOKEN_RE.sub(lambda match: redact_url(match.group(0)), text)


def _secret_variable_values(variables: Dict[str, str]) -> List[str]:
    """Values of the variables whose NAME marks them as secret-bearing (scrubbed from output)."""
    return [value for name, value in variables.items() if is_sensitive_name(name)]


def _suite_files(hurl_path: str) -> List[str]:
    """Resolve the input to a sorted list of .hurl files (a directory counts as one suite)."""
    if os.path.isdir(hurl_path):
        return sorted(glob.glob(os.path.join(hurl_path, "*.hurl")))
    return [hurl_path]


def _suite_name(hurl_path: str) -> str:
    return os.path.basename(os.path.abspath(hurl_path).rstrip(os.sep))


def _suite_attr_int(suite: ElementTree.Element, attr: str) -> int:
    """Read an integer junit attribute; unparseable values count as 0."""
    try:
        return int(suite.get(attr, 0) or 0)
    except (TypeError, ValueError):
        logger.debug("JUnit attribute '%s' is not numeric (%r); treating it as 0.", attr, suite.get(attr))
        return 0


def _parse_junit_report(junit_path: str) -> Dict[str, Any]:
    """
    Parse a JUnit XML report with the stdlib.

    Aggregates every <testsuite> (hurl may emit one or more) and collects the
    message of each failing case.
    """
    tree = ElementTree.parse(junit_path)
    executed = 0
    failed = 0
    errored = 0
    failures: List[Dict[str, Optional[str]]] = []
    for suite in tree.getroot().iter("testsuite"):
        executed += _suite_attr_int(suite, "tests")
        failed += _suite_attr_int(suite, "failures")
        errored += _suite_attr_int(suite, "errors")
        for case in suite.iter("testcase"):
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if problem is not None:
                failures.append({"name": case.get("name"), "message": (problem.get("message") or problem.text or "").strip() or None})
    return {"executed": executed, "failed": failed + errored, "failures": failures}


def _kill_process_tree(process: "asyncio.subprocess.Process") -> None:
    """
    Kill the process and, on POSIX, its whole process group.

    The spawn below uses start_new_session=True, so the child leads its own
    process group; killing the group also takes down grandchildren (e.g. helper
    processes) that would otherwise keep the stdout pipe open forever.
    """
    try:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            process.kill()
    except ProcessLookupError:
        pass
    except Exception:
        with suppress(Exception):
            process.kill()


@tool(
    agent_names=["api_nav_agent"],
    name="run_hurl_suite",
    description=(
        "Run a pre-written Hurl suite (a .hurl file or a directory of them) as "
        "a fully deterministic, LLM-free regression check. Use this for checks "
        "that are already known instead of re-deciding them with assertions. "
        "The path must be inside the test data / project folder. "
        "Variable values may reference captured values as ${name}. Requires "
        "the hurl binary on PATH; returns a structured ERROR result when it is "
        "missing."
    ),
)
async def run_hurl_suite(
    hurl_path: Annotated[str, "Path to a .hurl file, or to a directory whose *.hurl files run as one suite."],
    variables: Annotated[Optional[Dict[str, Any]], "Variables passed to hurl through HURL_VARIABLE_<name> environment entries (never argv); values may use ${name} placeholders."] = None,
    timeout_seconds: Annotated[int, "Kill the suite after this many seconds. Defaults to 60."] = 60,
) -> Annotated[str, "Minified JSON verdict with executed/succeeded/failed counts and per-case failures."]:
    hurl_binary = shutil.which(HURL_BINARY)
    if hurl_binary is None:
        return _minified(
            {
                "assertion": "ERROR",
                "suite": _suite_name(hurl_path),
                "reason": (f"'{HURL_BINARY}' is not installed or not on PATH. Install the hurl binary " "(e.g. via the system package manager or from https://hurl.dev) to run Hurl suites."),
            }
        )

    suite = _suite_name(hurl_path)
    # Model-supplied path: Hurl files can send arbitrary requests and write
    # files, so only files inside the test data / project roots may run. The
    # resolved absolute paths are passed on, so a leading '-' can never be
    # read as an option.
    try:
        resolved_input = resolve_in_roots(hurl_path)
        files = [resolve_in_roots(item) for item in _suite_files(resolved_input)]
    except PathConfinementError as e:
        return _minified({"assertion": "ERROR", "suite": suite, "reason": str(e)})
    if not files or not all(os.path.exists(item) for item in files):
        return _minified({"assertion": "ERROR", "suite": suite, "reason": f"No .hurl input found at '{hurl_path}'."})

    # Variables may themselves be ${name} — bridging an LLM-captured value
    # into an otherwise LLM-free suite.
    resolved_variables = {str(key): stringify_interpolated_value(interpolate_value(value)) for key, value in (variables or {}).items()}
    invalid_names = sorted(name for name in resolved_variables if not _VARIABLE_NAME_RE.match(name))
    if invalid_names:
        return _minified({"assertion": "ERROR", "suite": suite, "reason": f"Invalid hurl variable name(s): {', '.join(invalid_names)} (use letters, digits, '_' or '-')."})
    secret_values = _secret_variable_values(resolved_variables)
    process_env = _hurl_environment(resolved_variables)

    # A directory (not mkstemp): hurl panics when the report path already
    # exists, so the report file itself must not pre-exist.
    junit_dir = tempfile.mkdtemp(prefix="hurl-junit-")
    junit_path = os.path.join(junit_dir, "report.xml")
    try:
        command = [hurl_binary, "--test", "--report-junit", junit_path] + files

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env=process_env,
            )
        except (OSError, ValueError) as e:
            # ValueError: a variable name or value contains an embedded NUL byte.
            offending_variables = sorted(key for key, value in resolved_variables.items() if "\x00" in key or "\x00" in value)
            reason = f"Failed to launch {HURL_BINARY}: {e}"
            if offending_variables:
                reason += f" (offending variable(s): {', '.join(offending_variables)})"
            return _minified({"assertion": "ERROR", "suite": suite, "reason": reason})

        # communicate() runs as a task so a timeout can kill the process group
        # WITHOUT cancelling the read: cancelling it would discard the output
        # it had already consumed, and asyncio.wait (unlike wait_for) leaves
        # the task running for a bounded drain afterwards.
        communicate_task = asyncio.create_task(process.communicate())
        done, _ = await asyncio.wait({communicate_task}, timeout=timeout_seconds)
        timed_out = communicate_task not in done
        if timed_out:
            _kill_process_tree(process)
            output_bytes = b""
            # Bounded drain: keeps whatever was written before the kill, while
            # a pipe held open by a straggler cannot hang this past the grace
            # period.
            drained, _ = await asyncio.wait({communicate_task}, timeout=POST_KILL_DRAIN_SECONDS)
            if drained:
                with suppress(Exception):
                    output_bytes, _ = communicate_task.result()
            else:
                communicate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await communicate_task
        else:
            output_bytes, _ = await communicate_task

        # Redact before anything is logged or handed to the model.
        raw_output = _redact_output(output_bytes.decode("utf-8", errors="ignore"), secret_values)
        _append_log(raw_output)

        if timed_out:
            return _minified(
                {
                    "assertion": "ERROR",
                    "suite": suite,
                    "reason": f"Hurl suite timed out after {timeout_seconds}s and was killed.",
                    "output_excerpt": truncate_for_log(raw_output, MAX_OUTPUT_EXCERPT_CHARS),
                }
            )
        if not os.path.isfile(junit_path):
            return _minified(
                {
                    "assertion": "ERROR",
                    "suite": suite,
                    "reason": f"Hurl exited with code {process.returncode} without producing a JUnit report.",
                    "output_excerpt": truncate_for_log(raw_output, MAX_OUTPUT_EXCERPT_CHARS),
                }
            )

        try:
            report = _parse_junit_report(junit_path)
        except ElementTree.ParseError as e:
            return _minified(
                {
                    "assertion": "ERROR",
                    "suite": suite,
                    "reason": f"Could not parse the Hurl JUnit report: {e}",
                    "output_excerpt": truncate_for_log(raw_output, MAX_OUTPUT_EXCERPT_CHARS),
                }
            )

        saved_copy = _save_report_copy(junit_path, suite)

        executed = report["executed"]
        failed = report["failed"]
        failures = [{"name": item["name"], "message": _redact_output(item["message"], secret_values) if item["message"] else None} for item in report["failures"]]
        # The JUnit counts alone are not enough: a non-zero exit code (crash,
        # connection error, ...) means the suite did not succeed either.
        clean_exit = process.returncode == 0
        verdict: Dict[str, Any] = {
            "assertion": "PASSED" if failed == 0 and executed > 0 and clean_exit else "FAILED",
            "suite": suite,
            "executed": executed,
            "succeeded": executed - failed,
            "failed": failed,
            "failures": failures,
            "junit_report": saved_copy,
            "exit_code": process.returncode,
            "output_excerpt": truncate_for_log(raw_output, MAX_OUTPUT_EXCERPT_CHARS),
        }
        if failed == 0 and executed > 0 and not clean_exit:
            verdict["reason"] = f"Hurl exited with code {process.returncode} although the JUnit report lists no failed cases; the suite is not reported as passed."
        return _minified(verdict)
    finally:
        with suppress(OSError):
            shutil.rmtree(junit_dir)


def _append_log(raw_output: str) -> None:
    """Append hurl's raw output to the hurl log in the proof folder (capped)."""
    try:
        encoded = raw_output.encode("utf-8", errors="ignore")
        if len(encoded) > MAX_LOG_APPEND_BYTES:
            head = encoded[:MAX_LOG_APPEND_BYTES].decode("utf-8", errors="ignore")
            encoded = f"{head}\n... [truncated {len(encoded)} bytes total]".encode("utf-8")
        proof_path = get_global_conf().get_proof_path()
        hurl_log_path = os.path.join(proof_path, "hurl_logs.log")
        with open(hurl_log_path, "ab") as log_file:
            log_file.write(encoded + b"\n")
    except Exception as e:
        logger.warning(f"Failed to append to hurl_logs.log: {e}")


def _save_report_copy(junit_path: str, suite: str) -> Optional[str]:
    """Save a copy of the JUnit report under proofs/hurl_results/."""
    try:
        proof_path = get_global_conf().get_proof_path()
        results_dir = os.path.join(proof_path, "hurl_results")
        os.makedirs(results_dir, exist_ok=True)
        # The uuid suffix keeps same-millisecond saves from colliding on (and
        # silently overwriting) one path.
        copy_path = os.path.join(results_dir, f"{suite}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.xml")
        shutil.copyfile(junit_path, copy_path)
        return copy_path
    except Exception as e:
        logger.warning(f"Failed to save the Hurl JUnit report copy: {e}")
        return None
