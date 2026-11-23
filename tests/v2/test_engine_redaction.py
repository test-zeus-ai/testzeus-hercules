"""Evidence redaction: secrets are masked value by value before JSON is encoded, so short secrets do not break numbers
and secrets JSON escapes (quotes, backslashes, newlines) do not leak."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from testzeus_hercules.cases.models import RunResult, SecretRef, Step, StepResult, TestCase
from testzeus_hercules.engine.data import REDACTED, CaseData
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.masking import masked_json


def _data(secret: str) -> CaseData:
    case = TestCase(id="c", title="t", steps=[Step(action="log in")], test_data={"pw": SecretRef(secret="APP_PW")})
    return CaseData.from_case(case, env={"APP_PW": secret})


def _run(action: str) -> RunResult:
    return RunResult(run_id="r_1", status="failed", case_id="c", duration_s=1230.0, steps=[StepResult(index=1, action=action, status="failed", duration_ms=1230)])


@pytest.mark.parametrize("secret", ["123", 'a"b\\c', "line one\nline two", "pa'ss\tword\\n"])
def test_run_json_stays_valid_and_masks_the_secret(tmp_path: Path, secret: str) -> None:
    data = _data(secret)
    store = EvidenceStore(tmp_path, "r_1", redact=data.redact)
    path = store.write_run(_run(f"typed {secret} into the field"))
    text = path.read_text(encoding="utf-8")
    loaded = RunResult.model_validate_json(text)  # still valid JSON, numbers intact
    assert loaded.duration_s == 1230.0 and loaded.steps[0].duration_ms == 1230
    assert secret not in loaded.steps[0].action and REDACTED in loaded.steps[0].action
    if len(secret) > 3:
        assert json.dumps(secret)[1:-1] not in text


@pytest.mark.parametrize("secret", ['a"b\\c', "line one\nline two"])
def test_records_mask_escaped_secrets(tmp_path: Path, secret: str) -> None:
    data = _data(secret)
    store = EvidenceStore(tmp_path, "r_1", redact=data.redact)
    store.append_decision(1, {"value": secret, "nested": [{"note": f"x {secret} y"}], secret: 1})
    store.save_json(1, "http.json", {"body": secret})
    for name in ("decisions.jsonl", "http.json"):
        text = (tmp_path / "steps" / "1" / name).read_text(encoding="utf-8")
        assert secret not in json.loads(text.splitlines()[0] if name.endswith("l") else text).__repr__()
        assert json.dumps(secret)[1:-1] not in text


def test_a_short_secret_is_masked_only_where_it_stands_alone() -> None:
    data = _data("123")
    assert data.redact("123") == REDACTED
    assert data.redact("pin 123, again") == f"pin {REDACTED}, again"
    assert data.redact("took 1230 ms, order 41234") == "took 1230 ms, order 41234"
    assert _data("longsecret1").redact("xxlongsecret1yy") == f"xx{REDACTED}yy"


def test_masked_json_keeps_numbers_and_models() -> None:
    data = _data("123")
    text = masked_json({"n": 1230, "s": "123", "m": "a 123 b"}, data.redact, sort_keys=True)
    assert json.loads(text) == {"m": f"a {REDACTED} b", "n": 1230, "s": REDACTED}
