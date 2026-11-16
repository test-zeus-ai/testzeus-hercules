"""Run evidence on disk: per-step snapshots and decisions, and ``run.json``.

Layout under ``<project>/.hercules/runs/<run_id>/``::

    run.json  junit.xml  console.jsonl  network.jsonl
    preconditions/plain.jpg  observation.txt  decisions.jsonl
    steps/<n>/plain.jpg  som.jpg  observation.txt  decisions.jsonl  plan.jsonl
    steps/<n>/http.json  sql.json  checks.json
    steps/<n>/perception_error.json   (the page could not be read)

``plan.jsonl`` records how the model read the step (from the case reading made before
the run, from its cache, or on the page), how it read the step's expected outcome, and
every continuation or re-reading on the page.
API and SQL steps write their request and response (``http.json``), their query and
rows (``sql.json``) and the details of their checks (``checks.json``).

Text written here passes through the run's redactor so secret values never
land on disk; JSON is masked value by value before it is encoded (see
:func:`~testzeus_hercules.engine.masking.masked_json`). Evidence is referenced as ``hercules://runs/<run_id>/steps/<n>/<file>``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.decide.questions import SnapshotView
from testzeus_hercules.engine.masking import masked_json

Redactor = Callable[[str], str]


class EvidenceStore:
    def __init__(self, run_dir: Path, run_id: str, redact: Redactor = lambda text: text) -> None:
        self.run_dir = run_dir
        self.run_id = run_id
        self._redact = redact

    def step_dir(self, step: int) -> Path:
        path = self.run_dir / "steps" / str(step)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def uri(self, step: int, name: str) -> str:
        return f"hercules://runs/{self.run_id}/steps/{step}/{name}"

    def save_snapshot(self, step: int, snapshot: SnapshotView) -> tuple[str, ...]:
        """Write the latest snapshot of a step (overwriting earlier attempts); returns evidence URIs."""
        directory = self.step_dir(step)
        written = []
        for name, data in (("plain.jpg", snapshot.plain_jpeg), ("som.jpg", snapshot.som_jpeg)):
            if data:
                (directory / name).write_bytes(data)
                written.append(name)
        (directory / "observation.txt").write_text(self._redact(snapshot.observation_text()), encoding="utf-8")
        written.append("observation.txt")
        return tuple(self.uri(step, name) for name in written)

    def save_preconditions(self, snapshot: SnapshotView, record: Mapping[str, Any]) -> Path:
        """The page the preconditions were judged on and the judgement, under ``preconditions/``."""
        directory = self.run_dir / "preconditions"
        directory.mkdir(parents=True, exist_ok=True)
        if snapshot.plain_jpeg:
            (directory / "plain.jpg").write_bytes(snapshot.plain_jpeg)
        (directory / "observation.txt").write_text(self._redact(snapshot.observation_text()), encoding="utf-8")
        line = masked_json(record, self._redact, sort_keys=True)
        with (directory / "decisions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return directory

    def save_perception_error(self, step: int, record: Mapping[str, Any], header: str, screenshot: bytes | None = None) -> tuple[str, ...]:
        """Evidence of a page that could not be read: what it looked like, an observation holding only the header with the
        error, and the error's details (``perception_error.json``); returns evidence URIs."""
        directory = self.step_dir(step)
        written = []
        if screenshot:
            (directory / "plain.jpg").write_bytes(screenshot)
            written.append("plain.jpg")
        (directory / "observation.txt").write_text(self._redact(header) + "\n", encoding="utf-8")
        text = masked_json(record, self._redact, indent=2)
        (directory / "perception_error.json").write_text(text + "\n", encoding="utf-8")
        written += ["observation.txt", "perception_error.json"]
        return tuple(self.uri(step, name) for name in written)

    def save_screenshot(self, step: int, jpeg: bytes) -> str:
        (self.step_dir(step) / "plain.jpg").write_bytes(jpeg)
        return self.uri(step, "plain.jpg")

    def append_decision(self, step: int, record: Mapping[str, Any]) -> str:
        line = masked_json(record, self._redact, sort_keys=True)
        with (self.step_dir(step) / "decisions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return self.uri(step, "decisions.jsonl")

    def append_plan(self, step: int, record: Mapping[str, Any]) -> str:
        """Record how a model planned (or continued) a step's actions."""
        line = masked_json(record, self._redact, sort_keys=True)
        with (self.step_dir(step) / "plan.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return self.uri(step, "plan.jsonl")

    def save_json(self, step: int, name: str, record: Mapping[str, Any]) -> str:
        """Write one JSON record for a step (an API exchange, a query, check details), redacted; returns its URI."""
        text = masked_json(record, self._redact, indent=2)
        (self.step_dir(step) / name).write_text(text + "\n", encoding="utf-8")
        return self.uri(step, name)

    def write_run(self, result: RunResult) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "run.json"
        path.write_text(masked_json(result, self._redact, indent=2), encoding="utf-8")
        return path
