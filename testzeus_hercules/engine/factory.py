"""Build a run's engines from settings: the run mode, model connection, step interpreter and decider.

Every step interpretation and every decision is made by a model; there is no coded
decider to fall back to. Run modes:

* ``pure`` - the local model does everything: it reads steps, writes values and
  makes every decision (:class:`LocalLLMDecider`, with images when the server
  takes them and the tier has a vision budget). When a local Clef server is
  configured (``decider.clef_url``, or ``decider.backend: clef``) and answers a probe,
  it makes the decisions instead and the local model covers its outages;
* ``hybrid`` - the local model reads steps and does every other language task,
  and JEV, a hosted decision model, makes the decisions (:class:`JevFallbackDecider`):
  text only first, with the screenshot when unsure and the egress policy is
  ``hybrid``. When JEV cannot answer a decision, the local model answers that one;
* ``auto`` - ``decider.backend`` from settings when set (``local`` and ``clef`` are
  pure, ``jev`` is hybrid), else hybrid when the JEV key variable is set and the
  egress policy is ``hybrid-text`` or ``hybrid``, else pure.

Both modes need the local model server (it reads the steps); when it cannot be used
the run stops with an error pointing at ``hercules setup`` / ``hercules doctor``. The
``stub`` decider (``--decider stub``) is a test double: it contacts no model and reads
no steps unless a test injects an interpreter (:attr:`FactoryDeps.test_interpreter`).

Model-backed deciders contact only the endpoint resolved from settings, after the
egress check. Capabilities come from the cache ``hercules doctor`` writes, else from
the provider's documented defaults (conservative where unknown). The model is warmed
with a one-token request so the first real decision does not pay the load time.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from testzeus_hercules.decide.backends.clef import CLEF_ENGINE, clef_url, make_clef, probe_clef
from testzeus_hercules.decide.backends.jev import jev_from_settings
from testzeus_hercules.decide.backends.jev_fallback import DEFAULT_IMAGE_GATE, DecisionUsage, JevFallbackDecider
from testzeus_hercules.decide.backends.local_llm import LocalLLMDecider, text_budget_bytes
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.decide.engine import DecisionEngine
from testzeus_hercules.engine.intent import HOSTED_PARALLEL_PARTS, Interpreter, LLMInterpreter, StepInterpreter, case_part_size
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.llm.aliases import ModelNotFound, resolve_model
from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache, capability_url
from testzeus_hercules.llm.client import ChatClient, ChatResult, LLMError, Message
from testzeus_hercules.llm.providers import PROVIDERS, VERBATIM_MODEL_PROVIDERS, EgressBlocked, Endpoint, EndpointError, check_egress, redact_url, resolve_endpoint
from testzeus_hercules.llm.schema_limits import known_schema_limits
from testzeus_hercules.llm.server_info import auth_headers, read_server_version
from testzeus_hercules.llm.structured import StructuredLLM
from testzeus_hercules.llm.tiers import TierProfile, detect_memory_bytes, select_tier

__all__ = [
    "DEFAULT_JEV_KEY_ENV",
    "MODE_CHOICES",
    "TEST_DECIDERS",
    "EngineFactoryError",
    "FactoryDeps",
    "FactoryOptions",
    "MeteredChatClient",
    "ModelCall",
    "ModelConnection",
    "RunEngines",
    "RunMode",
    "TestDecider",
    "build_engines",
    "resolve_mode",
]

logger = logging.getLogger(__name__)

RunMode = Literal["auto", "pure", "hybrid"]
MODE_CHOICES: tuple[RunMode, ...] = ("auto", "pure", "hybrid")
TestDecider = Literal["stub"]
TEST_DECIDERS: tuple[TestDecider, ...] = ("stub",)
DEFAULT_JEV_KEY_ENV = "OPENROUTER_API_KEY"
JEV_TIMEOUT_S = 20.0
_IMAGE_EGRESS = "hybrid"
_HOSTED_EGRESS = frozenset({"hybrid-text", "hybrid"})
_WARMUP_PROMPT = "Reply with OK."
_WARMUP_MAX_TOKENS = 1
# Output budget of a whole-case reading (one structured call for every step of a case).
CASE_READING_MAX_TOKENS = 8192
# Steps whose titles a case-reading part is sized to carry as context (a longer case still fits: parts are a minimum size).
_CASE_TITLES_BUDGET = 60
_OFFLINE_HINT = (
    "every step is read and every decision made by a model, and there is no coded fallback: "
    "start the local model server and check it with `hercules doctor` (or configure one with `hercules setup`)"
)


class EngineFactoryError(RuntimeError):
    """The requested engines cannot be built (bad configuration, refused egress, server unavailable)."""


def _default_http(timeout_s: float, *, local: bool = False) -> httpx.AsyncClient:
    """The model connection's client. A local endpoint (loopback or a configured local host) is never reached through
    the environment's proxy (``HTTP_PROXY`` and friends); a remote one honours it."""
    return httpx.AsyncClient(timeout=timeout_s, follow_redirects=False, trust_env=not local)


