"""Safety regression tests for testzeus_hercules.core.playwright_manager.

Covers lifecycle hazards that previously leaked browsers, dropped work, or
dead-locked on initialization:

- concurrent async_initialize calls must not start two playwright drivers
- get_current_page must close (not orphan) a stale browser context
- pages created after initialization (new tabs, fallbacks, popups) must receive
  the full DOM-mutation instrumentation
- fire-and-forget tasks (stop_playwright, log writes) must be strongly referenced
- close_browser_context must close the browser process and remove the temp
  user-data dir
- the stable-network wait must be able to reach its idle threshold and must
  honor timeout_overwrite

No real browser is launched: instances are created via object.__new__ (bypassing
the singleton __new__ and the config-heavy __init__), mirroring the approach in
tests/test_network_log_redaction.py.
"""

import asyncio
import os
import time
import weakref
from typing import Any, Dict, List

import testzeus_hercules.core.playwright_manager as pm_module
from testzeus_hercules.core.playwright_manager import PlaywrightManager

# ── fakes ─────────────────────────────────────────────────────────────────────


class FakePage:
    def __init__(self, url: str = "about:blank", fail_wait: bool = False) -> None:
        self.url = url
        self.handlers: Dict[str, List[Any]] = {}
        self.exposed: List[str] = []
        self.frames: List[Any] = []
        self.main_frame: Any = object()
        self._fail_wait = fail_wait

    def is_closed(self) -> bool:
        return False

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        if event in self.handlers and handler in self.handlers[event]:
            self.handlers[event].remove(handler)

    async def wait_for_load_state(self, state: str = "load", **kwargs: Any) -> None:
        if self._fail_wait:
            raise RuntimeError(f"page closed while waiting for {state}")

    async def expose_function(self, name: str, callback: Any) -> None:
        self.exposed.append(name)


class FakeContext:
    def __init__(self, pages: List[FakePage]) -> None:
        self.pages = pages
        self.created = 0
        self.closed = 0
        self.registered_handlers: Dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.registered_handlers[event] = handler

    async def new_page(self) -> FakePage:
        self.created += 1
        page = FakePage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed += 1


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.closed = 0

    async def new_context(self, **kwargs: Any) -> FakeContext:
        return self._context

    async def close(self) -> None:
        self.closed += 1


class FakeLauncher:
    """Stands in for playwright's BrowserType; records launch options."""

    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser
        self.launch_options: Dict[str, Any] = {}

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        self.launch_options = kwargs
        return self._browser


class FakeRequest:
    method = "POST"
    url = "https://example.test/login"
    headers = {"content-type": "application/json", "authorization": "Bearer abc"}
    post_data = '{"username": "alice"}'


class FakeResponse:
    status = 200
    url = "https://example.test/login"
    headers = {"content-type": "application/json"}


class FakeConsoleMessage:
    type = "log"
    text = "hello"
    location = {"url": "https://example.test/", "lineNumber": 1, "columnNumber": 1}


# ── helpers ───────────────────────────────────────────────────────────────────


def _bare_manager() -> PlaywrightManager:
    """Manager instance without __init__ (no config/browser stack touched)."""
    manager = object.__new__(PlaywrightManager)
    manager._instrumented_pages = weakref.WeakSet()
    manager._browser_context = None
    manager._browser = None
    manager._user_data_dir = None
    manager.current_page = None
    manager._enable_tracing = False
    manager._trace_dir = None
    manager._record_video = False
    manager.log_requests_responses = False
    manager.log_console = False
    return manager


def _record_instrumentation(manager: PlaywrightManager, seen: list) -> None:
    """Replace manager._instrument_page with a recorder."""

    async def fake_instrument(page) -> None:
        seen.append(page)

    manager._instrument_page = fake_instrument


# ── F1: async_initialize concurrency ─────────────────────────────────────────


