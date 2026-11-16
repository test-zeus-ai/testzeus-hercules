"""Preflight: verify a model server before Hercules relies on it.

``probe_server`` runs, in order: reachability (``GET /v1/models``), server
version, model resolution, the feature probes (tool calling, ``tool_choice``,
JSON schema, image canary) and the effective context window. The context is
read last so the probes have already loaded the model on servers that only
report the context of loaded models (Ollama). Feature results are reused from
the capability cache when the server version and model are unchanged.
"""

from __future__ import annotations

import functools
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from testzeus_hercules.llm.aliases import ModelNotFound, resolve_model
from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache, ImageMode, capability_url
from testzeus_hercules.llm.checks import CheckResult, CheckStatus, overall_status, skipped
from testzeus_hercules.llm.feature_probes import ChatProbe, probe_images, probe_json_schema, probe_tool_calling, probe_tool_choice
from testzeus_hercules.llm.providers import VERBATIM_MODEL_PROVIDERS, Endpoint, redact_url
from testzeus_hercules.llm.server_info import auth_headers, context_check, read_context, read_server_version, version_check
from testzeus_hercules.llm.tiers import TierProfile

CHECK_ORDER = ("reachable", "server_version", "model", "tool_calling", "tool_choice", "json_schema", "images", "context")
_LIST_TIMEOUT_S = 10.0
_MAX_REPORTED_MODELS = 50


class ProbeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    base_url: str
    requested_model: str
    model_id: str | None = None
    server_version: str | None = None
    reachable: bool
    available_models: list[str] = []
    checks: list[CheckResult]
    capabilities: Capabilities | None = None
    from_cache: bool = False

    @property
    def status(self) -> CheckStatus:
        return overall_status(self.checks)


class _Features(BaseModel):
    supports_tools: bool
    supports_tool_choice: bool
    supports_json_schema: bool
    image_mode: ImageMode


async def probe_server(
    endpoint: Endpoint,
    requested_model: str,
    tier_profile: TierProfile,
    *,
    client: httpx.AsyncClient,
    timeout_s: float,
    cache: CapabilityCache | None = None,
    request_params: dict[str, Any] | None = None,
) -> ProbeReport:
    """Probe ``endpoint`` for ``requested_model`` (an id or a canonical alias).

    ``request_params`` (``llm.request_params``) go with every probe request, as with every run's requests;
    cached results are kept apart per set of parameters.
    """
    base_url = redact_url(endpoint.base_url)
    report = functools.partial(ProbeReport, provider=endpoint.provider, base_url=base_url, requested_model=requested_model)
    reachable, models, listing = await _list_models(endpoint, client, min(timeout_s, _LIST_TIMEOUT_S))
    if models is None:
        rest = skipped(CHECK_ORDER[1:], "server not reachable" if not reachable else "model list unavailable")
        return report(reachable=reachable, checks=[listing, *rest])

    version = await read_server_version(endpoint, client, timeout_s)
    checks = [listing, version_check(endpoint.provider, version)]
    reported = models[:_MAX_REPORTED_MODELS]
    try:
        model_id = resolve_model(requested_model, endpoint.provider, models, tier=tier_profile.name, verbatim=endpoint.provider in VERBATIM_MODEL_PROVIDERS)
    except ModelNotFound as exc:
        checks += [CheckResult(name="model", status="fail", detail=str(exc), fix=exc.fix), *skipped(CHECK_ORDER[3:], "model not available")]
        return report(server_version=version, reachable=True, available_models=reported, checks=checks)
    if model_id not in models:
        checks.append(CheckResult(name="model", status="warn", detail=f"{model_id} is not in the server's model list; it is sent as written", fix="check llm.model against the endpoint's model names"))
    else:
        checks.append(CheckResult(name="model", status="ok", detail=model_id if model_id == requested_model else f"{requested_model} -> {model_id}"))

    cached = cache.get(capability_url(base_url, request_params), model_id, version) if cache is not None else None
    if cached is not None:
        features = _Features.model_validate(cached.model_dump(include=set(_Features.model_fields)))
        checks += _cached_checks(features)
    else:
        chat = ChatProbe(client, endpoint, model_id, timeout_s, request_params=request_params)
        feature_checks, features = await _probe_features(chat, endpoint.provider)
        checks += feature_checks
        cache_check = _cache_check(endpoint, chat.cache_hits)
        if cache_check is not None:
            checks.append(cache_check)

    reading = await read_context(endpoint, model_id, client, timeout_s)
    checks.append(context_check(reading, tier_profile, endpoint.provider))
    capabilities = Capabilities(server_version=version, model_id=model_id, context_tokens=reading.tokens, **features.model_dump())
    return report(
        model_id=model_id,
        server_version=version,
        reachable=True,
        available_models=reported,
        checks=checks,
        capabilities=capabilities,
        from_cache=cached is not None,
    )


