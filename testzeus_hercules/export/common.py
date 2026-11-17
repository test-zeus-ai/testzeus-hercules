"""Shared pieces of the exporters: the result type, template and JSONPath parsing, and the writer base class.

A :class:`Writer` walks an :class:`~testzeus_hercules.cases.automation.AutomationSpec` step by step
and asks its language subclass for each statement. Rendering is deterministic: the same spec gives
the same text (no times, no run-dependent ordering).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationData, AutomationSpec, AutomationStep, LocatorSpec, TargetSpec
from testzeus_hercules.cases.models import ApiCall, SqlCall
from testzeus_hercules.engine.automation import REFERENCE, relative_url

Format = Literal["spec", "playwright-ts", "playwright-py", "appium-py"]
FORMATS: tuple[Format, ...] = ("spec", "playwright-ts", "playwright-py", "appium-py")
# Formats for a run in a browser, and for a native app run on a device.
WEB_FORMATS: tuple[Format, ...] = ("playwright-ts", "playwright-py")
APP_FORMATS: tuple[Format, ...] = ("appium-py",)
REDACTED_MASK = "******"
BASE_URL_ENV = "BASE_URL"
# A browser executable to run the exported test with (a Chrome for Testing build, as Hercules itself may use); unset,
# Playwright uses its own downloaded browser.
BROWSER_ENV = "HERCULES_CHROMIUM"
# JSONPath this exporter writes as plain property access: $, .name, ['name'], ["name"], [0].
_PATH_TOKEN = re.compile(r"""\.([A-Za-z_$][\w$-]*)|\[\s*(\d+)\s*\]|\[\s*'((?:[^'\\]|\\.)*)'\s*\]|\[\s*"((?:[^"\\]|\\.)*)"\s*\]""")
_EXACT_REFERENCE = re.compile(r"^\s*(?:\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\})\s*$")


class ExportError(ValueError):
    """The run cannot be exported (not finished, did not pass, recorded before exports existed); the message says why."""


@dataclass(frozen=True)
class EnvVar:
    name: str
    required: bool
    purpose: str


@dataclass(frozen=True)
class ExportResult:
    """An exported run: the code (or the spec as JSON), where to save it, what it needs, and what to review."""

    format: Format
    run_id: str
    case_id: str
    run_status: str
    filename: str
    code: str
    spec: dict[str, Any]
    env: tuple[EnvVar, ...] = ()
    notes: tuple[str, ...] = ()
    steps_exported: int = 0
    steps_total: int = 0
    partial: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "run_status": self.run_status,
            "filename": self.filename,
            "code": self.code,
            "spec": self.spec,
            "env": [{"name": item.name, "required": item.required, "purpose": item.purpose} for item in self.env],
            "notes": list(self.notes),
            "steps_exported": self.steps_exported,
            "steps_total": self.steps_total,
            "partial": self.partial,
        }


# A path segment the app made for this run (a record id, a number, a UUID): an address waited for after an action
# matches any value there, since the next run gets another.
_GENERATED_SEGMENT = re.compile(r"^(?:\d{4,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,})$")


def address_pattern(address: str) -> str | None:
    """A regular expression for an address an action navigated to (a path, or origin and path) whose segments made for
    the run (record ids, numbers, UUIDs) match any segment; None when it has no such segment (compare it exactly)."""
    origin, path = ("", address) if address.startswith("/") else (address[: address.find("/", address.find("//") + 2)], address[address.find("/", address.find("//") + 2) :])
    segments = path.split("/")
    if not any(_GENERATED_SEGMENT.match(segment) for segment in segments):
        return None
    return re.escape(origin) + "/".join("[^/]+" if _GENERATED_SEGMENT.match(segment) else re.escape(segment) for segment in segments)


def json_path_keys(path: str) -> list[str | int] | None:
    """A JSONPath of names and indexes as a list of keys; None for anything else (wildcards, filters, slices)."""
    text = path.strip()
    if text.startswith("$"):
        text = text[1:]
    elif text and not text.startswith((".", "[")):
        text = "." + text
    keys: list[str | int] = []
    position = 0
    while position < len(text):
        found = _PATH_TOKEN.match(text, position)
        if found is None:
            return None
        name, index, single, double = found.groups()
        if index is not None:
            keys.append(int(index))
        else:
            keys.append(name if name is not None else re.sub(r"\\(.)", r"\1", single if single is not None else double))
        position = found.end()
    return keys


def exact_reference(text: str) -> str | None:
    """The name when ``text`` is exactly one ``{{name}}`` or ``${name}``."""
    found = _EXACT_REFERENCE.match(text)
    return (found.group(1) or found.group(2)) if found else None


