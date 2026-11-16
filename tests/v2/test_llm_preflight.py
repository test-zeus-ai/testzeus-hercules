"""Tests for the model-server preflight probe, against in-process fake servers."""

import asyncio
from pathlib import Path

from testzeus_hercules.kernel.settings import LLMSettings
from testzeus_hercules.llm.aliases import GEMMA_4_26B_A4B, GEMMA_4_E4B
from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache
from testzeus_hercules.llm.preflight import CheckResult, ProbeReport, probe_server
from testzeus_hercules.llm.providers import Endpoint, resolve_endpoint
from testzeus_hercules.llm.tiers import TIER_PROFILES, TierProfile

from tests.v2.test_llm_fakes import FakeServer


def _endpoint(provider: str = "ollama", base_url: str | None = None) -> Endpoint:
    return resolve_endpoint(LLMSettings(provider=provider, base_url=base_url), env={})


def _probe(
    server: FakeServer,
    *,
    provider: str = "ollama",
    model: str = GEMMA_4_26B_A4B,
    tier: TierProfile = TIER_PROFILES["recommended"],
    cache: CapabilityCache | None = None,
    base_url: str | None = None,
    request_params: dict[str, object] | None = None,
) -> ProbeReport:
    async def run() -> ProbeReport:
        async with server.client() as client:
            return await probe_server(_endpoint(provider, base_url), model, tier, client=client, timeout_s=5.0, cache=cache, request_params=request_params)

    return asyncio.run(run())


def _check(report: ProbeReport, name: str) -> CheckResult:
    matches = [check for check in report.checks if check.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} check in {[c.name for c in report.checks]}"
    return matches[0]


def test_ollama_with_32k_context_passes() -> None:
    server = FakeServer(kind="ollama", context_length=32768)

    report = _probe(server)

    assert report.reachable is True
    assert report.model_id == "gemma4:26b-a4b-it-q4_K_M"
    assert report.server_version == "0.32.1"
    assert _check(report, "context").status == "ok"
    assert _check(report, "tool_calling").status == "ok"
    assert _check(report, "tool_choice").status == "warn"
    assert _check(report, "json_schema").status == "ok"
    assert _check(report, "images").status == "ok"
    assert report.status == "warn"
    assert report.capabilities == Capabilities(
        server_version="0.32.1",
        model_id="gemma4:26b-a4b-it-q4_K_M",
        context_tokens=32768,
        supports_tools=True,
        supports_tool_choice=False,
        supports_json_schema=True,
        image_mode="data_url",
    )


def test_ollama_with_default_4k_context_fails_with_the_exact_fix() -> None:
    server = FakeServer(kind="ollama", context_length=4096)

    report = _probe(server)

    context = _check(report, "context")
    assert context.status == "fail"
    assert "4096" in context.detail
    assert context.fix is not None
    assert "OLLAMA_CONTEXT_LENGTH=32768 ollama serve" in context.fix
    assert "PARAMETER num_ctx 32768" in context.fix
    assert report.status == "fail"


def test_context_below_tier_requirement_fails_even_above_16k() -> None:
    report = _probe(FakeServer(kind="ollama", context_length=16384), tier=TIER_PROFILES["recommended"])

    assert _check(report, "context").status == "fail"


def test_context_meeting_floor_tier_passes() -> None:
    report = _probe(FakeServer(kind="ollama", context_length=16384), model=GEMMA_4_E4B, tier=TIER_PROFILES["floor"])

    assert report.model_id == "gemma4:e4b"
    assert _check(report, "context").status == "ok"


def test_old_ollama_version_warns() -> None:
    report = _probe(FakeServer(kind="ollama", version="0.30.0"))

    version = _check(report, "server_version")
    assert version.status == "warn"
    assert "0.32.1" in (version.fix or "")


def test_llamacpp_context_and_version_come_from_props() -> None:
    server = FakeServer(kind="llamacpp", models=["gemma-4-26B-A4B-it-Q4_K_M.gguf"], context_length=32768, tool_choice=True)

    report = _probe(server, provider="llamacpp")

    assert report.model_id == "gemma-4-26B-A4B-it-Q4_K_M.gguf"
    assert report.server_version == "b6000-abc123"
    assert _check(report, "context").status == "ok"
    assert _check(report, "tool_choice").status == "ok"
    assert report.capabilities is not None and report.capabilities.supports_tool_choice is True
    assert report.status == "ok"


def test_llamacpp_small_context_fix_mentions_ctx_size() -> None:
    server = FakeServer(kind="llamacpp", models=["gemma-4-26B-A4B-it-Q4_K_M.gguf"], context_length=8192)

    context = _check(_probe(server, provider="llamacpp"), "context")

    assert context.status == "fail"
    assert "--ctx-size 32768" in (context.fix or "")


def test_mlx_style_server_without_json_schema_warns_and_context_is_unknown() -> None:
    server = FakeServer(kind="generic", models=["mlx-community/gemma-4-26B-A4B-it-4bit"], json_schema=False, version=None)

    report = _probe(server, provider="mlx-vlm")

    assert _check(report, "json_schema").status == "warn"
    assert _check(report, "context").status == "warn"
    assert _check(report, "server_version").status == "skipped"
    assert report.capabilities is not None
    assert report.capabilities.supports_json_schema is False
    assert report.capabilities.context_tokens is None
    assert report.status == "warn"


def test_colour_flip_canary_detects_a_server_that_ignores_images() -> None:
    report = _probe(FakeServer(kind="ollama", vision="always_red"))

    images = _check(report, "images")
    assert images.status == "warn"
    assert report.capabilities is not None and report.capabilities.image_mode == "none"