@dataclass(frozen=True)
class FactoryOptions:
    mode: RunMode = "auto"
    decider: TestDecider | None = None
    jev_key_env: str | None = None
    warm_up: bool = True
    check_timeout_s: float = 3.0
    image_gate: float = DEFAULT_IMAGE_GATE
    # Where the JEV key was read from when not the environment (e.g. the key file's path); named in the mode's reason, never the key.
    key_origin: str | None = None


def _default_jev(settings: Settings, key_env: str, env: Mapping[str, str]) -> DecisionEngine:
    """JEV as the ``decider`` settings describe it (transport, pinned model, zero data retention)."""
    return jev_from_settings(settings, key_env, env, timeout_s=JEV_TIMEOUT_S)


@dataclass(frozen=True)
class FactoryDeps:
    """Seams for tests: the HTTP client factory, memory detection, the JEV backend, the Clef HTTP client
    and the interpreter a ``stub`` test run reads steps with."""

    # None: the default client (no proxy for a local endpoint, see _default_http).
    make_http: Callable[[float], httpx.AsyncClient] | None = None
    detect_memory: Callable[[], int | None] = detect_memory_bytes
    make_jev: Callable[[Settings, str, Mapping[str, str]], DecisionEngine] = _default_jev
    # The HTTP client for a local Clef server; None lets the Clef engine open and close its own.
    clef_http: Callable[[], httpx.AsyncClient] | None = None
    test_interpreter: Interpreter | None = None


# --------------------------------------------------------------------------- metered client


@dataclass(frozen=True)
class ModelCall:
    """Timing and size of one chat request (never its content)."""

    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str | None
    schema: str | None
    images: int
    # Answered from a caching proxy's cache: its latency says nothing about the model.
    cached: bool = False


