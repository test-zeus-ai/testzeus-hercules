"""Checks on what a named field holds ('the "First Name" field contains "Ada"'), and typed values in the page text."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import pytest

from testzeus_hercules.act import browser as act
from testzeus_hercules.engine.intent import StepIntent
from testzeus_hercules.engine.step_graph import _split
from testzeus_hercules.engine.verify import Check, field_check, run_check
from tests.v2.engine_fixtures.browser import open_page

FORM = """
<html><body>
  <h1>Form Filler: Test Form</h1>
  <label for="first">First Name</label><input id="first" value="Alex">
  <label for="last">Last Name</label><input id="last" value="Morgan">
  <label for="full">Full Name</label><input id="full" value="Alex Morgan">
  <input aria-label="E-mail" value="ada@example.test">
  <label for="secret">Password</label><input id="secret" type="password" value="hunter2">
  <label for="notes">Notes</label><textarea id="notes">Ring twice</textarea>
  <input id="gone" value="Hidden away" style="display:none">
</body></html>
"""


def run(body: Callable[[Any], Awaitable[None]], html: str = FORM) -> None:
    async def go() -> None:
        async with open_page(html) as page:
            await body(page)

    asyncio.run(go())


async def check(page: Any, field: str, value: str, match: str = "contains", actions: Any = None) -> Any:
    # The engine hands checks a BrowserActions instance (not the module): test with what a run uses.
    used = act.BrowserActions() if actions is None else actions
    return await run_check(Check(kind="field", expected=value, match=match, subject=field), page=page, actions=used, step=7, timeout_ms=300, redact=lambda text: text)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- reading the step


@pytest.mark.parametrize(
    ("text", "field", "match"),
    [
        ('I verify the "First Name" field contains "Alex"', "First Name", "contains"),
        ('the "E-mail" field should contain "{{email}}"', "E-mail", "contains"),
        ('Verify "City" input is "Pune"', "City", "equals"),
        ('the "Notes" text box shows "Ring twice"', "Notes", "contains"),
        ('the "Zip" field has the value "560001"', "Zip", "contains"),
        ('the "Country" box should be "India"', "Country", "equals"),
        ('I verify the "Credit Card Type" dropdown has "Visa" selected', "Credit Card Type", "contains"),
        ('the "Month" select shows "June"', "Month", "contains"),
    ],
)
def test_a_step_naming_a_field_checks_that_fields_value(text: str, field: str, match: str) -> None:
    value = text.rsplit('"', 2)[1]
    upgraded = field_check(text, Check(kind="text", expected=value))
    assert upgraded is not None
    assert (upgraded.kind, upgraded.subject, upgraded.expected, upgraded.match) == ("field", field, value, match)
    assert upgraded.label == f"field_{match}"


@pytest.mark.parametrize(
    "text",
    [
        'I should see "Alex"',  # no field named
        'the "First Name" field contains "Alex"',  # a field, but for another text (below the check asks for "Bob")
        'the page shows "Welcome" in the header',
    ],
)
def test_a_step_without_a_field_for_the_text_keeps_its_text_check(text: str) -> None:
    expected = "Bob" if "First Name" in text else text.split('"')[1]
    assert field_check(text, Check(kind="text", expected=expected)) is None
    assert field_check(text, Check(kind="not_text", expected=expected)) is None


def test_the_step_reading_upgrades_the_text_check_it_gets_for_a_named_field() -> None:
    text = 'I verify the "First Name" field contains "Alex"'
    _, checks, uncovered = _split((StepIntent(kind="assert_text", expected="Alex"),), text)
    assert [(item.kind, item.subject, item.expected) for item in checks] == [("field", "First Name", "Alex")]
    assert not uncovered


# --------------------------------------------------------------------------- on the page


def test_the_named_field_holds_the_value() -> None:
    async def body(page: Any) -> None:
        passed = await check(page, "First Name", "alex")
        assert passed.verdict == "passed", passed.actual
        assert passed.actual == 'the "First Name" field holds "Alex"'
        assert (await check(page, "Full Name", "Alex Morgan", "equals")).verdict == "passed"
        assert (await check(page, "E-mail", "ada@example.test")).verdict == "passed", "named by aria-label"
        assert (await check(page, "Notes", "Ring")).verdict == "passed", "a textarea"

    run(body)


def test_another_value_fails_and_says_what_the_field_holds() -> None:
    async def body(page: Any) -> None:
        failed = await check(page, "First Name", "Bob")
        assert failed.verdict == "failed" and failed.actual == 'the "First Name" field holds "Alex"'
        assert (await check(page, "Full Name", "Alex", "equals")).verdict == "failed", "equals is the whole value"
        assert (await check(page, "Last Name", "Alex")).verdict == "failed", "only the named field counts, not any field holding the text"

    run(body)


def test_a_field_that_is_not_on_the_page_falls_back_to_the_page_text() -> None:
    async def body(page: Any) -> None:
        missing = await check(page, "Middle Initial", "K")
        assert missing.verdict == "failed" and missing.actual.startswith('no field named "Middle Initial" on the page; checked the page text instead: not found')
        shown = await check(page, "Surname", "Morgan")
        assert shown.verdict == "passed", "no field goes by that name, but the value is on the page (as a text check would say)"

    run(body)


def test_page_text_includes_what_visible_fields_hold_but_never_a_password() -> None:
    async def body(page: Any) -> None:
        text = await act.page_text(page)
        assert "Alex Morgan" in text and "ada@example.test" in text and "Ring twice" in text
        assert "hunter2" not in text, "a password is never page text"
        assert "Hidden away" not in text, "a field that is not shown is not"
        seen = await run_check(Check(kind="text", expected="Alex"), page=page, actions=act, step=7, timeout_ms=300, redact=lambda value: value)
        assert seen.verdict == "passed", "I should see a value typed into a field"

    run(body)


def test_every_browser_action_a_check_uses_is_on_the_runs_actions_object() -> None:
    for name in ("page_text", "text_visible", "closed_drop_down_texts", "field_values", "is_visible"):
        assert callable(getattr(act.BrowserActions(), name, None)), name


def test_a_driver_that_cannot_read_fields_checks_the_screens_text_instead() -> None:
    class ScreenOnly:
        page_text = staticmethod(act.page_text)

    async def body(page: Any) -> None:
        seen = await check(page, "First Name", "Alex", actions=ScreenOnly())
        assert seen.verdict == "passed" and "cannot be read here" in seen.actual

    run(body)


# A form like RoboForm's test page: each caption is text in a neighbouring element, not a label linked to the input, so
# Playwright finds no field by that name; the run perceives the field named by the text beside it.
UNLINKED = """
<html><body><form>
  <div class="row"><div class="caption">First Name</div><div class="input"><input name="02frstname" value="Alex"></div></div>
  <div class="row"><div class="caption">Last Name</div><div class="input"><input name="04lastname" value="Morgan"></div></div>
  <div class="row"><div class="caption">Full Name</div><div class="input"><input name="05fullname" value="Alex Morgan"></div></div>
  <div class="row"><div class="caption">Password</div><div class="input"><input name="21password" type="password" value="TestPassword@123"></div></div>
