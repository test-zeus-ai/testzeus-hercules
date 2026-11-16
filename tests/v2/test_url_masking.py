"""URL credential masking: the masker, step texts protected with placeholders, the run's redactor and masked decisions."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest

from testzeus_hercules.act.redact import REDACTED, is_credential_parameter, mask_url_credentials, redact_text, rewrite_url_credentials, url_credential_values
from testzeus_hercules.cases.automation import AutomationAction, AutomationStep
from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.decide.contract import ChoiceQuestion, DecisionRequest
from testzeus_hercules.decide.payload import ImagePart
from testzeus_hercules.engine.automation import build_spec, value_template
from testzeus_hercules.engine.data import REDACTED as SECRET_MASK
from testzeus_hercules.engine.data import CaseData, protect_case, protect_text
from testzeus_hercules.engine.masking import MaskingDecider

M = REDACTED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("http://127.0.0.1:8080/login?sid=S1&next=/home", f"http://127.0.0.1:8080/login?sid={M}&next=/home"),
        # encoded values, case-insensitive names, repeated parameters
        ("https://h.test/a?SID=a%2Fb%3D%3D&x=1&sid=again", f"https://h.test/a?SID={M}&x=1&sid={M}"),
        ("https://h.test/s3?X-Amz-Signature=abc&X-Amz-Date=1", f"https://h.test/s3?X-Amz-Signature={M}&X-Amz-Date=1"),
        ("https://h.test/cb?code=4/0Ad&state=xyz", f"https://h.test/cb?code={M}&state=xyz"),
        # fragments (implicit-flow tokens), matrix parameters and userinfo
        ("https://h.test/#access_token=t1&token_type=bearer&id_token=t2", f"https://h.test/#access_token={M}&token_type=bearer&id_token={M}"),
        ("https://h.test/app;jsessionid=J1/x", f"https://h.test/app;jsessionid={M}/x"),
        ("https://ada:pw@h.test/x", "https://h.test/x"),
        # relative addresses and bare query strings in free text
        ("then open /login?sid=S2&next=/home.", f"then open /login?sid={M}&next=/home."),
        ("the hash was #refresh_token=r1&x=2", f"the hash was #refresh_token={M}&x=2"),
        # trailing punctuation and brackets are not part of a URL
        ("(see https://h.test/x?token=t3).", f"(see https://h.test/x?token={M})."),
        # references and masked values are kept; ordinary parameters are kept
        ("https://h.test/x?sid={{url_credential_1}}&page=2", "https://h.test/x?sid={{url_credential_1}}&page=2"),
        ("https://h.test/x?sort_key=name&apikey=k", f"https://h.test/x?sort_key=name&apikey={M}"),
        # text that is not a URL is left alone
        ("key=value and a code 5 and Tom & Jerry=friends", "key=value and a code 5 and Tom & Jerry=friends"),
        ("", ""),
    ],
)
def test_the_masker_masks_credential_parameters_and_keeps_the_rest_readable(text: str, expected: str) -> None:
    assert mask_url_credentials(text) == expected


def test_masking_keeps_json_valid() -> None:
    record = {"action": 'I open "https://h.test/auth/link?sid=a1!B2&next=%2Fhome"', "lines": "a https://h.test/?token=t\nnext"}

    masked = json.loads(mask_url_credentials(json.dumps(record)))

    assert masked == {"action": f'I open "https://h.test/auth/link?sid={M}&next=%2Fhome"', "lines": f"a https://h.test/?token={M}\nnext"}


def test_credential_parameter_names() -> None:
    for name in (
        "sid",
        "SessionID",
        "session",
        "token",
        "access_token",
        "id_token",
        "refresh_token",
        "auth",
        "authorization",
        "code",
        "key",
        "api_key",
        "apikey",
        "password",
        "pwd",
        "secret",
        "signature",
        "sig",
        "X-Amz-Signature",
        "jwt",
    ):
        assert is_credential_parameter(name), name
    for name in ("next", "page", "sort_key", "state", "tab", "q"):
        assert not is_credential_parameter(name), name


def test_credential_values_and_rewriting() -> None:
    text = "open https://h.test/x?sid=a%2Fb&code=1 then https://ada:pw@h.test/ then /r?token=t"

    assert url_credential_values(text) == ["a%2Fb", "a/b", "1", "pw", "t"]
    assert rewrite_url_credentials(text, lambda value: "<" + value + ">") == "open https://h.test/x?sid=<a%2Fb>&code=<1> then https://ada:<pw>@h.test/ then /r?token=<t>"


def test_redact_text_masks_relative_urls_too() -> None:
    assert redact_text("GET /api/items?api_key=K-1 failed") == f"GET /api/items?api_key={M} failed"


# ── step texts protected with placeholders ──────────────────────────────────


def _case(*actions: str, **extra: Any) -> TestCase:
    return TestCase.model_validate({"id": "c", "title": "t", "steps": [{"action": action} for action in actions], **extra})


def test_protect_case_replaces_url_credentials_with_placeholders_and_keeps_their_values() -> None:
    case = _case(
        'I open "https://h.test/login?sid=S-1&next=/home"',
        "I open https://h.test/other?sid=S-1#access_token=T-2",
        "I click Save",
        preconditions=["The user is signed in at https://h.test/?token=T-3"],
    )

    protected, table = protect_case(case)

    assert [step.action for step in protected.steps] == [
        'I open "https://h.test/login?sid={{url_credential_1}}&next=/home"',
        "I open https://h.test/other?sid={{url_credential_1}}#access_token={{url_credential_2}}",
        "I click Save",
    ]
    assert protected.preconditions == ["The user is signed in at https://h.test/?token={{url_credential_3}}"]
    assert table == {"url_credential_1": "S-1", "url_credential_2": "T-2", "url_credential_3": "T-3"}
    assert protected.steps[2] is case.steps[2]
    assert protect_case(_case("I click Save")) == (_case("I click Save"), {})


def test_the_run_data_resolves_placeholders_for_the_browser_and_masks_values_everywhere_else() -> None:
    protected, table = protect_case(_case('I open "https://h.test/login?sid=S-123&x=1"'))
    data = CaseData.from_case(protected, env={}, hidden=table)

    assert data.resolve("https://h.test/login?sid={{url_credential_1}}&x=1") == "https://h.test/login?sid=S-123&x=1"
    assert data.redact("the page said S-123") == f"the page said {SECRET_MASK}"
    assert data.redact("now at https://h.test/home?sid=OTHER&tab=2") == f"now at https://h.test/home?sid={M}&tab=2"
    assert data.redact("https://h.test/login?sid={{url_credential_1}}") == "https://h.test/login?sid={{url_credential_1}}"
    assert data.protect("go to https://h.test/x?sid=S-123 and /y?token=new") == "go to https://h.test/x?sid={{url_credential_1}} and /y?token={{url_credential_2}}"
    assert data.hidden["url_credential_2"] == "new"
    assert value_template("https://h.test/login?sid={{url_credential_1}}", None, data) == ("https://h.test/login?sid={{url_credential_1}}", None)


def test_credentials_in_test_data_and_the_base_url_are_masked_too() -> None:
    case = _case("I open {{link}}", test_data={"link": "https://h.test/x?sid=DATA-1"}, environment={"base_url": "https://h.test/?token=BASE-1"})

    data = CaseData.from_case(case, env={})

    assert data.resolve("{{link}}") == "https://h.test/x?sid=DATA-1"
    assert data.redact("echo DATA-1 and BASE-1") == f"echo {SECRET_MASK} and {SECRET_MASK}"


def test_protect_text_numbers_distinct_values_once() -> None:
    table: dict[str, str] = {}

    first = protect_text("/a?sid=one&token=two", table)
    second = protect_text("/b?sid=two", table)

    assert first == "/a?sid={{url_credential_1}}&token={{url_credential_2}}" and second == "/b?sid={{url_credential_2}}"


def test_an_exported_run_reads_a_url_credential_from_the_environment() -> None:
    case, _ = protect_case(_case('I open "https://h.test/login?sid=S-1"'))
    step = AutomationStep(index=1, action=case.steps[0].action, status="passed", actions=[AutomationAction(kind="navigate", url="https://h.test/login?sid={{url_credential_1}}")])

    spec = build_spec(case, [step], browser=True)

    assert spec.data["url_credential_1"].kind == "secret" and spec.data["url_credential_1"].env == "URL_CREDENTIAL_1"


# ── what deciders and the event stream see ──────────────────────────────────


class _Recorder:
    engine = "recorder"

    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest, *, purpose: str) -> Any:
        self.requests.append(request)
        return "decided"


def test_decision_requests_leave_with_url_credentials_masked() -> None:
    data = CaseData.from_case(_case("x"), env={}, hidden={"url_credential_1": "S-9"})
    inner = _Recorder()
    decider = MaskingDecider(inner, data.redact)
    image = ImagePart.from_bytes(b"\xff\xd8\xff\xd9", "image/jpeg")
    request = DecisionRequest(
        state={"page": {"url": "https://h.test/home?sid=S-9&tab=1"}, "rows": [{"name": "Open https://h.test/r?token=T-1"}], "shot": image},
        questions={"target": ChoiceQuestion(instructions="Pick one.", criteria={"md_1": 'link "S-9 dashboard"', "no_match": "none"})},
    )

    assert asyncio.run(decider.decide(request, purpose="target")) == "decided"

    (sent,) = inner.requests
    wire = json.dumps(sent.to_wire())
    assert "S-9" not in wire and "T-1" not in wire
    assert sent.state["page"]["url"] == f"https://h.test/home?sid={SECRET_MASK}&tab=1", "a value the run knows is masked as a secret"
    assert sent.state["rows"][0]["name"] == f"Open https://h.test/r?token={M}", "any other credential is masked by name"
    assert sent.state["shot"] == request.state["shot"], "images are not touched"
    assert decider.engine == "recorder", "the wrapped decider's attributes show through"
    plain = DecisionRequest(state={"task": "nothing secret"}, questions=request.questions | {"target": ChoiceQuestion(instructions="Pick.", criteria={"md_1": "Save"})})
    assert decider.mask(plain) is plain


def test_worker_events_are_masked() -> None:
    from testzeus_hercules.cli.worker import JsonLinesSink

    stream = io.StringIO()
    JsonLinesSink(stream).emit("step_started", step=1, action='I open "https://h.test/x?sid=S-7&a=1"')

    event = json.loads(stream.getvalue())
    assert event["data"]["action"] == f'I open "https://h.test/x?sid={M}&a=1"'


def test_a_case_written_with_a_url_placeholder_is_still_checked_like_any_other() -> None:
    """Placeholders exist only inside a run: a case file naming one that is not in its test data is refused."""
    with pytest.raises(ValueError, match="unknown placeholder"):
        _case('I open "https://h.test/login?sid={{url_credential_1}}"')


# ── the daemon's copy of a run's case ───────────────────────────────────────


def test_the_daemon_keeps_a_masked_case_in_the_run_directory_and_gives_the_worker_the_real_one(tmp_path: Any) -> None:
    from pathlib import Path

    from tests.v2.fixtures.daemon_support import case_dict, client_for, make_config, wait_for

    config = make_config(tmp_path)
    case = case_dict()
    case["steps"][0]["action"] = 'I open "https://h.test/login?sid=S-DAEMON&next=/home"'
    with client_for(config) as client:
        response = client.post("/api/runs", json={"test_case": case})
        run_id = response.json()["run_id"]
        body = wait_for(client, run_id)
        served = client.get(f"/api/runs/{run_id}/case").json()

    run_dir = config.settings.project_dir / ".hercules" / "runs" / run_id
    assert body["status"] == "passed"
    kept = (run_dir / "case.json").read_text()
    assert "S-DAEMON" not in kept and f"sid={M}&next=/home" in kept
    seen = json.loads((run_dir / "case_input.json").read_text())
    assert seen["masked"] is False, "the worker ran the real case"
    assert run_dir not in Path(seen["path"]).parents and not Path(seen["path"]).exists(), "read from outside the run directory, removed after the run"
    assert served["test_case"]["steps"][0]["action"] == f'I open "https://h.test/login?sid={M}&next=/home"'
