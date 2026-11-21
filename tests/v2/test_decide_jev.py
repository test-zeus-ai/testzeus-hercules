"""Tests for the JEV decision backend, against in-process ``httpx.MockTransport`` servers (no network)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import zlib
from typing import Any

import httpx
import pytest
from testzeus_hercules.decide.backends import jev as jev_module
from testzeus_hercules.decide.backends.jev import OPENROUTER_URL, TYPESAFE_URL, JevBackend, image_info
from testzeus_hercules.decide.contract import BoxQuestion, ChoiceQuestion, DecisionRequest, ImagePart, NoulCriteria, NoulQuestion, ScoreQuestion
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.decide.errors import DecisionError, DecisionRejected, DecisionUnavailable

KEY_ENV = "JEV_TEST_KEY"
FAKE_SECRET = "fake-test-credential-0123456789abcdef"
PAGE_TEXT = "PAGE-CONTENT-MARKER-7781"


# --------------------------------------------------------------------------- image fixtures


def png(width: int, height: int, rgb: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """A solid-colour RGB PNG built with zlib only."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


def jpeg_header(width: int, height: int) -> bytes:
    """Just enough JPEG (SOI, APP0, SOF0) for a header parser to read the dimensions."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


RED = png(300, 300, (255, 0, 0))
BLUE = png(300, 300, (0, 0, 255))


# --------------------------------------------------------------------------- requests


def text_request() -> DecisionRequest:
    return DecisionRequest(
        state={"step": "Click Sign in", "tree": [{"md": "12", "role": "button", "name": "Sign in"}], "note": PAGE_TEXT},
        questions={
            "target": ChoiceQuestion(instructions="Which row does `step` act on?", criteria={"12": "button Sign in", "none": "Not listed"}),
            "absent": NoulQuestion(instructions="Is the target missing?"),
        },
    )


def image_request() -> DecisionRequest:
    return DecisionRequest(
        state={"page": {"shots": [{"label": "viewport", "image": ImagePart.from_bytes(RED, "image/png")}]}, "note": PAGE_TEXT},
        questions={"red": NoulQuestion(instructions="Is `page.shots[0].image` mostly red?")},
    )


def all_types_request() -> DecisionRequest:
    return DecisionRequest(
        state={"step": "Click Save", "screen": ImagePart.from_bytes(RED, "image/png")},
        questions={
            "target": ChoiceQuestion(criteria={"12": "button Save", "none": "Not listed"}),
            "urgency": ScoreQuestion(criteria=["low", "mid", "high"]),
            "done": NoulQuestion(instructions="Is the step done?"),
            "where": BoxQuestion(instructions="The Save button in `screen`"),
        },
    )


ALL_ANSWERS = {
    "target": {"type": "choice", "choice": "12", "confidence": 0.9, "probabilities": {"12": 0.95, "none": 0.05}},
    "urgency": {"type": "score", "score": 1.4, "confidence": 0.7, "legend": {"0": "low", "1": "mid", "2": "high"}, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}},
    "done": {"type": "noul", "noul": 0.04},
    "where": {"type": "box", "box": {"top": 0.2, "bottom": 0.6, "left": 0.1, "right": 0.5}, "confidence": None},
}


# --------------------------------------------------------------------------- fake server


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Server:
    """Replays queued responses (or exceptions) and records every request."""

    def __init__(self, *replies: Any, clock: FakeClock | None = None, step_s: float = 0.0) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.clock = clock
        self.step_s = step_s

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.clock is not None:
            self.clock.now += self.step_s
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply(request)
        return reply

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def ok(answers: dict[str, Any], *, model: str = "typesafe/jev-1.14-20261014", cost: float | None = 0.0000172) -> httpx.Response:
    usage: dict[str, Any] = {"input_tokens": 410, "output_tokens": 30}
    if cost is not None:
        usage["cost"] = cost
    return httpx.Response(200, json={"id": "gen-dec-1", "model": model, "provider": "TypeSafe", "answers": answers, "usage": usage})


def text_ok() -> httpx.Response:
    return ok({"target": {"type": "choice", "choice": "12", "confidence": 0.94, "probabilities": {"12": 0.97, "none": 0.03}}, "absent": {"type": "noul", "noul": 0.03}})


def error(status: int, message: str = "Invalid request parameters", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": message}}, headers=headers)


def backend(server: Server, *, clock: FakeClock | None = None, sleeps: list[float] | None = None, **overrides: Any) -> JevBackend:
    recorded = sleeps if sleeps is not None else []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    options: dict[str, Any] = {
        "api_key_env": KEY_ENV,
        "egress_policy": "hybrid",
        "env": {KEY_ENV: FAKE_SECRET},
        "http": httpx.AsyncClient(transport=httpx.MockTransport(server)),
        "clock": clock or FakeClock(),
        "sleep": fake_sleep,
    }
    options.update(overrides)
    return JevBackend(**options)


def decide(engine: JevBackend, request: DecisionRequest, purpose: str = "target") -> Any:
    return asyncio.run(engine.decide(request, purpose=purpose))


# --------------------------------------------------------------------------- construction


def test_implements_decision_engine_and_names_its_transport() -> None:
    assert isinstance(backend(Server(text_ok())), DecisionEngine)
    assert backend(Server(text_ok())).engine == "jev-openrouter"
    assert backend(Server(text_ok()), transport="typesafe").engine == "jev-typesafe"


def test_default_models_are_pinned_per_transport() -> None:
    assert backend(Server(text_ok())).model == "typesafe/jev-1.14"
    assert backend(Server(text_ok()), transport="typesafe").model == "jev-1.14.0"


@pytest.mark.parametrize(("transport", "model"), [("openrouter", "~typesafe/jev-latest"), ("openrouter", "~typesafe/jev-preview"), ("typesafe", "jev-latest"), ("typesafe", "jev-preview")])
def test_rejects_model_aliases_unless_allowed(transport: str, model: str) -> None:
    with pytest.raises(ValueError, match="pinned"):
        backend(Server(text_ok()), transport=transport, model=model)
    assert backend(Server(text_ok()), transport=transport, model=model, allow_alias=True).model == model


@pytest.mark.parametrize("overrides", [{"transport": "carrier-pigeon"}, {"egress_policy": "anywhere"}, {"api_key_env": "not a name"}, {"max_retries": -1}, {"timeout_s": 0}])
def test_rejects_invalid_configuration(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        backend(Server(text_ok()), **overrides)


def test_repr_never_contains_the_key() -> None:
    engine = backend(Server(text_ok()))
    assert FAKE_SECRET not in repr(engine)
    assert FAKE_SECRET not in str(vars(engine))


# --------------------------------------------------------------------------- OpenRouter wire shape


def test_openrouter_wire_shape_keeps_inline_image_parts_at_depth() -> None:
    server = Server(ok({"red": {"type": "noul", "noul": 0.99}}))
    request = image_request()
    decide(backend(server), request)

    (sent,) = server.requests
    assert sent.method == "POST"
    assert str(sent.url) == OPENROUTER_URL
    assert sent.headers["authorization"] == f"Bearer {FAKE_SECRET}"
    assert sent.headers["content-type"] == "application/json"
    assert "typesafe-danger-zone-images" not in sent.headers
    body = json.loads(sent.content)
    assert body == {"model": "typesafe/jev-1.14", "provider": {"zdr": True, "data_collection": "deny"}, **request.to_wire()}
    image = body["state"]["page"]["shots"][0]["image"]
    assert image == {"type": "image_url", "image_url": {"url": data_url(RED)}}
    assert "assets" not in body


def test_openrouter_text_request_body() -> None:
    server = Server(text_ok())
    decide(backend(server, model="typesafe/jev-1.13"), text_request())
    assert server.bodies == [{"model": "typesafe/jev-1.13", "provider": {"zdr": True, "data_collection": "deny"}, **text_request().to_wire()}]


def test_zdr_is_requested_by_default_and_can_be_turned_off() -> None:
    on = Server(text_ok())
    decide(backend(on), text_request())
    assert on.bodies[0]["provider"] == {"zdr": True, "data_collection": "deny"}
    assert backend(on).zdr is True

    off = Server(text_ok())
    decide(backend(off, zdr=False), text_request())
    assert "provider" not in off.bodies[0]


def test_zdr_is_never_sent_to_the_typesafe_api() -> None:
    server = Server(text_ok())
    decide(backend(server, transport="typesafe"), text_request())
    assert "provider" not in server.bodies[0]


# --------------------------------------------------------------------------- response mapping


def test_maps_every_answer_type_and_records_provenance() -> None:
    clock = FakeClock()
    server = Server(ok(ALL_ANSWERS, cost=0.0000435), clock=clock, step_s=0.123)
    decision = decide(backend(server, clock=clock), all_types_request())

    assert decision.engine == "jev-openrouter"
    assert decision.model_version == "typesafe/jev-1.14-20261014"
    assert decision.cost_usd == pytest.approx(0.0000435)
    assert decision.latency_ms == 123
    assert decision.cached is False

    choice = decision.choice("target")
    assert (choice.choice, choice.confidence, choice.probabilities) == ("12", 0.9, {"12": 0.95, "none": 0.05})
    score = decision.score("urgency")
    assert (score.score, score.confidence, score.legend) == (1.4, 0.7, {"0": "low", "1": "mid", "2": "high"})
    assert score.probabilities == {"0": 0.1, "1": 0.4, "2": 0.5}
    assert decision.noul("done").noul == 0.04
    box = decision.box("where")
    assert (box.box.top, box.box.bottom, box.box.left, box.box.right, box.confidence) == (0.2, 0.6, 0.1, 0.5, None)


def test_box_confidence_and_score_without_legend() -> None:
    answers = {
        **ALL_ANSWERS,
        "where": {"type": "box", "box": {"top": 0.25, "bottom": 0.75, "left": 0.25, "right": 0.76}, "confidence": 0.95},
        "urgency": {"type": "score", "score": 2.0, "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0}},
    }
    decision = decide(backend(Server(ok(answers))), all_types_request())
    assert decision.box("where").confidence == 0.95
    assert decision.score("urgency").legend == {"0": "low", "1": "mid", "2": "high"}
    assert decision.score("urgency").confidence is None


def test_clamps_rounding_noise_in_probabilities() -> None:
    answers = {"target": {"type": "choice", "choice": "12", "confidence": 1.0000002, "probabilities": {"12": 1.0000001, "none": -0.0}}, "absent": {"type": "noul", "noul": 1.0000003}}
    decision = decide(backend(Server(ok(answers))), text_request())
    assert decision.choice("target").confidence == 1.0
    assert decision.noul("absent").noul == 1.0


def test_missing_cost_is_none() -> None:
    decision = decide(backend(Server(ok(text_ok().json()["answers"], cost=None))), text_request())
    assert decision.cost_usd is None


def test_answer_that_does_not_match_the_request_is_a_decision_error() -> None:
    answers = {"target": {"type": "choice", "choice": "99", "confidence": 0.9, "probabilities": {"99": 0.9}}, "absent": {"type": "noul", "noul": 0.1}}
    with pytest.raises(DecisionError, match="not one of the options") as caught:
        decide(backend(Server(ok(answers))), text_request())
    assert not isinstance(caught.value, (DecisionRejected, DecisionUnavailable))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"model": "typesafe/jev-1.14", "usage": {}}),
        httpx.Response(200, json={"model": "typesafe/jev-1.14", "answers": {"target": {"type": "mystery"}}, "usage": {}}),
        httpx.Response(200, json={"answers": {}, "usage": {}}),
    ],
)
def test_malformed_response_is_a_decision_error(response: httpx.Response) -> None:
    with pytest.raises(DecisionError) as caught:
        decide(backend(Server(response)), text_request())
    assert not isinstance(caught.value, (DecisionRejected, DecisionUnavailable))


# --------------------------------------------------------------------------- egress


@pytest.mark.parametrize(
    ("policy", "with_image", "allowed"),
    [("local", False, False), ("local", True, False), ("hybrid-text", False, True), ("hybrid-text", True, False), ("hybrid", False, True), ("hybrid", True, True)],
)
def test_egress_matrix(policy: str, with_image: bool, allowed: bool) -> None:
    reply = ok({"red": {"type": "noul", "noul": 0.99}}) if with_image else text_ok()
    server = Server(reply)
    engine = backend(server, egress_policy=policy)
    request = image_request() if with_image else text_request()
    if allowed:
        decide(engine, request)
        assert len(server.requests) == 1
    else:
        with pytest.raises(DecisionRejected, match="egress"):
            decide(engine, request)
        assert server.requests == []


def test_box_question_without_an_image_is_rejected_before_sending() -> None:
    server = Server(text_ok())
    request = DecisionRequest(state={"step": "x"}, questions={"where": BoxQuestion(instructions="the button")})
    with pytest.raises(DecisionRejected, match="box"):
        decide(backend(server), request)
    assert server.requests == []


# --------------------------------------------------------------------------- image checks


def test_image_info_reads_png_and_jpeg_headers() -> None:
    assert image_info(png(300, 281)) == ("png", 300, 281)
    assert image_info(jpeg_header(640, 480)) == ("jpeg", 640, 480)
    with pytest.raises(ValueError):
        image_info(b"RIFF\x00\x00\x00\x00WEBPVP8 ")


@pytest.mark.parametrize(
    ("data", "mime", "accepted"),
    [
        (png(280, 280), "image/png", True),
        (png(279, 400), "image/png", False),
        (png(400, 279), "image/png", False),
        (png(1569, 300), "image/png", False),
        (jpeg_header(300, 300), "image/jpeg", True),
        (jpeg_header(200, 300), "image/jpeg", False),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32, "image/png", False),
        (jpeg_header(300, 300), "image/png", False),
    ],
)
def test_image_size_and_format_checks(data: bytes, mime: str, accepted: bool) -> None:
    server = Server(ok({"seen": {"type": "noul", "noul": 0.5}}))
    request = DecisionRequest(state={"shot": {"type": "image_url", "image_url": {"url": data_url(data, mime)}}}, questions={"seen": NoulQuestion()})
    if accepted:
        decide(backend(server), request)
        assert len(server.requests) == 1
        return
    with pytest.raises(DecisionRejected) as caught:
        decide(backend(server), request)
    assert server.requests == []
    assert base64.b64encode(data).decode("ascii")[:24] not in str(caught.value)


def test_oversized_body_is_rejected_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev_module, "MAX_BODY_BYTES", 200)
    server = Server(text_ok())
    with pytest.raises(DecisionRejected, match="too large"):
        decide(backend(server), text_request())
    assert server.requests == []


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_names_the_variable_but_never_the_key(status: int) -> None:
    server = Server(error(status, f"bad key {FAKE_SECRET}"))
    with pytest.raises(DecisionUnavailable) as caught:
        decide(backend(server), text_request())
    message = str(caught.value)
    assert KEY_ENV in message
    assert "authentication failed" in message
    assert FAKE_SECRET not in message
    assert len(server.requests) == 1


@pytest.mark.parametrize("env", [{}, {KEY_ENV: "   "}])
def test_missing_key_is_unavailable_without_a_request(env: dict[str, str]) -> None:
    server = Server(text_ok())
    with pytest.raises(DecisionUnavailable, match=KEY_ENV):
        decide(backend(server, env=env), text_request())
    assert server.requests == []


@pytest.mark.parametrize("status", [400, 404, 413, 415, 422])
def test_client_errors_are_rejected_with_the_server_message(status: int) -> None:
    server = Server(error(status, "max_tokens_exceeded"))
    with pytest.raises(DecisionRejected, match="max_tokens_exceeded") as caught:
        decide(backend(server), text_request())
    assert PAGE_TEXT not in str(caught.value)
    assert str(status) in str(caught.value)
    assert len(server.requests) == 1


def test_typesafe_validation_detail_omits_echoed_input() -> None:
    detail = {"detail": [{"loc": ["body", "questions", "target", "criteria"], "msg": "Field required", "type": "missing", "input": PAGE_TEXT}]}
    server = Server(httpx.Response(422, json=detail))
    with pytest.raises(DecisionRejected, match="Field required") as caught:
        decide(backend(server, transport="typesafe"), text_request())
    assert "questions.target.criteria" in str(caught.value)
    assert PAGE_TEXT not in str(caught.value)


def test_typesafe_string_detail_is_reported() -> None:
    server = Server(httpx.Response(400, json={"detail": "Images are not turned on"}))
    with pytest.raises(DecisionRejected, match="Images are not turned on"):
        decide(backend(server, transport="typesafe"), text_request())


def test_long_server_messages_are_truncated() -> None:
    server = Server(error(400, "x" * 5000))
    with pytest.raises(DecisionRejected) as caught:
        decide(backend(server), text_request())
    assert len(str(caught.value)) < 600


def test_payment_required_is_unavailable_without_retry() -> None:
    server = Server(error(402, "Insufficient credits"))
    with pytest.raises(DecisionUnavailable, match="credits"):
        decide(backend(server), text_request())
    assert len(server.requests) == 1


# --------------------------------------------------------------------------- retries


def test_retry_after_is_honoured() -> None:
    sleeps: list[float] = []
    server = Server(error(429, "rate limited", headers={"retry-after": "2"}), text_ok())
    decision = decide(backend(server, sleeps=sleeps), text_request())
    assert decision.choice("target").choice == "12"
    assert sleeps == [2.0]
    assert len(server.requests) == 2


def test_overloaded_and_server_errors_back_off_then_succeed() -> None:
    sleeps: list[float] = []
    server = Server(error(529, "overloaded"), error(503, "unavailable"), text_ok())
    decide(backend(server, sleeps=sleeps), text_request())
    assert len(server.requests) == 3
    assert len(sleeps) == 2
    assert 0 < sleeps[0] <= sleeps[1]


def test_retries_are_bounded_then_unavailable() -> None:
    sleeps: list[float] = []
    server = Server(error(500, "server error"))
    with pytest.raises(DecisionUnavailable, match="500"):
        decide(backend(server, sleeps=sleeps, max_retries=2), text_request())
    assert len(server.requests) == 3
    assert len(sleeps) == 2


def test_retry_after_beyond_the_bound_gives_up_at_once() -> None:
    sleeps: list[float] = []
    server = Server(error(429, "rate limited", headers={"retry-after": "120"}), text_ok())
    with pytest.raises(DecisionUnavailable, match="retry"):
        decide(backend(server, sleeps=sleeps), text_request())
    assert sleeps == []
    assert len(server.requests) == 1


def test_unparseable_retry_after_falls_back_to_backoff() -> None:
    sleeps: list[float] = []
    server = Server(error(429, "rate limited", headers={"retry-after": "soon"}), text_ok())
    decide(backend(server, sleeps=sleeps), text_request())
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 10


@pytest.mark.parametrize("exc", [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")])
def test_network_failures_retry_then_unavailable(exc: Exception) -> None:
    server = Server(exc)
    with pytest.raises(DecisionUnavailable):
        decide(backend(server, max_retries=1), text_request())
    assert len(server.requests) == 2


def test_network_failure_then_success() -> None:
    server = Server(httpx.ConnectError("reset"), text_ok())
    assert decide(backend(server), text_request()).noul("absent").noul == 0.03


# --------------------------------------------------------------------------- circuit breaker


def test_breaker_opens_after_three_unavailable_calls_and_fails_fast() -> None:
    clock = FakeClock()
    server = Server(error(503, "down"))
    engine = backend(server, clock=clock, max_retries=0)
    for _ in range(3):
        with pytest.raises(DecisionUnavailable):
            decide(engine, text_request())
    assert len(server.requests) == 3

    clock.now += 299
    with pytest.raises(DecisionUnavailable, match="circuit"):
        decide(engine, text_request())
    assert len(server.requests) == 3


def test_breaker_half_open_trial_success_closes_it() -> None:
    clock = FakeClock()
    server = Server(error(503, "down"), error(503, "down"), error(503, "down"), text_ok())
    engine = backend(server, clock=clock, max_retries=0)
    for _ in range(3):
        with pytest.raises(DecisionUnavailable):
            decide(engine, text_request())
    clock.now += 300
    assert decide(engine, text_request()).choice("target").choice == "12"
    assert decide(engine, text_request()).choice("target").choice == "12"
    assert len(server.requests) == 5


def test_breaker_half_open_trial_failure_reopens_it() -> None:
    clock = FakeClock()
    server = Server(error(503, "down"))
    engine = backend(server, clock=clock, max_retries=0)
    for _ in range(3):
        with pytest.raises(DecisionUnavailable):
            decide(engine, text_request())
    clock.now += 301
    with pytest.raises(DecisionUnavailable, match="503"):
        decide(engine, text_request())
    assert len(server.requests) == 4
    with pytest.raises(DecisionUnavailable, match="circuit"):
        decide(engine, text_request())
    assert len(server.requests) == 4


def test_rejections_and_successes_reset_the_failure_count() -> None:
    clock = FakeClock()
    server = Server(error(503, "down"), error(503, "down"), error(400, "bad"), error(503, "down"), error(503, "down"), text_ok())
    engine = backend(server, clock=clock, max_retries=0)
    for expected in (DecisionUnavailable, DecisionUnavailable, DecisionRejected, DecisionUnavailable, DecisionUnavailable):
        with pytest.raises(expected):
            decide(engine, text_request())
    decide(engine, text_request())
    assert len(server.requests) == 6


def test_local_refusals_do_not_trip_the_breaker() -> None:
    server = Server(text_ok())
    engine = backend(server, env={})
    for _ in range(5):
        with pytest.raises(DecisionUnavailable, match=KEY_ENV) as caught:
            decide(engine, text_request())
        assert "circuit" not in str(caught.value)


# --------------------------------------------------------------------------- TypeSafe transport


def test_typesafe_translates_image_parts_to_assets() -> None:
    server = Server(
        ok({"same": {"type": "noul", "noul": 0.9}, "pick": {"type": "choice", "choice": "red", "confidence": 0.8, "probabilities": {"red": 0.9, "blue": 0.1}}}, model="jev-1.14.0", cost=None)
    )
    red = ImagePart.from_bytes(RED, "image/png")
    blue_jpeg = jpeg_header(320, 320)
    request = DecisionRequest(
        state={"page": {"shots": [red]}, "again": red},
        questions={
            "same": NoulQuestion(instructions={"question": "Same image as this?", "example": ImagePart.from_bytes(blue_jpeg, "image/jpeg")}, criteria=NoulCriteria(true=red, false="different")),
            "pick": ChoiceQuestion(criteria={"red": {"example": red}, "blue": "blue"}),
        },
    )
    decision = decide(backend(server, transport="typesafe"), request)

    (sent,) = server.requests
    assert str(sent.url) == TYPESAFE_URL
    assert sent.headers["typesafe-danger-zone-images"] == "true"
    assert sent.headers["authorization"] == f"Bearer {FAKE_SECRET}"
    body = json.loads(sent.content)
    assert body["model"] == "jev-1.14.0"
    assets = body["assets"]
    assert len(assets) == 2
    red_ref = body["state"]["again"]
    assert body["state"]["page"]["shots"] == [red_ref]
    assert body["questions"]["same"]["criteria"]["true"] == red_ref
    assert body["questions"]["pick"]["criteria"]["red"] == {"example": red_ref}
    assert assets[red_ref] == {"type": "image", "format": "png", "data_base64": base64.b64encode(RED).decode("ascii")}
    jpeg_ref = body["questions"]["same"]["instructions"]["example"]
    assert assets[jpeg_ref]["format"] == "jpg"
    assert all(not value["data_base64"].startswith("data:") for value in assets.values())
    assert "image_url" not in json.dumps({key: value for key, value in body.items() if key != "assets"})
    assert decision.engine == "jev-typesafe"
    assert decision.model_version == "jev-1.14.0"
    assert decision.cost_usd is None


def test_typesafe_references_are_fresh_per_request() -> None:
    server = Server(ok({"red": {"type": "noul", "noul": 0.99}}, cost=None))
    engine = backend(server, transport="typesafe")
    decide(engine, image_request())
    decide(engine, image_request())
    first, second = (set(body["assets"]) for body in server.bodies)
    assert first.isdisjoint(second)


def test_typesafe_text_request_has_no_assets_or_images_header() -> None:
    server = Server(text_ok())
    decide(backend(server, transport="typesafe"), text_request())
    (sent,) = server.requests
    assert "typesafe-danger-zone-images" not in sent.headers
    assert json.loads(sent.content) == {"model": "jev-1.14.0", **text_request().to_wire()}


def test_typesafe_score_legend_maps_references_back_to_images() -> None:
    red = ImagePart.from_bytes(RED, "image/png")
    request = DecisionRequest(state={"shot": red}, questions={"level": ScoreQuestion(criteria=["none", {"example": red}])})

    def reply(sent: httpx.Request) -> httpx.Response:
        body = json.loads(sent.content)
        ref = body["questions"]["level"]["criteria"][1]["example"]
        legend = {"0": "none", "1": {"example": ref}}
        return ok({"level": {"type": "score", "score": 0.9, "confidence": 0.9, "legend": legend, "probabilities": {"0": 0.1, "1": 0.9}}}, cost=None)

    decision = decide(backend(Server(reply), transport="typesafe"), request)
    assert decision.score("level").legend == {"0": "none", "1": {"example": red.to_wire()}}


# --------------------------------------------------------------------------- logging hygiene


def test_logs_never_contain_page_content_images_or_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    decide(backend(Server(ok({"red": {"type": "noul", "noul": 0.99}}))), image_request())
    with pytest.raises(DecisionRejected):
        decide(backend(Server(error(400, "bad"))), image_request())
    with pytest.raises(DecisionUnavailable):
        decide(backend(Server(error(401, "nope"))), text_request())
    text = caplog.text
    assert "red" in text  # the question id is logged
    assert PAGE_TEXT not in text
    assert base64.b64encode(RED).decode("ascii")[:24] not in text
    assert FAKE_SECRET not in text


# --------------------------------------------------------------------------- client lifecycle


def test_owns_and_closes_its_client_when_none_is_given() -> None:
    engine = JevBackend(api_key_env=KEY_ENV, egress_policy="hybrid", env={KEY_ENV: FAKE_SECRET})

    async def run() -> None:
        await engine.aclose()
        await engine.aclose()

    asyncio.run(run())


def test_injected_client_is_not_closed() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(Server(text_ok())))
    engine = backend(Server(text_ok()), http=client)
    asyncio.run(engine.aclose())
    assert not client.is_closed


# --------------------------------------------------------------------------- edge cases


def test_image_info_handles_jpeg_fill_bytes_and_standalone_markers() -> None:
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 300, 400, 1) + b"\x01\x11\x00"
    assert image_info(b"\xff\xd8\xff\xff\xd0" + sof0) == ("jpeg", 400, 300)


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n\x00\x00",
        png(300, 300)[:33] + struct.pack(">I", 8) + b"acTL" + b"\x00" * 12,
        b"\xff\xd8\xff\xda\x00\x02",
        b"\xff\xd8\x00\x00\x00\x00",
    ],
)
def test_image_info_rejects_truncated_animated_or_frameless_files(data: bytes) -> None:
    with pytest.raises(ValueError):
        image_info(data)


def test_oversized_image_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev_module, "MAX_IMAGE_BYTES", 100)
    server = Server(ok({"red": {"type": "noul", "noul": 0.99}}))
    with pytest.raises(DecisionRejected, match="bytes"):
        decide(backend(server), image_request())
    assert server.requests == []


def test_retry_after_as_an_http_date() -> None:
    sleeps: list[float] = []
    server = Server(error(503, "busy", headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}), text_ok())
    decide(backend(server, sleeps=sleeps), text_request())
    assert sleeps == [0.0]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(400, content=b"plain failure"), "Bad Request"),
        (httpx.Response(400, json={"detail": {"message": "unreferenced asset"}}), "unreferenced asset"),
        (httpx.Response(400, json=["odd"]), "Bad Request"),
        (httpx.Response(400, json={"detail": [{"loc": [], "msg": "bad nesting"}]}), "bad nesting"),
    ],
)
def test_server_message_shapes(response: httpx.Response, expected: str) -> None:
    with pytest.raises(DecisionRejected, match=expected):
        decide(backend(Server(response)), text_request())


@pytest.mark.parametrize(
    "answers",
    [
        {**ALL_ANSWERS, "where": {"type": "box", "box": {"top": 0.6, "bottom": 0.2, "left": 0.1, "right": 0.5}, "confidence": 0.5}},
        {**ALL_ANSWERS, "done": "yes"},
        {**ALL_ANSWERS, "urgency": {"type": "score", "score": "high", "probabilities": {"0": 1.0}}},
    ],
)
def test_malformed_answers_are_decision_errors(answers: dict[str, Any]) -> None:
    with pytest.raises(DecisionError) as caught:
        decide(backend(Server(ok(answers))), all_types_request())
    assert not isinstance(caught.value, (DecisionRejected, DecisionUnavailable))


def test_score_above_the_top_level_is_clamped() -> None:
    answers = {**ALL_ANSWERS, "urgency": {"type": "score", "score": 2.0000004, "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0}}}
    assert decide(backend(Server(ok(answers))), all_types_request()).score("urgency").score == 2.0