def template_parts(text: str) -> list[tuple[str, str | None]]:
    """``text`` as parts in order: (literal text, None) and (the reference as written, its name)."""
    parts: list[tuple[str, str | None]] = []
    position = 0
    for found in REFERENCE.finditer(text):
        if found.start() > position:
            parts.append((text[position : found.start()], None))
        parts.append((found.group(0), found.group(1) or found.group(2)))
        position = found.end()
    if position < len(text):
        parts.append((text[position:], None))
    return parts


# Characters that end a comment line in Python or JavaScript (newlines, U+2028/2029 and other separators), control
# characters, and lone surrogates (which cannot be written as UTF-8).
_LITERAL_ESCAPE = re.compile(r"[\u2028\u2029\ud800-\udfff\x7f-\x9f]")
_UNSAFE_IN_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")


def viewport_size(viewport: str | None) -> tuple[int, int] | None:
    """``WIDTHxHEIGHT`` as (width, height), or None."""
    found = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", viewport or "")
    return (int(found.group(1)), int(found.group(2))) if found else None


def one_line(text: str) -> str:
    """``text`` safe on one comment line: every line break, separator and control character becomes a space (page- and
    model-derived text must never end a ``#`` or ``//`` comment and become code)."""
    return _UNSAFE_IN_LINE.sub(" ", str(text))


def safe_id(text: str) -> str:
    """An identifier (run id, case id) reduced to a safe charset for headers, docstrings and file names."""
    return re.sub(r"\.{2,}", ".", re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text))).strip("._") or "case"


