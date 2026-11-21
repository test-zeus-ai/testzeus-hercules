"""Field checks ('the "First Name" field contains "Alex"') exported as code: the element the step that entered the field
used, read the way it holds its value (an input's value, a native drop-down's chosen option, a custom drop-down's text),
compared with the expected text. The exported test runs against the RoboForm test form."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationSpec, AutomationStep, LocatorSpec, TargetSpec
from testzeus_hercules.export import export_run
from tests.v2.browser_fixtures import FIXTURE_PAGES
from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.export_harness import UI_TYPESCRIPT, node, run_exported_python, typescript_syntax_errors
from tests.v2.test_export_render import result


def css(selector: str, role: str = "textbox", name: str = "") -> TargetSpec:
    return TargetSpec(role=role, name=name, locator=LocatorSpec(strategy="css", selectors=[selector]))


FIRST = css('input[name="02frstname"]', name="First Name")
PASSWORD = css('input[name="31password"]', name="Password")
CARD = css('select[name="40cc__type"]', "combobox", "Credit Card Type")
MONTH = css('select[name="66mm"]', "combobox", "Date Of Birth")


def form_spec(*checks: AutomationCheck) -> AutomationSpec:
    return AutomationSpec(
        case_id="roboform-fields",
        title="RoboForm fields",
        steps=[
            AutomationStep(index=1, action='I open "/roboform_form.html"', status="passed", actions=[AutomationAction(kind="navigate", url="/roboform_form.html")]),
            AutomationStep(
                index=2,
                action="I fill the form",
                status="passed",
                actions=[
                    AutomationAction(kind="fill", value="Alex", target=FIRST),
                    AutomationAction(kind="fill", value="TestPassword@123", target=PASSWORD),
                    AutomationAction(kind="select", option="Visa (Preferred)", select_by="label", target=CARD),
                    AutomationAction(kind="select", option="Jun", select_by="label", target=MONTH),
                ],
            ),
            AutomationStep(index=3, action="I verify the fields", status="passed", checks=list(checks)),
        ],
    )


CHECKS = (
    AutomationCheck(kind="field", expected="Alex", subject="First Name", target=FIRST, read="value"),
    AutomationCheck(kind="field", expected="TestPassword@123", match="equals", subject="Password", target=PASSWORD, read="value"),
    AutomationCheck(kind="field", expected="Visa (Preferred)", match="equals", subject="Credit Card Type", target=CARD, read="option"),
    AutomationCheck(kind="field", expected="Jun", subject="Date Of Birth Month", target=MONTH, read="option"),
)


def test_field_checks_are_written_on_the_entered_element_the_way_it_holds_its_value() -> None:
    python = export_run(result(automation=form_spec(*CHECKS)), "playwright-py").code
    ast.parse(python)
    assert 'expect(page.locator("input[name=\\"02frstname\\"]")).to_have_value(re.compile(re.escape("Alex"), re.IGNORECASE))' in python
    assert 'expect(page.locator("input[name=\\"31password\\"]")).to_have_value("TestPassword@123")' in python
    assert 'expect(page.locator("select[name=\\"40cc__type\\"]").locator("option:checked")).to_have_text("Visa (Preferred)")' in python
    assert 'expect(page.locator("select[name=\\"66mm\\"]").locator("option:checked")).to_contain_text("Jun", ignore_case=True)' in python
    assert "\nimport re\n" in python

    typescript = export_run(result(automation=form_spec(*CHECKS)), "playwright-ts").code
    assert 'await expect(page.locator("input[name=\\"02frstname\\"]")).toHaveValue(new RegExp(escapeRegExp("Alex"), \'i\'));' in typescript
    assert 'await expect(page.locator("select[name=\\"66mm\\"]").locator("option:checked")).toContainText("Jun", { ignoreCase: true });' in typescript
    assert "function escapeRegExp" in typescript


def test_a_custom_drop_down_is_checked_by_the_text_it_shows() -> None:
    picker = css(".picker-display", "button", "Status")
    check = AutomationCheck(kind="field", expected="Temporary", subject="Status", target=picker, read="text")
    python = export_run(result(automation=form_spec(check)), "playwright-py").code
    assert 'expect(page.locator(".picker-display")).to_contain_text("Temporary", ignore_case=True)' in python


def test_a_field_no_step_entered_is_a_todo_not_a_guess() -> None:
    check = AutomationCheck(
        kind="field", expected="Kochi", subject="Birth Place", faithful=False, note='no step of the run entered the "Birth Place" field, so no locator was recorded for it; check its value by hand'
    )
    exported = export_run(result(automation=form_spec(check)), "playwright-py")
    assert 'no step of the run entered the "Birth Place" field' in exported.code
    assert "to_have_value" not in exported.code and "to_have_text" not in exported.code, "no assertion on an element that was never recorded"
    assert any("Birth Place" in note for note in exported.notes)


def test_the_exported_field_checks_pass_against_the_form_and_a_wrong_value_fails(tmp_path: Path) -> None:
    chromium = require_chromium()
    with serve_fixtures(FIXTURE_PAGES) as base:
        passing = export_run(result(automation=form_spec(*CHECKS)), "playwright-py").code
        done = run_exported_python(passing, tmp_path / "pass", env={"BASE_URL": base}, chromium=chromium)
        wrong = (AutomationCheck(kind="field", expected="Jul", subject="Date Of Birth Month", target=MONTH, read="option"),)
        failing = export_run(result(automation=form_spec(*wrong)), "playwright-py").code
        failed = run_exported_python(failing, tmp_path / "fail", env={"BASE_URL": base}, chromium=chromium)
    assert done.returncode == 0 and "1 passed" in done.stdout, done.stdout[-3000:] + done.stderr[-1500:]
    assert failed.returncode != 0 and "Jul" in failed.stdout, "a drop-down holding Jun fails a check for Jul"


@pytest.mark.skipif(node() is None or not UI_TYPESCRIPT.is_dir(), reason="node and ui/node_modules/typescript are needed to parse TypeScript")
def test_the_typescript_with_field_checks_parses(tmp_path: Path) -> None:
    code = export_run(result(automation=form_spec(*CHECKS)), "playwright-ts").code
    assert typescript_syntax_errors(code, tmp_path) == []
