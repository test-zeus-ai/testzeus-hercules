"""
Offline tests for the execute_python_sandbox child-process execution core.

Covers the Turn 11 hardening findings:
- infinite loops are killed by the hard timeout without freezing the caller
- subprocess imports cannot freeze the caller (child-process containment)
- stdout capture works
- script paths outside the workspace root are rejected
- return-value semantics (function_name / main / _sandbox_result) are preserved
- oversized output is truncated

Each test spawns a real child process; timeouts are kept small so the whole
file stays fast. No browser or network required.
"""

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.execute_python_sandbox import (
    _MAX_OUTPUT_CHARS,
    _OUTPUT_TRUNCATION_MARKER,
    ExecutionTimeout,
    _execute_in_sandbox,
    _resolve_file_path,
    _sandbox_mode,
)


@pytest.fixture(autouse=True)
def _subprocess_mode(monkeypatch):
    """The child-process tests below exercise the opt-in SANDBOX_MODE=subprocess isolation."""
    monkeypatch.setenv("SANDBOX_MODE", "subprocess")


@pytest.fixture()
def sandbox_dir():
    """A scratch dir inside the workspace root for test scripts."""
    workspace_root = Path(get_global_conf().get_project_source_root()).resolve()
    base = workspace_root / "temp" / "sandbox_pytest"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="pytest_sandbox_", dir=str(base)))
    yield run_dir
    shutil.rmtree(run_dir, ignore_errors=True)


def _write_script(sandbox_dir: Path, code: str) -> str:
    code_file = sandbox_dir / "agent_code.py"
    code_file.write_text(code, encoding="utf-8")
    return str(code_file)


def _run_sandbox(code_file: str, timeout_seconds: float = 20.0, **kwargs):
    """Run _execute_in_sandbox to completion, returning (result_dict, elapsed)."""
    start = time.perf_counter()
    result = asyncio.run(
        _execute_in_sandbox(
            page=None,
            browser=None,
            context=None,
            browser_manager=None,
            file_path=code_file,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )
    )
    return result, time.perf_counter() - start


def test_sync_infinite_loop_hits_hard_timeout_without_freezing_caller(sandbox_dir):
    """`while True: pass` must be killed by the child-process timeout; the test (and the caller's event loop) completes."""
    code_file = _write_script(sandbox_dir, "while True:\n    pass\n")

    start = time.perf_counter()
    with pytest.raises(ExecutionTimeout, match="timed out"):
        asyncio.run(
            _execute_in_sandbox(
                page=None,
                browser=None,
                context=None,
                browser_manager=None,
                file_path=code_file,
                timeout_seconds=1.0,
            )
        )
    elapsed = time.perf_counter() - start

    # Reaching this line proves the caller was not frozen; 1s timeout + child
    # startup + kill grace must stay well under this bound.
    assert elapsed < 25.0


def test_import_subprocess_and_sandbox_result_cannot_freeze_caller(sandbox_dir):
    """`import subprocess` must behave per current injection semantics (it succeeds), and must never hang the caller."""
    code = "import subprocess\n\n_sandbox_result = {'via': '_sandbox_result', 'subprocess': True}\n"
    code_file = _write_script(sandbox_dir, code)

    result, elapsed = _run_sandbox(code_file, timeout_seconds=20.0)

    assert result["success"] is True
    assert result["result"] == {"via": "_sandbox_result", "subprocess": True}
    assert elapsed < 25.0


def test_stdout_capture_and_main_return_value(sandbox_dir):
    code = "def main():\n" "    print('hello from sandbox')\n" "    return {'sum': 2 + 3, 'nested': {'ok': True}}\n"
    code_file = _write_script(sandbox_dir, code)

    result, _ = _run_sandbox(code_file)

    assert result["success"] is True
    assert result["result"] == {"sum": 5, "nested": {"ok": True}}
    assert "hello from sandbox" in result["stdout"]
    assert "execution_time" in result


def test_function_name_return_semantics(sandbox_dir):
    code_file = _write_script(sandbox_dir, "def add(a, b):\n    return a + b\n")

    result, _ = _run_sandbox(code_file, function_name="add", function_args='{"a": 20, "b": 22}')

    assert result["success"] is True
    assert result["result"] == 42


def test_async_main_is_awaited(sandbox_dir):
    code = "async def main():\n    await asyncio.sleep(0)\n    return {'status': 'async-ok'}\n"
    code_file = _write_script(sandbox_dir, code)

    result, _ = _run_sandbox(code_file)

    assert result["success"] is True
    assert result["result"] == {"status": "async-ok"}