def snake(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower() or "case"


def status_codes(expect: Any) -> list[str]:
    raw = expect if isinstance(expect, list) else [expect]
    return [str(code).lower() for code in raw]


class Writer:
    """Walks a spec and writes a test file; subclasses write each statement in their language."""

    comment = "//"
    indent_unit = "  "

    def __init__(self, spec: AutomationSpec, *, run_id: str, partial_note: str | None = None) -> None:
        self.spec = spec
        self.run_id = run_id
        self.partial_note = partial_note
        self.helpers: set[str] = set()
        self.secrets: dict[str, str] = {}  # env var -> purpose
        self.notes: list[str] = []
        self.lines: list[str] = []
        self.depth = 0
        self.api_used = any(step.kind == "api" for step in spec.steps)
        # Which response the code last parsed as JSON (a request makes a new one).
        self.response_seq = 0
        self.body_seq = -1
        self.sql_used = any(step.kind == "sql" for step in spec.steps)

    # ------------------------------------------------------------------ output

    def emit(self, line: str = "") -> None:
        self.lines.append((self.indent_unit * self.depth + line) if line else "")

    def note(self, text: str) -> None:
        self.emit(f"{self.comment} {one_line(text)}")

    def todo(self, step: AutomationStep, text: str) -> None:
        """A comment in the code and a note in the result: something to review or write by hand."""
        self.note(f"TODO: {text}")
        line = f"step {step.index}: {text}"
        if line not in self.notes:
            self.notes.append(line)

    def unproven(self, check: AutomationCheck, line: str) -> None:
        """A text assertion written in its plainest form: active when the run proved it holds (or did not probe it),
        commented out when no form held on the page (the check was already flagged with a TODO)."""
        if check.form is None and not check.faithful:
            self.note(line)
        else:
            self.emit(line)

    # ------------------------------------------------------------------ values

    def string(self, text: str) -> str:
        """A string literal valid in Python and JavaScript; line separators and lone surrogates are escaped."""
        return _LITERAL_ESCAPE.sub(lambda found: f"\\u{ord(found.group(0)):04x}", json.dumps(text, ensure_ascii=False))

    def data(self, name: str) -> AutomationData | None:
        return self.spec.data.get(name)

    def reference(self, name: str, *, typed: bool = False) -> str | None:
        """The expression for a data name (None: the name is unknown and stays as written)."""
        entry = self.data(name)
        if entry is None:
            return None
        if entry.kind == "secret" and entry.env:
            self.secrets.setdefault(entry.env, f"secret {{{{{name}}}}}")
            return self.env_expr(entry.env)
        if entry.kind == "value":
            return self.data_expr(name)
        if entry.kind == "builtin":
            return name.upper()
        if entry.kind == "saved":
            self.helpers.add("text")
            return self.saved_expr(name) if typed else self.text_expr(self.saved_expr(name))
        return None

    def expr(self, template: str, *, typed: bool = False) -> str:
        """A template as an expression: literals and references joined (an exact reference keeps a saved value's type when ``typed``)."""
        exact = exact_reference(template)
        if exact is not None:
            found = self.reference(exact, typed=typed)
            if found is not None:
                return found
        pieces: list[str] = []
        literal = ""
        for text, name in template_parts(template):
            found = self.reference(name) if name is not None else None
            if found is None:
                literal += text  # plain text, or a name the case does not define (kept as written, as the run did)
                continue
            if literal:
                pieces.append(self.string(literal))
                literal = ""
            pieces.append(found)
        if literal or not pieces:
            pieces.append(self.string(literal))
        return " + ".join(pieces)

    def json_value(self, node: Any) -> str:
        """A JSON value (a request body, an expected value) as a literal, with templates in its strings."""
        if isinstance(node, str):
            return self.expr(node, typed=True)
        if isinstance(node, bool):
            return self.true if node else self.false
        if node is None:
            return self.null
        if isinstance(node, (int, float)):
            return json.dumps(node)
        if isinstance(node, list):
            return "[" + ", ".join(self.json_value(item) for item in node) + "]"
        if isinstance(node, dict):
            return self.dict_literal([(str(key), self.json_value(value)) for key, value in node.items()])
        return self.string(str(node))

    def url_expr(self, url: str) -> str:
        """A navigation address: relative ones are joined to the base URL as the run did."""
        if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE) and not url.startswith("{{"):
            return self.expr(url)
        self.helpers.add("url")
        return f"url({self.expr(url)})"

    def api_url_expr(self, path: str) -> str:
        if re.match(r"^https?://", path, re.IGNORECASE):
            return self.expr(path)
        self.helpers.add("api_url")
        return f"apiUrl({self.expr(path)})" if self.comment == "//" else f"api_url({self.expr(path)})"

    # ------------------------------------------------------------------ walking

    def render(self) -> str:
        body_start = len(self.lines)
        self.open_test()
        self.depth += 1
        for step in self.spec.steps:
            self.step(step)
        self.depth -= 1
        self.close_test()
        body = self.lines[body_start:]
        self.lines = self.lines[:body_start]
        self.header()
        self.lines.extend(body)
        return "\n".join(self.lines).rstrip() + "\n"

    def step(self, step: AutomationStep) -> None:
        self.open_step(step)
        self.depth += 1
        for note in step.notes:
            self.note(note)
        if step.kind == "api":
            self.api_step(step)
        elif step.kind == "sql":
            self.sql_step(step)
        elif step.kind == "quality":
            self.quality_step(step)
        elif step.kind == "security":
            self.security_step(step)
        else:
            if not step.actions and not step.checks:
                self.note("this step did nothing a test needs to repeat")
            for action in step.actions:
                self.action(step, action)
            for check in step.checks:
                self.check(step, check)
        self.depth -= 1
        self.close_step(step)

    def action_notes(self, step: AutomationStep, action: AutomationAction) -> None:
        """An action's notes: a comment when it was exported faithfully, else a TODO (locator problems are told by ``target_expr``)."""
        for part in (action.note or "").split("; "):
            if not part or part.startswith(("no locator", "locators not recorded")):
                continue
            if action.faithful:
                self.note(part)
            else:
                self.todo(step, part)

    def target_expr(self, step: AutomationStep, target: TargetSpec | None, what: str) -> str:
        """The locator expression for an element; a best guess with a TODO when no unique locator was recorded."""
        if target is None:
            self.todo(step, f"no locator was recorded for the element to {what}; write one by hand")
            return self.scope_expr([]) + self.locator_call(LocatorSpec(strategy="css", selectors=["REPLACE-ME"]))
        scope = self.scope_expr(target.frames)
        if target.locator is None:
            self.todo(step, f'no locator matched only the element to {what} ({target.role} "{target.name}"); this one is a guess')
            guess = LocatorSpec(strategy="role", role=target.role, name=target.name or None) if target.role not in ("", "generic") else LocatorSpec(strategy="text", value=target.name, exact=False)
            return scope + self.locator_call(guess)
        if target.fallbacks:
            alternatives = "; ".join(self.scope_expr(target.frames) + self.locator_call(item) for item in target.fallbacks)
            self.note(f"also unique on the page: {alternatives}")
        return scope + self.locator_call(target.locator)

    def scope_expr(self, frames: list[Any]) -> str:
        scope = "page"
        for frame in frames:
            scope += self.locator_call(frame.locator) + self.content_frame
        return scope

    def url_check_value(self, check: AutomationCheck) -> tuple[str, bool]:
        """The expected address of an equals check (relative to the base URL when it is under it) and whether it is relative."""
        relative = relative_url(check.expected, self.spec.base_url)
        if relative != check.expected:
            self.helpers.add("url")
            return f"url({self.expr(relative)})", True
        return self.expr(check.expected), False

    def api_call(self, step: AutomationStep) -> ApiCall | None:
        try:
            return ApiCall.model_validate(step.api)
        except ValueError:
            return None

    def sql_call(self, step: AutomationStep) -> SqlCall | None:
        try:
            return SqlCall.model_validate(step.sql)
        except ValueError:
            return None

    def masked(self, step: AutomationStep, payload: Any) -> bool:
        """True (with a TODO) when a payload holds a masked secret value: the run wrote it literally instead of by name."""
        if REDACTED_MASK in json.dumps(payload, ensure_ascii=False):
            self.todo(step, "the payload holds a secret written literally in the case (masked in the run's files); reference it as {{name}} with a secret in test_data")
            return True
        return False

    # ------------------------------------------------------------------ API and SQL checks (shared; the hooks write the syntax)

    def json_condition(self, document: str, path: str, operator: str, value: Any, given: bool) -> str | None:
        """A boolean expression for one JSONPath check on ``document``, or None when the path is not plain property access."""
        keys = json_path_keys(path)
        if keys is None:
            return None
        self.helpers.add("at")
        actual = f"at({document}, {self.keys_literal(keys)})"
        expected = self.expected_value(value) if given else self.null
        if operator == "eq_path":
            other = json_path_keys(str(value)) if isinstance(value, str) else None
            if other is None:
                return None
            expected = f"at({document}, {self.keys_literal(other)})"
            operator = "eq"
        return self.operator_condition(actual, operator, expected)

    def operator_condition(self, actual: str, operator: str, expected: str) -> str | None:
        helper = {"eq": "same", "ne": "same", "contains": "contains", "not_contains": "contains", "matches": "matches", "count_eq": "size"}.get(operator)
        if operator in ("eq", "ne", "contains", "not_contains", "matches"):
            self.helpers.add(helper or "same")
            call = f"{helper}({actual}, {expected})"
            return self.negate(call) if operator in ("ne", "not_contains") else call
        if operator in ("exists", "not_exists"):
            self.helpers.add("exists")
            call = f"exists({actual})"
            return call if operator == "exists" else self.negate(call)
        if operator == "not_empty":
            self.helpers.add("filled")
            return f"filled({actual})"
        if operator in ("gt", "lt"):
            self.helpers.add("number")
            return f"number({actual}) {'>' if operator == 'gt' else '<'} number({expected})"
        if operator == "count_eq":
            self.helpers.update({"size", "same"})
            return f"same(size({actual}), {expected})"
        if operator in ("all_gte", "all_lte"):
            self.helpers.add("bounded")
            return f"bounded({actual}, {expected}, {self.string('gte' if operator == 'all_gte' else 'lte')})"
        return None

    def expected_value(self, value: Any) -> str:
        if isinstance(value, str) and value.startswith("literal:"):
            return self.string(value[len("literal:") :])
        return self.json_value(value)

    def keys_literal(self, keys: list[str | int]) -> str:
        return "[" + ", ".join(self.string(key) if isinstance(key, str) else str(key) for key in keys) + "]"

    def status_condition(self, status: str, codes: list[str]) -> str:
        if len(codes) == 1 and codes[0].isdigit():
            return f"{status} {self.equals} {codes[0]}"
        self.helpers.add("status_in")
        return f"statusIn({status}, {self.keys_literal(list(codes))})" if self.comment == "//" else f"status_in({status}, {self.keys_literal(list(codes))})"

    def negate(self, expr: str) -> str:
        raise NotImplementedError

    equals = "==="

    # ------------------------------------------------------------------ language hooks

    true = "true"
    false = "false"
    null = "null"
    content_frame = ""

    def header(self) -> None:
        raise NotImplementedError

    def uses_base(self) -> bool:
        """True when the code reads BASE_URL (relative addresses)."""
        raise NotImplementedError

    def open_test(self) -> None:
        raise NotImplementedError

    def close_test(self) -> None:
        raise NotImplementedError

    def open_step(self, step: AutomationStep) -> None:
        raise NotImplementedError

    def close_step(self, step: AutomationStep) -> None:
        raise NotImplementedError

    def action(self, step: AutomationStep, action: AutomationAction) -> None:
        raise NotImplementedError

    def check(self, step: AutomationStep, check: AutomationCheck) -> None:
        raise NotImplementedError

    def api_step(self, step: AutomationStep) -> None:
        raise NotImplementedError

    def sql_step(self, step: AutomationStep) -> None:
        raise NotImplementedError

    def quality_step(self, step: AutomationStep) -> None:
        raise NotImplementedError

    def security_step(self, step: AutomationStep) -> None:
        """A security scan as comments: what ran, and the scanner command to run it in a pipeline."""
        scan = step.security or {}
        self.note(f"The run scanned the app for security issues: {json.dumps(scan, ensure_ascii=False)}")
        tags = ",".join(str(tag) for tag in scan.get("tags") or ["cve", "exposure", "misconfig"])  # type: ignore[union-attr]
        self.note(f"To keep it in a pipeline: hercules scan --base-url <url> --tags {tags} --fail-on {scan.get('fail_on', 'medium')} (or run nuclei with -tags {tags})")
        self.todo(step, "a security scan is not test code; run it with `hercules scan` or the nuclei scanner in your pipeline")

    def locator_call(self, spec: LocatorSpec) -> str:
        raise NotImplementedError

    def env_expr(self, name: str) -> str:
        raise NotImplementedError

    def data_expr(self, name: str) -> str:
        raise NotImplementedError

    def saved_expr(self, name: str) -> str:
        raise NotImplementedError

    def text_expr(self, expr: str) -> str:
        raise NotImplementedError

    def dict_literal(self, items: list[tuple[str, str]]) -> str:
        raise NotImplementedError


