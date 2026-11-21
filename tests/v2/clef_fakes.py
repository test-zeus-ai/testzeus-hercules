"""A fake local Clef server: the SystemOne decisions API at ``/v1/systemone``, served by an httpx mock transport."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FakeClef:
    """Answers ``target`` with the row named ``wanted`` (else no_match); every other question gets a fixed answer."""

    wanted: str = "Sign in"
    reachable: bool = True
    model: str = "Cloudflare/clef-flash"
    requests: list[httpx.Request] = field(default_factory=list)

    def client(self, timeout: float = 5.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), timeout=timeout)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.reachable:
            raise httpx.ConnectError("connection refused", request=request)
        if request.method != "POST" or request.url.path != "/v1/systemone":
            return httpx.Response(404, json={"detail": "not found"})
        body = json.loads(request.content)
        answers: dict[str, Any] = {}
        for qid, question in body["questions"].items():
            if question["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": 0.97 if qid == "ok" else 0.03}
            elif question["type"] == "choice":
                options = list(question["criteria"])
                rows = [row["id"] for row in body["state"].get("rows", []) if row.get("name") == self.wanted]
                choice = rows[0] if rows and rows[0] in options else options[-1]
                rest = 0.06 / max(len(options) - 1, 1)
                answers[qid] = {"type": "choice", "choice": choice, "confidence": 0.94, "probabilities": {name: (0.94 if name == choice else rest) for name in options}}
        return httpx.Response(200, json={"model": self.model, "answers": answers, "usage": {"input_tokens": 100}})