def test_async_initialize_runs_each_step_once_under_concurrency(tmp_path) -> None:
    manager = object.__new__(PlaywrightManager)
    manager._PlaywrightManager__async_initialize_done = False
    manager._init_lock = asyncio.Lock()
    manager._screenshots_dir = str(tmp_path / "screenshots")
    manager._video_dir = str(tmp_path / "videos")
    manager._enable_tracing = False
    manager._trace_dir = None
    manager.request_response_log_file = str(tmp_path / "network_logs.json")
    manager.console_log_file = str(tmp_path / "console_logs.json")

    counters = {"start_playwright": 0, "ensure_browser_context": 0, "setup_handlers": 0, "go_to_homepage": 0}

    def make_stub(name: str):
        async def stub() -> None:
            counters[name] += 1
            await asyncio.sleep(0)  # force interleaving between the two callers

        return stub

    manager.start_playwright = make_stub("start_playwright")
    manager.ensure_browser_context = make_stub("ensure_browser_context")
    manager.setup_handlers = make_stub("setup_handlers")
    manager.go_to_homepage = make_stub("go_to_homepage")

    async def drive() -> None:
        await asyncio.gather(manager.async_initialize(), manager.async_initialize())

    asyncio.run(drive())

    assert counters == {"start_playwright": 1, "ensure_browser_context": 1, "setup_handlers": 1, "go_to_homepage": 1}
    assert manager._PlaywrightManager__async_initialize_done is True


def test_async_initialize_fast_path_after_completion(tmp_path) -> None:
    manager = object.__new__(PlaywrightManager)
    manager._PlaywrightManager__async_initialize_done = True
    manager._init_lock = asyncio.Lock()

    async def fail() -> None:
        raise AssertionError("must not run again")

    manager.start_playwright = fail

    asyncio.run(manager.async_initialize())  # returns immediately, fail() not called


# ── F2: get_current_page closes the orphaned context ─────────────────────────


def test_get_current_page_closes_stale_context_on_error() -> None:
    old_context = FakeContext(pages=[])
    new_context = FakeContext(pages=[])

    manager = _bare_manager()
    manager._browser_context = old_context

    calls = {"count": 0}

    async def fake_get_browser_context() -> FakeContext:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient failure")
        return new_context

    async def fake_create_browser_context() -> None:
        manager._browser_context = new_context  # mirror what the real method does

    manager.get_browser_context = fake_get_browser_context
    manager.create_browser_context = fake_create_browser_context

    instrumented: list = []
    _record_instrumentation(manager, instrumented)

    page = asyncio.run(manager.get_current_page())

    assert old_context.closed == 1, "stale context must be closed, not orphaned"
    assert manager._browser_context is new_context
    assert new_context.created == 1
    assert page is new_context.pages[-1]
    assert instrumented == [page]


def test_get_current_page_instruments_page_created_when_none_exist() -> None:
    context = FakeContext(pages=[])
    manager = _bare_manager()

    async def fake_get_browser_context() -> FakeContext:
        return context

    manager.get_browser_context = fake_get_browser_context

    instrumented: list = []
    _record_instrumentation(manager, instrumented)

    page = asyncio.run(manager.get_current_page())

    assert context.created == 1
    assert instrumented == [page]


# ── F3: full instrumentation on every page ────────────────────────────────────


def test_instrument_page_registers_observer_plumbing_once() -> None:
    page = FakePage()
    manager = _bare_manager()

    asyncio.run(manager._instrument_page(page))
    asyncio.run(manager._instrument_page(page))

    assert page.exposed == ["dom_mutation_change_detected"], "expose_function must run exactly once"
    assert len(page.handlers.get("domcontentloaded", [])) == 1
    assert len(page.handlers.get("frameattached", [])) == 1
    assert page in manager._instrumented_pages


