"""Compile a single-scenario Gherkin ``.feature`` file into a :class:`TestCase`.

Deterministic, one step per line: ``Background`` steps are prepended to the
scenario's steps, and every ``Then`` line (plus ``And``/``But``/``*`` lines that
follow it) becomes a step with ``action="verify"`` whose ``expected`` is the line
text. A doc string may follow a step that ends with ``:`` (an API request body, a
SQL query): it is appended to that step's text after the colon, line breaks kept,
so ``When I send a POST request to "/pets" with the JSON body:`` takes the JSON
below it. Other doc strings, data tables and scenario outlines are rejected for now. A
security scan step in plain words (``When I run a security scan for "cve, xss"``) gets its
``security`` payload, and a ``Then`` right after it that says there are no findings becomes
its expected outcome (see :mod:`testzeus_hercules.cases.scan_phrases`).

Two tags set where the case runs: ``@env:<name>`` names a saved environment (a web app, a
phone to emulate, or a native app on a device), and ``@device:<profile>`` emulates a phone or
tablet in the browser, with ``_`` for spaces (``@device:iPhone_15``, ``@device:Pixel_7``).

``{{name}}`` names a value of the case's test data, of its environment (variables and secrets) or a built-in. TestZeus
references ``[$.environment.<env>.<key>]`` and ``[$.testdata.<name>.<key>]`` are read as ``{{key}}``. The test data of
``<name>.feature`` is kept beside it in ``<name>.data.json`` as ``{"test_data": {...}}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from testzeus_hercules.cases.models import DataKey, EnvironmentRef, Step, TestCase, TestDataValue
from testzeus_hercules.cases.scan_phrases import fold_scan_steps

VERIFY_ACTION = "verify"

_STEP = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
_DOC_FENCES = ('"""', "```")
_SECTION = re.compile(r"^(Feature|Background|Scenario|Example|Scenario Outline|Scenario Template|Examples|Scenarios|Rule):\s*(.*)$")
_SLUG_INVALID = re.compile(r"[^a-z0-9]+")
_MAX_SLUG = 100


DATA_SUFFIX = ".data.json"
MAX_DATA_BYTES = 256 * 1024


class FeatureData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_data: dict[DataKey, TestDataValue] = Field(default_factory=dict)


class GherkinError(ValueError):
    """The feature text cannot be compiled; the message names the source and line."""


@dataclass
class _Scenario:
    name: str
    line: int
    steps: list[Step] = field(default_factory=list)


@dataclass
class _Parser:
    source: str
    feature: str | None = None
    tags: list[str] = field(default_factory=list)
    background: list[Step] = field(default_factory=list)
    scenarios: list[_Scenario] = field(default_factory=list)
    section: str | None = None
    in_then: bool = False
    doc: tuple[str, int, int] | None = None  # fence, opening line, indentation
    doc_lines: list[str] = field(default_factory=list)

    def fail(self, line: int, message: str) -> GherkinError:
        return GherkinError(f"{self.source}:{line}: {message}")

    def feed(self, number: int, raw: str) -> None:
        line = raw.strip()
        if self.doc is not None:
            self._feed_doc(raw, line)
            return
        if not line or line.startswith("#"):
            return
        if line.startswith(_DOC_FENCES):
            self._open_doc(number, raw, line)
            return
        if line.startswith("|"):
            raise self.fail(number, "data tables are not supported yet; write one step per row")
        if line.startswith("@"):
            self.tags.extend(tag.lstrip("@") for tag in line.split() if tag.lstrip("@"))
            return
        section = _SECTION.match(line)
        if section is not None:
            self._open_section(number, section.group(1), section.group(2).strip())
            return
        step = _STEP.match(line)
        if step is None:
            raise self.fail(number, f"expected a step (Given/When/Then/And/But/*) or a section keyword, got {line[:60]!r}")
        self._add_step(number, step.group(1), step.group(2).strip())

    def finish(self) -> None:
        if self.doc is not None:
            raise self.fail(self.doc[1], "doc string is not closed")

    def _steps(self) -> list[Step]:
        return self.background if self.section == "background" else self.scenarios[-1].steps

    def _open_doc(self, number: int, raw: str, line: str) -> None:
        steps = self._steps() if self.section is not None else []
        last = steps[-1] if steps else None
        text = (last.expected if last is not None and last.action == VERIFY_ACTION else last.action if last is not None else None) or ""
        if not text.rstrip().endswith(":"):
            raise self.fail(number, "doc strings are supported only after a step that ends with ':' (a request body or a SQL query); put other text in quotes on the step line")
        self.doc = (line[:3], number, len(raw) - len(raw.lstrip()))
        self.doc_lines = []

    def _feed_doc(self, raw: str, line: str) -> None:
        assert self.doc is not None
        fence, _, indent = self.doc
        if line.startswith(fence):
            self.doc = None
            self._fold_doc("\n".join(self.doc_lines).strip("\n"))
            return
        self.doc_lines.append(raw[indent:] if raw[:indent].strip() == "" else raw.strip())

    def _fold_doc(self, body: str) -> None:
        steps = self._steps()
        last = steps[-1]
        if last.action == VERIFY_ACTION and last.expected is not None:
            steps[-1] = last.model_copy(update={"expected": f"{last.expected.rstrip()} {body}"})
        else:
            steps[-1] = last.model_copy(update={"action": f"{last.action.rstrip()} {body}"})

    def _open_section(self, number: int, keyword: str, name: str) -> None:
        if keyword in ("Scenario Outline", "Scenario Template", "Examples", "Scenarios"):
            raise self.fail(number, "Scenario Outline / Examples are not supported yet; write one scenario per file")
        if keyword == "Rule":
            raise self.fail(number, "Rule blocks are not supported yet")
        self.in_then = False
        if keyword == "Feature":
            self.feature = name or None
            self.section = None
        elif keyword == "Background":
            self.section = "background"
        else:
            self.scenarios.append(_Scenario(name=name, line=number))
            self.section = "scenario"

    def _add_step(self, number: int, keyword: str, text: str) -> None:
        if self.section is None:
            raise self.fail(number, "step outside a scenario or background")
        if keyword == "Then":
            self.in_then = True
        elif keyword in ("Given", "When"):
            self.in_then = False
        step = Step(action=VERIFY_ACTION, expected=text) if self.in_then else Step(action=text)
        target = self.background if self.section == "background" else self.scenarios[-1].steps
        target.append(step)


