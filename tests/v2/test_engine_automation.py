"""Recording a run as automation: value templates (never secret values), addresses, the spec's data, and the step graph's records."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from testzeus_hercules.cases.automation import AutomationAction, AutomationStep, LocatorSpec, TargetSpec
from testzeus_hercules.cases.models import SecretRef, Step, TestCase
from testzeus_hercules.engine.automation import build_spec, navigated_path, references, relative_url, value_template
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.step_automation import _unexported_file_check, _without_secrets

from tests.v2.engine_fixtures.fakes import FakeActions, FakePerceiver, FakeResult
from tests.v2.test_engine_step_graph import SECRET, Harness

DATA = CaseData(values={"user": "alice"}, secrets={"password": "APP_PW"}, env={"APP_PW": SECRET})


# ── values, addresses, references ────────────────────────────────────────────


def test_a_typed_value_is_recorded_as_written_with_its_placeholders() -> None:
    assert value_template("{{password}}", SECRET, DATA) == ("{{password}}", None)
    assert value_template("user {{user}}", "user alice", DATA) == ("user {{user}}", None)
    assert value_template(None, "plain", DATA) == ("plain", None)
    assert value_template(None, None, DATA) == (None, None)


def test_a_secret_value_is_never_recorded_only_its_name() -> None:
    assert value_template(SECRET, SECRET, DATA) == ("{{password}}", None), "exactly the secret: its placeholder"
    template, problem = value_template(f"x{SECRET}x", f"x{SECRET}x", DATA)
    assert template is None and problem and SECRET not in problem


def test_addresses_are_relative_to_the_base_url_when_they_are_under_it() -> None:
    assert relative_url("/login", "http://shop.test") == "/login"
    assert relative_url("http://shop.test/a/b?x=1#y", "http://shop.test/app/") == "/a/b?x=1#y"
    assert relative_url("https://other.test/a", "http://shop.test") == "https://other.test/a"
    assert relative_url("http://shop.test/a", None) == "http://shop.test/a"


def test_a_navigation_an_action_caused_is_its_new_path() -> None:
    assert navigated_path("http://a.test/login", "http://a.test/welcome?x=1") == "/welcome"
    assert navigated_path("http://a.test/login", "http://a.test/login?error=1") is None
    assert navigated_path("http://a.test/x", "https://b.test/y?z") == "https://b.test/y"
    assert navigated_path("http://a.test/x", "") is None


def test_references_are_found_in_both_placeholder_syntaxes_in_order() -> None:
    assert references(["{{a}} and ${b}", "{{ a }} {{c}}"]) == ["a", "b", "c"]


def test_the_spec_names_only_the_data_its_steps_use_and_secrets_by_variable() -> None:
    case = TestCase(
        id="c",
        title="t",
        environment={"base_url": "http://shop.test"},
        test_data={"email": "user+{{run_id}}@shop.test", "password": SecretRef(secret="APP_PW"), "unused": "x", "db": SecretRef(secret="DB_URL"), "token": "never"},
        steps=[Step(action="a")],
    )
    steps = [
        AutomationStep(index=1, action="a", status="passed", actions=[AutomationAction(kind="fill", value="{{email}}"), AutomationAction(kind="fill", value="{{password}}")]),
        AutomationStep(index=2, action="b", status="passed", kind="api", api={"path": "/x", "save": [{"name": "token", "json_path": "$.t"}]}),
        AutomationStep(index=3, action="c", status="passed", kind="api", api={"path": "/y/${token}"}),
        AutomationStep(index=4, action="d", status="passed", kind="sql", sql={"db": "db", "query": "SELECT 1"}),
    ]
    spec = build_spec(case, steps, browser=True)
    data = {name: entry.model_dump(exclude_none=True) for name, entry in spec.data.items()}
    assert data == {
        "db": {"kind": "secret", "env": "DB_URL"},
        "email": {"kind": "value", "value": "user+{{run_id}}@shop.test"},
        "password": {"kind": "secret", "env": "APP_PW"},
        "run_id": {"kind": "builtin"},
        "token": {"kind": "saved", "step": 2},
    }
    assert spec.base_url == "http://shop.test" and spec.browser


def test_locators_that_hold_a_secret_are_dropped() -> None:
    redact = DATA.redact
    spec = TargetSpec(
        role="textbox",
        name="Password",
        locator=LocatorSpec(strategy="placeholder", value=f"e.g. {SECRET}"),
        fallbacks=[LocatorSpec(strategy="role", role="textbox", name="Password"), LocatorSpec(strategy="css", selectors=[f'[value="{SECRET}"]'])],
    )
    cleaned = _without_secrets(spec, redact)
    assert cleaned.locator == LocatorSpec(strategy="role", role="textbox", name="Password")
    assert cleaned.fallbacks == []
    assert SECRET not in cleaned.model_dump_json()


# ── the step graph's records ─────────────────────────────────────────────────


class LocatingPerceiver(FakePerceiver):
    """A fake perceiver that also records locators (one role locator per element)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.recorded: list[int] = []

    async def locators(self, page: Any, snapshot: Any, md: int, locator: Any) -> tuple[TargetSpec, dict[str, Any]]:
        self.recorded.append(md)
        element = snapshot.element(md)
        spec = LocatorSpec(strategy="role", role=element.role, name=element.name)
        return TargetSpec(role=element.role, name=element.name, locator=spec), {"candidates": [{"strategy": "role", "count": 1, "kept": True}]}

    async def text_form(self, page: Any, text: str, *, present: bool) -> tuple[str | None, list[Any]]:
        return ("text" if present else "count_zero"), []


