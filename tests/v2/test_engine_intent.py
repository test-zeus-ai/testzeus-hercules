"""Tests for step interpretation: a model reads every step (no rules), case readings are cached on disk.

The model is a fake: either an object answering the structured-output schema, or a
fake OpenAI-compatible server behind the real client, which checks what the prompt
and schema look like on the wire.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Sequence, TypeVar

import pytest
from pydantic import BaseModel

from testzeus_hercules.cases.models import SecretRef, Step, TestCase
from testzeus_hercules.engine.data import CaseData, MissingSecretError
from testzeus_hercules.engine import intent as intent_module
from testzeus_hercules.engine.intent import (
    CASE_SYSTEM_PROMPT,
    INTERPRET_SYSTEM_PROMPT,
    PROMPT_VERSION,
    CaseItem,
    case_reading_schema,
    IntentPlan,
    LLMInterpreter,
    ReadingCache,
    StepIntent,
    StepInterpreter,
    case_items,
    normalise_step_text,
)

T = TypeVar("T", bound=BaseModel)

NULLS = {"target": None, "value": None, "url": None, "expected": None, "match": None, "role": None}


def act(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **NULLS, **fields}


def test_normalising_is_lexical_only() -> None:
    assert normalise_step_text("When I click \u201cSave\u201d.") == 'I click "Save"'
    assert normalise_step_text('Then I should see "Done."') == 'I should see "Done."'
    assert normalise_step_text("make the dashboard look nice") == "make the dashboard look nice"


# --------------------------------------------------------------------------- case data


def _case(**data: Any) -> TestCase:
    return TestCase(id="c", title="t", steps=[Step(action="open /")], test_data=data)


def test_case_data_resolves_placeholders_and_secrets() -> None:
    data = CaseData.from_case(_case(user="alice", pw=SecretRef(secret="APP_PW")), env={"APP_PW": "s3cr3t!"}, run_id="r_1")

    assert data.resolve("{{user}}/{{ pw }}/{{run_id}}") == "alice/s3cr3t!/r_1"
    assert data.redact("token s3cr3t! leaked") == "token ****** leaked"
    assert "s3cr3t" not in data.fingerprint()


def test_missing_secret_raises_without_value() -> None:
    data = CaseData.from_case(_case(pw=SecretRef(secret="NOT_SET")), env={})

    with pytest.raises(MissingSecretError, match="NOT_SET"):
        data.resolve("{{pw}}")


def test_fingerprint_changes_with_data_but_not_secret_values() -> None:
    a = CaseData.from_case(_case(user="alice", pw=SecretRef(secret="P")), env={"P": "one"})
    b = CaseData.from_case(_case(user="alice", pw=SecretRef(secret="P")), env={"P": "two"})
    c = CaseData.from_case(_case(user="bob", pw=SecretRef(secret="P")), env={"P": "one"})

    assert a.fingerprint() == b.fingerprint() != c.fingerprint()


def test_intent_resolve_substitutes_fields() -> None:
    data = CaseData.from_case(_case(user="alice", base="/login"), env={})
    intent = StepIntent(kind="fill", target="User {{user}}", value="{{user}}", url="{{base}}")

    resolved = intent.resolved(data)

    assert (resolved.target, resolved.value, resolved.url) == ("User alice", "alice", "/login")
    assert intent.value == "{{user}}"  # original untouched


class PlanLLM:
    """Answers the plan schema with scripted actions."""

    def __init__(self, *actions: dict[str, Any]) -> None:
        self.actions = actions
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T:
        self.calls.append({"system": system, "user": user, "schema": schema})
        assert schema is IntentPlan
        full = [{"kind": "unknown", "target": None, "value": None, "url": None, "expected": None, "match": None, "role": None, **action} for action in self.actions]
        return schema.model_validate({"actions": full})  # type: ignore[return-value]


SIGN_IN = "they sign in with the email demo@example.com and the password {{password}}"


def test_compound_step_becomes_one_intent_per_action_in_order() -> None:
    llm = PlanLLM(
        {"kind": "fill", "target": "Email", "value": "demo@example.com"},
        {"kind": "fill", "target": "Password", "value": "{{password}}"},
        {"kind": "click", "target": "Sign in", "role": "button"},
    )
    interpreter = StepInterpreter(LLMInterpreter(llm))

    intent = asyncio.run(interpreter.interpret(SIGN_IN))

    parts = intent.parts()
    assert [(part.kind, part.target, part.value) for part in parts] == [("fill", "Email", "demo@example.com"), ("fill", "Password", "{{password}}"), ("click", "Sign in", None)]
    assert all(part.source == "case" for part in parts), "read without the page"
    assert intent.needs_target


def test_single_action_plan_has_no_followers() -> None:
    llm = PlanLLM({"kind": "click", "target": "Profile"})
    intent = asyncio.run(StepInterpreter(LLMInterpreter(llm)).interpret("open my profile from the avatar"))

    assert intent.kind == "click" and intent.then == ()


def test_every_step_goes_to_the_model_with_the_page_controls_and_is_cached_per_page() -> None:
    llm = PlanLLM({"kind": "select", "target": "Category", "value": "Accessories"})
    interpreter = StepInterpreter(LLMInterpreter(llm))
    asked: list[int] = []

    async def context() -> str:
        asked.append(1)
        return '- combobox "Category" options: All categories | Accessories'

    async def go() -> list[StepIntent]:
        return [
            await interpreter.interpret('click "Save"', context=context),
            await interpreter.interpret("limit the catalogue to accessories", context=context),
            await interpreter.interpret("limit the catalogue to accessories", context=context),
        ]

    results = asyncio.run(go())

    assert len(asked) == 3
    assert len(llm.calls) == 2, "even a plain 'click' step is read by the model; the same step on the same page is cached"
    assert 'Step: click "Save"' in llm.calls[0]["user"]
    assert 'combobox "Category" options: All categories | Accessories' in llm.calls[1]["user"]
    assert "PAGE CONTROLS" in llm.calls[1]["user"]
    assert all(result.source == "page" for result in results)


def test_on_model_failure_a_step_is_an_unknown_intent_for_the_decider() -> None:
    from testzeus_hercules.llm.client import LLMUnavailable

    failing = StepInterpreter(LLMInterpreter(ContinueLLM(error=LLMUnavailable("down"))))
    result = asyncio.run(failing.interpret("Then the user opens the profile."))
    assert (result.kind, result.target) == ("unknown", "the user opens the profile")


def test_context_failure_still_interprets_without_it() -> None:
    llm = PlanLLM({"kind": "click", "target": "Profile"})

    async def context() -> str:
        raise RuntimeError("snapshot failed")

    intent = asyncio.run(StepInterpreter(LLMInterpreter(llm)).interpret("open my profile from the avatar", context=context))

    assert intent.kind == "click"
    assert "PAGE CONTROLS" not in llm.calls[0]["user"]


def test_resolved_substitutes_placeholders_in_every_part() -> None:
    from testzeus_hercules.cases.models import Step, TestCase
    from testzeus_hercules.engine.data import CaseData

    case = TestCase(id="c", title="t", steps=[Step(action="open /")], test_data={"password": "s3cret"})
    data = CaseData.from_case(case, env={}, run_id="r_1")
    intent = StepIntent(kind="fill", target="Email", value="a@b.c", then=(StepIntent(kind="fill", target="Password", value="{{password}}"),))

    assert intent.resolved(data).then[0].value == "s3cret"


def test_plan_schema_requires_every_action_field() -> None:
    from testzeus_hercules.llm.structured import json_schema_for

    schema = json_schema_for(IntentPlan)
    members = schema["properties"]["actions"]["items"]["anyOf"]
    assert all(set(member["required"]) == set(member["properties"]) for member in members), "every field of a kind is required (null when it does not apply)"
    by_kind = {kind: member for member in members for kind in (member["properties"]["kind"].get("enum") or [member["properties"]["kind"]["const"]])}
    assert set(by_kind) == set(intent_module.ALL_KINDS)
    assert set(by_kind["fill"]["properties"]) == {"kind", "target", "value"}, "an action carries only the fields its kind uses"
    assert set(by_kind["upload"]["properties"]) == {"kind", "target", "value"}
    assert set(by_kind["download"]["properties"]) == {"kind", "target", "url", "value"}
    assert set(by_kind["assert_file_column"]["properties"]) == {"kind", "expected", "match", "target", "value"}
    assert set(by_kind["navigate"]["properties"]) == {"kind", "url"}
    assert set(by_kind["assert_url"]["properties"]) == {"kind", "expected", "match"}
    assert schema["properties"]["actions"]["minItems"] == 1


def test_a_verbose_action_with_every_field_is_still_accepted() -> None:
    plan = IntentPlan.model_validate({"actions": [{"kind": "fill", "target": "Email", "value": "a@b.c", "url": None, "expected": None, "match": None, "role": "textbox"}]})
    assert plan.actions[0].model_dump() == {"kind": "fill", "target": "Email", "value": "a@b.c"}


@pytest.mark.parametrize(
    ("intent", "text"),
    [
        (StepIntent(kind="click", target="Sign in", role="button"), 'click the "Sign in" button'),
        (StepIntent(kind="fill", target="Password", value="s3cret"), 'fill the "Password" field'),
        (StepIntent(kind="select", target="Category", value="Accessories"), 'select "Accessories" in "Category"'),
        (StepIntent(kind="upload", target="Case clearance photo", value="case.png"), 'upload a file to "Case clearance photo"'),
        (StepIntent(kind="download", target="Export CSV"), 'click "Export CSV" to download a file'),
        (StepIntent(kind="check", target="In stock only"), 'check "In stock only"'),
        (StepIntent(kind="uncheck", target="In stock only"), 'uncheck "In stock only"'),
        (StepIntent(kind="hover", target="Menu"), 'hover over "Menu"'),
        (StepIntent(kind="assert_visible", target="Logo"), '"Logo" should be visible'),
    ],
)
def test_describe_gives_a_value_free_description_of_one_action(intent: StepIntent, text: str) -> None:
    assert intent.describe() == text


class ContinueLLM:
    """Answers the continuation schema with scripted actions."""

    def __init__(self, *actions: dict[str, Any], error: Exception | None = None) -> None:
        self.actions = actions
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self.error is not None:
            raise self.error
        full = [{"kind": "unknown", "target": None, "value": None, "url": None, "expected": None, "match": None, "role": None, **action} for action in self.actions]
        return schema.model_validate({"actions": full})  # type: ignore[return-value]


def test_a_partly_done_step_asks_the_model_what_remains_on_the_current_page() -> None:
    llm = ContinueLLM(
        {"kind": "select", "target": "Account holder", "value": "Grace Hopper", "because": "Grace Hopper logs in"},
        {"kind": "click", "target": "Login", "because": "logs in as a customer"},
    )
    interpreter = StepInterpreter(LLMInterpreter(llm))

    async def context() -> str:
        return '- combobox "Account holder" (options: Alan Turing | Grace Hopper)\n- button "Login"'

    remaining = asyncio.run(interpreter.remaining("When Grace Hopper logs in as a customer", ['click button "Client sign-in"'], context=context))

    assert remaining is not None
    assert [(part.kind, part.target, part.value, part.source) for part in remaining] == [("select", "Account holder", "Grace Hopper", "page"), ("click", "Login", None, "page")]
    user = llm.calls[0]["user"]
    assert "Grace Hopper logs in as a customer" in user and not user.startswith("Step: When")
    assert '1. click button "Client sign-in"' in user
    assert '"Account holder"' in user and "PAGE CONTROLS" in user
    from testzeus_hercules.llm.structured import json_schema_for

    kinds = {
        kind
        for member in json_schema_for(llm.calls[0]["schema"])["properties"]["actions"]["items"]["anyOf"]
        for kind in (member["properties"]["kind"].get("enum") or [member["properties"]["kind"]["const"]])
    }
    assert kinds == {"navigate", "click", "fill", "select", "upload", "download", "check", "uncheck", "hover", "press", "wait"}, "a continuation plans actions only, never checks"


def test_a_finished_step_has_nothing_remaining() -> None:
    interpreter = StepInterpreter(LLMInterpreter(ContinueLLM()))

    assert asyncio.run(interpreter.remaining("they sign in", ['fill textbox "Username"'])) == ()


def test_on_model_failure_nothing_is_replanned() -> None:
    from testzeus_hercules.llm.client import LLMUnavailable

    failing = StepInterpreter(LLMInterpreter(ContinueLLM(error=LLMUnavailable("down"))))
    assert asyncio.run(failing.remaining("they sign in", [])) is None


def test_a_role_word_and_quotes_copied_into_a_planned_target_are_dropped() -> None:
    llm = PlanLLM({"kind": "click", "target": 'button "Basket (1)"', "role": "button"}, {"kind": "fill", "target": '"First Name"', "value": "Ada"})
    intent = asyncio.run(StepInterpreter(LLMInterpreter(llm)).interpret("they open the cart and type Ada"))
    assert [part.target for part in intent.parts()] == ["Basket (1)", "First Name"]


# --------------------------------------------------------------------------- whole-case reading


# The step phrasings the old regular-expression reader handled, as cases for the model's prompt and schema.
CASE_STEPS = [
    (1, 'I open "https://app.test/login"', None, [act("navigate", url="https://app.test/login")]),
    (2, 'I fill "{{password}}" in "Password"', None, [act("fill", target="Password", value="{{password}}")]),
    (3, "Tap the Continue button", '"Welcome back"', [act("click", target="Continue", role="button")]),
    (4, 'press key "Enter"', None, [act("press", value="Enter")]),
    (5, "wait 2 seconds", None, [act("wait", value="2")]),
    (
        6,
        "they sign in with the email a@b.test and the password {{password}}",
        None,
        [act("fill", target="Email", value="a@b.test"), act("fill", target="Password", value="{{password}}"), act("click", target="Sign in", role="button")],
    ),
]
EXPECTED_READING = [act("assert_text", expected="Welcome back")]


def case_reply(items: Sequence[CaseItem]) -> dict[str, Any]:
    by_step = {step: actions for step, _, _, actions in CASE_STEPS}
    return {item.key: EXPECTED_READING if item.role == "expected" else by_step[item.step] for item in items}


def _items() -> list[CaseItem]:
    return case_items([(step, action, expected) for step, action, expected, _ in CASE_STEPS])


def _read_with_server(tmp_path: Path, items: Sequence[CaseItem], replies: list[Any], *, model_id: str = "gemma-test", names: Sequence[str] = ("password",)) -> tuple[Any, Any]:
    from testzeus_hercules.kernel.settings import LLMSettings
    from testzeus_hercules.llm.capabilities import Capabilities
    from testzeus_hercules.llm.client import ChatClient
    from testzeus_hercules.llm.providers import resolve_endpoint
    from testzeus_hercules.llm.structured import StructuredLLM

    from tests.v2.test_llm_fakes import FakeServer

    server = FakeServer(kind="generic", models=[model_id], scripted=list(replies))
    capabilities = Capabilities(server_version="1", model_id=model_id, context_tokens=32768, supports_tools=False, supports_tool_choice=False, supports_json_schema=True, image_mode="none")

    async def run() -> Any:
        async with server.client() as http:
            client = ChatClient(resolve_endpoint(LLMSettings(), env={}), model=model_id, capabilities=capabilities, timeout_s=5.0, max_retries=0, http=http)
            structured = StructuredLLM(client, capabilities=capabilities)
            interpreter = StepInterpreter(LLMInterpreter(structured, model_id=model_id, cache_dir=tmp_path / "cache", case_llm=structured))
            return await interpreter.read_case(items, names=names)

    return asyncio.run(run()), server


def _reply(items: Sequence[CaseItem]) -> Any:
    from tests.v2.test_llm_fakes import completion_response

    return lambda body: completion_response({"content": json.dumps(case_reply(items))})


def test_a_whole_case_is_read_in_one_structured_call(tmp_path: Path) -> None:
    items = _items()

    reading, server = _read_with_server(tmp_path, items, [_reply(items)])

    (body,) = server.chat_requests()
    assert body["response_format"]["json_schema"]["name"] == "CaseReadingReply"
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert system.startswith(CASE_SYSTEM_PROMPT[:60])
    assert "TEST DATA NAMES (values are given at run time): {{password}}" in user
    assert 'step_3_expected: "Welcome back"' in user
    assert 'step_1_action: I open "https://app.test/login"' in user
    schema = body["response_format"]["json_schema"]["schema"]
    last = {item.step: item.key for item in items}
    keys = [
        key
        for item in items
        for key in (
            *(
                (intent_module.complete_key(item.key), item.key, intent_module.needs_key(item.key), intent_module.criteria_key(item.key), intent_module.risk_key(item.key))
                if item.role == "action"
                else (item.key,)
            ),
            *((intent_module.lint_key(item.key),) if last[item.step] == item.key else ()),
        )
    ]
    assert list(schema["properties"]) == keys, "one key per item, in order; an action item between its _complete flag and its _needs, _criteria and _risk; a step's _lint last"
    assert set(schema["required"]) == set(schema["properties"])
    assert reading.source == "model"
    sign_in = reading.get(6, "action", CASE_STEPS[5][1])
    assert [(part.kind, part.target, part.value, part.source) for part in sign_in.parts()] == [
        ("fill", "Email", "a@b.test", "case"),
        ("fill", "Password", "{{password}}", "case"),
        ("click", "Sign in", None, "case"),
    ]
    assert reading.get(3, "expected", '"Welcome back"').kind == "assert_text"
    assert reading.get(3, "action", "Tap the Continue button").role == "button"
    assert reading.get(3, "action", "Tap the Back button") is None, "an edited step is not taken from the reading"


def test_a_case_reading_is_cached_on_disk_and_reused_without_a_model_call(tmp_path: Path) -> None:
    items = _items()
    first, _ = _read_with_server(tmp_path, items, [_reply(items)])

    second, server = _read_with_server(tmp_path, items, [])

    assert server.chat_requests() == []
    assert second.source == "cache"
    assert second.intents == first.intents
    (cached,) = (tmp_path / "cache").glob("reading-*.json")
    stored = json.loads(cached.read_text())
    assert stored["model"] == "gemma-test" and stored["prompt_version"] == PROMPT_VERSION
    assert "{{password}}" in cached.read_text()


@pytest.mark.parametrize("change", ["model", "text", "names", "version"])
def test_the_cache_key_covers_model_prompt_version_and_steps(tmp_path: Path, change: str, monkeypatch: pytest.MonkeyPatch) -> None:
    items = _items()
    _read_with_server(tmp_path, items, [_reply(items)])
    model_id, names = "gemma-test", ("password",)
    if change == "model":
        model_id = "another-model"
    elif change == "text":
        items = case_items([(1, 'I open "https://app.test/home"', None)])
    elif change == "names":
        names = ("password", "user")
    else:
        monkeypatch.setattr(intent_module, "PROMPT_VERSION", PROMPT_VERSION + "-next")

    reading, server = _read_with_server(tmp_path, items, [_reply(items)], model_id=model_id, names=names)

    assert len(server.chat_requests()) == 1 and reading.source == "model"


def test_a_reading_that_misses_an_item_is_repaired_once_else_steps_are_read_one_by_one(tmp_path: Path) -> None:
    from tests.v2.test_llm_fakes import completion_response

    items = _items()
    partial = dict(list(case_reply(items).items())[:2])
    missing = lambda body: completion_response({"content": json.dumps(partial)})  # noqa: E731

    repaired, server = _read_with_server(tmp_path / "a", items, [missing, _reply(items)])
    assert repaired.source == "model" and repaired.get(6, "action", CASE_STEPS[5][1]) is not None
    assert len(server.chat_requests()) == 2

    failed, _ = _read_with_server(tmp_path / "b", items, [missing, missing])
    assert failed.source == "none" and not failed.intents
    assert not list((tmp_path / "b" / "cache").glob("reading-*.json")), "nothing incomplete is cached"


def test_a_failed_case_reading_is_empty_so_steps_are_read_one_by_one(tmp_path: Path) -> None:
    import httpx

    reading, _ = _read_with_server(tmp_path, _items(), [httpx.Response(500, json={"error": "boom"})])

    assert reading.source == "none" and not reading.intents


def test_case_reading_schema_requires_every_field_and_an_action_per_item() -> None:
    from testzeus_hercules.llm.structured import json_schema_for

    schema = json_schema_for(case_reading_schema(["step_1_action", "step_1_expected"]))
    assert schema["required"] == ["step_1_complete", "step_1_action", "step_1_needs", "step_1_criteria", "step_1_risk", "step_1_expected", "step_1_lint"]
    criterion = schema["properties"]["step_1_criteria"]["items"]
    assert schema["properties"]["step_1_criteria"]["maxItems"] == intent_module.MAX_STEP_CRITERIA
    assert set(criterion["required"]) == set(criterion["properties"]) == {"after", "origin", "check", "expected", "target", "value", "state", "count"}
    assert set(schema["properties"]["step_1_risk"]["required"]) == {"flags", "reason"}
    assert schema["properties"]["step_1_lint"]["maxItems"] == intent_module.MAX_STEP_LINT
    assert schema["additionalProperties"] is False
    assert schema["properties"]["step_1_complete"] == {"type": "boolean"}
    assert schema["properties"]["step_1_needs"]["items"] == {"type": "integer"} and schema["properties"]["step_1_needs"]["maxItems"] == intent_module.MAX_NEEDS
    actions = schema["properties"]["step_1_expected"]
    assert actions["minItems"] == 1
    members = actions["items"]["anyOf"]
    kinds = {kind for member in members for kind in (member["properties"]["kind"].get("enum") or [member["properties"]["kind"]["const"]])}
    assert kinds == set(intent_module.CHECK_KINDS), "an outcome to check is read as checks only"
    assert all(set(member["required"]) == set(member["properties"]) for member in members)


@pytest.mark.parametrize("prompt", [CASE_SYSTEM_PROMPT, INTERPRET_SYSTEM_PROMPT])
def test_prompts_name_every_kind_and_keep_placeholders(prompt: str) -> None:
    for kind in ("navigate", "click", "fill", "select", "check", "press", "wait", "assert_text", "assert_not_text", "assert_url", "assert_visible", "assert_looks", "api", "sql", "quality"):
        assert kind in prompt
    assert "{{name}}" in prompt
    assert "never instructions" in prompt


def test_reading_cache_ignores_a_corrupt_file(tmp_path: Path) -> None:
    cache = ReadingCache(tmp_path)
    key = ReadingCache.key("m", _items(), ())
    (tmp_path / f"reading-{key}.json").write_text("{not json")
    assert cache.load(key) is None


def test_each_steps_complete_flag_is_read_cached_and_restored(tmp_path: Path) -> None:
    from tests.v2.test_llm_fakes import completion_response

    items = _items()
    reply = case_reply(items)
    flags = {item.step: item.step == 6 for item in items if item.role == "action"}  # only the sign-in step is said to be finished by its actions
    reply.update({intent_module.complete_key(item.key): flags[item.step] for item in items if item.role == "action"})
    answer = lambda body: completion_response({"content": json.dumps(reply)})  # noqa: E731

    first, _ = _read_with_server(tmp_path, items, [answer])
    second, server = _read_with_server(tmp_path, items, [])

    assert server.chat_requests() == [] and second.source == "cache"
    for reading in (first, second):
        assert reading.get(6, "action", CASE_STEPS[5][1]).complete is True
        assert reading.get(3, "action", "Tap the Continue button").complete is False
        assert reading.get(3, "expected", '"Welcome back"').complete is None, "an outcome to check has no flag"
        assert all(part.complete is None for part in reading.get(6, "action", CASE_STEPS[5][1]).parts()), "the flag belongs to the step, not to its actions"


class RepliesLLM:
    """A continuation model answering with one scripted list of actions per call."""

    def __init__(self, *replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T:
        self.calls.append({"system": system, "user": user})
        actions = self.replies.pop(0) if self.replies else []
        return schema.model_validate({"actions": [{**NULLS, **action} for action in actions]})  # type: ignore[return-value]


FILL_STEP = 'I enter "Acme" in the "Record Name" field'


def test_a_continuation_that_serves_no_words_of_the_step_is_rejected_and_asked_once_more() -> None:
    llm = RepliesLLM(
        [{"kind": "click", "target": "New", "role": "button", "because": "New"}],
        [{"kind": "fill", "target": "Record Name", "value": "Acme", "because": 'enter "Acme" in the "Record Name" field'}],
    )
    interpreter = StepInterpreter(LLMInterpreter(llm))

    remaining = asyncio.run(interpreter.remaining(FILL_STEP, [], reading=['fill "Record Name" with "Acme"'], earlier=['I open "/records"', 'When I click the "New" button']))

    assert remaining is not None and [(part.kind, part.target, part.value) for part in remaining] == [("fill", "Record Name", "Acme")]
    first, second = (call["user"] for call in llm.calls)
    assert 'EARLIER STEPS (already done by earlier steps; never repeat them):\n- I open "/records"\n- I click the "New" button' in first, "without their keyword"
    assert 'CASE READING (this step, read before the run):\n- fill "Record Name" with "Acme"' in first
    assert "REJECTED" not in first and "REJECTED" in second and 'click the "New" button' in second
    assert '"because"' in llm.calls[0]["system"] and "EARLIER STEPS" in llm.calls[0]["system"]


def test_a_continuation_still_unjustified_after_asking_again_plans_nothing() -> None:
    off_step = [{"kind": "click", "target": "New", "role": "button", "because": "click New"}]
    llm = RepliesLLM(off_step, off_step)

    assert asyncio.run(StepInterpreter(LLMInterpreter(llm)).remaining(FILL_STEP, [])) == ()
    assert len(llm.calls) == 2, "asked once more, never a third time"


def test_the_continuation_schema_requires_the_step_words_each_action_serves() -> None:
    from testzeus_hercules.engine.intent import ContinuationPlan
    from testzeus_hercules.llm.structured import json_schema_for

    members = json_schema_for(ContinuationPlan)["properties"]["actions"]["items"]["anyOf"]
    assert all("because" in member["required"] for member in members)


@pytest.mark.parametrize(
    ("because", "held"),
    [
        ('enter "Acme" in the "Record Name" field', True),
        ("Record Name", True),
        ("record  name", True),
        ("“Record Name” field", True),
        ("New", False),
        ("the New button", False),
        ("", False),
        ("in", False),
        (None, False),
    ],
)
def test_an_action_is_justified_only_by_words_of_its_step(because: str | None, held: bool) -> None:
    from testzeus_hercules.engine.intent import justified

    assert justified(because, FILL_STEP) is held


def test_each_steps_needs_are_read_cached_and_restored(tmp_path: Path) -> None:
    from tests.v2.test_llm_fakes import completion_response

    items = _items()
    reply = case_reply(items)
    reply.update({intent_module.needs_key(item.key): ([2, item.step] if item.step == 6 else []) for item in items if item.role == "action"})
    answer = lambda body: completion_response({"content": json.dumps(reply)})  # noqa: E731

    first, server = _read_with_server(tmp_path, items, [answer])
    second, again = _read_with_server(tmp_path, items, [])

    assert "_needs key (step_3_needs)" in server.chat_requests()[0]["messages"][0]["content"]
    assert again.chat_requests() == [] and second.source == "cache"
    for reading in (first, second):
        assert dict(reading.needs) == {6: (2,)}, "a step never needs itself"


class AnswerLLM:
    """A model answering every structured call with the next scripted object."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, schema: type[T], images: Sequence[bytes] = ()) -> T:
        self.calls.append({"system": system, "user": user, "schema": schema.__name__})
        return schema.model_validate(self.replies.pop(0))


