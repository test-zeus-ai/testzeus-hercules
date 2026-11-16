"""Parse multi-scenario Gherkin files for importers.

:mod:`testzeus_hercules.cases.gherkin` compiles exactly one plain scenario. Imported
files are messier, so this parser also accepts several scenarios, ``Scenario
Outline`` with one or more ``Examples`` tables (one scenario per row, ``<name>``
replaced by the row's value), ``Rule`` blocks with their own ``Background``,
free-text descriptions, doc strings and step data tables (folded into the step
text). Step semantics match the single-scenario compiler: a ``Then`` line and the
``And``/``But``/``*`` lines after it become ``verify`` steps whose ``expected`` is
the line text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from testzeus_hercules.cases.gherkin import VERIFY_ACTION, GherkinError
from testzeus_hercules.cases.models import Step

_STEP = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
_SECTION = re.compile(r"^(Feature|Background|Scenario Outline|Scenario Template|Scenario|Example|Examples|Scenarios|Rule):\s*(.*)$")
_OUTLINE_KEYWORDS = frozenset({"Scenario Outline", "Scenario Template"})
_SCENARIO_KEYWORDS = frozenset({"Scenario", "Example"}) | _OUTLINE_KEYWORDS
_EXAMPLES_KEYWORDS = frozenset({"Examples", "Scenarios"})
_DOC_STRING_FENCES = ('"""', "```")
_PARAMETER = re.compile(r"<([^<>]+)>")
_MAX_STEP_CHARS = 2000
_STEP_KEYWORDS = ("given", "when", "then", "and", "but")
_COMMON_WORDS = frozenset(
    {
        "this",
        "that",
        "than",
        "them",
        "they",
        "thus",
        "these",
        "those",
        "there",
        "what",
        "whom",
        "with",
        "where",
        "which",
        "while",
        "will",
        "were",
        "went",
        "gives",
        "even",
        "seven",
        "liven",
        "eleven",
        "any",
        "bug",
        "bus",
        "buy",
        "bat",
        "ant",
        "end",
        "add",
        "bud",
        "but",
        "nut",
        "put",
        "cut",
        "out",
        "art",
        "aid",
        "age",
    }
)


@dataclass(frozen=True)
class ScenarioSpec:
    """One concrete scenario: an ordinary scenario, or one Examples row of an outline."""

    name: str
    line: int
    tags: list[str]
    steps: list[Step]
    outline: bool = False
    example: dict[str, str] | None = None
    example_index: int | None = None


@dataclass(frozen=True)
class FeatureSpec:
    name: str | None
    description: str | None
    tags: list[str]
    scenarios: list[ScenarioSpec]


@dataclass
class _RawStep:
    keyword: str
    text: str
    then: bool
    line: int
    table: list[list[str]] = field(default_factory=list)
    doc: list[str] | None = None

    def render(self, values: dict[str, str] | None = None) -> str:
        text = self.text
        if self.table:
            header, *rows = self.table
            cells = [", ".join(f"{name}={value}" for name, value in zip(header, row)) for row in rows] if rows else [", ".join(header)]
            text += f" (table: {'; '.join(cells)})"
        if self.doc is not None and text.rstrip().endswith(":"):
            text = f"{text.rstrip()} {chr(10).join(self.doc).strip(chr(10))}"  # a request body or a SQL query, line breaks kept
        elif self.doc is not None:
            body = "\\n".join(self.doc)
            text += f' with text: "{body}"'
        if values:
            text = _PARAMETER.sub(lambda match: values.get(match.group(1).strip(), match.group(0)), text)
        return text[:_MAX_STEP_CHARS]


@dataclass
class _Examples:
    tags: list[str]
    line: int
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class _RawScenario:
    name: str
    line: int
    tags: list[str]
    outline: bool
    background: list[_RawStep]
    steps: list[_RawStep] = field(default_factory=list)
    examples: list[_Examples] = field(default_factory=list)


