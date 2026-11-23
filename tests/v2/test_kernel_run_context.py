"""Tests for v2 kernel paths and RunContext."""

import ast
import asyncio
import dataclasses
import os
import re
import stat
import sys
from pathlib import Path

import pytest

import testzeus_hercules.kernel as kernel_pkg
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import CancelToken, ListEventSink, NullEventSink, RunCancelledError, RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

RUN_ID_FORMAT = re.compile(r"^r_\d{8}_\d{6}_[0-9a-f]{6}$")


def _settings(tmp_path: Path, name: str = "a", **overrides: object) -> Settings:
    project = tmp_path / f"project-{name}"
    project.mkdir(exist_ok=True)
    return Settings.load(project, env={"HERCULES_HOME": str(tmp_path / f"home-{name}")}, overrides=overrides)


def test_paths_layout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = HerculesPaths.from_settings(settings)
    home, project = settings.home_dir, settings.project_dir

    assert paths.home_dir == home
    assert paths.config_file == home / "config.yaml"
    assert paths.daemon_file == home / "daemon.json"
    assert paths.capabilities_file == home / "capabilities.json"
    assert paths.cache_dir == home / "cache"
    assert paths.installation_id_file.parent == home
    assert paths.project_dir == project
    assert paths.project_hercules_dir == project / ".hercules"
    assert paths.project_config_file == project / ".hercules" / "config.yaml"
    assert paths.cases_dir == project / ".hercules" / "cases"
    assert paths.envs_dir == project / ".hercules" / "envs"
    assert paths.runs_dir == project / ".hercules" / "runs"
    assert paths.history_db == project / ".hercules" / "history.db"


def test_paths_are_immutable(tmp_path: Path) -> None:
    paths = HerculesPaths.from_settings(_settings(tmp_path))

    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.home_dir = tmp_path  # type: ignore[misc]


def test_ensure_dirs_creates_layout_and_is_idempotent(tmp_path: Path) -> None:
    paths = HerculesPaths.from_settings(_settings(tmp_path))

    paths.ensure_dirs()
    paths.ensure_dirs()

    for directory in (paths.home_dir, paths.cache_dir, paths.project_hercules_dir, paths.cases_dir, paths.envs_dir, paths.runs_dir):
        assert directory.is_dir()
    assert not paths.history_db.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_ensure_dirs_makes_home_private(tmp_path: Path) -> None:
    paths = HerculesPaths.from_settings(_settings(tmp_path))
    old_umask = os.umask(0o022)
    try:
        paths.ensure_dirs()
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(paths.home_dir.stat().st_mode) == 0o700


@pytest.mark.parametrize("run_id", ["r_20261025_101500_abc123", "run-1", "a.b_c", "x" * 128])
def test_run_dir_accepts_safe_ids(tmp_path: Path, run_id: str) -> None:
    paths = HerculesPaths.from_settings(_settings(tmp_path))

    assert paths.run_dir(run_id) == paths.runs_dir / run_id


@pytest.mark.parametrize("run_id", ["", ".", "..", "a..b", "../escape", "a/b", "a\\b", "x" * 129, "id\n", "id with space", "/abs"])
def test_run_dir_rejects_unsafe_ids(tmp_path: Path, run_id: str) -> None:
    paths = HerculesPaths.from_settings(_settings(tmp_path))

    with pytest.raises(ValueError):
        paths.run_dir(run_id)


