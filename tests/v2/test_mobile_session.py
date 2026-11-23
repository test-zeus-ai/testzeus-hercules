"""The native app step loop (read, decide, act, verify) against a fake Appium server, with a scripted model."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from testzeus_hercules.cases.models import MobileApp, SecretRef, Step, TestCase
from testzeus_hercules.decide.backends.stub import StubDecider
from testzeus_hercules.decide.contract import NoulAnswer
from testzeus_hercules.engine.data import CaseData
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.kernel.run_context import ListEventSink
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.mobile.session import MobileOutcome, MobileSession, MobileSetupError
from testzeus_hercules.mobile.target import resolve_target

from tests.v2.fixtures.fake_appium import FakeDevice, serve_fake_appium

Reply = dict[str, Any] | Callable[[str], dict[str, Any]]


class ScriptedModel:
    """Answers each call with the next reply (a dict, or a function of the prompt); records the prompts."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.images: list[int] = []

    async def complete(self, *, system: str, user: str, schema: type, images: Any = ()) -> Any:
        self.prompts.append(user)
        self.images.append(len(images))
        reply = self.replies.pop(0) if self.replies else {"action": "done"}
        return schema.model_validate(reply(user) if callable(reply) else reply)


def tap(name: str) -> Callable[[str], dict[str, Any]]:
    """Tap the element whose line names ``name`` (as a model reading the list would)."""

    def choose(prompt: str) -> dict[str, Any]:
        found = re.search(r"^\[(\d+)\] \w+ \"" + re.escape(name), prompt, re.MULTILINE)
        assert found, f"{name!r} not on the screen:\n{prompt}"
        return {"action": "tap", "element": int(found.group(1)), "reason": f"tap {name}"}

    return choose


def type_into(role: str, text: str) -> Callable[[str], dict[str, Any]]:
    def choose(prompt: str) -> dict[str, Any]:
        found = re.search(r"^\[(\d+)\] " + role, prompt, re.MULTILINE)
        assert found
        return {"action": "type", "element": int(found.group(1)), "text": text}

    return choose


@pytest.fixture
def fake() -> Iterator[tuple[str, FakeDevice]]:
    with serve_fake_appium() as served:
        yield served


async def _no_wait(_: float) -> None:
    return None


def session(
    tmp_path: Path, url: str, model: Any, *, decider: Any = None, test_data: dict[str, Any] | None = None, env: dict[str, str] | None = None, platform: str = "android", **options: Any
) -> MobileSession:
    app = MobileApp(platform=platform, **({"package": "com.example.settings"} if platform == "android" else {"bundle_id": "com.example.settings"}), appium_url=url)  # type: ignore[arg-type]
    case = TestCase(id="m", title="m", environment={"mobile": app}, test_data=test_data or {}, steps=[Step(action="x")])  # type: ignore[arg-type]
    data = CaseData.from_case(case, env=env or {})
    settings = Settings.load(tmp_path, env={"HERCULES_HOME": str(tmp_path / "home")})
    target = resolve_target(app, settings, env or {}, tmp_path)
    return MobileSession(
        target,
        data=data,
        evidence=EvidenceStore(tmp_path / "run", "r1", redact=data.redact),
        decider=decider or StubDecider(),
        llm=model,
        events=ListEventSink(),
        sleep=_no_wait,
        assert_timeout_ms=0,
        **options,
    )


def run(mobile: MobileSession, *steps: Step) -> list[MobileOutcome]:
    async def go() -> list[MobileOutcome]:
        await mobile.open()
        try:
            return [await mobile.run(index, step) for index, step in enumerate(steps, start=1)]
        finally:
            await mobile.close(True)

    return asyncio.run(go())