def test_a_fill_is_recorded_with_its_locator_and_the_secret_as_a_placeholder(tmp_path: Path) -> None:
    h = Harness(tmp_path, perceiver=LocatingPerceiver())

    outcome = h.run(Step(action='I fill "{{password}}" in "Password"'), index=2)

    assert outcome.status == "passed"
    assert outcome.automation is not None
    (action,) = outcome.automation.actions
    assert (action.kind, action.value, action.faithful) == ("fill", "{{password}}", True)
    assert action.target is not None and action.target.locator == LocatorSpec(strategy="role", role="textbox", name="Password")
    record = json.loads((h.run_dir / "steps" / "2" / "locators.json").read_text())
    assert record["actions"][0]["kind"] == "fill" and record["actions"][0]["candidates"][0]["kept"]
    assert "hercules://runs/r_1/steps/2/locators.json" in outcome.evidence
    assert SECRET not in outcome.automation.model_dump_json()


def test_without_locator_recording_an_action_is_marked_for_review(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.automation is not None
    (action,) = outcome.automation.actions
    assert action.kind == "click" and not action.faithful
    assert "no locator candidates were recorded" in (action.note or "")
    assert not outcome.automation.faithful


def test_checks_and_navigation_are_recorded_with_their_templates(tmp_path: Path) -> None:
    h = Harness(tmp_path, perceiver=LocatingPerceiver(), actions=FakeActions(text="Welcome, alice"))

    opened = h.run(Step(action="Open /dashboard"))
    checked = h.run(Step(action="verify", expected='I should see "Welcome, {{user}}"'), index=2)

    assert opened.automation is not None and opened.automation.actions == [AutomationAction(kind="navigate", url="/dashboard")]
    assert checked.automation is not None
    (check,) = checked.automation.checks
    assert (check.kind, check.expected, check.form, check.verdict) == ("text", "Welcome, {{user}}", "text", "passed")


def test_a_check_judged_by_meaning_is_kept_for_review_with_the_page_it_was_judged_on(tmp_path: Path) -> None:
    h = Harness(tmp_path, perceiver=LocatingPerceiver(), actions=FakeActions(text="Your order is on its way"))

    outcome = h.run(Step(action="verify", expected="the order is confirmed", semantic=True))

    assert outcome.status == "passed"
    assert outcome.automation is not None
    (check,) = outcome.automation.checks
    assert (check.kind, check.source, check.faithful) == ("semantic", "llm", False)
    assert check.observed == "page at /login"
    assert "write an assertion for it in code" in (check.note or "")


def test_a_failed_action_is_not_recorded_only_the_one_that_worked(tmp_path: Path) -> None:
    h = Harness(tmp_path, perceiver=LocatingPerceiver(), actions=FakeActions(fail_mds=[6]))

    outcome = h.run(Step(action='click "Sign in"'))

    assert outcome.automation is not None
    assert outcome.automation.actions == [], "the click never succeeded"


def test_a_select_records_how_the_option_was_chosen(tmp_path: Path) -> None:
    class Choosing(FakeActions):
        async def select(self, locator: Any, value: str, *, timeout_ms: int = 0) -> FakeResult:
            await super().select(locator, value)
            return _Chosen(ok=True, detail="selected", chosen={"by": "label", "option": value})

    h = Harness(tmp_path, perceiver=LocatingPerceiver(), actions=Choosing())
    from tests.v2.engine_fixtures.fakes import intent

    h.deps.interpreter.readings["select {{user}} in Language"] = intent("select", "Language", value="{{user}}")  # type: ignore[union-attr]
    outcome = h.run(Step(action="select {{user}} in Language"))

    assert outcome.status == "passed", outcome.actual
    assert outcome.automation is not None
    (action,) = outcome.automation.actions
    assert (action.kind, action.select_by, action.option) == ("select", "label", "{{user}}")


class _Chosen(FakeResult):
    def __init__(self, *, chosen: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "chosen", chosen)


def test_a_text_check_on_a_download_without_text_is_not_exported(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("notes.txt", "Total 42")
    image = tmp_path / "logo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nTotal 42")
    notes = tmp_path / "notes.txt"
    notes.write_text("Total 42")

    assert _unexported_file_check("text", [archive]) == "the download is a zip file, which has no text to check"
    assert _unexported_file_check("not_text", [image]) == "the download is a png file, which has no text to check"
    assert _unexported_file_check("text", [notes]) is None