def env_vars(writer: Writer) -> tuple[EnvVar, ...]:
    """What the exported test reads from the environment: the base URL (optional) and every secret it uses."""
    base = EnvVar(BASE_URL_ENV, False, f"base URL of the app under test (default {writer.spec.base_url or 'none'})") if writer.uses_base() else None
    secrets = tuple(EnvVar(name, True, purpose) for name, purpose in sorted(writer.secrets.items()))
    state = writer.spec.browser_options.storage_state if writer.spec.browser_options and writer.spec.browser else None
    auth = (EnvVar("STORAGE_STATE", False, f"the auth state JSON file the browser starts with (default {state})"),) if state else ()
    return ((base,) if base else ()) + auth + secrets


@dataclass
class Helpers:
    """Helper functions a writer emits only when the code uses them (with the helpers they use), in a fixed order."""

    order: tuple[str, ...]
    source: dict[str, str] = field(default_factory=dict)
    requires: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def closure(self, used: set[str]) -> set[str]:
        needed = set(used)
        pending = list(used)
        while pending:
            for other in self.requires.get(pending.pop(), ()):
                if other not in needed:
                    needed.add(other)
                    pending.append(other)
        return needed

    def emit(self, used: set[str]) -> list[str]:
        needed = self.closure(used)
        lines: list[str] = []
        for name in self.order:
            if name in needed and self.source.get(name, "").strip():
                lines.extend(self.source[name].strip("\n").splitlines())
                lines.append("")
        return lines