def test_instrument_page_concurrent_calls_register_handlers_once() -> None:
    """Popup + inline instrumentation racing on the same page must not stack duplicate handlers.

    The page is marked as in-progress BEFORE the first await, so the second call
    observes it as already instrumented even though the first call is still mid-way.
    """
    manager = _bare_manager()
    page = FakePage()

    async def yield_then_return(state: str = "load", **kwargs: Any) -> None:
        await asyncio.sleep(0)  # suspend so the concurrent call reaches the membership check

    page.wait_for_load_state = yield_then_return

    async def drive() -> None:
        await asyncio.gather(manager._instrument_page(page), manager._instrument_page(page))

    asyncio.run(drive())

    assert page.exposed == ["dom_mutation_change_detected"], "expose_function must run exactly once under concurrency"
    assert len(page.handlers.get("domcontentloaded", [])) == 1
    assert len(page.handlers.get("frameattached", [])) == 1
    assert page in manager._instrumented_pages


def test_instrument_page_failure_releases_marker_and_allows_retry() -> None:
    """A failed attempt must drop its in-progress marker so a later call retries the page."""
    manager = _bare_manager()
    page = FakePage()
    attempts: list[str] = []

    async def failing_wait(state: str = "load", **kwargs: Any) -> None:
        attempts.append(state)
        raise RuntimeError("page closed while waiting")

    page.wait_for_load_state = failing_wait

    asyncio.run(manager._instrument_page(page))
    asyncio.run(manager._instrument_page(page))

    assert len(attempts) == 2, "a failed attempt must be retryable (in-progress marker released)"
    assert page not in manager._instrumented_pages


def test_reuse_or_create_tab_instruments_force_new_tab() -> None:
    context = FakeContext(pages=[])
    manager = _bare_manager()

    async def fake_get_browser_context() -> FakeContext:
        return context

    manager.get_browser_context = fake_get_browser_context

    instrumented: list = []
    _record_instrumentation(manager, instrumented)

    page = asyncio.run(manager.reuse_or_create_tab(force_new_tab=True))

    assert context.created == 1
    assert instrumented == [page]


def test_reuse_or_create_tab_last_resort_creates_instrumented_tab() -> None:
    stuck_page = FakePage(fail_wait=True)
    context = FakeContext(pages=[stuck_page])
    manager = _bare_manager()

    async def fake_get_browser_context() -> FakeContext:
        return context

    async def no_active_page():
        return None

    manager.get_browser_context = fake_get_browser_context
    manager.detect_active_tab_page = no_active_page

    instrumented: list = []
    _record_instrumentation(manager, instrumented)

    page = asyncio.run(manager.reuse_or_create_tab())

    assert page is not stuck_page
    assert context.created == 1
    assert instrumented == [page]


def test_context_page_handler_instruments_popups() -> None:
    manager = _bare_manager()
    popup = FakePage()

    instrumented: list = []
    _record_instrumentation(manager, instrumented)

    async def drive() -> None:
        before = set(PlaywrightManager._background_tasks)
        manager._on_context_page(popup)
        tracked = set(PlaywrightManager._background_tasks) - before
        assert len(tracked) == 1, "popup instrumentation task must be strongly referenced"
        await asyncio.sleep(0.05)
        assert instrumented == [popup]
        assert all(task not in PlaywrightManager._background_tasks for task in tracked)

    asyncio.run(drive())


# ── F4: close_instance keeps a reference to the stop task ─────────────────────


def _restore_class_state(saved_instances: Dict[str, PlaywrightManager], saved_default) -> None:
    PlaywrightManager._instances.clear()
    PlaywrightManager._instances.update(saved_instances)
    PlaywrightManager._default_instance = saved_default


