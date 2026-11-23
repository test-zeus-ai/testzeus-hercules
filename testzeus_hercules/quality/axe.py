"""Bundled axe-core runner: inject the pinned, unmodified ``axe.min.js`` into each frame and map violations.

axe-core ships inside the package (``quality/vendor/axe``, MPL-2.0, see its ``VERSION`` and ``LICENSE``);
nothing is fetched at runtime. Each same-origin frame gets its own copy and its own run, so findings
carry the frame path; cross-origin frames are reported as not scanned. Open shadow roots are scanned
by axe itself.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from testzeus_hercules.perceive.model import Snapshot
from testzeus_hercules.quality.checks import make_finding
from testzeus_hercules.quality.findings import Finding, Level
from testzeus_hercules.quality.frames import FrameInfo, list_frames, probe_frame

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

logger = logging.getLogger(__name__)

__all__ = ["AXE_DIR", "AxeResult", "DEFAULT_TAGS", "axe_info", "axe_source", "level_of", "run_axe", "wcag_of"]

AXE_DIR = "vendor/axe"
DEFAULT_TAGS: tuple[str, ...] = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")
AAA_TAGS: tuple[str, ...] = ("wcag2aaa", "wcag21aaa", "wcag22aaa")
_SC_TAG = re.compile(r"^wcag(\d)(\d)(\d{1,2})$")
_LEVEL_TAG = re.compile(r"^wcag2\d?(a{1,3})$")
_IMPACTS = {"critical", "serious", "moderate", "minor"}
_MAX_MESSAGE = 400

_RUN_JS = """
async ([tags]) => {
  const options = { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"], iframes: false, reporter: "v2" };
  const result = await window.axe.run(document, options);
  return {
    version: window.axe.version,
    violations: result.violations.map((v) => ({
      id: v.id, impact: v.impact, tags: v.tags, help: v.help, helpUrl: v.helpUrl,
      nodes: v.nodes.map((n) => ({
        target: n.target, impact: n.impact, html: n.html, summary: n.failureSummary,
        data: [...(n.any || []), ...(n.all || []), ...(n.none || [])].map((c) => c.data).find((d) => d && typeof d === "object") || null,
      })),
    })),
  };
}
"""


@lru_cache(maxsize=1)
def axe_source() -> str:
    """The bundled ``axe.min.js``, read from package data."""
    return resources.files("testzeus_hercules.quality").joinpath(AXE_DIR, "axe.min.js").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def axe_info() -> dict[str, str]:
    """Pinned version and checksum from the bundled ``VERSION`` file, plus the checksum of the file on disk."""
    text = resources.files("testzeus_hercules.quality").joinpath(AXE_DIR, "VERSION").read_text(encoding="utf-8")
    info = {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in text.splitlines()) if value}
    info["sha256_actual"] = hashlib.sha256(axe_source().encode("utf-8")).hexdigest()
    return info


def wcag_of(tags: Sequence[str]) -> str | None:
    """``wcag143`` -> ``1.4.3`` (the first success-criterion tag)."""
    for tag in tags:
        match = _SC_TAG.match(tag)
        if match:
            return ".".join(match.groups())
    return None


def level_of(tags: Sequence[str]) -> Level:
    """Conformance level from axe's ``wcag2a`` / ``wcag21aa`` / ``wcag2aaa`` tags; ``none`` for best practices."""
    levels = {match.group(1) for tag in tags if (match := _LEVEL_TAG.match(tag))}
    for level in ("a", "aa", "aaa"):
        if level in levels:
            return level.upper()  # type: ignore[return-value]
    return "none"


@dataclass
class AxeResult:
    """axe findings for a page and which frames were (not) scanned."""

    version: str
    findings: list[Finding] = field(default_factory=list)
    scanned: list[str] = field(default_factory=list)
    not_scanned: list[str] = field(default_factory=list)


async def _inject(frame: "Frame") -> None:
    from playwright.async_api import Error as PlaywrightError

    if await frame.evaluate("() => typeof window.axe === 'object' && !!window.axe.run"):
        return
    try:
        await frame.add_script_tag(content=axe_source())
    except PlaywrightError as exc:
        logger.debug("script tag injection failed (%s); evaluating instead", exc)
    if not await frame.evaluate("() => typeof window.axe === 'object' && !!window.axe.run"):
        # A Content-Security-Policy can block inline script tags; evaluation from the driver is not subject to it.
        await frame.evaluate(axe_source() + "\n;void 0")


def _message(violation: dict[str, Any], node: dict[str, Any]) -> str:
    summary = " ".join((node.get("summary") or "").split())
    text = f"{violation['help']}. {summary}" if summary else violation["help"]
    return text[:_MAX_MESSAGE]


async def _scan(info: FrameInfo, tags: Sequence[str], snapshot: Snapshot | None) -> tuple[str, list[Finding]]:
    await _inject(info.frame)
    result = await info.frame.evaluate(_RUN_JS, [list(tags)])
    targets = [node["target"][-1] if node["target"] else "" for v in result["violations"] for node in v["nodes"]]
    resolved = iter(await probe_frame(info, "resolve", targets=targets))
    findings = []
    for violation in result["violations"]:
        for node in violation["nodes"]:
            rec = next(resolved)
            if rec is None:
                logger.debug("axe target %r not found again", node["target"])
                continue
            impact = node.get("impact") or violation.get("impact") or "moderate"
            measured = {"axe_rule": violation["id"], "html": node["html"][:300], **({"data": node["data"]} if node.get("data") else {})}
            findings.append(
                make_finding(
                    info,
                    rec,
                    rule=violation["id"],
                    wcag=wcag_of(violation["tags"]),
                    level=level_of(violation["tags"]),
                    impact=impact if impact in _IMPACTS else "moderate",  # type: ignore[arg-type]
                    message=_message(violation, node),
                    measured=measured,
                    snapshot=snapshot,
                    source="axe",
                    help_url=violation.get("helpUrl"),
                )
            )
    return str(result["version"]), findings


async def run_axe(page: "Page", *, tags: Sequence[str] = DEFAULT_TAGS, snapshot: Snapshot | None = None, frames: Sequence[FrameInfo] | None = None) -> AxeResult:
    """Run the bundled axe-core in every same-origin frame with the given rule tags (WCAG 2.x A/AA by default)."""
    found = list(frames) if frames is not None else await list_frames(page)
    result = AxeResult(version=axe_info().get("version", ""))
    for info in found:
        if not info.same_origin:
            result.not_scanned.append(info.url)
            continue
        version, findings = await _scan(info, tags, snapshot)
        result.version = version
        result.findings.extend(findings)
        result.scanned.append(info.label or "top")
    return result
