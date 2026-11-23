"""Quality checks in a run: ``quality`` steps and the run's ``a11y`` / ``visual`` page audits.

A :class:`QualitySession` lives for one run. It runs the checks in :mod:`testzeus_hercules.quality`
on the live page and judges them in code:

* a quality **step** (a step with a ``quality`` payload, or one the model read into it) fails when a
  finding is at or above the check's ``fail_on`` impact; each finding comes with a fix value that was
  applied in the page and re-checked (``verified``). A visual step whose baseline does not exist yet
  creates it and is ``unverified`` (never passed); a visual difference above the pixel gate is
  classified by the decision engine (intended change, layout break, content change), and the class
  sets the finding's impact: a layout break is ``serious``, the other two ``minor``. A changed page
  that does not reach ``fail_on`` is ``unverified``, so a step never passes on a page that differs
  from its baseline.
* the run's **audits** (:class:`RunQualityOptions`) run after every browser step that leaves the page
  in a state not audited yet (URL, title, main headings and open dialogs): ``a11y`` runs the
  accessibility audit (without the keyboard walk, which moves focus), ``visual`` compares the page
  with a per-case baseline (created on the first run) and checks the layout at 375/768/1280 px.
  They never fail a step; the run fails only when ``fail_on`` is set and a finding reaches it.

Evidence is written under the step's directory, ``steps/<n>/quality/``: the findings and fixes as
JSON, a screenshot with a numbered box per finding (``*.annotated.png``, numbered in the findings'
order) and, for visual checks, ``<baseline>.before.png`` / ``.after.png`` / ``.diff.png``.
Baselines live in ``<project>/.hercules/baselines/``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from testzeus_hercules.cases.gherkin import VERIFY_ACTION
from testzeus_hercules.cases.models import QualityCheck, QualityPage, QualitySection, QualityVisual, Step, StepStatus, WcagLevel
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.quality_intent import QualityInterpreter
from testzeus_hercules.engine.verify import LedgerEntry
from testzeus_hercules.kernel.run_context import EventSink, NullEventSink
from testzeus_hercules.quality.findings import IMPACT_ORDER, DiffClassification, Finding, FixSuggestion, Impact, SELECTOR_JOIN, at_or_above

logger = logging.getLogger(__name__)

__all__ = ["DIFF_IMPACT", "QualityOutcome", "QualityReading", "QualitySession", "RunQualityOptions", "baseline_dir"]

BASELINES_DIRNAME = "baselines"
DIFF_IMPACT: dict[str, Impact] = {"layout_break": "serious", "intended_change": "minor", "content_change": "minor"}
DEFAULT_AUDIT_TIMEOUT_S = 180.0
_LISTED = 3
_CLIP = 160
_MAX_FILE_BYTES = 200_000
_MAX_FILES = 20
_EFFECTS = frozenset({"x", "y", "box-width", "box-height", "width", "height"})
_SLUG = re.compile(r"[^a-z0-9]+")
_LEVELS: dict[str, frozenset[str]] = {"A": frozenset({"A", "none"}), "AA": frozenset({"A", "AA", "none"}), "AAA": frozenset({"A", "AA", "AAA", "none"})}
_STATE_JS = """() => {
  const parts = [location.href.split('#')[0], document.title];
  const marks = document.querySelectorAll('h1, h2, dialog[open], [role="dialog"], [role="alertdialog"]');
  for (const el of Array.from(marks).slice(0, 20)) {
    if (el.getClientRects().length === 0) continue;
    parts.push(el.tagName + ':' + (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60));
  }
  return parts.join('\\n');
}"""
_IN_SCOPE_JS = """([anchors, scope]) => {
  if (!document.querySelector(scope)) return null;
  return anchors.map((a) => { try { const el = document.querySelector(a); return !!(el && el.closest(scope)); } catch (e) { return false; } });
}"""


def baseline_dir(project_hercules_dir: Path) -> Path:
    """Where visual baselines are kept for a project."""
    return project_hercules_dir / BASELINES_DIRNAME


@dataclass(frozen=True)
class RunQualityOptions:
    """The run's own page audits; off by default. ``fail_on`` None reports findings without failing the run."""

    a11y: bool = False
    visual: bool = False
    fail_on: Impact | None = None
    level: WcagLevel = "AA"
    widths: tuple[int, ...] = (375, 768, 1280)
    audit_timeout_s: float = DEFAULT_AUDIT_TIMEOUT_S

    def __post_init__(self) -> None:
        if self.fail_on is not None and self.fail_on not in IMPACT_ORDER:
            raise ValueError(f"fail_on must be one of {', '.join(IMPACT_ORDER)}")

    @property
    def enabled(self) -> bool:
        return self.a11y or self.visual

    @classmethod
    def from_flags(cls, a11y: bool = False, visual: bool = False, fail_on: str | None = None) -> RunQualityOptions:
        """Options from command-line style flags (what ``hercules run --a11y --visual`` passes)."""
        return cls(a11y=bool(a11y), visual=bool(visual), fail_on=fail_on or None)  # type: ignore[arg-type]