@dataclass
class _Parser:
    source: str
    feature: str | None = None
    description: list[str] = field(default_factory=list)
    feature_tags: list[str] = field(default_factory=list)
    pending_tags: list[str] = field(default_factory=list)
    background: list[_RawStep] = field(default_factory=list)
    rule_background: list[_RawStep] | None = None
    scenarios: list[_RawScenario] = field(default_factory=list)
    section: str | None = None  # feature | background | rule_background | scenario | examples
    in_then: bool = False
    doc_open: tuple[str, int, int] | None = None  # fence, line, indent
    section_steps: int = 0  # steps seen in the current section

    def fail(self, line: int, message: str) -> GherkinError:
        return GherkinError(f"{self.source}:{line}: {message}")

    def feed(self, number: int, raw: str) -> None:
        line = raw.strip()
        if self.doc_open is not None:
            self._feed_doc(raw, line)
            return
        if not line or line.startswith("#"):
            return
        if line.startswith(_DOC_STRING_FENCES):
            self._open_doc(number, raw, line)
        elif line.startswith("|"):
            self._add_row(number, line)
        elif line.startswith("@"):
            self.pending_tags.extend(tag.lstrip("@") for tag in line.split() if tag.lstrip("@"))
        elif (section := _SECTION.match(line)) is not None:
            self._open_section(number, section.group(1), section.group(2).strip())
        elif (step := _STEP.match(line)) is not None:
            self._add_step(number, step.group(1), step.group(2).strip())
        elif self.section == "feature":  # free text under Feature or Rule: the description
            self.description.append(line)
        elif self.section in ("background", "rule_background", "scenario") and not self.section_steps and not _misspelt_keyword(line):
            pass  # free text between a Scenario or Background line and its first step: its description (allowed by Gherkin)
        else:  # a line that is not a step after the first one, or one that starts like a misspelt keyword ("Gevin I log in"): never drop it
            raise self.fail(number, f"expected a step (Given/When/Then/And/But/*) or a section keyword, got {line[:60]!r}")

    def finish(self) -> None:
        if self.doc_open is not None:
            raise self.fail(self.doc_open[1], "doc string is not closed")

    # ------------------------------------------------------------------ sections

    def _open_section(self, number: int, keyword: str, name: str) -> None:
        tags, self.pending_tags = self.pending_tags, []
        self.in_then = False
        self.section_steps = 0
        if keyword == "Feature":
            self.feature, self.feature_tags, self.section = name or None, tags, "feature"
        elif keyword == "Rule":
            self.rule_background, self.section = [], "feature"
        elif keyword == "Background":
            self.section = "rule_background" if self.rule_background is not None else "background"
        elif keyword in _SCENARIO_KEYWORDS:
            background = [*self.background, *(self.rule_background or [])]
            self.scenarios.append(_RawScenario(name=name, line=number, tags=tags, outline=keyword in _OUTLINE_KEYWORDS, background=background))
            self.section = "scenario"
        else:  # Examples
            if not self.scenarios or not self.scenarios[-1].outline or self.section not in ("scenario", "examples"):
                raise self.fail(number, "Examples must follow a Scenario Outline")
            self.scenarios[-1].examples.append(_Examples(tags=tags, line=number))
            self.section = "examples"

    def _add_step(self, number: int, keyword: str, text: str) -> None:
        if self.section not in ("background", "rule_background", "scenario"):
            raise self.fail(number, "step outside a scenario or background")
        self.section_steps += 1
        if keyword == "Then":
            self.in_then = True
        elif keyword in ("Given", "When"):
            self.in_then = False
        step = _RawStep(keyword=keyword, text=text, then=self.in_then, line=number)
        self._step_target().append(step)

    def _step_target(self) -> list[_RawStep]:
        if self.section == "background":
            return self.background
        if self.section == "rule_background":
            assert self.rule_background is not None
            return self.rule_background
        return self.scenarios[-1].steps

    def _last_step(self, number: int, what: str) -> _RawStep:
        target = self._step_target() if self.section in ("background", "rule_background", "scenario") else []
        if not target:
            raise self.fail(number, f"{what} must follow a step")
        return target[-1]

    def _add_row(self, number: int, line: str) -> None:
        if not line.endswith("|") or len(line) < 2:
            raise self.fail(number, "table row must start and end with '|'")
        cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line[1:-1])]
        if self.section == "examples":
            examples = self.scenarios[-1].examples[-1]
            if examples.rows and len(cells) != len(examples.rows[0]):
                raise self.fail(number, f"Examples row has {len(cells)} cells but the header has {len(examples.rows[0])}")
            examples.rows.append(cells)
            return
        step = self._last_step(number, "a data table")
        if step.table and len(cells) != len(step.table[0]):
            raise self.fail(number, f"table row has {len(cells)} cells but the header has {len(step.table[0])}")
        step.table.append(cells)

    # ------------------------------------------------------------------ doc strings

    def _open_doc(self, number: int, raw: str, line: str) -> None:
        step = self._last_step(number, "a doc string")
        step.doc = []
        self.doc_open = (line[:3], number, len(raw) - len(raw.lstrip()))

    def _feed_doc(self, raw: str, line: str) -> None:
        assert self.doc_open is not None
        fence, _, indent = self.doc_open
        if line.startswith(fence):
            self.doc_open = None
            return
        step = self._step_target()[-1]
        assert step.doc is not None
        step.doc.append(raw[indent:] if raw[:indent].strip() == "" else raw.strip())


