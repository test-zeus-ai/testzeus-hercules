"""Test plan generation and deterministic Gherkin compilation.

Turns a :class:`NormalizedPRD` into a structured :class:`TestPlan` via an LLM,
then compiles the plan to a Gherkin feature file without any LLM involvement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field
from testzeus_hercules.intelligence.normalized_prd import NormalizedPRD
from testzeus_hercules.intelligence.prd_parser import parse_json_array
from testzeus_hercules.utils.logger import logger

PlanStepType = Literal["action", "assertion"]
PlanCategory = Literal["functional", "error_handling", "security", "ui_ux", "performance"]
PlanPriority = Literal["high", "medium", "low"]

VALID_CATEGORIES: tuple[str, ...] = ("functional", "error_handling", "security", "ui_ux", "performance")
VALID_PRIORITIES: tuple[str, ...] = ("high", "medium", "low")


class PlanStep(BaseModel):
    """A single step inside a test case: either an action or an assertion."""

    type: PlanStepType
    description: str


class TestCase(BaseModel):
    """One test case of a test plan."""

    __test__: ClassVar[bool] = False  # not a pytest test class despite the Test prefix

    id: str
    title: str
    description: str = ""
    category: PlanCategory = "functional"
    priority: PlanPriority = "medium"
    tags: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=1)
    data_refs: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class TestPlan(BaseModel):
    """A complete test plan with metadata, requirement mapping and test cases."""

    __test__: ClassVar[bool] = False  # not a pytest test class despite the Test prefix

    meta: dict[str, Any] = Field(default_factory=dict)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)


def plan_to_json(plan: TestPlan, path: str) -> None:
    """Serialize a :class:`TestPlan` to a JSON file."""
    Path(path).write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def plan_from_json(path: str) -> TestPlan:
    """Load a :class:`TestPlan` from a JSON file produced by :func:`plan_to_json`."""
    return TestPlan.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _default_llm() -> Any:
    from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model

    return get_litellm_chat_model("planner_agent")


def _format_requirement(requirement: Any) -> str:
    criteria = "\n".join(f"  - {criterion}" for criterion in requirement.acceptance_criteria)
    criteria_block = f"\nAcceptance criteria:\n{criteria}" if criteria else ""
    description = requirement.description or requirement.title
    return f"{requirement.id} - {requirement.title}\nDescription: {description}{criteria_block}"


_SINGLE_REQUIREMENT_PROMPT = """You are a senior QA architect designing test cases for a product requirement.

Requirement under test:
{requirement}

Return ONLY a pure JSON array (no prose, no markdown fences) of test case objects:
[
  {{
    "title": "short test case title",
    "description": "what this test case verifies",
    "category": "functional" | "error_handling" | "security" | "ui_ux" | "performance",
    "priority": "high" | "medium" | "low",
    "tags": ["frontend" and/or "backend"],
    "steps": [{{"type": "action" | "assertion", "description": "concrete UI or API step"}}],
    "data_refs": ["names of data variants this case is parameterized over, e.g. valid_user, invalid_user"]
  }}
]

Rules:
- Cover the happy path with a functional case and include at least one negative/error_handling case.
- Add security, ui_ux or performance cases only when relevant to this requirement.
- Each step must be a concrete UI action or API call that references the requirement's acceptance criteria.
- If a step asserts an outcome its type MUST be "assertion" and it MUST be the last step.
- Target {case_count} test case(s).
"""


_BATCHED_REQUIREMENTS_PROMPT = """You are a senior QA architect designing test cases for a set of product requirements.

Requirements:
{requirements}

Return ONLY a pure JSON array (no prose, no markdown fences) of test case objects:
[
  {{
    "requirement_id": "the id of the requirement this case tests (e.g. REQ-001)",
    "title": "short test case title",
    "description": "what this test case verifies",
    "category": "functional" | "error_handling" | "security" | "ui_ux" | "performance",
    "priority": "high" | "medium" | "low",
    "tags": ["frontend" and/or "backend"],
    "steps": [{{"type": "action" | "assertion", "description": "concrete UI or API step"}}],
    "data_refs": ["names of data variants this case is parameterized over"]
  }}
]