</form></body></html>
"""


def test_a_field_named_by_the_text_beside_it_is_found_as_the_run_perceives_it() -> None:
    from testzeus_hercules.engine.ports import DefaultPerceiver

    perceiver = DefaultPerceiver()

    async def body(page: Any) -> None:
        assert await act.field_values(page, "First Name") is None, "no label is linked: Playwright alone finds nothing"
        seq = iter(range(100))

        async def snapshot() -> Any:
            return await perceiver.snapshot(page, next(seq))

        def field(name: str, value: str, match: str = "contains") -> Check:
            return Check(kind="field", expected=value, match=match, subject=name)

        options: dict[str, Any] = {"page": page, "actions": act.BrowserActions(), "step": 7, "timeout_ms": 300, "redact": lambda text: text, "snapshot": snapshot}
        first = await run_check(field("First Name", "Alex"), **options)
        assert first.verdict == "passed" and first.actual == 'the "First Name" field holds "Alex"', first.actual
        assert (await run_check(field("Full Name", "Alex Morgan", "equals"), **options)).verdict == "passed"
        wrong = await run_check(field("Last Name", "Alex"), **options)
        assert wrong.verdict == "failed" and wrong.actual == 'the "Last Name" field holds "Morgan"'

    run(body, UNLINKED)


def test_a_password_field_is_compared_by_its_real_value_and_never_shown() -> None:
    from testzeus_hercules.engine.ports import DefaultPerceiver

    perceiver = DefaultPerceiver()

    async def body(page: Any) -> None:
        seq = iter(range(100))

        async def snapshot() -> Any:
            return await perceiver.snapshot(page, next(seq))

        async def reveal(view: Any, md: int) -> str | None:
            return await act.read_value(await perceiver.resolve(page, view, md))

        options: dict[str, Any] = {"page": page, "actions": act.BrowserActions(), "step": 7, "timeout_ms": 300, "redact": lambda text: text, "snapshot": snapshot, "reveal": reveal}
        right = await run_check(Check(kind="field", expected="TestPassword@123", subject="Password"), **options)
        assert right.verdict == "passed", right.actual
        assert right.actual == 'the "Password" field holds the expected value (a password: not shown)'
        wrong = await run_check(Check(kind="field", expected="hunter2", subject="Password"), **options)
        assert wrong.verdict == "failed" and wrong.actual == 'the "Password" field holds another value (a password: not shown)'
        assert "TestPassword@123" not in right.actual + wrong.actual and "***" not in wrong.actual

    run(body, UNLINKED)


# --------------------------------------------------------------------------- the field an earlier step entered


def _entered(table: dict[str, tuple[str, bool]]) -> Any:
    async def entered(name: str) -> tuple[str, bool] | None:
        return table.get(name.casefold())

    return entered


def _remembered_check(expected: str, field: str, entered: Any, match: str = "contains") -> Any:
    async def go() -> Any:
        return await run_check(Check(kind="field", expected=expected, match=match, subject=field), page=None, actions=object(), step=75, timeout_ms=200, redact=lambda text: text, entered=entered)  # type: ignore[arg-type]

    return asyncio.run(go())


def test_the_drop_down_an_earlier_step_chose_in_is_read_for_the_check() -> None:
    entered = _entered({"date of birth month": ("Jun", False), "date of birth day": ("15", False)})
    jun = _remembered_check("Jun", "Date Of Birth Month", entered)
    assert jun.verdict == "passed" and jun.actual == 'the "Date Of Birth Month" field holds "Jun"'
    assert _remembered_check("15", "Date Of Birth Day", entered).actual == 'the "Date Of Birth Day" field holds "15"'
    july = _remembered_check("Jul", "Date Of Birth Month", entered)
    assert july.verdict == "failed" and july.actual == 'the "Date Of Birth Month" field holds "Jun"'


def test_the_check_compares_the_text_literally_never_by_meaning() -> None:
    entered = _entered({"date of birth month": ("Jun", False)})
    june = _remembered_check("June", "Date Of Birth Month", entered)
    assert june.verdict == "failed" and june.actual == 'the "Date Of Birth Month" field holds "Jun"', "June is not the text the drop-down shows"


def test_a_remembered_password_is_compared_and_never_shown() -> None:
    entered = _entered({"password": ("TestPassword@123", True)})
    right = _remembered_check("TestPassword@123", "Password", entered)
    wrong = _remembered_check("other", "Password", entered)
    assert right.verdict == "passed" and right.actual == 'the "Password" field holds the expected value (a password: not shown)'
    assert wrong.verdict == "failed" and "TestPassword@123" not in wrong.actual


def test_a_field_no_step_entered_is_found_by_its_name_instead() -> None:
    async def body(page: Any) -> None:
        options: dict[str, Any] = {"page": page, "actions": act.BrowserActions(), "step": 7, "timeout_ms": 300, "redact": lambda text: text, "entered": _entered({})}
        seen = await run_check(Check(kind="field", expected="Alex", subject="First Name"), **options)
        assert seen.verdict == "passed" and seen.actual == 'the "First Name" field holds "Alex"'

    run(body)


DOB = """
<html><body><form>
  <div class="row"><div class="caption">Date Of Birth</div><div class="input">
    <select name="66mm"><option value="">Month</option><option value="1">Jan</option><option value="6">Jun</option><option value="7">Jul</option></select>
    <select name="66dd"><option value="">Day</option><option value="15">15</option></select>
  </div></div>