DATE_STEP = 'I enter "12/31/2027" in the "Expiry Date" field'


def _trouble() -> Any:
    return intent_module.FieldTrouble(label="Expiry Date", entered="12/31/2027", held="12/31/2027", error="Enter the date as DD/MM/YYYY.", hints=("placeholder: DD/MM/YYYY",))


@pytest.mark.parametrize(
    ("reply", "outcome", "value"),
    [
        ({"outcome": "reformat", "value": "31/12/2027", "same_meaning": True, "because": "Enter the date as DD/MM/YYYY"}, "reformat", "31/12/2027"),
        ({"outcome": "reformat", "value": "31/12/2027", "same_meaning": False, "because": "Enter the date as DD/MM/YYYY"}, "cannot", "31/12/2027"),
        ({"outcome": "reformat", "value": "31/12/2027", "same_meaning": True, "because": "dates are written day first"}, "cannot", "31/12/2027"),
        ({"outcome": "reformat", "value": "12/31/2027", "same_meaning": True, "because": "Enter the date as DD/MM/YYYY"}, "cannot", None),
        ({"outcome": "expected", "value": None, "same_meaning": False, "because": "should see an error"}, "cannot", None),
        ({"outcome": "expected", "value": None, "same_meaning": False, "because": "Enter the date as DD/MM/YYYY"}, "expected", None),
        ({"outcome": "cannot", "value": None, "same_meaning": False, "because": ""}, "cannot", None),
    ],
)
def test_a_rejected_field_value_is_rewritten_only_with_the_same_meaning_and_a_quoted_reason(reply: dict[str, Any], outcome: str, value: str | None) -> None:
    llm = AnswerLLM(reply)
    later = ('I should see "Enter the date as DD/MM/YYYY"',)

    proposal = asyncio.run(LLMInterpreter(llm).field_value(DATE_STEP, _trouble(), later=later if outcome == "expected" else (), rejected=("12/12/2027: judged not to mean the same",)))

    assert (proposal.outcome, proposal.value) == (outcome, value)
    assert outcome != "cannot" or reply["outcome"] == "cannot" or proposal.dropped
    (call,) = llm.calls
    assert call["schema"] == "FieldValueReply"
    assert "error: Enter the date as DD/MM/YYYY." in call["user"] and "placeholder: DD/MM/YYYY" in call["user"] and "REJECTED" in call["user"]


