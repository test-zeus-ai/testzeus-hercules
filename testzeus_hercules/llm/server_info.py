"""Server-native endpoints: version and effective context window.

Only Ollama (``/api/version``, ``/api/ps``, ``/api/show``) and llama.cpp
(``/props``) expose these. Ollama cannot set the context through its OpenAI
API and defaults to 4K below 24 GiB of VRAM, so reading the *effective*
context of the loaded model is the most important preflight check.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from testzeus_hercules.llm.checks import CheckResult
from testzeus_hercules.llm.providers import Endpoint, native_root
from testzeus_hercules.llm.tiers import TierProfile

logger = logging.getLogger(__name__)

MIN_CONTEXT_TOKENS = 16384
MIN_OLLAMA_VERSION = (0, 32, 1)
OLLAMA_KEEP_ALIVE = "30m"

_NUM_CTX = re.compile(r"^\s*num_ctx\s+(\d+)\s*$", re.MULTILINE)
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class ContextReading:
    tokens: int | None
    source: str


def auth_headers(endpoint: Endpoint) -> dict[str, str]:
    """The key header, plus the extra headers ``llm.headers`` names (for proxies and gateways)."""
    key = {"api-key": endpoint.api_key} if endpoint.provider == "azure" else {"Authorization": f"Bearer {endpoint.api_key}"}
    return {**key, **dict(endpoint.headers)}


async def _json(client: httpx.AsyncClient, endpoint: Endpoint, method: str, path: str, timeout_s: float, body: dict[str, Any] | None = None) -> Any:
    """Call a native endpoint; any failure (transport, status, JSON) yields None."""
    url = native_root(endpoint.base_url) + path
    try:
        response = await client.request(method, url, json=body, headers=auth_headers(endpoint), timeout=timeout_s)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("%s %s failed: %s", method, path, exc.__class__.__name__)
        return None


async def read_server_version(endpoint: Endpoint, client: httpx.AsyncClient, timeout_s: float) -> str | None:
    if endpoint.provider == "ollama":
        data = await _json(client, endpoint, "GET", "/api/version", timeout_s)
        return _str(data.get("version")) if isinstance(data, dict) else None
    if endpoint.provider == "llamacpp":
        data = await _json(client, endpoint, "GET", "/props", timeout_s)
        return _str(data.get("build_info")) if isinstance(data, dict) else None
    return None


async def read_context(endpoint: Endpoint, model_id: str, client: httpx.AsyncClient, timeout_s: float) -> ContextReading:
    """The context window the server will actually use for ``model_id``, when it reports one."""
    if endpoint.provider == "ollama":
        return await _ollama_context(endpoint, model_id, client, timeout_s)
    if endpoint.provider == "llamacpp":
        data = await _json(client, endpoint, "GET", "/props", timeout_s)
        if isinstance(data, dict):
            settings = data.get("default_generation_settings")
            n_ctx = settings.get("n_ctx") if isinstance(settings, dict) else None
            return ContextReading(_int(n_ctx if n_ctx is not None else data.get("n_ctx")), "llama.cpp /props")
    return ContextReading(None, "not reported by this server")


async def _ollama_context(endpoint: Endpoint, model_id: str, client: httpx.AsyncClient, timeout_s: float) -> ContextReading:
    tokens = await _ollama_loaded_context(endpoint, model_id, client, timeout_s)
    if tokens is None:
        # Load the model (an empty generate request only loads it) so /api/ps shows its real context.
        await _json(client, endpoint, "POST", "/api/generate", timeout_s, {"model": model_id, "keep_alive": OLLAMA_KEEP_ALIVE})
        tokens = await _ollama_loaded_context(endpoint, model_id, client, timeout_s)
    if tokens is not None:
        return ContextReading(tokens, "ollama /api/ps")
    show = await _json(client, endpoint, "POST", "/api/show", timeout_s, {"model": model_id})
    parameters = show.get("parameters") if isinstance(show, dict) else None
    match = _NUM_CTX.search(parameters) if isinstance(parameters, str) else None
    return ContextReading(int(match.group(1)) if match else None, "ollama /api/show num_ctx")


async def _ollama_loaded_context(endpoint: Endpoint, model_id: str, client: httpx.AsyncClient, timeout_s: float) -> int | None:
    data = await _json(client, endpoint, "GET", "/api/ps", timeout_s)
    models = data.get("models") if isinstance(data, dict) else None
    for model in models if isinstance(models, list) else ():
        if isinstance(model, dict) and model_id in (model.get("name"), model.get("model")):
            return _int(model.get("context_length"))
    return None


def required_context(tier: TierProfile) -> int:
    return max(MIN_CONTEXT_TOKENS, tier.context_tokens)


def context_check(reading: ContextReading, tier: TierProfile, provider: str) -> CheckResult:
    need = required_context(tier)
    if reading.tokens is None:
        return CheckResult(
            name="context",
            status="warn",
            detail=f"effective context window unknown ({reading.source}); tier '{tier.name}' needs at least {need} tokens",
            fix=context_fix(provider, need),
        )
    if reading.tokens < need:
        return CheckResult(
            name="context",
            status="fail",
            detail=f"effective context window is {reading.tokens} tokens ({reading.source}); tier '{tier.name}' needs at least {need}",
            fix=context_fix(provider, need),
        )
    return CheckResult(name="context", status="ok", detail=f"{reading.tokens} tokens ({reading.source}); needs at least {need}")


def context_fix(provider: str, need: int) -> str:
    if provider == "ollama":
        return f"restart Ollama with `OLLAMA_CONTEXT_LENGTH={need} ollama serve`, or build the model from a Modelfile with `PARAMETER num_ctx {need}`; confirm with `ollama ps`"
    if provider == "llamacpp":
        return f"start llama-server with `--ctx-size {need}` (per slot: multiply by the --parallel value)"
    if provider == "vllm":
        return f"start vLLM with `--max-model-len {need}`"
    if provider == "lmstudio":
        return f"load the model with a context length of at least {need} (`lms load <model> --context-length {need}`)"
    return f"configure the server to allow at least {need} tokens of context for this model"


def version_check(provider: str, version: str | None) -> CheckResult:
    if version is None:
        if provider == "ollama":
            return CheckResult(name="server_version", status="warn", detail="Ollama did not report its version", fix=_ollama_upgrade())
        return CheckResult(name="server_version", status="skipped", detail="this server does not report a version")
    parsed = _VERSION.match(version)
    if provider == "ollama" and parsed is not None and tuple(int(part) for part in parsed.groups()) < MIN_OLLAMA_VERSION:
        return CheckResult(name="server_version", status="warn", detail=f"Ollama {version} is older than the minimum for reliable tool calling", fix=_ollama_upgrade())
    return CheckResult(name="server_version", status="ok", detail=version)


def _ollama_upgrade() -> str:
    return "upgrade Ollama to {}.{}.{} or newer".format(*MIN_OLLAMA_VERSION)


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