def test_a_tap_step_passes_its_quoted_check_and_keeps_evidence(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, device = fake
    model = ScriptedModel(tap("Network & internet"), {"action": "done"})
    mobile = session(tmp_path, url, model)
    [outcome] = run(mobile, Step(action="Tap 'Network & internet'", expected="'Internet' is visible"))
    assert outcome.status == "passed", outcome.actual
    assert [(entry.check, entry.verdict) for entry in outcome.ledger] == [("text", "passed")]
    assert outcome.actions == 1 and outcome.decisions == 2 and outcome.done == ('tap [1] button "Network & internet"',)
    assert device.screen == "network" and device.ended == 1
    caps = device.sessions[0]
    assert caps["appium:appPackage"] == "com.example.settings" and caps["platformName"] == "Android"
    step_dir = tmp_path / "run" / "steps" / "1"
    assert {path.name for path in step_dir.iterdir()} >= {"plain.jpg", "observation.txt", "page_source.xml", "decisions.jsonl"}
    assert (step_dir / "plain.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    assert '"Internet"' in (step_dir / "observation.txt").read_text()
    decisions = [json.loads(line) for line in (step_dir / "decisions.jsonl").read_text().splitlines()]
    assert [record["decision"]["action"] for record in decisions] == ["tap", "done"]
    assert "Done so far: 1. tap" in model.prompts[1]
    kinds = [event.kind for event in mobile.events.events]  # type: ignore[attr-defined]
    assert {"decision", "action", "screenshot"} <= set(kinds)


def test_typed_secrets_reach_the_device_but_never_the_model_or_the_evidence(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, device = fake
    model = ScriptedModel(type_into("textbox", "{{pin}}"), {"action": "done"})
    mobile = session(tmp_path, url, model, test_data={"pin": SecretRef(secret="APP_PIN")}, env={"APP_PIN": "secret-8812"})
    [outcome] = run(mobile, Step(action="Type {{pin}} into the search field", expected="'{{pin}}' is shown"))
    assert device.typed == "secret-8812"
    assert outcome.status == "passed", outcome.actual
    assert all("secret-8812" not in prompt for prompt in model.prompts)
    for path in (tmp_path / "run").rglob("*"):
        if path.is_file() and path.suffix != ".jpg":
            assert "secret-8812" not in path.read_text(), path.name
    assert "secret-8812" not in json.dumps([entry.model_dump() for entry in outcome.ledger]) + str(outcome.actual) + str(outcome.done)


def test_a_field_not_found_again_is_tapped_and_typed_into_once_the_new_screen_focuses_it(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(search_unlocatable=True, focus_after=2)) as (url, device):
        model = ScriptedModel(type_into("textbox", "Battery"), {"action": "done"})
        [outcome] = run(session(tmp_path, url, model), Step(action="Type Battery into the search field"))
    assert outcome.status == "passed", outcome.actual
    assert device.typed == "Battery"
    assert sum(1 for request in device.requests if request[1].endswith("/element/active")) == 3


def test_a_negated_quoted_check_needs_the_text_gone(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    mobile = session(tmp_path, url, ScriptedModel())
    gone, there = run(mobile, Step(action="verify", expected="'SIMs' is not visible"), Step(action="verify", expected="'Network & internet' is not shown"))
    assert gone.status == "passed" and gone.ledger[0].check == "not_text"
    assert there.status == "failed" and there.error_class == "AssertionFailed" and "shown" in (there.actual or "")


def test_verify_steps_ask_no_model_for_actions(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    model = ScriptedModel()
    [outcome] = run(session(tmp_path, url, model), Step(action="verify", expected='"Network & internet" is visible'))
    assert outcome.status == "passed" and model.prompts == []


def test_unquoted_outcomes_are_judged_by_the_decider(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    holds = StubDecider({"e1": NoulAnswer(noul=0.95)})
    [passed] = run(session(tmp_path / "a", url, ScriptedModel(), decider=holds), Step(action="verify", expected="the main settings list is shown"))
    assert passed.status == "passed" and passed.ledger[0].source == "llm"
    assert "Network & internet" in holds.calls[0].request.state["page_text"]
    [failed] = run(session(tmp_path / "b", url, ScriptedModel(), decider=StubDecider()), Step(action="verify", expected="the wifi page is shown"))
    assert failed.status == "failed"
    [unsure] = run(session(tmp_path / "c", url, ScriptedModel(), decider=StubDecider({"e1": NoulAnswer(noul=0.5)})), Step(action="verify", expected="it looks fine"))
    assert unsure.status == "unverified"


def test_a_tap_with_no_effect_is_retried_where_the_element_is_drawn_then_fails(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(frozen=True)) as (url, device):
        model = ScriptedModel(*[tap("Network & internet")] * 5)
        [outcome] = run(session(tmp_path, url, model), Step(action="Tap 'Network & internet'"))
    assert outcome.status == "failed" and outcome.error_class == "NoEffect"
    taps = [request for request in device.requests if request[1].endswith("/actions")]
    assert len(taps) >= 2  # the first tap (no locator) and the retry at the element's centre


def test_the_model_can_say_a_step_cannot_be_done(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    [outcome] = run(session(tmp_path, url, ScriptedModel({"action": "fail", "reason": "there is no Bluetooth entry"})), Step(action="Tap 'Bluetooth'"))
    assert outcome.status == "failed" and outcome.error_class == "ActionFailed" and "Bluetooth" in (outcome.actual or "")


def test_steps_stop_after_the_action_limit(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    model = ScriptedModel(*[{"action": "scroll", "direction": d} for d in ("down", "up", "left", "right")] * 3)
    [outcome] = run(session(tmp_path, url, model, max_actions=3), Step(action="Find the hidden page"))
    assert outcome.status == "failed" and outcome.error_class == "TooManyActions" and outcome.actions == 3


def test_ios_has_no_back_action(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    model = ScriptedModel({"action": "back"}, {"action": "done"})
    [outcome] = run(session(tmp_path, url, model, platform="ios"), Step(action="Go back"))
    assert outcome.done and "iOS has no system back action" in outcome.done[0]


def test_a_step_without_a_model_is_an_error(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    [outcome] = run(session(tmp_path, url, None), Step(action="Tap 'Network & internet'"))
    assert outcome.status == "error" and outcome.error_class == "UnreadableStep"


def test_a_missing_appium_server_is_a_clear_setup_error(tmp_path: Path) -> None:
    mobile = session(tmp_path, "http://127.0.0.1:9", ScriptedModel())
    with pytest.raises(MobileSetupError, match="no Appium server answers at http://127.0.0.1:9"):
        asyncio.run(mobile.open())


def test_a_refused_session_says_why(tmp_path: Path) -> None:
    with serve_fake_appium(FakeDevice(refuse_session="Could not find a connected Android device")) as (url, _):
        mobile = session(tmp_path, url, ScriptedModel())
        with pytest.raises(MobileSetupError, match="Could not find a connected Android device"):
            asyncio.run(mobile.open())


def test_screenshots_go_to_the_model_only_when_it_reads_images(tmp_path: Path, fake: tuple[str, FakeDevice]) -> None:
    url, _ = fake
    seeing = ScriptedModel({"action": "done"})
    run(session(tmp_path / "a", url, seeing, images=True), Step(action="Look at the screen"))
    assert seeing.images == [1]

    class Blind(ScriptedModel):
        accepts_images = False

    blind = Blind({"action": "done"})
    run(session(tmp_path / "b", url, blind, images=True), Step(action="Look at the screen"))
    assert blind.images == [0]

    class Refusing(ScriptedModel):
        async def complete(self, *, system: str, user: str, schema: type, images: Any = ()) -> Any:
            if images:
                raise ValueError("this model server cannot take image input")
            return await super().complete(system=system, user=user, schema=schema, images=images)

    refusing = Refusing({"action": "done"})
    [outcome] = run(session(tmp_path / "c", url, refusing, images=True), Step(action="Look at the screen"))
    assert outcome.status == "passed" and refusing.images == [0]


def test_the_server_record_never_keeps_credentials_from_the_url(tmp_path: Path) -> None:
    mobile = session(tmp_path, "http://127.0.0.1:4723", None)
    mobile.target = dataclasses.replace(mobile.target, hub_url="https://alice:s3cret@grid.example.com:4444/wd/hub")
    assert mobile.server_record() == {"url": "https://grid.example.com:4444/wd/hub", "provider": "local", "url_credentials": "true"}
