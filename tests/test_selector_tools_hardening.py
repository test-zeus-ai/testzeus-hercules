"""Safety regression tests for the selector tools.

- click must register and remove its dialog handler in balanced pairs per call
  (no handler pile-up on the long-lived singleton page), including when the
  click itself fails
- bulk_select_option must tolerate a malformed entry: apply the remaining
  entries, report the failure as an error row, and surface the failed count
"""

import asyncio
from typing import Any, Dict, List

import pytest
import testzeus_hercules.core.tools.click_using_selector as click_module
import testzeus_hercules.core.tools.dropdown_using_selector as dropdown_module

# ── fakes ─────────────────────────────────────────────────────────────────────


class FakePage:
    """Records dialog listener registration/removal like a Playwright page."""

    def __init__(self) -> None:
        self.handlers: Dict[str, List[Any]] = {}
        self.calls: List[tuple] = []

    def on(self, event: str, handler: Any) -> None:
        self.calls.append(("on", event, handler))
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self.calls.append(("remove_listener", event, handler))
        if event in self.handlers and handler in self.handlers[event]:
            self.handlers[event].remove(handler)


class FakeBrowserManager:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def get_current_page(self) -> FakePage:
        return self._page

    async def take_screenshots(self, name: str, page: Any) -> None:
        pass

    async def highlight_element(self, selector: str) -> None:
        pass

    async def wait_for_load_state_if_enabled(self, page: Any = None) -> None:
        pass


# ── click: balanced dialog listener registration ──────────────────────────────


def _install_click_fakes(monkeypatch: pytest.MonkeyPatch, page: FakePage, do_click: Any) -> None:
    monkeypatch.setattr(click_module, "PlaywrightManager", lambda: FakeBrowserManager(page))
    monkeypatch.setattr(click_module, "do_click", do_click)


def test_click_registers_and_removes_dialog_handler_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()

    async def fake_do_click(page: Any, selector: str, wait: float, click_type: str) -> Dict[str, str]:
        return {"summary_message": "ok", "detailed_message": "Clicked ok"}

    _install_click_fakes(monkeypatch, page, fake_do_click)

    result = asyncio.run(click_module.click("md_submit"))

    assert result == "Clicked ok"
    on_calls = [c for c in page.calls if c[0] == "on"]
    remove_calls = [c for c in page.calls if c[0] == "remove_listener"]
    assert [(c[1], c[2]) for c in on_calls] == [("dialog", on_calls[0][2])]
    assert [(c[1], c[2]) for c in remove_calls] == [("dialog", on_calls[0][2])]
    # the exact handler object registered is also the one removed
    assert on_calls[0][2] is remove_calls[0][2]
    assert page.handlers.get("dialog") == []


def test_click_keeps_dialog_handlers_balanced_across_repeated_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()

    async def fake_do_click(page: Any, selector: str, wait: float, click_type: str) -> Dict[str, str]:
        return {"summary_message": "ok", "detailed_message": "Clicked ok"}

    _install_click_fakes(monkeypatch, page, fake_do_click)

    for _ in range(3):
        asyncio.run(click_module.click("md_submit"))

    on_calls = [c for c in page.calls if c[0] == "on" and c[1] == "dialog"]
    remove_calls = [c for c in page.calls if c[0] == "remove_listener" and c[1] == "dialog"]
    assert len(on_calls) == 3
    assert len(remove_calls) == 3
    assert page.handlers.get("dialog") == []


def test_click_removes_dialog_handler_when_click_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage()

    async def failing_do_click(page: Any, selector: str, wait: float, click_type: str) -> Dict[str, str]:
        raise RuntimeError("boom")

    _install_click_fakes(monkeypatch, page, failing_do_click)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(click_module.click("md_submit"))

    assert page.handlers.get("dialog") == []
    assert any(c[0] == "remove_listener" and c[1] == "dialog" for c in page.calls)


# ── bulk_select_option: per-entry error tolerance ─────────────────────────────


