from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from testzeus_hercules.kernel.secrets_store import (
    MAX_SECRET_CHARS,
    SECRET_NAME,
    SecretStoreError,
    remove_secret,
    save_secret,
    secret_sources,
    secrets_file,
)

if TYPE_CHECKING:
    from testzeus_hercules.daemon.app import Daemon

_MAX_NAMES = 100


class SecretValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=MAX_SECRET_CHARS)


def _api_error(status: int, message: str) -> Exception:
    from testzeus_hercules.daemon.app import ApiError

    return ApiError(status, message)


def _name(request: Request) -> str:
    name = request.path_params["name"]
    if not SECRET_NAME.fullmatch(name):
        raise _api_error(422, "a secret name is an environment variable name: UPPER_CASE letters, digits and _")
    return str(name)


def _view(daemon: Daemon, names: list[str]) -> list[dict[str, str]]:
    sources = secret_sources(names, daemon.config.env, daemon.config.settings.home_dir)
    return [{"name": name, "source": sources[name]} for name in names]


async def list_secrets(request: Request, daemon: Daemon) -> Response:
    raw = request.query_params.get("names", "")
    names = sorted({name.strip() for name in raw.split(",") if name.strip()})
    if len(names) > _MAX_NAMES:
        raise _api_error(422, f"ask for at most {_MAX_NAMES} names")
    bad = [name for name in names if not SECRET_NAME.fullmatch(name)]
    if bad:
        raise _api_error(422, f"not environment variable names: {', '.join(bad)}")
    return JSONResponse({"secrets": _view(daemon, names), "file": str(secrets_file(daemon.config.settings.home_dir))})


async def put_secret(request: Request, daemon: Daemon) -> Response:
    from testzeus_hercules.daemon.app import _body, _parse

    name = _name(request)
    body: SecretValue = _parse(SecretValue, await _body(request))
    try:
        await asyncio.to_thread(save_secret, daemon.config.settings.home_dir, name, body.value)
    except SecretStoreError as exc:
        raise _api_error(422, str(exc)) from None
    view = _view(daemon, [name])[0]
    note = f"saved, but the environment variable {name} is set where the daemon started and wins over the saved value" if view["source"] == "env" else None
    return JSONResponse({**view, "note": note})


async def delete_secret(request: Request, daemon: Daemon) -> Response:
    name = _name(request)
    try:
        removed = await asyncio.to_thread(remove_secret, daemon.config.settings.home_dir, name)
    except SecretStoreError as exc:
        raise _api_error(409, str(exc)) from None
    return JSONResponse({**_view(daemon, [name])[0], "removed": removed})