TESTZEUS_REFERENCE = re.compile(r"\[\$\.(?:environment|testdata)\.[A-Za-z0-9_-]+\.([A-Za-z_][A-Za-z0-9_]*)\]")


def slugify(name: str) -> str:
    """Case id derived from a scenario name: lowercase words joined by '-'."""
    slug = _SLUG_INVALID.sub("-", name.lower()).strip("-")[:_MAX_SLUG].strip("-")
    return slug or "scenario"


def compile_feature(
    text: str,
    *,
    source_name: str = "<feature>",
    base_url: str | None = None,
    test_data: Mapping[str, TestDataValue] | None = None,
) -> TestCase:
    """Compile feature text with exactly one scenario into a ready :class:`TestCase`."""
    text = TESTZEUS_REFERENCE.sub(lambda match: "{{" + match.group(1) + "}}", text)
    parser = _Parser(source=source_name)
    for number, raw in enumerate(text.splitlines(), start=1):
        parser.feed(number, raw)
    parser.finish()
    if len(parser.scenarios) != 1:
        raise GherkinError(f"{source_name}: expected exactly one scenario, found {len(parser.scenarios)}")
    scenario = parser.scenarios[0]
    if not scenario.steps:
        raise GherkinError(f"{source_name}:{scenario.line}: scenario {scenario.name!r} has no steps")
    return TestCase(
        id=slugify(scenario.name),
        title=scenario.name or "Scenario",
        description=parser.feature,
        tags=parser.tags,
        environment=_environment(parser.tags, base_url, source_name),
        test_data=dict(test_data or {}),
        steps=fold_scan_steps([*parser.background, *scenario.steps]),
    )


def _environment(tags: list[str], base_url: str | None, source_name: str) -> EnvironmentRef:
    """The case's environment from its ``@env:`` and ``@device:`` tags (the last of each wins) and the base URL."""
    fields: dict[str, str] = {"base_url": base_url} if base_url is not None else {}
    for tag in tags:
        key, _, value = tag.partition(":")
        if key == "env" and value:
            fields["ref"] = value
        elif key == "device" and value:
            fields["device"] = value.replace("_", " ")
    try:
        return EnvironmentRef.model_validate(fields)
    except ValidationError as exc:
        problem = "; ".join(str(error["msg"]).removeprefix("Value error, ") for error in exc.errors()[:3])
        raise GherkinError(f"{source_name}: {problem}") from None


def data_file(path: Path) -> Path:
    return path.with_name(path.stem + DATA_SUFFIX)


def read_feature_data(path: Path) -> dict[str, TestDataValue]:
    """The test data kept beside the ``.feature`` file ``path`` (none when there is no data file)."""
    sidecar = data_file(path)
    if sidecar.is_symlink() or not sidecar.is_file():
        return {}
    try:
        if sidecar.stat().st_size > MAX_DATA_BYTES:
            raise GherkinError(f"{sidecar.name}: the test data file is too large")
        return FeatureData.model_validate_json(sidecar.read_bytes()).test_data
    except OSError as exc:
        raise GherkinError(f"{sidecar.name}: cannot read the test data ({exc.__class__.__name__})") from None
    except ValidationError as exc:
        problem = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()[:3])
        raise GherkinError(f"{sidecar.name}: invalid test data ({problem})") from None


def load_feature(path: Path, *, base_url: str | None = None, test_data: Mapping[str, TestDataValue] | None = None) -> TestCase:
    """Read and compile a ``.feature`` file with the test data kept beside it."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GherkinError(f"{path.name}: cannot read feature file ({exc.__class__.__name__})") from None
    data = test_data if test_data is not None else read_feature_data(path)
    return compile_feature(text, source_name=path.name, base_url=base_url, test_data=data)