def test_bulk_select_option_continues_past_malformed_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: List[Dict[str, str]] = []

    async def fake_select_option(entry: Dict[str, str]) -> str:
        applied.append(entry)
        return f"Success. Dropdown selector '{entry['selector']}' selected option text 'v' with value 'v'"

    monkeypatch.setattr(dropdown_module, "select_option", fake_select_option)

    entries: List[Any] = [
        {"selector": "sel_a", "value_to_fill": "optA"},
        {"selector": "malformed"},  # missing value_to_fill -> ValueError
        {"selector": "sel_c", "value_to_fill": "optC"},
    ]

    results = asyncio.run(dropdown_module.bulk_select_option(entries))

    # the two valid entries were still applied
    assert applied == [entries[0], entries[2]]

    assert len(results) == 4
    assert results[0]["selector"] == "sel_a"
    assert "Success" in results[0]["result"]
    assert results[1] == {"option": {"selector": "malformed"}, "error": "Entry must contain value_to_fill."}
    assert results[2]["selector"] == "sel_c"
    assert "Success" in results[2]["result"]
    assert "summary" in results[3]
    assert "1 failed" in results[3]["summary"]
    assert "2 of 3 entries applied successfully" in results[3]["summary"]


def test_bulk_select_option_all_valid_has_no_summary_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_select_option(entry: Dict[str, str]) -> str:
        return f"Success. Dropdown selector '{entry['selector']}' selected option text 'v' with value 'v'"

    monkeypatch.setattr(dropdown_module, "select_option", fake_select_option)

    entries: List[Dict[str, str]] = [
        {"selector": "sel_a", "value_to_fill": "optA"},
        {"selector": "sel_b", "value_to_fill": "optB"},
    ]

    results = asyncio.run(dropdown_module.bulk_select_option(entries))

    assert len(results) == 2
    assert all("summary" not in row for row in results)


def test_bulk_select_option_tolerates_non_dict_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: List[Dict[str, str]] = []

    async def fake_select_option(entry: Dict[str, str]) -> str:
        applied.append(entry)
        return f"Success. Dropdown selector '{entry['selector']}' selected option text 'v' with value 'v'"

    monkeypatch.setattr(dropdown_module, "select_option", fake_select_option)

    entries: List[Any] = [
        "not-a-dict",  # malformed: neither dict nor (selector, value) pair
        {"selector": "sel_b", "value_to_fill": "optB"},
    ]

    results = asyncio.run(dropdown_module.bulk_select_option(entries))

    assert applied == [entries[1]]
    assert len(results) == 3
    assert results[0]["option"] == "not-a-dict"
    assert "Entry must contain selector and value_to_fill." in results[0]["error"]
    assert results[1]["selector"] == "sel_b"
    assert "1 failed" in results[2]["summary"]


# ── selector-store failure hook honours ENABLE_SELECTOR_RECORDING ─────────────


class _RecordingStore:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def lookup(self, name: str) -> Dict[str, str]:
        self.calls.append(f"lookup:{name}")
        return {}

    def record_failure(self, name: str) -> None:
        self.calls.append(f"failure:{name}")


def _run_not_found_click(monkeypatch: pytest.MonkeyPatch) -> _RecordingStore:
    import testzeus_hercules.healing.selector_store as store_module
    from types import SimpleNamespace

    class _NoElementManager:
        async def find_element(self, selector: str, page: Any, element_name: str = "") -> None:
            return None

    class _NullSelectorLogger:
        async def log_selector_interaction(self, **kwargs: Any) -> None:
            return None

    store = _RecordingStore()
    monkeypatch.setattr(store_module, "get_default_store", lambda: store)
    monkeypatch.setattr(click_module, "PlaywrightManager", _NoElementManager)
    monkeypatch.setattr(click_module, "get_browser_logger", lambda path: _NullSelectorLogger())
    monkeypatch.setattr(click_module, "get_global_conf", lambda: SimpleNamespace(get_proof_path=lambda: "unused"))
    try:
        asyncio.run(click_module.do_click(FakePage(), "role=button[name='Save']", 0, "click"))
    except Exception:
        pass  # the not-found outcome itself is covered elsewhere; only the store side effects matter here
    return store


def test_click_not_found_skips_selector_store_when_recording_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_SELECTOR_RECORDING", raising=False)
    assert _run_not_found_click(monkeypatch).calls == []


def test_click_not_found_updates_selector_store_when_recording_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_SELECTOR_RECORDING", "true")
    assert _run_not_found_click(monkeypatch).calls == ["lookup:role=button[name='Save']", "failure:role=button[name='Save']"]