def test_oversized_output_is_truncated(sandbox_dir):
    code_file = _write_script(sandbox_dir, "print('x' * (300 * 1024))\n")

    result, _ = _run_sandbox(code_file)

    assert result["success"] is True
    assert len(result["stdout"]) <= _MAX_OUTPUT_CHARS + len(_OUTPUT_TRUNCATION_MARKER)
    assert "truncated" in result["stdout"]


def test_resolve_rejects_paths_outside_workspace_root():
    workspace_root = Path(get_global_conf().get_project_source_root()).resolve()
    outside = Path(tempfile.gettempdir()) / "hercules_sandbox_outside_ws.py"
    outside.write_text("pass\n", encoding="utf-8")
    try:
        assert workspace_root not in outside.resolve().parents  # precondition
        with pytest.raises(PermissionError, match="outside the workspace root"):
            _resolve_file_path(str(outside))
    finally:
        outside.unlink(missing_ok=True)


def test_resolve_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        _resolve_file_path("/nonexistent/hercules_sandbox_missing.py")


def test_resolve_allows_paths_inside_workspace_root(sandbox_dir):
    workspace_root = Path(get_global_conf().get_project_source_root()).resolve()

    # Absolute path inside the workspace stays allowed
    inside = sandbox_dir / "inside.py"
    inside.write_text("pass\n", encoding="utf-8")
    resolved = _resolve_file_path(str(inside))
    assert Path(resolved) == inside.resolve()

    # Relative path (relative to the workspace root) stays allowed
    relative = inside.resolve().relative_to(workspace_root)
    resolved = _resolve_file_path(str(relative))
    assert Path(resolved) == inside.resolve()


# ── SANDBOX_MODE: legacy in-process default keeps live Playwright objects ────


class _FakePage:
    url = "https://example.test/live"


def _run_in_mode(code_file: str, mode, **kwargs):
    page = _FakePage()
    return asyncio.run(
        _execute_in_sandbox(
            page=page,
            browser="browser-obj",
            context="context-obj",
            browser_manager="manager-obj",
            file_path=code_file,
            timeout_seconds=20.0,
            mode=mode,
            **kwargs,
        )
    )


def test_default_mode_is_inprocess(monkeypatch):
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    assert _sandbox_mode() == "inprocess"
    monkeypatch.setenv("SANDBOX_MODE", "subprocess")
    assert _sandbox_mode() == "subprocess"
    monkeypatch.setenv("SANDBOX_MODE", "bogus")
    assert _sandbox_mode() == "inprocess"


def test_inprocess_mode_exposes_live_browser_objects(sandbox_dir, monkeypatch):
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    code = "def main():\n    print('hi')\n    return {'url': page.url, 'browser': browser, 'context': context, 'pm': playwright_manager}\n"
    code_file = _write_script(sandbox_dir, code)

    result = _run_in_mode(code_file, mode=None)

    assert result["success"] is True
    assert result["result"] == {"url": "https://example.test/live", "browser": "browser-obj", "context": "context-obj", "pm": "manager-obj"}
    assert "hi" in result["stdout"]


def test_inprocess_mode_async_function_and_timeout(sandbox_dir, monkeypatch):
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    ok = _write_script(sandbox_dir, "async def main():\n    await asyncio.sleep(0)\n    return 7\n")
    assert _run_in_mode(ok, mode=None)["result"] == 7

    slow = sandbox_dir / "slow.py"
    slow.write_text("async def main():\n    await asyncio.sleep(5)\n", encoding="utf-8")
    with pytest.raises(ExecutionTimeout):
        asyncio.run(
            _execute_in_sandbox(
                page=_FakePage(),
                browser=None,
                context=None,
                browser_manager=None,
                file_path=str(slow),
                timeout_seconds=0.2,
                mode="inprocess",
            )
        )


def test_inprocess_mode_restores_stdout_after_script_error(sandbox_dir):
    import sys

    before = sys.stdout
    code_file = _write_script(sandbox_dir, "raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError, match="boom"):
        _run_in_mode(code_file, mode="inprocess")
    assert sys.stdout is before


def test_subprocess_mode_does_not_expose_live_objects(sandbox_dir):
    code = "def main():\n    return page.url\n"
    code_file = _write_script(sandbox_dir, code)
    with pytest.raises(ValueError, match="not available in the sandbox child process"):
        _run_in_mode(code_file, mode="subprocess")
