"""Verification: text, URL and visibility checks recorded in an assertion ledger.

A model reads what a step expects (see :mod:`testzeus_hercules.engine.intent`); the
checks it names are run here in code against what the page shows, and every check
produces one :class:`LedgerEntry`. Expectations no literal check can hold are judged
by a model in the step graph: ``semantic`` (``Step.semantic``, or an outcome the model
could only read by its meaning) on the page text and, when images may be sent, the
screenshot; ``visual`` (how the page looks) on the screenshot. A judgement that is
not confident either way is ``unverified``, never passed.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict

from testzeus_hercules.act.file_contents import FileReadError, file_json, file_table, file_text, file_type, type_matches
from testzeus_hercules.engine.intent import StepIntent

CheckKind = Literal["text", "not_text", "url", "visible", "field", "visual", "semantic", "file_name", "file_type", "file_text", "file_not_text", "file_rows", "file_column", "file_json"]
FILE_CHECK_KINDS: frozenset[str] = frozenset({"file_name", "file_type", "file_text", "file_not_text", "file_rows", "file_column", "file_json"})
LedgerVerdict = Literal["passed", "failed", "unverified"]
Redactor = Callable[[str], str]

_ACTUAL_CLIP = 240
_POLL_S = 0.2


class LedgerEntry(BaseModel):
    """One assertion: what was checked, what was expected and seen, and the verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int
    check: str
    expected: str
    actual: str
    verdict: LedgerVerdict
    source: Literal["deterministic", "llm"] = "deterministic"


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    expected: str
    match: Literal["contains", "equals"] = "contains"
    reason: str = ""
    # File checks: the column (file_column) or JSON path (file_json) checked, and the row a file_column check looks at
    # ("<column> = <value>"; None: every row).
    subject: str | None = None
    where: str | None = None
    # ``expected`` as the step wrote it, placeholders kept (set by ``resolved``; for exporting the run as code).
    template: str | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        if self.kind == "text":
            return "text_contains"
        if self.kind == "not_text":
            return "text_absent"
        if self.kind == "url":
            return f"url_{self.match}"
        if self.kind == "field":
            return f"field_{self.match}"
        if self.kind == "file_name":
            return f"file_name_{self.match}"
        return self.kind

    def resolved(self, resolve: Callable[[str], str]) -> Check:
        return Check(
            kind=self.kind,
            expected=resolve(self.expected),
            match=self.match,
            reason=self.reason,
            subject=resolve(self.subject) if self.subject is not None else None,
            where=resolve(self.where) if self.where is not None else None,
            template=self.template if self.template is not None else self.expected,
        )


def check_from_intent(intent: StepIntent) -> Check | None:
    """The check an assertion intent stands for (``assert_visible`` needs an element, so None)."""
    if intent.kind == "assert_text" and intent.expected is not None:
        return Check(kind="text", expected=intent.expected)
    if intent.kind == "assert_not_text" and intent.expected is not None:
        return Check(kind="not_text", expected=intent.expected)
    if intent.kind == "assert_url" and intent.expected is not None:
        return Check(kind="url", expected=intent.expected, match=intent.match or "contains")
    if intent.kind == "assert_looks" and intent.expected is not None:
        return Check(kind="visual", expected=intent.expected, reason="an expectation about how the page looks is judged on a screenshot")
    if intent.kind in _FILE_ASSERTS and intent.expected is not None:
        kind = _FILE_ASSERTS[intent.kind]
        if kind in ("file_column", "file_json") and not (intent.target or "").strip():
            return None
        match = intent.match or ("equals" if kind in ("file_name", "file_column", "file_json") else "contains")
        return Check(kind=kind, expected=intent.expected, match=match, subject=intent.target, where=intent.value)  # type: ignore[arg-type]
    return None


_FILE_ASSERTS: dict[str, str] = {
    "assert_file_name": "file_name",
    "assert_file_type": "file_type",
    "assert_file_text": "file_text",
    "assert_file_not_text": "file_not_text",
    "assert_file_rows": "file_rows",
    "assert_file_column": "file_column",
    "assert_file_json": "file_json",
}


# 'the "First Name" field contains "Ada"', '"Email" input should be "a@b.c"', 'the "City" box shows "Pune"': a check on what one
# named field holds, which the page text never includes (a typed value is not text on the page).
_FIELD_CHECK = re.compile(
    r'"(?P<field>[^"]+)"\s+(?:field|input|text\s?box|box|textarea|text\s+area|drop-?\s?down|select|selector|list|picklist|combo\s?box)\s+'
    r"(?P<verb>contains|has|holds|shows|displays|reads|equals|is|should\s+(?:contain|have|hold|show|display|read|equal|be)|has\s+the\s+value(?:\s+of)?)\s+"
    r'"(?P<value>[^"]*)"',
    re.IGNORECASE,
)
_EXACT_VERBS = ("equals", "is", "should equal", "should be")