Rules:
- Every test case MUST carry the "requirement_id" of the requirement it tests.
- For each requirement cover the happy path (functional) and at least one negative/error_handling case.
- Add security, ui_ux or performance cases only when they are relevant.
- Each step must be a concrete UI action or API call that references the requirement's acceptance criteria.
- If a step asserts an outcome its type MUST be "assertion" and it MUST be the last step.
- Target {case_count} test case(s) per requirement.
"""


def _normalize_case(
    item: Any,
    requirement_id: str,
    context: str,
) -> dict[str, Any] | None:
    """Validate/normalize one raw LLM test case dict; returns None (with a log) when unusable."""
    if not isinstance(item, dict):
        logger.warning("%s: skipping non-object test case entry: %r", context, item)
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        logger.warning("%s: skipping test case without a title: %r", context, item)
        return None

    raw_steps = item.get("steps")
    steps: list[PlanStep] = []
    if isinstance(raw_steps, list):
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                continue
            step_type = str(raw_step.get("type") or "").strip().lower()
            description = str(raw_step.get("description") or "").strip()
            if step_type not in ("action", "assertion") or not description:
                continue
            steps.append(PlanStep(type=step_type, description=description))  # type: ignore[arg-type]
    if not steps:
        logger.warning("%s: skipping test case %r without any valid steps", context, title)
        return None

    category = str(item.get("category") or "functional").strip().lower()
    if category not in VALID_CATEGORIES:
        logger.debug("%s: unknown category %r for case %r, coercing to 'functional'", context, category, title)
        category = "functional"
    priority = str(item.get("priority") or "medium").strip().lower()
    if priority not in VALID_PRIORITIES:
        logger.debug("%s: unknown priority %r for case %r, coercing to 'medium'", context, priority, title)
        priority = "medium"
    tags = item.get("tags")
    normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
    raw_data_refs = item.get("data_refs")
    data_refs = [str(ref).strip() for ref in raw_data_refs if str(ref).strip()] if isinstance(raw_data_refs, list) else []

    return {
        "title": title,
        "description": str(item.get("description") or "").strip(),
        "category": category,
        "priority": priority,
        "tags": normalized_tags,
        "steps": steps,
        "data_refs": data_refs,
        "requirement_ids": [requirement_id],
    }


async def generate_test_plan(
    prd: NormalizedPRD,
    llm: Any = None,
    cases_per_requirement: int = 3,
    include_categories: list[str] | None = None,
) -> TestPlan:
    """Generate a :class:`TestPlan` from a :class:`NormalizedPRD`.

    Uses one batched LLM prompt when the PRD has at most 3 requirements,
    otherwise one prompt per requirement. A requirement whose response cannot
    be parsed is logged and skipped (not fatal). Raises :class:`ValueError` when
    zero test cases could be generated.
    """
    if llm is None:
        llm = _default_llm()
    if not prd.requirements:
        raise ValueError("Cannot generate a test plan: the PRD contains no requirements")

    case_limit = max(1, int(cases_per_requirement))
    allowed_categories = {category.strip().lower() for category in include_categories} if include_categories else None

    raw_cases_by_requirement: dict[str, list[dict[str, Any]]] = {requirement.id: [] for requirement in prd.requirements}
    requirement_ids = set(raw_cases_by_requirement)

    if len(prd.requirements) <= 3:
        requirements_block = "\n\n".join(_format_requirement(requirement) for requirement in prd.requirements)
        prompt = _BATCHED_REQUIREMENTS_PROMPT.format(requirements=requirements_block, case_count=case_limit)
        logger.info("Generating test cases for %d requirements in a single batched prompt", len(prd.requirements))
        try:
            response = await llm.ainvoke(prompt)
            content = getattr(response, "content", response)
            if not isinstance(content, str):
                content = str(content)
            items = parse_json_array(content, context="batched test case generation")
            for item in items:
                if not isinstance(item, dict):
                    logger.warning("Batched test case generation: skipping non-object entry: %r", item)
                    continue
                requirement_id = str(item.get("requirement_id") or "").strip()
                if requirement_id not in requirement_ids:
                    logger.warning("Batched test case generation: skipping case with unknown requirement_id %r", requirement_id)
                    continue
                normalized = _normalize_case(item, requirement_id, context=f"requirement {requirement_id}")
                if normalized is not None:
                    raw_cases_by_requirement[requirement_id].append(normalized)
        except Exception as exc:
            logger.warning("Batched test case generation failed and will be skipped: %s", exc)
    else:
        for requirement in prd.requirements:
            prompt = _SINGLE_REQUIREMENT_PROMPT.format(requirement=_format_requirement(requirement), case_count=case_limit)
            logger.info("Generating test cases for requirement %s (%s)", requirement.id, requirement.title)
            try:
                response = await llm.ainvoke(prompt)
                content = getattr(response, "content", response)
                if not isinstance(content, str):
                    content = str(content)
                items = parse_json_array(content, context=f"requirement {requirement.id} test case generation")
                for item in items:
                    normalized = _normalize_case(item, requirement.id, context=f"requirement {requirement.id}")
                    if normalized is not None:
                        raw_cases_by_requirement[requirement.id].append(normalized)
            except Exception as exc:
                logger.warning("Skipping requirement %s: test case generation failed: %s", requirement.id, exc)

    test_cases: list[TestCase] = []
    for requirement in prd.requirements:
        for case_data in raw_cases_by_requirement[requirement.id]:
            if allowed_categories is not None and case_data["category"] not in allowed_categories:
                logger.debug("Dropping case %r: category %r not in include_categories", case_data["title"], case_data["category"])
                continue
            test_cases.append(TestCase(id="PENDING", **case_data))

    if not test_cases:
        raise ValueError(f"No test cases could be generated for project: {prd.project_name}")

    for index, test_case in enumerate(test_cases, start=1):
        test_case.id = f"TC{index:03d}"

    case_ids_by_requirement: dict[str, list[str]] = {requirement.id: [] for requirement in prd.requirements}
    for test_case in test_cases:
        for requirement_id in test_case.requirement_ids:
            case_ids_by_requirement.setdefault(requirement_id, []).append(test_case.id)

    requirements_mapping = [
        {
            "id": requirement.id,
            "description": requirement.description or requirement.title,
            "source": requirement.source,
            "test_ids": case_ids_by_requirement.get(requirement.id, []),
        }
        for requirement in prd.requirements
    ]

    meta = {
        "project": prd.project_name,
        "version": prd.source_metadata.get("version", "1.0"),
        "generated_by": "hercules",
    }
    return TestPlan(meta=meta, requirements=requirements_mapping, test_cases=test_cases)


def _sanitize_line(text: str, max_length: int | None = None) -> str:
    """Collapse whitespace/newlines into single spaces and optionally truncate."""
    sanitized = " ".join(str(text or "").split())
    if max_length is not None and len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip()
    return sanitized


def compile_plan_to_gherkin(plan: TestPlan, feature_title: str | None = None) -> str:
    """Deterministically compile a :class:`TestPlan` into a Gherkin feature file.

    No LLM is involved. The output is parseable by
    ``testzeus_hercules.utils.gherkin_helper.split_feature_file``: every test
    case becomes one ``Scenario:`` (or ``Scenario Outline:`` when the case has
    non-empty data refs after sanitization), the first action becomes ``Given``,
    subsequent actions ``When``, and assertions ``Then``. Note: ``Scenario Outline`` examples are emitted as
    literal ``<data_ref>`` placeholders — binding real values is left to the
    test_data resolution at execution time.
    """
    project = plan.meta.get("project") or "Unnamed Project"
    title = _sanitize_line(feature_title or f"Hercules Test Plan - {project}", max_length=200).replace("|", "/")
    lines: list[str] = [f"Feature: {title}", ""]

    seen_titles: dict[str, int] = {}
    for case in plan.test_cases:
        scenario_title = _sanitize_line(case.title, max_length=120)
        if scenario_title in seen_titles:
            seen_titles[scenario_title] += 1
            scenario_title = f"{scenario_title}_{seen_titles[scenario_title]}"
        else:
            seen_titles[scenario_title] = 1

        # Sanitize refs first: only non-empty refs justify an Outline (an
        # ``Scenario Outline:`` without an Examples block would be invalid Gherkin).
        refs = [_sanitize_line(ref).replace("|", "/") for ref in case.data_refs if _sanitize_line(ref)]
        keyword = "Scenario Outline:" if refs else "Scenario:"
        case_id = _sanitize_line(case.id)
        lines.append(f"{keyword} {case_id} {scenario_title}")

        first_action_seen = False
        for step in case.steps:
            description = _sanitize_line(step.description).replace("|", "/")
            if step.type == "assertion":
                lines.append(f"  Then {description}")
            elif not first_action_seen:
                first_action_seen = True
                lines.append(f"  Given {description}")
            else:
                lines.append(f"  When {description}")

        if refs:
            lines.append("")
            lines.append("  Examples:")
            lines.append("    | " + " | ".join(refs) + " |")
            lines.append("    | " + " | ".join(f"<{ref}>" for ref in refs) + " |")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