@dataclass(frozen=True)
class QualityReading:
    """A step to run as a quality check (or why it cannot be)."""

    check: QualityCheck | None = None
    source: Literal["payload", "llm"] = "payload"
    problem: str | None = None


@dataclass(frozen=True)
class QualityOutcome:
    status: StepStatus
    actual: str | None
    ledger: tuple[LedgerEntry, ...] = ()
    evidence: tuple[str, ...] = ()
    pages: tuple[QualityPage, ...] = ()
    error_class: str | None = None


class _ModelForFixes:
    """The interpreter's model as the fix suggester expects it (text, and images when the server takes them)."""

    def __init__(self, port: Any) -> None:
        self._port = port

    @property
    def accepts_images(self) -> bool:
        return bool(getattr(self._port, "accepts_images", False))

    async def complete(self, **kwargs: Any) -> Any:
        return await self._port.complete(**kwargs)


@dataclass
class QualitySession:
    """Per-run state of quality checks: the audited page states and how often each URL was seen."""

    evidence: EvidenceStore
    decider: DecisionEngine
    baselines: Path
    case_id: str
    llm: Any = None
    images: bool = False
    interpreter: QualityInterpreter = field(default_factory=QualityInterpreter)
    options: RunQualityOptions = field(default_factory=RunQualityOptions)
    files_changed: tuple[str, ...] = ()
    project_dir: Path | None = None
    redact: Any = None
    events: EventSink = field(default_factory=NullEventSink)
    _files: dict[str, str] | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------ reading

    def read(self, step: Step) -> QualityReading | None:
        """The step's own ``quality`` payload as a reading, or None."""
        return QualityReading(check=step.quality) if step.quality is not None else None

    async def interpret(self, step: Step) -> QualityReading:
        """Ask the model for the check a step the interpreter classified as a quality step describes."""
        if not self.interpreter.has_llm:
            return QualityReading(problem="this quality step has no structured quality payload and no model is configured to read its text", source="llm")
        check = await self.interpreter.interpret(_text_of(step))
        if check is None:
            return QualityReading(problem="the model could not read this step as an accessibility, visual or layout check; give it a structured quality payload", source="llm")
        return QualityReading(check=check, source="llm")

    # ------------------------------------------------------------------ steps

    async def run_step(self, index: int, page: Any, reading: QualityReading, resolve: Any = None) -> QualityOutcome:
        """Run the step's check on ``page`` and judge it; never raises for page problems."""
        check = reading.check
        if reading.problem is not None or check is None:
            return QualityOutcome(status="error", actual=reading.problem or "nothing to check", error_class="UnreadableStep")
        if page is None:
            return QualityOutcome(status="error", actual="no page is open to check", error_class="NoPage")
        if resolve is not None:
            check = _resolved(check, resolve)
        self.events.emit("action", step=index, action=f"quality_{check.kind}", ok=True)
        try:
            if check.kind == "a11y":
                return await self._a11y_step(index, page, check)
            if check.kind == "layout":
                return await self._layout_step(index, page, check)
            return await self._visual_step(index, page, check)
        except _ScopeMissing as exc:
            return QualityOutcome(status="error", actual=str(exc), error_class="ScopeNotFound")

    async def _a11y_step(self, index: int, page: Any, check: QualityCheck) -> QualityOutcome:
        findings, fixes, not_scanned = await self._accessibility(page, level=check.level, keyboard=check.keyboard)
        findings = [finding for finding in findings if not check.rules or finding.rule in check.rules]
        findings = await _in_scope(page, findings, check.scope)
        fixes = _fixes_for(findings, fixes)
        record = await self._page(index, page, "a11y", "step", findings, fixes, not_scanned=not_scanned, prefix="step-a11y")
        expected = f"no accessibility findings at or above {check.fail_on} (WCAG {check.level}{', rules ' + ', '.join(check.rules) if check.rules else ''})"
        return self._gated(index, "a11y", expected, check.fail_on, record, "accessibility")

    async def _layout_step(self, index: int, page: Any, check: QualityCheck) -> QualityOutcome:
        from testzeus_hercules.quality.audit import audit_layout

        widths = tuple(check.viewports) or self.options.widths
        report = await audit_layout(page, widths=widths, files_changed=self.files_changed, file_contents=self._file_contents())
        findings = await _in_scope(page, list(report.findings), check.scope)
        record = await self._page(index, page, "layout", "step", findings, _fixes_for(findings, report.fixes), prefix="step-layout")
        expected = f"no layout findings at or above {check.fail_on} at {', '.join(f'{width}px' for width in widths)}"
        return self._gated(index, "layout", expected, check.fail_on, record, "layout")

    def _gated(self, index: int, label: str, expected: str, gate: Impact, record: QualityPage, noun: str) -> QualityOutcome:
        failing = [finding for finding in record.findings if at_or_above(finding.impact, gate)]
        if failing:
            actual = f"{len(failing)} {noun} finding(s) at or above {gate}: {_listed(failing, record.fixes)}"
            verdict: Literal["passed", "failed"] = "failed"
        else:
            below = f"; {len(record.findings)} below {gate} reported" if record.findings else ""
            actual = f"no {noun} findings at or above {gate}{below}"
            verdict = "passed"
        entry = self._entry(index, label, expected, actual, verdict)
        status: StepStatus = "failed" if failing else "passed"
        return QualityOutcome(status=status, actual=entry.actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,), error_class="QualityFindings" if failing else None)

    async def _visual_step(self, index: int, page: Any, check: QualityCheck) -> QualityOutcome:
        assert check.baseline is not None
        record = await self._visual(index, page, check.baseline, mask=check.mask, key_elements=check.key_elements, full_page=check.full_page, threshold=check.threshold, source="step")
        visual = record.visual
        expected = f"the page looks like baseline {check.baseline!r}"
        if record.error is not None or visual is None:
            entry = self._entry(index, "visual_baseline", expected, f"error: {record.error or 'no comparison'}", "failed")
            return QualityOutcome(status="error", actual=entry.actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,), error_class="VisualCheckError")
        if visual.created:
            actual = f"not verified: baseline {check.baseline!r} did not exist; it was created from this page, and the next run compares against it"
            entry = self._entry(index, "visual_baseline", expected, actual, "unverified")
            return QualityOutcome(status="unverified", actual=actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,))
        if not _differs(visual):
            actual = f"{visual.diff_ratio:.2%} of compared pixels differ (gate {visual.threshold:.2%}); key element styles unchanged"
            entry = self._entry(index, "visual_baseline", expected, actual, "passed")
            return QualityOutcome(status="passed", actual=actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,))
        change = _describe_change(visual)
        classification = visual.classification
        if classification is None:
            actual = f"not verified: {change}; the difference could not be classified ({visual.note or 'no decider answer'})"
            entry = self._entry(index, "visual_baseline", expected, actual, "unverified")
            return QualityOutcome(status="unverified", actual=actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,))
        reading = f"{classification.label.replace('_', ' ')} ({classification.confidence:.0%}, {classification.engine})"
        if any(at_or_above(finding.impact, check.fail_on) for finding in record.findings):
            actual = f"{change}; judged a {reading}"
            entry = self._entry(index, "visual_baseline", expected, actual, "failed")
            return QualityOutcome(status="failed", actual=actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,), error_class="VisualRegression")
        actual = f"not verified: {change}; judged a {reading}: review it, and delete baseline {check.baseline!r} to accept the new look"
        entry = self._entry(index, "visual_baseline", expected, actual, "unverified")
        return QualityOutcome(status="unverified", actual=actual, ledger=(entry,), evidence=tuple(record.evidence), pages=(record,))

    # ------------------------------------------------------------------ run-level audits

    async def state_key(self, page: Any) -> str | None:
        """What identifies the page's state for audits: URL, title, main headings and open dialogs (hashed)."""
        try:
            text = await page.evaluate(_STATE_JS)
        except Exception as exc:  # the page is navigating or gone: nothing to audit now
            logger.debug("no page state for the quality audit: %s", exc)
            return None
        return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]

    async def audit(self, index: int, page: Any, *, url_seen: int = 1) -> list[QualityPage]:
        """The run's audits of the page after step ``index``; ``url_seen`` counts visits of this URL (for baseline names)."""
        pages: list[QualityPage] = []
        options = self.options
        work: list[tuple[str, Callable[[], Awaitable[QualityPage]]]] = []
        if options.a11y:
            work.append(("a11y", lambda: self._run_a11y(index, page)))
        if options.visual:
            work.append(("visual", lambda: self._visual(index, page, self._auto_baseline(page.url, url_seen), source="run")))
            work.append(("layout", lambda: self._run_layout(index, page)))
        for kind, job in work:
            try:
                pages.append(await asyncio.wait_for(job(), timeout=options.audit_timeout_s))
            except asyncio.TimeoutError:
                pages.append(QualityPage(kind=kind, source="run", step=index, url=self._clean(page.url), error=f"the {kind} audit timed out after {options.audit_timeout_s:g} s"))  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # an audit problem is reported on the page, never fails the run by itself
                logger.warning("quality %s audit failed: %s", kind, exc)
                pages.append(QualityPage(kind=kind, source="run", step=index, url=self._clean(page.url), error=self._clean(f"{type(exc).__name__}: {_first_line(exc)}")))  # type: ignore[arg-type]
        for record in pages:
            self.events.emit("quality", step=index, kind=record.kind, source="run", findings=len(record.findings), error=record.error)
        return pages

    async def _run_a11y(self, index: int, page: Any) -> QualityPage:
        findings, fixes, not_scanned = await self._accessibility(page, level=self.options.level, keyboard=False)
        return await self._page(index, page, "a11y", "run", findings, fixes, not_scanned=not_scanned, prefix="run-a11y")

    async def _run_layout(self, index: int, page: Any) -> QualityPage:
        from testzeus_hercules.quality.audit import audit_layout

        report = await audit_layout(page, widths=self.options.widths, files_changed=self.files_changed, file_contents=self._file_contents())
        return await self._page(index, page, "layout", "run", list(report.findings), list(report.fixes), prefix="run-layout")

    def _auto_baseline(self, url: str, seen: int) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        slug = _SLUG.sub("-", (parts.path or "/").lower()).strip("-") or "home"
        digest = hashlib.sha256(f"{parts.netloc}{parts.path}?{parts.query}#{seen}".encode("utf-8")).hexdigest()[:8]
        return f"{self.case_id[:40]}.{slug[:40]}.{digest}"

    def section(self, pages: Sequence[QualityPage]) -> QualitySection | None:
        """The run's quality section, or None when the run audited nothing and had no quality steps."""
        if not pages and not self.options.enabled:
            return None
        return QualitySection(a11y=self.options.a11y, visual=self.options.visual, fail_on=self.options.fail_on, pages=list(pages))

    # ------------------------------------------------------------------ checks

    async def _accessibility(self, page: Any, *, level: str, keyboard: bool) -> tuple[list[Finding], list[FixSuggestion], list[str]]:
        from testzeus_hercules.quality.audit import audit_accessibility
        from testzeus_hercules.quality.axe import AAA_TAGS, DEFAULT_TAGS

        tags = (*DEFAULT_TAGS, *AAA_TAGS) if level == "AAA" else DEFAULT_TAGS
        llm = _ModelForFixes(self.llm) if self.llm is not None else None
        report = await audit_accessibility(page, tags=tags, level=level, keyboard=keyboard, llm=llm, files_changed=self.files_changed, file_contents=self._file_contents())  # type: ignore[arg-type]
        allowed = _LEVELS.get(level, _LEVELS["AA"])
        findings = [finding for finding in report.findings if finding.level in allowed]
        return findings, _fixes_for(findings, report.fixes), list(report.not_scanned)

    async def _visual(
        self,
        index: int,
        page: Any,
        name: str,
        *,
        mask: Sequence[str] = (),
        key_elements: Sequence[str] = (),
        full_page: bool = False,
        threshold: float | None = None,
        source: Literal["step", "run"],
    ) -> QualityPage:
        from testzeus_hercules.quality.visual import DEFAULT_THRESHOLD, capture_baseline, compare, load_baseline

        gate = DEFAULT_THRESHOLD if threshold is None else threshold
        url, title = self._clean(page.url), await _title(page)
        if not (self.baselines / f"{name}.json").is_file():
            await capture_baseline(page, name, baseline_dir=self.baselines, mask=list(mask), key_elements=list(key_elements), full_page=full_page)
            visual = QualityVisual(baseline=name, created=True, threshold=gate)
            return QualityPage(kind="visual", source=source, step=index, url=url, title=title, visual=visual)
        baseline = load_baseline(self.baselines, name)
        out_dir = self._quality_dir(index)
        diff = await compare(page, baseline, baseline_dir=self.baselines, out_dir=out_dir, threshold=gate, mask=list(mask))
        images = {key: self.evidence.uri(index, f"quality/{Path(path).name}") for key, path in diff.evidence.items()}
        visual = QualityVisual(
            baseline=name,
            diff_ratio=round(diff.diff_ratio, 6),
            threshold=gate,
            changed=diff.changed,
            regions=diff.regions,
            style_deltas=diff.style_deltas,
            missing=diff.missing,
            images=images,
        )
        findings = list(diff.findings)
        if _differs(visual):
            classification, note = await self._classify(index, diff.evidence, diff.style_deltas, url)
            visual = visual.model_copy(update={"classification": classification, "note": note})
            if classification is not None:
                impact = DIFF_IMPACT.get(classification.label, "moderate")
                findings = [_classified(finding, impact, classification) for finding in findings]
        evidence = [*images.values(), self._save_json(index, f"{source}-visual-{name}.json", {"visual": visual.model_dump(mode="json"), "findings": [f.model_dump(mode="json") for f in findings]})]
        return QualityPage(kind="visual", source=source, step=index, url=url, title=title, findings=findings, fixes=_fixes_for(findings, diff.fixes), visual=visual, evidence=evidence)

    async def _classify(self, index: int, paths: Mapping[str, str], deltas: Sequence[Any], url: str) -> tuple[DiffClassification | None, str | None]:
        """The decider's reading of a diff; (None, why) when it cannot see screenshots or does not answer."""
        from testzeus_hercules.quality.visual import classify_diff

        if not self.images:
            return None, "the decider cannot see screenshots"
        if not all(key in paths for key in ("before", "after", "diff")):
            return None, "no diff images were written"
        try:
            pngs = {key: Path(paths[key]).read_bytes() for key in ("before", "after", "diff")}
            classification = await classify_diff(self.decider, pngs["before"], pngs["after"], pngs["diff"], deltas=deltas, context=f"page {url}")
        except Exception as exc:  # unclassified: the step stays unverified
            logger.warning("visual diff classification failed: %s", exc)
            return None, f"the decider did not answer ({type(exc).__name__})"
        record = {
            "purpose": "visual_diff",
            "engine": classification.engine,
            "choice": classification.label,
            "confidence": round(classification.confidence, 4),
            "probabilities": classification.probabilities,
        }
        self.evidence.append_decision(index, record)
        self.events.emit("decision", step=index, purpose="visual_diff", engine=classification.engine, choice=classification.label, confidence=classification.confidence)
        return classification, None

    async def _page(
        self,
        index: int,
        page: Any,
        kind: Literal["a11y", "layout"],
        source: Literal["step", "run"],
        findings: list[Finding],
        fixes: list[FixSuggestion],
        *,
        prefix: str,
        not_scanned: Sequence[str] = (),
    ) -> QualityPage:
        """The audit record of one page, with its findings JSON and an annotated screenshot as evidence."""
        url, title = self._clean(page.url), await _title(page)
        evidence = [
            self._save_json(index, f"{prefix}.json", {"kind": kind, "url": url, "findings": [f.model_dump(mode="json") for f in findings], "fixes": [x.model_dump(mode="json") for x in fixes]})
        ]
        if findings:
            uri = await self._annotated(index, page, findings, prefix)
            if uri is not None:
                evidence.append(uri)
        return QualityPage(kind=kind, source=source, step=index, url=url, title=title, findings=findings, fixes=fixes, not_scanned=list(not_scanned), evidence=evidence)

    async def _annotated(self, index: int, page: Any, findings: Sequence[Finding], prefix: str) -> str | None:
        from testzeus_hercules.quality.annotate import annotate

        try:
            from testzeus_hercules.perceive.screen_guard import capture

            png = await capture(page, type="png", animations="disabled", caret="hide")
        except Exception as exc:  # evidence is best effort
            logger.info("no annotated screenshot: %s", exc)
            return None
        image, _ = annotate(png, findings)
        name = f"quality/{prefix}.annotated.png"
        (self._quality_dir(index) / f"{prefix}.annotated.png").write_bytes(image)
        return self.evidence.uri(index, name)

    # ------------------------------------------------------------------ helpers

    def _save_json(self, index: int, name: str, record: Mapping[str, Any]) -> str:
        self._quality_dir(index)
        return self.evidence.save_json(index, f"quality/{name}", record)

    def _quality_dir(self, index: int) -> Path:
        path = self.evidence.step_dir(index) / "quality"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _entry(self, index: int, check: str, expected: str, actual: str, verdict: Literal["passed", "failed", "unverified"]) -> LedgerEntry:
        return LedgerEntry(step=index, check=check, expected=self._clean(expected), actual=self._clean(actual), verdict=verdict)

    def _clean(self, text: str) -> str:
        return self.redact(text) if callable(self.redact) else text

    def _file_contents(self) -> dict[str, str] | None:
        """The changed files' text (for ``likely_file``), read once from inside the project."""
        if self._files is not None or not self.files_changed or self.project_dir is None:
            return self._files
        root = self.project_dir.resolve()
        contents: dict[str, str] = {}
        for name in self.files_changed[:_MAX_FILES]:
            path = (root / name).resolve()
            try:
                if path.is_relative_to(root) and path.is_file() and path.stat().st_size <= _MAX_FILE_BYTES:
                    contents[name] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        self._files = contents
        return contents


