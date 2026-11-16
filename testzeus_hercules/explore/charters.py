"""Charters: code lists the legal ones from what recon saw; a model chooses which runs next.

A charter is one sentence with a timebox: an area of the app (a route), the levers to use there, maybe a persona.
Code enumerates the charters the mission and the app model allow (and the dig charters that follow a promising clue);
when an explorer is free, a model picks the next one (purpose ``explore_charter``). Charters a person adds or pins
come first; a vetoed charter never runs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from testzeus_hercules.decide.contract import ChoiceQuestion, DecisionRequest
from testzeus_hercules.explore.missions import Mission
from testzeus_hercules.explore.models import Charter, Clue

__all__ = ["CHARTER_PURPOSE", "candidate_charters", "choose_charter", "dig_charter"]

CHARTER_PURPOSE = "explore_charter"
_MAX_OFFERED = 20
_LEVER_WORDS = {
    "inputs": "unusual inputs (empty, long, negative, emoji, quotes)",
    "order": "out-of-order moves (back, reload, double submit)",
    "conditions": "bad conditions (narrow screen, offline moments)",
    "time": "timing (waiting, expired sessions)",
    "people": "how different people use it (keyboard only, impatient, confused)",
}
_READ_ONLY_LEVERS = frozenset({"conditions", "time", "people"})


def _id(*parts: str) -> str:
    return "ch-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:8]


def candidate_charters(states: Mapping[str, Mapping[str, Any]], mission: Mission, levers: Sequence[str], *, changed: Sequence[str] = (), personas: Sequence[str] = ()) -> list[Charter]:
    """Every charter the mission allows over the routes recon saw (routes with something to push on first)."""
    routes: dict[str, set[str]] = {}
    for state in states.values():
        route = str(state.get("route") or "/")
        routes.setdefault(route, set()).update(str(name) for name in state.get("elements") or ())
    chosen = [lever for lever in mission.levers if lever in levers] or list(levers)
    if changed:
        wanted = [route for route in routes if any(part.strip("/") and part.strip("/") in route for part in changed)]
        if wanted:
            routes = {route: routes[route] for route in wanted}
    charters: list[Charter] = []
    for route, elements in sorted(routes.items(), key=lambda item: -len(item[1])):
        for lever in chosen:
            weight = mission.lever_weights.get(lever, 1.0)
            priority = min(1.0, 0.3 + 0.05 * min(len(elements), 10) + 0.1 * (weight - 1))
            charters.append(
                Charter(
                    id=_id(route, lever, mission.persona or ""),
                    title=f"Explore {route} with {_LEVER_WORDS.get(lever, lever)}",
                    area=route,
                    levers=[lever],  # type: ignore[list-item]
                    persona=mission.persona,
                    timebox_s=mission.timebox_s,
                    priority=round(priority, 2),
                    writes=lever not in _READ_ONLY_LEVERS,
                )
            )
        for persona in personas:
            charters.append(
                Charter(
                    id=_id(route, "people", persona),
                    title=f"Use {route} as a {persona} user would",
                    area=route,
                    levers=["people", "inputs", "order"],
                    persona=persona,
                    timebox_s=mission.timebox_s,
                    priority=0.5,
                )
            )
    return charters


def dig_charter(clue: Clue, lever: str, timebox_s: int = 180) -> Charter:
    """A narrow, high-priority charter that follows a promising clue."""
    return Charter(
        id=_id("dig", clue.signature),
        title=f"Dig into “{clue.title[:120]}”: what else breaks around it",
        kind="dig",
        area=clue.route,
        levers=[lever] if lever else [],  # type: ignore[list-item]
        timebox_s=timebox_s,
        priority=0.9,
        dig_of=clue.id,
    )


async def choose_charter(decider: Any, queued: Sequence[Charter], *, mission: str, covered: Mapping[str, int], running: Sequence[Charter]) -> Charter | None:
    """The next charter: a pinned one first, else the model's pick among the highest-priority queued ones."""
    if not queued:
        return None
    pinned = [charter for charter in queued if charter.pinned]
    if pinned:
        return pinned[0]
    offered = sorted(queued, key=lambda charter: (-charter.priority, charter.origin != "person"))[:_MAX_OFFERED]
    if decider is None or len(offered) == 1:
        return offered[0]
    options: dict[str, Any] = {
        f"c{index}": f"{charter.title} (area {charter.area}, priority {charter.priority}, already covered there: {covered.get(charter.area, 0)} moves)"
        for index, charter in enumerate(offered, start=1)
    }
    state = {
        "task": "Pick the charter an exploratory tester should run next for the mission: the most risk not covered yet.",
        "mission": mission[:800],
        "running": [charter.title for charter in running],
    }
    try:
        decision = await decider.decide(DecisionRequest(state=state, questions={"charter": ChoiceQuestion(instructions="Which charter next?", criteria=options)}), purpose=CHARTER_PURPOSE)
        picked = decision.choice("charter").choice
        index = int(picked[1:]) - 1 if picked.startswith("c") and picked[1:].isdigit() else 0
    except Exception:
        index = 0
    return offered[min(max(index, 0), len(offered) - 1)]
