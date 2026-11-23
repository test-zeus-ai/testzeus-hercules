"""Recording a run as automation: the pieces of :class:`~testzeus_hercules.cases.automation.AutomationSpec`.

The step graph records what each step did (see :mod:`testzeus_hercules.engine.step_graph`); this module
turns the run's own values into the spec's form: typed values as templates (``{{name}}`` kept, a secret
value never written, only its name), addresses relative to the case's base URL, and, at the end of the
run, the data entries the steps refer to. It only describes; it decides nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from testzeus_hercules.cases.automation import AutomationData, AutomationSpec, AutomationStep
from testzeus_hercules.cases.models import BUILTIN_PLACEHOLDERS, SecretRef, TestCase
from testzeus_hercules.engine.data import URL_CREDENTIAL_PREFIX, CaseData

# ``{{name}}`` (test data, built-ins, saved values) and ``${name}`` (saved values, as in JSONPath checks).
REFERENCE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")
_HIDDEN_SECRET = "the value holds a secret that the case does not reference by name, so it is not recorded"


def value_template(template: str | None, resolved: str | None, data: CaseData) -> tuple[str | None, str | None]:
    """A typed value as a template, and why it could not be recorded (None when it was).

    The template is the value as the step's reading wrote it (placeholders kept). A value that, as
    written, holds a secret's value is recorded as that secret's placeholder when it is exactly the
    secret, and never otherwise.
    """
    text = template if template is not None else resolved
    if text is None:
        return None, None
    if data.redact(text) == text:
        return text, None
    for name, variable in sorted(data.secrets.items()):
        if data.env.get(variable) == text:
            return "{{" + name + "}}", None
    return None, _HIDDEN_SECRET


def relative_url(template: str, base_url: str | None) -> str:
    """An address as written, made relative to ``base_url`` when it is an absolute address on the same origin."""
    parts = urlsplit(template)
    if not base_url or not parts.scheme or parts.scheme not in ("http", "https"):
        return template
    base = urlsplit(base_url)
    if (parts.scheme, parts.netloc) != (base.scheme, base.netloc):
        return template
    return urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))


def navigated_path(before: str, after: str) -> str | None:
    """Where an action navigated to (a different document path): the path on the same origin, else origin and path."""
    old, new = urlsplit(before or ""), urlsplit(after or "")
    if not after or (old.scheme, old.netloc, old.path) == (new.scheme, new.netloc, new.path) or new.scheme not in ("http", "https", "file"):
        return None
    if (old.scheme, old.netloc) == (new.scheme, new.netloc):
        return new.path or "/"
    return urlunsplit((new.scheme, new.netloc, new.path or "/", "", ""))


def navigated_from(before: str, after: str) -> str | None:
    """The page an action navigated away from (see :func:`navigated_path`), written the same way; None without a navigation."""
    if navigated_path(before, after) is None:
        return None
    old, new = urlsplit(before or ""), urlsplit(after or "")
    if (old.scheme, old.netloc) == (new.scheme, new.netloc):
        return old.path or "/"
    return urlunsplit((old.scheme, old.netloc, old.path or "/", "", ""))


def references(texts: Iterable[str]) -> list[str]:
    """The names ``texts`` refer to, in order of first appearance."""
    seen: dict[str, None] = {}
    for text in texts:
        for match in REFERENCE.finditer(text):
            seen.setdefault(match.group(1) or match.group(2), None)
    return list(seen)


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for key, value in node.items() for text in (*_strings(key), *_strings(value))]
    if isinstance(node, (list, tuple)):
        return [text for item in node for text in _strings(item)]
    return []


def _step_texts(step: AutomationStep) -> list[str]:
    texts: list[str] = []
    for action in step.actions:
        texts.extend(text for text in (action.value, action.url, action.option, action.text) if text)
    texts.extend(action.value for action in step.device or () if action.value)
    texts.extend(check.expected for check in step.checks)
    for payload in (step.api, step.sql, step.quality, step.security):
        if payload:
            texts.extend(_strings({key: value for key, value in payload.items() if key != "save"}))
    if step.sql and isinstance(step.sql.get("db"), str):
        texts.append("{{" + step.sql["db"] + "}}")  # the test_data entry holding the connection string
    return texts


def _saved_by(steps: Sequence[AutomationStep]) -> dict[str, int]:
    saved: dict[str, int] = {}
    for step in steps:
        payload = step.api or step.sql or {}
        for capture in payload.get("save") or []:  # type: ignore[union-attr]
            if isinstance(capture, dict) and isinstance(capture.get("name"), str):
                saved.setdefault(capture["name"], step.index)
    return saved


def build_spec(case: TestCase, steps: Sequence[AutomationStep], *, browser: bool, appium: dict[str, Any] | None = None) -> AutomationSpec:
    """The run's automation spec: its steps, and the data entries they refer to (secrets by variable name only).
    ``appium``: a native app run's server (see :meth:`~testzeus_hercules.mobile.session.MobileSession.server_record`)."""
    saved = _saved_by(steps)
    data: dict[str, AutomationData] = {}
    pending = references(text for step in steps for text in _step_texts(step))
    while pending:
        name = pending.pop(0)
        if name in data:
            continue
        value = case.test_data.get(name)
        if name in saved:
            data[name] = AutomationData(kind="saved", step=saved[name])
        elif isinstance(value, SecretRef):
            data[name] = AutomationData(kind="secret", env=value.secret)
        elif isinstance(value, str):
            data[name] = AutomationData(kind="value", value=value)
            # A case's value may use the built-ins (user+{{run_id}}@...); other names in it stay as written (see CaseData).
            pending.extend(other for other in references([value]) if other in BUILTIN_PLACEHOLDERS and other not in data)
        elif name in BUILTIN_PLACEHOLDERS:
            data[name] = AutomationData(kind="builtin")
        elif name.startswith(URL_CREDENTIAL_PREFIX):
            # A credential the run took out of a URL in the case (a session id, a token): the exported test reads it
            # from an environment variable of the same name, never from the code.
            data[name] = AutomationData(kind="secret", env=name.upper())
    environment = case.environment
    return AutomationSpec(
        case_id=case.id,
        title=case.title,
        base_url=environment.base_url,
        viewport=environment.viewport,
        device=environment.device,
        browser_name=environment.browser if environment.mobile is None else None,
        browser_options=environment.browser_options if environment.mobile is None else None,
        app=environment.mobile.model_dump(mode="json", exclude_defaults=True) if environment.mobile is not None else None,
        appium=appium if environment.mobile is not None else None,
        browser=browser,
        data=dict(sorted(data.items())),
        steps=list(steps),
    )
