"""Any OpenAI-compatible endpoint, and a LiteLLM proxy: keys, extra headers, model names, egress and the proxy cache."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.engine.factory import FactoryDeps, FactoryOptions, RunEngines, build_engines
from testzeus_hercules.kernel.settings import LLMSettings, Settings
from testzeus_hercules.llm.aliases import GEMMA_4_26B_A4B, resolve_model
from testzeus_hercules.llm.client import ChatClient
from testzeus_hercules.llm.preflight import ProbeReport, probe_server
from testzeus_hercules.llm.providers import KEYLESS_PLACEHOLDER, EgressBlocked, EndpointError, check_egress, resolve_endpoint
from testzeus_hercules.llm.tiers import GIB, TIER_PROFILES

from tests.v2.test_llm_fakes import FakeServer

HELLO = [{"role": "user", "content": "hi"}]
LITELLM_MODEL = "gemini/gemini-2.5-flash"


def _settings(tmp_path: Path, **env: str) -> Settings:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home"), **env})


def _chat(server: FakeServer, llm: LLMSettings, env: dict[str, str]) -> Any:
    async def run() -> Any:
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(llm, env), model=LITELLM_MODEL, timeout_s=5.0, http=http, egress_policy="hybrid")
            return await client.chat(HELLO)

    return asyncio.run(run())


def _probe(server: FakeServer, llm: LLMSettings, env: dict[str, str], model: str = LITELLM_MODEL) -> ProbeReport:
    async def run() -> ProbeReport:
        async with server.client() as client:
            return await probe_server(resolve_endpoint(llm, env), model, TIER_PROFILES["standard"], client=client, timeout_s=5.0)

    return asyncio.run(run())


def test_a_litellm_proxy_defaults_to_its_local_port_with_an_optional_key() -> None:
    keyless = resolve_endpoint(LLMSettings(provider="litellm"), env={})
    keyed = resolve_endpoint(LLMSettings(provider="litellm"), env={"LITELLM_API_KEY": "sk-proxy"})
    remote = resolve_endpoint(LLMSettings(provider="litellm", base_url="https://llm-gateway.example.com/v1"), env={})

    assert keyless.base_url == "http://127.0.0.1:4000/v1" and keyless.is_local and keyless.api_key == KEYLESS_PLACEHOLDER
    assert keyed.api_key == "sk-proxy"
    assert not remote.is_local, "local or remote comes from the URL, not the provider"
    with pytest.raises(EgressBlocked):
        check_egress(remote, "local")
    check_egress(keyless, "local")


def test_a_hosted_openai_compatible_endpoint_needs_its_named_key_and_is_remote() -> None:
    llm = LLMSettings(provider="openai-compatible", base_url="https://api.example-inference.com/v1", api_key_env="INFERENCE_KEY")

    with pytest.raises(EndpointError, match="INFERENCE_KEY"):
        resolve_endpoint(llm, env={})
    endpoint = resolve_endpoint(llm, env={"INFERENCE_KEY": "sk-hosted"})
    assert endpoint.api_key == "sk-hosted" and not endpoint.is_local
    assert resolve_endpoint(LLMSettings(provider="openai-compatible", base_url="http://10.1.2.3:9000/v1"), env={}).api_key == KEYLESS_PLACEHOLDER


def test_extra_headers_come_from_the_variables_llm_headers_names_and_reach_every_request() -> None:
    server = FakeServer(kind="generic", models=[LITELLM_MODEL], version=None)
    llm = LLMSettings(provider="litellm", headers={"x-api-key": "GATEWAY_KEY"})

    _chat(server, llm, {"GATEWAY_KEY": "gw-secret"})
    _probe(server, llm, {"GATEWAY_KEY": "gw-secret"})

    assert any(request.url.path.endswith("/models") for request in server.requests)
    assert all(request.headers.get("x-api-key") == "gw-secret" for request in server.requests), "chat requests and the model list alike"
    with pytest.raises(EndpointError, match="GATEWAY_KEY") as missing:
        resolve_endpoint(llm, env={})
    assert "gw-secret" not in str(missing.value)
    assert "gw-secret" not in repr(resolve_endpoint(llm, env={"GATEWAY_KEY": "gw-secret"})), "header values are never printed"


@pytest.mark.parametrize(("provider", "cache", "flag"), [("litellm", None, True), ("litellm", "allow", False), ("litellm", "bypass", True), ("openai-compatible", "bypass", False)])
def test_a_litellm_proxy_is_asked_for_fresh_answers_unless_its_cache_is_allowed(provider: str, cache: str | None, flag: bool) -> None:
    server = FakeServer(kind="generic", models=[LITELLM_MODEL], version=None)
    llm = LLMSettings(provider=provider, base_url="http://127.0.0.1:4000/v1", cache=cache)

    _chat(server, llm, {})
    _probe(server, llm, {})

    assert all((body.get("cache") == {"no-cache": True}) is flag for body in server.chat_requests())


def test_answers_from_the_proxys_cache_are_marked_counted_and_reported() -> None:
    server = FakeServer(kind="generic", models=[LITELLM_MODEL], version=None, cache_hits=True)
    llm = LLMSettings(provider="litellm", cache="allow")

    result = _chat(server, llm, {})
    report = _probe(server, llm, {})

    assert result.cached is True
    cache = next(check for check in report.checks if check.name == "cache")
    assert cache.status == "warn" and "cache" in cache.detail and "llm.cache: bypass" in (cache.fix or "")


def test_a_run_counts_the_local_calls_a_proxy_answered_from_its_cache(tmp_path: Path) -> None:
    server = FakeServer(kind="generic", models=[LITELLM_MODEL], version=None, cache_hits=True)
    settings = _settings(tmp_path, HERCULES_LLM_PROVIDER="litellm", HERCULES_LLM_MODEL=LITELLM_MODEL, HERCULES_LLM_CACHE="allow", HERCULES_EGRESS="hybrid-text")

    async def run() -> RunEngines:
        engines = await build_engines(settings, env={}, options=FactoryOptions(mode="pure"), deps=FactoryDeps(make_http=server.client, detect_memory=lambda: 32 * GIB))
        assert engines.llm is not None
        await engines.llm.client.chat(HELLO)
        await engines.aclose()
        return engines

    engines = asyncio.run(run())
    assert engines.llm is not None and engines.llm.model_id == LITELLM_MODEL, "the proxy's model name is used as written"
    assert engines.usage().cache_hits == 1


@pytest.mark.parametrize("name", [LITELLM_MODEL, "bedrock/anthropic.claude-sonnet", "openai/gpt-4o"])
def test_model_names_with_slashes_pass_through_verbatim_on_generic_endpoints(name: str) -> None:
    listed = ["openai/gpt-4o-mini", "gemini/gemini-2.5-flash-lite", "bedrock/anthropic.claude-sonnet-v2"]

    assert resolve_model(name, "litellm", listed, verbatim=True) == name, "never matched by prefix to another model"
    assert resolve_model(name, "openai-compatible", [], verbatim=True) == name
    assert resolve_model(GEMMA_4_26B_A4B, "litellm", ["google/gemma-4-26b-a4b-it"], verbatim=True) == "google/gemma-4-26b-a4b-it", "a canonical name is still resolved"


def test_doctor_probes_measure_a_proxys_capabilities_instead_of_assuming_them() -> None:
    server = FakeServer(kind="generic", models=[LITELLM_MODEL], version=None, json_schema=False, vision="reject")

    report = _probe(server, LLMSettings(provider="litellm"), {})

    assert report.capabilities is not None
    assert report.capabilities.supports_json_schema is False and report.capabilities.image_mode == "none"
    assert next(check for check in report.checks if check.name == "model").status == "ok"
    unlisted = _probe(FakeServer(kind="generic", models=["other/model"], version=None), LLMSettings(provider="litellm"), {})
    model = next(check for check in unlisted.checks if check.name == "model")
    assert model.status == "warn" and "sent as written" in model.detail


@pytest.mark.parametrize(("egress", "images"), [("hybrid", True), ("hybrid-text", False)])
def test_a_remote_model_gets_screenshots_only_when_the_egress_policy_lets_them_leave(tmp_path: Path, egress: str, images: bool) -> None:
    server = FakeServer(kind="generic", models=[LITELLM_MODEL], version=None)
    settings = _settings(tmp_path, HERCULES_LLM_PROVIDER="openai-compatible", HERCULES_LLM_BASE_URL="https://llm.example.com/v1", HERCULES_LLM_MODEL=LITELLM_MODEL, HERCULES_EGRESS=egress)
    cache_dir = tmp_path / "home"
    from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache

    CapabilityCache(cache_dir / "capabilities.json").put(
        "https://llm.example.com/v1",
        Capabilities(server_version=None, model_id=LITELLM_MODEL, context_tokens=32768, supports_tools=True, supports_tool_choice=False, supports_json_schema=True, image_mode="data_url"),
    )

    async def run() -> RunEngines:
        engines = await build_engines(settings, env={}, options=FactoryOptions(mode="pure"), deps=FactoryDeps(make_http=server.client, detect_memory=lambda: 32 * GIB))
        await engines.aclose()
        return engines

    assert asyncio.run(run()).images is images