def field_check(text: str, check: Check) -> Check | None:
    """A text check upgraded to a check on a named field's value when the step says the field holds the text
    ('the "First Name" field contains "Ada"'); None when the step names no field for this text."""
    if check.kind != "text":
        return None
    for found in _FIELD_CHECK.finditer(text):
        if _norm(found.group("value")) == _norm(check.expected) and found.group("field").strip():
            verb = " ".join(found.group("verb").lower().split())
            match: Literal["contains", "equals"] = "equals" if verb in _EXACT_VERBS else "contains"
            return Check(kind="field", expected=check.expected, match=match, subject=found.group("field").strip(), template=check.template)
    return None


def _field_holds(value: str, wanted: str, match: str) -> bool:
    held, expected = _norm(value), _norm(wanted)
    return held == expected if match == "equals" else expected in held


def semantic_check(text: str) -> Check:
    """An expected outcome judged by its meaning (a model compares it with the page)."""
    return Check(kind="semantic", expected=text, reason="judged by meaning")


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _ACTUAL_CLIP else flat[: _ACTUAL_CLIP - 1] + "…"


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _entry(step: int, check: Check, actual: str, verdict: LedgerVerdict, redact: Redactor) -> LedgerEntry:
    return LedgerEntry(step=step, check=check.label, expected=redact(check.expected), actual=redact(actual), verdict=verdict)


async def _poll(predicate: Callable[[], Any], timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if await predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_POLL_S)


async def run_check(
    check: Check,
    *,
    page: Any,
    actions: Any,
    step: int,
    timeout_ms: int,
    redact: Redactor,
    snapshot: Callable[[], Awaitable[Any]] | None = None,
    reveal: Callable[[Any, int], Awaitable[str | None]] | None = None,
    entered: Callable[[str], Awaitable[tuple[str, bool] | None]] | None = None,
) -> LedgerEntry:
    """Run one check against the page, waiting up to ``timeout_ms`` for it to hold. ``snapshot`` (a fresh perception of the
    page) lets a field check find the field by the name the run perceives."""
    if check.kind in ("semantic", "visual"):  # judged by a model in the step graph, never by matching text
        return _entry(step, check, check.reason or "not verified", "unverified", redact)
    if check.kind == "url":
        return await _check_url(check, page=page, step=step, timeout_ms=timeout_ms, redact=redact)
    if check.kind == "field":
        return await _check_field(check, page=page, actions=actions, step=step, timeout_ms=timeout_ms, redact=redact, snapshot=snapshot, reveal=reveal, entered=entered)
    if check.kind == "visible":
        ok = await actions.text_visible(page, check.expected, timeout_ms=timeout_ms)
        return _entry(step, check, "visible" if ok else "no visible element shows this text", "passed" if ok else "failed", redact)
    wanted = _norm(check.expected)
    present = check.kind == "text"
    seen = {"text": ""}
    drop_downs = getattr(actions, "closed_drop_down_texts", None)

    async def holds() -> bool:
        seen["text"] = await actions.page_text(page)
        hidden = await drop_downs(page) if callable(drop_downs) and wanted in _norm(seen["text"]) else []
        return _shown(wanted, _norm(seen["text"]), hidden) == present

    if await _poll(holds, timeout_ms):
        return _entry(step, check, "shown" if present else "not shown", "passed", redact)
    if present:
        return _entry(step, check, f'not found; page text: "{_clip(seen["text"])}"', "failed", redact)
    return _entry(step, check, "the text is shown on the page", "failed", redact)


def _shown(wanted: str, text: str, drop_downs: list[tuple[str, str]]) -> bool:
    """True when ``wanted`` occurs in the page text other than among a closed drop-down's unchosen options.

    innerText lists every option of a closed drop-down although only the chosen one is shown; a match
    that runs on past the drop-down, or occurs elsewhere too, still counts.
    """
    found = text.count(wanted)
    if not found:
        return False
    hidden = sum(_norm(options).count(wanted) for options, chosen in drop_downs if wanted not in _norm(chosen))
    return found > hidden


