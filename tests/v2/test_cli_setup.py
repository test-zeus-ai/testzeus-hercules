"""``hercules setup``: detect the local server, take and check the key (never echoed), egress and telemetry."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from testzeus_hercules.cli.main import main
from testzeus_hercules.cli.setup import SetupDeps
from testzeus_hercules.kernel.telemetry import TELEMETRY_NOTICE
from testzeus_hercules.llm.tiers import GIB

SECRET = "sk-or-test-not-a-real-key-789"
MLX_MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"


class World:
    """Fake network: an mlx-vlm server on 8080 and OpenRouter's decisions endpoint."""

    def __init__(self, *, key_ok: bool = True, servers: bool = True) -> None:
        self.key_ok = key_ok
        self.servers = servers
        self.requests: list[httpx.Request] = []

    def client(self, timeout: float = 5.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), timeout=timeout)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == "http://127.0.0.1:8080/v1/models" and self.servers:
            return httpx.Response(200, json={"data": [{"id": MLX_MODEL}]})
        if url == "https://openrouter.ai/api/alpha/decisions":
            if not self.key_ok:
                return httpx.Response(401, json={"error": {"message": "invalid key"}})
            return httpx.Response(200, json={"model": "typesafe/jev-1.14-20261014", "answers": {"ok": {"type": "noul", "noul": 0.98}}, "usage": {"cost": 0.000012}})
        raise httpx.ConnectError("connection refused", request=request)

    def jev_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests if request.url.host == "openrouter.ai"]


def run(tmp_path: Path, world: World, *args: str, answers: list[str] | None = None, secret: str = "", stdin: str = "", env: dict[str, str] | None = None) -> tuple[int, str]:
    queue = list(answers or [])
    deps = SetupDeps(
        ask=lambda prompt: queue.pop(0) if queue else "",
        ask_secret=lambda prompt: secret,
        make_client=world.client,
        detect_memory=lambda: 24 * GIB,
        stdin=io.StringIO(stdin),
    )
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    out = io.StringIO()
    code = main(["setup", *args, "--project", str(project)], env={"HERCULES_HOME": str(tmp_path / "home"), **(env or {})}, setup_deps=deps, stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


def config(tmp_path: Path) -> dict[str, Any]:
    return yaml.safe_load((tmp_path / "home" / "config.yaml").read_text())


def test_interactive_setup_detects_the_server_saves_a_pasted_key_and_writes_the_config(tmp_path: Path) -> None:
    world = World()

    code, out = run(tmp_path, world, answers=["", "", "n"], secret=SECRET)

    assert code == 0, out
    assert "has the recommended mlx-community/gemma-4-26b-a4b-it-4bit" in out
    assert "key check: ok" in out
    assert SECRET not in out
    key_path = tmp_path / "home" / "openrouter.env"
    assert key_path.read_text() == f"OPENROUTER_API_KEY={SECRET}\n"
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    assert config(tmp_path) == {"llm": {"provider": "mlx-vlm"}, "egress": {"policy": "hybrid"}, "telemetry": {"enabled": False}}
    assert TELEMETRY_NOTICE in out
    assert (tmp_path / "home" / "telemetry_notice_shown").is_file()
    assert "Runs use hybrid mode" in out
    assert f"[auto: OPENROUTER_API_KEY is read from {key_path} and egress policy is 'hybrid']" in out
    (body,) = world.jev_bodies()
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}
    assert SECRET not in json.dumps(body)


def test_yes_takes_every_default_and_without_a_key_runs_are_pure(tmp_path: Path) -> None:
    world = World()

    code, out = run(tmp_path, world, "--yes")

    assert code == 0, out
    assert "no key: runs use pure mode" in out
    assert world.jev_bodies() == [], "no key, no JEV request"
    assert config(tmp_path)["telemetry"] == {"enabled": True}
    assert "Runs use pure mode" in out


def test_key_from_stdin_is_checked_and_saved_for_ci(tmp_path: Path) -> None:
    world = World()

    code, out = run(tmp_path, world, "--yes", "--key-stdin", "--egress", "hybrid-text", "--telemetry", "off", stdin=SECRET + "\n")

    assert code == 0, out
    assert SECRET not in out
    assert (tmp_path / "home" / "openrouter.env").is_file()
    assert config(tmp_path)["egress"] == {"policy": "hybrid-text"}
    assert config(tmp_path)["telemetry"] == {"enabled": False}


def test_a_rejected_key_exits_1_and_is_not_saved(tmp_path: Path) -> None:
    world = World(key_ok=False)

    code, out = run(tmp_path, world, "--yes", "--key-stdin", stdin=SECRET + "\n")

    assert code == 1
    assert "key check: FAILED" in out and "authentication failed" in out
    assert SECRET not in out
    assert not (tmp_path / "home" / "openrouter.env").exists()


def test_a_key_already_in_the_environment_is_used_and_not_written(tmp_path: Path) -> None:
    world = World()

    code, out = run(tmp_path, world, "--yes", env={"OPENROUTER_API_KEY": SECRET})

    assert code == 0, out
    assert "found a key from the environment variable OPENROUTER_API_KEY" in out
    assert not (tmp_path / "home" / "openrouter.env").exists()
    assert world.requests[-1].headers["authorization"] == f"Bearer {SECRET}"


def test_no_server_found_keeps_the_provider_and_starts_nothing(tmp_path: Path) -> None:
    world = World(servers=False)

    code, out = run(tmp_path, world, "--yes", "--no-key-check")

    assert code == 0, out
    assert "no local model server answered" in out
    assert "llm" not in config(tmp_path)
    assert all(request.method == "GET" for request in world.requests)


def test_flags_override_answers_and_bad_flags_exit_2(tmp_path: Path) -> None:
    code, out = run(tmp_path, World(), "--yes", "--provider", "ollama", "--model", "gemma4:e4b", "--clef-url", "http://127.0.0.1:30000")
    assert code == 0, out
    assert config(tmp_path)["llm"] == {"provider": "ollama", "model": "gemma4:e4b"}
    assert config(tmp_path)["decider"] == {"clef_url": "http://127.0.0.1:30000"}

    bad, out = run(tmp_path, World(), "--yes", "--provider", "nope")
    assert bad == 2 and "unknown provider" in out
    remote, out = run(tmp_path, World(), "--yes", "--clef-url", "https://clef.example.com")
    assert remote == 2 and "not on this machine" in out


def test_project_scope_writes_the_project_config_and_keeps_other_keys(tmp_path: Path) -> None:
    project_config = tmp_path / "project" / ".hercules" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text("llm:\n  tier: standard\n")

    code, out = run(tmp_path, World(), "--yes", "--scope", "project", "--egress", "local")

    assert code == 0, out
    saved = yaml.safe_load(project_config.read_text())
    assert saved["llm"] == {"tier": "standard", "provider": "mlx-vlm"}
    assert saved["egress"] == {"policy": "local"}
