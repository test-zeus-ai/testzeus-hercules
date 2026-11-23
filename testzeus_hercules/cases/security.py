"""Security scan results: the findings of a scan step, and the run's security section.

A security step (:class:`~testzeus_hercules.cases.models.SecurityScan`) runs the nuclei
scanner against the case's own app. Each finding keeps the template that matched, its
severity, where it matched and what it extracted (already redacted). Severities follow
nuclei: ``critical`` > ``high`` > ``medium`` > ``low`` > ``info``; nuclei's ``unknown``
counts as ``info``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
SEVERITY_ORDER: tuple[Severity, ...] = ("critical", "high", "medium", "low", "info")  # most severe first

Count = Annotated[int, Field(ge=0)]

SCAN_TAG_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
# Template tags a scan runs when the step names none: known CVEs, exposed files and data, misconfigurations.
DEFAULT_SCAN_TAGS: tuple[str, ...] = ("cve", "exposure", "misconfig")
# Template tags that attack the app harder (exploits that change state, denial of service, fuzzing and the scanner's
# DAST templates that inject payloads into requests, password guessing and default-password logins, which can lock
# accounts). They are excluded from every scan unless the scan opts in (``intrusive: true`` / ``--allow-intrusive``).
INTRUSIVE_TAGS: tuple[str, ...] = ("intrusive", "dos", "fuzz", "fuzzing", "dast", "bruteforce", "default-login")


def severity_of(value: object) -> Severity:
    """A nuclei severity as one of :data:`SEVERITY_ORDER` (``unknown`` and anything else count as ``info``)."""
    text = str(value or "").strip().lower()
    return text if text in SEVERITY_ORDER else "info"  # type: ignore[return-value]


def at_or_above(severity: str, gate: str) -> bool:
    """True when ``severity`` is as severe as ``gate`` or more."""
    return SEVERITY_ORDER.index(severity_of(severity)) <= SEVERITY_ORDER.index(severity_of(gate))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecurityFinding(_Model):
    """One match of a scanner template on the app (text already redacted)."""

    template_id: str
    name: str = ""
    severity: Severity = "info"
    matched_at: str = Field(default="", description="The URL (or host) the template matched")
    matcher: str | None = Field(default=None, description="Which of the template's matchers fired")
    tags: list[str] = Field(default_factory=list)
    extracted: list[str] = Field(default_factory=list, description="Values the template extracted (first few, clipped)")
    description: str | None = None

    def line(self) -> str:
        matcher = f":{self.matcher}" if self.matcher else ""
        return f"[{self.severity}] {self.template_id}{matcher} {self.matched_at}".strip()


class SecurityCounts(_Model):
    """Findings per severity, and their total."""

    critical: Count = 0
    high: Count = 0
    medium: Count = 0
    low: Count = 0
    info: Count = 0
    findings: Count = 0


def count_findings(findings: list[SecurityFinding] | tuple[SecurityFinding, ...]) -> SecurityCounts:
    return SecurityCounts(**{severity: sum(finding.severity == severity for finding in findings) for severity in SEVERITY_ORDER}, findings=len(findings))


class SecurityScanResult(_Model):
    """One scan: what was scanned with which tags, its findings, and the ones at or above ``fail_on``."""

    step: Annotated[int, Field(ge=1)] | None = Field(default=None, description="The step that ran the scan (null for `hercules scan`)")
    target: str = Field(description="The URL scanned, or the OpenAPI spec (relative to the project) whose operations were scanned")
    input: Literal["url", "openapi"] = "url"
    tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    fail_on: Severity = "medium"
    scanner: str | None = Field(default=None, description="Scanner and version, e.g. nuclei 3.11.1")
    templates: str | None = Field(default=None, description="Templates version, when known")
    duration_ms: Count = 0
    findings: list[SecurityFinding] = Field(default_factory=list)
    counts: SecurityCounts = Field(default_factory=lambda: SecurityCounts(), description="Computed from findings; any value given is replaced")
    error: str | None = Field(default=None, description="Why the scan did not finish (scanner missing, refused target, timeout)")
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _count(self) -> SecurityScanResult:
        object.__setattr__(self, "counts", count_findings(self.findings))  # derived, never trusted from input
        return self

    def failing(self) -> list[SecurityFinding]:
        """Findings at or above ``fail_on``."""
        return [finding for finding in self.findings if at_or_above(finding.severity, self.fail_on)]

    def summary_line(self) -> str:
        counts = self.counts
        by_severity = ", ".join(f"{getattr(counts, severity)} {severity}" for severity in SEVERITY_ORDER if getattr(counts, severity))
        return f"{counts.findings} finding(s){f' ({by_severity})' if by_severity else ''} on {self.target}"


class SecuritySection(_Model):
    """Every security scan of a run, and the findings per severity over all of them."""

    scans: list[SecurityScanResult] = Field(default_factory=list)
    counts: SecurityCounts = Field(default_factory=lambda: SecurityCounts(), description="Computed from scans; any value given is replaced")

    @model_validator(mode="after")
    def _count(self) -> SecuritySection:
        object.__setattr__(self, "counts", count_findings([finding for scan in self.scans for finding in scan.findings]))
        return self

    def summary_line(self) -> str:
        counts = self.counts
        by_severity = ", ".join(f"{getattr(counts, severity)} {severity}" for severity in SEVERITY_ORDER if getattr(counts, severity))
        return f"Security: {counts.findings} finding(s) in {len(self.scans)} scan(s){f' ({by_severity})' if by_severity else ''}"
