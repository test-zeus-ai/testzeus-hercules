"""Regression tests for the post-merge mobile (#160) findings: client timeouts,
orphan-session cleanup, error sanitising, flag honouring, validation, platform
reconciliation and prompt handling. Everything is offline (fake drivers; the
only socket is a local server on 127.0.0.1 that accepts and never replies)."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests import conftest_mobile as cm
from tests.conftest_mobile import (  # noqa: F401
    FakeDriver,
    clean_manager_state,
    fake_manager,
    mobile_env,
)

pytest_plugins = ["tests.conftest_mobile"]

am = cm.appium_manager_module
tools = cm.mobile_tools
settings = cm.mobile_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(coro):
    return asyncio.run(coro)


def _bounded(coro, seconds: float = 15.0):
    """Run a coroutine, failing (instead of hanging the suite) when it overruns."""

    async def _inner():
        return await asyncio.wait_for(coro, timeout=seconds)

    return asyncio.run(_inner())


class _SilentServer:
    """TCP server on a port in 5800-5899 that accepts connections and never answers."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for port in range(5800, 5900):
            try:
                self.sock.bind(("127.0.0.1", port))
                self.port = port
                break
            except OSError:
                continue
        else:  # pragma: no cover
            pytest.skip("no free port in 5800-5899")
        self.sock.listen(8)
        self._clients: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            self._clients.append(client)  # hold open, never reply

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for client in self._clients:
            client.close()
        self.sock.close()


# ── 1. bounded timeouts ─────────────────────────────────────────────────────


