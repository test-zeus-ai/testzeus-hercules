"""Offline tests for test plan generation, Gherkin compilation, and coverage."""

import asyncio
import json
import os
import re
from types import SimpleNamespace

import pytest
from testzeus_hercules.intelligence.coverage import (
    coverage_matrix,
    filter_plan_by_test_ids,
    format_coverage_markdown,
)
from testzeus_hercules.intelligence.normalized_prd import NormalizedPRD, PRDRequirement
from testzeus_hercules.intelligence.test_plan import (
    PlanStep,
    TestCase,
    TestPlan,
    compile_plan_to_gherkin,
    generate_test_plan,
    plan_from_json,
    plan_to_json,
)
from testzeus_hercules.utils.gherkin_helper import split_feature_file


class FakeLLM:
    """Offline LLM double: pops scripted responses in order, repeating the last one."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._last = responses[-1] if responses else ""
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        content = self._responses.pop(0) if self._responses else self._last
        return SimpleNamespace(content=content)


def _make_prd(n_requirements: int = 2) -> NormalizedPRD:
    requirements = [
        PRDRequirement(
            id=f"REQ-{index:03d}",
            title=f"Requirement {index}",
            description=f"Description {index}",
            acceptance_criteria=[f"criterion {index}a", f"criterion {index}b"],
            source=f"chunk {index}",
        )
        for index in range(1, n_requirements + 1)
    ]
    return NormalizedPRD(project_name="Widget Portal", requirements=requirements, source_metadata={"chunk_count": 1})


def _case(requirement_id: str, title: str, category: str = "functional", steps: list | None = None, **extra) -> dict:
    if steps is None:
        steps = [{"type": "action", "description": "open the page"}, {"type": "assertion", "description": "page loads"}]
    return {"requirement_id": requirement_id, "title": title, "category": category, "steps": steps, **extra}


# --- generate_test_plan ------------------------------------------------------------


def test_generate_test_plan_batched_maps_cases_to_requirements() -> None:
    prd = _make_prd(2)
    batch = json.dumps(
        [
            _case(
                "REQ-001",
                "Login happy path",
                priority="high",
                tags=["frontend"],
                steps=[
                    {"type": "action", "description": "open login page"},
                    {"type": "action", "description": "enter valid credentials"},
                    {"type": "assertion", "description": "dashboard visible"},
                ],
            ),
            _case("REQ-001", "Login blocked with bad password", category="error_handling", data_refs=["bad_password"]),
            _case("REQ-002", "Password reset flow"),
            _case("REQ-999", "orphan case for unknown requirement"),
            _case("REQ-002", "case without valid steps", steps=[]),
            "not-an-object",
        ]
    )
    llm = FakeLLM([batch])

    plan = asyncio.run(generate_test_plan(prd, llm=llm))

    assert len(llm.prompts) == 1  # 2 requirements -> single batched prompt
    assert [case.id for case in plan.test_cases] == ["TC001", "TC002", "TC003"]
    assert [case.title for case in plan.test_cases] == ["Login happy path", "Login blocked with bad password", "Password reset flow"]
    assert plan.test_cases[0].category == "functional"
    assert plan.test_cases[1].category == "error_handling"
    assert plan.test_cases[0].requirement_ids == ["REQ-001"]
    assert plan.meta["project"] == "Widget Portal"
    assert plan.meta["generated_by"] == "hercules"
    assert plan.requirements[0] == {"id": "REQ-001", "description": "Description 1", "source": "chunk 1", "test_ids": ["TC001", "TC002"]}
    assert plan.requirements[1]["test_ids"] == ["TC003"]


def test_generate_test_plan_per_requirement_skips_bad_response() -> None:
    prd = _make_prd(4)  # >3 requirements -> one prompt per requirement
    responses = [
        json.dumps([_case("REQ-001", "Case for one")]),
        json.dumps([_case("REQ-002", "Case for two")]),
        "<<<totally not json>>>",
        json.dumps([_case("REQ-004", "Case for four")]),
    ]
    llm = FakeLLM(responses)

    plan = asyncio.run(generate_test_plan(prd, llm=llm))

    assert len(llm.prompts) == 4
    assert "REQ-003" in llm.prompts[2]
    assert [case.id for case in plan.test_cases] == ["TC001", "TC002", "TC003"]
    assert [case.title for case in plan.test_cases] == ["Case for one", "Case for two", "Case for four"]
    test_ids_by_requirement = {entry["id"]: entry["test_ids"] for entry in plan.requirements}
    assert test_ids_by_requirement["REQ-003"] == []
    assert test_ids_by_requirement["REQ-004"] == ["TC003"]


def test_generate_test_plan_zero_cases_raises() -> None:
    prd = _make_prd(1)
    llm = FakeLLM(["absolute garbage, no JSON here"])

    with pytest.raises(ValueError, match="No test cases could be generated"):
        asyncio.run(generate_test_plan(prd, llm=llm))


def test_generate_test_plan_filters_by_include_categories() -> None:
    prd = _make_prd(1)
    batch = json.dumps(
        [
            _case("REQ-001", "Functional case"),
            _case("REQ-001", "Security scan", category="security"),
        ]
    )
    llm = FakeLLM([batch])

    plan = asyncio.run(generate_test_plan(prd, llm=llm, include_categories=["functional"]))

    assert [case.title for case in plan.test_cases] == ["Functional case"]
    assert plan.requirements[0]["test_ids"] == ["TC001"]


# --- compile_plan_to_gherkin --------------------------------------------------------


def _make_plan() -> TestPlan:
    cases = [
        TestCase(
            id="TC001",
            title="Login happy path",
            steps=[
                PlanStep(type="action", description="open login page"),
                PlanStep(type="action", description="enter valid credentials"),
                PlanStep(type="assertion", description="dashboard visible"),
            ],
        ),
        TestCase(
            id="TC002",
            title="Logout works",
            data_refs=["session_type"],
            steps=[PlanStep(type="assertion", description="user is logged out")],
        ),
        TestCase(
            id="TC003",
            title="Login happy path",  # duplicate title -> _2 suffix
            steps=[PlanStep(type="action", description="retry login | slowly")],
        ),
    ]
    return TestPlan(
        meta={"project": "Widget Portal", "generated_by": "hercules"},
        requirements=[{"id": "REQ-001", "description": "d", "source": "s", "test_ids": ["TC001", "TC003"]}],
        test_cases=cases,
    )


def test_compile_plan_to_gherkin_basic_structure() -> None:
    gherkin = compile_plan_to_gherkin(_make_plan())

    assert gherkin.startswith("Feature: Hercules Test Plan - Widget Portal\n")
    assert len(re.findall(r"^Scenario(?::| Outline:)", gherkin, re.MULTILINE)) == 3
    assert "Scenario: TC001 Login happy path" in gherkin
    assert "Given open login page" in gherkin
    assert "When enter valid credentials" in gherkin
    assert "Then dashboard visible" in gherkin
    # first step is an assertion -> Then, no And keyword is ever emitted
    assert "Then user is logged out" in gherkin
    assert "\nAnd " not in gherkin
    assert "\nBut " not in gherkin
    # duplicate titles are disambiguated
    assert "Scenario: TC003 Login happy path_2" in gherkin
    # pipes are escaped from step text
    assert "Given retry login / slowly" in gherkin


def test_compile_plan_to_gherkin_data_refs_become_outline_and_examples() -> None:
    gherkin = compile_plan_to_gherkin(_make_plan())

    assert "Scenario Outline: TC002 Logout works" in gherkin
    assert "  Examples:" in gherkin
    assert "    | session_type |" in gherkin
    assert "    | <session_type> |" in gherkin


def test_compile_plan_to_gherkin_custom_title() -> None:
    gherkin = compile_plan_to_gherkin(_make_plan(), feature_title="Custom | Feature")
    assert gherkin.startswith("Feature: Custom / Feature\n")


def test_compile_plan_to_gherkin_whitespace_only_data_refs_stay_plain_scenario(tmp_path) -> None:
    """Refs that sanitize to nothing must not produce a dangling Scenario Outline (F6)."""
    plan = TestPlan(
        meta={"project": "Widget Portal"},
        requirements=[],
        test_cases=[
            TestCase(id="TC001", title="Whitespace refs", data_refs=["  ", ""], steps=[PlanStep(type="action", description="do thing")]),
        ],
    )

    gherkin = compile_plan_to_gherkin(plan)

    assert "Scenario: TC001 Whitespace refs" in gherkin
    assert "Scenario Outline" not in gherkin
    assert "Examples:" not in gherkin

    input_file = tmp_path / "ws.feature"
    input_file.write_text(gherkin, encoding="utf-8")
    scenarios = asyncio.run(split_feature_file(str(input_file), str(tmp_path / "ws_split")))
    assert [entry["scenario"] for entry in scenarios] == ["TC001 Whitespace refs"]


def test_compile_plan_to_gherkin_sanitizes_case_id() -> None:
    """A case id with embedded newlines must not inject extra Gherkin lines (F6)."""
    plan = TestPlan(
        meta={"project": "Widget Portal"},
        requirements=[],
        test_cases=[
            TestCase(id="TC001\nInjected Scenario: evil", title="t", steps=[PlanStep(type="action", description="a")]),
        ],
    )

    gherkin = compile_plan_to_gherkin(plan)

    assert "\nInjected" not in gherkin
    assert "Scenario: TC001 Injected Scenario: evil t" in gherkin


def test_compiled_plan_is_parseable_by_split_feature_file(tmp_path) -> None:
    plan = _make_plan()
    gherkin = compile_plan_to_gherkin(plan)
    input_file = tmp_path / "plan.feature"
    input_file.write_text(gherkin, encoding="utf-8")

    scenarios = asyncio.run(split_feature_file(str(input_file), str(tmp_path / "split")))

    # Expected scenario names mirror the compiler's title dedupe (second "Login happy path" gets _2).
    expected_names = []
    seen_titles: dict[str, int] = {}
    for case in plan.test_cases:
        title = case.title
        if title in seen_titles:
            seen_titles[title] += 1
            title = f"{title}_{seen_titles[title]}"
        else:
            seen_titles[title] = 1
        expected_names.append(f"{case.id} {title}")

    assert len(scenarios) == len(plan.test_cases)
    for entry, expected_name in zip(scenarios, expected_names):
        assert entry["feature"] == "Hercules Test Plan - Widget Portal"
        assert entry["scenario"] == expected_name
        assert os.path.exists(entry["output_file"])


# --- coverage -----------------------------------------------------------------------


def _plan_for_coverage() -> TestPlan:
    return TestPlan(
        meta={"project": "Widget Portal"},
        requirements=[
            {"id": "REQ-001", "description": "first", "source": "chunk 1", "test_ids": ["TC001", "TC002"]},
            {"id": "REQ-002", "description": "second", "source": "chunk 1", "test_ids": ["TC002"]},
            {"id": "REQ-003", "description": "third", "source": "chunk 2", "test_ids": []},
        ],
        test_cases=[
            TestCase(id="TC001", title="t1", steps=[PlanStep(type="action", description="a")]),
            TestCase(id="TC002", title="t2", steps=[PlanStep(type="action", description="a")]),
        ],
    )


def test_coverage_matrix_math() -> None:
    matrix = coverage_matrix(_plan_for_coverage())

    assert matrix["totals"] == {"requirements_total": 3, "requirements_covered": 2, "coverage_percent": 66.67}
    by_id = {row["id"]: row for row in matrix["requirements"]}
    assert by_id["REQ-001"]["covered"] is True
    assert by_id["REQ-001"]["test_ids"] == ["TC001", "TC002"]
    assert by_id["REQ-003"]["covered"] is False

    empty = coverage_matrix(TestPlan())
    assert empty["totals"] == {"requirements_total": 0, "requirements_covered": 0, "coverage_percent": 0}


def test_format_coverage_markdown() -> None:
    markdown = format_coverage_markdown(_plan_for_coverage())

    assert markdown.startswith("## Coverage")
    assert "| Requirement | Description | Test Cases | Covered |" in markdown
    assert "| REQ-001 | first | TC001, TC002 | Yes |" in markdown
    assert "| REQ-003 | third | - | No |" in markdown
    assert "66.67% of requirements covered (2/3)" in markdown

    empty_markdown = format_coverage_markdown(TestPlan())
    assert "0% of requirements covered (0/0)" in empty_markdown


def test_coverage_matrix_handles_string_ids_and_case_folding() -> None:
    """String test_ids are split, matched case-insensitively, and normalized (F7)."""
    plan = TestPlan(
        meta={"project": "Widget Portal"},
        requirements=[
            {"id": "REQ-001", "description": "first", "source": "s", "test_ids": "TC001 TC002"},
            {"id": "REQ-002", "description": "second", "source": "s", "test_ids": ["tc002", "TC999"]},
        ],
        test_cases=[
            TestCase(id="TC001", title="t1", steps=[PlanStep(type="action", description="a")]),
            TestCase(id="TC002", title="t2", steps=[PlanStep(type="action", description="a")]),
        ],
    )

    matrix = coverage_matrix(plan)

    by_id = {row["id"]: row for row in matrix["requirements"]}
    assert by_id["REQ-001"]["test_ids"] == ["TC001", "TC002"]
    assert by_id["REQ-001"]["covered"] is True
    # lowercase match is normalized to the canonical case id; dangling TC999 is dropped
    assert by_id["REQ-002"]["test_ids"] == ["TC002"]
    assert by_id["REQ-002"]["covered"] is True
    assert matrix["totals"] == {"requirements_total": 2, "requirements_covered": 2, "coverage_percent": 100}


def test_format_coverage_markdown_escapes_pipes() -> None:
    plan = TestPlan(
        meta={"project": "Widget Portal"},
        requirements=[{"id": "REQ-001", "description": "costs | profit", "source": "s", "test_ids": ["TC001"]}],
        test_cases=[TestCase(id="TC001", title="t", steps=[PlanStep(type="action", description="a")])],
    )

    markdown = format_coverage_markdown(plan)

    assert "| REQ-001 | costs \\| profit | TC001 | Yes |" in markdown


def test_filter_plan_by_test_ids() -> None:
    plan = _plan_for_coverage()

    filtered = filter_plan_by_test_ids(plan, ["TC001", "TC003"])

    # TC002 is dropped, so REQ-002 (only covered by TC002) disappears too.
    assert [case.id for case in filtered.test_cases] == ["TC001"]
    assert [entry["id"] for entry in filtered.requirements] == ["REQ-001"]
    assert filtered.requirements[0]["test_ids"] == ["TC001"]
    assert filtered.meta == plan.meta
    # the original plan is untouched
    assert len(plan.test_cases) == 2
    assert len(plan.requirements) == 3


def test_filter_plan_by_test_ids_is_case_insensitive() -> None:
    filtered = filter_plan_by_test_ids(_plan_for_coverage(), ["tc002"])
    assert [case.id for case in filtered.test_cases] == ["TC002"]
    assert [entry["id"] for entry in filtered.requirements] == ["REQ-001", "REQ-002"]


def test_filter_plan_by_test_ids_accepts_a_string() -> None:
    # A bare string must not be iterated char-by-char ("T", "C", "0", ...).
    filtered = filter_plan_by_test_ids(_plan_for_coverage(), "TC001 TC002")
    assert [case.id for case in filtered.test_cases] == ["TC001", "TC002"]
    assert filter_plan_by_test_ids(_plan_for_coverage(), "TC001").test_cases[0].id == "TC001"


# --- json helpers -------------------------------------------------------------------


def test_plan_json_roundtrip(tmp_path) -> None:
    plan = _make_plan()
    path = str(tmp_path / "plan.json")

    plan_to_json(plan, path)
    loaded = plan_from_json(path)

    assert loaded.model_dump() == plan.model_dump()