def test_server_rejecting_images_records_no_image_mode() -> None:
    report = _probe(FakeServer(kind="ollama", vision="reject"))

    assert _check(report, "images").status == "warn"
    assert report.capabilities is not None and report.capabilities.image_mode == "none"


def test_server_without_tool_calling_fails() -> None:
    report = _probe(FakeServer(kind="ollama", tools=False))

    tools = _check(report, "tool_calling")
    assert tools.status == "fail"
    assert report.capabilities is not None and report.capabilities.supports_tools is False


def test_unreachable_server_fails_and_skips_everything_else() -> None:
    server = FakeServer(reachable=False)

    report = _probe(server)

    reachable = _check(report, "reachable")
    assert reachable.status == "fail"
    assert "ollama serve" in (reachable.fix or "")
    assert report.reachable is False
    assert report.capabilities is None
    assert all(check.status == "skipped" for check in report.checks if check.name != "reachable")
    assert server.paths() == ["GET /v1/models"]


def test_model_not_found_lists_available_ids_and_the_pull_hint() -> None:
    server = FakeServer(kind="ollama", models=["llama3.2:3b", "qwen3:8b"])

    report = _probe(server, tier=TIER_PROFILES["standard"])

    model = _check(report, "model")
    assert model.status == "fail"
    assert "llama3.2:3b" in model.detail and "qwen3:8b" in model.detail
    assert model.fix == "ollama pull gemma4:26b-a4b-it-qat"
    assert report.capabilities is None
    assert not server.chat_requests()


def test_requests_are_small_and_deterministic() -> None:
    server = FakeServer(kind="ollama")

    _probe(server)

    bodies = server.chat_requests()
    assert bodies
    for body in bodies:
        assert body["temperature"] == 0
        assert body["max_tokens"] <= 64
    assert all(request.headers["authorization"] == "Bearer hercules-local" for request in server.requests if request.url.path.startswith("/v1/"))


def test_tool_choice_request_forces_a_specific_function() -> None:
    server = FakeServer(kind="ollama")

    _probe(server)

    forced = [body for body in server.chat_requests() if isinstance(body.get("tool_choice"), dict)]
    assert len(forced) == 1
    assert forced[0]["tool_choice"]["type"] == "function"
    assert len(forced[0]["tools"]) == 2


def test_cached_capabilities_skip_the_feature_probes(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "capabilities.json")
    first = FakeServer(kind="ollama")
    report = _probe(first, cache=cache)
    assert report.capabilities is not None
    cache.put(report.base_url, report.capabilities)

    second = FakeServer(kind="ollama")
    cached = _probe(second, cache=cache)

    assert cached.from_cache is True
    assert not second.chat_requests()
    assert cached.capabilities == report.capabilities
    assert "cached" in _check(cached, "tool_calling").detail
    assert _check(cached, "context").status == "ok"
    assert "POST /api/generate" in second.paths()


def test_cache_is_ignored_when_the_server_version_changes(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "capabilities.json")
    report = _probe(FakeServer(kind="ollama"), cache=cache)
    assert report.capabilities is not None
    cache.put(report.base_url, report.capabilities)

    upgraded = FakeServer(kind="ollama", version="0.33.0")
    fresh = _probe(upgraded, cache=cache)

    assert fresh.from_cache is False
    assert upgraded.chat_requests()


def test_check_results_serialise() -> None:
    report = _probe(FakeServer(kind="ollama", context_length=4096))

    assert ProbeReport.model_validate_json(report.model_dump_json()) == report


def test_a_reasoning_model_that_answers_nothing_is_reported_as_not_measured_with_the_setting_to_use() -> None:
    report = _probe(FakeServer(kind="generic", models=[GEMMA_4_26B_A4B], reasoning=True, version=None), provider="openai-compatible", base_url="https://llm.example/v1")

    for name in ("tool_calling", "json_schema", "images"):
        check = _check(report, name)
        assert check.status == "warn", (name, check)
        assert "reasoning" in check.detail and "not measured" in check.detail
        assert '"reasoning_effort": "none"' in (check.fix or "") and "request_params" in (check.fix or "")


def test_request_params_go_with_every_probe_and_make_a_reasoning_model_answer(tmp_path: Path) -> None:
    server = FakeServer(kind="generic", models=[GEMMA_4_26B_A4B], reasoning=True, version=None)
    cache = CapabilityCache(tmp_path / "capabilities.json")

    report = _probe(server, provider="openai-compatible", base_url="https://llm.example/v1", request_params={"reasoning_effort": "none"}, cache=cache)

    assert all(body["reasoning_effort"] == "none" for body in server.chat_requests())
    assert _check(report, "tool_calling").status == "ok"
    assert _check(report, "json_schema").status == "ok"
    assert _check(report, "images").status == "ok"


def test_capabilities_are_cached_apart_for_each_set_of_request_params(tmp_path: Path) -> None:
    from testzeus_hercules.llm.capabilities import capability_url

    cache = CapabilityCache(tmp_path / "capabilities.json")
    caps = Capabilities(server_version=None, model_id="m", context_tokens=None, supports_tools=True, supports_tool_choice=False, supports_json_schema=True, image_mode="data_url")
    cache.put(capability_url("https://llm.example/v1", {"reasoning_effort": "none"}), caps)

    assert cache.get(capability_url("https://llm.example/v1", {"reasoning_effort": "none"}), "m", None) == caps
    assert cache.get(capability_url("https://llm.example/v1", None), "m", None) is None
    assert capability_url("https://llm.example/v1", {}) == "https://llm.example/v1"