def test_close_instance_tracks_stop_task_until_done() -> None:
    instance = object.__new__(PlaywrightManager)
    stopped: list = []

    async def fake_stop() -> None:
        stopped.append(True)
        await asyncio.sleep(0.02)

    instance.stop_playwright = fake_stop

    saved_instances = dict(PlaywrightManager._instances)
    saved_default = PlaywrightManager._default_instance
    try:
        PlaywrightManager._instances["safety-test"] = instance

        async def drive() -> None:
            before = set(PlaywrightManager._background_tasks)
            PlaywrightManager.close_instance("safety-test")
            assert "safety-test" not in PlaywrightManager._instances
            tracked = set(PlaywrightManager._background_tasks) - before
            assert len(tracked) == 1, "stop task must be strongly referenced"
            await asyncio.sleep(0.1)
            assert stopped == [True]
            assert all(task not in PlaywrightManager._background_tasks for task in tracked), "done task must be discarded"

        asyncio.run(drive())
    finally:
        _restore_class_state(saved_instances, saved_default)


def test_close_instance_retrieves_stop_task_exception() -> None:
    instance = object.__new__(PlaywrightManager)

    async def failing_stop() -> None:
        raise RuntimeError("close failed")

    instance.stop_playwright = failing_stop

    saved_instances = dict(PlaywrightManager._instances)
    saved_default = PlaywrightManager._default_instance
    try:
        PlaywrightManager._instances["safety-test"] = instance

        async def drive() -> None:
            before = set(PlaywrightManager._background_tasks)
            PlaywrightManager.close_instance("safety-test")
            tracked = set(PlaywrightManager._background_tasks) - before
            assert len(tracked) == 1
            await asyncio.sleep(0.05)
            # The done-callback retrieved the exception and discarded the task;
            # no "exception was never retrieved" warning is emitted.
            assert all(task not in PlaywrightManager._background_tasks for task in tracked)

        asyncio.run(drive())
    finally:
        _restore_class_state(saved_instances, saved_default)


# ── F6: log writes are tracked tasks ──────────────────────────────────────────


def test_log_request_and_response_tasks_are_tracked_and_complete() -> None:
    captured: list = []
    manager = _bare_manager()
    manager.request_response_log_file = "unused.jsonl"

    async def fake_write(entry, log_file) -> None:
        captured.append(entry)

    manager._write_log_entry_to_file = fake_write

    async def drive() -> None:
        before = set(PlaywrightManager._background_tasks)
        manager.log_request(FakeRequest())
        manager.log_response(FakeResponse())
        tracked = set(PlaywrightManager._background_tasks) - before
        assert len(tracked) == 2, "log write tasks must be strongly referenced"
        await asyncio.sleep(0.05)
        assert len(captured) == 2
        assert all(task not in PlaywrightManager._background_tasks for task in tracked)

    asyncio.run(drive())


def test_log_console_message_tasks_are_tracked_and_complete() -> None:
    captured: list = []
    manager = _bare_manager()
    manager.console_log_file = "unused.jsonl"

    async def fake_write(entry, log_file) -> None:
        captured.append(entry)

    manager._write_log_entry_to_file = fake_write

    async def drive() -> None:
        before = set(PlaywrightManager._background_tasks)
        manager.log_console_message(FakeConsoleMessage())
        tracked = set(PlaywrightManager._background_tasks) - before
        assert len(tracked) == 1
        await asyncio.sleep(0.05)
        assert len(captured) == 1
        assert captured[0]["text"] == "hello"

    asyncio.run(drive())


# ── F5: browser handle + user-data dir teardown ───────────────────────────────


def test_launch_browser_with_video_stores_handles_and_close_cleans_up(tmp_path) -> None:
    manager = _bare_manager()
    manager.browser_type = "chromium"
    manager.browser_channel = None
    manager.browser_version = None
    manager.browser_path = None
    manager.isheadless = True
    manager._extension_path = None
    manager._video_dir = str(tmp_path / "videos")
    manager._record_video = True
    manager.browser_cookies = None
    manager._build_emulation_context_options = dict  # type: ignore[method-assign]

    context = FakeContext(pages=[])
    browser = FakeBrowser(context)
    launcher = FakeLauncher(browser)

    asyncio.run(manager._launch_browser_with_video(launcher, "", []))

    assert manager._browser is browser, "browser handle must be stored for teardown"
    user_data_dir = manager._user_data_dir
    assert user_data_dir is not None and os.path.isdir(user_data_dir)
    assert os.path.basename(user_data_dir).startswith("playwright-user-data-")

    asyncio.run(manager.close_browser_context())

    assert context.closed == 1
    assert browser.closed == 1, "browser process must be closed with the context"
    assert manager._browser is None
    assert manager._user_data_dir is None
    assert not os.path.exists(user_data_dir), "temp user-data dir must be removed"


