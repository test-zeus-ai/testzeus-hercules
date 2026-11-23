"""``hercules run``: hybrid by default with the key file, the no-key notice, Clef in pure mode, one mode line."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

from testzeus_hercules.cli.keys import write_key_file
from testzeus_hercules.cli.main import main
from testzeus_hercules.kernel.telemetry import TELEMETRY_NOTICE

from tests.v2.clef_fakes import FakeClef
from tests.v2.test_cli_run import Harness
from tests.v2.test_cli_run_llm import DECISION, KEY, LLM_ENV, MODEL, FakeJev, _case, _decisions, _server, _with_jev

SECRET = "sk-or-test-not-a-real-key-456"


def _with_clef(h: Harness, clef: FakeClef) -> Harness:
    h.deps = replace(h.deps, factory=replace(h.deps.factory, clef_http=clef.client))
    return h


def test_without_a_key_the_default_run_is_pure_with_one_notice_line(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=_server("ok", DECISION), env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path))

    assert code == 0, out
    notices = [line for line in out.splitlines() if line.startswith("notice:")]
    assert len(notices) == 1
    assert "no" in notices[0] and "hercules setup" in notices[0] and "pure" in notices[0]
    assert "error" not in out.lower()


def test_the_mode_line_is_printed_once(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=_server("ok", DECISION), env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path))

    assert code == 0, out
    assert sum(line.startswith("Mode:") for line in out.splitlines()) == 1
    assert "Decisions: local-llm 1" in out


def test_a_key_in_the_private_key_file_makes_the_default_run_hybrid(tmp_path: Path) -> None:
    jev = FakeJev()
    h = _with_jev(Harness(tmp_path, server=_server("ok"), env=LLM_ENV), jev)
    write_key_file(tmp_path / "home", SECRET)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path))

    assert code == 0, out
    key_file = tmp_path / "home" / "openrouter.env"
    assert f"Mode: hybrid [auto: OPENROUTER_API_KEY is read from {key_file} and egress policy is 'hybrid'" in out
    assert str(key_file) in out and out.count(str(key_file)) == 1, "the key file is named once, not again as '(key from ...)'"
    assert "notice:" not in out
    assert SECRET not in out and SECRET not in h.err.getvalue()
    run_dir = next((h.project / ".hercules" / "runs").iterdir())
    assert all(SECRET not in path.read_text(errors="ignore") for path in run_dir.rglob("*") if path.is_file())


def test_hybrid_runs_offer_every_candidate_not_the_local_token_budget(tmp_path: Path) -> None:
    from tests.v2.engine_fixtures.fakes import FakeElement, FakePerceiver, FakeSnapshot, login_elements

    filler = tuple(FakeElement(md=100 + n, role="link", name=f"Help article number {n} " + "with a long descriptive title " * 3) for n in range(60))
    jev = FakeJev()
    h = _with_jev(Harness(tmp_path, server=_server("ok"), env={**LLM_ENV, **KEY}, perceiver=FakePerceiver(FakeSnapshot(seq=0, elements=login_elements() + filler))), jev)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--mode", "hybrid")

    assert code == 0, out
    rows = jev.requests[0].state["rows"]
    assert len(rows) == len(login_elements()) + 60, "JEV weighs every element; the local tier budget does not trim it"


def test_pure_mode_uses_a_reachable_local_clef_server(tmp_path: Path) -> None:
    clef = FakeClef()
    h = _with_clef(Harness(tmp_path, server=_server("ok"), env={**LLM_ENV, "HERCULES_CLEF_URL": "http://127.0.0.1:30000"}), clef)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--mode", "pure", "--json")

    assert code == 0, out + h.err.getvalue()
    err = h.err.getvalue()
    assert "Clef Cloudflare/clef-flash at http://127.0.0.1:30000/v1/systemone decides" in err
    assert "Clef calls: 1" in err
    payload = json.loads(out)
    assert payload["engines"]["mode"] == "pure"
    assert payload["engines"]["decider_model"] == "Cloudflare/clef-flash"
    assert (payload["engines"]["clef_calls"], payload["engines"]["jev_calls"]) == (1, 0), "Clef decisions are not counted as JEV calls"
    record = _decisions(h, 2)[0]
    assert record["engine"] == "clef" and record["route"]["calls"][0]["images"] is False
    assert len(clef.requests) == 2, "one health check and one decision"


def test_decider_backend_clef_falls_back_to_the_local_model_when_clef_is_down(tmp_path: Path) -> None:
    clef = FakeClef(reachable=False)
    h = _with_clef(Harness(tmp_path, server=_server("ok", DECISION), env={**LLM_ENV, "HERCULES_DECIDER_BACKEND": "clef"}), clef)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path))

    assert code == 0, out
    assert "Mode: pure [decider.backend: clef]" in out
    assert "note: Clef server http://127.0.0.1:30000/v1/systemone is not usable" in out
    assert "Decisions: local-llm 1" in out


def test_the_telemetry_notice_is_shown_once_per_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = {"HERCULES_HOME": str(home)}
    first, second = io.StringIO(), io.StringIO()

    assert main(["version"], env=env, stdout=io.StringIO(), stderr=first) == 0
    assert main(["version"], env=env, stdout=io.StringIO(), stderr=second) == 0

    assert TELEMETRY_NOTICE in first.getvalue()
    assert second.getvalue() == ""
    assert (home / "telemetry_notice_shown").is_file()


def test_no_telemetry_notice_when_telemetry_is_off_or_for_protocol_commands(tmp_path: Path) -> None:
    err = io.StringIO()
    main(["version"], env={"HERCULES_HOME": str(tmp_path / "a"), "HERCULES_TELEMETRY": "0"}, stdout=io.StringIO(), stderr=err)

    assert err.getvalue() == ""
    assert not (tmp_path / "a" / "telemetry_notice_shown").exists()


def test_model_is_still_named_in_pure_runs(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=_server("ok", DECISION), env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    _, out = h.run(str(path))

    assert MODEL in out
