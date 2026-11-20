from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.v2.fixtures.daemon_support import (
    case_dict,
    client_for,
    make_config,
    wait_for,
)
from testzeus_hercules.daemon import cases as case_files

FEATURE = """Feature: Checkout
  Scenario: Shopper pays by card
    Given I open "/checkout.html"
    When I click the "Pay" button
    Then I should see "Paid"
"""


@pytest.fixture()
def config(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_config(tmp_path)


def _cases_dir(config) -> Path:  # type: ignore[no-untyped-def]
    return config.settings.project_dir / ".hercules" / "cases"


def test_folders_hold_cases_and_the_listing_names_them_by_path(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        top = client.post("/api/folders", json={"name": "Checkout"})
        nested = client.post("/api/folders", json={"parent": "Checkout", "name": " Payments "})
        saved = client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {"card": "4242"}, "folder": "Checkout/Payments"})
        loose = client.post("/api/cases", json={"test_case": case_dict()})
        listing = client.get("/api/cases").json()
        one = client.get("/api/cases/Checkout/Payments/shopper-pays-by-card.feature")
        plan = client.get("/api/cases/Checkout/Payments/shopper-pays-by-card.feature/plan")
        files = client.get("/api/cases/Checkout/Payments/shopper-pays-by-card.feature/files")
        missing_folder = client.post("/api/cases", json={"test_case": case_dict(case_id="other"), "folder": "Nowhere"})

    assert top.status_code == 201 and top.json() == {"folder": "Checkout"}
    assert nested.json() == {"folder": "Checkout/Payments"}
    assert saved.status_code == 201
    assert saved.json() == {
        "name": "Checkout/Payments/shopper-pays-by-card.feature",
        "path": ".hercules/cases/Checkout/Payments/shopper-pays-by-card.feature",
        "id": "shopper-pays-by-card",
        "title": "Shopper pays by card",
        "steps": 3,
    }
    assert (_cases_dir(config) / "Checkout" / "Payments" / "shopper-pays-by-card.data.json").is_file()
    assert loose.json()["name"] == "demo-login.json"
    assert listing["folders"] == ["Checkout", "Checkout/Payments"]
    assert [(entry["name"], entry["folder"]) for entry in listing["cases"]] == [("demo-login.json", ""), ("Checkout/Payments/shopper-pays-by-card.feature", "Checkout/Payments")]
    assert one.status_code == 200 and one.json()["name"] == "Checkout/Payments/shopper-pays-by-card.feature" and one.json()["folder"] == "Checkout/Payments"
    assert one.json()["test_case"]["test_data"] == {"card": "4242"}
    assert plan.status_code == 404 and "no plan" in plan.text
    assert files.status_code == 200
    assert missing_folder.status_code == 422 and "no folder Nowhere" in missing_folder.text


def test_a_case_id_is_unique_across_folders(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Smoke"})
        client.post("/api/cases", json={"test_case": case_dict()})
        again = client.post("/api/cases", json={"test_case": case_dict(), "folder": "Smoke", "overwrite": True})
        same_place = client.post("/api/cases", json={"test_case": case_dict(), "overwrite": True})

    assert again.status_code == 422 and "already in the top level" in again.text
    assert same_place.status_code == 201


def test_a_case_id_is_unique_across_file_types(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="shopper-pays-by-card"), "folder": "Checkout"})
        feature = client.post("/api/cases", json={"gherkin": FEATURE, "folder": "Checkout", "overwrite": True})

    assert feature.status_code == 422 and "already in Checkout (shopper-pays-by-card.json)" in feature.text


def test_a_case_in_a_folder_takes_reference_files(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict(), "folder": "Checkout"})
        added = client.put("/api/cases/Checkout/demo-login.json/files/invoice.png", content=b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "application/octet-stream"})
        as_json = client.put("/api/cases/Checkout/demo-login.json/files/invoice.png", content=b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "application/json"})
        listed = client.get("/api/cases/Checkout/demo-login.json/files").json()

    assert added.status_code == 200 and added.json()["case"] == "demo-login"
    assert as_json.status_code == 415
    assert [item["name"] for item in listed["files"]] == ["invoice.png"]


@pytest.mark.parametrize(
    "name, problem",
    [
        ("", "at least 1"),
        ("a/b", "cannot contain"),
        ("what?", "cannot contain"),
        (".hidden", "start with a dot"),
        ("trailing.", "end with a dot"),
        ("CON", "reserved name"),
        ("x" * 121, "at most 120"),
    ],
)
def test_folder_names_must_suit_every_file_system(config, name: str, problem: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.post("/api/folders", json={"name": name})
    assert response.status_code == 422 and problem in response.text


def test_sibling_names_are_unique_ignoring_case_and_depth_stops_at_eight(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "API"})
        clash = client.post("/api/folders", json={"name": "api"})
        parent = ""
        for level in range(1, 9):
            assert client.post("/api/folders", json={"parent": parent, "name": f"L{level}"}).status_code == 201
            parent = f"{parent}/L{level}" if parent else f"L{level}"
        ninth = client.post("/api/folders", json={"parent": parent, "name": "L9"})
        missing_parent = client.post("/api/folders", json={"parent": "Nope", "name": "x"})

    assert clash.status_code == 409 and "already has a folder named API" in clash.text
    assert ninth.status_code == 422 and "8 levels" in ninth.text
    assert missing_parent.status_code == 404


