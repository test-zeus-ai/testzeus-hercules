"""Daemon REST API: run input validation, SSE replay, evidence confinement, cases, environments, models."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from tests.v2.fixtures.daemon_support import TOKEN, case_dict, client_for, isolated_env, make_config, submit, wait_for

FEATURE = """Feature: Sign in
  Scenario: Shopper signs in
    Given I open "/login.html"
    When I click the "Sign in" button
    Then I should see "Welcome"
"""


@pytest.fixture()
def config(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_config(tmp_path)


# --------------------------------------------------------------------------- runs input


def test_gherkin_run_compiles_the_feature(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.post("/api/runs", json={"gherkin": FEATURE, "base_url": "http://127.0.0.1:9"})
        body = wait_for(client, response.json()["run_id"])
    assert response.status_code == 201
    assert body["case_id"] == "shopper-signs-in"
    assert body["total_steps"] == 3


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "exactly one"),
        ({"test_case": case_dict(), "gherkin": FEATURE}, "exactly one"),
        ({"test_case": {"id": "x"}}, "steps"),
        ({"gherkin": "Feature: x\n  Scenario: a\n"}, "no steps"),
        ({"gherkin": FEATURE, "base_url": "javascript:alert(1)"}, "base_url"),
        ({"test_case": case_dict(), "mode": "turbo"}, "mode"),
        ({"test_case": case_dict(), "decider": "oracle"}, "decider"),
        ({"test_case": case_dict(), "surprise": 1}, "surprise"),
        ({"case_path": "../../etc/passwd"}, "inside the project"),
        ({"case_path": "/etc/hosts"}, "inside the project"),
        ({"case_path": ".hercules/cases/missing.json"}, "not found"),
        ({"test_case": case_dict(), "environment": "nope"}, "unknown environment"),
    ],
)
def test_bad_run_requests_are_422(config, payload, message: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.post("/api/runs", json=payload)
    assert response.status_code == 422, response.text
    assert message in response.text


def test_non_json_body_is_rejected(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.post("/api/runs", content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_environment_fills_the_base_url(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        assert client.post("/api/environments", json={"name": "local", "base_url": "http://127.0.0.1:8123", "viewport": "1024x700"}).status_code == 201
        case = case_dict()
        case["environment"] = {"ref": "local"}
        run_id = submit(client, test_case=case)
        wait_for(client, run_id)
    written = json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "case.json").read_text())
    assert written["environment"]["base_url"] == "http://127.0.0.1:8123"
    assert written["environment"]["viewport"] == "1024x700"


def test_base_url_override_wins(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict(), base_url="http://127.0.0.1:5555")
        wait_for(client, run_id)
    written = json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "case.json").read_text())
    assert written["environment"]["base_url"] == "http://127.0.0.1:5555"


# --------------------------------------------------------------------------- SSE


def _parse_sse(text: str) -> list[dict[str, object]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields: dict[str, object] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if "id" in fields:
            fields["data"] = json.loads(str(fields["data"]))
            events.append(fields)
    return events


def test_sse_streams_all_events_then_closes(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, "fake-slow")
        with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            text = "".join(response.iter_text())
    events = _parse_sse(text)
    ids = [int(str(event["id"])) for event in events]
    assert ids == list(range(1, len(ids) + 1))
    assert events[-1]["event"] == "status"
    assert events[-1]["data"]["status"] == "passed"  # type: ignore[index]


def test_sse_replays_after_last_event_id(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client)
        wait_for(client, run_id)
        full = _parse_sse(client.get(f"/api/runs/{run_id}/events").text)
        tail = _parse_sse(client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "3"}).text)
        query = _parse_sse(client.get(f"/api/runs/{run_id}/events?after=3").text)
        bad = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "abc"})
    assert [event["id"] for event in tail] == [event["id"] for event in full[3:]]
    assert query == tail
    assert bad.status_code == 422


# --------------------------------------------------------------------------- evidence


@pytest.fixture()
def failed_run(config):  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, "fake-fail")
        wait_for(client, run_id)
        yield client, run_id


def test_evidence_serves_files_with_content_types(failed_run, config) -> None:  # type: ignore[no-untyped-def]
    client, run_id = failed_run
    image = client.get(f"/api/runs/{run_id}/evidence/steps/2/plain.jpg")
    text = client.get(f"/api/runs/{run_id}/evidence/steps/2/observation.txt")
    run_json = client.get(f"/api/runs/{run_id}/evidence/run.json")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert text.headers["content-type"].startswith("text/plain")
    assert run_json.headers["content-type"].startswith("application/json")
    assert json.loads(run_json.text)["status"] == "failed"


def test_evidence_can_be_downscaled(failed_run) -> None:  # type: ignore[no-untyped-def]
    client, run_id = failed_run
    response = client.get(f"/api/runs/{run_id}/evidence/steps/2/som.jpg?max_side=640")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(response.content)).size) == 640
    assert client.get(f"/api/runs/{run_id}/evidence/steps/2/som.jpg?max_side=5").status_code == 422


def test_a_recording_named_by_playwright_is_served_and_can_be_seeked(failed_run, config) -> None:  # type: ignore[no-untyped-def]
    client, run_id = failed_run
    video_dir = config.settings.project_dir / ".hercules" / "runs" / run_id / "video"
    video_dir.mkdir()
    (video_dir / "page@63e5ae63961d94690f8734bf0192136f.webm").write_bytes(b"\x1aE\xdf\xa3" + bytes(range(256)) * 4)
    path = f"/api/runs/{run_id}/evidence/video/page@63e5ae63961d94690f8734bf0192136f.webm"

    whole = client.get(path)
    part = client.get(path, headers={"Range": "bytes=100-199"})

    assert whole.status_code == 200 and whole.headers["content-type"] == "video/webm" and len(whole.content) == 1028
    assert whole.headers["accept-ranges"] == "bytes"
    assert part.status_code == 206 and len(part.content) == 100 and part.content == whole.content[100:200]
    assert part.headers["content-range"] == "bytes 100-199/1028"
    assert client.get(f"/api/runs/{run_id}/evidence/video/@page.webm").status_code == 404  # still starts with a letter or digit


@pytest.mark.parametrize(
    "path",
    [
        "../../../etc/passwd",
        "..%2f..%2f..%2fetc%2fpasswd",
        "steps/../../other/run.json",
        "steps/2/.hidden",
        "steps/2/a%00b.txt",
        "steps//2/plain.jpg",
        "/etc/passwd",
        "steps/2/missing.jpg",
        "steps/2",
    ],
)
def test_evidence_paths_are_confined(failed_run, path: str) -> None:  # type: ignore[no-untyped-def]
    client, run_id = failed_run
    response = client.get(f"/api/runs/{run_id}/evidence/{path}")
    assert response.status_code in (400, 404), (path, response.status_code)
    assert "root:" not in response.text


def test_evidence_refuses_symlinks(failed_run, config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client, run_id = failed_run
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    run_dir = config.settings.project_dir / ".hercules" / "runs" / run_id
    (run_dir / "link.txt").symlink_to(secret)
    (run_dir / "steps" / "linkdir").symlink_to(tmp_path)
    inside = run_dir / "inside.txt"
    inside.write_text("fine")
    (run_dir / "inside-link.txt").symlink_to(inside)

    for path in ("link.txt", "steps/linkdir/secret.txt", "inside-link.txt"):
        response = client.get(f"/api/runs/{run_id}/evidence/{path}")
        assert response.status_code == 404, path
        assert "top secret" not in response.text
    assert client.get(f"/api/runs/{run_id}/evidence/inside.txt").text == "fine"


def test_evidence_of_a_symlinked_run_dir_is_refused(config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "run.json").write_text("{}")
    with client_for(config) as client:
        run_id = submit(client)
        wait_for(client, run_id)
        run_dir = config.settings.project_dir / ".hercules" / "runs" / run_id
        for child in sorted(run_dir.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        run_dir.rmdir()
        run_dir.symlink_to(elsewhere)
        assert client.get(f"/api/runs/{run_id}/evidence/run.json").status_code == 404


# --------------------------------------------------------------------------- cases and environments


def test_cases_save_list_and_get(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        saved = client.post("/api/cases", json={"test_case": case_dict()})
        duplicate = client.post("/api/cases", json={"test_case": case_dict()})
        overwritten = client.post("/api/cases", json={"test_case": case_dict(), "overwrite": True})
        imported = client.post("/api/cases", json={"gherkin": FEATURE})
        listing = client.get("/api/cases").json()["cases"]
        one = client.get("/api/cases/demo-login.json")
        missing = client.get("/api/cases/nope.json")
        traversal = client.get("/api/cases/..%2fconfig.yaml")

    assert saved.status_code == 201 and saved.json()["path"] == ".hercules/cases/demo-login.json"
    assert duplicate.status_code == 409
    assert overwritten.status_code == 201
    assert imported.status_code == 201 and imported.json()["path"] == ".hercules/cases/shopper-signs-in.feature"
    assert {case["name"] for case in listing} == {"demo-login.json", "shopper-signs-in.feature"}
    assert all(case["valid"] for case in listing)
    assert one.json()["test_case"]["id"] == "demo-login"
    assert missing.status_code == 404 and traversal.status_code in (404, 422)


def test_cases_list_reports_invalid_files(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = config.settings.project_dir / ".hercules" / "cases"
    (cases_dir / "broken.json").write_text('{"id": "Bad Id"}')
    with client_for(config) as client:
        listing = client.get("/api/cases").json()["cases"]
    broken = next(case for case in listing if case["name"] == "broken.json")
    assert broken["valid"] is False and broken["error"]


@pytest.mark.parametrize("payload", [{"test_case": {"id": "x"}}, {"gherkin": "nonsense"}, {}, {"test_case": case_dict(), "gherkin": FEATURE}])
def test_cases_reject_invalid_input(config, payload) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        assert client.post("/api/cases", json=payload).status_code == 422


def test_environments_crud_and_validation(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        created = client.post("/api/environments", json={"name": "staging", "base_url": "https://staging.example.test", "secret_names": ["SHOP_PASSWORD"]})
        listing = client.get("/api/environments").json()["environments"]
        bad_name = client.post("/api/environments", json={"name": "../x", "base_url": "http://127.0.0.1"})
        bad_url = client.post("/api/environments", json={"name": "x", "base_url": "ftp://x"})
        secret_value = client.post("/api/environments", json={"name": "x", "base_url": "http://127.0.0.1", "password": "hunter2"})
        duplicate = client.post("/api/environments", json={"name": "staging", "base_url": "https://staging.example.test"})

    assert created.status_code == 201
    assert listing == [
        {
            "name": "staging",
            "base_url": "https://staging.example.test",
            "viewport": None,
            "browser": None,
            "browser_options": None,
            "device": None,
            "mobile": None,
            "secret_names": ["SHOP_PASSWORD"],
            "variables": {},
        }
    ]
    assert bad_name.status_code == 422 and bad_url.status_code == 422 and secret_value.status_code == 422
    assert duplicate.status_code == 409


# --------------------------------------------------------------------------- models, health, openapi


def test_models_reports_modes_without_the_key(tmp_path: Path) -> None:
    fake_key = "sk-or-test-" + "z" * 30
    config = make_config(tmp_path, env=isolated_env(tmp_path, OPENROUTER_API_KEY=fake_key, HERCULES_LLM_PROVIDER="mlx-vlm", HERCULES_EGRESS="hybrid-text"))
    with client_for(config) as client:
        response = client.get("/api/models")
    body = response.json()
    assert fake_key not in response.text
    assert body["modes"]["pure"]["available"] is True
    assert body["modes"]["hybrid"]["key_env"] == "OPENROUTER_API_KEY"
    assert body["modes"]["hybrid"]["key_set"] is True
    assert body["modes"]["hybrid"]["available"] is True
    assert body["endpoint"]["provider"] == "mlx-vlm"
    assert body["parallel_runs"] == 1


def test_models_without_key_says_hybrid_unavailable(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        body = client.get("/api/models").json()
    assert body["modes"]["hybrid"]["key_set"] is False
    assert body["modes"]["hybrid"]["available"] is False
    assert "OPENROUTER_API_KEY" in body["modes"]["hybrid"]["reason"]


def test_models_finds_the_key_in_the_key_file_like_the_worker_does(tmp_path: Path) -> None:
    from testzeus_hercules.cli.keys import write_key_file

    fake_key = "sk-or-test-" + "f" * 30
    config = make_config(tmp_path, env=isolated_env(tmp_path, HERCULES_LLM_PROVIDER="mlx-vlm", HERCULES_EGRESS="hybrid"))
    write_key_file(config.paths.home_dir, fake_key)
    with client_for(config) as client:
        response = client.get("/api/models")
    body = response.json()
    assert fake_key not in response.text
    assert body["modes"]["hybrid"]["key_set"] is True
    assert body["modes"]["hybrid"]["key_source"] == "file"
    assert body["modes"]["hybrid"]["available"] is True


def test_health_and_openapi(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        health = client.get("/api/health").json()
        spec = client.get("/api/openapi.json").json()
    assert health["project_dir"] == str(config.settings.project_dir)
    assert health["queue"] == {"queued": 0, "running": 0, "paused": 0, "needs_approval": 0, "needs_plan_approval": 0}
    assert health["home_dir"] == str(config.paths.home_dir) and health["egress_policy"] == config.settings.egress.policy
    assert "user" in health and "host" in health
    assert spec["openapi"].startswith("3.")
    for path in (
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/events",
        "/api/runs/{run_id}/cancel",
        "/api/runs/{run_id}/approve",
        "/api/runs/{run_id}/rerun",
        "/api/runs/{run_id}/evidence/{path}",
        "/api/cases",
        "/api/environments",
        "/api/models",
        "/api/health",
    ):
        assert path in spec["paths"], path
    assert "RunCreate" in spec["components"]["schemas"]
    assert TOKEN not in json.dumps(spec)


def test_daemon_and_engine_agree_on_the_jev_key_variable() -> None:
    from testzeus_hercules.daemon.system import DEFAULT_JEV_KEY_ENV
    from testzeus_hercules.engine.factory import DEFAULT_JEV_KEY_ENV as ENGINE_DEFAULT

    assert DEFAULT_JEV_KEY_ENV == ENGINE_DEFAULT


# --------------------------------------------------------------------------- export as code


def test_export_serves_a_passed_run_as_code_and_refuses_others(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        passed = submit(client)
        wait_for(client, passed)
        failed = submit(client, "fake-fail")
        wait_for(client, failed)
        typescript = client.get(f"/api/runs/{passed}/export")
        python = client.get(f"/api/runs/{passed}/export", params={"format": "playwright-py", "target_dir": "e2e"})
        spec = client.get(f"/api/runs/{passed}/export", params={"format": "spec"})
        refused = client.get(f"/api/runs/{failed}/export")
        partial = client.get(f"/api/runs/{failed}/export", params={"allow_failed": "true"})
        unknown = client.get(f"/api/runs/{passed}/export", params={"format": "cypress"})
        appium = client.get(f"/api/runs/{passed}/export", params={"format": "appium-py"})
        outside = client.get(f"/api/runs/{passed}/export", params={"target_dir": "../x"})
        missing = client.get("/api/runs/r_19990101_000000_000000/export")
        documented = client.get("/api/openapi.json").json()
    assert typescript.status_code == 200
    body = typescript.json()
    assert body["format"] == "playwright-ts" and body["filename"] == "tests/demo-login.spec.ts" and body["steps_exported"] == 3
    assert 'getByRole("button", { name: "step number 1", exact: true }).click();' in body["code"]
    assert body["guidance"] and body["env"] == [], "clicks only: no address, no secret"
    assert python.json()["filename"] == "e2e/test_demo_login.py"
    assert spec.json()["spec"]["case_id"] == "demo-login"
    assert refused.status_code == 409 and "only passed runs are exported" in refused.json()["error"]
    assert partial.status_code == 200 and partial.json()["partial"] and partial.json()["steps_exported"] == 1
    assert unknown.status_code == 422 and outside.status_code == 422 and missing.status_code == 404
    assert appium.status_code == 409 and "Appium code is for native app runs" in appium.json()["error"]
    assert "/api/runs/{run_id}/export" in documented["paths"] and "ExportView" in documented["components"]["schemas"]


# --------------------------------------------------------------------------- re-run with changed options


def _worker_args(config, run_id: str) -> list[str]:  # type: ignore[no-untyped-def]
    return json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "worker_args.json").read_text())


def _written_case(config, run_id: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return json.loads((config.settings.project_dir / ".hercules" / "runs" / run_id / "case.json").read_text())


def test_run_view_shows_the_options_the_run_started_with(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        run_id = submit(client, test_case=case_dict(), mode="pure", egress="local", a11y=True, fail_on="serious", ask_on_failure=True, approval_timeout_s=90, base_url="http://127.0.0.1:5555")
        view = wait_for(client, run_id)
    settings = view["settings"]
    assert settings["mode"] == "pure" and settings["egress"] == "local" and settings["base_url"] == "http://127.0.0.1:5555"
    assert settings["a11y"] is True and settings["fail_on"] == "serious" and settings["ask_on_failure"] is True and settings["approval_timeout_s"] == 90
    assert settings["visual"] is False and settings["environment"] is None and settings["destructive_terms"] is None


def test_rerun_inherits_the_options_and_applies_the_given_ones(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        parent = submit(client, test_case=case_dict(), mode="pure", a11y=True, confirm_destructive=True, destructive_terms=["wipe"])
        parent_view = wait_for(client, parent)
        same = client.post(f"/api/runs/{parent}/rerun", json={"scope": "whole"})
        changed = client.post(f"/api/runs/{parent}/rerun", json={"scope": "whole", "mode": "hybrid", "visual": True, "fail_on": "minor", "destructive_terms": None, "headed": True})
        assert same.status_code == 201 and changed.status_code == 201, changed.text
        same_view = wait_for(client, same.json()["run_id"])
        changed_view = wait_for(client, changed.json()["run_id"])
        grandchild = client.post(f"/api/runs/{changed_view['run_id']}/rerun", json={})
        grandchild_view = wait_for(client, grandchild.json()["run_id"])

    assert same_view["settings"] == parent_view["settings"]
    same_args = _worker_args(config, same_view["run_id"])
    assert same_args[same_args.index("--mode") + 1] == "pure" and "--a11y" in same_args and same_args[same_args.index("--destructive-terms") + 1] == "wipe"

    args = _worker_args(config, changed_view["run_id"])
    assert args[args.index("--mode") + 1] == "hybrid" and "--headed" in args
    assert {"--a11y", "--visual", "--confirm-destructive"} <= set(args) and "--destructive-terms" not in args
    assert args[args.index("--quality-fail-on") + 1] == "minor"
    assert changed_view["mode"] == "hybrid" and changed_view["parent_run_id"] == parent
    assert changed_view["settings"]["destructive_terms"] is None and changed_view["settings"]["visual"] is True
    # A re-run of the re-run inherits the changed options.
    assert grandchild_view["settings"] == changed_view["settings"]


def test_rerun_turning_the_audits_off_drops_the_inherited_fail_on(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        parent = submit(client, test_case=case_dict(), a11y=True, fail_on="serious")
        wait_for(client, parent)
        response = client.post(f"/api/runs/{parent}/rerun", json={"scope": "whole", "a11y": False})
        assert response.status_code == 201, response.text
        view = wait_for(client, response.json()["run_id"])
    assert view["settings"]["a11y"] is False and view["settings"]["fail_on"] is None
    assert not {"--a11y", "--quality-fail-on"} & set(_worker_args(config, view["run_id"]))


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"mode": "turbo"}, "mode"),
        ({"mode": None}, "mode"),
        ({"egress": "everywhere"}, "egress"),
        ({"decider": "oracle"}, "decider"),
        ({"surprise": 1}, "surprise"),
        ({"base_url": "javascript:alert(1)"}, "base_url"),
        ({"environment": "nope"}, "unknown environment"),
        ({"environment": "Bad Name"}, "environment"),
        ({"fail_on": "serious"}, "fail_on needs a11y or visual"),
        ({"a11y": True, "fail_on": "fatal"}, "fail_on"),
        ({"destructive_terms": ["a,b"]}, "destructive_terms"),
        ({"approval_timeout_s": 0}, "approval_timeout_s"),
        ({"max_steps": 500}, "max_steps"),
        ({"headed": "yes please"}, "headed"),
    ],
)
def test_rerun_options_are_validated_like_a_new_run(config, payload, message: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        parent = submit(client, "fake-fail")
        wait_for(client, parent)
        response = client.post(f"/api/runs/{parent}/rerun", json={"scope": "failed_only", **payload})
        listed = client.get("/api/runs").json()["runs"]
    assert response.status_code == 422, response.text
    assert message in response.text
    assert [run["run_id"] for run in listed] == [parent]  # nothing was queued


def test_rerun_failed_only_of_a_passed_run_is_409_even_with_options(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        parent = submit(client)
        wait_for(client, parent)
        response = client.post(f"/api/runs/{parent}/rerun", json={"scope": "failed_only", "mode": "pure"})
    assert response.status_code == 409 and "passed" in response.text


def test_rerun_can_move_a_run_to_another_environment_or_base_url(config) -> None:  # type: ignore[no-untyped-def]
    case_file = config.settings.project_dir / ".hercules" / "cases" / "login.json"
    case_file.write_text(json.dumps(case_dict()))
    with client_for(config) as client:
        assert client.post("/api/environments", json={"name": "staging", "base_url": "http://127.0.0.1:8124"}).status_code == 201
        inline = submit(client, test_case=case_dict())
        from_file = submit(client, case_path=".hercules/cases/login.json", base_url="http://127.0.0.1:5555")
        wait_for(client, inline)
        wait_for(client, from_file)
        moved = client.post(f"/api/runs/{inline}/rerun", json={"environment": "staging"}).json()["run_id"]
        rebased = client.post(f"/api/runs/{from_file}/rerun", json={"base_url": "http://127.0.0.1:6666"}).json()["run_id"]
        kept = client.post(f"/api/runs/{from_file}/rerun", json={"mode": "pure"}).json()["run_id"]
        moved_view = wait_for(client, moved)
        for run_id in (rebased, kept):
            wait_for(client, run_id)
        again = client.post(f"/api/runs/{moved}/rerun", json={}).json()["run_id"]
        wait_for(client, again)

    assert _written_case(config, moved)["environment"]["base_url"] == "http://127.0.0.1:8124"  # type: ignore[index]
    assert moved_view["settings"]["environment"] == "staging"
    assert _written_case(config, again)["environment"]["base_url"] == "http://127.0.0.1:8124"  # type: ignore[index]
    assert _written_case(config, rebased)["environment"]["base_url"] == "http://127.0.0.1:6666"  # type: ignore[index]
    assert _written_case(config, kept)["environment"]["base_url"] == "http://127.0.0.1:5555"  # type: ignore[index]
