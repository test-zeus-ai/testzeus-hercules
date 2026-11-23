"""Exporters with hostile text: page- and model-derived strings never become code in the exported test.

Titles, observed page text, notes, step wording and ids can hold newlines, U+2028/U+2029 (which end a
JavaScript ``//`` comment), quotes, backslashes, ``${}`` and ``*/``. Each must stay inside a comment,
a docstring or a string literal.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationData, AutomationSpec, AutomationStep, LocatorSpec, TargetSpec
from testzeus_hercules.cases.models import RunResult, StepResult
from testzeus_hercules.export import export_run
from testzeus_hercules.export.common import one_line

from tests.v2.export_harness import UI_TYPESCRIPT, node, typescript_syntax_errors

PAYLOAD = "os.system('pwned')"
HOSTILE = [
    f"line1\nimport os; {PAYLOAD}",
    f"line1\r{PAYLOAD}",
    f"line1 {PAYLOAD}",
    f"line1 {PAYLOAD}",
    f"line1\x85{PAYLOAD}",
    f'say "hi"',
    f'end """ {PAYLOAD} """',
    "back\\slash\\",
    f"${{{PAYLOAD}}}",
    f"*/ {PAYLOAD} /*",
    f"nul\x00{PAYLOAD}",
]


def hostile_spec(text: str) -> AutomationSpec:
    steps = [
        AutomationStep(
            index=1,
            action=f"I open the page {text}",
            status="passed",
            notes=[f"note {text}"],
            actions=[
                AutomationAction(kind="navigate", url="/start"),
                AutomationAction(kind="click", target=TargetSpec(role="button", name=f"Go {text}", locator=None)),
                AutomationAction(kind="fill", value=f"typed {text}", target=TargetSpec(role="textbox", name="Name", locator=LocatorSpec(strategy="label", value=f"Name {text}"))),
            ],
            checks=[
                AutomationCheck(kind="text", expected=f"Welcome {text}", form="text"),
                AutomationCheck(kind="not_text", expected=f"Gone {text}", form=None, faithful=False, note=f"no form held {text}"),
                AutomationCheck(kind="semantic", expected=f"confirmed {text}", source="llm", observed=f"page {text}", faithful=False, note=f"judged {text}; write an assertion"),
            ],
            faithful=False,
        ),
        AutomationStep(index=2, action="Call", status="passed", kind="api", api={"path": "/pets", "expect": {"status": 200}}),
        AutomationStep(index=3, action="Time it", status="passed", kind="api", api={"expect": {"status": 200, "max_time_ms": 500}}),
    ]
    return AutomationSpec(case_id=f"../case/{text}", title=f"Title {text}", base_url="http://shop.test", data={"x": AutomationData(kind="value", value="1")}, steps=steps)


def run(text: str, run_id: str = "r_1") -> RunResult:
    spec = hostile_spec(text)
    return RunResult(run_id=run_id, status="passed", case_id="hostile", steps=[StepResult(index=s.index, action=s.action, status="passed") for s in spec.steps], automation=spec)  # type: ignore[arg-type]


def python_code_names(code: str) -> list[str]:
    """Every NAME token outside comments, strings and docstrings."""
    return [token.string for token in tokenize.generate_tokens(io.StringIO(code).readline) if token.type == tokenize.NAME]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_stays_out_of_the_python_code(text: str) -> None:
    exported = export_run(run(text, run_id='r_1"""\nimport os'), "playwright-py")
    tree = ast.parse(exported.code)
    assert "system" not in python_code_names(exported.code), "page text became Python code"
    assert " " not in exported.code and " " not in exported.code and "\x00" not in exported.code
    exported.code.encode("utf-8")
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    assert "Title" in (ast.get_docstring(function) or "")


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_stays_in_typescript_comments_and_strings(text: str) -> None:
    exported = export_run(run(text), "playwright-ts")
    assert " " not in exported.code and " " not in exported.code and "\x00" not in exported.code
    for line in exported.code.splitlines():
        if PAYLOAD in line:
            stripped = line.strip()
            assert stripped.startswith("//") or '"' in stripped.split(PAYLOAD)[0], f"payload outside a comment or string: {line!r}"


@pytest.mark.skipif(node() is None or not UI_TYPESCRIPT.is_dir(), reason="node and ui/node_modules/typescript are needed to parse TypeScript")
def test_hostile_typescript_parses(tmp_path: Path) -> None:
    for index, text in enumerate(HOSTILE):
        assert typescript_syntax_errors(export_run(run(text), "playwright-ts").code, tmp_path / str(index)) == [], text


def test_a_hostile_case_id_is_never_a_path_and_ids_are_sanitised_in_headers() -> None:
    for fmt in ("spec", "playwright-ts", "playwright-py"):
        exported = export_run(run("x/../../etc"), fmt)  # type: ignore[arg-type]
        assert ".." not in exported.filename and exported.filename.count("/") <= 1
    code = export_run(run("x", run_id="r_1\nimport os"), "playwright-ts").code
    assert "from run r_1_import_os " in code


def test_a_response_time_check_without_a_request_of_its_own_is_a_todo() -> None:
    for fmt, marker in (("playwright-py", "elapsed_ms"), ("playwright-ts", "elapsed")):
        exported = export_run(run("plain"), fmt)  # type: ignore[arg-type]
        assert marker not in exported.code, "elapsed is only defined after a request in the same step"
        assert any("response time is under 500 ms" in note for note in exported.notes)


def test_one_line_removes_every_line_break_and_control_character() -> None:
    assert one_line("a\nb\rc d e\x85f\x0bg\x0ch\x00i\ud800j") == "a b c d e f g h i j"


def test_automation_data_names_are_identifiers_like_case_data_keys() -> None:
    from pydantic import ValidationError

    from testzeus_hercules.cases import automation
    from testzeus_hercules.cases.models import DATA_KEY_PATTERN

    assert automation._DATA_KEY == DATA_KEY_PATTERN
    with pytest.raises(ValidationError):
        automation.AutomationSpec.model_validate({"case_id": "c", "title": "t", "data": {"x: 1, y": {"kind": "value", "value": "v"}}})
