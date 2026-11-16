"""Frames, element paths and the in-page probe shared by every quality check.

Elements are identified the way perception identifies them: a chain of iframe paths from the top
frame down, then the element's own path (one CSS selector per shadow tree). That makes every finding
resolvable to a Playwright locator and matchable to a Snapshot ``md``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from testzeus_hercules.perceive.model import BBox, ElementPath, Snapshot
from testzeus_hercules.perceive.snapshot import walker_source
from testzeus_hercules.quality.findings import SELECTOR_JOIN, selector_of

if TYPE_CHECKING:
    from playwright.async_api import Frame, Locator, Page

logger = logging.getLogger(__name__)

__all__ = ["FrameInfo", "bbox_of", "list_frames", "locator_for", "md_for", "probe_element", "probe_frame", "probe_source"]

FramePath = tuple[tuple[str, ...], ...]


@lru_cache(maxsize=1)
def probe_source() -> str:
    """The probe function with the perception walker bound in, ready for ``evaluate``."""
    probe = resources.files("testzeus_hercules.quality").joinpath("probe.js").read_text(encoding="utf-8")
    return "(first, second) => {\nconst walker = " + walker_source().strip().rstrip(";") + ";\nconst probe = " + probe.strip().rstrip(";") + ";\nreturn probe(first, second, walker);\n}"


@dataclass(frozen=True)
class FrameInfo:
    """One frame of the page: how to reach it and where its content sits in the top viewport."""

    frame: "Frame"
    chain: FramePath
    label: str | None
    offset: tuple[float, float]
    url: str
    same_origin: bool

    @property
    def frame_path(self) -> list[str]:
        return [selector_of(segments) for segments in self.chain]


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _inherits_origin(url: str) -> bool:
    return url in ("", "about:blank", "about:srcdoc")


async def list_frames(page: "Page") -> list[FrameInfo]:
    """Every attached frame, top first, with its iframe path chain and content offset."""
    from playwright.async_api import Error as PlaywrightError

    top_origin = _origin(page.url)
    out: list[FrameInfo] = [FrameInfo(page.main_frame, (), None, (0.0, 0.0), page.url, True)]

    async def visit(parent: FrameInfo) -> None:
        for child in parent.frame.child_frames:
            if child.is_detached():
                continue
            try:
                handle = await child.frame_element()
                described = await handle.evaluate(probe_source(), {"op": "describe"})
                box = await handle.evaluate(
                    "(f) => { const r = f.getBoundingClientRect(); const s = getComputedStyle(f);"
                    " return [r.left + f.clientLeft + parseFloat(s.paddingLeft), r.top + f.clientTop + parseFloat(s.paddingTop)]; }"
                )
                label = await handle.evaluate("(f) => f.getAttribute('title') || f.getAttribute('name') || f.id || null")
                await handle.dispose()
            except PlaywrightError as exc:
                logger.debug("skipping frame %s: %s", child.url, exc)
                continue
            same = parent.same_origin and (_inherits_origin(child.url) or _origin(child.url) == top_origin)
            info = FrameInfo(
                frame=child,
                chain=parent.chain + (tuple(described["path"]),),
                label=label or f"frame {len(out)}",
                offset=(parent.offset[0] + box[0], parent.offset[1] + box[1]),
                url=child.url,
                same_origin=same,
            )
            out.append(info)
            if same:
                await visit(info)

    await visit(out[0])
    return out


async def probe_frame(info: FrameInfo, op: str, **args: Any) -> Any:
    """Run a whole-frame probe op."""
    return await info.frame.evaluate(probe_source(), {"op": op, **args})


async def probe_element(locator: "Locator", op: str, **args: Any) -> Any:
    """Run a single-element probe op on the first element the locator matches."""
    return await locator.first.evaluate(probe_source(), {"op": op, **args})


def _chain(scope: Any, segments: Sequence[str]) -> Any:
    locator = scope.locator(segments[0])
    for segment in segments[1:]:
        locator = locator.locator(segment)
    return locator


def locator_for(page: "Page", frame_path: Sequence[str], selector: str) -> "Locator":
    """A locator for an element given its finding ``frame_path`` and ``selector``."""
    scope: Any = page
    for frame_selector in frame_path:
        scope = _chain(scope, frame_selector.split(SELECTOR_JOIN)).first.content_frame
    return _chain(scope, selector.split(SELECTOR_JOIN)).first  # type: ignore[no-any-return]


def bbox_of(rect: Sequence[float], offset: tuple[float, float]) -> BBox:
    return BBox(rect[0] + offset[0], rect[1] + offset[1], rect[2], rect[3])


def md_for(snapshot: Snapshot | None, chain: FramePath, path: Sequence[str]) -> int | None:
    """The snapshot ``md`` of the element at this path, if perception marked it."""
    if snapshot is None:
        return None
    wanted = ElementPath(frames=tuple(tuple(c) for c in chain), segments=tuple(path))
    for md, found in snapshot.paths.items():
        if found == wanted:
            return md
    return None
