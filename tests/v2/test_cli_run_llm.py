"""``hercules run`` in pure and hybrid mode, against an in-process fake model server and a fake JEV."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from testzeus_hercules.decide.contract import ChoiceAnswer, Decision, DecisionRequest, NoulAnswer
from testzeus_hercules.decide.errors import DecisionError, DecisionUnavailable
from testzeus_hercules.kernel.settings import Settings

from tests.v2.engine_fixtures.fakes import FakeActions, FakeElement, FakePerceiver, FakeResult, FakeSnapshot, login_elements
from tests.v2.test_cli_run import LLM_ENV, MODEL, Harness, model_server
from tests.v2.test_llm_fakes import FakeServer

# A local-model decision reply picking md_6 ("Sign in" on the login page), for tests that script replies.
DECISION = json.dumps({"target": {"probabilities": {"md_6": 0.92, "no_match": 0.08}}, "target_absent": {"yes_probability": 0.05}})


def _case(tmp_path: Path, h: Harness, *steps: str, base_url: str = "https://app.test") -> Path:
    case = {"id": "c1", "title": "Login", "environment": {"base_url": base_url}, "steps": [{"action": step} for step in steps]}
    return h.write("case.json", json.dumps(case))


def _server(*scripted: Any) -> FakeServer:
    """The fake local model (it reads the steps and decides); ``scripted`` replies are served first."""
    return model_server(None, *scripted)


KEY = {"OPENROUTER_API_KEY": "test-key"}
JEV_MODEL = "typesafe/jev-1.14"


class FakeJev:
    """Stands in for the JEV backend: picks md_6, or raises the scripted errors first."""

    engine = "jev-openrouter"
    model = JEV_MODEL

    def __init__(self, *errors: DecisionError, confidence: float = 0.93, cost: float = 0.001) -> None:
        self.errors = list(errors)
        self.confidence = confidence
        self.cost = cost
        self.requests: list[DecisionRequest] = []
        self.closed = False

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        self.requests.append(request)
        if self.errors:
            raise self.errors.pop(0)
        options = request.questions["target"].criteria
        rest = (1 - self.confidence) / (len(options) - 1)
        answers: dict[str, Any] = {
            "target": ChoiceAnswer(choice="md_6", confidence=self.confidence, probabilities={name: (self.confidence if name == "md_6" else rest) for name in options}),
            "target_absent": NoulAnswer(noul=0.02),
        }
        return Decision(answers=answers, engine=self.engine, model_version=JEV_MODEL, latency_ms=210, cost_usd=self.cost)

    async def aclose(self) -> None:
        self.closed = True


def _with_jev(h: Harness, jev: FakeJev) -> Harness:
    def make_jev(settings: Settings, key_env: str, env: Mapping[str, str]) -> FakeJev:
        return jev

    h.deps = replace(h.deps, factory=replace(h.deps.factory, make_jev=make_jev))
    return h


def _decisions(h: Harness, step: int) -> list[dict[str, Any]]:
    run_dir = next((h.project / ".hercules" / "runs").iterdir())
    return [json.loads(line) for line in (run_dir / "steps" / str(step) / "decisions.jsonl").read_text().splitlines()]


def test_auto_without_a_jev_key_runs_pure_and_says_why(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=_server(), env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path))

    assert code == 0, out
    assert "Mode: pure [auto: OPENROUTER_API_KEY is not set]" in out
    assert MODEL in out
    assert "Decisions: local-llm 1" in out
    assert "Local model calls: 2" in out, "one reading of the whole case and one decision"
    assert "JEV calls" not in out


def test_pure_mode_records_engine_model_and_latency(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=_server(), env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--mode", "pure", "--json")

    assert code == 0, out + h.err.getvalue()
    payload = json.loads(out)
    assert payload["engines"]["mode"] == "pure"
    assert payload["engines"]["decider"] == "local-llm"
    assert payload["engines"]["decider_model"] == MODEL
    assert payload["cost"]["usd"] is None
    record = _decisions(h, 2)[0]
    assert record["engine"] == "local-llm" and record["model"] == MODEL
    assert isinstance(record["latency_ms"], int)
    assert "Mode: pure" in h.err.getvalue()


def test_hybrid_mode_decides_with_jev_and_reports_cost(tmp_path: Path) -> None:
    jev = FakeJev()
    h = _with_jev(Harness(tmp_path, server=_server(), env={**LLM_ENV, **KEY}), jev)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"', 'click "Sign in"')

    code, out = h.run(str(path), "--mode", "hybrid", "--egress", "hybrid-text", "--json")

    assert code == 0, out + h.err.getvalue()
    payload = json.loads(out)
    assert payload["engines"]["mode"] == "hybrid"
    assert payload["engines"]["egress"] == "hybrid-text"
    assert payload["engines"]["decider_model"] == JEV_MODEL
    assert payload["engines"]["jev_calls"] == 2
    assert payload["cost"]["usd"] == 0.002
    assert all(not request.has_images() for request in jev.requests)
    record = _decisions(h, 2)[0]
    assert (record["engine"], record["model"], record["cost_usd"], record["fallback"]) == ("jev-openrouter", JEV_MODEL, 0.001, None)
    assert record["route"]["calls"][0]["images"] is False
    err = h.err.getvalue()
    assert "Mode: hybrid [--mode hybrid]" in err and "text only" in err
    estimate = sum(request.text_tokens_estimate() for request in jev.requests)
    assert f"JEV calls: 2, ~{estimate:,} prompt tokens (estimated), cost $0.0020" in err
    assert payload["cost"]["decision_tokens_est"] == estimate
    assert jev.closed


def test_auto_with_key_and_text_egress_is_hybrid(tmp_path: Path) -> None:
    h = _with_jev(Harness(tmp_path, server=_server(), env={**LLM_ENV, **KEY}), FakeJev())
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--egress", "hybrid-text")

    assert code == 0, out
    assert "Mode: hybrid [auto: OPENROUTER_API_KEY is set and egress policy is 'hybrid-text']" in out
    assert "Decisions: jev-openrouter 1" in out


def test_hybrid_falls_back_to_the_local_model_when_jev_is_down(tmp_path: Path) -> None:
    jev = FakeJev(DecisionUnavailable("JEV unavailable for 'target' after 3 attempt(s): timed out after 20 s"))
    h = _with_jev(Harness(tmp_path, server=_server(), env={**LLM_ENV, **KEY}), jev)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--mode", "hybrid", "--egress", "hybrid-text", "--json")

    assert code == 0, out + h.err.getvalue()
    payload = json.loads(out)
    assert payload["engines"]["fallbacks"] == 1
    record = _decisions(h, 2)[0]
    assert record["engine"] == "local-llm"
    assert "timed out" in record["fallback"]
    assert "local fallback 1" in h.err.getvalue()


def test_hybrid_with_screenshot_egress_reasks_with_the_image_when_unsure(tmp_path: Path) -> None:
    jev = FakeJev(confidence=0.3)
    h = _with_jev(Harness(tmp_path, server=_server(), env={**LLM_ENV, **KEY, "HERCULES_LLM_PROVIDER": "mlx-vlm"}), jev)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    h.run(str(path), "--mode", "hybrid", "--egress", "hybrid", "--json")

    assert [request.has_images() for request in jev.requests[:2]] == [False, True]
    record = _decisions(h, 2)[0]
    assert [call["images"] for call in record["route"]["calls"]] == [False, True]


def test_hybrid_mode_with_local_egress_exits_2_with_a_clear_message(tmp_path: Path) -> None:
    server = _server()
    h = Harness(tmp_path, server=server, env={**LLM_ENV, **KEY, "HERCULES_EGRESS": "local"})
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--mode", "hybrid")

    assert code == 2
    assert "--egress hybrid-text" in out
    assert server.requests == []


def test_hybrid_mode_without_the_key_exits_2(tmp_path: Path) -> None:
    h = Harness(tmp_path, server=_server(), env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--mode", "hybrid", "--egress", "hybrid-text", "--jev-key-env", "MY_JEV_KEY")

    assert code == 2
    assert "MY_JEV_KEY" in out


def test_no_model_server_exits_2_and_points_to_setup_and_doctor(tmp_path: Path) -> None:
    h = Harness(tmp_path, env=LLM_ENV, server=FakeServer(kind="generic", reachable=False))
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--mode", "pure")

    assert code == 2
    assert "not reachable" in out and "hercules doctor" in out and "hercules setup" in out
    assert "--decider" not in out


def test_the_stub_test_double_is_announced_as_such(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--decider", "stub", "--json")

    assert code == 2, "without a step reader a stub run cannot read its steps"
    assert json.loads(out)["engines"]["mode"] == "test"
    assert "a test double" in h.err.getvalue()


def test_the_heuristic_decider_and_the_rules_only_flag_are_gone(tmp_path: Path) -> None:
    import pytest

    h = Harness(tmp_path)
    path = _case(tmp_path, h, "Open /login")
    for flag in (["--decider", "heuristic"], ["--no-llm-interpreter"]):
        with pytest.raises(SystemExit):
            h.run(str(path), *flag)


def test_remote_endpoint_under_local_egress_exits_2(tmp_path: Path) -> None:
    server = _server()
    h = Harness(tmp_path, server=server, env={"HERCULES_LLM_PROVIDER": "openrouter", "HERCULES_EGRESS": "local", "OPENROUTER_API_KEY": "k"})
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--mode", "pure")

    assert code == 2
    assert "egress policy 'local'" in out
    assert server.requests == []


def test_base_url_flag_overrides_the_case_environment(tmp_path: Path) -> None:
    actions = FakeActions(text="Welcome, alice")
    h = Harness(tmp_path, actions=actions)
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--base-url", "http://127.0.0.1:9999")

    assert code == 0, out
    assert actions.calls[0] == ("open_url", "/login", "http://127.0.0.1:9999")


def test_invalid_base_url_exits_2(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--base-url", "not a url")

    assert code == 2
    assert "base-url" in out


def test_max_steps_stops_the_run_early(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"', 'click "Sign in"')

    code, out = h.run(str(path), "--max-steps", "1", "--json")

    payload = json.loads(out)
    assert code == 1
    assert [step["status"] for step in payload["steps"]] == ["passed", "skipped", "skipped"]
    assert any("--max-steps" in hint for hint in payload["hints"])


class SlowActions(FakeActions):
    async def open_url(self, page: Any, url: str, *, base_url: str | None = None, timeout_ms: int = 0, file_roots: Any = None) -> FakeResult:
        await asyncio.sleep(5)
        return await super().open_url(page, url, base_url=base_url)


def test_step_timeout_flag_bounds_each_step(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=SlowActions())
    path = _case(tmp_path, h, "Open /login")

    code, out = h.run(str(path), "--step-timeout-s", "0.3", "--json")

    assert code == 2
    assert "timed out after 0.3 s" in json.loads(out)["steps"][0]["actual"]


def test_the_whole_case_is_read_by_the_model_in_one_call_before_the_steps(tmp_path: Path) -> None:
    from tests.v2.fixtures.fake_model import FakeModel

    model = FakeModel()
    server = model_server(model)
    h = Harness(tmp_path, server=server, env=LLM_ENV, model=model)
    path = _case(tmp_path, h, "Open /login", "Proceed by submitting the sign in form")
    model.add("Proceed by submitting the sign in form", {"kind": "click", "target": "Sign in", "value": None, "url": None, "expected": None, "match": None, "role": "button"})

    code, out = h.run(str(path), "--mode", "pure")

    assert code == 0, out
    names = [body.get("response_format", {}).get("json_schema", {}).get("name") for body in server.chat_requests()]
    assert names[:2] == [None, "CaseReadingReply"], "warm-up, then one reading of every step"
    assert "IntentPlan" not in names, "no step is read again on its own"
    reading = next(body for body in server.chat_requests() if body.get("response_format", {}).get("json_schema", {}).get("name") == "CaseReadingReply")
    action = reading["response_format"]["json_schema"]["schema"]["properties"]["step_2_action"]["items"]
    shapes = {tuple(sorted(member["properties"]["kind"].get("enum") or [member["properties"]["kind"]["const"]])): member for member in action["anyOf"]}
    assert all(set(member["required"]) == set(member["properties"]) for member in shapes.values()), "every field of a kind is required so constrained decoding cannot drop the value"
    assert set(shapes[("fill", "select", "upload")]["required"]) == {"kind", "target", "value"}, "an action carries only the fields its kind uses"
    cache = list((h.project / ".hercules" / "cache" / "interpret").glob("reading-*.json"))
    assert len(cache) == 1


def test_a_second_run_of_the_same_case_reuses_the_cached_reading(tmp_path: Path) -> None:
    server = _server()
    h = Harness(tmp_path, server=server, env=LLM_ENV)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    assert h.run(str(path), "--mode", "pure")[0] == 0
    first = len(server.chat_requests())
    code, out = h.run(str(path), "--mode", "pure")

    assert code == 0, out
    names = [body.get("response_format", {}).get("json_schema", {}).get("name") for body in server.chat_requests()[first:]]
    assert "CaseReadingReply" not in names
    readings = sorted(json.loads((run_dir / "steps" / "2" / "plan.jsonl").read_text().splitlines()[0])["reading"] for run_dir in (h.project / ".hercules" / "runs").iterdir())
    assert readings == ["cache", "model"]


def test_every_candidate_goes_to_the_local_model_too(tmp_path: Path) -> None:
    filler = tuple(FakeElement(md=100 + n, role="link", name=f"Help article number {n} " + "with a long descriptive title " * 3) for n in range(60))
    perceiver = FakePerceiver(FakeSnapshot(seq=0, elements=login_elements() + filler))
    h = Harness(tmp_path, server=_server(), env=LLM_ENV, perceiver=perceiver)
    path = _case(tmp_path, h, "Open /login", 'click "Sign in"')

    code, out = h.run(str(path), "--mode", "pure")

    assert code == 0, out
    record = _decisions(h, 2)[0]
    assert record["rows"] == len(login_elements()) + 60, "nothing is trimmed by matching words; a long pick is split by the decider"
    assert record["choice"] == "md_6"


def test_pure_mode_gives_each_step_longer_unless_the_run_sets_a_limit() -> None:
    from testzeus_hercules.cli.run import PURE_STEP_TIMEOUT_S, step_limits

    assert step_limits("hybrid", None).step_timeout_s == 120.0
    assert step_limits("pure", None).step_timeout_s == PURE_STEP_TIMEOUT_S == 300.0
    assert step_limits("pure", 45.0).step_timeout_s == 45.0