_CONTENT_TYPES = frozenset({"pdf", "docx", "xlsx", "zip", "png", "jpeg", "gif"})
_ROW_OPS = {"": "==", "=": "==", "==": "==", "exactly": "==", ">=": ">=", "at least": ">=", "<=": "<=", "at most": "<=", ">": ">", "more than": ">", "<": "<", "fewer than": "<", "less than": "<"}
_ROW_COUNT = re.compile(r"^\s*(>=|<=|==|=|>|<|at least|at most|more than|fewer than|less than|exactly)?\s*(\d[\d,]*)\s*(?:rows?)?\s*$", re.IGNORECASE)


DOWNLOADED_FILE = "{{downloaded_file}}"


def uploads_download(action: Any) -> bool:
    """True when an upload attaches the file an earlier step downloaded (``{{downloaded_file}}``)."""
    return action.kind == "upload" and DOWNLOADED_FILE in "".join((action.value or "").split())


def file_type_kinds(expected: str) -> tuple[str, ...] | None:
    """The file types a check of the named type accepts (``PDF``, ``an Excel file``), each told by its content; None for text types."""
    from testzeus_hercules.act.file_contents import TYPE_NAMES

    words = [word.strip(".").lower() for word in expected.replace("-", " ").split()]
    kinds = set().union(*(TYPE_NAMES.get(word, frozenset()) for word in words)) if words else set()
    if not kinds or kinds - _CONTENT_TYPES:
        return None
    return tuple(sorted(kinds))


def row_comparison(expected: str) -> tuple[str, int] | None:
    """A file_rows check's operator (``==``, ``>=``, ...) and number."""
    found = _ROW_COUNT.match(expected)
    if found is None:
        return None
    return _ROW_OPS[(found.group(1) or "").lower()], int(found.group(2).replace(",", ""))
