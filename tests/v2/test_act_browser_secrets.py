"""Typed values never reach action results, secret-like fields never expose their value, and the element handles a
custom drop-down needs are released."""

from __future__ import annotations

from typing import Any

import pytest

from testzeus_hercules.act import browser as act

from tests.v2.test_act_browser import CUSTOM_DROPDOWNS, run

TOKEN = "tok-9f8e7d6c5b4a"
SECRET_FIELDS = """
<html><body>
  <label for="otp">One-time code</label><input id="otp" name="otp" maxlength="4">
  <label for="api">API key</label><input id="api" name="api_key" onblur="this.value = this.value + 'Z9'">
  <label for="pw">Password</label><input id="pw" type="password" onblur="this.value = this.value + '1'">
  <label for="city">City</label><input id="city" name="city" onblur="this.value = this.value + ' (UK)'">
  <label for="mangle">Note</label><input id="mangle" oninput="this.value = this.value.slice(0, 3) + 'x'">
</body></html>
"""


def _everything(result: act.ActionResult) -> str:
    return repr(result)


def test_a_secret_like_text_field_never_echoes_the_typed_value() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#otp"), "123456")
        assert not result.ok and result.kind == "too_long"
        assert "123456" not in _everything(result) and "1234" not in _everything(result)
        assert result.field is not None and result.field.value is None and result.field.secret

    run(body, SECRET_FIELDS)


def test_a_changed_secret_field_keeps_its_value_out_of_the_result() -> None:
    async def body(page: Any) -> None:
        for selector, typed in (("#api", TOKEN), ("#pw", "hunter2")):
            result = await act.fill(page.locator(selector), typed)
            assert not result.ok and result.kind == "changed", result.detail
            assert typed not in _everything(result) and typed.upper() not in _everything(result)
            assert result.field is not None and result.field.value is None

    run(body, SECRET_FIELDS)


def test_an_ordinary_changed_field_still_reports_its_value_for_the_step_graph() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#city"), "London")
        assert not result.ok and result.kind == "changed"
        assert result.field is not None and result.field.value == "London (UK)"
        assert "London" not in result.detail, "the detail gives lengths only"

    run(body, SECRET_FIELDS)


def test_a_value_mismatch_detail_gives_lengths_only() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#mangle"), TOKEN)
        assert not result.ok and result.kind == "value_mismatch"
        assert TOKEN[:3] not in result.detail and TOKEN not in result.detail

    run(body, SECRET_FIELDS)


def test_field_facts_leave_out_a_password_value() -> None:
    async def body(page: Any) -> None:
        await page.fill("#pw", "hunter2")
        facts = await act.field_facts(page.locator("#pw"))
        assert facts is not None and facts.value is None and facts.secret
        plain = await act.field_facts(page.locator("#city"))
        assert plain is not None and plain.value == "" and not plain.secret

    run(body, SECRET_FIELDS)


def test_a_custom_drop_down_releases_every_element_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    released: list[Any] = []
    original = act._dispose

    async def recording(handles: list[Any]) -> None:
        released.extend(handles)
        await original(handles)

    monkeypatch.setattr(act, "_dispose", recording)

    async def body(page: Any) -> None:
        result = await act.select(page.locator(".picker-display"), "Temporary")
        assert result.ok, result.detail
        assert released, "the option's handle (at least) was created and released"
        for handle in released:
            with pytest.raises(Exception):
                await handle.evaluate("el => el.tagName")

    run(body, CUSTOM_DROPDOWNS)
