"""Tests for the run-dependency factory: pure and hybrid modes, Clef in pure mode, and the stub test double."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.decide.backends.jev import JevBackend
from testzeus_hercules.decide.backends.jev_fallback import JevFallbackDecider
from testzeus_hercules.decide.backends.local_llm import LOCAL_TEXT_SHARE, LocalLLMDecider, text_budget_bytes
from testzeus_hercules.decide.questions import MAX_TEXT_BYTES
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.engine.factory import EngineFactoryError, FactoryDeps, FactoryOptions, RunEngines, build_engines, resolve_mode
from testzeus_hercules.engine.intent import StepInterpreter
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache
from testzeus_hercules.llm.providers import redact_url
from testzeus_hercules.llm.tiers import GIB

from tests.v2.test_llm_fakes import FakeServer

MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"
KEY_ENV = "OPENROUTER_API_KEY"
LOCAL = {"HERCULES_LLM_PROVIDER": "mlx-vlm", "HERCULES_LLM_MODEL": "gemma-4-26b-a4b"}


def _settings(tmp_path: Path, **env: str) -> Settings:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home"), **env})


def _deps(server: FakeServer, memory_gib: int = 32, **extra: Any) -> FactoryDeps:
    return FactoryDeps(make_http=server.client, detect_memory=lambda: memory_gib * GIB, **extra)


def _build(settings: Settings, server: FakeServer, *, env: dict[str, str] | None = None, memory_gib: int = 32, deps: dict[str, Any] | None = None, **options: Any) -> RunEngines:
    async def run() -> RunEngines:
        engines = await build_engines(settings, env=env or {}, options=FactoryOptions(**options), deps=_deps(server, memory_gib, **(deps or {})))
        await engines.aclose()
        return engines

    return asyncio.run(run())


def _mlx_server(**kwargs: Any) -> FakeServer:
    return FakeServer(kind="generic", models=[MODEL], version=None, **kwargs)


# --------------------------------------------------------------------------- mode selection


def test_auto_without_a_jev_key_is_pure_and_warms_the_local_model(tmp_path: Path) -> None:
    server = _mlx_server()

    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text"), server)

    assert engines.mode == "pure"
    assert isinstance(engines.decider, LocalLLMDecider)
    assert "OPENROUTER_API_KEY is not set" in engines.mode_reason
    assert engines.llm is not None and engines.llm.model_id == MODEL
    assert engines.llm.capabilities_source == "defaults"
    assert isinstance(engines.interpreter, StepInterpreter)
    chats = server.chat_requests()
    assert len(chats) == 1, "exactly one warm-up request"
    assert chats[0]["max_tokens"] <= 4
    assert engines.warmup_ms is not None
    assert engines.decider._max_text_bytes == text_budget_bytes(engines.llm.tier.context_tokens), "local picks are split by the tier's context budget"
    assert engines.decider._max_text_bytes < MAX_TEXT_BYTES or engines.llm.tier.context_tokens * LOCAL_TEXT_SHARE * 4 >= MAX_TEXT_BYTES
    assert server.paths().count("GET /v1/models") == 1


def test_auto_with_a_key_and_text_egress_is_hybrid(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text"), _mlx_server(), env={KEY_ENV: "k"})

    assert engines.mode == "hybrid"
    assert isinstance(engines.decider, JevFallbackDecider)
    assert isinstance(engines.decider.jev, JevBackend)
    assert isinstance(engines.decider.fallback, LocalLLMDecider)
    assert engines.decider.images is False, "egress hybrid-text never sends screenshots to JEV"
    assert isinstance(engines.interpreter, StepInterpreter), "the local model still interprets steps"
    assert "hybrid-text" in engines.mode_reason


def test_auto_reason_says_where_the_key_was_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid")
    key_file = str(tmp_path / "home" / "openrouter.env")

    _, from_env, _ = resolve_mode(settings, {KEY_ENV: "k"}, FactoryOptions())
    _, from_file, _ = resolve_mode(settings, {KEY_ENV: "k"}, FactoryOptions(key_origin=key_file))

    assert from_env == "auto: OPENROUTER_API_KEY is set and egress policy is 'hybrid'"
    assert from_file == f"auto: OPENROUTER_API_KEY is read from {key_file} and egress policy is 'hybrid'"


def test_auto_with_a_key_but_local_egress_stays_pure(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="local"), _mlx_server(), env={KEY_ENV: "k"})

    assert engines.mode == "pure"
    assert "egress" in engines.mode_reason


def test_hybrid_with_image_egress_may_send_screenshots(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid"), _mlx_server(), env={KEY_ENV: "k"}, mode="hybrid")

    assert isinstance(engines.decider, JevFallbackDecider)
    assert engines.decider.images is True
    assert engines.images is True
    assert "typesafe/jev-1.14" in engines.describe()


def test_hybrid_mode_under_local_egress_is_a_clear_error(tmp_path: Path) -> None:
    server = _mlx_server()

    with pytest.raises(EngineFactoryError, match="--egress hybrid-text"):
        _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="local"), server, env={KEY_ENV: "k"}, mode="hybrid")

    assert server.requests == []


def test_hybrid_mode_without_the_key_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(EngineFactoryError, match="OPENROUTER_API_KEY"):
        _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text"), _mlx_server(), mode="hybrid")


def test_jev_key_env_names_another_variable(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text"), _mlx_server(), env={"MY_JEV_KEY": "k"}, jev_key_env="MY_JEV_KEY")

    assert engines.mode == "hybrid"
    assert "MY_JEV_KEY" in engines.mode_reason


@pytest.mark.parametrize(("backend", "mode"), [("local", "pure"), ("jev", "hybrid")])
def test_settings_backend_maps_to_a_mode(tmp_path: Path, backend: str, mode: str) -> None:
    settings = _settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text", HERCULES_DECIDER_BACKEND=backend)

    engines = _build(settings, _mlx_server(), env={KEY_ENV: "k"})

    assert engines.mode == mode
    assert f"decider.backend: {backend}" in engines.mode_reason


def test_explicit_mode_beats_the_settings_backend(tmp_path: Path) -> None:
    settings = _settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text", HERCULES_DECIDER_BACKEND="jev")

    engines = _build(settings, _mlx_server(), env={KEY_ENV: "k"}, mode="pure")

    assert engines.mode == "pure"


def test_decider_backend_clef_is_pure_mode_with_a_reachable_clef_server_deciding(tmp_path: Path) -> None:
    from tests.v2.clef_fakes import FakeClef

    clef = FakeClef()
    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_DECIDER_BACKEND="clef"), _mlx_server(), env={KEY_ENV: "k"}, deps={"clef_http": clef.client})

    assert engines.mode == "pure" and engines.mode_reason == "decider.backend: clef"
    assert isinstance(engines.decider, JevFallbackDecider) and isinstance(engines.decider.fallback, LocalLLMDecider)
    assert engines.clef and engines.decider_label == "Clef"
    assert engines.decider_model == "Cloudflare/clef-flash"
    assert "Clef Cloudflare/clef-flash at http://127.0.0.1:30000/v1/systemone decides" in engines.describe()
    assert len(clef.requests) == 1, "one health check"


def test_an_unreachable_clef_server_leaves_the_local_model_deciding_with_a_note(tmp_path: Path) -> None:
    from tests.v2.clef_fakes import FakeClef

    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_CLEF_URL="http://127.0.0.1:30001"), _mlx_server(), deps={"clef_http": FakeClef(reachable=False).client})

    assert isinstance(engines.decider, LocalLLMDecider) and not engines.clef
    assert any("Clef server http://127.0.0.1:30001/v1/systemone is not usable" in note for note in engines.notes)


def test_clef_is_never_asked_in_hybrid_mode(tmp_path: Path) -> None:
    from tests.v2.clef_fakes import FakeClef

    clef = FakeClef()
    engines = _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text", HERCULES_CLEF_URL="http://127.0.0.1:30000"), _mlx_server(), env={KEY_ENV: "k"}, deps={"clef_http": clef.client})

    assert engines.mode == "hybrid" and not engines.clef
    assert clef.requests == []


@pytest.mark.parametrize("mode", ["auto", "pure", "hybrid"])
def test_no_reachable_local_model_is_an_error_pointing_to_setup_and_doctor(tmp_path: Path, mode: str) -> None:
    server = FakeServer(kind="generic", reachable=False)

    with pytest.raises(EngineFactoryError, match="not reachable") as caught:
        _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text"), server, env={KEY_ENV: "k"}, mode=mode)

    message = str(caught.value)
    assert "hercules doctor" in message and "hercules setup" in message and "no coded fallback" in message
    assert "--decider" not in message


def test_the_default_jev_honours_zero_data_retention_from_settings(tmp_path: Path) -> None:
    on = _build(_settings(tmp_path / "on", **LOCAL, HERCULES_EGRESS="hybrid-text"), _mlx_server(), env={KEY_ENV: "k"})
    off = _build(_settings(tmp_path / "off", **LOCAL, HERCULES_EGRESS="hybrid-text", HERCULES_DECIDER_ZDR="0"), _mlx_server(), env={KEY_ENV: "k"})

    assert isinstance(on.jev, JevBackend) and on.jev.zdr is True
    assert isinstance(off.jev, JevBackend) and off.jev.zdr is False
    assert "zero data retention off" in off.describe()


def test_unknown_local_model_is_an_error(tmp_path: Path) -> None:
    server = FakeServer(kind="generic", models=["some-other-model"], version=None)

    with pytest.raises(EngineFactoryError, match="not served"):
        _build(_settings(tmp_path, **LOCAL), server)

    assert server.chat_requests() == []


def test_egress_policy_local_refuses_a_remote_endpoint_without_contacting_it(tmp_path: Path) -> None:
    server = _mlx_server()
    settings = _settings(tmp_path, HERCULES_LLM_PROVIDER="openrouter", HERCULES_EGRESS="local")

    with pytest.raises(EngineFactoryError, match="egress policy 'local'"):
        _build(settings, server, env={"OPENROUTER_API_KEY": "k"}, mode="pure")

    assert server.requests == []


def test_the_stub_test_double_never_contacts_any_server_and_reads_with_an_injected_reader_only(tmp_path: Path) -> None:
    from tests.v2.engine_fixtures.fakes import ScriptedInterpreter

    server = _mlx_server()

    engines = _build(_settings(tmp_path, **LOCAL), server, decider="stub", env={KEY_ENV: "k"})
    reader = ScriptedInterpreter()
    injected = _build(_settings(tmp_path, **LOCAL), server, decider="stub", deps={"test_interpreter": reader})

    assert isinstance(engines.decider, StubDecider)
    assert engines.mode == "test"
    assert engines.decider_name == "stub"
    assert engines.interpreter is None and injected.interpreter is reader
    assert server.requests == []
    assert engines.llm is None
    assert "a test double" in engines.describe()


def test_pure_decider_uses_vision_when_the_server_and_tier_allow_images(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, **LOCAL), _mlx_server(), mode="pure")

    assert isinstance(engines.decider, LocalLLMDecider)
    assert engines.images is True


def test_pure_decider_without_image_support_runs_text_only(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, HERCULES_LLM_PROVIDER="mlx", HERCULES_LLM_MODEL="gemma-4-26b-a4b"), _mlx_server(), mode="pure")

    assert engines.images is False
    assert engines.llm is not None and engines.llm.capabilities.image_mode == "none"


def test_cached_capabilities_from_doctor_are_used(tmp_path: Path) -> None:
    settings = _settings(tmp_path, **LOCAL)
    cached = Capabilities(server_version=None, model_id=MODEL, context_tokens=32768, supports_tools=True, supports_tool_choice=True, supports_json_schema=False, image_mode="none")
    CapabilityCache(HerculesPaths.from_settings(settings).capabilities_file).put(redact_url("http://127.0.0.1:8080/v1"), cached)

    engines = _build(settings, _mlx_server(), mode="pure")

    assert engines.llm is not None
    assert engines.llm.capabilities_source == "cache"
    assert engines.llm.capabilities == cached
    assert engines.images is False


def test_the_tier_follows_the_memory(tmp_path: Path) -> None:
    engines = _build(_settings(tmp_path, **LOCAL), _mlx_server(), memory_gib=16)

    assert engines.llm is not None and engines.llm.tier.name == "floor"


def test_case_readings_are_cached_under_the_projects_hercules_cache(tmp_path: Path) -> None:
    from testzeus_hercules.engine.intent import case_items

    from tests.v2.fixtures.fake_model import FakeModel

    settings = _settings(tmp_path, **LOCAL)
    server = _mlx_server(responder=FakeModel().response)

    async def run() -> Any:
        engines = await build_engines(settings, env={}, options=FactoryOptions(mode="pure"), deps=_deps(server))
        try:
            assert engines.interpreter is not None
            return await engines.interpreter.read_case(case_items([(1, 'click "Sign in"', None)]))
        finally:
            await engines.aclose()

    reading = asyncio.run(run())

    assert reading.source == "model"
    cached = list((settings.project_dir / ".hercules" / "cache" / "interpret").glob("reading-*.json"))
    assert len(cached) == 1
    request = next(body for body in server.chat_requests() if body.get("response_format"))
    assert request["max_tokens"] >= 4096, "a whole case gets a larger output budget than one step"


def test_warm_up_failure_is_an_error(tmp_path: Path) -> None:
    import httpx

    server = _mlx_server(scripted=[httpx.Response(503, json={"error": "loading"}), httpx.Response(503, json={"error": "loading"})])

    with pytest.raises(EngineFactoryError, match="warm-up"):
        _build(_settings(tmp_path, **LOCAL), server)


def test_llm_request_params_go_with_the_runs_model_requests(tmp_path: Path) -> None:
    server = _mlx_server()

    _build(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text", HERCULES_LLM_REQUEST_PARAMS='{"reasoning_effort": "none"}'), server)

    (warm_up,) = server.chat_requests()
    assert warm_up["reasoning_effort"] == "none"


# --------------------------------------------------------------------------- connections: proxies and cleanup


def test_the_default_client_skips_the_environment_proxy_for_a_local_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.engine.factory import _default_http

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")

    async def check() -> tuple[bool, bool]:
        local, remote = _default_http(1.0, local=True), _default_http(1.0, local=False)
        try:
            return local.trust_env, remote.trust_env
        finally:
            await local.aclose()
            await remote.aclose()

    assert asyncio.run(check()) == (False, True)


class _Tracked:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.clients: list[Any] = []

    def client(self, timeout: float = 5.0) -> Any:
        made = self.server.client(timeout)
        self.clients.append(made)
        return made


def test_a_failing_jev_setup_closes_the_local_connection(tmp_path: Path) -> None:
    tracked = _Tracked(_mlx_server())

    def broken_jev(*_: Any) -> Any:
        raise RuntimeError("jev setup failed")

    async def run() -> None:
        deps = FactoryDeps(make_http=tracked.client, detect_memory=lambda: 32 * GIB, make_jev=broken_jev)
        await build_engines(_settings(tmp_path, **LOCAL, HERCULES_EGRESS="hybrid-text"), env={KEY_ENV: "k"}, options=FactoryOptions(mode="hybrid"), deps=deps)

    with pytest.raises(RuntimeError, match="jev setup failed"):
        asyncio.run(run())
    assert tracked.clients and all(client.is_closed for client in tracked.clients)


def test_aclose_closes_the_local_connection_when_jev_close_fails(tmp_path: Path) -> None:
    tracked = _Tracked(_mlx_server())

    class BadClose:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    async def run() -> None:
        deps = FactoryDeps(make_http=tracked.client, detect_memory=lambda: 32 * GIB)
        engines = await build_engines(_settings(tmp_path, **LOCAL), env={}, options=FactoryOptions(mode="pure"), deps=deps)
        engines.jev = BadClose()  # type: ignore[assignment]
        await engines.aclose()

    with pytest.raises(RuntimeError, match="close failed"):
        asyncio.run(run())
    assert tracked.clients and all(client.is_closed for client in tracked.clients)
