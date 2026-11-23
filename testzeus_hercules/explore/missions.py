"""Mission presets, the severity rubric and the floors code sets under a model's severity call.

A mission says what a session looks for and how hard; the user may replace its target text (``--mission "..."``)
and keeps every knob explicit. A preset may *suggest* a swarm size; the user's number always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from testzeus_hercules.explore.models import LEVERS, SEVERITIES

__all__ = ["MISSIONS", "Mission", "RUBRIC", "floor_for", "load_mission", "rank", "worse"]


@dataclass(frozen=True)
class Mission:
    name: str
    target: str
    levers: tuple[str, ...] = LEVERS
    severity_floor: str = "P2"
    suggested_workers: int | None = None
    lever_weights: dict[str, float] = field(default_factory=dict)
    timebox_s: int = 300
    persona: str | None = None


MISSIONS: dict[str, Mission] = {
    "p1": Mission("p1", "Find bugs at P1 or worse: anything that loses or corrupts data, takes money wrongly, shows the wrong user's data, or blocks a core flow.", severity_floor="P1"),
    "forms": Mission(
        "forms",
        "Find anything wrong with how forms handle input: refused valid values, accepted invalid ones, errors instead of messages, lost entries.",
        levers=("inputs", "order"),
        lever_weights={"inputs": 2.0},
    ),
    "changed": Mission("changed", "Find bugs in what changed: the routes, components and API paths listed as changed, and what they touch.", lever_weights={"inputs": 1.5, "order": 1.5}),
    "smoke-plus": Mission(
        "smoke-plus", "Walk the happy paths, then push each once: confirm the core flows work and survive one unusual input or order.", levers=("inputs", "order"), suggested_workers=2, timebox_s=180
    ),
    "a11y": Mission(
        "a11y",
        "Find accessibility barriers: what a keyboard-only or screen-reader user cannot reach, read or operate.",
        levers=("people", "conditions"),
        lever_weights={"people": 3.0},
        persona="screen-reader",
    ),
    "conditions": Mission(
        "conditions",
        "Find what breaks under bad conditions: narrow screens, offline moments, slow responses, expired sessions.",
        levers=("conditions", "time"),
        lever_weights={"conditions": 2.0, "time": 2.0},
    ),
}
for _persona in ("impatient", "careful", "confused", "screen-reader"):
    MISSIONS[f"persona:{_persona}"] = Mission(f"persona:{_persona}", f"Use the app as a {_persona} user would and find where it fails them.", levers=("people", "inputs", "order"), persona=_persona)

# The default rubric (a project's own replaces it: .hercules/explore/rubric.yaml with the same keys).
RUBRIC: dict[str, str] = {
    "P0": "Data loss or corruption, a security or privacy breach, or a core flow fully blocked for everyone (an order charged twice, another user's data shown, checkout crashes).",
    "P1": "A core flow wrong or blocked in a common case, with no easy workaround (a valid value refused, a saved change lost, money or counts wrong).",
    "P2": "Wrong in an uncommon case, or there is a workaround (an unusual input gives a server error, a duplicate draft, a stale counter).",
    "P3": "Cosmetic, minor or rare (a misaligned label, a console warning, a control that does nothing visible but has another way).",
}

# What code knows for sure about a kind of signal: the lowest severity it can be. The model may only raise it.
_FLOORS: dict[str, str] = {
    "server_error": "P2",
    "persistence": "P2",
    "page_error": "P3",
    "console_error": "P3",
    "request_failed": "P3",
    "dead_action": "P3",
    "broken_link": "P3",
    "invariant": "P3",
    "a11y": "P3",
    "layout": "P3",
    "judged": "P3",
}


def rank(severity: str) -> int:
    """0 for P0 (worst) to 3 for P3."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def worse(left: str, right: str) -> str:
    return left if rank(left) <= rank(right) else right


def floor_for(oracle: str) -> str:
    return _FLOORS.get(oracle, "P3")


def load_mission(name: str, prompt: str | None, project_dir: Path | None = None) -> Mission:
    """The preset (or a project mission in .hercules/explore/missions/<name>.yaml), with the prompt as its target when given."""
    mission = MISSIONS.get(name)
    if mission is None and project_dir is not None:
        path = project_dir / ".hercules" / "explore" / "missions" / f"{name}.yaml"
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            mission = Mission(
                name, str(data.get("target") or ""), tuple(data.get("levers") or LEVERS), str(data.get("severity_floor") or "P2"), data.get("suggested_workers"), dict(data.get("lever_weights") or {})
            )
    if mission is None:
        if not prompt:
            raise ValueError(f"unknown mission {name!r}: use one of {', '.join(sorted(MISSIONS))}, or give your own with a prompt")
        mission = Mission(name, prompt)
    if prompt:
        mission = Mission(mission.name, prompt, mission.levers, mission.severity_floor, mission.suggested_workers, dict(mission.lever_weights), mission.timebox_s, mission.persona)
    return mission


def load_rubric(project_dir: Path | None) -> dict[str, str]:
    path = project_dir / ".hercules" / "explore" / "rubric.yaml" if project_dir is not None else None
    if path is not None and path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {level: str(data.get(level) or RUBRIC[level]) for level in SEVERITIES}
    return dict(RUBRIC)
