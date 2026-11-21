"""One security scan, from a request to its result and evidence.

:func:`scan` checks the scope (the base URL, or a spec inside the project) and the
tags, finds the scanner, runs it and writes three files into ``out_dir``:

* ``findings.jsonl``: one finding per line (redacted; no raw requests or responses),
* ``scan.json``: what ran (the command with secrets masked), the scanner version, the
  exit code, the end of its output (redacted) and the counts,
* ``summary.txt``: the same for a person: the findings by severity, failing ones first.

A refused target or a missing scanner raises before anything runs
(:class:`~testzeus_hercules.scan.scope.ScopeError`, :class:`~testzeus_hercules.act.files.PathConfinementError`,
:class:`~testzeus_hercules.scan.install.ScannerMissing`).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, Sequence

from testzeus_hercules.cases.security import DEFAULT_SCAN_TAGS, INTRUSIVE_TAGS, SEVERITY_ORDER, SecurityFinding, SecurityScanResult, Severity, at_or_above
from testzeus_hercules.scan.install import find_nuclei
from testzeus_hercules.scan.nuclei import NucleiOptions, NucleiRun, build_command, run_nuclei, scanner_version
from testzeus_hercules.scan.redact import credential_header_values, redact_tool_output
from testzeus_hercules.scan.scope import ScanTarget, ScopeError, openapi_target, url_target

__all__ = ["FINDINGS_FILE", "SCAN_FILE", "SUMMARY_FILE", "ScanRequest", "render_summary", "scan"]

FINDINGS_FILE = "findings.jsonl"
SCAN_FILE = "scan.json"
SUMMARY_FILE = "summary.txt"

Runner = Callable[[Sequence[str], Path, float, Callable[[str], str]], Awaitable[NucleiRun]]


@dataclass(frozen=True)
class ScanRequest:
    """What to scan and how; ``headers`` hold real values (they are masked wherever they are shown)."""

    base_url: str | None = None
    path: str | None = None
    openapi: str | None = None
    tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    intrusive: bool = False
    fail_on: Severity = "medium"
    rate_limit: int = 50
    timeout_s: float = 240.0
    headers: tuple[tuple[str, str], ...] = field(default=())
    step: int | None = None

    def effective_tags(self) -> tuple[str, ...]:
        return self.tags or DEFAULT_SCAN_TAGS


def _check_tags(request: ScanRequest) -> None:
    if request.fail_on not in SEVERITY_ORDER:
        raise ScopeError(f"fail_on must be one of {', '.join(SEVERITY_ORDER)}")
    if not request.intrusive:
        risky = sorted(set(request.effective_tags()) & set(INTRUSIVE_TAGS))
        if risky:
            raise ScopeError(f"tag(s) {', '.join(risky)} run intrusive templates: allow them explicitly (intrusive: true, or --allow-intrusive), only on an app you may break")


def _target(request: ScanRequest, roots: Iterable[Path], work: Path) -> ScanTarget:
    if request.openapi is not None:
        if request.path is not None:
            raise ScopeError("a scan takes a path or an OpenAPI spec, not both")
        return openapi_target(request.openapi, roots, request.base_url, work)
    return url_target(request.base_url, request.path)


async def scan(
    request: ScanRequest,
    *,
    roots: Sequence[Path],
    out_dir: Path,
    env: Mapping[str, str],
    cache_dir: Path | None = None,
    secrets: Iterable[str] = (),
    redact: Callable[[str], str] = lambda text: text,
    binary: Path | None = None,
    runner: Runner = run_nuclei,
) -> SecurityScanResult:
    """Run one scan and write its evidence into ``out_dir``; see the module docstring for what raises."""
    _check_tags(request)
    known = [*secrets, *credential_header_values(request.headers)]

    def redactor(text: str) -> str:
        return redact(redact_tool_output(text, known))

    with tempfile.TemporaryDirectory(prefix="hercules-scan-") as scratch:
        work = Path(scratch)
        target = _target(request, roots, work)  # the scope is checked before the scanner is looked for
        executable = binary or find_nuclei(env, cache_dir)
        options = NucleiOptions(
            tags=request.effective_tags(),
            exclude_tags=request.exclude_tags,
            intrusive=request.intrusive,
            rate_limit=request.rate_limit,
            timeout_s=request.timeout_s,
            headers=request.headers,
        )
        output = work / "nuclei.jsonl"
        command = build_command(executable, target, options, output)
        version = await scanner_version(executable)
        run = await runner(command, output, request.timeout_s, redactor)
    result = SecurityScanResult(
        step=request.step,
        target=redactor(target.shown),
        input=target.input,
        tags=list(options.tags),
        exclude_tags=list(request.exclude_tags),
        fail_on=request.fail_on,
        scanner=f"nuclei {version}" if version else "nuclei",
        duration_ms=run.duration_ms,
        findings=_ordered(run.findings),
        error=_problem(run, request.timeout_s, frozenset(origin.split("://", 1)[1] for origin in target.origins)),
    )
    _write_evidence(out_dir, result, run, target)
    return result


def _ordered(findings: list[SecurityFinding]) -> list[SecurityFinding]:
    return sorted(findings, key=lambda finding: (SEVERITY_ORDER.index(finding.severity), finding.template_id, finding.matched_at))


def _problem(run: NucleiRun, timeout_s: float, target_hosts: frozenset[str]) -> str | None:
    if run.timed_out:
        return f"the scan was stopped after {timeout_s:g} s (its time limit, timeout_s); the findings so far are kept"
    # Templates may also probe other ports of the host; only giving up on the app itself makes the scan incomplete.
    gave_up = next((host for host in run.unresponsive if host in target_hosts), None)
    if gave_up is not None:
        return (
            f"the app stopped answering: the scanner gave up on {gave_up} after repeated errors, so the scan is incomplete. " "Check that the app is up and the base URL is right, or lower rate_limit"
        )
    if run.returncode in (0, None):
        return None
    lines = [line.strip() for line in run.stderr.splitlines() if line.strip()]
    detail = next((line for line in reversed(lines) if "FTL" in line or "ERR" in line), lines[-1] if lines else "no output")
    hint = ""
    if "no templates" in run.stderr.lower() or "templates are not installed" in run.stderr.lower():
        hint = "; download the templates with `hercules scan --update-templates` (or `nuclei -update-templates`)"
    return f"nuclei failed (exit {run.returncode}): {detail[:400]}{hint}"


def _write_evidence(out_dir: Path, result: SecurityScanResult, run: NucleiRun, target: ScanTarget) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(finding.model_dump(mode="json", exclude_none=True), ensure_ascii=False) for finding in result.findings]
    (out_dir / FINDINGS_FILE).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    record = {
        "target": result.target,
        "input": result.input,
        "origins": list(target.origins),
        "tags": result.tags,
        "exclude_tags": result.exclude_tags,
        "fail_on": result.fail_on,
        "scanner": result.scanner,
        "command": run.command,
        "exit_code": run.returncode,
        "timed_out": run.timed_out,
        "duration_ms": result.duration_ms,
        "counts": result.counts.model_dump(),
        "failing": len(result.failing()),
        "error": result.error,
        "stderr_tail": run.stderr[-4000:],
    }
    (out_dir / SCAN_FILE).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / SUMMARY_FILE).write_text(render_summary(result), encoding="utf-8")


def render_summary(result: SecurityScanResult) -> str:
    """A readable report of one scan: what was scanned, the counts, each failing finding, then the others by template."""
    head = f"Security scan of {result.target} ({'OpenAPI operations' if result.input == 'openapi' else 'URL'})"
    lines = [head, f"tags: {', '.join(result.tags)}; fails on: {result.fail_on} or above; {result.scanner or 'nuclei'}; {result.duration_ms / 1000:.1f} s"]
    if result.exclude_tags:
        lines.append(f"excluded tags: {', '.join(result.exclude_tags)}")
    lines.append(result.summary_line())
    if result.error:
        lines.append(f"error: {result.error}")
    failing = result.failing()
    lines.append(f"{len(failing)} finding(s) at or above {result.fail_on}:" if failing else f"no finding at or above {result.fail_on}")
    for finding in failing:
        lines.append(f"  FAIL {finding.line()}{f'  {finding.name}' if finding.name else ''}")
        if finding.extracted:
            lines.append(f"       extracted: {', '.join(finding.extracted)}")
    others: dict[tuple[str, str], list[SecurityFinding]] = {}
    for finding in result.findings:
        if not at_or_above(finding.severity, result.fail_on):
            others.setdefault((finding.severity, finding.template_id), []).append(finding)
    if others:
        lines.append(f"below {result.fail_on}:")
    for (severity, template), group in others.items():
        places = list(dict.fromkeys(finding.matched_at for finding in group))
        where = places[0] if len(places) == 1 else f"{len(places)} URLs, e.g. {places[0]}"
        matchers = sorted({finding.matcher for finding in group if finding.matcher})
        detail = f" ({len(matchers)} matchers: {', '.join(matchers[:6])}{', ...' if len(matchers) > 6 else ''})" if len(matchers) > 1 else f" ({matchers[0]})" if matchers else ""
        lines.append(f"  [{severity}] {template}{detail} on {where}{f'  {group[0].name}' if group[0].name else ''}")
    return "\n".join(lines) + "\n"