async def _check_field(
    check: Check,
    *,
    page: Any,
    actions: Any,
    step: int,
    timeout_ms: int,
    redact: Redactor,
    snapshot: Callable[[], Awaitable[Any]] | None = None,
    reveal: Callable[[Any, int], Awaitable[str | None]] | None = None,
    entered: Callable[[str], Awaitable[tuple[str, bool] | None]] | None = None,
) -> LedgerEntry:
    """The field named ``check.subject`` holds ``check.expected`` (contains, or equals), waiting up to ``timeout_ms``.

    The field is, first, the one an earlier step of the run filled or chose in under this name (``entered``). Else it is found as the run
    perceives the page (``snapshot``: named from its label, or from the text beside it when no label is linked), else by
    its linked label or accessible name."""
    field = check.subject or ""
    if entered is not None:
        known = await _check_entered(check, field, entered, step=step, timeout_ms=timeout_ms, redact=redact)
        if known is not None:
            return known
    labelled = getattr(actions, "field_values", None)
    hidden = {"any": False}  # a password field: its value is compared, never shown

    async def read(page: Any, name: str) -> list[str] | None:
        if snapshot is not None:
            from testzeus_hercules.engine.criteria import _VALUE_ROLES, _named  # criteria imports this module

            view = await snapshot()
            found = _named(view, name, _VALUE_ROLES)
            if found:
                values = []
                for element in found:
                    secret = str((getattr(element, "attrs", {}) or {}).get("type", "")).lower() == "password"
                    hidden["any"] = hidden["any"] or secret
                    # The snapshot masks a password's value ("***"): read what the field really holds.
                    value = await reveal(view, element.md) if secret and reveal is not None else getattr(element, "value", "")
                    values.append(str(value or ""))
                return values
        return await labelled(page, name) if callable(labelled) else None

    if snapshot is None and not callable(labelled):  # a driver that cannot read fields by name (an app screen): what is shown counts
        entry = await run_check(Check(kind="text", expected=check.expected), page=page, actions=actions, step=step, timeout_ms=timeout_ms, redact=redact)
        return _entry(step, check, f"{entry.actual} (the field's value cannot be read here; checked the screen's text)", entry.verdict, redact)
    seen: dict[str, list[str] | None] = {"held": None}

    async def holds() -> bool:
        seen["held"] = await read(page, field)
        return any(_field_holds(value, check.expected, check.match) for value in seen["held"] or [])

    if await _poll(holds, timeout_ms):
        shown = "the expected value (a password: not shown)" if hidden["any"] else _shown_values(seen["held"])
        return _entry(step, check, f'the "{field}" field holds {shown}', "passed", redact)
    if seen["held"] is None:  # no field goes by this name (a drop-down without a label of its own): what is shown counts
        entry = await run_check(Check(kind="text", expected=check.expected), page=page, actions=actions, step=step, timeout_ms=timeout_ms, redact=redact)
        return _entry(step, check, f'no field named "{field}" on the page; checked the page text instead: {entry.actual}', entry.verdict, redact)
    shown = "another value (a password: not shown)" if hidden["any"] else _shown_values(seen["held"])
    return _entry(step, check, f'the "{field}" field holds {shown}', "failed", redact)


async def _check_entered(check: Check, field: str, entered: Callable[[str], Awaitable[tuple[str, bool] | None]], *, step: int, timeout_ms: int, redact: Redactor) -> LedgerEntry | None:
    """The check on the field a step of the run entered under ``field``; None when no step did (or it cannot be read)."""
    seen: dict[str, Any] = {"read": None}

    async def holds() -> bool:
        seen["read"] = await entered(field)
        return seen["read"] is not None and _field_holds(seen["read"][0], check.expected, check.match)

    passed = await _poll(holds, timeout_ms)
    if seen["read"] is None:
        return None
    held, password = seen["read"]
    if password:
        shown = "the expected value" if passed else "another value"
        return _entry(step, check, f'the "{field}" field holds {shown} (a password: not shown)', "passed" if passed else "failed", redact)
    return _entry(step, check, f'the "{field}" field holds "{_clip(held)}"', "passed" if passed else "failed", redact)


def _shown_values(values: list[str] | None) -> str:
    return ", ".join(f'"{_clip(value)}"' for value in values or []) or "nothing"


async def _check_url(check: Check, *, page: Any, step: int, timeout_ms: int, redact: Redactor) -> LedgerEntry:
    async def holds() -> bool:
        url = str(page.url)
        return url == check.expected if check.match == "equals" else check.expected in url

    ok = await _poll(holds, timeout_ms)
    return _entry(step, check, str(page.url), "passed" if ok else "failed", redact)


async def verify_visible(locator: Any | None, target: str, *, page: Any, actions: Any, step: int, timeout_ms: int, redact: Redactor) -> LedgerEntry:
    """The chosen element is visible; without one, some element showing ``target`` is."""
    check = Check(kind="visible", expected=target)
    if locator is None:
        return await run_check(check, page=page, actions=actions, step=step, timeout_ms=timeout_ms, redact=redact)
    ok = await actions.is_visible(locator, timeout_ms=timeout_ms)
    return _entry(step, check, "visible" if ok else "the element is present but not visible", "passed" if ok else "failed", redact)