def test_rename_and_move_a_folder_with_what_it_holds(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        for parent, name in (("", "Checkout"), ("Checkout", "Payments"), ("", "Archive"), ("Archive", "Payments")):
            client.post("/api/folders", json={"parent": parent, "name": name})
        client.post("/api/cases", json={"gherkin": FEATURE, "folder": "Checkout/Payments"})
        renamed = client.patch("/api/folders/Checkout/Payments", json={"name": "Cards"})
        into_itself = client.patch("/api/folders/Checkout", json={"parent": "Checkout/Cards"})
        same_place = client.patch("/api/folders/Checkout/Cards", json={"parent": "Checkout"})
        moved = client.patch("/api/folders/Checkout/Cards", json={"parent": ""})
        client.post("/api/folders", json={"name": "PAYMENTS"})
        clash = client.patch("/api/folders/Archive/Payments", json={"parent": ""})
        both = client.patch("/api/folders/Archive", json={"name": "x", "parent": ""})
        listing = client.get("/api/cases").json()

    assert renamed.json() == {"folder": "Checkout/Cards", "was": "Checkout/Payments"}
    assert into_itself.status_code == 422 and "inside itself" in into_itself.text
    assert same_place.status_code == 422 and "already there" in same_place.text
    assert moved.json()["folder"] == "Cards"
    assert clash.status_code == 409 and "already has a folder named PAYMENTS" in clash.text
    assert both.status_code == 422
    assert listing["folders"] == ["Archive", "Archive/Payments", "Cards", "Checkout", "PAYMENTS"]
    assert [entry["name"] for entry in listing["cases"]] == ["Cards/shopper-pays-by-card.feature"]


def test_moving_a_folder_keeps_it_within_eight_levels(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    (cases_dir / "A" / "B" / "C").mkdir(parents=True)
    (cases_dir / "D1" / "D2" / "D3" / "D4" / "D5" / "D6").mkdir(parents=True)
    with pytest.raises(case_files.CaseError, match="8 levels"):
        case_files.move_folder(cases_dir, "A", "D1/D2/D3/D4/D5/D6")
    assert case_files.move_folder(cases_dir, "A", "D1/D2/D3/D4/D5") == "D1/D2/D3/D4/D5/A"


def test_cases_move_between_folders_with_their_test_data(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Smoke"})
        client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {"card": "4242"}})
        client.post("/api/cases", json={"test_case": case_dict()})
        moved = client.post("/api/cases/move", json={"names": ["shopper-pays-by-card.feature", "demo-login.json"], "folder": "Smoke"})
        missing = client.post("/api/cases/move", json={"names": ["gone.json"], "folder": ""})
        back = client.post("/api/cases/move", json={"names": ["Smoke/demo-login.json"], "folder": ""})

    assert moved.json() == {"folder": "Smoke", "names": ["Smoke/shopper-pays-by-card.feature", "Smoke/demo-login.json"]}
    assert (_cases_dir(config) / "Smoke" / "shopper-pays-by-card.data.json").is_file()
    assert not (_cases_dir(config) / "shopper-pays-by-card.data.json").exists()
    assert missing.status_code == 404
    assert back.json()["names"] == ["demo-login.json"]


def test_deleting_a_folder_moves_what_it_holds_up_by_default(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    with client_for(config) as client:
        for parent, name in (("", "Checkout"), ("Checkout", "Payments"), ("Checkout/Payments", "Cards")):
            client.post("/api/folders", json={"parent": parent, "name": name})
        client.post("/api/cases", json={"gherkin": FEATURE, "folder": "Checkout/Payments"})
        (cases_dir / "Checkout" / "Payments" / ".DS_Store").write_text("x")
        deleted = client.delete("/api/folders/Checkout/Payments")
        listing = client.get("/api/cases").json()

    assert deleted.json() == {"folder": "Checkout/Payments", "mode": "move_up", "moved": 2, "deleted": 0}
    assert listing["folders"] == ["Checkout", "Checkout/Cards"]
    assert [entry["name"] for entry in listing["cases"]] == ["Checkout/shopper-pays-by-card.feature"]


def test_moving_up_refuses_a_name_the_parent_already_has(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    (cases_dir / "Checkout" / "Smoke").mkdir(parents=True)
    (cases_dir / "Smoke").mkdir()
    with client_for(config) as client:
        response = client.delete("/api/folders/Checkout")
    assert response.status_code == 409 and "already has Smoke" in response.text
    assert (cases_dir / "Checkout" / "Smoke").is_dir()


def test_deleting_a_folder_with_its_cases_removes_their_data_and_files(config) -> None:  # type: ignore[no-untyped-def]
    project = config.settings.project_dir
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Old"})
        client.post("/api/folders", json={"parent": "Old", "name": "Older"})
        client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {"card": "4242"}, "folder": "Old/Older"})
        client.post("/api/cases", json={"test_case": case_dict(), "folder": "Old"})
        uploaded = client.put("/api/cases/Old/demo-login.json/files/photo.png", content=b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "application/octet-stream"})
        (_cases_dir(config) / "Old" / "notes.txt").write_text("keep me")
        refused = client.delete("/api/folders/Old?mode=delete_cases")
        (_cases_dir(config) / "Old" / "notes.txt").unlink()
        deleted = client.delete("/api/folders/Old?mode=delete_cases")
        bad_mode = client.delete("/api/folders/Old?mode=shred")
        listing = client.get("/api/cases").json()

    assert uploaded.status_code == 200 and uploaded.json()["case"] == "demo-login"
    assert refused.status_code == 422 and "notes.txt" in refused.text
    assert deleted.json() == {"folder": "Old", "mode": "delete_cases", "moved": 0, "deleted": 2}
    assert listing == {"cases": [], "folders": [], "truncated": False}
    assert not (project / ".hercules" / "case-files" / "demo-login").exists()
    assert bad_mode.status_code == 422