def parse_feature(text: str, *, source_name: str = "<feature>") -> FeatureSpec:
    """Parse a feature file into concrete scenarios (outlines expanded); raises :class:`GherkinError`."""
    parser = _Parser(source=source_name)
    for number, raw in enumerate(text.splitlines(), start=1):
        parser.feed(number, raw)
    parser.finish()
    scenarios: list[ScenarioSpec] = []
    for raw_scenario in parser.scenarios:
        scenarios.extend(_expand(parser, raw_scenario))
    description = " ".join(parser.description) or None
    return FeatureSpec(name=parser.feature, description=description, tags=list(parser.feature_tags), scenarios=scenarios)


def _expand(parser: _Parser, scenario: _RawScenario) -> list[ScenarioSpec]:
    tags = [*parser.feature_tags, *scenario.tags]
    raw_steps = [*scenario.background, *scenario.steps] if scenario.steps else []
    if not scenario.outline:
        return [ScenarioSpec(name=scenario.name, line=scenario.line, tags=tags, steps=[_step(step) for step in raw_steps])]
    if not scenario.examples:
        raise parser.fail(scenario.line, f"Scenario Outline {scenario.name!r} has no Examples")
    expanded: list[ScenarioSpec] = []
    for examples in scenario.examples:
        if len(examples.rows) < 2:
            raise parser.fail(examples.line, "Examples need a header row and at least one value row")
        header, *rows = examples.rows
        for row in rows:
            values = dict(zip(header, row))
            name = _PARAMETER.sub(lambda match: values.get(match.group(1).strip(), match.group(0)), scenario.name)
            expanded.append(
                ScenarioSpec(
                    name=name,
                    line=scenario.line,
                    tags=[*tags, *examples.tags],
                    steps=[_step(step, values) for step in raw_steps],
                    outline=True,
                    example=values,
                    example_index=len(expanded) + 1,
                )
            )
    return expanded


def _step(raw: _RawStep, values: dict[str, str] | None = None) -> Step:
    text = raw.render(values)
    return Step(action=VERIFY_ACTION, expected=text) if raw.then else Step(action=text)


def _misspelt_keyword(line: str) -> bool:
    """True when a line starts like a misspelt step keyword ("Gevin", "Whne", "Thne", "Anf"), not like a sentence."""
    word = line.split(maxsplit=1)[0].lower()
    if word in _STEP_KEYWORDS:  # a bare keyword with nothing after it
        return True
    if word in _COMMON_WORDS:
        return False
    long_ = any(_distance(word, keyword) <= 2 for keyword in ("given", "when", "then") if len(word) >= 4)
    short = any(_distance(word, keyword) <= 1 for keyword in ("and", "but") if len(word) <= 4)
    return long_ or short


def _distance(a: str, b: str) -> int:
    """Edit distance (insertions, deletions, substitutions)."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]