_COUNT = re.compile(r"^\s*(>=|<=|==|=|>|<|at least|at most|more than|fewer than|less than|exactly)?\s*(\d[\d,]*)\s*(?:rows?)?\s*$", re.IGNORECASE)
_COMPARE: dict[str, Callable[[int, int], bool]] = {
    ">=": lambda seen, wanted: seen >= wanted,
    "at least": lambda seen, wanted: seen >= wanted,
    "<=": lambda seen, wanted: seen <= wanted,
    "at most": lambda seen, wanted: seen <= wanted,
    ">": lambda seen, wanted: seen > wanted,
    "more than": lambda seen, wanted: seen > wanted,
    "<": lambda seen, wanted: seen < wanted,
    "fewer than": lambda seen, wanted: seen < wanted,
    "less than": lambda seen, wanted: seen < wanted,
}


def _same(seen: str, wanted: str, match: str) -> bool:
    return _norm(wanted) in _norm(seen) if match == "contains" else _norm(seen) == _norm(wanted)


def _shown_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _column(names: list[str], wanted: str, file: Path) -> int:
    found = next((index for index, name in enumerate(names) if _norm(name) == _norm(wanted)), None)
    if found is None:
        raise FileReadError(f"{file.name} has no column {wanted!r} (columns: {', '.join(names)})")
    return found


def _file_verdict(check: Check, file: Path) -> tuple[bool, str]:
    """Whether the check holds on ``file``, and what was seen."""
    if check.kind == "file_name":
        return _same(file.name, check.expected, check.match), file.name
    if check.kind == "file_type":
        kind = file_type(file)
        return type_matches(kind, check.expected), f"a {kind} file"
    if check.kind in ("file_text", "file_not_text"):
        text = file_text(file)
        found = _norm(check.expected) in _norm(text)
        if check.kind == "file_text":
            return found, "found in the file" if found else f'not found; the file starts: "{_clip(text)}"'
        return not found, "the file holds this text" if found else "not in the file"
    if check.kind == "file_rows":
        parsed = _COUNT.match(check.expected)
        if parsed is None:
            raise FileReadError(f"{check.expected!r} is not a number of rows")
        _, rows = file_table(file)
        wanted = int(parsed.group(2).replace(",", ""))
        compare = _COMPARE.get((parsed.group(1) or "").lower(), lambda seen, number: seen == number)
        return compare(len(rows), wanted), f"{len(rows)} rows"
    if check.kind == "file_column":
        names, rows = file_table(file)
        column = _column(names, check.subject or "", file)
        picked = rows
        if check.where:
            key, _, value = check.where.partition("=")
            by = _column(names, key.strip(), file)
            picked = [row for row in rows if by < len(row) and _norm(row[by]) == _norm(value)]
            if not picked:
                return False, f"no row has {key.strip()} = {value.strip()}"
        if not picked:
            return False, "the file has no data rows"
        cells = [row[column] if column < len(row) else "" for row in picked]
        wrong = [cell for cell in cells if not _same(cell, check.expected, check.match)]
        if wrong:
            return False, f"{len(wrong)} of {len(cells)} rows differ, for example {wrong[0]!r}"
        return True, f"{len(cells)} row{'s' if len(cells) != 1 else ''} hold it"
    if check.kind == "file_json":
        from testzeus_hercules.export.common import json_path_keys

        keys = json_path_keys(check.subject or "")
        if keys is None:
            raise FileReadError(f"{check.subject!r} is not a JSON path of names and indexes")
        value: Any = file_json(file)
        for key in keys:
            try:
                value = value[key]
            except (KeyError, IndexError, TypeError):
                return False, f"the file has no value at {check.subject}"
        shown = _shown_value(value)
        return _same(shown, check.expected, check.match), shown
    raise FileReadError(f"unknown file check {check.kind}")


def run_file_check(check: Check, downloads: Sequence[Path], *, step: int, redact: Redactor) -> LedgerEntry:
    """Run one file check on the run's latest downloaded file, in code; the file never reaches a model."""
    if not downloads:
        return _entry(step, check, "no file was downloaded before this check", "failed", redact)
    file = downloads[-1]
    try:
        held, seen = _file_verdict(check, file)
    except (FileReadError, OSError) as exc:
        return _entry(step, check, str(exc), "failed", redact)
    return _entry(step, check, f"{file.name}: {seen}", "passed" if held else "failed", redact)