class _ScopeMissing(LookupError):
    """The check's scope selector matches nothing on the page."""


async def _in_scope(page: Any, findings: list[Finding], scope: str | None) -> list[Finding]:
    """Findings whose element (or the frame or shadow host holding it) is inside ``scope``."""
    if scope is None:
        return findings
    anchors = [(finding.frame_path[0] if finding.frame_path else finding.selector).split(SELECTOR_JOIN)[0] for finding in findings]
    inside = await page.evaluate(_IN_SCOPE_JS, [anchors, scope])
    if inside is None:
        raise _ScopeMissing(f"the scope {scope!r} matches nothing on the page")
    return [finding for finding, keep in zip(findings, inside) if keep]


def _fixes_for(findings: Sequence[Finding], fixes: Sequence[FixSuggestion]) -> list[FixSuggestion]:
    kept = {finding.id for finding in findings}
    return [fix for fix in fixes if fix.finding_ref in kept]


def _classified(finding: Finding, impact: Impact, classification: DiffClassification) -> Finding:
    measured = {**(finding.measured or {}), "classification": classification.label, "classification_confidence": round(classification.confidence, 4)}
    return finding.model_copy(update={"impact": impact, "measured": measured})


def _differs(visual: QualityVisual) -> bool:
    return visual.changed or bool(visual.style_deltas) or bool(visual.missing)


