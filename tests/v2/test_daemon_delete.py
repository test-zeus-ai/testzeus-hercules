from __future__ import annotations

from pathlib import Path

from testzeus_hercules.cases.step_files import save_step_file
from tests.v2.fixtures.daemon_support import client_for, make_config, submit, wait_for

FEATURE = """Feature: Shop
  Scenario: Checkout
    Given I open "http://127.0.0.1:9/cart"
    When I fill "{{coupon}}" in "Coupon"
"""


def test_a_finished_run_is_deleted_with_its_evidence(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        finished = submit(client)
        wait_for(client, finished)
        hanging = submit(client, "fake-hang")
        wait_for(client, hanging, {"running"})
        busy = client.delete(f"/api/runs/{hanging}")
        removed = client.delete(f"/api/runs/{finished}")
        again = client.delete(f"/api/runs/{finished}")
        listed = [run["run_id"] for run in client.get("/api/runs").json()["runs"]]
        client.post(f"/api/runs/{hanging}/cancel")
    assert busy.status_code == 409 and "cancel it" in busy.json()["error"]
    assert removed.json() == {"run_id": finished, "removed": True} and again.status_code == 404
    assert finished not in listed and not config.paths.run_dir(finished).exists()


def test_a_case_is_deleted_with_its_test_data_and_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    project = config.settings.project_dir
    with client_for(config) as client:
        client.post("/api/cases", json={"gherkin": FEATURE, "test_data": {"coupon": "PANTRY10"}})
        save_step_file(project, "checkout", "rules.md", b"PANTRY10 takes 10% off", scope="case")
        removed = client.delete("/api/cases/checkout.feature")
        missing = client.delete("/api/cases/checkout.feature")
        sidecar = client.delete("/api/cases/checkout.data.json")
    assert removed.json() == {"name": "checkout.feature", "removed": True} and missing.status_code == 404 and sidecar.status_code == 404
    assert not any(config.paths.cases_dir.iterdir()) and not (project / ".hercules" / "case-files" / "checkout").exists()


def test_an_environment_is_deleted_with_its_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    project = config.settings.project_dir
    with client_for(config) as client:
        client.post("/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9"})
        save_step_file(project, "shop", "rules.md", b"PANTRY10 takes 10% off")
        removed = client.delete("/api/environments/shop")
        missing = client.delete("/api/environments/shop")
        listed = client.get("/api/environments").json()["environments"]
    assert removed.json() == {"name": "shop", "removed": True} and missing.status_code == 404
    assert listed == [] and not (project / ".hercules" / "files" / "shop").exists()