def test_the_listing_leaves_out_hidden_folders_links_and_sidecars(config, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    (cases_dir / ".git").mkdir()
    (cases_dir / ".git" / "x.json").write_text(json.dumps(case_dict(case_id="hidden")))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "linked.json").write_text(json.dumps(case_dict(case_id="linked")))
    os.symlink(outside, cases_dir / "Linked")
    (cases_dir / "Real").mkdir()
    (cases_dir / "Real" / "real.json").write_text(json.dumps(case_dict(case_id="real")))
    with client_for(config) as client:
        listing = client.get("/api/cases").json()
        through_link = client.get("/api/cases/Linked/linked.json")
        traversal = client.get("/api/cases/Real/../../config.json")
    assert listing["folders"] == ["Real"]
    assert [entry["name"] for entry in listing["cases"]] == ["Real/real.json"]
    assert through_link.status_code in (404, 422)
    assert traversal.status_code in (404, 422)


def test_running_a_folder_queues_its_ready_cases_as_one_batch(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Checkout"})
        client.post("/api/folders", json={"parent": "Checkout", "name": "Payments"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="top-one"), "folder": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="nested-one"), "folder": "Checkout/Payments"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="drafted", status="draft"), "folder": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="elsewhere")})
        (cases_dir / "Checkout" / "broken.json").write_text('{"id": "Bad Id"}')
        batch = client.post("/api/runs/batch", json={"folder": "Checkout", "decider": "stub"})
        only_here = client.post("/api/runs/batch", json={"folder": "Checkout", "subfolders": False, "decider": "stub"})
        missing = client.post("/api/runs/batch", json={"folder": "Nope"})
        body = batch.json()
        for run in body["runs"]:
            wait_for(client, run["run_id"])
        listed = client.get(f"/api/runs?batch={body['batch']}").json()["runs"]
        one = client.get(f"/api/runs/{body['runs'][0]['run_id']}").json()

    assert batch.status_code == 201
    assert body["folder"] == "Checkout" and body["batch"].startswith("b_")
    assert sorted(run["case_id"] for run in body["runs"]) == ["nested-one", "top-one"]
    assert {skip["name"] for skip in body["skipped"]} == {"Checkout/drafted.json", "Checkout/broken.json"}
    assert [run["case_id"] for run in only_here.json()["runs"]] == ["top-one"]
    assert missing.status_code == 404
    assert sorted(run["case_id"] for run in listed) == ["nested-one", "top-one"]
    assert one["batch"] == body["batch"] and one["folder"] == "Checkout"