def test_create_generates_sortable_unique_run_ids(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    ids = [RunContext.create(settings).run_id for _ in range(20)]

    assert all(RUN_ID_FORMAT.match(run_id) for run_id in ids)
    assert len(set(ids)) == len(ids)
    assert [run_id[:17] for run_id in ids] == sorted(run_id[:17] for run_id in ids)


def test_create_defaults(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    ctx = RunContext.create(settings)

    assert ctx.settings is settings
    assert ctx.paths == HerculesPaths.from_settings(settings)
    assert isinstance(ctx.events, NullEventSink)
    assert isinstance(ctx.telemetry, TelemetryClient)
    assert isinstance(ctx.cancel, CancelToken)
    assert not ctx.cancel.is_cancelled
    assert not settings.home_dir.exists(), "creating a context must not touch the filesystem"


def test_create_validates_explicit_run_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert RunContext.create(settings, run_id="my-run").run_id == "my-run"
    with pytest.raises(ValueError):
        RunContext.create(settings, run_id="../evil")


def test_run_context_is_frozen(tmp_path: Path) -> None:
    ctx = RunContext.create(_settings(tmp_path))

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.run_id = "other"  # type: ignore[misc]


def test_list_event_sink_records_events(tmp_path: Path) -> None:
    sink = ListEventSink()
    ctx = RunContext.create(_settings(tmp_path), events=sink)

    ctx.events.emit("step_started", index=1)
    ctx.events.emit("step_finished", index=1, status="passed")

    assert [event.kind for event in sink.events] == ["step_started", "step_finished"]
    assert sink.events[1].data == {"index": 1, "status": "passed"}


def test_null_event_sink_accepts_anything() -> None:
    NullEventSink().emit("anything", a=1, b=[1, 2])


def test_cancel_token() -> None:
    token = CancelToken()
    token.raise_if_cancelled()

    token.cancel("user pressed stop")

    assert token.is_cancelled
    assert token.reason == "user pressed stop"
    with pytest.raises(RunCancelledError, match="user pressed stop"):
        token.raise_if_cancelled()


def test_two_contexts_in_one_process_do_not_interfere(tmp_path: Path) -> None:
    settings_a = _settings(tmp_path, "a", llm={"model": "model-a"}, ui={"port": 8001})
    settings_b = _settings(tmp_path, "b", llm={"model": "model-b", "provider": "lmstudio"}, telemetry={"enabled": False})
    sink_a, sink_b = ListEventSink(), ListEventSink()
    transport_a, transport_b = RecordingTransport(), RecordingTransport()
    ctx_a = RunContext.create(settings_a, events=sink_a, telemetry=TelemetryClient(settings_a, HerculesPaths.from_settings(settings_a), transport=transport_a))
    ctx_b = RunContext.create(settings_b, events=sink_b, telemetry=TelemetryClient(settings_b, HerculesPaths.from_settings(settings_b), transport=transport_b))

    async def run(ctx: RunContext) -> tuple[str, str]:
        for index in range(5):
            ctx.events.emit("tick", index=index, model=ctx.settings.llm.model)
            await asyncio.sleep(0)
        ctx.telemetry.emit("run_finished", run_status="passed", step_count=5)
        return ctx.settings.llm.model, str(ctx.paths.runs_dir)

    async def both() -> list[tuple[str, str]]:
        return list(await asyncio.gather(run(ctx_a), run(ctx_b)))

    results = asyncio.run(both())
    ctx_a.cancel.cancel()
    ctx_a.telemetry.close()
    ctx_b.telemetry.close()

    assert results[0] == ("model-a", str(settings_a.project_dir / ".hercules" / "runs"))
    assert results[1] == ("model-b", str(settings_b.project_dir / ".hercules" / "runs"))
    assert {event.data["model"] for event in sink_a.events} == {"model-a"}
    assert {event.data["model"] for event in sink_b.events} == {"model-b"}
    assert ctx_a.cancel.is_cancelled and not ctx_b.cancel.is_cancelled
    assert ctx_a.settings.ui.port == 8001 and ctx_b.settings.ui.port == 7373
    assert [payload["llm_provider"] for payload in transport_a.payloads] == ["ollama"]
    assert transport_b.payloads == []


def _kernel_modules() -> list[Path]:
    return sorted(Path(kernel_pkg.__file__).parent.glob("*.py"))


@pytest.mark.parametrize("module", _kernel_modules(), ids=lambda path: path.name)
def test_kernel_has_no_mutable_module_state(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Global, ast.Nonlocal)), f"{module.name}:{node.lineno} uses global/nonlocal"
    mutable = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            assert not isinstance(node.value, mutable), f"{module.name}:{node.lineno} defines mutable module-level state"
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                assert node.value.func.id not in {"dict", "list", "set"}, f"{module.name}:{node.lineno} defines mutable module-level state"


@pytest.mark.parametrize("module", _kernel_modules(), ids=lambda path: path.name)
def test_only_settings_reads_the_process_environment(module: Path) -> None:
    if module.name == "settings.py":
        return
    source = module.read_text(encoding="utf-8")

    assert "os.environ" not in source and "getenv" not in source
