"""Example cases and environments shipped with Hercules, written into a project when the daemon starts.

The examples (``cases/Pre-Seeded/*.feature`` and the ``envs/*.json`` they name) run against
public demo sites, so a new project has something to run at once: two browser cases on
the RoboForm test form and two API cases on Restful Booker.

Each example is written once: a file that already exists is left as it is, and
``.hercules/seeded.json`` records every example the project has been offered, so one the
user edits keeps their edits and one they delete stays deleted. A later Hercules version
that ships a new example adds only that one. ``HERCULES_SEED_EXAMPLES=0`` turns seeding off.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from testzeus_hercules.kernel.paths import HerculesPaths

__all__ = ["MARKER", "SEED_ENV", "seed_examples", "shipped_examples"]

MARKER = "seeded.json"
SEED_ENV = "HERCULES_SEED_EXAMPLES"
_KINDS = {"cases": (".feature", ".json"), "envs": (".json",)}


def _walk(folder: Traversable, prefix: str) -> list[tuple[str, Traversable]]:
    found: list[tuple[str, Traversable]] = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        name = f"{prefix}/{entry.name}"
        if entry.is_dir():
            found.extend(_walk(entry, name))
        elif entry.is_file():
            found.append((name, entry))
    return found


def shipped_examples() -> dict[str, bytes]:
    """The shipped examples by their path under ``.hercules`` (``cases/Pre-Seeded/x.feature``, ``envs/y.json``)."""
    root = resources.files(__name__)
    examples: dict[str, bytes] = {}
    for kind, suffixes in _KINDS.items():
        folder = root / kind
        if not folder.is_dir():
            continue
        for name, entry in _walk(folder, kind):
            if name.endswith(suffixes):
                examples[name] = entry.read_bytes()
    return examples


def _offered(marker: Path) -> set[str]:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names = data.get("offered") if isinstance(data, Mapping) else None
    return {name for name in names if isinstance(name, str)} if isinstance(names, list) else set()


def _target(paths: HerculesPaths, name: str) -> Path:
    kind, _, rest = name.partition("/")
    return (paths.cases_dir if kind == "cases" else paths.envs_dir) / rest


def seed_examples(paths: HerculesPaths, *, env: Mapping[str, str] | None = None) -> list[str]:
    """Write the examples this project has not been offered yet; returns the paths written (relative to ``.hercules``)."""
    environ = os.environ if env is None else env
    if environ.get(SEED_ENV, "").strip().lower() in ("0", "false", "no", "off"):
        return []
    marker = paths.project_hercules_dir / MARKER
    if marker.is_symlink():
        return []
    offered = _offered(marker)
    examples = shipped_examples()
    written: list[str] = []
    for name, content in examples.items():
        if name in offered:
            continue
        target = _target(paths, name)
        try:
            if not target.exists() and not target.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "xb") as handle:  # never replaces a file that appeared meanwhile
                    handle.write(content)
                written.append(name)
        except OSError:
            continue  # tried again on the next start
        offered.add(name)
    if offered != _offered(marker):
        try:
            marker.write_text(json.dumps({"offered": sorted(offered)}, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
    return written