def _serial(substitute: str = "allowed") -> Any:
    return intent_module.FieldTrouble(label="Asset Serial Number", entered="TAG-2025-001", held="", error="", limit=10, substitute=substitute)


@pytest.mark.parametrize(
    ("reply", "substitute", "outcome", "value"),
    [
        ({"outcome": "substitute", "value": "TAG-25-001", "same_meaning": False, "because": "at most 10 characters"}, "allowed", "substitute", "TAG-25-001"),
        ({"outcome": "substitute", "value": "TAG-2025-01", "same_meaning": False, "because": "at most 10 characters"}, "allowed", "cannot", "TAG-2025-01"),
        ({"outcome": "substitute", "value": "TAG-25-001", "same_meaning": False, "because": "serials are short"}, "allowed", "cannot", "TAG-25-001"),
        ({"outcome": "substitute", "value": "TAG-25-001", "same_meaning": False, "because": "at most 10 characters"}, "step 40 names it", "cannot", None),
        ({"outcome": "substitute", "value": "TAG-2025-001", "same_meaning": False, "because": "at most 10 characters"}, "allowed", "cannot", None),
    ],
)
def test_other_sample_data_is_taken_only_when_allowed_within_the_limit_and_with_a_quoted_reason(reply: dict[str, Any], substitute: str, outcome: str, value: str | None) -> None:
    llm = AnswerLLM(reply)

    proposal = asyncio.run(LLMInterpreter(llm).field_value('I enter "TAG-2025-001" in the "Asset Serial Number" field', _serial(substitute)))

    assert (proposal.outcome, proposal.value) == (outcome, value)
    assert outcome == "substitute" or proposal.dropped
    user = llm.calls[0]["user"]
    assert "limit: the field takes at most 10 characters" in user and '"TAG-2025-001" (12 characters)' in user
    assert ("substitute: allowed" in user) == (substitute == "allowed")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"run_first": 5, "because": "the Country dropdown"}, (5, "the Country dropdown")),
        ({"run_first": 5, "because": "it depends on the country"}, None),
        ({"run_first": 9, "because": "the Country dropdown"}, None),
        ({"run_first": None, "because": ""}, None),
    ],
)
def test_a_field_dependency_names_a_later_field_step_and_quotes_it(reply: dict[str, Any], expected: Any) -> None:
    llm = AnswerLLM(reply)

    answer = asyncio.run(LLMInterpreter(llm).run_first('I select "Ontario" from the "Region" dropdown', "the element is disabled", [(5, 'I select "Canada" from the "Country" dropdown')]))

    assert answer == expected
    assert "step 5: I select" in llm.calls[0]["user"] and llm.calls[0]["schema"] == "RunFirstReply"