async def _list_models(endpoint: Endpoint, client: httpx.AsyncClient, timeout_s: float) -> tuple[bool, list[str] | None, CheckResult]:
    """Return (reachable, model ids or None, check)."""
    url = endpoint.base_url.rstrip("/") + "/models"
    shown = redact_url(url)
    try:
        response = await client.get(url, headers=auth_headers(endpoint), timeout=timeout_s)
    except httpx.HTTPError as exc:
        detail = f"cannot reach {shown} ({exc.__class__.__name__})"
        return False, None, CheckResult(name="reachable", status="fail", detail=detail, fix=_start_hint(endpoint.provider, shown))
    if response.status_code in (401, 403):
        fix = "check the API key: set the environment variable named by llm.api_key_env"
        return True, None, CheckResult(name="reachable", status="fail", detail=f"{shown} refused the request (HTTP {response.status_code})", fix=fix)
    try:
        response.raise_for_status()
        data = response.json()["data"]
        models = [str(item["id"]) for item in data]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        detail = f"{shown} did not return an OpenAI-compatible model list ({exc.__class__.__name__}, HTTP {response.status_code})"
        return True, None, CheckResult(name="reachable", status="fail", detail=detail, fix="check llm.base_url; it should end with the server's /v1 path")
    return True, models, CheckResult(name="reachable", status="ok", detail=f"{shown} lists {len(models)} model(s)")


async def _probe_features(chat: ChatProbe, provider: str) -> tuple[list[CheckResult], _Features]:
    tools_check, supports_tools = await probe_tool_calling(chat, provider)
    if supports_tools:
        choice_check, supports_choice = await probe_tool_choice(chat)
    else:
        choice_check, supports_choice = CheckResult(name="tool_choice", status="skipped", detail="tool calling unavailable"), False
    json_check, supports_json = await probe_json_schema(chat)
    image_check, image_mode = await probe_images(chat, provider)
    features = _Features(supports_tools=supports_tools, supports_tool_choice=supports_choice, supports_json_schema=supports_json, image_mode=image_mode)
    return [tools_check, choice_check, json_check, image_check], features


def _cache_check(endpoint: Endpoint, hits: int) -> CheckResult | None:
    """How a caching proxy's response cache is used: reported for LiteLLM, and whenever a probe was answered from a cache."""
    if hits:
        fix = "set llm.cache: bypass (HERCULES_LLM_CACHE=bypass), or turn the proxy's cache off"
        detail = f"{hits} probe(s) were answered from the proxy's cache (x-litellm-cache-key), so they may not show what the model does; run timings would look too fast"
        return CheckResult(name="cache", status="warn", detail=detail, fix=fix)
    if endpoint.provider != "litellm":
        return None
    if endpoint.no_cache:
        return CheckResult(name="cache", status="ok", detail="every request asks the proxy for a fresh answer (llm.cache: bypass)")
    return CheckResult(name="cache", status="ok", detail="the proxy may answer from its cache (llm.cache: allow); cache hits are counted per run")


def _cached_checks(features: _Features) -> list[CheckResult]:
    def check(name: str, supported: bool, *, missing: CheckStatus = "warn") -> CheckResult:
        return CheckResult(name=name, status="ok" if supported else missing, detail=f"{'supported' if supported else 'not supported'} (cached; run with --no-cache to re-probe)")

    return [
        check("tool_calling", features.supports_tools, missing="fail"),
        check("tool_choice", features.supports_tool_choice),
        check("json_schema", features.supports_json_schema),
        check("images", features.image_mode != "none"),
    ]


def _start_hint(provider: str, url: str) -> str:
    hints = {
        "ollama": "start Ollama with `ollama serve` (or open the Ollama app)",
        "lmstudio": "start the LM Studio server with `lms server start`",
        "llamacpp": "start `llama-server -m <model.gguf> --jinja --port 8080`",
        "vllm": "start `vllm serve <model> --port 8000`",
        "mlx-vlm": "start the mlx-vlm server on port 8080",
        "mlx": "start the mlx-lm server on port 8080",
    }
    return hints.get(provider, f"check that the server at {url} is running and reachable, and that llm.base_url is correct")
