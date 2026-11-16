"""Tests for verification in code (of the checks a model read) and the assertion ledger."""

import asyncio
from typing import Any

from testzeus_hercules.engine.intent import StepIntent
from testzeus_hercules.engine.verify import Check, LedgerEntry, check_from_intent, run_check, semantic_check, verify_visible

from tests.v2.engine_fixtures.fakes import FakeActions, FakeLocator, FakePage


def run(check: Check, actions: FakeActions, page: Any = None, redact: Any = None) -> LedgerEntry:
    page = page or FakePage(url="https://app.test/home?x=1")
    return asyncio.run(run_check(check, page=page, actions=actions, step=2, timeout_ms=10, redact=redact or (lambda text: text)))


def test_check_from_intent() -> None:
    assert check_from_intent(StepIntent(kind="assert_text", expected="Hi")) == Check(kind="text", expected="Hi")
    assert check_from_intent(StepIntent(kind="assert_not_text", expected="Err")) == Check(kind="not_text", expected="Err")
    assert check_from_intent(StepIntent(kind="assert_url", expected="/home", match="equals")) == Check(kind="url", expected="/home", match="equals")
    assert check_from_intent(StepIntent(kind="assert_looks", expected="the logo is blue")).kind == "visual"
    assert check_from_intent(StepIntent(kind="click", target="x")) is None
    assert check_from_intent(StepIntent(kind="unknown")) is None


def test_semantic_and_visual_checks_are_never_settled_by_matching_text() -> None:
    actions = FakeActions(text="The payment step is shown")
    for check in (semantic_check("The payment step is shown"), Check(kind="visual", expected="The payment step is shown")):
        entry = run(check, actions)
        assert entry.verdict == "unverified", "only a model judgement may pass these"
    assert semantic_check("x").label == "semantic"


def test_text_contains_passes_and_fails_with_actual() -> None:
    actions = FakeActions(text="Welcome,   Alice\nYour orders")
    passed = run(Check(kind="text", expected="welcome, alice"), actions)
    assert passed.verdict == "passed"
    assert passed.source == "deterministic"
    assert passed.step == 2

    failed = run(Check(kind="text", expected="Goodbye"), actions)
    assert failed.verdict == "failed"
    assert failed.check == "text_contains"
    assert failed.expected == "Goodbye"
    assert "Welcome, Alice" in failed.actual


def test_text_absent() -> None:
    actions = FakeActions(text="Invalid password")
    assert run(Check(kind="not_text", expected="Invalid password"), actions).verdict == "failed"
    assert run(Check(kind="not_text", expected="Server error"), actions).verdict == "passed"


def test_url_contains_and_equals() -> None:
    actions = FakeActions()
    assert run(Check(kind="url", expected="/home"), actions).verdict == "passed"
    assert run(Check(kind="url", expected="https://app.test/home?x=1", match="equals"), actions).verdict == "passed"
    failed = run(Check(kind="url", expected="https://app.test/home", match="equals"), actions)
    assert failed.verdict == "failed"
    assert failed.check == "url_equals"
    assert failed.actual == "https://app.test/home?x=1"


def test_actual_is_redacted() -> None:
    entry = run(Check(kind="text", expected="nope"), FakeActions(text="token s3cr3t shown"), redact=lambda text: text.replace("s3cr3t", "******"))
    assert "s3cr3t" not in entry.actual


def test_verify_visible_uses_locator_or_text_fallback() -> None:
    actions = FakeActions(text="Dashboard", invisible_mds=[9])
    page = FakePage()

    def visible(locator: Any, target: str) -> LedgerEntry:
        return asyncio.run(verify_visible(locator, target, page=page, actions=actions, step=1, timeout_ms=10, redact=lambda t: t))

    assert visible(FakeLocator(3), "Dashboard").verdict == "passed"
    assert visible(FakeLocator(9), "Dashboard").verdict == "failed"
    assert visible(None, "Dashboard").verdict == "passed"
    assert visible(None, "Reports").verdict == "failed"


class DropDownActions(FakeActions):
    """Page text as innerText gives it: a closed drop-down contributes every option."""

    def __init__(self, text: str, drop_downs: list[tuple[str, str]]) -> None:
        super().__init__(text=text)
        self.drop_downs = drop_downs

    async def closed_drop_down_texts(self, page: Any) -> list[tuple[str, str]]:
        return list(self.drop_downs)


OPTIONS = "---Choose---\nAlan Turing\nGrace Hopper"
SORT = "Name (A to Z)\nPrice (high to low)"


def test_text_found_only_among_a_closed_drop_downs_options_is_not_shown() -> None:
    actions = DropDownActions(f"Sign in\nAccount holder\n{OPTIONS}\nLogin", [(OPTIONS, "Grace Hopper")])
    assert run(Check(kind="text", expected="Alan Turing"), actions).verdict == "failed"
    assert run(Check(kind="not_text", expected="Alan Turing"), actions).verdict == "passed"
    assert run(Check(kind="text", expected="Grace Hopper"), actions).verdict == "passed", "the chosen option is shown"


def test_text_that_runs_on_past_a_drop_down_or_appears_elsewhere_still_counts() -> None:
    actions = DropDownActions(f"Products {SORT}\nTrail Lamp $9.99\nPrice list", [(SORT, "Name (A to Z)")])
    assert run(Check(kind="text", expected="Price (high to low) Trail Lamp"), actions).verdict == "passed"
    assert run(Check(kind="text", expected="Price"), actions).verdict == "passed"