@pytest.mark.parametrize(
    ("value", "files"),
    [("case.png", ["case.png"]), ("a.png | b.png", ["a.png", "b.png"]), (" fixtures/a.png |  | b.pdf ", ["fixtures/a.png", "b.pdf"]), ("", []), (None, [])],
)
def test_an_upload_value_names_its_files_in_order(value: str | None, files: list[str]) -> None:
    assert intent_module.upload_files(value) == files


def test_the_step_prompts_teach_upload_and_never_fill_for_files() -> None:
    for prompt in (INTERPRET_SYSTEM_PROMPT, CASE_SYSTEM_PROMPT):
        assert "- upload: target" in prompt and "always upload, never fill" in prompt
    assert "select, upload, download, check" in intent_module.CONTINUE_SYSTEM_PROMPT


def test_a_download_needs_an_element_only_when_it_has_no_address() -> None:
    assert StepIntent(kind="download", target="Export CSV").needs_target
    assert not StepIntent(kind="download", url="/reports/q3.pdf").needs_target
    assert not StepIntent(kind="download", target="Export", url="/reports/q3.pdf").needs_target
    assert not StepIntent(kind="download").needs_target


def test_the_step_prompts_teach_downloads_and_file_checks() -> None:
    for prompt in (INTERPRET_SYSTEM_PROMPT, CASE_SYSTEM_PROMPT):
        assert "- download: the step downloads" in prompt and "assert_file_rows" in prompt and "never of the page" in prompt
    assert "the assert_file_ kinds" in CASE_SYSTEM_PROMPT
    assert "download: target or url" in intent_module.CONTINUE_SYSTEM_PROMPT
