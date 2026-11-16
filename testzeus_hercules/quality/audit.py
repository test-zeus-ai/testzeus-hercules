"""One-call audits that combine the runners into a :class:`QualityReport`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from testzeus_hercules.perceive.model import Snapshot
from testzeus_hercules.quality.axe import DEFAULT_TAGS, run_axe
from testzeus_hercules.quality.checks import check_contrast, check_images, check_keyboard, check_names, check_target_size
from testzeus_hercules.quality.findings import QualityReport
from testzeus_hercules.quality.fixes import suggest_fixes
from testzeus_hercules.quality.frames import list_frames
from testzeus_hercules.quality.layout import DEFAULT_WIDTHS, check_layout

if TYPE_CHECKING:
    from playwright.async_api import Page

    from testzeus_hercules.llm.structured import StructuredLLM

__all__ = ["audit_accessibility", "audit_layout"]


async def audit_accessibility(
    page: "Page",
    *,
    snapshot: Snapshot | None = None,
    tags: Sequence[str] = DEFAULT_TAGS,
    level: str = "AA",
    keyboard: bool = True,
    llm: "StructuredLLM | None" = None,
    files_changed: Sequence[str] = (),
    file_contents: Mapping[str, str] | None = None,
    verify: bool = True,
) -> QualityReport:
    """axe-core plus the deterministic checks, then a verified fix for every finding that has one."""
    frames = await list_frames(page)
    axe = await run_axe(page, tags=tags, snapshot=snapshot, frames=frames)
    findings = [
        *axe.findings,
        *await check_contrast(page, snapshot=snapshot, level=level, frames=frames),
        *await check_names(page, snapshot=snapshot, frames=frames),
        *await check_target_size(page, snapshot=snapshot, frames=frames),
        *await check_images(page, snapshot=snapshot, frames=frames),
    ]
    if keyboard:
        findings.extend((await check_keyboard(page, snapshot=snapshot, frames=frames)).findings)
    fixes = await suggest_fixes(page, findings, llm=llm, files_changed=files_changed, file_contents=file_contents, verify=verify)
    return QualityReport(url=page.url, findings=findings, fixes=fixes, not_scanned=axe.not_scanned, engine={"axe-core": axe.version})


async def audit_layout(
    page: "Page",
    *,
    widths: Sequence[int] = DEFAULT_WIDTHS,
    snapshot: Snapshot | None = None,
    files_changed: Sequence[str] = (),
    file_contents: Mapping[str, str] | None = None,
) -> QualityReport:
    """Layout checks at each width, flattened into one report (each finding records its width)."""
    results = await check_layout(page, widths=widths, snapshot=snapshot, files_changed=files_changed, file_contents=file_contents)
    return QualityReport(url=page.url, findings=[f for r in results for f in r.findings], fixes=[x for r in results for x in r.fixes])
