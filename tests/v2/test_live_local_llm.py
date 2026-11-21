"""Opt-in: the login scenario through ``hercules run`` with a real local model server.

Skipped unless ``HERCULES_LIVE_LLM=1``. The model server comes from the usual
settings variables (``HERCULES_LLM_PROVIDER``, default ``mlx-vlm``;
``HERCULES_LLM_BASE_URL``; ``HERCULES_LLM_MODEL``; and for any OpenAI-compatible
endpoint ``HERCULES_LLM_API_KEY_ENV``, ``HERCULES_LLM_REQUEST_PARAMS``, ``HERCULES_LLM_HEADERS``).
Pure mode keeps the egress policy ``local`` for a local server; a hosted endpoint runs
under ``hybrid-text`` (text only). The hybrid test also needs ``OPENROUTER_API_KEY`` and sends page
text (never screenshots) to JEV. Run serially (``-p no:xdist``): local servers
serve few sequences at once. Timings are printed; use ``-s`` to see them.
"""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.gherkin import compile_feature
from testzeus_hercules.cli.main import main

from testzeus_hercules.llm.providers import PROVIDERS, is_local_url

from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.test_engine_e2e import SCENARIO, SECRET

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("HERCULES_LIVE_LLM") != "1", reason="set HERCULES_LIVE_LLM=1 to run against a real local model server"),
]

_PASSTHROUGH = (
    "HERCULES_LLM_PROVIDER",
    "HERCULES_LLM_BASE_URL",
    "HERCULES_LLM_MODEL",
    "HERCULES_LLM_TIER",
    "HERCULES_LLM_TIMEOUT_S",
    "HERCULES_LLM_API_KEY_ENV",
    "HERCULES_LLM_REQUEST_PARAMS",
    "HERCULES_LLM_HEADERS",
    "HERCULES_LLM_CACHE",
    "HOME",
    "PATH",
    "OPENROUTER_API_KEY",
)


def _env(tmp_path: Path, chromium: str) -> dict[str, str]:
    env = {name: os.environ[name] for name in _PASSTHROUGH if os.environ.get(name)}
    env.setdefault("HERCULES_LLM_PROVIDER", "mlx-vlm")
    key_env = env.get("HERCULES_LLM_API_KEY_ENV")
    if key_env and os.environ.get(key_env):
        env[key_env] = os.environ[key_env]
    for variable in json.loads(env.get("HERCULES_LLM_HEADERS") or "{}").values():
        if os.environ.get(variable):
            env[variable] = os.environ[variable]
    # Any OpenAI-compatible endpoint: a local server keeps everything on the machine; a hosted one needs text egress.
    base_url = env.get("HERCULES_LLM_BASE_URL") or PROVIDERS[env["HERCULES_LLM_PROVIDER"]].default_base_url or ""
    egress = "local" if is_local_url(base_url) else "hybrid-text"
    env.update(HERCULES_HOME=str(tmp_path / "home"), HERCULES_TELEMETRY="0", HERCULES_EGRESS=egress, HERCULES_CHROMIUM=chromium, LOGIN_PW=SECRET)
    return env


# "Log in" names no control on the page (its button says "Sign in"): the model has to see that they are the same.
ESCALATING_SCENARIO = SCENARIO.replace('I click the "Sign in" button', 'I click the "Log in" button')


def _run(tmp_path: Path, mode_args: list[str], scenario: str = SCENARIO) -> tuple[int, dict[str, Any], Path, str, float]:
    chromium = require_chromium()
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    case = compile_feature(scenario.format(expected="Welcome"), base_url="http://127.0.0.1:1", test_data={"password": {"secret": "LOGIN_PW"}})  # type: ignore[dict-item]
    path = tmp_path / "login.json"
    path.write_text(case.model_dump_json(), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with serve_fixtures() as base_url:
        started = time.monotonic()
        code = main(["run", str(path), *mode_args, "--base-url", base_url, "--json", "--project", str(project)], env=_env(tmp_path, chromium), stdout=out, stderr=err)
        wall_s = time.monotonic() - started
    payload = json.loads(out.getvalue()) if out.getvalue().strip().startswith("{") else {}
    run_dir = project / ".hercules" / "runs" / payload["run_id"] if payload else project
    log = err.getvalue() if payload else err.getvalue() + out.getvalue()
    return code, payload, run_dir, log, wall_s


def _decisions(run_dir: Path) -> dict[int, list[dict[str, Any]]]:
    found = {}
    for path in sorted((run_dir / "steps").glob("*/decisions.jsonl")):
        found[int(path.parent.name)] = [json.loads(line) for line in path.read_text().splitlines()]
    return found


def _report(decider: str, payload: dict[str, Any], decisions: dict[int, list[dict[str, Any]]], log: str, wall_s: float) -> None:
    print(f"\n[{decider}] {payload.get('status')} in {wall_s:.1f} s wall ({payload.get('duration_s')} s run)")
    print(log.strip())
    for step in payload.get("steps", []):
        made = decisions.get(step["index"], [])
        detail = ", ".join(f"{record['engine']} {record['latency_ms']} ms conf {record['confidence']:.2f} rows {record['rows']} ~{record['prompt_tokens_est']} tok" for record in made)
        print(f"  step {step['index']} {step['status']:<8} {step['duration_ms']:>6} ms  {step['action'][:40]:<40} {detail}")


def _assert_passed(code: int, payload: dict[str, Any], run_dir: Path, log: str) -> None:
    assert code == 0, log
    assert payload["status"] == "passed", log
    assert [step["status"] for step in payload["steps"]] == ["passed"] * 5
    for path in run_dir.rglob("*"):
        if path.suffix in (".txt", ".jsonl", ".json"):
            assert SECRET not in path.read_text(), path


def test_login_in_pure_mode(tmp_path: Path) -> None:
    code, payload, run_dir, log, wall_s = _run(tmp_path, ["--mode", "pure"])
    decisions = _decisions(run_dir)
    _report("pure", payload, decisions, log, wall_s)

    _assert_passed(code, payload, run_dir, log)
    assert payload["engines"]["mode"] == "pure"
    assert payload["engines"]["decider"] == "local-llm"
    assert payload["engines"]["llm"]
    engines = {record["engine"] for records in decisions.values() for record in records}
    assert engines == {"local-llm"}


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="hybrid mode needs OPENROUTER_API_KEY")
def test_login_in_hybrid_mode_with_text_egress(tmp_path: Path) -> None:
    code, payload, run_dir, log, wall_s = _run(tmp_path, ["--mode", "hybrid", "--egress", "hybrid-text"])
    decisions = _decisions(run_dir)
    _report("hybrid", payload, decisions, log, wall_s)

    _assert_passed(code, payload, run_dir, log)
    assert payload["engines"]["mode"] == "hybrid"
    assert payload["cost"]["usd"] is not None
    for records in decisions.values():
        for record in records:
            assert record["route"]["answered_by"] == record["engine"]
            assert all(call["images"] is False for call in record["route"]["calls"])
