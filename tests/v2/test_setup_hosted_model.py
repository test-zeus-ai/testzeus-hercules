"""``hercules setup`` with a hosted model: OpenRouter, or an OpenAI-compatible URL and key, configured before the UI
opens; the key is saved privately, never shown, and reaches runs, doctor and the daemon."""

from __future__ import annotations

import io
import stat
from pathlib import Path
from typing import Any

import httpx
import yaml

from testzeus_hercules.cli.deciders import prepare
from testzeus_hercules.cli.keys import with_model_key
from testzeus_hercules.cli.main import main
from testzeus_hercules.cli.setup import SetupDeps
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.llm.providers import resolve_endpoint
from testzeus_hercules.llm.tiers import GIB

ENDPOINT = "https://llm.example.com/v1"
MODEL_KEY = "sk-endpoint-test-not-real-123"
OPENROUTER_KEY = "sk-or-test-not-real-456"


class Endpoint:
    """A fake hosted endpoint that lists two models for the right key; nothing local answers."""

    def __init__(self, key: str = MODEL_KEY) -> None:
        self.key = key
        self.requests: list[httpx.Request] = []

    def client(self, timeout: float = 5.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), timeout=timeout)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == f"{ENDPOINT}/models":
            if request.headers.get("authorization") != f"Bearer {self.key}":
                return httpx.Response(401, json={"error": "bad key"})
            return httpx.Response(200, json={"data": [{"id": "fast-model"}, {"id": "vision-model"}]})
        raise httpx.ConnectError("refused", request=request)


def setup(
    tmp_path: Path, *args: str, answers: list[str] | None = None, secrets: list[str] | None = None, stdin: str = "", env: dict[str, str] | None = None, endpoint: Endpoint | None = None
) -> tuple[int, str]:
    asked, hidden = list(answers or []), list(secrets or [])
    deps = SetupDeps(
        ask=lambda prompt: asked.pop(0) if asked else "",
        ask_secret=lambda prompt: hidden.pop(0) if hidden else "",
        make_client=(endpoint or Endpoint()).client,
        detect_memory=lambda: 24 * GIB,
        stdin=io.StringIO(stdin),
        which=lambda command: None,
    )
    out = io.StringIO()
    code = main(["setup", *args, "--project", str(tmp_path), "--agents", "none"], env={"HERCULES_HOME": str(tmp_path / "home"), **(env or {})}, setup_deps=deps, stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


def config(tmp_path: Path) -> dict[str, Any]:
    return yaml.safe_load((tmp_path / "home" / "config.yaml").read_text())


def test_an_openai_compatible_endpoint_is_configured_from_flags(tmp_path: Path) -> None:
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible", "--base-url", ENDPOINT, "--model", "fast-model", "--llm-key-stdin", "--telemetry", "off", stdin=MODEL_KEY + "\n")
    assert code == 0, out
    assert MODEL_KEY not in out and "endpoint check: ok, the endpoint answered with 2 models" in out
    assert config(tmp_path)["llm"] == {"provider": "openai-compatible", "base_url": ENDPOINT, "model": "fast-model", "api_key_env": "HERCULES_LLM_API_KEY"}
    assert config(tmp_path)["egress"] == {"policy": "hybrid"}, "a hosted model needs an egress policy that allows it"
    key_file = tmp_path / "home" / "llm.env"
    assert key_file.read_text() == f"HERCULES_LLM_API_KEY={MODEL_KEY}\n"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_interactive_setup_offers_hosted_models_and_lists_the_endpoints_models(tmp_path: Path) -> None:
    # "u": another endpoint; its URL; model number 2; egress default; telemetry no. Hidden: the endpoint key, then no OpenRouter key.
    code, out = setup(tmp_path, answers=["u", ENDPOINT, "2", "", "n"], secrets=[MODEL_KEY, ""])
    assert code == 0, out
    assert "o. OpenRouter" in out and "u. another OpenAI-compatible endpoint" in out and "2. vision-model" in out
    assert config(tmp_path)["llm"]["model"] == "vision-model"
    assert MODEL_KEY not in out and (tmp_path / "home" / "llm.env").is_file()


def test_openrouter_uses_the_openrouter_key_for_the_model_and_for_hybrid_mode(tmp_path: Path) -> None:
    code, out = setup(tmp_path, "--yes", "--provider", "openrouter", "--no-key-check", env={"OPENROUTER_API_KEY": OPENROUTER_KEY})
    assert code == 0, out
    assert config(tmp_path)["llm"] == {"provider": "openrouter"}
    assert config(tmp_path)["egress"] == {"policy": "hybrid"} and "Runs use hybrid mode" in out


def test_a_refused_key_is_not_saved(tmp_path: Path) -> None:
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible", "--base-url", ENDPOINT, "--model", "m", "--llm-key-stdin", stdin="wrong\n")
    assert code == 1 and "refused the key (HTTP 401)" in out
    assert not (tmp_path / "home" / "llm.env").exists() and not (tmp_path / "home" / "config.yaml").exists()


def test_bad_combinations_are_refused(tmp_path: Path) -> None:
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible", "--egress", "local", "--base-url", ENDPOINT, "--model", "m", "--no-key-check")
    assert code == 2 and "cannot use a hosted model" in out
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible")
    assert code == 2 and "needs --base-url" in out
    code, out = setup(tmp_path, "--yes", "--key-stdin", "--llm-key-stdin")
    assert code == 2 and "both read standard input" in out
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible", "--base-url", ENDPOINT, "--api-key-env", "not a name")
    assert code == 2


def test_without_a_key_the_command_to_add_one_is_printed(tmp_path: Path) -> None:
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible", "--base-url", ENDPOINT, "--model", "m", "--no-key-check")
    assert code == 0
    assert "run `hercules setup --provider openai-compatible` in your own terminal" in out


def test_the_saved_key_reaches_runs_doctor_and_the_daemon(tmp_path: Path) -> None:
    code, out = setup(tmp_path, "--yes", "--provider", "openai-compatible", "--base-url", ENDPOINT, "--model", "fast-model", "--llm-key-stdin", stdin=MODEL_KEY + "\n")
    assert code == 0, out
    env = {"HERCULES_HOME": str(tmp_path / "home")}
    settings = Settings.load(tmp_path, env=env)
    run_env = prepare(settings, env).env
    assert resolve_endpoint(settings.llm, run_env).api_key == MODEL_KEY
    assert with_model_key({"HERCULES_LLM_API_KEY": "from-env"}, settings.home_dir, "HERCULES_LLM_API_KEY")["HERCULES_LLM_API_KEY"] == "from-env", "the environment wins"

    doctor_out = io.StringIO()
    main(["doctor", "--json", "--project", str(tmp_path)], env=env, stdout=doctor_out, stderr=io.StringIO())
    assert "needs an API key" not in doctor_out.getvalue() and MODEL_KEY not in doctor_out.getvalue()


def test_an_openrouter_model_finds_the_openrouter_key_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "openrouter.env").write_text(f"OPENROUTER_API_KEY={OPENROUTER_KEY}\n")
    (home / "openrouter.env").chmod(0o600)
    assert with_model_key({}, home, "OPENROUTER_API_KEY") == {"OPENROUTER_API_KEY": OPENROUTER_KEY}
    assert with_model_key({}, home, None) == {}
