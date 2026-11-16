"""Security steps: scan the case's own app and judge the findings in code.

A step's ``security`` payload (:class:`~testzeus_hercules.cases.models.SecurityScan`)
runs without a browser, like an API step (see :mod:`testzeus_hercules.engine.service`).
The scan targets the case's base URL (or a path under it, or the operations of a spec in
the project); its headers and auth take ``{{name}}`` placeholders, resolved from the
case's data and masked everywhere they are shown. The findings are the step's evidence
(``findings.jsonl``, ``scan.json``, ``summary.txt``) and one ledger entry says whether any
reaches ``fail_on``; when one does, the step fails with the failing findings as its
``actual``. A refused target, a missing scanner or a scan that did not finish is an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from testzeus_hercules.act.api import auth_headers
from testzeus_hercules.act.files import PathConfinementError
from testzeus_hercules.cases.models import SecurityScan, StepStatus
from testzeus_hercules.cases.security import SecurityScanResult
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.verify import LedgerEntry
from testzeus_hercules.kernel.run_context import EventSink

__all__ = ["SecurityOutcome", "run_security_step", "security_actual"]

_LEFTOVER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_LISTED = 10
_CLIP = 300


@dataclass(frozen=True)
class SecurityOutcome:
    status: StepStatus
    actual: str | None
    ledger: tuple[LedgerEntry, ...] = ()
    evidence: tuple[str, ...] = ()
    error_class: str | None = None
    result: SecurityScanResult | None = None


class _Problem(Exception):
    def __init__(self, message: str, error_class: str) -> None:
        super().__init__(message)
        self.message, self.error_class = message, error_class


def _resolved(data: CaseData, raw: str, where: str) -> str:
    text = data.resolve(raw)
    leftover = _LEFTOVER.search(text)
    if leftover:
        raise _Problem(f"{where}: unknown placeholder {{{{{leftover.group(1)}}}}}", "MissingData")
    return text


def _headers(data: CaseData, scan: SecurityScan) -> tuple[tuple[str, str], ...]:
    headers = [(_resolved(data, name, "headers"), _resolved(data, value, "headers")) for name, value in scan.headers.items()]
    if scan.auth is not None:
        extra, problem = auth_headers(scan.auth.type, _resolved(data, scan.auth.value, "auth"))
        if problem:
            raise _Problem(problem, "InvalidRequest")
        headers.extend(extra.items())
    return tuple(headers)


def security_actual(result: SecurityScanResult) -> str:
    """The step's ``actual``: the failing findings (most severe first), else the counts."""
    failing = result.failing()
    if not failing:
        return f"{result.summary_line()}; none at or above {result.fail_on}"
    listed = "; ".join(finding.line()[:_CLIP] for finding in failing[:_LISTED])
    more = f" (+{len(failing) - _LISTED} more)" if len(failing) > _LISTED else ""
    return f"{len(failing)} finding(s) at or above {result.fail_on}: {listed}{more}"


async def run_security_step(
    index: int,
    scan: SecurityScan,
    *,
    data: CaseData,
    evidence: EvidenceStore,
    project_dir: Path,
    base_url: str | None,
    env: Mapping[str, str],
    cache_dir: Path | None,
    events: EventSink,
) -> SecurityOutcome:
    """Run step ``index``'s scan; never raises for scan problems (they make the step an error)."""
    from testzeus_hercules.scan.install import ScannerMissing
    from testzeus_hercules.scan.runner import FINDINGS_FILE, SCAN_FILE, SUMMARY_FILE, ScanRequest, scan as run_scan
    from testzeus_hercules.scan.scope import ScopeError

    try:
        request = ScanRequest(
            base_url=_resolved(data, base_url, "base_url") if base_url else None,
            path=_resolved(data, scan.path, "path") if scan.path else None,
            openapi=_resolved(data, scan.openapi, "openapi") if scan.openapi else None,
            tags=tuple(scan.effective_tags()),
            exclude_tags=tuple(scan.exclude_tags),
            intrusive=scan.intrusive,
            fail_on=scan.fail_on,
            rate_limit=scan.rate_limit,
            timeout_s=float(scan.timeout_s),
            headers=_headers(data, scan),
            step=index,
        )
        hercules = project_dir / ".hercules"
        result = await run_scan(
            request,
            roots=(hercules / "specs", hercules, project_dir),
            out_dir=evidence.step_dir(index),
            env=env,
            cache_dir=cache_dir,
            secrets=data.secret_values(),
            redact=data.redact,
        )
    except _Problem as problem:
        return SecurityOutcome(status="error", actual=data.redact(problem.message), error_class=problem.error_class)
    except (ScopeError, PathConfinementError) as exc:
        return SecurityOutcome(status="error", actual=data.redact(f"scan refused: {exc}"), error_class="ScanRefused")
    except ScannerMissing as exc:
        return SecurityOutcome(status="error", actual=str(exc), error_class="ScannerMissing")
    uris = tuple(evidence.uri(index, name) for name in (SUMMARY_FILE, FINDINGS_FILE, SCAN_FILE))
    result = result.model_copy(update={"evidence": list(uris)})
    failing = result.failing()
    events.emit("action", step=index, action="security_scan", ok=result.error is None)
    events.emit("security_scan", step=index, findings=result.counts.findings, failing=len(failing), fail_on=result.fail_on, counts=result.counts.model_dump())
    expected = f"no finding at or above {result.fail_on}"
    if result.error is not None:
        entry = LedgerEntry(step=index, check="security_scan", expected=expected, actual=data.redact(f"error: {result.error}"[:_CLIP]), verdict="failed")
        return SecurityOutcome(status="error", actual=data.redact(f"{result.error} ({result.summary_line()})"), ledger=(entry,), evidence=uris, error_class="ScanFailed", result=result)
    actual = data.redact(security_actual(result))
    entry = LedgerEntry(step=index, check="security_scan", expected=expected, actual=actual[:_CLIP], verdict="failed" if failing else "passed")
    if failing:
        return SecurityOutcome(status="failed", actual=actual, ledger=(entry,), evidence=uris, error_class="SecurityFindings", result=result)
    return SecurityOutcome(status="passed", actual=actual, ledger=(entry,), evidence=uris, result=result)
