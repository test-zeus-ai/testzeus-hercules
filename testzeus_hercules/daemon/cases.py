"""Cases and environments on disk for the daemon: load, compile, save and list, with every path confined.

Cases live in ``<project>/.hercules/cases`` (``<id>.json`` TestCases and ``.feature``
files), environments in ``<project>/.hercules/envs/<name>.json``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from testzeus_hercules.cases.gherkin import GherkinError, compile_feature, load_feature, slugify
from testzeus_hercules.cases.models import EnvironmentRef, Step, TestCase
from testzeus_hercules.daemon.models import Environment

CASE_SUFFIXES = (".json", ".feature")
_CASE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}\.(json|feature)$")
_ENV_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_LISTED = 500


class CaseError(ValueError):
    """The case or environment cannot be used; the message is safe to show to the client."""


class CaseExists(CaseError):
    """Saving would overwrite an existing file."""


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


def compile_gherkin(text: str, source_name: str = "<gherkin>") -> TestCase:
    try:
        return compile_feature(text, source_name=source_name)
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
        return case.model_copy(update={"environment": EnvironmentRef.model_validate(fields)})
    except ValidationError as exc:
        raise CaseError(describe_validation(exc, "environment")) from None


# --------------------------------------------------------------------------- saved cases


def case_file(cases_dir: Path, name: str) -> Path:
    if not _CASE_NAME.match(name) or ".." in name:
        raise CaseError("invalid case name")
    return cases_dir / name


def list_cases(cases_dir: Path, project_dir: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not cases_dir.is_dir():
        return found
    for path in sorted(cases_dir.iterdir())[:_MAX_LISTED]:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in CASE_SUFFIXES:
            continue
        entry: dict[str, Any] = {"name": path.name, "path": _relative(path, project_dir), "kind": path.suffix.lstrip(".")}
        try:
            case = load_case_file(path)
        except CaseError as exc:
            entry.update(valid=False, error=str(exc))
        else:
            entry.update(valid=True, id=case.id, title=case.title, status=case.status, steps=len(case.steps))
        found.append(entry)
    return found


def read_saved_case(cases_dir: Path, name: str) -> tuple[Path, TestCase]:
    path = case_file(cases_dir, name)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(name)
    return path, load_case_file(path)


def save_case(cases_dir: Path, *, test_case: TestCase | None, gherkin: str | None, overwrite: bool) -> tuple[Path, TestCase]:
    cases_dir.mkdir(parents=True, exist_ok=True)
    if gherkin is not None:
        case = compile_gherkin(gherkin)
        path, data = cases_dir / f"{case.id}.feature", gherkin if gherkin.endswith("\n") else gherkin + "\n"
    else:
        assert test_case is not None
        case = test_case
        path, data = cases_dir / f"{case.id}.json", case.model_dump_json(indent=2, exclude_none=True) + "\n"
    _write_new(path, data, overwrite)
    return path, case


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
