"""Body redaction fails closed: bodies that are not one JSON document, and JSON deeper than the walk, never keep a secret."""

from __future__ import annotations

import json
import time

import pytest

from testzeus_hercules.act.redact import BODY_MAX_DEPTH, BODY_MAX_NODES, REDACTED, redact_body_text, redact_tree, redact_value

SECRET = "hunter2-fake-secret"


@pytest.mark.parametrize(
    "body",
    [
        f'{{"password":"{SECRET}"}}\n{{"user":"a","api_key":"{SECRET}"}}\n',  # NDJSON
        f'{{"user": "a", "password": "{SECRET}", "items": [1, 2',  # truncated
        f'{{"token": "{SECRET}", }}',  # malformed
        f'[{{"secret": "{SECRET}"}}',  # truncated array
        f'{{"pin": 1, "access_token": {SECRET}',  # unquoted value
    ],
)
def test_a_body_that_is_not_one_json_document_is_still_redacted(body: str) -> None:
    shown = redact_body_text(body, "application/json")
    assert SECRET not in shown and REDACTED in shown


def test_ndjson_lines_are_redacted_one_by_one_and_stay_json() -> None:
    shown = redact_body_text(f'{{"password":"{SECRET}","n":1}}\n{{"n":2}}', "application/x-ndjson")
    first, second = (json.loads(line) for line in shown.splitlines())
    assert first == {"password": REDACTED, "n": 1} and second == {"n": 2}


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (f"user=a&password={SECRET}", "text/plain"),
        (f"<login><user>a</user><password>{SECRET}</password></login>", "application/xml"),
        (f"client_secret={SECRET}; other=1", ""),
    ],
)
def test_text_bodies_lose_secret_fields_by_pattern(body: str, content_type: str) -> None:
    assert SECRET not in redact_body_text(body, content_type)


def test_json_deeper_than_the_walk_is_masked_not_kept() -> None:
    deep: object = {"password": SECRET}
    for _ in range(BODY_MAX_DEPTH + 3):
        deep = {"level": deep}
    assert SECRET not in json.dumps(redact_tree(deep))
    assert SECRET not in redact_body_text(json.dumps(deep), "application/json")


def test_json_past_the_node_budget_is_masked_not_kept() -> None:
    wide = [{"a": 1} for _ in range(BODY_MAX_NODES)] + [{"note": {"password": SECRET}}]
    assert SECRET not in json.dumps(redact_tree(wide))


def test_redact_value_walks_nested_dicts_from_the_top() -> None:
    value = {"a": {"b": {"c": {"password": SECRET}}}, "token": SECRET}
    shown = redact_value(value)
    assert SECRET not in json.dumps(shown)
    assert shown["a"]["b"]["c"]["password"] == REDACTED


def test_ordinary_json_is_unchanged() -> None:
    body = {"name": "Bella", "tags": ["a", "b"], "nested": {"age": 2}}
    assert json.loads(redact_body_text(json.dumps(body), "application/json")) == body


def test_pattern_redaction_stays_fast_on_hostile_input() -> None:
    started = time.monotonic()
    redact_body_text("{" + "a." * 200_000 + '"x', "")
    redact_body_text('{"' + "p" * 200_000, "")
    assert time.monotonic() - started < 5
