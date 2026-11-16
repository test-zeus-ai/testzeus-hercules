"""A JUnit XML report of one run (``junit.xml`` in the run directory), from its :class:`RunResult`.

One ``testsuite`` per run, one ``testcase`` per step: a failed or blocked step is a
``failure``, an error an ``error``, a skipped step ``skipped``, and an ``unverified``
step a ``failure`` too (a run passes only when every step passed). A security step's
failure lists its failing findings; the run's security counts are suite properties and its
scans' summaries are in ``system-out``. Written with the standard library only; text is
already redacted in the result.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from testzeus_hercules.cases.models import RunResult

__all__ = ["JUNIT_FILE", "write_junit"]

JUNIT_FILE = "junit.xml"
_FAILURES = frozenset({"failed", "blocked", "unverified"})
# Characters XML 1.0 cannot hold (control characters but tab, newline and return; surrogates; U+FFFE and U+FFFF):
# a page's text may carry them into a step's result, and one would make the whole file unreadable.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml(text: str) -> str:
    return _NOT_XML.sub("\ufffd", text)


def write_junit(result: RunResult, run_dir: Path) -> Path:
    """Write ``junit.xml`` for ``result`` into ``run_dir``; returns its path."""
    steps = result.steps
    suite = ElementTree.Element(
        "testsuite",
        name=_xml(result.case_id),
        tests=str(len(steps)),
        failures=str(sum(step.status in _FAILURES for step in steps)),
        errors=str(sum(step.status == "error" for step in steps) + (1 if result.status == "error" and not any(step.status == "error" for step in steps) else 0)),
        skipped=str(sum(step.status == "skipped" for step in steps)),
        time=f"{result.duration_s:.3f}",
    )
    properties = ElementTree.SubElement(suite, "properties")
    for name, value in (("run_id", result.run_id), ("status", result.status), ("mode", result.engines.mode or ""), ("decider", result.engines.decider or "")):
        ElementTree.SubElement(properties, "property", name=name, value=_xml(str(value)))
    if result.security is not None:
        for severity, count in result.security.counts.model_dump().items():
            ElementTree.SubElement(properties, "property", name=f"security_{severity}", value=str(count))
    for step in steps:
        case = ElementTree.SubElement(suite, "testcase", classname=_xml(result.case_id), name=_xml(f"step {step.index}: {step.action}"[:500]), time=f"{step.duration_ms / 1000:.3f}")
        detail = step.actual or step.status
        if step.status in _FAILURES:
            failure = ElementTree.SubElement(case, "failure", message=_xml(f"{step.status}: {detail}"[:1000]), type=step.status)
            failure.text = _body(step.expected, step.actual)
        elif step.status == "error":
            error = ElementTree.SubElement(case, "error", message=_xml(detail[:1000]), type="error")
            error.text = _body(step.expected, step.actual)
        elif step.status == "skipped":
            ElementTree.SubElement(case, "skipped", message=_xml(detail[:1000]))
    scans = result.security.scans if result.security is not None else []
    if result.hints or result.failure is not None or scans:
        out = ElementTree.SubElement(suite, "system-out")
        lines = list(result.hints)
        if result.failure is not None:
            lines.insert(0, f"failure: {result.failure.category} ({result.failure.confidence:.2f}) {result.failure.summary or ''}".strip())
        for scan in scans:
            lines.append(f"security (step {scan.step}): {scan.summary_line()}; {len(scan.failing())} at or above {scan.fail_on}")
            lines.extend(f"  {finding.line()}" for finding in scan.failing()[:50])
        out.text = _xml("\n".join(lines))
    path = run_dir / JUNIT_FILE
    run_dir.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _body(expected: str | None, actual: str | None) -> str:
    lines = []
    if expected:
        lines.append(f"expected: {expected}")
    if actual:
        lines.append(f"actual: {actual}")
    return _xml("\n".join(lines))
