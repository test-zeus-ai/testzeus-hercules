"""Hosted decision backend: JEV, TypeSafe's decision model, over OpenRouter or the TypeSafe API.

JEV answers the contract's question types natively, with calibrated probabilities,
so a :class:`~testzeus_hercules.decide.contract.DecisionRequest` maps onto the wire
almost one to one:

* **OpenRouter** (``POST https://openrouter.ai/api/alpha/decisions``) takes the
  request's ``state`` and ``questions`` as they are. Images stay inline as
  ``image_url`` parts, the only image form that endpoint reads.
* **TypeSafe** (``POST https://api.typesafe.ai/v1/systemone``) takes images as a
  top-level ``assets`` map; each inline part is replaced by a fresh reference
  string and the images header is sent.

JEV is hosted only, so page content leaves the machine. The egress policy decides
what may be sent: ``local`` sends nothing, ``hybrid-text`` sends text but no images,
``hybrid`` sends both. Images are checked locally against the documented limits
(PNG or JPEG, 280 to 1568 px a side) before anything is sent. On OpenRouter every
request asks for zero data retention and no data collection
(``provider: {"zdr": true, "data_collection": "deny"}``) unless ``zdr`` is off.

The same client talks to any JEV-compatible decision server at another ``url`` (a
local Clef server takes the TypeSafe shape at ``/v1/systemone``). A server on this
machine or the local network needs no key and no egress allowance.

A pick with more than :data:`~testzeus_hercules.decide.questions.MAX_TEXT_BYTES` of text
(where JEV switches to its slower long-context mode) or more than 255 options is split
into parts in page order and decided among the parts' winners (see
:mod:`testzeus_hercules.decide.chunking`).

The API key is read from the environment variable named by ``api_key_env`` on each
call and is never stored, logged or put in an error message. Request bodies are
never logged either: they hold page content. Logs carry question ids, sizes and
latency only.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import email.utils
import json
import logging
import os
import re
import struct
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal, get_args

import httpx
from pydantic import TypeAdapter, ValidationError

from testzeus_hercules.decide.chunking import DEFAULT_MAX_PARALLEL, decide_in_chunks
from testzeus_hercules.decide.contract import Answer, BoxQuestion, Decision, DecisionRequest, Question, ScoreQuestion
from testzeus_hercules.decide.errors import DecisionError, DecisionRejected, DecisionUnavailable
from testzeus_hercules.decide.payload import is_image_part, iter_image_parts
from testzeus_hercules.decide.questions import MAX_TEXT_BYTES
from testzeus_hercules.kernel.settings import DeciderTransport, EgressPolicy, Settings
from testzeus_hercules.llm.providers import is_local_url, redact_url

__all__ = [
    "DEFAULT_MODELS",
    "JEV_TIMEOUT_S",
    "IMAGES_HEADER",
    "MAX_IMAGE_SIDE",
    "MIN_IMAGE_SIDE",
    "OPENROUTER_URL",
    "TYPESAFE_URL",
    "ZDR_PROVIDER",
    "JevBackend",
    "image_info",
    "jev_from_settings",
]

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODELS: Mapping[str, str] = {"openrouter": "typesafe/jev-1.14", "typesafe": "jev-1.14.0"}
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
JEV_TIMEOUT_S = 20.0
# OpenRouter provider preferences: route only to providers with zero data retention that do not collect data.
ZDR_PROVIDER: Mapping[str, Any] = {"zdr": True, "data_collection": "deny"}

# Documented request limits. TypeSafe's minimum side (280 px) is stricter than
# OpenRouter's (256 px); using it keeps an image valid on either transport.
MIN_IMAGE_SIDE = 280
MAX_IMAGE_SIDE = 1568
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024 * 1024

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 300.0
MAX_RETRY_AFTER_S = 10.0
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
MAX_ERROR_MESSAGE_CHARS = 300

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})
_AUTH_STATUSES = frozenset({401, 403})
_ENV_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ALIAS_SUFFIXES = ("-latest", "-preview")
_ANSWERS: TypeAdapter[Answer] = TypeAdapter(Answer)

ImageKind = Literal["png", "jpeg"]
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


# --------------------------------------------------------------------------- images


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Start-of-frame markers carry the dimensions; C4 (DHT), C8 (JPG) and CC (DAC) do not.
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})


def image_info(data: bytes) -> tuple[ImageKind, int, int]:
    """Return ``(kind, width, height)`` read from a PNG or JPEG header; ``ValueError`` otherwise."""
    if data.startswith(_PNG_SIGNATURE):
        return ("png", *_png_size(data))
    if data.startswith(b"\xff\xd8"):
        return ("jpeg", *_jpeg_size(data))
    raise ValueError("not a PNG or JPEG file")


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ValueError("PNG header is truncated")
    width, height = struct.unpack(">II", data[16:24])
    position = 8
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        if kind == b"acTL":
            raise ValueError("animated PNGs are not accepted")
        if kind == b"IDAT":
            break
        position += 12 + length
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int]:
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise ValueError("JPEG marker expected")
        marker = data[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if marker in _JPEG_STANDALONE:
            position += 2
            continue
        if marker in (0xD9, 0xDA):
            break
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        if marker in _JPEG_SOF:
            if position + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return width, height
        position += 2 + length
    raise ValueError("JPEG has no frame header before its image data")


def _check_image(part: Mapping[str, Any], number: int) -> None:
    """Reject an image part the API would refuse. Messages never quote the payload."""
    url = part.get("image_url", {}).get("url", "")
    header, _, payload = url.partition(",")
    declared: ImageKind = "png" if header.startswith("data:image/png") else "jpeg"
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise DecisionRejected(f"image #{number} is not valid base64") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise DecisionRejected(f"image #{number} is {len(data)} bytes; JEV accepts at most {MAX_IMAGE_BYTES} bytes per image")
    try:
        kind, width, height = image_info(data)
    except (ValueError, struct.error) as exc:
        raise DecisionRejected(f"image #{number} cannot be sent to JEV: {exc}; send a still PNG or JPEG") from None
    if kind != declared:
        raise DecisionRejected(f"image #{number} is declared image/{declared} but holds {kind.upper()} data")
    if not (MIN_IMAGE_SIDE <= width <= MAX_IMAGE_SIDE and MIN_IMAGE_SIDE <= height <= MAX_IMAGE_SIDE):
        raise DecisionRejected(f"image #{number} is {width}x{height} px; JEV needs {MIN_IMAGE_SIDE} to {MAX_IMAGE_SIDE} px on each side (scale or pad it first)")


# --------------------------------------------------------------------------- TypeSafe assets


def _to_assets(value: Any, assets: dict[str, dict[str, str]], refs: dict[str, str]) -> Any:
    """Copy of ``value`` with each inline image part replaced by an asset reference string."""
    if is_image_part(value):
        url = value["image_url"]["url"]
        reference = refs.get(url)
        if reference is None:
            reference = refs[url] = f"img-{uuid.uuid4().hex}"
            header, _, payload = url.partition(",")
            assets[reference] = {"type": "image", "format": "png" if header.startswith("data:image/png") else "jpg", "data_base64": payload}
        return reference
    if isinstance(value, dict):
        return {key: _to_assets(item, assets, refs) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_assets(item, assets, refs) for item in value]
    return value


def _from_assets(value: Any, parts: Mapping[str, Any]) -> Any:
    """Copy of ``value`` with asset reference strings swapped back for their image parts."""
    if isinstance(value, str):
        return parts.get(value, value)
    if isinstance(value, dict):
        return {key: _from_assets(item, parts) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_assets(item, parts) for item in value]
    return value


# --------------------------------------------------------------------------- answers


def _unit(value: Any) -> Any:
    """Clamp a probability into [0, 1], absorbing rounding noise from the server."""
    return min(max(float(value), 0.0), 1.0) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _clean(raw: dict[str, Any], question: Question | None, parts: Mapping[str, Any]) -> dict[str, Any]:
    kind = raw.get("type")
    if kind == "noul":
        return {"type": "noul", "noul": _unit(raw.get("noul"))}
    if kind == "box":
        box = raw.get("box")
        edges = {edge: _unit(value) for edge, value in box.items()} if isinstance(box, dict) else box
        return {"type": "box", "box": edges, "confidence": _unit(raw.get("confidence"))}
    if kind not in ("choice", "score"):
        raise DecisionError(f"unknown answer type {kind!r}")
    probabilities = raw.get("probabilities")
    cleaned = {**raw, "confidence": _unit(raw.get("confidence"))}
    if isinstance(probabilities, dict):
        cleaned["probabilities"] = {name: _unit(value) for name, value in probabilities.items()}
    if kind == "score":
        legend = raw.get("legend")
        if isinstance(legend, dict):
            cleaned["legend"] = _from_assets(legend, parts)
        elif isinstance(question, ScoreQuestion):
            cleaned["legend"] = {str(index): level for index, level in enumerate(question.criteria)}
        score = raw.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            top = len(question.criteria) - 1 if isinstance(question, ScoreQuestion) else float("inf")
            cleaned["score"] = min(max(float(score), 0.0), top)
    return cleaned


def _answers(payload: Any, request: DecisionRequest, parts: Mapping[str, Any]) -> dict[str, Answer]:
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise DecisionError("JEV response has no answers object")
    mapped: dict[str, Answer] = {}
    for qid, raw in answers.items():
        if not isinstance(raw, dict):
            raise DecisionError(f"JEV answer {qid!r} is not an object")
        try:
            mapped[qid] = _ANSWERS.validate_python(_clean(raw, request.questions.get(qid), parts))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise DecisionError(f"JEV answer {qid!r} is malformed at {where or 'top level'}: {first['msg']}") from None
    return mapped


# --------------------------------------------------------------------------- errors


def _server_message(response: httpx.Response) -> str:
    """The server's own explanation, without any echoed request input."""
    try:
        payload = response.json()
    except ValueError:
        return response.reason_phrase or ""
    if not isinstance(payload, dict):
        return response.reason_phrase or ""
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"]
    if isinstance(detail, list):
        problems = []
        for item in detail:
            if isinstance(item, dict):
                location = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                problems.append(f"{location}: {item.get('msg', '')}" if location else str(item.get("msg", "")))
        return "; ".join(problems)
    return response.reason_phrase or ""


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds the server asks to wait, from ``retry-after`` (seconds or an HTTP date)."""
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)


class _Breaker:
    """Fails fast after ``threshold`` consecutive unavailable calls, then lets one trial through after ``cooldown_s``."""

    def __init__(self, threshold: int, cooldown_s: float, clock: Clock) -> None:
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._open_until: float | None = None
        self._trial = False

    def check(self) -> None:
        if self._open_until is None:
            return
        remaining = self._open_until - self._clock()
        if remaining > 0 or self._trial:
            wait = f"retrying in {remaining:.0f} s" if remaining > 0 else "a trial request is in flight"
            raise DecisionUnavailable(f"JEV circuit open after {self._failures} consecutive failures; {wait}")
        self._trial = True

    def success(self) -> None:
        self._failures = 0
        self._open_until = None
        self._trial = False

    def failure(self) -> None:
        self._failures += 1
        if self._open_until is not None or self._failures >= self._threshold:
            self._open_until = self._clock() + self._cooldown_s
        self._trial = False

    def abandon(self) -> None:
        """The call was cancelled before an answer: let the next call be the trial again (no failure counted)."""
        self._trial = False


# --------------------------------------------------------------------------- engine


class JevBackend:
    """:class:`~testzeus_hercules.decide.engine.DecisionEngine` backed by JEV.

    One HTTP call per request (a pick too large for one call is split, see the module
    docstring). Rate limits, overload, server errors, timeouts and connection failures
    are retried ``max_retries`` times with exponential backoff (honouring
    ``retry-after`` up to :data:`MAX_RETRY_AFTER_S`). After :data:`BREAKER_THRESHOLD`
    consecutive unavailable calls the backend fails fast for
    :data:`BREAKER_COOLDOWN_S` seconds, then lets one trial request through.

    ``url`` points the client at another JEV-compatible server; ``api_key_env`` may
    then be None when that server is local. ``engine_name`` names it in decisions and
    logs (default ``jev-<transport>``).
    """

    def __init__(
        self,
        *,
        transport: DeciderTransport = "openrouter",
        model: str | None = None,
        api_key_env: str | None,
        egress_policy: EgressPolicy,
        env: Mapping[str, str] = os.environ,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        allow_alias: bool = False,
        zdr: bool = True,
        url: str | None = None,
        local_hosts: Iterable[str] = (),
        engine_name: str | None = None,
        max_text_bytes: int = MAX_TEXT_BYTES,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if transport not in get_args(DeciderTransport):
            raise ValueError(f"unknown JEV transport {transport!r}; use one of {', '.join(get_args(DeciderTransport))}")
        if egress_policy not in get_args(EgressPolicy):
            raise ValueError(f"unknown egress policy {egress_policy!r}; use one of {', '.join(get_args(EgressPolicy))}")
        if url is not None and not url.startswith(("http://", "https://")):
            raise ValueError("url must be an http(s) URL")
        local = url is not None and is_local_url(url, local_hosts)
        if api_key_env is None and not local:
            raise ValueError("api_key_env must name the environment variable holding the key (only a local server needs none)")
        if api_key_env is not None and not _ENV_VAR_NAME.fullmatch(api_key_env):
            raise ValueError("api_key_env must be the name of an environment variable, such as OPENROUTER_API_KEY")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or more")
        if max_text_bytes < 1024:
            raise ValueError("max_text_bytes must be at least 1024")
        chosen = model or DEFAULT_MODELS[transport]
        if not allow_alias and (chosen.startswith("~") or chosen.endswith(_ALIAS_SUFFIXES)):
            raise ValueError(f"JEV model {chosen!r} is an alias that can change under you; use a pinned model id such as {DEFAULT_MODELS[transport]!r}")
        self._transport: DeciderTransport = transport
        self._model = chosen
        self._api_key_env = api_key_env
        # A closure, so the key never sits in an attribute where repr() or vars() could show it.
        self._read_key: Callable[[], str] = (lambda: env.get(api_key_env, "").strip()) if api_key_env is not None else (lambda: "")
        self._egress: EgressPolicy = egress_policy
        self._url = url or (OPENROUTER_URL if transport == "openrouter" else TYPESAFE_URL)
        self._local = local
        self._zdr = zdr and transport == "openrouter" and url is None
        self._engine = engine_name or f"jev-{transport}"
        self._max_text_bytes = max_text_bytes
        self._max_parallel = max_parallel
        self._http = http
        self._owns_http = http is None
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._breaker = _Breaker(BREAKER_THRESHOLD, BREAKER_COOLDOWN_S, clock)

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def url(self) -> str:
        """Where requests go (without credentials or query)."""
        return redact_url(self._url)

    @property
    def zdr(self) -> bool:
        """True when requests ask OpenRouter for zero data retention and no data collection."""
        return self._zdr

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return f"JevBackend(engine={self._engine!r}, transport={self._transport!r}, model={self._model!r}, api_key_env={self._api_key_env!r}, egress_policy={self._egress!r})"

    async def aclose(self) -> None:
        """Close the HTTP client if this backend created it."""
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
        """Answer ``request``; a pick too large for one call is split into parts first."""

        async def one(part: DecisionRequest) -> Decision:
            return await self._decide_one(part, purpose)

        return await decide_in_chunks(one, request, max_text_bytes=self._max_text_bytes, max_parallel=self._max_parallel, engine=self._engine)

    async def _decide_one(self, request: DecisionRequest, purpose: str) -> Decision:
        if self._egress == "local" and not self._local:
            raise DecisionRejected(
                "egress policy 'local' forbids sending page content to JEV, a hosted service; "
                "allow it with egress.policy: hybrid-text (text only) or hybrid (text and images), or use the local decider"
            )
        wire = request.to_wire()
        images = list(iter_image_parts(wire))
        self._check_request(request, images)
        url, body, headers, parts = self._prepare(wire, images)
        key = self._read_key()
        if not key and not self._local:
            raise DecisionUnavailable(f"JEV needs an API key: set the environment variable {self._api_key_env}")
        self._breaker.check()
        logger.debug("jev %s request: purpose=%s questions=%s text_bytes=%d images=%d", self.engine, purpose, list(request.questions), request.text_size_bytes(), len(images))
        started = self._clock()
        try:
            response = await self._post(url, body, {**headers, "Authorization": f"Bearer {key}"} if key else headers, key, purpose)
        except DecisionRejected:
            self._breaker.success()  # the server answered; only the request was wrong
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            self._breaker.abandon()  # never leave the breaker waiting on a trial that will not finish
            raise
        except BaseException:
            self._breaker.failure()  # DecisionUnavailable, or anything unexpected
            raise
        self._breaker.success()
        latency_ms = max(round((self._clock() - started) * 1000), 0)
        decision = self._decision(response, request, parts, latency_ms)
        logger.info("jev %s answered purpose=%s questions=%s model=%s latency_ms=%d", self.engine, purpose, list(request.questions), decision.model_version, latency_ms)
        return decision.validate_against(request)

    # ----------------------------------------------------------------------- request

    def _check_request(self, request: DecisionRequest, images: list[dict[str, Any]]) -> None:
        if images and self._egress == "hybrid-text" and not self._local:
            raise DecisionRejected(f"egress policy 'hybrid-text' sends text only, and this request holds {len(images)} image(s); allow images with egress.policy: hybrid")
        boxes = [qid for qid, question in request.questions.items() if isinstance(question, BoxQuestion)]
        if boxes and not images:
            raise DecisionRejected(f"box question(s) {boxes} need an image in the request")
        for number, part in enumerate(images, start=1):
            _check_image(part, number)

    def _prepare(self, wire: dict[str, Any], images: list[dict[str, Any]]) -> tuple[str, bytes, dict[str, str], dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        parts: dict[str, Any] = {}
        url = self._url
        if self._transport == "openrouter":
            body = {"model": self._model, **wire}
            if self._zdr:
                body["provider"] = dict(ZDR_PROVIDER)
        else:
            assets: dict[str, dict[str, str]] = {}
            refs: dict[str, str] = {}
            body = {"model": self._model, **_to_assets(wire, assets, refs)}
            if assets:
                body["assets"] = assets
                by_url = {part["image_url"]["url"]: part for part in images}
                parts = {reference: by_url[image_url] for image_url, reference in refs.items()}
            if assets or any(question.get("type") == "box" for question in wire["questions"].values()):
                headers[IMAGES_HEADER] = "true"
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            raise DecisionRejected(f"request body is too large for JEV ({len(encoded)} bytes; the limit is {MAX_BODY_BYTES}); send fewer or smaller images or less state")
        return url, encoded, headers, parts

    # ----------------------------------------------------------------------- transport

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            # A local server is reached directly, never through an environment proxy; hosted JEV honours the proxy.
            self._http = httpx.AsyncClient(timeout=self._timeout_s, trust_env=not self._local)
        return self._http

    async def _post(self, url: str, body: bytes, headers: dict[str, str], key: str, purpose: str) -> httpx.Response:
        name = "JEV" if self._engine.startswith("jev") else self._engine.capitalize()
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            delay: float | None = None
            try:
                response = await self._client().post(url, content=body, headers=headers, timeout=self._timeout_s)
            except httpx.TimeoutException:
                reason = f"timed out after {self._timeout_s:g} s"
            except httpx.TransportError as exc:
                reason = f"network error ({type(exc).__name__})"
            else:
                status = response.status_code
                if status < 300:
                    return response
                message = self._safe(_server_message(response), key)
                if status in _AUTH_STATUSES:
                    raise DecisionUnavailable(f"{name} authentication failed (HTTP {status}); check {self._api_key_env}")
                if status == 402:
                    raise DecisionUnavailable(f"{name} account cannot pay for requests (HTTP 402: {message}); add credits")
                if status not in _RETRY_STATUSES:
                    raise DecisionRejected(f"{name} rejected the {purpose!r} decision (HTTP {status}): {message}")
                reason = f"HTTP {status}: {message}"
                delay = _retry_after(response)
                if delay is not None and delay > MAX_RETRY_AFTER_S:
                    raise DecisionUnavailable(f"{name} unavailable for {purpose!r} ({reason}); server asks to retry after {delay:.0f} s")
            if attempt + 1 >= attempts:
                raise DecisionUnavailable(f"{name} unavailable for {purpose!r} after {attempts} attempt(s): {reason}")
            wait = delay if delay is not None else min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt)
            logger.warning("jev %s attempt %d/%d failed (%s); retrying in %.2f s", self.engine, attempt + 1, attempts, reason, wait)
            await self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _safe(message: str, key: str) -> str:
        cleaned = " ".join((message.replace(key, "[redacted]") if key else message).split())
        return cleaned if len(cleaned) <= MAX_ERROR_MESSAGE_CHARS else cleaned[: MAX_ERROR_MESSAGE_CHARS - 1] + "…"

    # ----------------------------------------------------------------------- response

    def _decision(self, response: httpx.Response, request: DecisionRequest, parts: Mapping[str, Any], latency_ms: int) -> Decision:
        try:
            payload = response.json()
        except ValueError:
            raise DecisionError("JEV returned a response that is not JSON") from None
        answers = _answers(payload, request, parts)
        model = payload.get("model")
        if not isinstance(model, str) or not model:
            raise DecisionError("JEV response does not name the model that answered")
        usage = payload.get("usage")
        cost = usage.get("cost") if isinstance(usage, dict) else None
        cost_usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None
        return Decision(answers=answers, engine=self.engine, model_version=model, latency_ms=latency_ms, cost_usd=cost_usd)


def jev_from_settings(settings: Settings, key_env: str, env: Mapping[str, str], *, timeout_s: float = JEV_TIMEOUT_S) -> JevBackend:
    """The JEV engine the ``decider`` settings describe (transport, pinned model, ZDR), reading its key from ``key_env``."""
    decider = settings.decider
    return JevBackend(
        transport=decider.jev_transport,
        model=decider.model,
        api_key_env=key_env,
        egress_policy=settings.egress.policy,
        env=env,
        timeout_s=timeout_s,
        zdr=decider.zdr,
        local_hosts=settings.egress.local_hosts,
    )
