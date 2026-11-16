"""Importers write atomically: a failed write leaves neither a half-written file nor its temporary file behind."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from testzeus_hercules.cases.importers import common
from testzeus_hercules.cases.importers.common import ImportReport, PlannedFile, sensitive_file, write_plan


def test_a_failed_write_removes_its_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(source: str, target: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(common.os, "replace", refuse)
    with pytest.raises(OSError):
        write_plan(tmp_path, [PlannedFile(kind="case", relative="cases/a.json", content="{}", source="s")], ImportReport(title="t"), dry_run=False, force=False)

    folder = tmp_path / ".hercules" / "cases"
    assert list(folder.iterdir()) == []


def test_a_write_is_flushed_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(common.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])

    write_plan(tmp_path, [PlannedFile(kind="data", relative="data/a.bin", content=b"\x00\x01", source="s")], ImportReport(title="t"), dry_run=False, force=False)

    assert synced and (tmp_path / ".hercules" / "data" / "a.bin").read_bytes() == b"\x00\x01"


@pytest.mark.parametrize(
    ("name", "head", "sensitive"),
    [("cert.pem", b"", True), (".env.local", b"", True), ("sa.json", b'{"type": "service_account"}', True), ("users.csv", b"name,email\n", False), ("avatar.png", b"\x89PNG", False)],
)
def test_credential_files_are_recognised_by_name_or_content(name: str, head: bytes, sensitive: bool) -> None:
    assert sensitive_file(name, head) is sensitive