</form></body></html>
"""


def test_a_step_read_again_on_the_page_is_remembered_under_both_its_names() -> None:
    from types import SimpleNamespace

    from testzeus_hercules.engine.step_graph import StepRunner

    async def body(page: Any) -> None:
        month = page.locator('select[name="66mm"]')
        await month.select_option(label="Jun")
        runner = SimpleNamespace(deps=SimpleNamespace(entered={}))
        state = {"kind": "select", "case_intent": StepIntent(kind="select", target="Date Of Birth Month", value="Jun"), "value": "Jun"}
        reread = StepIntent(kind="select", target="Date Of Birth", value="Jun", source="page")
        StepRunner._remember_entered(runner, state, month, reread, SimpleNamespace(chosen={"by": "label", "option": "Jun"}))  # type: ignore[arg-type]
        assert set(runner.deps.entered) == {"date of birth month", "date of birth"}
        assert runner.deps.entered["date of birth month"].read == "option"

        assert await StepRunner._entered_field(runner, "Date Of Birth Month") == ("Jun", False), "a drop-down's chosen option label, not its value attribute"
        seen = await run_check(Check(kind="field", expected="Jun", subject="Date Of Birth Month"), page=page, actions=act.BrowserActions(), step=75, timeout_ms=300, redact=lambda text: text, entered=lambda name: StepRunner._entered_field(runner, name))  # type: ignore[arg-type]
        assert seen.verdict == "passed" and seen.actual == 'the "Date Of Birth Month" field holds "Jun"'

    run(body, DOB)
