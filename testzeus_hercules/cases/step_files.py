"""Files a case's upload steps attach: kept per case and per environment, never read into a model."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from testzeus_hercules.act.files import is_secret_file
from testzeus_hercules.cases.importers.common import sensitive_file

FILES_DIRNAME = "files"
CASE_FILES_DIRNAME = "case-files"
MAX_FILES = 20
MAX_FILE_BYTES = 25 * 1024 * 1024
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$")
_ENV_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")

Scope = Literal["environment", "case"]


class StepFileError(ValueError):
    pass


@dataclass(frozen=True)
class StepFile:
    name: str
    size: int


def files_dir(project_dir: Path, owner: str, scope: Scope = "environment") -> Path:
    if scope == "case":
        if not _CASE_ID.fullmatch(owner) or ".." in owner:
            raise StepFileError("invalid case id")
        return project_dir / ".hercules" / CASE_FILES_DIRNAME / owner
    if not _ENV_NAME.fullmatch(owner):
        raise StepFileError("invalid environment name")
    return project_dir / ".hercules" / FILES_DIRNAME / owner


def _checked_name(name: str) -> str:
    if not FILE_NAME.fullmatch(name) or ".." in name or name.strip() != name:
        raise StepFileError("a file name uses letters, digits, spaces, '.', '_' or '-' (at most 120 characters, no folders)")
    if is_secret_file(name) or sensitive_file(name):
        raise StepFileError(f"{name} looks like a credential or configuration file; it cannot be attached")
    return name


def save_step_file(project_dir: Path, owner: str, name: str, content: bytes, scope: Scope = "environment") -> StepFile:
    _checked_name(name)
    if not content:
        raise StepFileError(f"{name} is empty")
    if len(content) > MAX_FILE_BYTES:
        raise StepFileError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    if sensitive_file("", content[:4096]):
        raise StepFileError(f"{name} holds a private key or service-account credentials; it was not saved")
    folder = files_dir(project_dir, owner, scope)
    existing = {item.name for item in list_step_files(project_dir, owner, scope)}
    if name not in existing and len(existing) >= MAX_FILES:
        raise StepFileError(f"{'a case' if scope == 'case' else 'an environment'} holds at most {MAX_FILES} files; remove one first")
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=folder, prefix=".incoming-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)
        os.replace(temporary, folder / name)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return StepFile(name=name, size=len(content))


def remove_step_file(project_dir: Path, owner: str, name: str, scope: Scope = "environment") -> bool:
    path = files_dir(project_dir, owner, scope) / _checked_name(name)
    found = path.is_file()
    path.unlink(missing_ok=True)
    return found


def list_step_files(project_dir: Path, owner: str, scope: Scope = "environment") -> list[StepFile]:
    folder = files_dir(project_dir, owner, scope)
    if not folder.is_dir():
        return []
    return [
        StepFile(name=path.name, size=path.stat().st_size)
        for path in sorted(folder.iterdir())
        if path.is_file() and not path.is_symlink() and not path.name.startswith(".") and FILE_NAME.fullmatch(path.name)
    ]


def step_file_roots(project_dir: Path, environment: str | None, case_id: str | None) -> tuple[Path, ...]:
    """Where an upload step looks for a file it names: the case's files, the environment's, then the project."""
    owners: list[tuple[str, Scope]] = [(owner, scope) for owner, scope in ((case_id, "case"), (environment, "environment")) if owner]
    roots: list[Path] = []
    for owner, scope in owners:
        try:
            roots.append(files_dir(project_dir, owner, scope))
        except StepFileError:
            continue
    return (*roots, project_dir)
