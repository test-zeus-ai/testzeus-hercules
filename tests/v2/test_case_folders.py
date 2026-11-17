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
    assert saved.json() == {"name": "Checkout/Payments/shopper-pays-by-card.feature", "path": ".hercules/cases/Checkout/Payments/shopper-pays-by-card.feature", "id": "shopper-pays-by-card"}
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
    assert listing == {"cases": [], "folders": []}
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