class MeteredChatClient(ChatClient):
    """A :class:`ChatClient` that records the latency and token usage of every call."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[ModelCall] = []

    async def chat(self, messages: Sequence[Message], **kwargs: Any) -> ChatResult:
        result = await super().chat(messages, **kwargs)
        response_format = kwargs.get("response_format") or {}
        schema = response_format.get("json_schema", {}).get("name") if isinstance(response_format, Mapping) else None
        self.calls.append(
            ModelCall(
                latency_ms=result.latency_ms,
                prompt_tokens=result.usage.prompt,
                completion_tokens=result.usage.completion,
                finish_reason=result.finish_reason,
                schema=schema,
                images=_count_images(messages),
                cached=result.cached,
            )
        )
        return result


def _count_images(messages: Sequence[Message]) -> int:
    return sum(1 for message in messages if isinstance(message.get("content"), list) for part in message["content"] if isinstance(part, dict) and part.get("type") == "image_url")


# --------------------------------------------------------------------------- results


@dataclass
class ModelConnection:
    endpoint: Endpoint
    model_id: str
    server_version: str | None
    capabilities: Capabilities
    capabilities_source: Literal["cache", "defaults"]
    tier: TierProfile
    client: MeteredChatClient
    structured: StructuredLLM
    http: httpx.AsyncClient = field(repr=False)

    @property
    def calls(self) -> list[ModelCall]:
        return self.client.calls


@dataclass(frozen=True)
class RunUsage:
    """What a run's engines used: local-model tokens, JEV calls and cost."""

    tokens_in: int = 0
    tokens_out: int = 0
    local_calls: int = 0
    # Local-model calls a caching proxy answered from its cache.
    cache_hits: int = 0
    decisions: DecisionUsage | None = None
    # True when the decisions' hosted-or-local decision engine was a Clef server (its calls are Clef calls, not JEV calls).
    clef: bool = False

    @property
    def cost_usd(self) -> float | None:
        return round(self.decisions.cost_usd, 6) if self.decisions is not None else None


@dataclass
class RunEngines:
    """What one run decides and interprets with, plus the connections it owns."""

    decider: DecisionEngine
    decider_name: str
    interpreter: Interpreter | None
    mode: Literal["pure", "hybrid", "test"] = "test"
    mode_reason: str = ""
    images: bool = False
    llm: ModelConnection | None = None
    jev: DecisionEngine | None = None
    notes: tuple[str, ...] = ()
    warmup_ms: int | None = None
    _closed: bool = field(default=False, repr=False)

    @property
    def decider_model(self) -> str | None:
        """The model that makes decisions (JEV's in hybrid mode, Clef's or the local one in pure mode)."""
        if self.jev is not None:
            return str(getattr(self.jev, "model", "") or "") or None
        return self.llm.model_id if self.llm is not None else None

    @property
    def clef(self) -> bool:
        """True when a local Clef server makes the decisions (pure mode)."""
        return self.jev is not None and getattr(self.jev, "engine", None) == CLEF_ENGINE

    @property
    def decider_label(self) -> str:
        """``Clef`` when a Clef server decides, else ``JEV`` (for the calls line of the summary)."""
        return "Clef" if self.clef else "JEV"

    def describe(self) -> str:
        """One line naming the mode, who decides and who does the language work."""
        if self.mode == "test":
            return f"Mode: test (decider {self.decider_name}, a test double; no model)"
        assert self.llm is not None
        local = f"{self.llm.model_id} via {self.llm.endpoint.provider} at {redact_url(self.llm.endpoint.base_url)}, tier {self.llm.tier.name}"
        reason = f" [{self.mode_reason}]" if self.mode_reason else ""
        if self.mode == "hybrid":
            sends = "text and screenshots when unsure" if getattr(self.decider, "images", False) else "text only"
            line = f"Mode: hybrid{reason} - JEV {self.decider_model} decides ({sends}); local model {local} interprets and covers JEV outages"
            zdr = getattr(self.jev, "zdr", None)
            return line + (f"; zero data retention {'requested' if zdr else 'off'}" if isinstance(zdr, bool) else "")
        if self.clef:
            sends = "text and screenshots when unsure" if getattr(self.decider, "images", False) else "text only; screenshots stay with the local model"
            where = getattr(self.jev, "url", "")
            return f"Mode: pure{reason} - Clef {self.decider_model} at {where} decides ({sends}); local model {local} interprets and covers Clef outages"
        vision = "with images" if self.images else "text only"
        return f"Mode: pure{reason} - local model {local} decides and interprets ({vision})"

    def usage(self) -> RunUsage:
        calls = self.llm.calls if self.llm is not None else []
        usage_of = getattr(self.decider, "usage", None)
        decisions = usage_of() if callable(usage_of) else None
        return RunUsage(
            tokens_in=sum(call.prompt_tokens for call in calls),
            tokens_out=sum(call.completion_tokens for call in calls),
            local_calls=len(calls),
            cache_hits=sum(1 for call in calls if call.cached),
            decisions=decisions if isinstance(decisions, DecisionUsage) else None,
            clef=self.clef,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.jev, "aclose", None)
        try:
            if callable(close):
                await close()
        finally:
            if self.llm is not None:
                await self.llm.http.aclose()


