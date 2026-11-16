"""Ports the execution engine depends on: perception and a structured-output LLM.

The engine codes against these protocols so tests can inject fakes, and so the
perception package and the LLM client stay swappable. :class:`DefaultPerceiver`
imports the perception package lazily, on first use.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, TypeVar, runtime_checkable

from pydantic import BaseModel

from testzeus_hercules.decide.questions import SnapshotView

T = TypeVar("T", bound=BaseModel)


class TargetGone(LookupError):
    """The chosen element no longer exists on the page (the snapshot is stale)."""


class PerceptionFailed(RuntimeError):
    """The page could not be read (perception failed): ``error`` is the page's JavaScript error, as it was raised.

    The step that needed the page ends as an ``error`` naming it; the run is not judged a test problem.
    """

    def __init__(self, error: str, *, url: str = "", title: str = "", attempts: Sequence[str] = (), errors: Sequence[Mapping[str, Any]] = (), screenshot: bytes | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.url = url
        self.title = title
        self.attempts = tuple(attempts)
        self.errors = tuple(dict(record) for record in errors)
        self.screenshot = screenshot


@runtime_checkable
class Perceiver(Protocol):
    """Takes Set-of-Mark snapshots of a page and resolves ``md`` ids to locators."""

    async def snapshot(self, page: Any, seq: int, focus_terms: Sequence[str] = ()) -> SnapshotView: ...

    async def resolve(self, page: Any, snapshot: Any, md: int) -> Any: ...


@runtime_checkable
class IntentLLM(Protocol):
    """A model call that returns an instance of ``schema`` (structured output)."""

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T: ...


class DefaultPerceiver:
    """Adapter over :mod:`testzeus_hercules.perceive`, imported on first use.

    Besides snapshots and resolution it records, for exporting a run as code, the locators that find an
    acted-on element uniquely (:meth:`locators`) and how a text check that held is written (:meth:`text_form`).
    A perceiver without these methods (a test double) records neither; the export then says so.
    """

    async def snapshot(self, page: Any, seq: int, focus_terms: Sequence[str] = ()) -> SnapshotView:
        from testzeus_hercules.perceive import PerceptionError, take_snapshot

        try:
            snapshot: SnapshotView = await take_snapshot(page, seq=seq, focus_terms=tuple(focus_terms))
        except PerceptionError as exc:
            raise PerceptionFailed(exc.message, url=exc.url, title=exc.title, attempts=exc.attempts, errors=exc.errors, screenshot=exc.screenshot) from exc
        return snapshot

    def __init__(self) -> None:
        # How the latest resolve found its element (``{"via": "path"}``, or the fallback used after the path failed).
        self.last_resolution: dict[str, Any] | None = None

    async def resolve(self, page: Any, snapshot: Any, md: int) -> Any:
        from testzeus_hercules.perceive import ElementGone, resolve

        report: dict[str, Any] = {}
        self.last_resolution = report
        try:
            return await resolve(page, snapshot, md, report=report)
        except ElementGone as exc:
            report.update(via="none", error=str(exc))
            raise TargetGone(f"element {md} is gone from the page") from exc

    async def locators(self, page: Any, snapshot: Any, md: int, locator: Any) -> tuple[Any, dict[str, Any]]:
        """The element's unique locators on the live page (a ``TargetSpec``) and every candidate tried."""
        from testzeus_hercules.perceive.locators import record_target

        path = (getattr(snapshot, "paths", None) or {}).get(md)
        frames = path.frames if path is not None else ()
        segments = path.segments if path is not None else None
        return await record_target(page, locator, frames=frames, segments=segments)

    async def text_form(self, page: Any, text: str, *, present: bool) -> tuple[str | None, list[Any]]:
        """How a text check that held is written as an assertion that also holds on this page (None: no simple form),
        and the frames (``FrameSpec``) the text is in when it is not in the top document."""
        from testzeus_hercules.perceive.locators import text_form

        return await text_form(page, text, present=present)
