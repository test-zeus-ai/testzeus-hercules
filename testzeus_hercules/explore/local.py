"""An in-process :class:`~testzeus_hercules.explore.coordinator.ChildRunner`: child runs as asyncio tasks in this process.

The daemon runs explorers and reproducers as worker processes (isolation, live views, cancel). This runner does the
same work in-process, each child with its own browser: for tests, and for ``hercules explore --no-daemon``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.kernel.run_context import CancelToken, RunContext
from testzeus_hercules.kernel.settings import Settings

__all__ = ["LocalRunner"]


class LocalRunner:
    def __init__(self, settings: Settings, make_deps: Callable[[], RunDeps], *, telemetry: Any = None) -> None:
        self.settings = settings
        self.make_deps = make_deps
        self.telemetry = telemetry
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.contexts: dict[str, RunContext] = {}
        self.statuses: dict[str, str] = {}
        self.labels: dict[str, str] = {}

    async def start(self, *, case: TestCase, job: Mapping[str, Any], label: str) -> str:
        from testzeus_hercules.explore.explorer import ExploreJob, run_exploration
        from testzeus_hercules.explore.reproduce import ReproJob, run_reproduction

        ctx = RunContext.create(self.settings, telemetry=self.telemetry)
        ctx = replace(ctx, cancel=CancelToken())
        run_id = ctx.run_id
        self.contexts[run_id] = ctx
        self.statuses[run_id] = "running"
        self.labels[run_id] = label
        data = dict(job)
        kind = data.pop("kind", None)
        deps = self.make_deps()

        async def work() -> None:
            try:
                if kind == "reproduce":
                    result = await run_reproduction(case, ctx, deps, ReproJob.model_validate(data))
                elif kind == "explore":
                    result = await run_exploration(case, ctx, deps, ExploreJob.model_validate(data))
                else:
                    result = await run_case(case, ctx, deps)
                self.statuses[run_id] = "cancelled" if ctx.cancel.is_cancelled else result.status
            except asyncio.CancelledError:
                self.statuses[run_id] = "cancelled"
            except Exception:
                self.statuses[run_id] = "error"

        self.tasks[run_id] = asyncio.ensure_future(work())
        await asyncio.sleep(0)
        return run_id

    def status(self, run_id: str) -> str:
        return self.statuses.get(run_id, "error")

    def run_dir(self, run_id: str) -> Path:
        ctx = self.contexts[run_id]
        return ctx.paths.run_dir(run_id)

    async def cancel(self, run_id: str) -> None:
        ctx = self.contexts.get(run_id)
        if ctx is not None:
            ctx.cancel.cancel("stopped by the session")
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), 20)
            if not task.done():
                task.cancel()
        if self.statuses.get(run_id) not in ("passed", "failed", "error"):
            self.statuses[run_id] = "cancelled"