# --------------------------------------------------------------------------- building


def resolve_mode(settings: Settings, env: Mapping[str, str], options: FactoryOptions) -> tuple[Literal["pure", "hybrid"], str, str]:
    """The run's mode, why it was chosen, and the JEV key variable; raises :class:`EngineFactoryError`."""
    key_env = options.jev_key_env or settings.decider.api_key_env or DEFAULT_JEV_KEY_ENV
    backend = settings.decider.backend
    egress = settings.egress.policy
    mode: Literal["pure", "hybrid"]
    if options.mode != "auto":
        mode, reason = options.mode, f"--mode {options.mode}"
    elif backend is not None:
        mode, reason = ("hybrid" if backend == "jev" else "pure"), f"decider.backend: {backend}"
    elif not env.get(key_env, "").strip():
        mode, reason = "pure", f"auto: {key_env} is not set"
    elif egress not in _HOSTED_EGRESS:
        mode, reason = "pure", f"auto: egress policy '{egress}' keeps page content local"
    else:
        found = f"{key_env} is read from {options.key_origin}" if options.key_origin else f"{key_env} is set"
        mode, reason = "hybrid", f"auto: {found} and egress policy is '{egress}'"
    if mode == "hybrid":
        if egress not in _HOSTED_EGRESS:
            raise EngineFactoryError(
                f"hybrid mode sends page text to JEV, a hosted service, and the egress policy is '{egress}'; "
                "run with --egress hybrid-text (text only) or --egress hybrid (text, and screenshots when unsure), or use --mode pure"
            )
        if not env.get(key_env, "").strip():
            raise EngineFactoryError(f"hybrid mode needs the JEV API key in the environment variable {key_env} (set it, or name another variable with --jev-key-env)")
    return mode, reason, key_env


async def build_engines(settings: Settings, *, env: Mapping[str, str], options: FactoryOptions = FactoryOptions(), deps: FactoryDeps = FactoryDeps()) -> RunEngines:
    """Build the engines for the mode ``options`` and settings ask for; raises :class:`EngineFactoryError`."""
    if options.decider == "stub":
        return RunEngines(decider=StubDecider(), decider_name="stub", interpreter=deps.test_interpreter)
    mode, reason, key_env = resolve_mode(settings, env, options)
    try:
        connection, warmup_ms = await _connect(settings, env, options, deps)
    except _Unavailable as exc:
        raise EngineFactoryError(f"{exc}; {mode} mode needs the local model: {_OFFLINE_HINT}") from None
    jev: DecisionEngine | None = None
    try:
        jev = deps.make_jev(settings, key_env, env) if mode == "hybrid" else None
        engines = _model_engines(mode, reason, connection, warmup_ms, options, settings, jev)
    except BaseException:
        # Nothing owns the connections yet: close them here.
        try:
            close = getattr(jev, "aclose", None)
            if callable(close):
                await close()
        finally:
            await connection.http.aclose()
        raise
    if mode == "pure":
        try:
            await _attach_clef(engines, settings, deps)
        except BaseException:
            await engines.aclose()
            raise
    return engines


async def _attach_clef(engines: RunEngines, settings: Settings, deps: FactoryDeps) -> None:
    """In pure mode, put a reachable local Clef server in front of the local model (a note says why not when it cannot be used)."""
    url = clef_url(settings)
    if url is None or not isinstance(engines.decider, LocalLLMDecider):
        return
    http = deps.clef_http() if deps.clef_http is not None else None
    probe = await probe_clef(url, model=settings.decider.clef_model, http=http, local_hosts=settings.egress.local_hosts)
    if not probe.ok:
        engines.notes += (f"Clef server {probe.url} is not usable ({probe.detail}); the local model decides",)
        return
    clef = make_clef(url, model=settings.decider.clef_model, http=http, local_hosts=settings.egress.local_hosts)
    engines.decider = JevFallbackDecider(clef, engines.decider, images=settings.decider.clef_images)
    engines.jev = clef