def _describe_change(visual: QualityVisual) -> str:
    parts = [f"{visual.diff_ratio:.2%} of compared pixels differ (gate {visual.threshold:.2%}) in {len(visual.regions)} region(s)"]
    if visual.style_deltas:
        # Styles set on the element first; boxes and sizes are usually their effect.
        ordered = sorted(visual.style_deltas, key=lambda delta: delta.property in _EFFECTS)
        listed = ", ".join(f"{delta.property} {delta.before} -> {delta.after} on {_clip(delta.selector)}" for delta in ordered[:_LISTED])
        more = f" (+{len(visual.style_deltas) - _LISTED} more)" if len(visual.style_deltas) > _LISTED else ""
        parts.append(f"style changes: {listed}{more}")
    if visual.missing:
        parts.append(f"missing: {', '.join(visual.missing[:_LISTED])}")
    return "; ".join(parts)


def _listed(findings: Sequence[Finding], fixes: Sequence[FixSuggestion]) -> str:
    ordered = sorted(findings, key=lambda finding: IMPACT_ORDER.index(finding.impact))
    distinct = list({finding.selector: finding for finding in reversed(ordered)}.values())[::-1]  # one per element, most severe first
    items = []
    for finding in distinct[:_LISTED]:
        fix = next((item for item in fixes if item.finding_ref == finding.id and item.verified), None) or next((item for item in fixes if item.finding_ref == finding.id), None)
        text = f"{finding.rule} ({finding.impact}) on {_clip(finding.selector)}"
        if fix is not None:
            text += f" - fix {fix.property_or_attr}: {_clip(fix.suggested)} ({'verified' if fix.verified else 'not verified'})"
        items.append(text)
    more = f"; +{len(findings) - len(items)} more" if len(findings) > len(items) else ""
    return "; ".join(items) + more


def _resolved(check: QualityCheck, resolve: Any) -> QualityCheck:
    """The check with ``{{placeholders}}`` in its selectors and names substituted."""
    data = check.model_dump(exclude_unset=True)
    for key in ("scope", "baseline"):
        if isinstance(data.get(key), str):
            data[key] = resolve(data[key])
    for key in ("mask", "key_elements", "rules"):
        if data.get(key):
            data[key] = [resolve(item) for item in data[key]]
    return QualityCheck.model_validate(data)


def _text_of(step: Step) -> str:
    if step.action.strip().lower() == VERIFY_ACTION and step.expected:
        return step.expected
    return f"{step.action}\nExpected: {step.expected}" if step.expected else step.action


async def _title(page: Any) -> str | None:
    try:
        title = await page.title()
    except Exception:  # navigating: no title
        return None
    return str(title)[:200] or None


def _clip(text: str) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _CLIP else flat[: _CLIP - 1] + "…"


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0][:300] if lines else ""