def test_close_browser_context_is_idempotent(tmp_path) -> None:
    context = FakeContext(pages=[])
    browser = FakeBrowser(context)

    manager = _bare_manager()
    manager._browser_context = context
    manager._browser = browser
    manager._user_data_dir = None

    asyncio.run(manager.close_browser_context())
    asyncio.run(manager.close_browser_context())  # second call must be a safe no-op

    assert context.closed == 1
    assert browser.closed == 1
    assert manager._browser is None


# ── F7: stable-network wait ───────────────────────────────────────────────────


def test_wait_for_stable_network_can_reach_idle_under_default_cap(monkeypatch) -> None:
    # Shrink the constants so the test stays fast; the old cap (0.6s) was below
    # the idle threshold (2s), so idle could never be observed.
    monkeypatch.setattr(pm_module, "WAIT_FOR_NETWORK_IDLE", 0.2)
    monkeypatch.setattr(pm_module, "MAX_WAIT_PAGE_LOAD_TIME", 0.05)

    page = FakePage()
    manager = _bare_manager()

    async def fake_get_current_page() -> FakePage:
        return page

    manager.get_current_page = fake_get_current_page

    async def drive() -> float:
        start = time.monotonic()
        await manager._wait_for_stable_network()
        return time.monotonic() - start

    elapsed = asyncio.run(drive())

    assert elapsed >= 0.15, f"wait gave up before the idle threshold could be reached ({elapsed:.3f}s)"
    # It must break on idle, not burn the whole cap (max(0.05, 0.2 + 0.5) = 0.7s).
    assert elapsed < 0.7
    # Listeners are always detached, even on the timeout path.
    assert "request" not in page.handlers or not page.handlers["request"]
    assert "response" not in page.handlers or not page.handlers["response"]


def test_wait_for_stable_network_honors_timeout_overwrite(monkeypatch) -> None:
    monkeypatch.setattr(pm_module, "WAIT_FOR_NETWORK_IDLE", 0.5)
    monkeypatch.setattr(pm_module, "MAX_WAIT_PAGE_LOAD_TIME", 10.0)

    page = FakePage()
    manager = _bare_manager()

    async def fake_get_current_page() -> FakePage:
        return page

    manager.get_current_page = fake_get_current_page

    async def drive() -> float:
        start = time.monotonic()
        await manager._wait_for_stable_network(timeout=0.25)
        return time.monotonic() - start

    elapsed = asyncio.run(drive())

    # The explicit timeout caps the wait well below both the idle threshold
    # (0.5s) and the default cap (~10s); without the fix this call would have
    # burned the full 10s default cap.
    assert elapsed < 0.5, f"timeout_overwrite was not honored as the cap ({elapsed:.3f}s)"


def test_wait_for_page_and_frames_load_passes_timeout_overwrite() -> None:
    manager = _bare_manager()

    async def fake_get_current_page() -> FakePage:
        return FakePage()

    manager.get_current_page = fake_get_current_page

    seen: Dict[str, Any] = {}

    async def fake_wait(timeout: Any = None) -> None:
        seen["timeout"] = timeout

    manager._wait_for_stable_network = fake_wait

    asyncio.run(manager.wait_for_page_and_frames_load(timeout_overwrite=3.5))
    assert seen["timeout"] == 3.5

    asyncio.run(manager.wait_for_page_and_frames_load())
    assert seen["timeout"] is None