def _model_engines(
    mode: Literal["pure", "hybrid"], reason: str, connection: ModelConnection, warmup_ms: int | None, options: FactoryOptions, settings: Settings, jev: DecisionEngine | None
) -> RunEngines:
    tier, capabilities = connection.tier, connection.capabilities
    # Screenshots go to the model only when it can read them, and, for a remote endpoint, only when the egress
    # policy lets screenshots leave the machine (hybrid; hybrid-text sends text only).
    vision = capabilities.image_mode != "none" and tier.vision_tokens > 0 and (connection.endpoint.is_local or settings.egress.policy == _IMAGE_EGRESS)
    # Picks larger than a share of the model's context are asked in page-order parts (never pruned).
    context = min(filter(None, (capabilities.context_tokens, tier.context_tokens)), default=None)
    local = LocalLLMDecider(connection.structured, model_version=connection.model_id, vision=vision, max_text_bytes=text_budget_bytes(context))
    decider: DecisionEngine = local
    images = vision
    if jev is not None:
        jev_images = settings.egress.policy == _IMAGE_EGRESS
        decider = JevFallbackDecider(jev, local, images=jev_images, image_gate=options.image_gate)
        images = vision or jev_images
    # Reading a case is extraction: greedy decoding (temperature 0) gives the same reading for the same steps.
    case_llm = StructuredLLM(connection.client, capabilities=capabilities, vision_tokens=tier.vision_tokens, max_tokens=CASE_READING_MAX_TOKENS, temperature=0.0)
    cache_dir = HerculesPaths.from_settings(settings).project_hercules_dir / "cache" / "interpret"
    # A long case is read in parts the model's context and output budget hold; a hosted endpoint reads a few at once.
    part_items = case_part_size(context_tokens=context, max_output_tokens=CASE_READING_MAX_TOKENS, items=_CASE_TITLES_BUDGET)
    parallel = 1 if connection.endpoint.is_local else HOSTED_PARALLEL_PARTS
    limits = known_schema_limits(connection.endpoint.base_url)
    interpreter = StepInterpreter(
        LLMInterpreter(connection.structured, model_id=connection.model_id, cache_dir=cache_dir, case_llm=case_llm, part_items=part_items, parallel=parallel, schema_limits=limits)
    )
    notes: tuple[str, ...] = ()
    if connection.capabilities_source == "defaults":
        notes += ("capabilities not measured yet; using the provider's defaults (run `hercules doctor` to measure them)",)
    return RunEngines(
        decider=decider,
        decider_name=mode,
        interpreter=interpreter,
        mode=mode,
        mode_reason=reason,
        images=images,
        llm=connection,
        jev=jev,
        notes=notes,
        warmup_ms=warmup_ms,
    )


class _Unavailable(Exception):
    """The model server cannot be used for this run (a fallback is possible in auto mode)."""


