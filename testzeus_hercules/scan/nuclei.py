"""Run the nuclei scanner once and read its findings.

The command always keeps the scan on the target and the traffic on the app: no update
check (``-duc``), no out-of-band callbacks to public interaction servers (``-ni``; the
templates that need them are skipped), HTTP and TLS templates only, a request rate limit,
and intrusive, denial-of-service and fuzzing templates excluded unless the scan opts in
(an OpenAPI scan then also fuzzes the spec's operations with the scanner's DAST templates).
Findings are written as JSON lines without the raw requests and responses (``-or``).

Everything the scanner prints or writes is redacted before it is returned
(:func:`~testzeus_hercules.scan.redact.redact_tool_output`); the command is shown with
header values and URL credentials masked, while the process gets the real values.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from testzeus_hercules.cases.security import INTRUSIVE_TAGS, SecurityFinding, severity_of
from testzeus_hercules.kernel.child_env import child_environment
from testzeus_hercules.scan.redact import redact_command_for_log
from testzeus_hercules.scan.scope import ScanTarget

__all__ = ["NucleiOptions", "NucleiRun", "build_command", "parse_findings", "run_nuclei", "scanner_version"]

_TAIL_CHARS = 20_000
_EXTRACTED_KEPT = 5
_TEXT_CLIP = 300
_DESCRIPTION_CLIP = 500
_VERSION = re.compile(r"Engine Version:\s*v?([0-9][0-9A-Za-z.\-]*)")
# The scanner stops sending to a host after repeated errors (a closed port, an app that stopped answering).
_UNRESPONSIVE = re.compile(r"Skipped (\S+) from target list as found unresponsive")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PER_REQUEST_TIMEOUT_S = 10
_CONCURRENCY = 10

Redactor = Callable[[str], str]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NucleiOptions:
    tags: tuple[str, ...]
    exclude_tags: tuple[str, ...] = ()
    intrusive: bool = False
    rate_limit: int = 50
    timeout_s: float = 240.0
    headers: tuple[tuple[str, str], ...] = ()


@dataclass
class NucleiRun:
    command: str  # redacted
    returncode: int | None
    stdout: str  # redacted, last part only
    stderr: str  # redacted, last part only
    findings: list[SecurityFinding] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False
    unresponsive: tuple[str, ...] = ()  # hosts (host:port) the scanner gave up on


def excluded_tags(options: NucleiOptions) -> list[str]:
    risky = [] if options.intrusive else [tag for tag in INTRUSIVE_TAGS if tag not in options.exclude_tags]
    return list(dict.fromkeys([*risky, *options.exclude_tags]))


def build_command(binary: Path | str, target: ScanTarget, options: NucleiOptions, output: Path) -> list[str]:
    """The nuclei argv for one scan (real header values; log it with :func:`redact_command_for_log`)."""
    # Not -silent: the scanner says on stderr when it gave up on an unresponsive host, which must never read as a clean scan.
    command = [str(binary), "-duc", "-ni", "-nc", "-jsonl", "-or", "-o", str(output), "-tags", ",".join(options.tags), "-pt", "http,ssl"]
    excluded = excluded_tags(options)
    if excluded:
        command += ["-etags", ",".join(excluded)]
    if options.intrusive:
        chosen = [tag for tag in options.tags if tag in INTRUSIVE_TAGS]
        if chosen:
            command += ["-itags", ",".join(chosen)]  # run them even when nuclei's own configuration ignores them
    command += ["-rl", str(options.rate_limit), "-c", str(_CONCURRENCY), "-timeout", str(_PER_REQUEST_TIMEOUT_S), "-retries", "1"]
    if target.input == "openapi" and options.intrusive:
        # The spec's operations, their parameters fuzzed by the DAST templates (requests that may change data).
        assert target.spec is not None
        command += ["-im", target.spec_mode, "-l", str(target.spec), "-skip-format-validation", "-dast"]
    elif target.input == "openapi":
        # The API's URLs, scanned by the chosen templates like any URL.
        assert target.url_list is not None
        command += ["-l", str(target.url_list)]
    else:
        assert target.url is not None
        command += ["-u", target.url]
    for name, value in options.headers:
        command += ["-H", f"{name}: {value}"]
    return command


def _clip(text: str, limit: int = _TEXT_CLIP) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str) and value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def parse_findings(text: str, redact: Redactor) -> list[SecurityFinding]:
    """Findings from nuclei's JSON lines, redacted and de-duplicated; lines that are not findings are skipped."""
    findings: dict[tuple[str, str, str], SecurityFinding] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or not record.get("template-id"):
            continue
        raw_info = record.get("info")
        info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
        description = info.get("description")
        finding = SecurityFinding(
            template_id=redact(_clip(record["template-id"], 200)),
            name=redact(_clip(info.get("name") or "")),
            severity=severity_of(info.get("severity")),
            matched_at=redact(_clip(record.get("matched-at") or record.get("url") or record.get("host") or "", 500)),
            matcher=redact(_clip(record["matcher-name"], 200)) if record.get("matcher-name") else None,
            tags=[_clip(tag, 64) for tag in _strings(info.get("tags"))][:20],
            extracted=[redact(_clip(item)) for item in _strings(record.get("extracted-results"))][:_EXTRACTED_KEPT],
            description=redact(_clip(description, _DESCRIPTION_CLIP)) if isinstance(description, str) and description.strip() else None,
        )
        findings.setdefault((finding.template_id, finding.matcher or "", finding.matched_at), finding)
    return list(findings.values())


def _tail(data: bytes, redact: Redactor) -> str:
    text = _ANSI.sub("", data.decode(errors="replace"))
    return redact(text[-_TAIL_CHARS:])


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Stop the scanner and anything it started (its own process group on POSIX)."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5)


async def run_nuclei(command: Sequence[str], output: Path, timeout_s: float, redact: Redactor) -> NucleiRun:
    """Run the command, stop it after ``timeout_s`` (or when the caller is cancelled), and read the findings file."""
    started = time.monotonic()
    shown = redact(redact_command_for_log(command))
    logger.info("running the security scanner: %s", shown)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=sys.platform != "win32",
        env=child_environment(os.environ),  # no keys or tokens: a template must not be able to read them
    )
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        await _stop(process)
        stdout, stderr = b"", b""
    except BaseException:
        await _stop(process)  # cancelled with the step or the run: never leave a scan running
        raise
    skipped = tuple(dict.fromkeys(_UNRESPONSIVE.findall(_ANSI.sub("", stderr.decode(errors="replace")))))
    findings: list[SecurityFinding] = []
    if output.is_file():
        findings = parse_findings(output.read_text(encoding="utf-8", errors="replace"), redact)
    return NucleiRun(
        command=shown,
        returncode=process.returncode,
        stdout=_tail(stdout, redact),
        stderr=_tail(stderr, redact),
        findings=findings,
        duration_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
        unresponsive=skipped,
    )


_VERSIONS: dict[str, str | None] = {}


async def scanner_version(binary: Path | str) -> str | None:
    """The scanner's engine version (``3.11.1``), or None when it cannot be read; cached per executable."""
    key = str(binary)
    if key in _VERSIONS:
        return _VERSIONS[key]
    version: str | None = None
    try:
        process = await asyncio.create_subprocess_exec(key, "-version", "-duc", stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(process.communicate(), timeout=30)
        found = _VERSION.search(_ANSI.sub("", (out + err).decode(errors="replace")))
        version = found.group(1) if found else None
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            process.kill()
    except OSError:
        version = None
    _VERSIONS[key] = version
    return version
