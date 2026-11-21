"""Structured quality results: findings, verified fix suggestions and the report that aggregates them.

A :class:`Finding` says what is wrong with which element, measured by code. A :class:`FixSuggestion`
carries a concrete value for one property or attribute and whether applying it to the live page
actually resolved the finding (``verified``). Both are plain pydantic models so they travel through
MCP, the UI and reports unchanged.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = ["IMPACT_ORDER", "DiffClassification", "Finding", "FixSuggestion", "Impact", "Kind", "Level", "QualityReport", "StyleDelta", "at_or_above", "finding_id", "selector_of"]

Kind = Literal["a11y", "visual", "layout"]
Level = Literal["A", "AA", "AAA", "none"]
Impact = Literal["critical", "serious", "moderate", "minor"]
IMPACT_ORDER: tuple[Impact, ...] = ("critical", "serious", "moderate", "minor")  # most severe first
Method = Literal["css_override", "attribute", "text"]
SELECTOR_JOIN = " >> "


def selector_of(segments: list[str] | tuple[str, ...]) -> str:
    """One Playwright selector from a perception path: one CSS selector per (shadow) tree, chained."""
    return SELECTOR_JOIN.join(segments)


def at_or_above(impact: str, gate: str) -> bool:
    """True when ``impact`` is as severe as ``gate`` or more (``critical`` is the most severe)."""
    return IMPACT_ORDER.index(impact) <= IMPACT_ORDER.index(gate)  # type: ignore[arg-type]


def finding_id(rule: str, frame_path: list[str] | tuple[str, ...], selector: str, extra: str = "") -> str:
    """Stable id of a finding: same rule on the same element gives the same id across runs."""
    digest = hashlib.sha1("\x00".join([rule, *frame_path, selector, extra]).encode("utf-8")).hexdigest()[:10]
    return f"{rule}:{digest}"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Finding(_Model):
    """One problem on one element, with the values code measured."""

    id: str
    kind: Kind
    rule: str
    source: Literal["axe", "hercules"] = "hercules"
    wcag: str | None = None
    level: Level = "none"
    impact: Impact = "moderate"
    element_md: int | None = None
    selector: str
    frame_path: list[str] = Field(default_factory=list)
    frame: str | None = None
    element_desc: str = ""
    message: str
    measured: dict[str, Any] | None = None
    evidence: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    help_url: str | None = None

    @property
    def segments(self) -> list[str]:
        return self.selector.split(SELECTOR_JOIN)


class FixSuggestion(_Model):
    """A concrete change for one finding and the result of trying it on the live page.

    ``changes`` is exactly what was applied for verification: CSS declarations (``css_override``,
    optionally under a ``state`` pseudo-class such as ``:focus-visible``) or attribute values
    (``attribute`` / ``text``). ``target_selector`` names the element changed when it is not the
    finding's own element (a flex container, say), in the same frame.
    """

    finding_ref: str
    property_or_attr: str
    current: str | None = None
    suggested: str
    changes: dict[str, str] = Field(default_factory=dict)
    state: str | None = None
    target_selector: str | None = None
    after_fix: dict[str, Any] | None = None
    verified: bool = False
    method: Method
    snippet: str | None = None
    likely_file: str | None = None
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class StyleDelta(_Model):
    """One computed-style (or box) property of a key element that differs from its baseline value."""

    key: str
    selector: str
    property: str
    before: str
    after: str


class DiffClassification(_Model):
    """A decision engine's reading of a visual diff: intended change, layout break or content change."""

    label: str
    confidence: float
    probabilities: dict[str, float]
    engine: str


class QualityReport(_Model):
    """Every finding and fix from one audit of one page (or one page across viewports)."""

    url: str = ""
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[FixSuggestion] = Field(default_factory=list)
    not_scanned: list[str] = Field(default_factory=list)
    engine: dict[str, str] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def counts(self) -> dict[str, Any]:
        """Totals by kind, level and impact, plus fixes and verified fixes."""
        return {
            "findings": len(self.findings),
            "by_kind": dict(sorted(Counter(f.kind for f in self.findings).items())),
            "by_level": dict(sorted(Counter(f.level for f in self.findings).items())),
            "by_impact": dict(sorted(Counter(f.impact for f in self.findings).items())),
            "fixes": len(self.fixes),
            "verified_fixes": sum(1 for fix in self.fixes if fix.verified),
        }

    def merged(self, other: QualityReport) -> QualityReport:
        """A new report with ``other``'s findings and fixes added; findings with the same id are kept once."""
        seen = {f.id for f in self.findings}
        findings = [*self.findings, *(f for f in other.findings if f.id not in seen)]
        return QualityReport(
            url=self.url or other.url,
            findings=findings,
            fixes=[*self.fixes, *other.fixes],
            not_scanned=sorted({*self.not_scanned, *other.not_scanned}),
            engine={**self.engine, **other.engine},
        )

    def fixes_for(self, finding: Finding) -> list[FixSuggestion]:
        return [fix for fix in self.fixes if fix.finding_ref == finding.id]