def test_session_start_against_silent_server_returns_error_quickly(monkeypatch, clean_manager_state, tmp_path) -> None:
    server = _SilentServer()
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    monkeypatch.setenv("DEVICE_OS", "android")
    monkeypatch.setenv("APPIUM_SERVER_URL", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("APPIUM_SESSION_START_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(am.AppiumManager, "_resolve_proof_path", lambda self: str(tmp_path), raising=False)
    manager = am.AppiumManager(stake_id="silent-start")
    manager._record_video = False
    started = time.time()
    try:
        with pytest.raises(am.MobileEnvironmentError) as excinfo:
            _bounded(manager.create_session())
    finally:
        server.close()
        manager.shutdown_pool()
    assert time.time() - started < 8
    assert "APPIUM_SERVER_URL" in str(excinfo.value)
    assert manager.driver is None


def test_command_against_hung_driver_returns_error_within_timeout(fake_manager, monkeypatch) -> None:
    manager, driver = fake_manager
    release = threading.Event()

    def hang():
        release.wait(10)
        return {"width": 1, "height": 1}

    driver.get_window_size = hang
    monkeypatch.setenv("APPIUM_COMMAND_TIMEOUT_SECONDS", "0.3")
    started = time.time()
    try:
        result = _bounded(tools.get_mobile_viewport_size())
    finally:
        release.set()
    assert time.time() - started < 5
    assert result.startswith("Error:")
    assert "APPIUM_SERVER_URL" in result


def test_run_in_ui_thread_explicit_timeout_raises_timeout_error(fake_manager) -> None:
    manager, _driver = fake_manager
    release = threading.Event()
    try:
        with pytest.raises(am.AppiumTimeoutError, match="APPIUM_SERVER_URL"):
            _bounded(manager._run_in_ui_thread(lambda: release.wait(10), "slow", timeout=0.2))
    finally:
        release.set()


def test_session_that_finishes_starting_after_the_timeout_is_quit(monkeypatch, clean_manager_state, tmp_path) -> None:
    """asyncio cannot stop the executor thread: a driver that comes back late must be quit, not leaked."""
    monkeypatch.setenv("APPIUM_SESSION_START_TIMEOUT_SECONDS", "0.2")
    late = FakeDriver(session_id="late-session")
    release = threading.Event()
    quit_seen = threading.Event()
    original_quit = late.quit

    def quit_and_signal() -> None:
        original_quit()
        quit_seen.set()

    late.quit = quit_and_signal

    def slow_factory():
        release.wait(10)
        return late

    manager = am.AppiumManager(stake_id="late", _driver_factory=slow_factory)
    manager._record_video = False
    manager._video_dir = str(tmp_path / "v")
    manager._screenshots_dir = str(tmp_path / "s")
    try:
        with pytest.raises(am.AppiumTimeoutError):
            _bounded(manager.create_session({"platformName": "Android"}))
        assert manager.driver is None and late.quit_called == 0  # still creating
        release.set()  # the server finally answers
        assert quit_seen.wait(5), "the late session was leaked: nothing quit it"
        assert late.quit_called == 1
        assert manager.driver is None  # and it was never adopted
    finally:
        release.set()
        manager.shutdown_pool()


def test_late_factory_failure_after_the_timeout_is_swallowed(monkeypatch, clean_manager_state, tmp_path) -> None:
    monkeypatch.setenv("APPIUM_SESSION_START_TIMEOUT_SECONDS", "0.2")
    release = threading.Event()

    def failing_factory():
        release.wait(10)
        raise RuntimeError("server gave up")

    manager = am.AppiumManager(stake_id="latefail", _driver_factory=failing_factory)
    try:
        with pytest.raises(am.AppiumTimeoutError):
            _bounded(manager.create_session({"platformName": "Android"}))
        release.set()
        manager.shutdown_pool(wait=True)  # drains the worker: the callback ran without raising
    finally:
        release.set()


def test_session_creation_sets_client_timeouts_on_real_client_config(monkeypatch, clean_manager_state, tmp_path) -> None:
    pytest.importorskip("appium")
    import appium.webdriver as appium_webdriver

    seen = {}

    class _Driver:
        session_id = "s-timeouts"

        def __init__(self, client_config) -> None:
            self.command_executor = SimpleNamespace(_client_config=client_config)

        def quit(self) -> None:
            pass

    def fake_remote(command_executor=None, options=None, client_config=None):
        seen["start_timeout"] = client_config.timeout
        seen["driver"] = _Driver(client_config)
        return seen["driver"]

    monkeypatch.setattr(appium_webdriver, "Remote", fake_remote)
    monkeypatch.setenv("APPIUM_SESSION_START_TIMEOUT_SECONDS", "77")
    monkeypatch.setenv("APPIUM_COMMAND_TIMEOUT_SECONDS", "33")
    monkeypatch.setattr(am.AppiumManager, "_resolve_proof_path", lambda self: str(tmp_path), raising=False)
    manager = am.AppiumManager(stake_id="client-timeouts")
    manager._record_video = False
    try:
        _run(manager.create_session({"platformName": "Android"}))
    finally:
        am.AppiumManager.close_instance("client-timeouts")
    assert seen["start_timeout"] == 77
    assert seen["driver"].command_executor._client_config.timeout == 33  # lowered after the session exists


def test_timeout_settings_defaults_and_invalid_values(monkeypatch) -> None:
    for key in ("APPIUM_COMMAND_TIMEOUT_SECONDS", "APPIUM_SESSION_START_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    assert settings.get_command_timeout_seconds() == 120
    assert settings.get_session_start_timeout_seconds() == 180
    monkeypatch.setenv("APPIUM_COMMAND_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("APPIUM_SESSION_START_TIMEOUT_SECONDS", "soon")
    assert settings.get_command_timeout_seconds() == 120
    assert settings.get_session_start_timeout_seconds() == 180
    monkeypatch.setenv("APPIUM_COMMAND_TIMEOUT_SECONDS", "2.5")
    assert settings.get_command_timeout_seconds() == 2.5


def test_connection_refused_error_mentions_server_url(fake_manager) -> None:
    manager, _driver = fake_manager

    def refuse():
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    with pytest.raises(am.MobileEnvironmentError, match="APPIUM_SERVER_URL"):
        _run(manager._run_in_ui_thread(refuse, "probe"))


def test_session_start_failure_mentions_server_url(monkeypatch, clean_manager_state, tmp_path) -> None:
    monkeypatch.setenv("DEVICE_MANAGER", "appium")
    cm.install_stub_appium(monkeypatch, remote=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Unknown server reply: <html>")))
    monkeypatch.setattr(am.AppiumManager, "_resolve_proof_path", lambda self: str(tmp_path), raising=False)
    manager = am.AppiumManager(stake_id="bad-reply")
    try:
        with pytest.raises(am.MobileEnvironmentError, match="APPIUM_SERVER_URL"):
            _run(manager.create_session())
    finally:
        manager.shutdown_pool()


# ── 2. orphan sessions: atexit + SIGTERM ────────────────────────────────────


@pytest.fixture
def clean_exit_hooks(monkeypatch):
    """Reset the module's hook state and restore the process signal handlers afterwards."""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(am, "_atexit_registered", False)
    monkeypatch.setattr(am, "_previous_signal_handlers", {})
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_register_exit_hooks_is_idempotent_and_chains_previous_handler(monkeypatch, clean_exit_hooks) -> None:
    calls = []
    monkeypatch.setattr(am.atexit, "register", lambda fn, *a, **k: calls.append(fn))
    sentinel = lambda signum, frame: None  # noqa: E731
    signal.signal(signal.SIGTERM, sentinel)

    am.register_exit_hooks()
    am.register_exit_hooks()

    assert calls == [am._cleanup_all_sessions]
    assert signal.getsignal(signal.SIGTERM) is am._handle_exit_signal
    assert am._previous_signal_handlers[signal.SIGTERM] is sentinel  # not overwritten by the 2nd call


def test_cleanup_all_sessions_quits_active_drivers(fake_manager) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    am._cleanup_all_sessions()
    assert driver.quit_called == 1
    assert manager.driver is None
    am._cleanup_all_sessions()  # idempotent
    assert driver.quit_called == 1


def test_signal_handler_cleans_up_then_calls_previous_handler(fake_manager, monkeypatch, clean_exit_hooks) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    received = []
    monkeypatch.setitem(am._previous_signal_handlers, signal.SIGTERM, lambda signum, frame: received.append(signum))
    am._handle_exit_signal(signal.SIGTERM, None)
    assert driver.quit_called == 1
    assert received == [signal.SIGTERM]


def _run_handler_in_thread(signum, seconds: float) -> bool:
    """Run the signal handler on a helper thread; True when it returned within ``seconds``."""
    handler = threading.Thread(target=am._handle_exit_signal, args=(signum, None), daemon=True)
    handler.start()
    handler.join(seconds)
    return not handler.is_alive()


def test_signal_handler_is_bounded_when_the_driver_quit_hangs(fake_manager, monkeypatch, clean_exit_hooks) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    release = threading.Event()
    driver.quit = lambda: release.wait(30)
    received = []
    monkeypatch.setitem(am._previous_signal_handlers, signal.SIGTERM, lambda signum, frame: received.append(signum))
    monkeypatch.setattr(am, "SIGNAL_CLEANUP_JOIN_SECONDS", 0.5)
    try:
        started = time.time()
        assert _run_handler_in_thread(signal.SIGTERM, 5.0), "the handler must not wait on a hung driver forever"
        assert time.time() - started < 4
        assert received == [signal.SIGTERM]  # still chained to the previous handler
    finally:
        release.set()


def test_signal_handler_runs_the_driver_calls_off_the_calling_thread(fake_manager, monkeypatch, clean_exit_hooks) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    quit_threads = []
    driver.quit = lambda: quit_threads.append(threading.current_thread())
    monkeypatch.setitem(am._previous_signal_handlers, signal.SIGTERM, lambda signum, frame: None)
    am._handle_exit_signal(signal.SIGTERM, None)
    assert len(quit_threads) == 1 and quit_threads[0] is not threading.current_thread()
    assert quit_threads[0].daemon


def test_teardown_waits_for_an_in_flight_command_before_quitting(fake_manager) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    events = []
    release = threading.Event()
    started = threading.Event()

    def slow_command():
        started.set()
        release.wait(10)
        events.append("command-finished")
        return {"width": 1, "height": 1}

    driver.get_window_size = slow_command
    driver.quit = lambda: events.append("quit")

    async def scenario():
        command = asyncio.ensure_future(manager._run_in_ui_thread(lambda: driver.get_window_size(), "slow"))
        await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        threading.Timer(0.4, release.set).start()
        await asyncio.get_running_loop().run_in_executor(None, am._cleanup_all_sessions)
        await command

    _bounded(scenario())
    assert events == ["command-finished", "quit"], "quit must not race the command still in flight"


def test_teardown_gives_up_on_a_wedged_command_after_a_bounded_wait(fake_manager, monkeypatch) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    monkeypatch.setattr(am, "EXIT_INFLIGHT_WAIT_SECONDS", 0.2)
    release = threading.Event()
    started = threading.Event()
    driver.get_window_size = lambda: (started.set(), release.wait(10))
    try:

        async def scenario():
            command = asyncio.ensure_future(manager._run_in_ui_thread(lambda: driver.get_window_size(), "wedged", timeout=0.1))
            await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
            with pytest.raises(am.AppiumTimeoutError):
                await command

        _bounded(scenario())
        began = time.time()
        am._cleanup_all_sessions()
        assert time.time() - began < 3
        assert driver.quit_called == 1
    finally:
        release.set()


def test_create_session_registers_exit_hooks(monkeypatch, clean_manager_state, tmp_path, clean_exit_hooks) -> None:
    monkeypatch.setattr(am.atexit, "register", lambda *a, **k: None)
    driver = FakeDriver()
    manager = am.AppiumManager(stake_id="hooks", _driver_factory=lambda: driver)
    manager._record_video = False
    manager._video_dir = str(tmp_path / "v")
    manager._screenshots_dir = str(tmp_path / "s")
    try:
        _run(manager.create_session({"platformName": "Android"}))
    finally:
        manager.shutdown_pool()
    assert am._atexit_registered is True


_SIGTERM_SCRIPT = textwrap.dedent("""
    import asyncio, os, sys, time
    from testzeus_hercules.core.mobile import appium_manager as am

    marker = sys.argv[1]

    class Driver:
        session_id = "s-sigterm"
        def quit(self):
            with open(marker, "w") as f:
                f.write("quit")

    am.AppiumManager._resolve_proof_path = lambda self: os.path.dirname(marker)
    manager = am.AppiumManager(stake_id="sigterm", _driver_factory=lambda: Driver())
    manager._record_video = False
    asyncio.run(manager.create_session({"platformName": "Android"}))
    print("READY", flush=True)
    time.sleep(30)
    """)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_quits_session_before_process_dies(tmp_path) -> None:
    script = tmp_path / "run.py"
    script.write_text(_SIGTERM_SCRIPT)
    marker = tmp_path / "quit.marker"
    env = {**os.environ, "ENABLE_TELEMETRY": "0", "PYTHONPATH": str(REPO_ROOT), "RECORD_VIDEO": "false", "TAKE_SCREENSHOTS": "false"}
    proc = subprocess.Popen([sys.executable, str(script), str(marker)], cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 60
        line = ""
        while time.time() < deadline and "READY" not in line:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
        assert "READY" in line, proc.stderr.read()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert marker.read_text() == "quit"
    assert proc.returncode == -signal.SIGTERM  # the default termination still happens


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_normal_exit_quits_session_via_atexit(tmp_path) -> None:
    script = tmp_path / "run_exit.py"
    script.write_text(_SIGTERM_SCRIPT.replace("time.sleep(30)", "pass"))
    marker = tmp_path / "quit.marker"
    env = {**os.environ, "ENABLE_TELEMETRY": "0", "PYTHONPATH": str(REPO_ROOT), "RECORD_VIDEO": "false", "TAKE_SCREENSHOTS": "false"}
    result = subprocess.run([sys.executable, str(script), str(marker)], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "quit"


# ── 3. error text sent to the LLM ───────────────────────────────────────────


class NoSuchElementException(Exception):  # same class name as selenium's
    pass


def test_format_error_strips_server_stacktrace_and_caps_length() -> None:
    error = RuntimeError("Message: An unknown server-side error occurred\nStacktrace:\n    at doStuff (node_modules/x.js:1)\n    at more (y.js:2)")
    text = tools._format_error("tap", error)
    assert text == "Error: tap failed: Message: An unknown server-side error occurred"
    assert "Stacktrace" not in text and "node_modules" not in text
    long = tools._format_error("tap", RuntimeError("x" * 5000))
    assert len(long) <= len("Error: tap failed: ") + tools.MAX_ERROR_CHARS


def test_format_error_logs_expected_failures_without_traceback(monkeypatch) -> None:
    records = []
    fake_logger = SimpleNamespace(
        warning=lambda *a, **k: records.append(("warning", k)),
        error=lambda *a, **k: records.append(("error", k)),
    )
    monkeypatch.setattr(tools, "logger", fake_logger)
    tools._format_error("click_element", NoSuchElementException("no such element"))
    tools._format_error("click_element", am.MobileEnvironmentError("No element found"))
    tools._format_error("click_element", RuntimeError("boom"))
    assert [level for level, _ in records] == ["warning", "warning", "error"]
    assert not records[0][1].get("exc_info") and not records[1][1].get("exc_info")
    assert records[2][1].get("exc_info") is True


# ── 4. RECORD_VIDEO / TAKE_SCREENSHOTS flags ────────────────────────────────


def test_start_screen_recording_tool_reports_disabled_recording(fake_manager) -> None:
    manager, driver = fake_manager
    manager._record_video = False
    result = _run(tools.start_screen_recording())
    assert result.startswith("Error:") and "RECORD_VIDEO" in result
    assert driver.record_started is False
    manager._record_video = True
    assert _run(tools.start_screen_recording()).startswith("Successfully started")
    assert driver.record_started is True


def test_take_mobile_screenshot_honours_take_screenshots_false(fake_manager) -> None:
    manager, driver = fake_manager
    manager._take_screenshots = False
    result = _run(tools.take_mobile_screenshot("x"))
    assert result.startswith("Error:") and "TAKE_SCREENSHOTS" in result
    assert driver.screenshots == []
    manager._take_screenshots = True
    assert _run(tools.take_mobile_screenshot("x")).startswith("Successfully captured")
    assert len(driver.screenshots) == 1


# ── 5. argument and configuration validation ────────────────────────────────


@pytest.mark.parametrize("tool_name", ["click_element", "enter_text", "clear_text", "long_press_element"])
@pytest.mark.parametrize("identifier", ["", "   "])
def test_empty_selector_is_rejected_without_touching_the_driver(fake_manager, tool_name, identifier) -> None:
    _manager, driver = fake_manager
    kwargs = {"accessibility_id": identifier}
    if tool_name == "enter_text":
        kwargs["text"] = "hello"
    result = _run(getattr(tools, tool_name)(**kwargs))
    assert result.startswith("Error:") and "accessibility_id" in result
    assert driver.finds == []


def test_tap_with_non_numeric_coordinates_is_a_clean_error(fake_manager) -> None:
    _manager, driver = fake_manager
    result = _run(tools.tap(x="abc", y=5))
    assert result == "Error: x must be a number, got 'abc'."
    assert driver.w3c_actions == []
    assert _run(tools.tap(x=float("nan"), y=5)).startswith("Error: x must be a finite number")


def test_swipe_with_non_numeric_coordinates_is_a_clean_error(fake_manager) -> None:
    _manager, driver = fake_manager
    assert _run(tools.swipe(start_x="left", start_y=1, end_x=2, end_y=3)).startswith("Error: start_x must be a number")
    assert driver.swipes == []


def test_negative_durations_are_rejected(fake_manager) -> None:
    _manager, driver = fake_manager
    assert _run(tools.tap(x=10, y=10, wait_before_action=-1)).startswith("Error: wait_before_action must be zero or a positive number")
    assert _run(tools.swipe(start_x=1, start_y=1, end_x=2, end_y=2, duration_ms=-5)).startswith("Error: duration_ms must be zero or a positive number")
    assert _run(tools.long_press_element(accessibility_id="OK", duration_ms=-5)).startswith("Error: duration_ms must be zero or a positive number")
    assert driver.swipes == [] and driver.finds == []


def test_swipe_duration_reaches_driver_as_int(fake_manager) -> None:
    _manager, driver = fake_manager
    assert _run(tools.swipe(start_x=1, start_y=1, end_x=2, end_y=2, duration_ms=250.0)).startswith("Successfully")
    assert driver.swipes[0][-1] == 250 and isinstance(driver.swipes[0][-1], int)


def test_invalid_device_os_is_a_config_error_not_silent_android(monkeypatch, clean_manager_state, tmp_path) -> None:
    monkeypatch.setenv("DEVICE_OS", "windows")
    manager = am.AppiumManager(stake_id="bad-os", _driver_factory=lambda: FakeDriver())
    try:
        with pytest.raises(am.MobileEnvironmentError, match="DEVICE_OS"):
            _run(manager.create_session())
    finally:
        manager.shutdown_pool()
    assert any("DEVICE_OS" in problem for problem in settings.validate_mobile_settings())


@pytest.mark.parametrize("value,expected", [("iOS", "ios"), ("IOS", "ios"), ("ios17", "ios"), ("Android", "android"), ("ANDROID", "android"), ("", "android")])
def test_device_os_values_remain_backward_compatible(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("DEVICE_OS", value)
    assert settings.get_device_os() == expected
    assert settings.validate_mobile_settings() == []


@pytest.mark.parametrize("port", ["0", "70000", "-1", "abc"])
def test_out_of_range_port_is_reported_and_not_used(monkeypatch, port) -> None:
    monkeypatch.delenv("APPIUM_SERVER_URL", raising=False)
    monkeypatch.setenv("APPIUM_SERVER_PORT", port)
    assert any("APPIUM_SERVER_PORT" in problem for problem in settings.validate_mobile_settings())
    assert settings.get_appium_server_port() == 4723


def test_valid_port_is_used(monkeypatch) -> None:
    monkeypatch.delenv("APPIUM_SERVER_URL", raising=False)
    monkeypatch.setenv("APPIUM_SERVER_PORT", "65535")
    assert settings.validate_mobile_settings() == []
    assert settings.get_appium_server_url() == "http://127.0.0.1:65535"


def test_server_url_without_scheme_gets_http_prefix(monkeypatch) -> None:
    monkeypatch.setenv("APPIUM_SERVER_URL", "appium-host:4723")
    assert settings.get_appium_server_url() == "http://appium-host:4723"
    monkeypatch.setenv("APPIUM_SERVER_URL", "https://cloud.example/wd/hub")
    assert settings.get_appium_server_url() == "https://cloud.example/wd/hub"


# ── 6. platformName reconciliation and prompt handling ──────────────────────


def test_capabilities_cannot_override_platform_name_from_device_os(monkeypatch, clean_manager_state, tmp_path) -> None:
    monkeypatch.setenv("DEVICE_OS", "android")
    monkeypatch.setenv("APPIUM_CAPABILITIES", '{"platformName": "iOS", "appium:foo": 1}')
    manager = am.AppiumManager(stake_id="plat")
    try:
        caps = manager.build_default_capabilities()
    finally:
        manager.shutdown_pool()
    assert caps["platformName"] == "Android"
    assert caps["appium:foo"] == 1


def test_matching_platform_name_in_capabilities_is_left_alone(monkeypatch) -> None:
    monkeypatch.setenv("DEVICE_OS", "ios")
    assert settings.reconcile_platform_name({"platformName": "ios"}) == {"platformName": "ios"}


def _build_agent(monkeypatch, system_prompt, ltm: str = ""):
    from testzeus_hercules.core.agents import mobile_nav_agent
    from testzeus_hercules.utils import llm_helper

    monkeypatch.setattr(llm_helper, "create_chat_model", lambda *a, **k: object())
    monkeypatch.setattr(mobile_nav_agent.MobileNavAgent, "get_ltm", lambda self: ltm)
    monkeypatch.setattr(mobile_nav_agent.MobileNavAgent, "register_tools", lambda self: None)
    return mobile_nav_agent.MobileNavAgent({"model": "m"}, {}, system_prompt)


def test_custom_system_prompt_is_appended_to_the_mobile_prompt(monkeypatch) -> None:
    agent = _build_agent(monkeypatch, "Always answer in French.")
    assert "# Mobile Navigation Agent" in agent.system_message
    assert "start_mobile_session" in agent.system_message
    assert "Always answer in French." in agent.system_message
    agent_list = _build_agent(monkeypatch, ["line one", "line two"])
    assert "line one\nline two" in agent_list.system_message and "start_mobile_session" in agent_list.system_message


def test_placeholder_is_blank_without_ltm_and_filled_with_ltm(monkeypatch) -> None:
    without = _build_agent(monkeypatch, None, ltm="")
    assert "$basic_test_information" not in without.system_message
    assert "Available Test Data:" in without.system_message
    with_ltm = _build_agent(monkeypatch, None, ltm="user=alice")
    assert "user=alice" in with_ltm.system_message and "$basic_test_information" not in with_ltm.system_message
