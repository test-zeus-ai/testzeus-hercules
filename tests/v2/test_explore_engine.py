"""Exploration in a real browser against the seeded-bug app (bench/explore/app.py), with scripted models.

An explorer raises a clue for a seeded bug, the reproducer confirms it from a fresh start without a model and writes
a known-bug case, and an in-process session finds seeded bugs end to end. With the bugs off the same moves raise
nothing (the clean-build check).
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import json
import urllib.request
from pathlib import Path
from typing import Any

from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.engine.ports import DefaultPerceiver
from testzeus_hercules.engine.run_graph import RunDeps
from testzeus_hercules.explore.coordinator import Session
from testzeus_hercules.explore.explorer import ExploreJob, read_jsonl, run_exploration
from testzeus_hercules.explore.local import LocalRunner
from testzeus_hercules.explore.models import Charter, Clue, ExploreSpec
from testzeus_hercules.explore.reproduce import ReproJob, run_reproduction
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient
from tests.v2.engine_fixtures.browser import require_chromium
from tests.v2.explore_fixtures import ExploreDecider, MoveReader, pantry, prefer


def _signed_in(url: str, path: Path) -> None:
    jar = http.cookiejar.CookieJar()
    urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)).open(url + "/login", data=b"name=ana")
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/", "expires": -1, "httpOnly": True, "secure": False, "sameSite": "Lax"} for c in jar]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")


def _context(project: Path, home: Path) -> tuple[Settings, RunContext]:
    settings = Settings.load(project, env={"HERCULES_HOME": str(home)})
    return settings, RunContext.create(settings, telemetry=TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport()))


def _explore_profile(tmp_path: Path, bugs: str) -> tuple[Settings, RunContext, list[Clue], str, TestCase]:
    project = tmp_path / "p"
    project.mkdir()
    settings, ctx = _context(project, tmp_path / "home")
    chromium = require_chromium()
    with pantry(bugs) as url:
        _signed_in(url, project / ".hercules/auth/ana.json")
        case = TestCase.model_validate(
            {"id": "explore-profile", "title": "Explore", "steps": [{"action": "explore"}], "environment": {"base_url": url, "browser_options": {"storage_state": ".hercules/auth/ana.json"}}}
        )
        job = ExploreJob(
            session="s1",
            charter=Charter(id="ch1", title="Push the profile form with unusual names", area="/profile", levers=["inputs"], timebox_s=120, max_turns=3),
            mission="find bugs",
            levers=["inputs"],
            base_url=url,
            start_url=url + "/profile",
            judge=False,
        )
        deps = RunDeps(decider=ExploreDecider(pick=prefer("just_over_40", "Display name")), interpreter=MoveReader(), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium})
        asyncio.run(run_exploration(case, ctx, deps, job))
        out = ctx.paths.run_dir(ctx.run_id) / "explore"
        clues = read_jsonl(out / "clues.jsonl", Clue)
        if bugs != "none" and clues:
            repro_ctx = RunContext.create(settings, telemetry=ctx.telemetry)
            repro_deps = RunDeps(decider=ExploreDecider(severity="P2"), interpreter=MoveReader(), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium})
            asyncio.run(run_reproduction(case, repro_ctx, repro_deps, ReproJob(session="s1", clue=clues[0], base_url=url, start_url=url + "/profile")))
            finding = (repro_ctx.paths.run_dir(repro_ctx.run_id) / "explore" / "finding.json").read_text(encoding="utf-8")
            return settings, ctx, clues, finding, case
    return settings, ctx, clues, "", case


def test_an_explorer_finds_a_seeded_server_error_and_the_reproducer_confirms_it(tmp_path: Path) -> None:
    _, ctx, clues, finding_text, _ = _explore_profile(tmp_path, "B1")
    out = ctx.paths.run_dir(ctx.run_id) / "explore"
    assert any(clue.oracle == "server_error" and clue.route == "/profile" for clue in clues), [clue.title for clue in clues]
    assert (out / "turns.jsonl").is_file() and (out / "debrief.json").is_file() and (out / "coverage.json").is_file()
    finding = json.loads(finding_text)
    data = finding.get("finding", finding)
    assert data["category"] == "product_bug"
    assert data["reproduced_recorded"].startswith(("3/3", "2/3"))
    case_path = tmp_path / "p" / data["case_path"]
    assert case_path.is_file() and json.loads(case_path.read_text(encoding="utf-8"))["status"] == "known_bug"


def test_the_same_moves_raise_nothing_on_a_clean_build(tmp_path: Path) -> None:
    _, _, clues, _, _ = _explore_profile(tmp_path, "none")
    assert [clue for clue in clues if clue.tier == "code"] == []


def test_a_session_finds_seeded_bugs_end_to_end(tmp_path: Path) -> None:
    chromium = require_chromium()
    project = tmp_path / "p"
    (project / ".hercules" / "cases").mkdir(parents=True)

    def pick(options: dict[str, Any]) -> str:
        for name, text in options.items():
            if name != "done" and "(tried already)" not in str(text) and any(f"/{word}]" in str(text) for word in ("just_over_40", "empty")):
                return name
        return "m1"

    with pantry("B1,B7") as url:
        (project / ".hercules/cases/sign-in.json").write_text(
            json.dumps(
                {"id": "sign-in", "title": "Sign in", "environment": {"base_url": url}, "steps": [{"action": 'Open "/login"'}, {"action": 'Fill "ana" in "Name" and click the "Sign in" button'}]}
            ),
            encoding="utf-8",
        )
        settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
        runner = LocalRunner(settings, lambda: RunDeps(decider=ExploreDecider(pick=pick, severity="P2"), interpreter=MoveReader(), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium}))
        spec = ExploreSpec(
            start_case=".hercules/cases/sign-in.json",
            base_url=url,
            workers=2,
            reproducers=1,
            levers=["inputs"],
            budget={"time_s": 150, "recon_share": 0.2, "reproduce_grace_s": 120, "target_findings": 1},
            isolation="partition",
            severity_floor="P3",
            gate_on="proposed-hard",
        )
        events: list[tuple[str, dict[str, Any]]] = []
        session = Session("x_20261113_000000_abcdef", spec, project, runner, decider=ExploreDecider(), emit=lambda kind, data: events.append((kind, data)), poll_s=0.5)
        view = asyncio.run(session.run())
    assert view.status in ("finished", "stopped"), view.message
    assert view.findings, f"no findings; {view.message}; clues {view.clues}"
    assert all(finding.case_path for finding in view.findings)
    assert view.exit_code == 1  # proposed-hard: a code-oracle finding at or above the floor fails the gate before triage
    report = (project / ".hercules/explore/x_20261113_000000_abcdef/report.md").read_text(encoding="utf-8")
    assert "finding(s)" in report and "(proposed)" in report
    ledger = json.loads((project / ".hercules/explore/ledger.json").read_text(encoding="utf-8"))
    assert {item["id"] for item in ledger["findings"]} >= {finding.id for finding in view.findings}
    assert any(kind == "finding" for kind, _ in events)
    # The start case was recorded, so reproductions sign in afresh rather than reusing an explorer's session.
    setup = [json.loads(line) for line in (project / ".hercules/explore/x_20261113_000000_abcdef/start-0.setup.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(setup) == 2 and all(turn["automation"] for turn in setup)
    outcomes = [json.loads(path.read_text(encoding="utf-8")) for path in (project / ".hercules" / "runs").glob("*/explore/finding.json")]
    assert outcomes and all("clean browser" in " ".join(item["notes"]) for item in outcomes)


# --------------------------------------------------------------------------- reproducing from a clean session


def _session_tools(tmp_path: Path, url: str) -> tuple[Settings, RunContext, Path, list[Any], TestCase]:
    """Sign in once (a setup job, as a session does), keeping the saved state and the recorded sign-in steps."""
    from testzeus_hercules.explore.models import Turn

    project = tmp_path / "p"
    project.mkdir(exist_ok=True)
    settings, ctx = _context(project, tmp_path / "home")
    state = project / ".hercules/explore/s1/start-0.json"
    sign_in = TestCase.model_validate(
        {"id": "sign-in", "title": "Sign in", "environment": {"base_url": url}, "steps": [{"action": 'Open "/login"'}, {"action": 'Fill "ana" in "Name" and click the "Sign in" button'}]}
    )
    job = ExploreJob(
        session="s1",
        charter=Charter(id="setup-0", title="Reach the starting state", kind="setup", timebox_s=300),
        mission="",
        levers=["inputs"],
        base_url=url,
        start_url=url + "/login",
        save_state_to=str(state),
        judge=False,
    )
    asyncio.run(run_exploration(sign_in, ctx, RunDeps(decider=ExploreDecider(), interpreter=MoveReader(), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": require_chromium()}), job))
    setup = read_jsonl(state.with_suffix(".setup.jsonl"), Turn)
    signed_in = TestCase.model_validate(
        {"id": "explore", "title": "Explore", "steps": [{"action": "explore"}], "environment": {"base_url": url, "browser_options": {"storage_state": str(state.relative_to(project))}}}
    )
    return settings, ctx, project, setup, signed_in


def _charter(settings: Settings, ctx: RunContext, case: TestCase, url: str, area: str, levers: list[str], pick: Any, turns: int, promises: list[Any]) -> tuple[list[Any], list[Clue]]:
    from testzeus_hercules.explore.models import Turn

    run = RunContext.create(settings, telemetry=ctx.telemetry)
    job = ExploreJob(
        session="s1",
        charter=Charter(id=f"ch-{area.strip('/') or 'home'}", title=f"Explore {area}", area=area, levers=levers, timebox_s=120, max_turns=turns),
        mission="find bugs",
        levers=levers,
        base_url=url,
        start_url=url + area,
        promises=promises,
        judge=False,
    )
    deps = RunDeps(decider=ExploreDecider(pick=pick), interpreter=MoveReader(), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": require_chromium()})
    asyncio.run(run_exploration(case, run, deps, job))
    out = run.paths.run_dir(run.run_id) / "explore"
    return read_jsonl(out / "turns.jsonl", Turn), read_jsonl(out / "clues.jsonl", Clue)


def _reproduce(settings: Settings, ctx: RunContext, case: TestCase, url: str, clue: Clue, setup: list[Any], history: list[Any], promises: list[Any]) -> dict[str, Any]:
    run = RunContext.create(settings, telemetry=ctx.telemetry)
    deps = RunDeps(decider=ExploreDecider(severity="P2"), interpreter=MoveReader(), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": require_chromium()})
    job = ReproJob(session="s1", clue=clue, base_url=url, start_url=clue.start_url, promises=promises, setup=setup, history=history)
    asyncio.run(run_reproduction(case, run, deps, job))
    out = run.paths.run_dir(run.run_id) / "explore"
    outcome = dict(json.loads((out / "finding.json").read_text(encoding="utf-8")))
    outcome["interpreted_moves"] = [(turn["move"]["text"], turn["status"], turn["url_after"]) for turn in (json.loads(line) for line in (out / "turns.jsonl").read_text(encoding="utf-8").splitlines())]
    return outcome


def test_a_clean_replay_clicks_a_control_whose_count_changed(tmp_path: Path) -> None:
    """B8 (a dead "Apply coupon"), reached through the "Cart (2)" link: a fresh session shows "Cart (0)"."""

    def pick(options: dict[str, Any]) -> str:
        for name, text in options.items():
            if "Coupon" in str(text) and "Fill" in str(text) and "(tried already)" not in str(text):
                return name
        return next((name for name, text in options.items() if 'the "Cart (' in str(text)), "m1")

    with pantry("B8") as url:
        settings, ctx, _, setup, case = _session_tools(tmp_path, url)
        _charter(settings, ctx, case, url, "/shop", ["order"], prefer("Add Green tea"), 1, [])
        _, clues = _charter(settings, ctx, case, url, "/home", ["inputs"], pick, 2, [])
        clue = next(clue for clue in clues if clue.oracle == "dead_action")
        assert any('"Cart (' in turn.move.text and "(0)" not in turn.move.text for turn in clue.turns), [turn.move.text for turn in clue.turns]
        outcome = _reproduce(settings, ctx, case, url, clue, setup, [], [])
    assert outcome["recorded"].startswith(("3/3", "2/3")), outcome["notes"]
    assert "finding" in outcome, (outcome["notes"], outcome["interpreted_moves"])
    # The case signs in first, as the session did: rerun on its own, it reaches the bug (and passes once it is fixed).
    written = json.loads((settings.project_dir / outcome["finding"]["case_path"]).read_text(encoding="utf-8"))
    assert setup and [step["action"] for step in written["steps"][: len(setup)]] == [turn.move.text for turn in setup]


def test_a_clue_that_needs_an_earlier_charters_state_reproduces_with_the_sessions_moves(tmp_path: Path) -> None:
    """B4 (the badge keeps the old count): its charter only removes; adding the item was an earlier charter's move."""
    from testzeus_hercules.explore.models import Move, Promise, Turn

    badge = [Promise(id="badge-equals-count", kind="equal", route="/cart", target="#badge", other="#count", text="The cart badge equals the items in the cart")]
    with pantry("B4") as url:
        settings, ctx, _, setup, case = _session_tools(tmp_path, url)
        added, _ = _charter(settings, ctx, case, url, "/shop", ["order"], prefer("Add Green tea"), 1, [])
        _, clues = _charter(settings, ctx, case, url, "/cart", ["order"], prefer("Remove Green tea"), 1, badge)
        clue = next(clue for clue in clues if clue.signature.endswith("badge-equals-count"))
        alone = _reproduce(settings, ctx, case, url, clue, setup, [], badge)
        history = [Turn(n=1, move=Move(lever="order", variant="goto", text=f'Open "{url}/shop"', direct="goto", value=url + "/shop"), status="passed"), *added]
        carried = _reproduce(settings, ctx, case, url, clue, setup, history, badge)
    assert alone["recorded"] == "0/3" and "finding" not in alone
    assert carried["recorded"].startswith(("3/3", "2/3")), carried["notes"]
    assert any("state built earlier in the session" in note for note in carried["notes"])
    steps = carried["finding"]["steps"]
    assert any("Add Green tea" in step for step in steps) and any("Remove Green tea" in step for step in steps)
