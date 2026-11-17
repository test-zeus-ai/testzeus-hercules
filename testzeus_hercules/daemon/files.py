from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from testzeus_hercules.cases.step_files import (
    MAX_FILE_BYTES,
    MAX_FILES,
    Scope,
    StepFileError,
    list_step_files,
    remove_step_file,
    save_step_file,
)

if TYPE_CHECKING:
    from testzeus_hercules.daemon.app import Daemon


def _api_error(status: int, message: str) -> Exception:
    from testzeus_hercules.daemon.app import ApiError

    return ApiError(status, message)


def _environment(request: Request, daemon: Daemon) -> tuple[str, Scope]:
    from testzeus_hercules.daemon.cases import load_environment

    name = str(request.path_params["name"])
    if load_environment(daemon.config.paths.envs_dir, name) is None:
        raise _api_error(404, "environment not found: save it first, then add files")
    return name, "environment"


def _case(request: Request, daemon: Daemon) -> tuple[str, Scope]:
    from testzeus_hercules.daemon.cases import CaseError, read_saved_case

    try:
        _, case = read_saved_case(daemon.config.paths.cases_dir, str(request.path_params["name"]))
    except FileNotFoundError:
        raise _api_error(404, "case not found: save it first, then add files") from None
    except CaseError as exc:
        raise _api_error(422, str(exc)) from None
    return case.id, "case"


def _owner(request: Request, daemon: Daemon) -> tuple[str, Scope]:
    return _case(request, daemon) if request.url.path.startswith("/api/cases/") else _environment(request, daemon)


def _filename(request: Request) -> str:
    return str(request.path_params["filename"])


def _listing(daemon: Daemon, owner: str, scope: Scope) -> dict[str, object]:
    files = list_step_files(daemon.config.settings.project_dir, owner, scope)
    return {scope: owner, "files": [asdict(item) for item in files], "max_files": MAX_FILES, "max_bytes": MAX_FILE_BYTES}


async def list_files(request: Request, daemon: Daemon) -> Response:
    return JSONResponse(_listing(daemon, *_owner(request, daemon)))


async def put_file(request: Request, daemon: Daemon) -> Response:
    owner, scope = _owner(request, daemon)
    name = _filename(request)
    content = await request.body()
    try:
        saved = await asyncio.to_thread(save_step_file, daemon.config.settings.project_dir, owner, name, content, scope)
    except StepFileError as exc:
        raise _api_error(422, str(exc)) from None
    return JSONResponse({"file": asdict(saved), **_listing(daemon, owner, scope)})


async def delete_file(request: Request, daemon: Daemon) -> Response:
    owner, scope = _owner(request, daemon)
    try:
        removed = await asyncio.to_thread(remove_step_file, daemon.config.settings.project_dir, owner, _filename(request), scope)
    except StepFileError as exc:
        raise _api_error(422, str(exc)) from None
    return JSONResponse({"removed": removed, **_listing(daemon, owner, scope)})