def test_the_openapi_lists_the_folder_routes(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        spec = client.get("/api/openapi.json").json()
    assert {"/api/folders", "/api/folders/{folder}", "/api/cases/move", "/api/runs/batch"} <= set(spec["paths"])
    assert {"FolderCreate", "FolderUpdate", "CasesMove", "RunBatchCreate", "RunBatchCreated"} <= set(spec["components"]["schemas"])


def test_a_kept_explore_case_stays_in_the_folder_it_was_filed_in(tmp_path: Path) -> None:
    from testzeus_hercules.explore.cases import sync_case
    from testzeus_hercules.explore.models import Finding

    project = tmp_path / "project"
    filed = project / ".hercules" / "cases" / "Known bugs"
    filed.mkdir(parents=True)
    (filed / "explore-f0123456789.json").write_text(json.dumps(case_dict(case_id="explore-f0123456789", status="known_bug")))
    finding = Finding(
        id="f0123456789", title="Badge shows a stale count", signature="invariant|/cart|badge", oracle="invariant", tier="invariant", proposed_severity="P2", proposed_priority="P2", case="regression"
    )

    where = sync_case(project, finding)

    assert where == ".hercules/cases/Known bugs/explore-f0123456789.json"
    assert json.loads((filed / "explore-f0123456789.json").read_text())["status"] == "ready"
    assert not (project / ".hercules" / "cases" / "explore-f0123456789.json").exists()
    assert filed.is_dir()


def _explore_finding(decision: str):  # type: ignore[no-untyped-def]
    from testzeus_hercules.explore.models import Finding

    return Finding(
        id="f0123456789", title="Badge shows a stale count", signature="invariant|/cart|badge", oracle="invariant", tier="invariant", proposed_severity="P2", proposed_priority="P2", case=decision
    )


def test_rejecting_an_explore_case_takes_it_out_of_the_folder_it_was_filed_in(tmp_path: Path) -> None:
    from testzeus_hercules.explore.cases import sync_case

    project = tmp_path / "project"
    filed = project / ".hercules" / "cases" / "Bugs"
    filed.mkdir(parents=True)
    (filed / "explore-f0123456789.json").write_text(json.dumps(case_dict(case_id="explore-f0123456789", status="known_bug")))

    where = sync_case(project, _explore_finding("rejected"))

    assert where == ".hercules/explore/cases/rejected/f0123456789.json"
    assert json.loads((project / where).read_text())["id"] == "explore-f0123456789"
    assert not (filed / "explore-f0123456789.json").exists()
    assert filed.is_dir()


def test_a_tester_folder_named_explore_stays_when_its_explore_case_leaves(tmp_path: Path) -> None:
    from testzeus_hercules.explore.cases import sync_case

    project = tmp_path / "project"
    mine = project / ".hercules" / "cases" / "explore"
    mine.mkdir(parents=True)
    (mine / "explore-f0123456789.json").write_text(json.dumps(case_dict(case_id="explore-f0123456789", status="known_bug")))

    sync_case(project, _explore_finding("rejected"))

    assert mine.is_dir() and not any(mine.iterdir())


def test_moving_up_works_when_a_subfolder_has_the_folder_name(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Smoke"})
        client.post("/api/folders", json={"parent": "Smoke", "name": "Smoke"})
        client.post("/api/cases", json={"test_case": case_dict(), "folder": "Smoke/Smoke"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="outer"), "folder": "Smoke"})
        deleted = client.delete("/api/folders/Smoke")
        listing = client.get("/api/cases").json()

    assert deleted.status_code == 200 and deleted.json()["moved"] == 2
    assert listing["folders"] == ["Smoke"]
    assert [entry["name"] for entry in listing["cases"]] == ["outer.json", "Smoke/demo-login.json"]
    assert sorted(path.name for path in cases_dir.iterdir()) == ["Smoke", "outer.json"]


def test_a_move_up_that_fails_part_way_puts_everything_back(config, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    for name in ("a", "b", "c"):
        (cases_dir / "Old" / name).mkdir(parents=True)
    real = os.rename

    def flaky(source: Path, destination: Path) -> None:
        if Path(source).name == "c":
            raise PermissionError("busy")
        real(source, destination)

    monkeypatch.setattr(case_files.os, "rename", flaky)
    with pytest.raises(case_files.CaseError, match="nothing was changed"):
        case_files.delete_folder(cases_dir, config.settings.project_dir, "Old")
    monkeypatch.undo()

    assert sorted(path.name for path in cases_dir.iterdir()) == ["Old"]
    assert sorted(path.name for path in (cases_dir / "Old").iterdir()) == ["a", "b", "c"]


def test_two_cases_with_one_file_name_cannot_move_into_one_folder(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    for folder in ("A", "B", "C"):
        (cases_dir / folder).mkdir(parents=True)
    for folder in ("A", "B"):
        (cases_dir / folder / "login.json").write_text(json.dumps(case_dict(case_id="login")))
    with client_for(config) as client:
        response = client.post("/api/cases/move", json={"names": ["A/login.json", "B/login.json"], "folder": "C"})

    assert response.status_code == 409 and "both named login.json" in response.text
    assert (cases_dir / "A" / "login.json").is_file() and (cases_dir / "B" / "login.json").is_file()
    assert not any((cases_dir / "C").iterdir())


def _ignores_case(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.mkdir()
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.rmdir()


def test_a_folder_named_with_other_letter_case_is_the_same_folder(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    cases_dir.mkdir(parents=True, exist_ok=True)
    if not _ignores_case(cases_dir):
        pytest.skip("this file system tells names apart by letter case")
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Checkout"})
        client.post("/api/folders", json={"parent": "Checkout", "name": "Payments"})
        client.post("/api/cases", json={"test_case": case_dict(), "folder": "Checkout"})
        again = client.post("/api/cases", json={"test_case": case_dict(), "folder": "checkout", "overwrite": True})
        inside = client.patch("/api/folders/Checkout", json={"parent": "checkout/payments"})
        opened = client.get("/api/cases/CHECKOUT/demo-login.json")
        deleted = client.delete("/api/folders/checkout/payments")

    assert again.status_code == 201 and again.json()["name"] == "Checkout/demo-login.json"
    assert inside.status_code == 422 and "inside itself" in inside.text
    assert opened.json()["name"] == "Checkout/demo-login.json"
    assert deleted.status_code == 200 and deleted.json()["folder"] == "Checkout/Payments"


def test_folder_work_covers_every_case_and_reads_each_one_a_bounded_number_of_times(config, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    cases_dir, project = _cases_dir(config), config.settings.project_dir
    (cases_dir / "Big" / "Inner").mkdir(parents=True)
    (cases_dir / "Zed").mkdir()
    for index in range(3):
        (cases_dir / f"top-{index}.json").write_text(json.dumps(case_dict(case_id=f"top-{index}")))
        (cases_dir / "Big" / f"big-{index}.json").write_text(json.dumps(case_dict(case_id=f"big-{index}")))
        (cases_dir / "Big" / "Inner" / f"inner-{index}.json").write_text(json.dumps(case_dict(case_id=f"inner-{index}")))
    (cases_dir / "a-shared.json").write_text(json.dumps(case_dict(case_id="shared")))
    (cases_dir / "Zed" / "shared.json").write_text(json.dumps(case_dict(case_id="shared")))
    attached = project / ".hercules" / "case-files" / "shared"
    attached.mkdir(parents=True)
    (attached / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(case_files, "_MAX_LISTED", 2)
    loads: list[Path] = []
    real = case_files.load_case_file
    monkeypatch.setattr(case_files, "load_case_file", lambda path: loads.append(path) or real(path))

    in_folder = case_files.cases_in_folder(cases_dir, project, "Big", subfolders=True)
    assert len(in_folder) == 6 and len(loads) == 6

    case_files.delete_case(cases_dir, project, "a-shared.json")
    assert attached.is_dir()

    loads.clear()
    result = case_files.delete_folder(cases_dir, project, "Big", "delete_cases")
    assert result["deleted"] == 6 and not (cases_dir / "Big").exists()
    assert len(loads) <= 6 + 4


def test_feature_test_data_too_large_to_read_back_is_refused_before_anything_is_written(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    with client_for(config) as client:
        refused = client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {f"value_{index}": "x" * 4000 for index in range(70)}})
        kept = client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {"card": "4242"}})

    assert refused.status_code == 422 and "256 KiB" in refused.text
    assert kept.status_code == 201
    assert sorted(path.name for path in cases_dir.iterdir()) == ["shopper-pays-by-card.data.json", "shopper-pays-by-card.feature"]


def test_moving_up_keeps_hidden_entries_and_drops_only_system_files(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    old = cases_dir / "Old"
    (old / ".git").mkdir(parents=True)
    (old / ".git" / "HEAD").write_text("ref")
    (old / ".notes").write_text("mine")
    (old / ".DS_Store").write_text("x")
    (old / "._login.json").write_text("x")
    (cases_dir / "Other" / ".notes").parent.mkdir(parents=True)
    (cases_dir / "Other" / ".notes").write_text("theirs")
    (cases_dir / "Other" / "Inner").mkdir()
    (cases_dir / "Other" / "Inner" / ".notes").write_text("inner")
    with client_for(config) as client:
        deleted = client.delete("/api/folders/Old")
        clash = client.delete("/api/folders/Other/Inner")

    assert deleted.status_code == 200 and deleted.json()["moved"] == 2
    assert (cases_dir / ".git" / "HEAD").read_text() == "ref" and (cases_dir / ".notes").read_text() == "mine"
    assert not (cases_dir / ".DS_Store").exists() and not (cases_dir / "._login.json").exists()
    assert clash.status_code == 409 and ".notes" in clash.text
    assert (cases_dir / "Other" / "Inner" / ".notes").read_text() == "inner"


def test_deleting_cases_refuses_hidden_files_that_are_not_cases(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Old"})
        client.post("/api/cases", json={"test_case": case_dict(), "folder": "Old"})
        (cases_dir / "Old" / ".notes").write_text("mine")
        (cases_dir / "Old" / ".DS_Store").write_text("x")
        refused = client.delete("/api/folders/Old?mode=delete_cases")
        (cases_dir / "Old" / ".notes").unlink()
        deleted = client.delete("/api/folders/Old?mode=delete_cases")

    assert refused.status_code == 422 and ".notes" in refused.text
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 1 and not (cases_dir / "Old").exists()


# --------------------------------------------------------------------------- replacing a case, and the checks on saving


def test_replace_writes_the_named_file_keeps_its_test_data_and_checks_kind_and_id(config) -> None:  # type: ignore[no-untyped-def]
    cases = _cases_dir(config)
    named = cases / "pay.feature"  # named apart from its id, shopper-pays-by-card
    named.write_text(FEATURE.replace('"Paid"', '"Paid {{card}}"'), encoding="utf-8")
    (cases / "pay.data.json").write_text(json.dumps({"test_data": {"card": "4242"}}), encoding="utf-8")
    (cases / "broken.json").write_text("{not json", encoding="utf-8")
    with client_for(config) as client:
        kept = client.post("/api/cases", json={"gherkin": FEATURE.replace('"Paid"', '"Paid with {{card}}"'), "replace": "pay.feature"})
        cleared = client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {}, "replace": "pay.feature"})
        wrong_kind = client.post("/api/cases", json={"test_case": case_dict(), "replace": "pay.feature"})
        new_id = client.post("/api/cases", json={"gherkin": FEATURE.replace("Shopper pays by card", "Shopper pays cash"), "replace": "pay.feature"})
        repaired = client.post("/api/cases", json={"test_case": case_dict(case_id="repaired"), "replace": "broken.json"})
        missing = client.post("/api/cases", json={"gherkin": FEATURE, "replace": "nope.feature"})
        duplicate = client.post("/api/cases", json={"gherkin": FEATURE})
    assert kept.status_code == 200 and kept.json()["path"] == ".hercules/cases/pay.feature", kept.text
    assert sorted(path.name for path in cases.iterdir() if path.is_file()) == ["broken.json", "pay.feature"]
    assert cleared.status_code == 200 and not (cases / "pay.data.json").exists(), "test_data {} clears the kept data"
    assert wrong_kind.status_code == 422 and "give its new content as gherkin" in wrong_kind.json()["error"]
    assert new_id.status_code == 422 and "id would change" in new_id.json()["error"]
    assert repaired.status_code == 200 and json.loads((cases / "broken.json").read_text())["id"] == "repaired"
    assert missing.status_code == 404
    assert duplicate.status_code == 422 and duplicate.json()["detail"] == {"code": "case_id_taken", "case": "pay.feature"}, "an id held by a file named otherwise is found"


def test_saving_checks_the_environment_the_case_names(config) -> None:  # type: ignore[no-untyped-def]
    envs = config.settings.project_dir / ".hercules" / "envs"
    (envs / "shop.json").write_text(json.dumps({"name": "shop", "base_url": "http://127.0.0.1:9"}), encoding="utf-8")
    (envs / "bad.json").write_text(json.dumps({"name": "bad", "viewport": "huge"}), encoding="utf-8")
    with client_for(config) as client:
        known = client.post("/api/cases", json={"gherkin": "@env:shop\n" + FEATURE})
        unknown = client.post("/api/cases", json={"gherkin": "@env:nowhere\n" + FEATURE.replace("by card", "by cheque")})
        invalid = client.post("/api/cases", json={"gherkin": "@env:bad\n" + FEATURE.replace("by card", "by voucher")})
        own_url = client.post("/api/cases", json={"test_case": {**case_dict(case_id="own-url"), "environment": {"ref": "nowhere", "base_url": "http://127.0.0.1:9"}}})
        checked = client.post("/api/cases/validate", json={"gherkin": "@env:nowhere\n" + FEATURE})
    assert known.status_code == 201
    assert unknown.status_code == 422 and "unknown environment 'nowhere'" in unknown.json()["error"]
    assert invalid.status_code == 422 and "the environment 'bad' (bad.json) is not valid" in invalid.json()["error"]
    assert own_url.status_code == 201, "a case with its own base URL runs without the named environment"
    assert checked.json()["valid"] is False and "unknown environment 'nowhere'" in checked.json()["error"]


def test_the_listing_filters_by_folder_and_words_before_its_cap(config, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    cases = _cases_dir(config)
    for number in range(4):
        (cases / f"top-{number}.json").write_text(json.dumps(case_dict(case_id=f"top-{number}")), encoding="utf-8")
    (cases / "Late").mkdir()
    (cases / "Late" / "Login.JSON").write_text(json.dumps(case_dict(case_id="late-login")), encoding="utf-8")
    monkeypatch.setattr(case_files, "_MAX_LISTED", 3)
    with client_for(config) as client:
        capped = client.get("/api/cases").json()
        folder = client.get("/api/cases", params={"folder": "late"}).json()
        words = client.get("/api/cases", params={"query": "LATE-LOGIN"}).json()
        nowhere = client.get("/api/cases", params={"folder": "Nowhere"})
        upper = client.get("/api/cases/Late/Login.JSON")
    assert len(capped["cases"]) == 3 and capped["truncated"] is True
    assert [case["path"] for case in folder["cases"]] == [".hercules/cases/Late/Login.JSON"] and folder["truncated"] is False, "past the cap of the whole listing, found in its folder"
    assert [case["id"] for case in words["cases"]] == ["late-login"]
    assert nowhere.status_code == 404
    assert upper.status_code == 200 and upper.json()["test_case"]["id"] == "late-login", "upper-case suffixes are case names too"


def test_an_id_is_taken_by_a_file_named_like_it_in_another_case(config) -> None:  # type: ignore[no-untyped-def]
    """Login.json holds the id login: saving Scenario: Login elsewhere is refused, also where file names are case-sensitive."""
    cases = _cases_dir(config)
    (cases / "Accounts").mkdir()
    (cases / "Accounts" / "Login.json").write_text(json.dumps(case_dict(case_id="login")), encoding="utf-8")
    login = 'Feature: Accounts\n  Scenario: Login\n    Given I open "/login"\n'
    with client_for(config) as client:
        taken = client.post("/api/cases", json={"gherkin": login})
        again = client.post("/api/cases", json={"gherkin": FEATURE})
        same_file = client.post("/api/cases", json={"gherkin": FEATURE})
    assert taken.status_code == 422 and taken.json()["detail"]["code"] == "case_id_taken", taken.text
    assert taken.json()["detail"]["case"].casefold() == "accounts/login.json"
    assert sorted(path.name for path in cases.rglob("*") if path.is_file()) == ["Login.json", "shopper-pays-by-card.feature"]
    assert again.status_code == 201
    assert same_file.status_code == 409 and same_file.json()["detail"] == {"code": "case_exists", "case": "shopper-pays-by-card.feature"}


def test_a_feature_with_an_upper_case_suffix_keeps_its_test_data_together(config) -> None:  # type: ignore[no-untyped-def]
    cases = _cases_dir(config)
    for folder in ("Old", "New", "Gone"):
        (cases / folder).mkdir()
    for folder, stem in (("Old", "pay"), ("Old", "move"), ("Gone", "lost")):
        (cases / folder / f"{stem}.FEATURE").write_text(FEATURE.replace("Shopper pays by card", f"Case {stem}").replace('"Paid"', '"Paid {{card}}"'), encoding="utf-8")
        (cases / folder / f"{stem}.data.json").write_text(json.dumps({"test_data": {"card": "4242"}}), encoding="utf-8")
    with client_for(config) as client:
        listed = [case["name"] for case in client.get("/api/cases").json()["cases"]]
        one = client.get("/api/cases/Old/pay.FEATURE")
        deleted = client.delete("/api/cases/Old/pay.FEATURE")
        moved = client.post("/api/cases/move", json={"names": ["Old/move.FEATURE"], "folder": "New"})
        folder_gone = client.request("DELETE", "/api/folders/Gone", params={"mode": "delete_cases"})
    assert sorted(listed) == ["Gone/lost.FEATURE", "Old/move.FEATURE", "Old/pay.FEATURE"], "a data file is never listed as a case"
    assert one.status_code == 200 and one.json()["test_case"]["test_data"] == {"card": "4242"}
    assert deleted.status_code == 200 and sorted(path.name for path in (cases / "Old").iterdir()) == [], "deleting the case removes its data file"
    assert moved.status_code == 200 and sorted(path.name for path in (cases / "New").iterdir()) == ["move.FEATURE", "move.data.json"], "moving the case moves its data file"
    assert folder_gone.status_code == 200 and not (cases / "Gone").exists(), "a folder of such cases deletes cleanly"


def test_listing_a_folder_reads_it_once_however_many_data_files_it_holds(config, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    cases = _cases_dir(config)
    for number in range(30):
        (cases / f"case-{number}.feature").write_text(FEATURE.replace("Shopper pays by card", f"Case {number}"), encoding="utf-8")
        (cases / f"case-{number}.data.json").write_text(json.dumps({"test_data": {"card": "4242"}}), encoding="utf-8")
    (cases / "upper.FEATURE").write_text(FEATURE.replace("Shopper pays by card", "Upper"), encoding="utf-8")
    (cases / "upper.data.json").write_text(json.dumps({"test_data": {"card": "4242"}}), encoding="utf-8")
    reads: list[Path] = []
    iterdir = Path.iterdir

    def counted(self: Path):  # type: ignore[no-untyped-def]
        reads.append(self)
        return iterdir(self)

    monkeypatch.setattr(Path, "iterdir", counted)
    paths = case_files._case_paths(cases, "")
    assert len(paths) == 31 and not any(path.name.endswith(".data.json") for path in paths)
    assert reads == [cases], "one read of the folder, not one per data file"


def test_a_folder_run_summary_counts_each_cases_latest_attempt(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict("fake-pass", case_id="pays"), "folder": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict("fake-fail", case_id="breaks"), "folder": "Checkout"})
        body = client.post("/api/runs/batch", json={"folder": "Checkout"}).json()
        for run in body["runs"]:
            wait_for(client, run["run_id"])
        failed = next(run["run_id"] for run in body["runs"] if run["case_id"] == "breaks")
        first = client.get(f"/api/runs/batches/{body['batch']}").json()
        # The failed run is rerun twice from the same run: the case still counts once, as its newest attempt.
        for _ in range(2):
            rerun = client.post(f"/api/runs/{failed}/rerun", json={"scope": "whole"}).json()["run_id"]
            wait_for(client, rerun)
        again = client.get(f"/api/runs/batches/{body['batch']}").json()
        members = client.get("/api/runs", params={"batch": body["batch"]}).json()["runs"]
        missing = client.get("/api/runs/batches/b_nope")
    assert first | {"total": 2, "open": 0, "counts": {"passed": 1, "failed": 1}, "folder": "Checkout"} == first and first["finished_at"]
    assert len(members) == 4, "the reruns joined the folder run"
    assert again["total"] == 2 and again["counts"] == {"passed": 1, "failed": 1}, again
    assert missing.status_code == 404


def test_a_dry_folder_run_lists_what_it_would_run_and_starts_nothing(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        client.post("/api/folders", json={"name": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="ready-one"), "folder": "Checkout"})
        client.post("/api/cases", json={"test_case": case_dict(case_id="drafted", status="draft"), "folder": "Checkout"})
        own_url = {**case_dict(case_id="own-url"), "environment": {"ref": "gone", "base_url": "http://127.0.0.1:9"}}
        (_cases_dir(config) / "Checkout" / "own-url.json").write_text(json.dumps(own_url))
        no_url = {**case_dict(case_id="no-url"), "environment": {"ref": "gone"}}
        (_cases_dir(config) / "Checkout" / "no-url.json").write_text(json.dumps(no_url))
        planned = client.post("/api/runs/batch", json={"folder": "Checkout", "dry_run": True})
        empty = client.post("/api/folders", json={"name": "Empty"}) and client.post("/api/runs/batch", json={"folder": "Empty", "dry_run": True})
        runs = client.get("/api/runs").json()["runs"]
    body = planned.json()
    assert planned.status_code == 200 and body["runs"] == [] and body["batch"] == ""
    assert sorted(body["would_run"]) == ["Checkout/own-url.json", "Checkout/ready-one.json"], "a missing environment with the case's own base_url still runs"
    assert {skip["name"] for skip in body["skipped"]} == {"Checkout/drafted.json", "Checkout/no-url.json"}
    assert empty.status_code == 200 and empty.json()["would_run"] == []
    assert runs == []


def test_cases_in_every_folder_count_for_an_environment(config) -> None:  # type: ignore[no-untyped-def]
    cases_dir = _cases_dir(config)
    for index in range(case_files._MAX_FOLDERS):
        (cases_dir / f"f{index:04d}").mkdir()
    (cases_dir / "zz").mkdir()  # past the folder listing's cap
    (cases_dir / "zz" / "pay.feature").write_text('@env:shop\nFeature: Pay\n  Scenario: Pay\n    Given I open "/pay"\n', encoding="utf-8")
    with client_for(config) as client:
        listed = client.get("/api/cases").json()
        found = client.get("/api/cases", params={"environment": "shop"}).json()["cases"]
    assert "zz" not in listed["folders"], "the plain listing stays capped"
    assert [case["name"] for case in found] == ["zz/pay.feature"]


def test_latest_attempts_group_by_case_even_when_a_run_in_between_was_deleted() -> None:
    from testzeus_hercules.daemon.app import latest_attempts

    def record(run_id: str, status: str, parent: str | None, path: str) -> dict[str, object]:
        return {"id": run_id, "status": status, "parent_run_id": parent, "path": path}

    # A failed, B (deleted) reran it, C reran B: one case, its newest attempt C.
    chain = [record("A", "failed", None, "cases/pay.json"), record("C", "passed", "B", "cases/pay.json")]
    # X deleted; S1 and S2 both reran it.
    twice = [record("S1", "failed", "X", "cases/cart.json"), record("S2", "passed", "X", "cases/cart.json")]
    assert [item["id"] for item in latest_attempts(chain + twice + [record("D", "passed", None, "cases/other.json")])] == ["C", "S2", "D"]
