"""Exploration pieces that need no browser: levers, scope, signatures, minimize, ledger, charters, sizing, report, issues."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from testzeus_hercules.explore.charters import candidate_charters, choose_charter, dig_charter
from testzeus_hercules.explore.coordinator import Limits, estimate, size_problems
from testzeus_hercules.explore.issues import github_new_issue_url, issue_text, issue_title
from testzeus_hercules.explore.ledger import TRANSITIONS, Ledger, TriageError
from testzeus_hercules.explore.levers import candidate_moves, writes
from testzeus_hercules.explore.missions import MISSIONS, floor_for, load_mission, rank, worse
from testzeus_hercules.explore.models import Charter, Clue, ExploreSpec, Finding, Move, Scope, Turn
from testzeus_hercules.explore.oracles import normalize_route, signature
from testzeus_hercules.explore.replay import ReplayResult, confirm, minimize
from testzeus_hercules.explore.report import render_markdown
from testzeus_hercules.explore.scope import ScopeGuard, production_host
from testzeus_hercules.mcp.explore_tools import explore_spec, session_summary

# --------------------------------------------------------------------------- helpers


@dataclass
class _Element:
    role: str
    name: str
    section: str = ""
    value: str | None = None
    disabled: bool = False
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class _Snapshot:
    offered: list[_Element]


def _page() -> _Snapshot:
    return _Snapshot(
        [
            _Element("textbox", "Name", "Profile"),
            _Element("spinbutton", "Quantity", "Profile"),
            _Element("button", "Save", "Profile"),
            _Element("link", "Cart"),
            _Element("button", "Sign out"),
            _Element("button", "Disabled", disabled=True),
        ]
    )


def _finding(fid: str = "f1", sig: str = "server_error|/cart|5xx", **extra: Any) -> Finding:
    data: dict[str, Any] = {
        "id": fid,
        "title": "Saving a 41-character name returns a server error",
        "signature": sig,
        "oracle": "server_error",
        "tier": "code",
        "proposed_severity": "P1",
        "proposed_priority": "P1",
        "steps": ['Open "/profile"', 'Fill "xxxx" in "Name" and click the "Save" button'],
        "expected": "The name is saved or refused with a message",
        "actual": "HTTP 500",
        "sessions": ["x_1"],
        "builds": ["b1"],
        "case_path": ".hercules/cases/explore/x_1/f1.json",
    }
    data.update(extra)
    return Finding.model_validate(data)


def _turn(n: int) -> Turn:
    return Turn(n=n, move=Move(lever="order", variant="reload", text="Reload the page", direct="reload"), status="passed")


class _Replayer:
    """Raises the signature only when every turn in ``needed`` is replayed (in order)."""

    def __init__(self, needed: set[int], sig: str = "sig") -> None:
        self.needed = needed
        self.sig = sig
        self.calls = 0

    async def replay(self, turns: list[Turn]) -> ReplayResult:
        self.calls += 1
        hit = self.needed <= {turn.n for turn in turns}
        return ReplayResult(signatures=frozenset({self.sig}) if hit else frozenset(), completed=True)


# --------------------------------------------------------------------------- levers


def test_inputs_lever_fills_fields_and_submits_with_the_sections_button() -> None:
    moves = candidate_moves(_page(), levers=["inputs"])
    name_moves = [move for move in moves if move.element == "textbox Name"]
    assert name_moves and all(move.clicks == "Save" and move.writes for move in name_moves)
    assert name_moves[0].variant == "just_over_40" and name_moves[0].value == "x" * 41
    quantity = [move for move in moves if move.element == "spinbutton Quantity"]
    assert quantity[0].variant == "negative"  # numbers lead with the negative, zero and huge values
    assert any(move.text.startswith('Clear "Name"') for move in name_moves)  # the empty value clears the field
    assert not any("Disabled" in (move.element or "") for move in moves)


def test_order_lever_offers_double_submit_only_for_state_changing_buttons() -> None:
    moves = candidate_moves(_page(), levers=["order"])
    doubles = [move for move in moves if move.variant == "double_submit"]
    assert "Save" in {move.clicks for move in doubles} and "Cart" not in {move.clicks for move in doubles}  # the scope guard, not the lever, keeps "Sign out" from being clicked
    assert {move.direct for move in moves if move.direct} >= {"resubmit", "reload", "back"}


def test_tried_moves_are_not_offered_first() -> None:
    first = candidate_moves(_page(), levers=["inputs"])[0]
    tried = {(first.element or "", first.lever, first.variant)}
    again = candidate_moves(_page(), levers=["inputs"], tried=tried)
    assert again[0] != first


def test_writes_reads_the_control_name() -> None:
    assert writes("Place order") and writes("Save changes") and not writes("Cart")


# --------------------------------------------------------------------------- scope


def test_forbidden_moves_are_read_from_the_clicked_control_only() -> None:
    guard = ScopeGuard(Scope(), "http://localhost:8000")
    typing_email = Move(lever="inputs", variant="empty", text='Clear "Email" and click the "Save" button', element="textbox Email", clicks="Save")
    assert guard.forbidden_move(typing_email) is None
    sign_out = Move(lever="order", variant="double_submit", text='Click the "Sign out" button twice quickly', clicks="Sign out")
    assert guard.forbidden_move(sign_out) == "sign out"
    allowed = ScopeGuard(Scope(allow=["sign out"]), "http://localhost:8000")
    assert allowed.forbidden_move(sign_out) is None
    extra = ScopeGuard(Scope(forbid=["archive"]), "http://localhost:8000")
    assert extra.forbidden_move(Move(lever="order", variant="x", text="t", clicks="Archive project")) == "archive"


def test_requests_outside_scope_deny_paths_and_deletes_are_blocked() -> None:
    guard = ScopeGuard(Scope(deny_paths=["/admin"]), "http://localhost:8000")
    assert guard.request_problem("http://localhost:8000/cart", "POST") is None
    assert "outside" in (guard.request_problem("https://evil.example/x", "GET") or "")
    assert "deny_paths" in (guard.request_problem("http://localhost:8000/admin/users", "GET") or "")
    assert guard.request_problem("http://localhost:8000/administrator", "GET") is None
    assert "DELETE" in (guard.request_problem("http://localhost:8000/cart/1", "DELETE") or "")
    assert "forbidden action" in (guard.request_problem("http://localhost:8000/api/delete-account", "POST") or "")
    assert guard.request_problem("data:text/plain,hi", "GET") is None


def test_a_page_may_load_its_assets_from_other_hosts_but_explorers_never_go_or_write_there() -> None:
    guard = ScopeGuard(Scope(), "https://shop.example")
    assert guard.request_problem("https://cdn.example/banner.jpg", "GET", navigation=False) is None
    assert "outside" in (guard.request_problem("https://cdn.example/page", "GET", navigation=True) or "")
    assert "outside" in (guard.request_problem("https://cdn.example/collect", "POST", navigation=False) or "")


def test_the_guards_own_blocks_never_become_clues() -> None:
    from testzeus_hercules.explore.oracles import Oracles, TurnStart

    oracles = Oracles(base_url="https://shop.example")
    move = Move(lever="order", variant="visit", text="Open the shop")
    records = [
        ("network", {"url": "https://cdn.example/a.jpg", "method": "GET", "failed": failed}) for failed in ("net::ERR_BLOCKED_BY_CLIENT.Inspector", "net::ERR_BLOCKED_BY_CLIENT", "blockedbyclient")
    ]
    start = TurnStart(url="https://shop.example/", text="", digest="", records=0)
    assert oracles._code(records, "/", move, start, "https://shop.example/", "", "done") == []


@pytest.mark.parametrize(
    ("host", "production"),
    [("localhost:8000", False), ("staging.acme.com", False), ("app-qa.acme.com", False), ("10.0.0.4", False), ("shop.test", False), ("acme.com", True), ("www.acme.com", True)],
)
def test_production_host(host: str, production: bool) -> None:
    assert production_host(host) is production


# --------------------------------------------------------------------------- signatures


def test_signatures_fold_ids_and_numbers() -> None:
    assert normalize_route("http://x/orders/123/items/9f8e7d6c5b4a") == "/orders/:id/items/:id"
    assert normalize_route("http://x/") == "/"
    first = signature("server_error", "/orders/:id", "POST /api/orders/17 returned 500")
    second = signature("server_error", "/orders/:id", "POST /api/orders/42 returned 500")
    assert first == second
    assert first != signature("page_error", "/orders/:id", "POST /api/orders/17 returned 500")


# --------------------------------------------------------------------------- reproduce


def test_minimize_keeps_only_the_turns_that_matter() -> None:
    turns = [_turn(n) for n in range(1, 9)]
    replayer = _Replayer({3, 8})
    kept, used = asyncio.run(minimize(replayer, turns, "sig", max_runs=15))  # type: ignore[arg-type]
    assert [turn.n for turn in kept] == [3, 8]
    assert used == replayer.calls <= 15


def test_minimize_respects_its_run_budget() -> None:
    turns = [_turn(n) for n in range(1, 30)]
    kept, used = asyncio.run(minimize(_Replayer({2, 5, 11, 17, 29}), turns, "sig", max_runs=4))  # type: ignore[arg-type]
    assert used <= 4 and kept[-1].n == 29


def test_confirm_counts_hits() -> None:
    seen: list[tuple[int, bool]] = []

    async def on_run(index: int, hit: bool) -> None:
        seen.append((index, hit))

    hits = asyncio.run(confirm(_Replayer({1}), [_turn(1)], "sig", 3, on_run=on_run))  # type: ignore[arg-type]
    assert hits == 3 and seen == [(1, True), (2, True), (3, True)]


# --------------------------------------------------------------------------- ledger


def test_ledger_records_new_seen_again_regression_and_suppressed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    assert ledger.record(_finding()).outcome == "new"
    again = ledger.record(_finding("f2", sessions=["x_2"], builds=["b2"]))
    assert again.outcome == "seen_again" and again.finding.id == "f1"
    assert again.finding.sessions == ["x_1", "x_2"] and again.finding.builds == ["b1", "b2"]
    ledger.triage("f1", by="ana", state="confirmed")
    ledger.triage("f1", by="ana", state="verify_fixed")
    ledger.triage("f1", by="ana", state="closed")
    regressed = ledger.record(_finding("f3", sessions=["x_3"]))
    assert regressed.outcome == "regression" and regressed.finding.regression and regressed.finding.state == "new"
    ledger.record(_finding("g1", sig="page_error|/|boom"))
    ledger.triage("g1", by="ana", state="by_design", reason="the boom is intended")
    assert ledger.record(_finding("g2", sig="page_error|/|boom")).outcome == "suppressed"
    assert ledger.suppressed("page_error|/|boom")


def test_triage_rules_and_audit(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.record(_finding())
    ledger.record(_finding("f2", sig="other"))
    with pytest.raises(TriageError, match="cannot move"):
        ledger.triage("f1", by="ana", state="closed")
    with pytest.raises(TriageError, match="say why"):
        ledger.triage("f1", by="ana", state="wont_fix")
    with pytest.raises(TriageError, match="duplicate"):
        ledger.triage("f1", by="ana", state="duplicate", reason="same")
    with pytest.raises(TriageError, match="severity"):
        ledger.triage("f1", by="ana", severity="P9")
    with pytest.raises(TriageError, match="no finding"):
        ledger.triage("nope", by="ana", state="confirmed")
    finding = ledger.triage("f1", by="ana", severity="P0", tracker="ACME-12")
    assert finding.severity == "P0" and finding.state == "in_tracker" and finding.tracker == "ACME-12"
    assert [event.field for event in finding.audit] == ["severity", "tracker", "state", "case"] and finding.case == "kept"
    duplicate = ledger.triage("f2", by="ana", state="duplicate", duplicate_of="f1", reason="same bug")
    assert duplicate.duplicate_of == "f1"
    assert all(state in TRANSITIONS for targets in TRANSITIONS.values() for state in targets)


def test_reverify_and_precision(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    for index in range(4):
        ledger.record(_finding(f"f{index}", sig=f"s{index}"))
    ledger.triage("f0", by="ana", state="confirmed")
    ledger.triage("f1", by="ana", state="confirmed")
    ledger.triage("f2", by="ana", state="by_design", reason="intended")
    assert ledger.verified("f0", passed=True, build="b9").state == "verify_fixed"
    assert ledger.verified("f0", passed=False, build="b10").state == "confirmed"
    precision = ledger.precision()
    assert precision["confirmed"] == 2 and precision["dismissed"] == 1 and precision["untriaged"] == 1
    assert precision["precision"] == pytest.approx(0.667, abs=0.001)
    low, high = precision["interval_90"]  # type: ignore[misc]
    assert 0 <= low < 0.667 < high <= 1
    assert Ledger(tmp_path / "empty").precision()["precision"] is None


def test_ledger_survives_a_corrupt_file_and_keeps_a_copy(tmp_path: Path) -> None:
    (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
    ledger = Ledger(tmp_path)
    assert ledger.all() == []
    ledger.record(_finding())
    copies = list(tmp_path.glob("ledger.json.corrupt-*"))
    assert copies and copies[0].read_text(encoding="utf-8") == "{not json"


def test_ledger_keeps_entries_it_cannot_read(tmp_path: Path) -> None:
    import json

    future = {"id": "fz", "title": "from a newer version", "kind_of_thing": "new field", "signature": "x"}
    (tmp_path / "ledger.json").write_text(json.dumps({"version": 1, "findings": [future]}), encoding="utf-8")
    ledger = Ledger(tmp_path)
    ledger.record(_finding())
    saved = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))["findings"]
    assert future in saved and any(item["id"] == "f1" for item in saved)


# --------------------------------------------------------------------------- charters and missions


_STATES = {
    "a": {"route": "/profile", "elements": ["Name", "Quantity", "Save"]},
    "b": {"route": "/cart", "elements": ["Remove", "Checkout"]},
    "c": {"route": "/about", "elements": []},
}


def test_candidate_charters_cover_routes_by_mission_levers() -> None:
    charters = candidate_charters(_STATES, MISSIONS["p1"], ["inputs", "order"])
    assert charters[0].area == "/profile"  # the busiest route first
    assert {(charter.area, charter.levers[0]) for charter in charters} >= {("/profile", "inputs"), ("/cart", "order")}
    assert len({charter.id for charter in charters}) == len(charters)


def test_changed_mission_narrows_to_changed_routes_and_personas_add_charters() -> None:
    charters = candidate_charters(_STATES, MISSIONS["changed"], ["inputs"], changed=["src/cart/Cart.tsx", "/cart"], personas=["impatient"])
    assert {charter.area for charter in charters} == {"/cart"}
    assert any(charter.persona == "impatient" for charter in charters)


def test_dig_charter_follows_a_clue() -> None:
    clue = Clue(id="c1", oracle="server_error", tier="code", signature="server_error|/cart|5xx", title="POST /cart returned 500", route="/cart")
    charter = dig_charter(clue, "inputs")
    assert charter.kind == "dig" and charter.priority == 0.9 and charter.dig_of == "c1" and charter.area == "/cart"


class _PickDecider:
    def __init__(self, choice: str) -> None:
        self.choice_name = choice

    async def decide(self, request: Any, *, purpose: str) -> Any:
        assert purpose == "explore_charter"
        choice = self.choice_name

        class _D:
            def choice(self, _: str) -> Any:
                return type("A", (), {"choice": choice})()

        return _D()


def test_choose_charter_pins_first_then_asks_the_model() -> None:
    queued = [Charter(id=f"c{index}", title=f"Charter {index}", priority=0.5 + index / 10) for index in range(3)]
    picked = asyncio.run(choose_charter(_PickDecider("c2"), queued, mission="p1", covered={}, running=[]))
    assert picked is not None and picked.id == "c1"  # offered by priority: c2 (0.7), c1 (0.6), c0 (0.5)
    assert asyncio.run(choose_charter(None, queued, mission="p1", covered={}, running=[])).id == "c2"  # type: ignore[union-attr]
    pinned = [*queued, Charter(id="p", title="Pinned", priority=0.1, pinned=True)]
    assert asyncio.run(choose_charter(_PickDecider("c1"), pinned, mission="p1", covered={}, running=[])).id == "p"  # type: ignore[union-attr]
    assert asyncio.run(choose_charter(_PickDecider("zz"), queued, mission="p1", covered={}, running=[])).id == "c2"  # type: ignore[union-attr]
    assert asyncio.run(choose_charter(None, [], mission="p1", covered={}, running=[])) is None


def test_missions_and_severity_order(tmp_path: Path) -> None:
    assert rank("P0") < rank("P3") and worse("P2", "P1") == "P1"
    assert floor_for("page_error") in ("P0", "P1", "P2", "P3")
    custom = load_mission("p1", "find ways the cart total goes wrong")
    assert custom.target == "find ways the cart total goes wrong"
    with pytest.raises(ValueError):
        load_mission("no-such-mission", None, tmp_path)


# --------------------------------------------------------------------------- the swarm size is the user's


def test_spec_bounds_and_where() -> None:
    with pytest.raises(ValidationError, match="say where"):
        ExploreSpec()
    with pytest.raises(ValidationError):
        ExploreSpec(base_url="http://localhost:1", workers=65)
    with pytest.raises(ValidationError, match="one account per explorer"):
        ExploreSpec(base_url="http://localhost:1", workers=3, isolation="accounts", accounts=["A"])
    assert ExploreSpec(base_url="http://localhost:1", workers=64, reproducers=16).workers == 64


def test_size_problems_report_numbers_never_cut() -> None:
    spec = ExploreSpec(base_url="http://localhost:1", workers=12, reproducers=2)
    problems = size_problems(spec, Limits(max_workers=8, parallel_cap=8, memory_bytes=2 * 2**30))
    assert any("12 explorers is more than explore.max_workers (8)" in item for item in problems)
    assert any("14 browsers at once" in item for item in problems)
    assert any("GiB" in item for item in problems)
    assert spec.workers == 12  # never changed
    assert size_problems(ExploreSpec(base_url="http://localhost:1"), Limits()) == []


def test_estimate_scales_with_size_and_time() -> None:
    small = estimate(ExploreSpec(base_url="http://localhost:1", workers=1, budget={"time_s": 600}), local_model=False)
    big = estimate(ExploreSpec(base_url="http://localhost:1", workers=4, budget={"time_s": 600}), local_model=False)
    assert big["decisions"] == 4 * small["decisions"] and big["cost_usd"] > small["cost_usd"]
    local = estimate(ExploreSpec(base_url="http://localhost:1", workers=2), local_model=True)
    assert local["cost_usd"] == 0.0 and "one decision at a time" in local["note"]


# --------------------------------------------------------------------------- report and issues


def _report_data(findings: list[Finding]) -> dict[str, Any]:
    spec = ExploreSpec(base_url="http://localhost:1")
    return {
        "id": "x_20261113_120000_abcdef",
        "spec": spec.model_dump(),
        "status": "finished",
        "message": "time is up",
        "exit_code": 1 if findings else 0,
        "elapsed_s": 300,
        "cost_usd": 0.12,
        "workers": 2,
        "reproducers": 1,
        "size_changes": [{"workers": 3}],
        "findings": [item.model_dump() for item in findings],
        "finding_outcomes": {item.id: "regression" for item in findings},
        "environment": {"base_url": "http://localhost:1", "build": "b1", "browser": "chromium"},
        "debriefs": [
            {
                "charter": "Explore /cart with order",
                "turns": 5,
                "by_lever": {"order": 5},
                "states": 2,
                "ended": "timebox",
                "clues": [],
                "not_reached": ["/admin"],
                "time_split": {"setup_s": 0.1, "testing_s": 0.8, "investigating_s": 0.1},
                "confidence": "medium",
                "confidence_reason": "5 moves",
            }
        ],
        "coverage": {
            "routes_seen": ["/", "/cart"],
            "states_seen": 3,
            "moves_tried": 9,
            "gaps": [{"route": "/cart", "levers_not_used": ["inputs"]}],
            "charters_not_run": ["Explore /profile with inputs"],
        },
        "promises": [{"text": "The total is never negative", "source": "learned"}],
        "blocked": [{"what": "POST /logout", "reason": "sign out"}],
        "rubric": {"P0": "data loss"},
    }


def test_report_says_no_findings_is_not_a_pass() -> None:
    text = render_markdown(_report_data([]))
    assert "No findings is not a pass" in text and "## Coverage gaps" in text and "/cart: levers not used: inputs" in text
    assert "Learned promises above can encode a bug" in text and "Blocked by the scope guard" in text


def test_report_lists_findings_with_proposed_severity_and_regressions() -> None:
    text = render_markdown(_report_data([_finding()]))
    assert "P1 (proposed)" in text and "**regression**" in text and "(1 change(s) during the session)" in text
    confirmed = render_markdown(_report_data([_finding(severity="P0")]))
    assert "**P0** (confirmed)" in confirmed


def test_issue_texts() -> None:
    finding = _finding(evidence=["runs/r1/steps/2/plain.jpg"], promise="total-not-negative", promise_source="specified")
    markdown = issue_text(finding)
    assert "P1 (proposed by the model, not confirmed)" in markdown and "1. Open" in markdown and "hercules-finding: f1" in markdown
    assert "Broken promise: total-not-negative (specified)" in markdown
    jira = issue_text(finding, "jira")
    assert "h3. Steps to reproduce" in jira and "# Open" in jira
    assert issue_title(finding).startswith("[P1] ")
    url = github_new_issue_url("acme/shop", finding)
    assert url.startswith("https://github.com/acme/shop/issues/new?") and "labels=bug%2CP1" in url


# --------------------------------------------------------------------------- MCP mapping and CLI


def test_mcp_arguments_become_a_spec() -> None:
    spec = explore_spec({"base_url": "http://localhost:3000", "time_s": 600, "workers": 3, "deny_paths": ["/admin"], "forbid": [], "max_cost_usd": 1.5, "prompt": ""})
    assert spec == {"base_url": "http://localhost:3000", "workers": 3, "budget": {"time_s": 600, "max_cost_usd": 1.5}, "scope": {"deny_paths": ["/admin"]}}
    ExploreSpec.model_validate(spec)


def test_mcp_session_summary_counts_charters() -> None:
    view = {
        "id": "x_1",
        "status": "exploring",
        "workers": 2,
        "reproducers": 1,
        "spec": {"budget": {"time_s": 900}},
        "charters": [{"status": "running"}, {"status": "queued"}, {"status": "queued"}],
        "findings": [_finding().model_dump()],
        "clues": 4,
    }
    data = session_summary(view, "http://127.0.0.1:7788")
    assert data["charters"] == {"running": 1, "queued": 2} and not data["finished"] and data["time_s"] == 900
    assert data["ui_url"] == "http://127.0.0.1:7788/explore/x_1" and data["findings"][0]["proposed_severity"] == "P1"


@pytest.mark.parametrize(("text", "seconds"), [("90", 5400), ("5", 300), ("90s", 90), ("30m", 1800), ("1h", 3600), ("1h30m", 5400)])
def test_parse_duration(text: str, seconds: int) -> None:
    from testzeus_hercules.cli.explore_cmd import parse_duration

    assert parse_duration(text) == seconds


def test_parse_duration_rejects_under_a_minute() -> None:
    from testzeus_hercules.cli.explore_cmd import parse_duration

    with pytest.raises(argparse.ArgumentTypeError, match="under a minute"):
        parse_duration("30s")


def test_parse_duration_rejects_nonsense() -> None:
    from testzeus_hercules.cli.explore_cmd import parse_duration

    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("soon")


# --------------------------------------------------------------------------- the benchmark scorer


def _bench() -> Any:
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2] / "bench" / "explore"
    sys.path.insert(0, str(root))
    try:
        spec = importlib.util.spec_from_file_location("explore_run_bench", root / "run_bench.py")
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    finally:
        sys.path.remove(str(root))
    return module


def test_bench_scores_recall_precision_and_false_findings() -> None:
    bench = _bench()
    manifest = bench.load_manifest()
    report = {
        "id": "x_1",
        "findings": [
            {"id": "f1", "title": "POST /profile returned 500", "oracle": "server_error", "signature": "server_error|/profile|post /profile #xx"},
            {"id": "f2", "title": "Promise broken on /cart: The cart badge equals the items in the cart", "oracle": "invariant", "signature": "invariant|/cart|badge-equals-count"},
            {"id": "f3", "title": "Home looks odd", "oracle": "judged", "signature": "judged|/home|odd"},
        ],
    }
    summary = bench.score(report, ["B1", "B3", "B4", "B7"], manifest)
    assert summary["found"] == {"B1": ["f1"], "B4": ["f2"]} and summary["missed"] == ["B3", "B7"]  # the badge finding is not the total bug
    assert summary["recall"] == 0.5 and summary["precision"] == 0.667 and summary["false_findings"] == 1
    clean = bench.score(report, [], manifest)
    assert clean["recall"] is None and clean["false_findings"] == 3


def test_reproducers_take_code_clues_before_promises_and_judged_ones() -> None:
    from testzeus_hercules.explore.coordinator import _reproduce_order

    clues = [
        Clue(id="c1", oracle="judged", tier="judged", signature="j", title="looks wrong", confidence=0.95),
        Clue(id="c2", oracle="invariant", tier="invariant", signature="i", title="promise broken"),
        Clue(id="c3", oracle="server_error", tier="code", signature="s", title="500"),
        Clue(id="c4", oracle="judged", tier="judged", signature="k", title="looks very wrong", confidence=0.99),
    ]
    order = sorted(range(len(clues)), key=lambda index: _reproduce_order(clues[index], index))
    assert [clues[index].id for index in order] == ["c3", "c2", "c4", "c1"]


def test_scope_guard_closes_the_reviewed_holes() -> None:
    guard = ScopeGuard(Scope(deny_paths=["/admin"]), "http://user:secret@localhost:8000")
    assert guard.hosts == {"localhost:8000"}  # no credentials in the host check
    assert guard.request_problem("http://localhost:8000/cart", "GET") is None
    assert "file:" in (guard.request_problem("file:///etc/passwd", "GET") or "")
    assert "deny_paths" in (guard.request_problem("http://localhost:8000/Admin/users", "GET") or "")
    assert "sign the explorer out" in (guard.request_problem("http://localhost:8000/logout", "GET") or "")
    assert "DELETE" in (guard.request_problem("http://localhost:8000/items/3?_method=DELETE", "POST") or "")


@pytest.mark.parametrize(("host", "production"), [("192.168.1.20:8080", False), ("8.8.8.8", True), ("[::1]:3000", False)])
def test_production_host_addresses(host: str, production: bool) -> None:
    assert production_host(host) is production


def test_spec_rejects_traversal_and_credentials() -> None:
    with pytest.raises(ValidationError):
        ExploreSpec(base_url="http://localhost:1", mission="../../etc/x")
    with pytest.raises(ValidationError, match="user name or password"):
        ExploreSpec(base_url="http://ana:pw@localhost:1")
    assert ExploreSpec(base_url="http://localhost:1", mission="persona:screen-reader").mission == "persona:screen-reader"


# --------------------------------------------------------------------------- oracles (what the first real runs showed)


class _Page:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text

    async def evaluate(self, script: str, *args: Any) -> Any:
        return self.text


class _JudgeAlwaysWrong:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, request: Any, *, purpose: str) -> Any:
        self.calls += 1

        class _D:
            def noul(self, _: str) -> Any:
                return type("N", (), {"noul": 1.0 if _ != "opinion" else 0.0})()

        return _D()


def test_the_judge_is_not_asked_when_a_code_check_already_explains_the_move() -> None:
    from testzeus_hercules.explore.oracles import Oracles

    judge = _JudgeAlwaysWrong()
    oracles = Oracles(base_url="http://localhost:1", decider=judge)
    page = _Page("http://localhost:1/search", "Search Go")
    move = Move(lever="inputs", variant="empty", text='Clear "Search" and click the "Go" button', element="searchbox Search", value="", clicks="Go")
    start = asyncio.run(oracles.before(page))
    oracles.listen("network", {"url": "http://localhost:1/search?q=", "method": "GET", "status": 500})
    page.text = "Something went wrong. Internal server error."
    drafts, _ = asyncio.run(oracles.after(page, move, start, status="passed"))
    assert [draft.oracle for draft in drafts] == ["server_error"] and judge.calls == 0


def test_a_submit_that_ignores_an_empty_value_is_not_a_dead_action() -> None:
    from testzeus_hercules.explore.oracles import Oracles

    oracles = Oracles(base_url="http://localhost:1", judge=False)
    page = _Page("http://localhost:1/notes", "Notes New note Save note")
    empty = Move(lever="inputs", variant="empty", text='Clear "New note" and click the "Save note" button', value="", clicks="Save note", writes=True)
    start = asyncio.run(oracles.before(page))
    assert asyncio.run(oracles.after(page, empty, start, status="passed"))[0] == []
    typed = Move(lever="inputs", variant="unicode", text='Fill "Zoë" in "Coupon" and click the "Apply coupon" button', value="Zoë", clicks="Apply coupon", writes=True)
    start = asyncio.run(oracles.before(page))
    assert [draft.oracle for draft in asyncio.run(oracles.after(page, typed, start, status="passed"))[0]] == ["dead_action"]


# --------------------------------------------------------------------------- cost


def _run_json(tmp_path: Path, *, usd: float | None = None, hosted_llm_calls: int = 0, cache_hits: int = 0) -> Path:
    from testzeus_hercules.cases.models import CostInfo, EngineInfo, RunResult

    result = RunResult(
        run_id="r_1",
        status="error",
        case_id="c",
        engines=EngineInfo(hosted_llm_calls=hosted_llm_calls, llm_cache_hits=cache_hits),
        cost=CostInfo(tokens_in=1000, tokens_out=100, usd=usd),
    )
    (tmp_path / "run.json").write_text(result.model_dump_json(), encoding="utf-8")
    return tmp_path


def test_a_child_on_a_hosted_model_is_priced_per_call_as_an_estimate(tmp_path: Path) -> None:
    from testzeus_hercules.explore.coordinator import run_cost

    assert run_cost(_run_json(tmp_path, hosted_llm_calls=40), per_hosted_call_usd=0.0005) == (0.02, True)


def test_billed_dollars_are_kept_and_a_local_model_is_free(tmp_path: Path) -> None:
    from testzeus_hercules.explore.coordinator import run_cost

    assert run_cost(_run_json(tmp_path, usd=0.031), per_hosted_call_usd=0.0005) == (0.031, False)
    assert run_cost(_run_json(tmp_path), per_hosted_call_usd=0.0005) == (0.0, False)
    assert run_cost(tmp_path / "missing", per_hosted_call_usd=0.0005) == (0.0, False)


def test_run_usage_counts_calls_to_a_hosted_model_but_not_to_a_local_one() -> None:
    from types import SimpleNamespace

    from testzeus_hercules.cli.run import run_usage
    from testzeus_hercules.engine.factory import RunUsage

    def engines(is_local: bool) -> Any:
        llm = SimpleNamespace(endpoint=SimpleNamespace(is_local=is_local))
        return SimpleNamespace(llm=llm, usage=lambda: RunUsage(tokens_in=10, tokens_out=2, local_calls=7, cache_hits=2))

    assert run_usage(engines(False))[1]["hosted_llm_calls"] == 5
    assert "hosted_llm_calls" not in run_usage(engines(True))[1]


def test_a_blocked_background_request_gets_an_empty_json_answer_so_the_page_carries_on() -> None:
    @dataclass
    class FakeRequest:
        url: str
        method: str = "POST"
        navigation: bool = False
        headers: dict[str, str] = field(default_factory=dict)

        def is_navigation_request(self) -> bool:
            return self.navigation

    @dataclass
    class FakeRoute:
        done: list[str] = field(default_factory=list)

        async def abort(self, code: str = "") -> None:
            self.done.append(f"abort {code}")

        async def fulfill(self, status: int = 200, body: str = "", content_type: str = "", **_: Any) -> None:
            self.done.append(f"fulfill {status} {content_type} {body}")

        async def continue_(self) -> None:
            self.done.append("continue")

    guard = ScopeGuard(Scope(deny_paths=["/admin"]), "https://shop.example")

    async def handle(request: FakeRequest) -> list[str]:
        route = FakeRoute()
        await guard._handle(route, request)
        return route.done

    assert asyncio.run(handle(FakeRequest("https://analytics.example/collect"))) == ["fulfill 200 application/json {}"]
    assert asyncio.run(handle(FakeRequest("https://elsewhere.example/", "GET", navigation=True))) == ["abort blockedbyclient"]
    assert asyncio.run(handle(FakeRequest("https://shop.example/admin/users", "GET"))) == ["abort blockedbyclient"]
    assert asyncio.run(handle(FakeRequest("https://cdn.example/a.jpg", "GET"))) == ["continue"]
    assert any("analytics.example" in note for note in map(str, guard.blocked))


def test_a_page_error_thrown_only_by_another_hosts_script_is_not_the_apps_bug() -> None:
    from testzeus_hercules.explore.oracles import Oracles, TurnStart

    oracles = Oracles(base_url="https://shop.example")
    move = Move(lever="order", variant="visit", text="Open the shop")
    start = TurnStart(url="https://shop.example/", text="", digest="", records=0)
    third_party = "TypeError: x\n    at n (https://cdn.push.example/sdk.js:1:200)\n    at https://cdn.push.example/sdk.js:1:90"
    ours = "TypeError: y\n    at render (https://shop.example/static/app.js:3:10)\n    at https://cdn.push.example/sdk.js:1:90"
    records = [
        ("console", {"type": "pageerror", "text": "Cannot read properties of undefined (reading 'enabled')", "stack": third_party}),
        ("console", {"type": "pageerror", "text": "Cannot read properties of undefined (reading 'price')", "stack": ours}),
        ("console", {"type": "pageerror", "text": "boom"}),
    ]
    found = [draft.detail for draft in oracles._code(records, "/", move, start, "https://shop.example/", "", "done")]
    assert found == ["Cannot read properties of undefined (reading 'price')", "boom"]
    allowed = Oracles(base_url="https://shop.example", allow_hosts=("cdn.push.example",))
    assert len(allowed._code(records[:1], "/", move, start, "https://shop.example/", "", "done")) == 1


# --------------------------------------------------------------------------- the explore case suite


def _project_with_staged_case(tmp_path: Path, fid: str = "f1") -> tuple[Path, Ledger]:
    project = tmp_path / "p"
    staged = project / ".hercules" / "explore" / "cases" / f"{fid}.json"
    staged.parent.mkdir(parents=True)
    case = {"id": fid, "title": "Saving a long name returns a server error", "steps": [{"action": 'Open "/profile"', "expected": "No server error"}], "status": "known_bug", "tags": ["explore"]}
    staged.write_text(json.dumps(case), encoding="utf-8")
    return project, Ledger(project / ".hercules" / "explore")


def _case_at(project: Path, relative: str | None) -> dict[str, Any]:
    assert relative is not None
    return dict(json.loads((project / relative).read_text(encoding="utf-8")))


def test_a_new_findings_case_is_proposed_outside_the_suite(tmp_path: Path) -> None:
    project, ledger = _project_with_staged_case(tmp_path)
    finding = ledger.record(_finding(case_path=".hercules/explore/cases/f1.json")).finding
    assert finding.case == "proposed" and finding.case_path == ".hercules/explore/cases/f1.json"
    assert not (project / ".hercules" / "cases").exists()


def test_confirming_keeps_the_case_as_a_known_bug_and_closing_makes_it_a_regression_test(tmp_path: Path) -> None:
    project, ledger = _project_with_staged_case(tmp_path)
    ledger.record(_finding(case_path=".hercules/explore/cases/f1.json"))
    kept = ledger.triage("f1", by="ana", state="confirmed")
    assert kept.case == "kept" and kept.case_path == ".hercules/cases/explore-f1.json"
    assert _case_at(project, kept.case_path)["status"] == "known_bug"
    assert not (project / ".hercules" / "explore" / "cases" / "f1.json").exists()
    ledger.triage("f1", by="ana", state="verify_fixed")
    old = project / ".hercules" / "cases" / "explore" / "x_1" / "f2.json"  # where earlier versions wrote a case
    old.parent.mkdir(parents=True)
    old.write_text((project / ".hercules" / "cases" / "explore-f1.json").read_text(encoding="utf-8"), encoding="utf-8")
    ledger.record(_finding("f2", sig="page_error|/|old", case_path=".hercules/cases/explore/x_1/f2.json"))
    assert ledger.triage("f2", by="ana", case="kept").case_path == ".hercules/cases/explore-f2.json"
    assert not (project / ".hercules" / "cases" / "explore").exists()
    closed = ledger.triage("f1", by="ana", state="closed")
    assert closed.case == "regression"
    case = _case_at(project, closed.case_path)
    assert case["status"] == "ready" and "regression" in case["tags"]
    assert [event.after for event in closed.audit if event.field == "case"] == ["kept", "regression"]


def test_dismissing_rejects_the_case_and_a_later_session_does_not_propose_it_again(tmp_path: Path) -> None:
    project, ledger = _project_with_staged_case(tmp_path)
    ledger.record(_finding(case_path=".hercules/explore/cases/f1.json"))
    rejected = ledger.triage("f1", by="ana", state="by_design", reason="the banner script is not ours")
    assert rejected.case == "rejected" and rejected.case_path == ".hercules/explore/cases/rejected/f1.json"
    _project_with_staged_case(tmp_path / "again")  # a later session writes the same case to staging again
    staged = project / ".hercules" / "explore" / "cases" / "f1.json"
    staged.write_text((tmp_path / "again" / "p" / ".hercules" / "explore" / "cases" / "f1.json").read_text(encoding="utf-8"), encoding="utf-8")
    again = ledger.record(_finding("f9", sessions=["x_2"], case_path=".hercules/explore/cases/f1.json"))
    assert again.outcome == "suppressed" and again.finding.case == "rejected" and not staged.exists()


def test_a_person_decides_the_case_directly_and_rejecting_needs_a_reason(tmp_path: Path) -> None:
    project, ledger = _project_with_staged_case(tmp_path)
    ledger.record(_finding(case_path=".hercules/explore/cases/f1.json"))
    with pytest.raises(TriageError, match="why"):
        ledger.triage("f1", by="ana", case="rejected")
    with pytest.raises(TriageError, match="case must be one of"):
        ledger.triage("f1", by="ana", case="maybe")
    with pytest.raises(TriageError, match="not fixed yet"):
        ledger.triage("f1", by="ana", case="regression", reason="guards the checkout")
    ledger.triage("f1", by="ana", state="confirmed")
    ledger.triage("f1", by="ana", state="verify_fixed")
    regression = ledger.triage("f1", by="ana", case="regression", reason="guards the checkout")
    assert regression.state == "verify_fixed" and _case_at(project, regression.case_path)["status"] == "ready"
    back = ledger.triage("f1", by="ana", case="proposed")
    assert back.case_path == ".hercules/explore/cases/f1.json" and _case_at(project, back.case_path)["status"] == "known_bug"


def _reverify_findings(project: Path) -> list[dict[str, Any]]:
    (project / ".hercules" / "cases").mkdir(parents=True, exist_ok=True)
    for name in ("explore-fa.json", "explore-fb.json", "explore-fc.json", "explore-fd.json"):
        (project / ".hercules" / "cases" / name).write_text("{}", encoding="utf-8")
    return [
        {"id": "fa", "title": "kept, now fixed", "state": "confirmed", "case": "kept", "case_path": ".hercules/cases/explore-fa.json"},
        {"id": "fb", "title": "regression, back", "state": "closed", "case": "regression", "case_path": ".hercules/cases/explore-fb.json"},
        {"id": "fc", "title": "proposed only", "state": "new", "case": "proposed", "case_path": ".hercules/explore/cases/fc.json"},
        {"id": "fd", "title": "kept, run broke", "state": "confirmed", "case": "kept", "case_path": ".hercules/cases/explore-fd.json"},
    ]


def _run_reverify(project: Path, outcome: dict[str, str], **kwargs: Any) -> tuple[int, list[str], list[dict[str, Any]], list[tuple[str, dict[str, Any]]], list[float]]:
    from testzeus_hercules.explore.reverify import reverify

    lines: list[str] = []
    results: list[dict[str, Any]] = []
    triaged: list[tuple[str, dict[str, Any]]] = []
    timeouts: list[float] = []

    async def start_run(path: Path) -> str:
        return path.name

    async def wait_run(run_id: str, timeout_s: float) -> str:
        timeouts.append(timeout_s)
        return outcome[run_id]

    async def triage(finding_id: str, body: dict[str, Any]) -> None:
        triaged.append((finding_id, body))

    code = asyncio.run(reverify(_reverify_findings(project), project_dir=project, build="b7", start_run=start_run, wait_run=wait_run, triage=triage, say=lines.append, report=results.append, **kwargs))
    return code, lines, results, triaged, timeouts


def test_reverify_runs_kept_and_regression_cases_and_fails_when_a_fixed_bug_is_back(tmp_path: Path) -> None:
    outcome = {"explore-fa.json": "passed", "explore-fb.json": "failed", "explore-fd.json": "passed"}
    code, lines, results, triaged, timeouts = _run_reverify(tmp_path, outcome, case_timeout_s=120)
    assert code == 1 and lines[0] == "running 3 case(s)…"
    assert "fa: passes now: verify_fixed (kept, now fixed)" in lines and "fb: REGRESSION, the bug is back (regression, back)" in lines
    assert not any(line.startswith("fc") for line in lines)
    assert sorted((fid, body["state"]) for fid, body in triaged) == [("fa", "verify_fixed"), ("fb", "new"), ("fd", "verify_fixed")]
    assert all("build b7" in body["reason"] for _, body in triaged) and timeouts == [120, 120, 120]
    assert {item["finding"]: item["outcome"] for item in results} == {"fa": "verify_fixed", "fb": "regression", "fd": "verify_fixed"}


@pytest.mark.parametrize("status", ["error", "needs_approval", "cancelled", "timeout"])
def test_an_inconclusive_reverify_run_changes_nothing_and_exits_2(tmp_path: Path, status: str) -> None:
    outcome = {"explore-fa.json": "failed", "explore-fb.json": "passed", "explore-fd.json": status}
    code, lines, results, triaged, _ = _run_reverify(tmp_path, outcome)
    assert code == 2
    assert triaged == []  # a kept case still failing and a regression case still passing change nothing either
    assert {item["finding"]: item["outcome"] for item in results} == {"fa": "still_failing", "fb": "still_passes", "fd": "inconclusive"}
    assert any(line.startswith("fd: inconclusive") for line in lines)


def test_reverify_can_be_limited_to_findings_and_says_why_one_is_skipped(tmp_path: Path) -> None:
    code, lines, results, triaged, _ = _run_reverify(tmp_path, {"explore-fa.json": "passed"}, only=["fa", "fc"])
    assert code == 0 and [fid for fid, _ in triaged] == ["fa"]
    assert any(line.startswith("fc: its case is proposed") for line in lines)
    assert {item["finding"]: item["outcome"] for item in results} == {"fa": "verify_fixed", "fc": "skipped"}


def test_cli_reverify_follows_the_daemon_job_and_keeps_its_exit_code(tmp_path: Path) -> None:
    import io

    from testzeus_hercules.cli.explore_cmd import _reverify

    calls: list[tuple[str, str, Any]] = []
    first = {"id": "rv_0000abcd", "finished": False, "lines": ["running 2 case(s)…"]}
    last = {"id": "rv_0000abcd", "finished": True, "lines": ["running 2 case(s)…", "fa: passes now: verify_fixed (a)", "fb: REGRESSION, the bug is back (b)"], "errors": [], "exit_code": 1}

    class Response:
        def __init__(self, data: Any) -> None:
            self.data, self.status_code = data, 200

        def json(self) -> Any:
            return self.data

    class Client:
        async def request(self, method: str, path: str, **kwargs: Any) -> Response:
            calls.append((method, path, kwargs.get("json")))
            return Response(first if method == "POST" else last)

    out = io.StringIO()
    args = argparse.Namespace(build="b7", finding=["fa12"], timeout=300.0, json=False)
    code = asyncio.run(_reverify(args, Client(), tmp_path, out, io.StringIO()))
    assert code == 1 and out.getvalue().splitlines() == last["lines"]
    assert calls[0] == ("POST", "/api/explore/reverify", {"build": "b7", "case_timeout_s": 300.0, "findings": ["fa12"]})
    assert calls[1][1] == "/api/explore/reverify/rv_0000abcd"
    as_json = io.StringIO()
    assert asyncio.run(_reverify(argparse.Namespace(build="b7", finding=None, timeout=900.0, json=True), Client(), tmp_path, as_json, io.StringIO())) == 1
    assert json.loads(as_json.getvalue())["exit_code"] == 1


# --------------------------------------------------------------------------- recon and replanning


def test_recon_gets_enough_time_to_learn_a_slow_site_but_never_most_of_the_session() -> None:
    from testzeus_hercules.explore.coordinator import recon_timebox

    assert recon_timebox(300, 0.1) == 90  # a 5-minute session: 90 s, not 30 s (one move on a real site takes ~12 s)
    assert recon_timebox(120, 0.1) == 48  # never more than 40% of a short session
    assert recon_timebox(7200, 0.1) == 600


def test_new_pages_found_while_exploring_get_charters_and_charters_that_ran_are_not_planned_again(tmp_path: Path) -> None:
    from testzeus_hercules.explore.coordinator import Session

    spec = ExploreSpec.model_validate({"base_url": "http://localhost:8000", "workers": 2, "levers": ["inputs"]})
    session = Session("x_20261114_000000_abcdef", spec, tmp_path, runner=None)  # type: ignore[arg-type]
    session.mission = load_mission("p1", None)
    session.states = {"s1": {"route": "/", "elements": ["textbox Search"]}}
    session._plan()
    first = [charter.id for charter in session.queue]
    assert first and not session._needs_plan()
    session.ran.update(first)
    session.queue.clear()
    assert not session._needs_plan()  # nothing new was seen
    session.states["s2"] = {"route": "/checkout", "elements": ["textbox Coupon"]}
    assert session._needs_plan()
    session._plan()
    assert [charter.id for charter in session.queue] and not set(first) & {charter.id for charter in session.queue}


def test_recon_tries_again_when_the_start_page_does_not_open_and_says_why_it_stopped(tmp_path: Path) -> None:
    from testzeus_hercules.explore.coordinator import Session

    spec = ExploreSpec.model_validate({"base_url": "https://shop.example", "workers": 2, "budget": {"time_s": 600}})
    session = Session("x_20261114_000000_abcdef", spec, tmp_path, runner=None)  # type: ignore[arg-type]
    session.deadline = session.clock() + 600
    attempts: list[str] = []

    async def start(charter: Any, *, explorer: int, judge: bool = True) -> str:
        attempts.append(charter.id)
        session.children[f"r{len(attempts)}"] = Any  # type: ignore[assignment]
        return f"r{len(attempts)}"

    async def wait(run_id: str, **_: Any) -> str:
        return "error"

    def collect(child: Any) -> None:
        session.debriefs.append({"setup_error": "the starting page https://shop.example could not be opened"})

    session._start_explorer = start  # type: ignore[method-assign]
    session._wait = wait  # type: ignore[method-assign]
    session._collect_explorer = collect  # type: ignore[method-assign]
    assert asyncio.run(session._recon()) is False
    assert attempts == ["recon", "recon"]
    assert session.stop_reason == "recon could not start: the starting page https://shop.example could not be opened"


def test_the_same_console_message_on_two_pages_is_one_clue_signature() -> None:
    from testzeus_hercules.explore.oracles import Oracles, TurnStart

    oracles = Oracles(base_url="https://shop.example")
    move = Move(lever="order", variant="visit", text="Open a page")
    start = TurnStart(url="", text="", digest="", records=0)
    record = [("console", {"type": "error", "text": 'Warning: You are importing createRoot from "react-dom"'})]
    home = oracles._code(record, "/", move, start, "https://shop.example/", "", "done")
    enquiry = oracles._code(record, "/bulkenquiry", move, start, "https://shop.example/bulkenquiry", "", "done")
    assert home[0].signature == enquiry[0].signature and enquiry[0].route == "/bulkenquiry"
    errors = [("console", {"type": "pageerror", "text": "boom"})]
    assert oracles._code(errors, "/", move, start, "", "", "done")[0].signature != oracles._code(errors, "/cart", move, start, "", "", "done")[0].signature


def _cli_args(action: str, **values: Any) -> argparse.Namespace:
    defaults = {"explore_action": action, "json": False, "state": None, "case": None, "session": "last", "limit": 10, "tracker": None, "finding": None}
    return argparse.Namespace(**{**defaults, **values})


def _session_file(explore_dir: Path, session_id: str, started: str, **extra: Any) -> None:
    folder = explore_dir / session_id
    folder.mkdir(parents=True)
    view = {
        "id": session_id,
        "status": "finished",
        "started_at": started,
        "elapsed_s": 300,
        "workers": 2,
        "reproducers": 1,
        "spec": {"mission": "p1", "base_url": "https://shop.test", "budget": {"time_s": 300}},
    }
    (folder / "session.json").write_text(json.dumps({**view, **extra}), encoding="utf-8")
    (folder / "report.md").write_text(f"# report {session_id}\n", encoding="utf-8")


def test_read_only_commands_work_from_the_files_when_the_daemon_serves_another_project(tmp_path: Path) -> None:
    import io

    from testzeus_hercules.cli.explore_cmd import _offline

    explore_dir = tmp_path / ".hercules" / "explore"
    ledger = Ledger(explore_dir)
    ledger.record(_finding("f1aaaaaaaaa"))
    ledger.record(_finding("f2bbbbbbbbb", sig="server_error|/profile|5xx", sessions=["x_2"]))
    _session_file(explore_dir, "x_20260101_000000_aaaaaa", "2026-01-01T00:00:00Z")
    _session_file(explore_dir, "x_20260102_000000_bbbbbb", "2026-01-02T00:00:00Z")
    out, err = io.StringIO(), io.StringIO()
    assert _offline(_cli_args("findings"), tmp_path, out, err) == 0
    assert "f1aaaaaaaaa" in out.getvalue() and "wait for a decision" in out.getvalue()
    out = io.StringIO()
    assert _offline(_cli_args("show", finding="f2b"), tmp_path, out, err) == 0
    assert out.getvalue().startswith("f2bbbbbbbbb") and "Decide: hercules explore triage f2bbbbbbbbb" in out.getvalue()
    assert _offline(_cli_args("show", finding="f"), tmp_path, io.StringIO(), err) == 2 and "matches 2 findings" in err.getvalue()
    out = io.StringIO()
    assert _offline(_cli_args("report"), tmp_path, out, err) == 0 and "report x_20260102_000000_bbbbbb" in out.getvalue()
    out = io.StringIO()
    assert _offline(_cli_args("report", session="x_20260101"), tmp_path, out, err) == 0 and "aaaaaa" in out.getvalue()
    out = io.StringIO()
    assert _offline(_cli_args("sessions"), tmp_path, out, err) == 0
    assert out.getvalue().index("bbbbbb") < out.getvalue().index("aaaaaa")


def test_a_finished_session_says_what_to_do_next() -> None:
    import io

    from testzeus_hercules.cli.explore_cmd import _event_line, _next_steps

    out = io.StringIO()
    _next_steps({"findings": [{"id": "f1", "state": "new"}, {"id": "f2", "state": "confirmed"}], "exit_code": 0}, out)
    text = out.getvalue()
    assert "1 of 2 finding(s) wait for your decision" in text and "--gate-on proposed-hard" in text and "hercules explore triage f1 --state confirmed" in text
    out = io.StringIO()
    _next_steps({"findings": []}, out)
    assert "not a pass" in out.getvalue()
    assert "f9" in _event_line({"kind": "finding", "data": {"finding": "f9", "severity": "P2", "outcome": "new", "title": "500"}})
    assert _event_line({"kind": "size", "data": {"by": "ana", "workers": [2, 4]}}) == "  swarm size changed by ana: explorers 2 → 4"


@pytest.mark.parametrize(
    ("base", "address", "opened"),
    [
        ("http://localhost:8000/index.html", "/shop.html", "http://localhost:8000/shop.html"),
        ("http://localhost:8000/index.html", "shop.html", "http://localhost:8000/shop.html"),
        ("http://host/app", "login", "http://host/app/login"),
        ("http://host/app", "/app/cart", "http://host/app/cart"),
        ("http://localhost:8000", "cart.html", "http://localhost:8000/cart.html"),
        ("http://localhost:8000/index.html", "https://other.test/x", "https://other.test/x"),
    ],
)
def test_an_area_opens_where_a_browser_on_the_base_url_would(base: str, address: str, opened: str) -> None:
    from testzeus_hercules.explore.oracles import address_under

    assert address_under(base, address) == opened
