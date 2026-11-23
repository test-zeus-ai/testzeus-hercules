"""A small local pet store API (Starlette) served on a free port, matching ``petstore_api.yaml``.

``/v1/broken-pet`` answers with a body that violates the declared ``Pet`` schema, so
schema checks have something real to catch. ``POST /v1/pets`` needs the header
``X-API-Key: <API_KEY>``.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

SPEC = Path(__file__).resolve().with_name("petstore_api.yaml")
API_KEY = "pets-key-5f2a9c"


def create_app() -> Starlette:
    pets: dict[int, dict[str, Any]] = {1: {"id": 1, "name": "Rex", "tag": "dog"}, 2: {"id": 2, "name": "Tom", "tag": "cat"}}

    async def list_pets(request: Request) -> Response:
        limit = request.query_params.get("limit")
        if limit is None or not limit.isdigit():
            return JSONResponse({"detail": "limit is required"}, status_code=400)
        return JSONResponse(list(pets.values())[: int(limit)])

    async def create_pet(request: Request) -> Response:
        if request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"detail": "missing or wrong API key"}, status_code=401)
        body = await request.json()
        if not isinstance(body, dict) or not body.get("name"):
            return JSONResponse({"detail": "name is required"}, status_code=400)
        pet = {"id": max(pets) + 1, "name": body["name"], "tag": body.get("tag")}
        pets[pet["id"]] = pet
        return JSONResponse(pet, status_code=201, headers={"Location": f"/v1/pets/{pet['id']}"})

    async def show_pet(request: Request) -> Response:
        pet = pets.get(int(request.path_params["pet_id"]))
        return JSONResponse(pet) if pet else JSONResponse({"detail": "no such pet"}, status_code=404)

    async def broken_pet(request: Request) -> Response:
        return JSONResponse({"id": "seven", "tag": 3})

    async def health(request: Request) -> Response:
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/v1/pets", list_pets, methods=["GET"]),
            Route("/v1/pets", create_pet, methods=["POST"]),
            Route("/v1/pets/{pet_id:int}", show_pet, methods=["GET"]),
            Route("/v1/broken-pet", broken_pet, methods=["GET"]),
            Route("/v1/health", health, methods=["GET"]),
        ]
    )


@contextlib.contextmanager
def serve_api() -> Iterator[str]:
    """Serve a fresh store on 127.0.0.1 in a thread; yields the base URL (``http://127.0.0.1:<port>/v1``)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the fixture API did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
