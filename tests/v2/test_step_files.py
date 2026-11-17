from __future__ import annotations

from pathlib import Path

import pytest

from testzeus_hercules.act.files import confine
from testzeus_hercules.cases.step_files import MAX_FILES, StepFileError, list_step_files, remove_step_file, save_step_file, step_file_roots
from tests.v2.fixtures.daemon_support import client_for, make_config

PNG = b"\x89PNG\r\n\x1a\nfake image"


def test_any_file_is_kept_as_it_was_given(tmp_path: Path) -> None:
    saved = save_step_file(tmp_path, "shop", "case.png", PNG)
    archive = save_step_file(tmp_path, "shop", "bundle.zip", b"PK\x03\x04zip")

    assert (saved.name, saved.size) == ("case.png", len(PNG)) and archive.name == "bundle.zip"
    assert (tmp_path / ".hercules" / "files" / "shop" / "case.png").read_bytes() == PNG
    assert [item.name for item in list_step_files(tmp_path, "shop")] == ["bundle.zip", "case.png"]
    assert not any(path.name.startswith(".") for path in (tmp_path / ".hercules" / "files" / "shop").iterdir())
    assert remove_step_file(tmp_path, "shop", "case.png") is True
    assert remove_step_file(tmp_path, "shop", "case.png") is False
    assert [item.name for item in list_step_files(tmp_path, "shop")] == ["bundle.zip"]


@pytest.mark.parametrize(
    ("name", "content", "problem"),
    [
        ("../escape.png", b"x", "no folders"),
        (".env", b"A=1", "no folders"),
        ("prod.env", b"A=1", "credential"),
        ("id_rsa.txt", b"x", "credential"),
        ("empty.png", b"", "empty"),
        ("key.txt", b"-----BEGIN PRIVATE KEY-----\nMIIE", "private key"),
    ],
)
def test_unsafe_files_are_refused(tmp_path: Path, name: str, content: bytes, problem: str) -> None:
    with pytest.raises(StepFileError, match=problem):
        save_step_file(tmp_path, "shop", name, content)
    assert not (tmp_path / ".hercules" / "files" / "shop" / name).exists()


def test_an_environment_holds_a_limited_number_of_files(tmp_path: Path) -> None:
    for number in range(MAX_FILES):
        save_step_file(tmp_path, "shop", f"photo-{number}.png", PNG)

    with pytest.raises(StepFileError, match="at most"):
        save_step_file(tmp_path, "shop", "one-more.png", PNG)
    assert save_step_file(tmp_path, "shop", "photo-0.png", b"replaced").size == len(b"replaced")


def test_an_upload_finds_the_case_file_first_then_the_environment_then_the_project(tmp_path: Path) -> None:
    save_step_file(tmp_path, "shop", "case.png", b"environment")
    save_step_file(tmp_path, "shop", "logo.png", b"environment logo")
    save_step_file(tmp_path, "checkout", "case.png", b"case", scope="case")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "invoice.pdf").write_bytes(b"%PDF")
    roots = step_file_roots(tmp_path, "shop", "checkout")

    assert roots == (tmp_path / ".hercules" / "case-files" / "checkout", tmp_path / ".hercules" / "files" / "shop", tmp_path)
    assert confine("case.png", roots).read_bytes() == b"case"
    assert confine("logo.png", roots).read_bytes() == b"environment logo"
    assert confine("fixtures/invoice.pdf", roots).read_bytes() == b"%PDF"
    assert step_file_roots(tmp_path, None, None) == (tmp_path,)
    assert step_file_roots(tmp_path, "../x", "Bad Id") == (tmp_path,)
    with pytest.raises(StepFileError, match="invalid case id"):
        save_step_file(tmp_path, "../x", "a.png", PNG, scope="case")


def test_files_api_uploads_lists_and_removes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        client.post("/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9"})
        added = client.put("/api/environments/shop/files/case.png", content=PNG, headers={"Content-Type": "application/octet-stream"})
        listed = client.get("/api/environments/shop/files")
        as_json = client.put("/api/environments/shop/files/case.png", content=PNG, headers={"Content-Type": "application/json"})
        refused = client.put("/api/environments/shop/files/prod.env", content=b"A=1", headers={"Content-Type": "application/octet-stream"})
        unknown_env = client.put("/api/environments/nope/files/case.png", content=PNG, headers={"Content-Type": "application/octet-stream"})
        elsewhere = client.put("/api/environments/shop", content=PNG, headers={"Content-Type": "application/octet-stream"})
        removed = client.delete("/api/environments/shop/files/case.png")
    assert added.status_code == 200 and added.json()["file"] == {"name": "case.png", "size": len(PNG)} and "model" not in added.json()
    assert [item["name"] for item in listed.json()["files"]] == ["case.png"]
    assert as_json.status_code == 415 and elsewhere.status_code == 415
    assert refused.status_code == 422 and unknown_env.status_code == 404
    assert removed.json()["removed"] is True and removed.json()["files"] == []


def test_case_files_api_uses_the_saved_case(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    case = {"id": "checkout", "title": "Checkout", "steps": [{"action": "Open the cart"}]}
    with client_for(config) as client:
        missing = client.put("/api/cases/checkout.json/files/case.png", content=PNG, headers={"Content-Type": "application/octet-stream"})
        client.post("/api/cases", json={"test_case": case})
        added = client.put("/api/cases/checkout.json/files/case.png", content=PNG, headers={"Content-Type": "application/octet-stream"})
        listed = client.get("/api/cases/checkout.json/files")
        removed = client.delete("/api/cases/checkout.json/files/case.png")
    assert missing.status_code == 404
    assert added.status_code == 200 and added.json()["case"] == "checkout"
    assert [item["name"] for item in listed.json()["files"]] == ["case.png"]
    assert removed.json()["removed"] is True and removed.json()["files"] == []


def test_a_file_name_in_the_url_is_decoded_once() -> None:
    from starlette.requests import Request

    from testzeus_hercules.daemon.files import _filename

    assert _filename(Request({"type": "http", "path_params": {"filename": "%41.png"}})) == "%41.png"


@pytest.mark.parametrize("linked", [".hercules/files", ".hercules/files/shop", ".hercules/case-files/checkout"])
def test_files_are_never_written_through_a_symbolic_link(tmp_path: Path, linked: str) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    outside.mkdir()
    (outside / "case.png").write_bytes(b"theirs")
    link = project / linked
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    owner, scope = ("checkout", "case") if "case-files" in linked else ("shop", "environment")

    with pytest.raises(StepFileError, match="symbolic link"):
        save_step_file(project, owner, "case.png", PNG, scope)  # type: ignore[arg-type]
    with pytest.raises(StepFileError, match="symbolic link"):
        remove_step_file(project, owner, "case.png", scope)  # type: ignore[arg-type]
    with pytest.raises(StepFileError, match="symbolic link"):
        list_step_files(project, owner, scope)  # type: ignore[arg-type]

    assert (outside / "case.png").read_bytes() == b"theirs" and sorted(path.name for path in outside.iterdir()) == ["case.png"]
    roots = step_file_roots(project, "shop", "checkout")
    assert len(roots) == 2 and roots[-1] == project
    assert all(root.resolve().is_relative_to(project.resolve()) for root in roots)


def test_files_are_never_written_outside_the_project(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".hercules").symlink_to(outside, target_is_directory=True)

    with pytest.raises(StepFileError, match="inside the project"):
        save_step_file(project, "shop", "case.png", PNG)

    assert not any(outside.iterdir())
