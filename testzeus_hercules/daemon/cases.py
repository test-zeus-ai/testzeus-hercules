"""Cases and environments on disk for the daemon: load, compile, save and list, with every path confined.

Cases live in ``<project>/.hercules/cases`` and its folders, up to 8 deep (``<id>.json`` TestCases and ``.feature``
files, each with its test data in ``<name>.data.json``); a case's name is its path there (``Checkout/login.feature``) and
its id is unique across folders. Environments live in ``<project>/.hercules/envs/<name>.json``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import AbstractSet, Any, Literal

from pydantic import ValidationError

from testzeus_hercules.cases.gherkin import DATA_SUFFIX, MAX_DATA_BYTES, FeatureData, GherkinError, compile_feature, data_file, load_feature, read_feature_data, slugify
from testzeus_hercules.cases.step_files import files_dir
from testzeus_hercules.cases.models import BUILTIN_PLACEHOLDERS, EnvironmentRef, Step, TestCase, TestDataValue, placeholder_names_in
from testzeus_hercules.daemon.models import Environment

CASE_SUFFIXES = (".json", ".feature")
_CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.(?i:json|feature)$")
_ENV_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_LISTED = 1000


class CaseError(ValueError):
    """The case or environment cannot be used; the message is safe to show to the client."""


class CaseExists(CaseError):
    """Saving would overwrite an existing file."""

    code = "case_exists"
    other: str | None = None


class CaseIdTaken(CaseError):
    """Another case file already has the id (ids are unique across folders and file types)."""

    code = "case_id_taken"

    def __init__(self, message: str, other: str) -> None:
        super().__init__(message)
        self.other = other


def describe_validation(exc: ValidationError, prefix: str = "") -> str:
    """``loc: message`` for each problem, without echoing input values."""
    parts = []
    for error in exc.errors()[:8]:
        loc = ".".join(str(part) for part in ((prefix,) if prefix else ()) + tuple(error["loc"]))
        parts.append(f"{loc or '<root>'}: {error['msg']}")
    return "; ".join(parts)


def confine(project_dir: Path, raw: str) -> Path:
    """``raw`` (relative to the project, or absolute) resolved and required to stay inside the project."""
    if "\x00" in raw:
        raise CaseError("case_path must be a path inside the project directory")
    root = project_dir.resolve()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise CaseError("case_path must be a path inside the project directory")
    return resolved


def load_case_file(path: Path) -> TestCase:
    """A ``.json`` TestCase or a single-scenario ``.feature`` file."""
    if path.suffix.lower() not in CASE_SUFFIXES:
        raise CaseError(f"unsupported case file {path.name!r}: use a .json test case or a .feature file")
    if not path.is_file():
        raise CaseError(f"case file not found: {path.name}")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise CaseError(f"case file {path.name} is too large")
    if path.suffix.lower() == ".feature":
        try:
            return load_feature(path)
        except (GherkinError, ValidationError) as exc:
            raise CaseError(str(exc) if isinstance(exc, GherkinError) else describe_validation(exc)) from None
    try:
        return TestCase.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise CaseError(f"invalid test case in {path.name}: {describe_validation(exc)}") from None
    except OSError as exc:
        raise CaseError(f"cannot read {path.name} ({type(exc).__name__})") from None


def compile_gherkin(text: str, source_name: str = "<gherkin>", test_data: dict[str, TestDataValue] | None = None) -> TestCase:
    try:
        return compile_feature(text, source_name=source_name, test_data=test_data)
    except GherkinError as exc:
        raise CaseError(str(exc)) from None
    except ValidationError as exc:
        raise CaseError(describe_validation(exc)) from None


def draft_from_description(text: str) -> TestCase:
    """A draft case from plain language: one step per non-empty line (list markers removed)."""
    lines = [_LIST_MARKER.sub("", line).strip() for line in text.splitlines()]
    steps = [line for line in lines if line]
    if not steps:
        raise CaseError("description has no steps")
    title = steps[0][:300]
    try:
        return TestCase(id=slugify(title)[:100].strip("-") or "draft", title=title, description=text[:2000], steps=[Step(action=step[:2000]) for step in steps[:200]], status="draft")
    except ValidationError as exc:
        raise CaseError(describe_validation(exc)) from None


def apply_environment(case: TestCase, envs_dir: Path, *, environment: str | None, base_url: str | None) -> TestCase:
    """Fill the case's environment from a saved environment (request name, else the case's ``ref``) and an explicit base URL."""
    name = environment or case.environment.ref
    fields: dict[str, Any] = case.environment.model_dump(exclude_none=True)
    saved: Environment | None = None
    if name is not None:
        saved = load_environment(envs_dir, name) if _ENV_NAME.match(name) else None
        if saved is None:
            if environment is not None or case.environment.base_url is None:
                raise CaseError(f"unknown environment {name!r}: save it with POST /api/environments first")
        else:
            fields["ref"] = saved.name
            base = case.environment.base_url if environment is None and case.environment.base_url else saved.base_url
            if base is not None:
                fields["base_url"] = base
            # The case's own target wins: a case that tests a native app takes no browser settings, a web case no app.
            native = "mobile" in fields or ("device" not in fields and "viewport" not in fields and saved.mobile is not None)
            keys = ("mobile",) if native else ("viewport", "browser", "device")
            for key in keys:
                value = getattr(saved, key)
                if value is not None and key not in fields:
                    fields[key] = value.model_dump(mode="json", exclude_defaults=True) if key == "mobile" else value
            if not native and saved.browser_options is not None:
                # Option by option: the case's own options win over the environment's.
                own = case.environment.browser_options.model_dump(mode="json", exclude_defaults=True) if case.environment.browser_options else {}
                fields["browser_options"] = {**saved.browser_options.model_dump(mode="json", exclude_defaults=True), **own}
    if base_url is not None:
        fields["base_url"] = base_url
    try:
        environment_ref = EnvironmentRef.model_validate(fields)
    except ValidationError as exc:
        raise CaseError(describe_validation(exc, "environment")) from None
    shared = saved.data() if name is not None and saved is not None else {}
    test_data = {**shared, **case.test_data}
    unknown = sorted(set(placeholder_names_in(environment_ref.base_url or "")) - set(test_data) - BUILTIN_PLACEHOLDERS)
    if unknown:
        raise CaseError(f"environment.base_url uses {', '.join('{{' + item + '}}' for item in unknown)}, which no test data or environment variable defines")
    return case.model_copy(update={"environment": environment_ref, "test_data": test_data})


# --------------------------------------------------------------------------- saved cases and folders

MAX_FOLDER_DEPTH = 8
MAX_FOLDER_NAME = 120
_MAX_FOLDERS = 1000
_FOLDER_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))})
DeleteMode = Literal["move_up", "delete_cases"]
_SYSTEM_FILES = frozenset({".DS_Store"})


def folder_name_error(name: str) -> str | None:
    if not name:
        return "enter a folder name"
    if len(name) > MAX_FOLDER_NAME:
        return f"a folder name has at most {MAX_FOLDER_NAME} characters"
    if _FOLDER_FORBIDDEN.search(name):
        return 'a folder name cannot contain / \\ : * ? " < > | or control characters'
    if name.startswith("."):
        return "a folder name cannot start with a dot"
    if name != name.strip() or name.endswith("."):
        return "a folder name cannot start or end with a space, or end with a dot"
    if name.split(".")[0].lower() in _RESERVED_NAMES:
        return f"{name} is a reserved name on Windows"
    return None


def _system_file(name: str) -> bool:
    return name in _SYSTEM_FILES or name.startswith("._")


def _folder_parts(folder: str) -> list[str]:
    if not folder:
        return []
    parts = folder.split("/")
    if len(parts) > MAX_FOLDER_DEPTH:
        raise CaseError(f"folders go {MAX_FOLDER_DEPTH} levels deep at most")
    for part in parts:
        problem = folder_name_error(part)
        if problem:
            raise CaseError(f"invalid folder {folder!r}: {problem}")
    return parts


def folder_dir(cases_dir: Path, folder: str) -> Path:
    path = cases_dir
    for part in _folder_parts(folder):
        path = path / part
        if path.is_symlink():
            raise CaseError(f"{folder} is a symbolic link")
    return path


def _existing_folder(cases_dir: Path, folder: str) -> Path:
    path = folder_dir(cases_dir, folder)
    if not path.is_dir():
        raise FileNotFoundError(folder)
    return path


def _parent(folder: str) -> str:
    return folder.rpartition("/")[0]


def _joined(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def _folder_name(cases_dir: Path, directory: Path) -> str:
    return "" if directory == cases_dir else directory.relative_to(cases_dir).as_posix()


def spelled_folder(cases_dir: Path, folder: str) -> str:
    path = cases_dir
    for part in _folder_parts(folder):
        entry = path / part
        if entry.is_dir() and not entry.is_symlink() and part not in os.listdir(path):
            entry = _sibling_named(path, part) or entry
        path = entry
    return _folder_name(cases_dir, path)


def case_file(cases_dir: Path, name: str) -> Path:
    folder, _, file = name.rpartition("/")
    if not _CASE_NAME.match(file) or ".." in file:
        raise CaseError("invalid case name")
    return folder_dir(cases_dir, spelled_folder(cases_dir, folder)) / file


def is_feature_data(path: Path) -> bool:
    if not path.name.endswith(DATA_SUFFIX):
        return False
    stem = path.name.removesuffix(DATA_SUFFIX)
    try:
        return any(entry.stem == stem and entry.suffix.lower() == ".feature" and entry.is_file() for entry in path.parent.iterdir())
    except OSError:
        return False


def list_folders(cases_dir: Path, under: str = "", *, limit: int | None = _MAX_FOLDERS) -> list[str]:
    found: list[str] = []

    def full() -> bool:
        return limit is not None and len(found) >= limit

    def walk(directory: Path, prefix: str, depth: int) -> None:
        if depth >= MAX_FOLDER_DEPTH or full():
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
        except OSError:
            return
        for entry in entries:
            if full():
                return
            if entry.is_symlink() or not entry.is_dir() or folder_name_error(entry.name):
                continue
            found.append(prefix + entry.name)
            walk(entry, f"{prefix}{entry.name}/", depth + 1)

    root = folder_dir(cases_dir, under)
    if root.is_dir():
        walk(root, f"{under}/" if under else "", len(_folder_parts(under)))
    return found


def _case_paths(cases_dir: Path, folder: str) -> list[Path]:
    directory = folder_dir(cases_dir, folder)
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
        if not path.is_symlink() and path.is_file() and path.suffix.lower() in CASE_SUFFIXES and not is_feature_data(path)
    ]


def _cases_under(cases_dir: Path, folder: str, *, subfolders: bool = True) -> list[tuple[str, Path]]:
    folders = [folder, *(list_folders(cases_dir, folder, limit=None) if subfolders else [])]
    return [(name, path) for name in folders for path in _case_paths(cases_dir, name)]


def _case_entry(project_dir: Path, folder: str, path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": _joined(folder, path.name), "folder": folder, "path": _relative(path, project_dir), "kind": path.suffix.lstrip(".")}
    try:
        case = load_case_file(path)
    except CaseError as exc:
        entry.update(valid=False, error=str(exc))
    else:
        entry.update(valid=True, id=case.id, title=case.title, status=case.status, steps=len(case.steps), environment=case.environment.ref)
    return entry


def list_cases(cases_dir: Path, project_dir: Path, folders: list[str] | None = None) -> list[dict[str, Any]]:
    return find_cases(cases_dir, project_dir, folders=folders)[0]


def _matches(entry: dict[str, Any], query: str) -> bool:
    return not query or query in " ".join(str(entry.get(key) or "") for key in ("title", "id", "name", "path")).casefold()


def find_cases(cases_dir: Path, project_dir: Path, *, folders: list[str] | None = None, under: str = "", query: str = "", limit: int | None = None) -> tuple[list[dict[str, Any]], bool]:
    """The cases (all, or ``under`` one folder with its subfolders) whose title, id or path contains ``query``, at most
    ``limit`` of them (default: the listing's cap); and whether there were more. Folder and query apply before the limit."""
    limit = _MAX_LISTED if limit is None else limit
    found: list[dict[str, Any]] = []
    if not cases_dir.is_dir():
        return found, False
    if under:
        under = spelled_folder(cases_dir, under)
        if not folder_dir(cases_dir, under).is_dir():
            raise CaseError(f"there is no folder {under}")
        scope = [under, *list_folders(cases_dir, under, limit=None)]
    else:
        scope = ["", *(list_folders(cases_dir) if folders is None else folders)]
    wanted = query.strip().casefold()
    for folder in scope:
        for path in _case_paths(cases_dir, folder):
            entry = _case_entry(project_dir, folder, path)
            if not _matches(entry, wanted):
                continue
            if len(found) >= limit:
                return found, True
            found.append(entry)
    return found, False


def _case_ids(cases_dir: Path) -> set[str]:
    ids: set[str] = set()
    for _, path in _cases_under(cases_dir, ""):
        try:
            ids.add(load_case_file(path).id)
        except CaseError:
            continue
    return ids


def _same_id(cases_dir: Path, case_id: str, path: Path) -> Path | None:
    """Another case file with the id ``case_id``: one named after it first (quick), then any other whose content has it
    (a file need not be named after its id, such as ``pay.feature`` holding ``Scenario: Pay by card``)."""

    def other_than_path(other: Path) -> bool:
        return not (path.exists() and os.path.samefile(other, path))

    folders = ["", *list_folders(cases_dir)]
    named = {f"{case_id}{suffix}" for suffix in CASE_SUFFIXES}
    for folder in folders:
        directory = folder_dir(cases_dir, folder)
        for name in named:
            other = directory / name
            if other.is_file() and not other.is_symlink() and other_than_path(other):
                return other
    for folder in folders:
        for other in _case_paths(cases_dir, folder):
            if other.name in named or not other_than_path(other):
                continue
            try:
                if load_case_file(other).id == case_id:
                    return other
            except CaseError:
                continue
    return None


def read_saved_case(cases_dir: Path, name: str) -> tuple[Path, TestCase]:
    path = case_file(cases_dir, name)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(name)
    return path, load_case_file(path)


def environment_problem(case: TestCase, envs_dir: Path) -> str | None:
    """Why the environment the case names cannot be used, as a run would find (None when it can, or it names none)."""
    name = case.environment.ref
    if not name or case.environment.base_url is not None:
        return None
    path = envs_dir / f"{name}.json"
    if not _ENV_NAME.match(name) or path.is_symlink() or not path.is_file():
        return f"unknown environment {name!r}: save it on the Environments screen (or POST /api/environments) first, or fix the @env tag"
    try:
        Environment.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        return f"the environment {name!r} ({path.name}) is not valid: {describe_validation(exc)}"
    except (OSError, ValueError) as exc:
        return f"the environment {name!r} ({path.name}) cannot be read ({type(exc).__name__})"
    return None


def save_case(
    cases_dir: Path,
    *,
    test_case: TestCase | None,
    gherkin: str | None,
    overwrite: bool,
    test_data: dict[str, TestDataValue] | None = None,
    folder: str = "",
    replace: str | None = None,
    envs_dir: Path | None = None,
) -> tuple[Path, TestCase]:
    """Save a case as ``<id>.feature`` or ``<id>.json`` in ``folder``; or, with ``replace``, write it to that saved case's
    file (same kind and id; a ``.feature`` keeps its test data unless ``test_data`` is given). With ``envs_dir``, the
    environment the case names must exist. Nothing is written when the case is not valid."""
    replacing: Path | None = None
    if replace is not None:
        replacing = case_file(cases_dir, replace)
        if replacing.is_symlink() or not replacing.is_file() or is_feature_data(replacing):
            raise FileNotFoundError(replace)
        kind = "gherkin" if replacing.suffix.lower() == ".feature" else "test_case"
        if (gherkin if kind == "gherkin" else test_case) is None:
            raise CaseError(f"{replace} is a {replacing.suffix} case: give its new content as {kind}")
        directory = replacing.parent
        if gherkin is not None and test_data is None:
            try:
                test_data = read_feature_data(replacing)
            except GherkinError:
                test_data = None
    else:
        folder = spelled_folder(cases_dir, folder)
        directory = folder_dir(cases_dir, folder)
        if folder and not directory.is_dir():
            raise CaseError(f"there is no folder {folder}")
    cases_dir.mkdir(parents=True, exist_ok=True)
    sidecar: str | None = None
    if gherkin is not None:
        case = compile_gherkin(gherkin, test_data=test_data)
        path, data = directory / f"{case.id}.feature", gherkin if gherkin.endswith("\n") else gherkin + "\n"
        sidecar = FeatureData(test_data=case.test_data).model_dump_json(indent=2) + "\n" if case.test_data else None
        if sidecar is not None and len(sidecar.encode("utf-8")) > MAX_DATA_BYTES:
            raise CaseError(f"the test data takes more than {MAX_DATA_BYTES // 1024} KiB once saved; keep large values in files attached to the case")
    else:
        assert test_case is not None
        case = test_case
        path, data = directory / f"{case.id}.json", case.model_dump_json(indent=2, exclude_none=True) + "\n"
    if replacing is not None:
        try:
            old_id: str | None = load_case_file(replacing).id
        except CaseError:
            old_id = None
        if old_id is not None and case.id != old_id:
            raise CaseError(f"the case id would change from {old_id!r} to {case.id!r} (the scenario name or id); keep it, or save a new case and delete this one")
        path, overwrite = replacing, True
    if envs_dir is not None:
        problem = environment_problem(case, envs_dir)
        if problem is not None:
            raise CaseError(problem)
    other = _same_id(cases_dir, case.id, path)
    if other is not None:
        where = _folder_name(cases_dir, other.parent) or "the top level"
        raise CaseIdTaken(
            f"a case with the id {case.id} is already in {where} ({other.name}): case ids are unique across folders and file types, so give this one another id or open that one",
            other=case_name(cases_dir, other),
        )
    try:
        _write_new(path, data, overwrite)
    except CaseExists as exc:
        exc.other = case_name(cases_dir, path)
        raise
    if gherkin is not None:
        _save_feature_data(path, sidecar)
    return path, case


def case_name(cases_dir: Path, path: Path) -> str:
    return path.relative_to(cases_dir).as_posix()


def _remove_case(path: Path) -> str | None:
    try:
        case_id: str | None = load_case_file(path).id
    except CaseError:
        case_id = None
    path.unlink()
    sidecar = data_file(path)
    if path.suffix.lower() == ".feature" and sidecar.is_file() and not sidecar.is_symlink():
        sidecar.unlink()
    return case_id


def _drop_case_files(cases_dir: Path, project_dir: Path, ids: set[str]) -> None:
    if not ids:
        return
    for case_id in ids - _case_ids(cases_dir):
        _remove_tree(files_dir(project_dir, case_id, "case"))


def delete_case(cases_dir: Path, project_dir: Path, name: str) -> Path:
    path = case_file(cases_dir, name)
    if path.is_symlink() or not path.is_file() or is_feature_data(path):
        raise FileNotFoundError(name)
    case_id = _remove_case(path)
    _drop_case_files(cases_dir, project_dir, {case_id} if case_id else set())
    return path


def move_cases(cases_dir: Path, names: list[str], folder: str) -> list[str]:
    folder = spelled_folder(cases_dir, folder)
    target = _existing_folder(cases_dir, folder) if folder else cases_dir
    moves: dict[Path, Path] = {}
    queued: dict[str, str] = {}
    for name in names:
        source = case_file(cases_dir, name)
        if source.is_symlink() or not source.is_file() or is_feature_data(source):
            raise FileNotFoundError(name)
        if source.parent == target or source in moves:
            continue
        destination = target / source.name
        if destination.exists():
            raise CaseExists(f"{folder or 'the top level'} already has a case named {source.name}")
        if source.name.casefold() in queued:
            raise CaseExists(f"{queued[source.name.casefold()]} and {case_name(cases_dir, source)} are both named {source.name}, so they cannot go into one folder")
        queued[source.name.casefold()] = case_name(cases_dir, source)
        moves[source] = destination
    moved = []
    for source, destination in moves.items():
        os.replace(source, destination)
        sidecar = data_file(source)
        if source.suffix.lower() == ".feature" and sidecar.is_file() and not sidecar.is_symlink():
            os.replace(sidecar, data_file(destination))
        moved.append(case_name(cases_dir, destination))
    return moved


def _sibling_named(directory: Path, name: str, *, besides: Path | None = None) -> Path | None:
    if not directory.is_dir():
        return None
    wanted = unicodedata.normalize("NFC", name).casefold()
    return next((entry for entry in directory.iterdir() if entry != besides and unicodedata.normalize("NFC", entry.name).casefold() == wanted), None)


def _checked_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", raw).strip()
    problem = folder_name_error(name)
    if problem:
        raise CaseError(problem)
    return name


def create_folder(cases_dir: Path, parent: str, raw_name: str) -> str:
    name = _checked_name(raw_name)
    parent = spelled_folder(cases_dir, parent)
    if len(_folder_parts(parent)) + 1 > MAX_FOLDER_DEPTH:
        raise CaseError(f"folders go {MAX_FOLDER_DEPTH} levels deep at most")
    directory = _existing_folder(cases_dir, parent) if parent else cases_dir
    clash = _sibling_named(directory, name)
    if clash is not None:
        raise CaseExists(f"{parent or 'the top level'} already has {'a folder' if clash.is_dir() else 'a file'} named {clash.name}")
    (directory / name).mkdir(parents=True)
    return _joined(parent, name)


def rename_folder(cases_dir: Path, folder: str, raw_name: str) -> str:
    if not folder:
        raise CaseError("the top level cannot be renamed")
    folder = spelled_folder(cases_dir, folder)
    source = _existing_folder(cases_dir, folder)
    name = _checked_name(raw_name)
    clash = _sibling_named(source.parent, name, besides=source)
    if clash is not None:
        raise CaseExists(f"{_parent(folder) or 'the top level'} already has a folder named {clash.name}")
    os.rename(source, source.parent / name)
    return _joined(_parent(folder), name)


def _height(cases_dir: Path, folder: str) -> int:
    return 1 + max((other.count("/") - folder.count("/") for other in list_folders(cases_dir, folder, limit=None)), default=0)


def move_folder(cases_dir: Path, folder: str, parent: str) -> str:
    if not folder:
        raise CaseError("the top level cannot be moved")
    folder, parent = spelled_folder(cases_dir, folder), spelled_folder(cases_dir, parent)
    source = _existing_folder(cases_dir, folder)
    if parent == folder or parent.startswith(folder + "/"):
        raise CaseError("a folder cannot go inside itself")
    if parent == _parent(folder):
        raise CaseError(f"{folder} is already there")
    target = _existing_folder(cases_dir, parent) if parent else cases_dir
    if len(_folder_parts(parent)) + _height(cases_dir, folder) > MAX_FOLDER_DEPTH:
        raise CaseError(f"folders go {MAX_FOLDER_DEPTH} levels deep at most")
    clash = _sibling_named(target, source.name)
    if clash is not None:
        raise CaseExists(f"{parent or 'the top level'} already has a folder named {clash.name}")
    os.rename(source, target / source.name)
    return _joined(parent, source.name)


def _move_up(folder: str, source: Path) -> int:
    held = [entry.name for entry in sorted(source.iterdir()) if not _system_file(entry.name)]
    target = source.parent
    clashes = [name for name in held if _sibling_named(target, name, besides=source) is not None]
    if clashes:
        raise CaseExists(f"{_parent(folder) or 'the top level'} already has {', '.join(clashes[:3])}{' and more' if len(clashes) > 3 else ''}; rename or move those first")
    staging = source.with_name(f".{source.name}.{secrets.token_hex(4)}.deleting")
    try:
        os.rename(source, staging)
    except OSError as exc:
        raise CaseError(f"cannot delete {folder} ({type(exc).__name__})") from None
    moved: list[str] = []
    try:
        for name in held:
            os.rename(staging / name, target / name)
            moved.append(name)
    except OSError as exc:
        for name in reversed(moved):
            os.rename(target / name, staging / name)
        os.rename(staging, source)
        raise CaseError(f"cannot move what {folder} holds up a level ({type(exc).__name__}); nothing was changed") from None
    _remove_tree(staging)
    return len(held)


def delete_folder(cases_dir: Path, project_dir: Path, folder: str, mode: DeleteMode = "move_up") -> dict[str, Any]:
    if not folder:
        raise CaseError("the top level cannot be deleted")
    folder = spelled_folder(cases_dir, folder)
    source = _existing_folder(cases_dir, folder)
    if mode == "move_up":
        return {"folder": folder, "mode": mode, "moved": _move_up(folder, source), "deleted": 0}
    found = [path for _, path in _cases_under(cases_dir, folder)]
    keep = set(found) | {data_file(path) for path in found if path.suffix.lower() == ".feature"}
    others = [path for path in source.rglob("*") if path.is_file() and path not in keep and not _system_file(path.name)]
    if others:
        listed = ", ".join(path.relative_to(source).as_posix() for path in others[:3])
        raise CaseError(f"{folder} also holds files that are not cases ({listed}{' and more' if len(others) > 3 else ''}); move them out first, or keep the cases")
    ids = {case_id for case_id in map(_remove_case, found) if case_id}
    _remove_tree(source)
    _drop_case_files(cases_dir, project_dir, ids)
    return {"folder": folder, "mode": mode, "moved": 0, "deleted": len(found)}


def cases_in_folder(cases_dir: Path, project_dir: Path, folder: str, *, subfolders: bool) -> list[dict[str, Any]]:
    folder = spelled_folder(cases_dir, folder)
    if folder:
        _existing_folder(cases_dir, folder)
    return [_case_entry(project_dir, name, path) for name, path in _cases_under(cases_dir, folder, subfolders=subfolders)]


def delete_environment(envs_dir: Path, project_dir: Path, name: str) -> bool:
    if not _ENV_NAME.match(name):
        return False
    path = envs_dir / f"{name}.json"
    if path.is_symlink() or not path.is_file():
        return False
    path.unlink()
    _remove_tree(files_dir(project_dir, name))
    return True


def _remove_tree(folder: Path) -> None:
    if folder.is_dir() and not folder.is_symlink():
        shutil.rmtree(folder)


def _save_feature_data(path: Path, text: str | None) -> None:
    sidecar = data_file(path)
    if text is None:
        if sidecar.is_file() and not sidecar.is_symlink():
            sidecar.unlink()
        return
    _write_new(sidecar, text, True)


# --------------------------------------------------------------------------- environments


def load_environment(envs_dir: Path, name: str) -> Environment | None:
    if not _ENV_NAME.match(name):
        return None
    path = envs_dir / f"{name}.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return Environment.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return None


def environment_data(envs_dir: Path, name: str | None) -> dict[str, TestDataValue]:
    saved = load_environment(envs_dir, name) if name else None
    return saved.data() if saved is not None else {}


def environment_names(envs_dir: Path) -> Callable[[str], AbstractSet[str]]:
    return lambda name: set(environment_data(envs_dir, name))


def list_environments(envs_dir: Path) -> list[Environment]:
    if not envs_dir.is_dir():
        return []
    found = []
    for path in sorted(envs_dir.glob("*.json"))[:_MAX_LISTED]:
        env = load_environment(envs_dir, path.stem)
        if env is not None:
            found.append(env)
    return found


def save_environment(envs_dir: Path, env: Environment, *, overwrite: bool) -> Path:
    envs_dir.mkdir(parents=True, exist_ok=True)
    path = envs_dir / f"{env.name}.json"
    _write_new(path, env.model_dump_json(indent=2) + "\n", overwrite)
    return path


def _write_new(path: Path, data: str, overwrite: bool) -> None:
    """Write ``path`` whole (never half-written). Without ``overwrite`` the file appears only if no other exists at that
    moment: linked into place, which fails when a file was created since the check (two saves of one new name)."""
    if path.is_symlink():
        raise CaseError(f"{path.name} is a symbolic link; refusing to write through it")
    exists = CaseExists(f"{path.name} already exists; pass overwrite=true to replace it")
    if path.exists() and not overwrite:
        raise exists
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(data, encoding="utf-8")
    try:
        if overwrite:
            os.replace(tmp, path)
            return
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise exists from None
        except OSError:  # no hard links on this file system: create it exclusively instead
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                raise exists from None
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(data)
    finally:
        tmp.unlink(missing_ok=True)


def _relative(path: Path, project_dir: Path) -> str:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return path.as_posix()


def case_json(case: TestCase) -> dict[str, Any]:
    return json.loads(case.model_dump_json(exclude_none=True))
