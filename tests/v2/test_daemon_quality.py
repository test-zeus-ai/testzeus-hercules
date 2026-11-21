"""Quality options through the daemon: a11y/visual/fail_on reach the worker, and the result's quality section is served."""

from __future__ import annotations

import json
from pathlib import Path

from tests.v2.fixtures.daemon_support import case_dict, client_for, make_config, submit, wait_for


def _worker_args(config: object, run_id: str) -> list[str]:
    return json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "worker_args.json").read_text())  # type: ignore[attr-defined]


def test_quality_options_reach_the_worker_and_the_result_carries_findings(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict(), a11y=True, visual=True)
        view = wait_for(client, run_id)
        gated = submit(client, test_case=case_dict(), a11y=True, fail_on="serious")
        gated_view = wait_for(client, gated)
        plain = submit(client, test_case=case_dict())
        wait_for(client, plain)
        image = client.get(f"/api/runs/{run_id}/evidence/steps/1/quality/run-a11y.annotated.png", params={"max_side": 256})
        rerun = client.post(f"/api/runs/{run_id}/rerun", json={"scope": "whole"}).json()["run_id"]
        wait_for(client, rerun)

    args = _worker_args(config, run_id)
    assert "--a11y" in args and "--visual" in args and "--quality-fail-on" not in args
    assert _worker_args(config, gated)[_worker_args(config, gated).index("--quality-fail-on") + 1] == "serious"
    assert not {"--a11y", "--visual", "--quality-fail-on"} & set(_worker_args(config, plain))
    assert "--a11y" in _worker_args(config, rerun)  # a re-run keeps the run's options

    quality = view["result"]["quality"]
    assert view["status"] == "passed" and quality["counts"]["serious"] == 1 and quality["counts"]["verified_fixes"] == 1
    assert "Quality: 1 finding(s) on 2 page(s) (1 serious)" in view["summary"]
    assert gated_view["status"] == "failed" and gated_view["result"]["quality"]["fail_on"] == "serious"
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"


def test_fail_on_needs_an_audit_and_a_known_impact(tmp_path: Path) -> None:
    with client_for(make_config(tmp_path)) as client:
        alone = client.post("/api/runs", json={"test_case": case_dict(), "fail_on": "serious"})
        unknown = client.post("/api/runs", json={"test_case": case_dict(), "a11y": True, "fail_on": "blocker"})
    assert alone.status_code == 422 and "fail_on needs a11y or visual" in alone.text
    assert unknown.status_code == 422


def test_quality_step_payload_is_accepted_and_validated(tmp_path: Path) -> None:
    case = case_dict()
    case["steps"].append({"action": "The checkout looks right", "quality": {"kind": "visual", "baseline": "checkout", "mask": [".clock"]}})
    bad = case_dict()
    bad["steps"].append({"action": "Broken", "quality": {"kind": "a11y", "baseline": "x"}})
    with client_for(make_config(tmp_path)) as client:
        run_id = submit(client, test_case=case)
        wait_for(client, run_id)
        refused = client.post("/api/runs", json={"test_case": bad})
        stored = client.get(f"/api/runs/{run_id}/case").json()["test_case"]
    assert {key: stored["steps"][-1]["quality"][key] for key in ("kind", "baseline", "mask")} == {"kind": "visual", "baseline": "checkout", "mask": [".clock"]}
    assert refused.status_code == 422 and "baseline: not used by a11y checks" in refused.text