async def _connect(settings: Settings, env: Mapping[str, str], options: FactoryOptions, deps: FactoryDeps) -> tuple[ModelConnection, int | None]:
    try:
        endpoint = resolve_endpoint(settings.llm, env, settings.egress.local_hosts)
    except EndpointError as exc:
        raise _Unavailable(f"model endpoint not configured: {exc}") from None
    try:
        check_egress(endpoint, settings.egress.policy)
    except EgressBlocked as exc:
        raise EngineFactoryError(str(exc)) from None
    tier = select_tier(deps.detect_memory(), settings.llm.tier).profile
    timeout_s = settings.llm.request_timeout_s
    http = deps.make_http(timeout_s) if deps.make_http is not None else _default_http(timeout_s, local=endpoint.is_local)
    try:
        model_id = await _resolve_model(endpoint, settings, tier, http, min(options.check_timeout_s, timeout_s))
        version = await read_server_version(endpoint, http, min(options.check_timeout_s, timeout_s))
        capabilities, source = _capabilities(settings, endpoint, model_id, version)
        client = MeteredChatClient(
            endpoint,
            model=model_id,
            capabilities=capabilities,
            timeout_s=timeout_s,
            max_retries=settings.llm.max_retries,
            http=http,
            egress_policy=settings.egress.policy,
            request_params=settings.llm.request_params,
        )
        warmup_ms = await _warm_up(client) if options.warm_up else None
        client.calls.clear()  # report decision and interpretation calls only
    except BaseException:
        await http.aclose()
        raise
    structured = StructuredLLM(client, capabilities=capabilities, vision_tokens=tier.vision_tokens)
    connection = ModelConnection(
        endpoint=endpoint,
        model_id=model_id,
        server_version=version,
        capabilities=capabilities,
        capabilities_source=source,
        tier=tier,
        client=client,
        structured=structured,
        http=http,
    )
    return connection, warmup_ms


async def _resolve_model(endpoint: Endpoint, settings: Settings, tier: TierProfile, http: httpx.AsyncClient, timeout_s: float) -> str:
    """The served model id, from one ``GET /models`` (the reachability check)."""
    url = endpoint.base_url.rstrip("/") + "/models"
    shown = redact_url(url)
    try:
        response = await http.get(url, headers=auth_headers(endpoint), timeout=timeout_s)
        response.raise_for_status()
        ids = [str(item["id"]) for item in response.json()["data"]]
    except httpx.HTTPStatusError as exc:
        raise _Unavailable(f"model server at {shown} is not usable (HTTP {exc.response.status_code})") from None
    except httpx.HTTPError as exc:
        raise _Unavailable(f"model server at {shown} is not reachable ({type(exc).__name__})") from None
    except (ValueError, KeyError, TypeError) as exc:
        raise _Unavailable(f"model server at {shown} did not return an OpenAI-compatible model list ({type(exc).__name__})") from None
    requested = tier.model_alias if settings.llm.model == "auto" else settings.llm.model
    try:
        return resolve_model(requested, endpoint.provider, ids, tier=tier.name, verbatim=endpoint.provider in VERBATIM_MODEL_PROVIDERS)
    except ModelNotFound as exc:
        raise _Unavailable(str(exc)) from None


def _capabilities(settings: Settings, endpoint: Endpoint, model_id: str, version: str | None) -> tuple[Capabilities, Literal["cache", "defaults"]]:
    cache = CapabilityCache(HerculesPaths.from_settings(settings).capabilities_file)
    cached = cache.get(capability_url(redact_url(endpoint.base_url), settings.llm.request_params), model_id, version)
    if cached is not None:
        return cached, "cache"
    hints = PROVIDERS[endpoint.provider].hints
    defaults = Capabilities(
        server_version=version,
        model_id=model_id,
        context_tokens=None,
        supports_tools=bool(hints.tool_choice),
        supports_tool_choice=bool(hints.tool_choice),
        supports_json_schema=hints.json_schema is True,
        image_mode="data_url" if hints.vision is True and hints.image_urls != "none" else "none",
    )
    return defaults, "defaults"


async def _warm_up(client: ChatClient) -> int:
    """Load the model with a one-token request; returns its latency in milliseconds."""
    started = time.perf_counter()
    try:
        await client.chat([{"role": "user", "content": _WARMUP_PROMPT}], max_tokens=_WARMUP_MAX_TOKENS, temperature=0.0)
    except LLMError as exc:
        raise _Unavailable(f"model warm-up failed: {exc}") from None
    return int((time.perf_counter() - started) * 1000)
