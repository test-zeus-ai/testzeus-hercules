"""API, SQL and security steps: run the tools directly and judge the results in code.

A :class:`ServiceSession` lives for one run. A step's ``api`` or ``sql`` payload is
executed as written; a step without one is read into that payload by the model
(:mod:`~testzeus_hercules.engine.service_intent`) when the case is tagged
``kind:api``/``kind:sql`` or the step interpreter says the step is an API/SQL step.

* ``api``: the request is sent (relative paths joined to the case's ``base_url``, its
  path prefix kept); each ``expect`` check (status, JSON Schema, OpenAPI operation,
  JSONPath, header, body text, time) judges the response and adds one ledger entry.
  Without ``path`` the checks apply to the previous response.
* ``sql``: one read-only SELECT on the connection string ``test_data[db]`` holds
  (normally a secret reference); ``expect`` checks the row count and JSONPaths over the rows.
* ``security``: a scan of the case's own app with the nuclei scanner (see
  :mod:`~testzeus_hercules.engine.security`); findings at or above ``fail_on`` fail the step.
* ``save`` keeps values for later steps as ``{{name}}`` (an exact ``"{{name}}"`` JSON
  value keeps the saved value's type).

A check that cannot be evaluated (bad JSONPath, missing spec, no response yet) is a
failed ledger entry and makes the step ``error``, never a pass. A step whose
``expected`` text has no structured check is ``unverified``. Requests, responses,
queries and check details are written as redacted JSON evidence next to the step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

from testzeus_hercules.act import api_assert, api_schema, jsonpath
from testzeus_hercules.act.api import HttpRequest, HttpResponse, auth_headers, exchange_record, send
from testzeus_hercules.act.files import PathConfinementError, confine
from testzeus_hercules.act.redact import REDACTED, is_secret_name, redact_url, redact_value
from testzeus_hercules.act.sql import MAX_ROWS, SqlResult, select
from testzeus_hercules.cases.gherkin import VERIFY_ACTION
from testzeus_hercules.cases.importers.openapi import SPECS_DIRNAME
from testzeus_hercules.cases.models import ApiCall, ApiExpect, Capture, HeaderCheck, JsonCheck, SecurityScan, SqlCall, Step, StepStatus
from testzeus_hercules.cases.security import SecurityScanResult
from testzeus_hercules.engine.data import CaseData, MissingSecretError
from testzeus_hercules.engine.evidence import EvidenceStore
from testzeus_hercules.engine.security import run_security_step
from testzeus_hercules.engine.service_intent import ServiceInterpreter, ServicePlan
from testzeus_hercules.engine.verify import LedgerEntry
from testzeus_hercules.kernel.run_context import EventSink, NullEventSink

_CLIP = 300
_EVIDENCE_ROWS = 50
_EXACT_PLACEHOLDER = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
_LEFTOVER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


@dataclass(frozen=True)
class ServiceReading:
    """A step to run as an API or SQL call or a security scan (or why it cannot be)."""

    call: ApiCall | SqlCall | SecurityScan | None = None
    source: Literal["payload", "llm"] = "payload"
    problem: str | None = None
    unverified: str | None = None  # the step's ``expected`` text, which no structured check covers


@dataclass(frozen=True)
class ServiceOutcome:
    status: StepStatus
    actual: str | None
    ledger: tuple[LedgerEntry, ...] = ()
    evidence: tuple[str, ...] = ()
    error_class: str | None = None
    actions: int = 0
    security: SecurityScanResult | None = None


class _StepFailure(Exception):
    def __init__(self, status: StepStatus, message: str, error_class: str) -> None:
        super().__init__(message)
        self.status, self.message, self.error_class = status, message, error_class


@dataclass
class _Exchange:
    request: HttpRequest
    response: HttpResponse
    uri: str


@dataclass
class _Query:
    result: SqlResult
    uri: str


class ServiceSession:
    """Per-run state of API and SQL steps: the last response and the last rows."""

    def __init__(
        self,
        *,
        data: CaseData,
        evidence: EvidenceStore,
        project_dir: Path,
        base_url: str | None = None,
        interpreter: ServiceInterpreter | None = None,
        hint: str | None = None,
        events: EventSink | None = None,
        timeout_s: float = 30.0,
        transport: Any = None,
        sql_engines: dict[str, Callable[..., Any]] | None = None,
        env: Mapping[str, str] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.data, self.evidence, self.project_dir, self.base_url = data, evidence, project_dir, base_url
        self.interpreter = interpreter or ServiceInterpreter()
        self.hint = hint
        self.events = events or NullEventSink()
        self.timeout_s = timeout_s
        self.transport = transport
        self.sql_engines = sql_engines or {}
        self.env: Mapping[str, str] = env if env is not None else {}
        self.cache_dir = cache_dir
        self.exchange: _Exchange | None = None
        self.query: _Query | None = None
        self._specs: dict[Path, tuple[float, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ reading

    def handles(self, step: Step) -> bool:
        """True when the step runs without a browser: it has an api, sql or security payload, or the case is an API/SQL case."""
        return step.api is not None or step.sql is not None or step.security is not None or self.hint is not None

    async def read(self, step: Step) -> ServiceReading | None:
        """The step as a call: its payload; in an API/SQL case the model's reading; otherwise None (not decided here)."""
        if step.security is not None:
            return ServiceReading(call=step.security)  # its fail_on gate checks the step's expected outcome
        call = step.api or step.sql
        if call is not None:
            return ServiceReading(call=call, unverified=_unverified(step, call))
        if self.hint is None:
            return None
        return await self.interpret(step)

    async def interpret(self, step: Step) -> ServiceReading:
        """Ask the model for the step's payload (also for a step the step interpreter classified as API/SQL)."""
        text = _text_of(step)
        if not self.interpreter.has_llm:
            return ServiceReading(problem="this API/SQL step has no structured api/sql payload and no model is configured to read its text", source="llm")
        plan = await self.interpreter.interpret(text, hint=self.hint)
        call = plan.call() if isinstance(plan, ServicePlan) else None
        if call is None:
            return ServiceReading(problem="the model could not read this step as an API or SQL call; give it a structured api or sql payload", source="llm")
        expected_covered = _has_checks(call) or step.expected is None or step.action.strip().lower() == VERIFY_ACTION
        return ServiceReading(call=call, source="llm", unverified=None if expected_covered else step.expected)

    # ------------------------------------------------------------------ running

    async def run(self, index: int, reading: ServiceReading) -> ServiceOutcome:
        """Execute step ``index`` (1-based); never raises for step problems."""
        if reading.problem is not None or reading.call is None:
            return ServiceOutcome(status="error", actual=reading.problem or "nothing to run", error_class="UnreadableStep")
        if isinstance(reading.call, SecurityScan):
            return await self._security(index, reading.call)
        ledger: list[LedgerEntry] = []
        evidence: list[str] = []
        actual: str | None = None
        guard = reading.source == "llm"
        call = reading.call
        actions = 1 if (isinstance(call, ApiCall) and call.path) or (isinstance(call, SqlCall) and call.query) else 0
        try:
            if isinstance(reading.call, ApiCall):
                actual = await self._api(index, reading.call, ledger, evidence, guard)
            else:
                actual = await self._sql(index, reading.call, ledger, evidence, guard)
        except _StepFailure as failure:
            if ledger:
                evidence.append(self._save_checks(index, ledger))
            return ServiceOutcome(status=failure.status, actual=self.data.redact(failure.message), ledger=tuple(ledger), evidence=tuple(evidence), error_class=failure.error_class, actions=actions)
        except MissingSecretError as exc:
            return ServiceOutcome(status="error", actual=str(exc), ledger=tuple(ledger), evidence=tuple(evidence), error_class="MissingSecret", actions=actions)
        if ledger:
            evidence.append(self._save_checks(index, ledger))
        failed = next((entry for entry in ledger if entry.verdict == "failed"), None)
        if failed is not None:
            status: StepStatus = "error" if failed.actual.startswith("error: ") else "failed"
            message = self.data.redact(f"expected {failed.check} {failed.expected}: {failed.actual}")
            return ServiceOutcome(status=status, actual=message, ledger=tuple(ledger), evidence=tuple(evidence), error_class="CheckError" if status == "error" else "AssertionFailed", actions=actions)
        if reading.unverified is not None:
            note = "not verified: the expected outcome has no structured check (add api.expect or sql.expect)"
            entry = LedgerEntry(step=index, check="semantic", expected=self.data.redact(reading.unverified), actual=note, verdict="unverified")
            return ServiceOutcome(status="unverified", actual=note, ledger=(*ledger, entry), evidence=tuple(evidence), actions=actions)
        return ServiceOutcome(status="passed", actual=self.data.redact(actual) if actual else None, ledger=tuple(ledger), evidence=tuple(evidence), actions=actions)

    async def _security(self, index: int, scan: SecurityScan) -> ServiceOutcome:
        outcome = await run_security_step(
            index,
            scan,
            data=self.data,
            evidence=self.evidence,
            project_dir=self.project_dir,
            base_url=self.base_url,
            env=self.env,
            cache_dir=self.cache_dir,
            events=self.events,
        )
        return ServiceOutcome(status=outcome.status, actual=outcome.actual, ledger=outcome.ledger, evidence=outcome.evidence, error_class=outcome.error_class, actions=1, security=outcome.result)

    # ------------------------------------------------------------------ values

    def _text(self, raw: str, where: str) -> str:
        resolved = self.data.resolve(raw)
        leftover = _LEFTOVER.search(resolved)
        if leftover:
            raise _StepFailure("error", f"{where}: unknown placeholder {{{{{leftover.group(1)}}}}}", "MissingData")
        return resolved

    def _value(self, node: Any, where: str) -> Any:
        """A JSON value with placeholders resolved; an exact ``"{{name}}"`` string becomes the saved value with its type."""
        if isinstance(node, dict):
            return {self._text(str(key), where): self._value(item, where) for key, item in node.items()}
        if isinstance(node, list):
            return [self._value(item, where) for item in node]
        if isinstance(node, str):
            exact = _EXACT_PLACEHOLDER.match(node.strip())
            if exact and exact.group(1) in self.data.captured:
                return self.data.captured[exact.group(1)]
            return self._text(node, where)
        return node

    # ------------------------------------------------------------------ HTTP

    def _url(self, raw: str) -> str:
        url = self._text(raw, "path").strip()
        scheme = urlsplit(url).scheme
        if scheme in ("http", "https"):
            return url
        if scheme:
            raise _StepFailure("error", f"only http and https URLs can be requested (got {scheme}:)", "InvalidRequest")
        if not self.base_url:
            raise _StepFailure("error", f"the relative path {url!r} needs the case's environment.base_url (or --base-url)", "InvalidRequest")
        return self.base_url.rstrip("/") + "/" + url.lstrip("/")

    def _request(self, call: ApiCall) -> HttpRequest:
        headers = [(self._text(name, "headers"), self._text(value, "headers")) for name, value in call.headers.items()]
        if call.auth is not None:
            extra, problem = auth_headers(call.auth.type, self._text(call.auth.value, "auth"))
            if problem:
                raise _StepFailure("error", problem, "InvalidRequest")
            headers.extend(extra.items())
        query: list[tuple[str, Any]] = [(self._text(name, "query"), self._value(value, "query")) for name, value in call.query.items()]
        body = self._value(call.body, "body") if call.body is not None else None
        return HttpRequest(method=call.method, url=self._url(call.path or ""), headers=tuple(headers), query=tuple(query), body=body, body_mode=call.body_mode)  # type: ignore[arg-type]

    async def _api(self, index: int, call: ApiCall, ledger: list[LedgerEntry], evidence: list[str], guard: bool) -> str | None:
        actual: str | None = None
        if call.path is not None:
            request = self._request(call)
            response = await send(request, timeout_s=self.timeout_s, roots=self._roots(), transport=self.transport)
            uri = self.evidence.save_json(index, "http.json", exchange_record(request, response))
            evidence.append(uri)
            self.events.emit("action", step=index, action="http_request", ok=response.ok)
            self.exchange = _Exchange(request=request, response=response, uri=uri)
            shown = f"{request.method} {redact_url(response.url)}"
            if not response.ok:
                raise _StepFailure("error", f"{shown} failed: {response.error}", "RequestFailed")
            actual = f"{shown} -> {response.status} {response.reason} in {response.duration_ms} ms"
        elif self.exchange is not None:
            evidence.append(self.exchange.uri)
        if call.expect is not None:
            ledger.extend(self._api_checks(index, call.expect, guard))
        saved = [self._save(capture, "response") for capture in call.save]
        return "; ".join(item for item in (actual, *saved) if item) or None

    def _roots(self) -> tuple[Path, ...]:
        hercules = self.project_dir / ".hercules"
        return (hercules / SPECS_DIRNAME, hercules, self.project_dir)

    # ------------------------------------------------------------------ SQL

    async def _sql(self, index: int, call: SqlCall, ledger: list[LedgerEntry], evidence: list[str], guard: bool) -> str | None:
        actual: str | None = None
        if call.query is not None:
            database = call.db or ""
            connection = self.data.lookup(database)
            if not connection:
                raise _StepFailure("error", f'unknown database "{database}": add test_data.{database} with its connection string, for example {{"secret": "APP_DB_URL"}}', "MissingData")
            query = self._text(call.query, "query")
            params = {name: self._value(value, "params") for name, value in call.params.items()}
            result = await select(
                connection,
                query,
                params=params,
                base_dir=self.project_dir,
                secrets=self.data.secret_values(),
                make_engine=self.sql_engines.get("sync"),
                make_async_engine=self.sql_engines.get("async"),
            )
            record = {
                "database": database,
                "query": query,
                "dialect": result.dialect,
                "columns": list(result.columns),
                "row_count": len(result.rows),
                "truncated": result.truncated,
                "rows": [{key: redact_value(value, key) for key, value in row.items()} for row in result.rows[:_EVIDENCE_ROWS]],
                "duration_ms": result.duration_ms,
                "error": result.error,
            }
            uri = self.evidence.save_json(index, "sql.json", record)
            evidence.append(uri)
            self.events.emit("action", step=index, action="sql_query", ok=result.ok)
            self.query = _Query(result=result, uri=uri)
            if not result.ok:
                raise _StepFailure("error", f'the query on "{database}" failed: {result.error}', "QueryRefused" if result.error_kind == "refused" else "QueryFailed")
            more = f" (first {MAX_ROWS} kept)" if result.truncated else ""
            actual = f'{len(result.rows)} row(s){more} from "{database}" in {result.duration_ms} ms'
        elif self.query is not None:
            evidence.append(self.query.uri)
        if call.expect is not None:
            expect = call.expect
            for operator, bound in (("eq", expect.rows), ("gte", expect.min_rows), ("lte", expect.max_rows)):
                if bound is not None:
                    ledger.append(self._checked(index, f"row_count_{operator}", str(bound), partial(self._row_count, index, operator, bound)))
            for check in expect.json_path:
                ledger.append(self._checked(index, f"sql_{check.op}", check.path, partial(self._json_check, index, check, "query", guard)))
        saved = [self._save(capture, "query") for capture in call.save]
        return "; ".join(item for item in (actual, *saved) if item) or None

    # ------------------------------------------------------------------ saves

    def _save(self, capture: Capture, subject: str) -> str:
        if capture.header is not None:
            value: Any = self._need_exchange().response.header(capture.header)
            if value is None:
                raise _StepFailure("failed", f'nothing to save as {{{{{capture.name}}}}}: the response has no header "{capture.header}"', "AssertionFailed")
        else:
            document = self._document(subject)
            path = self._text(capture.json_path or "$", "save")
            try:
                matches = jsonpath.find(document, path)
            except ImportError as exc:
                raise _StepFailure("error", str(exc), "CheckError") from None
            except Exception as exc:
                raise _StepFailure("error", f"invalid JSONPath '{path}': {exc}", "CheckError") from None
            if not matches:
                raise _StepFailure("failed", f"nothing to save as {{{{{capture.name}}}}}: '{path}' matched nothing", "AssertionFailed")
            value = matches[0]
        self.data.capture(capture.name, value)
        shown = REDACTED if is_secret_name(capture.name) else _clip(api_assert.stringify(value))
        return f"saved {shown} as {{{{{capture.name}}}}}"

    # ------------------------------------------------------------------ checks

    def _need_exchange(self) -> _Exchange:
        if self.exchange is None:
            raise _StepFailure("failed", "no request has been sent in this case yet", "NoResponse")
        return self.exchange

    def _need_query(self) -> _Query:
        if self.query is None:
            raise _StepFailure("failed", "no query has been run in this case yet", "NoResponse")
        return self.query

    def _document(self, subject: str) -> Any:
        if subject == "query":
            return [dict(row) for row in self._need_query().result.rows]
        response = self._need_exchange().response
        try:
            return response.json()
        except ValueError as exc:
            raise _StepFailure("failed", f"{exc} (content type {response.content_type or 'none'}): {_clip(response.text)}", "AssertionFailed") from None

    def _checked(self, index: int, label: str, expected: str, run: Callable[[], LedgerEntry]) -> LedgerEntry:
        """Run one check; one that cannot start (no response yet, a body that is not JSON) is recorded as failed."""
        try:
            return run()
        except _StepFailure as failure:
            return self._entry(index, label, expected, ("error: " if failure.status == "error" else "") + failure.message, False)

    def _api_checks(self, index: int, expect: ApiExpect, guard: bool) -> list[LedgerEntry]:
        entries: list[LedgerEntry] = []
        if expect.status is not None:
            wanted = [str(code).lower() for code in (expect.status if isinstance(expect.status, list) else [expect.status])]
            entries.append(self._checked(index, "http_status", " or ".join(wanted), lambda: self._status(index, wanted)))
        if expect.json_schema is not None:
            shown = expect.json_schema if isinstance(expect.json_schema, str) else "inline schema"
            entries.append(self._checked(index, "json_schema", shown, lambda: self._from_verdict(index, "json_schema", shown, self._schema_verdict(expect.json_schema))))
        if expect.openapi is not None:
            entries.append(
                self._checked(index, "openapi_response", expect.openapi, lambda: self._from_verdict(index, "openapi_response", expect.openapi or "", self._openapi_verdict(expect.openapi or "")))
            )
        for check in expect.json_path:
            entries.append(self._checked(index, f"json_{check.op}", check.path, partial(self._json_check, index, check, "response", guard)))
        for header in expect.headers:
            entries.append(self._checked(index, f"header_{header.op}", header.name, partial(self._header_check, index, header)))
        for text in expect.body_contains:
            entries.append(self._checked(index, "body_contains", text, partial(self._body_contains, index, text)))
        if expect.max_time_ms is not None:
            limit = expect.max_time_ms
            entries.append(self._checked(index, "response_time_lt", f"{limit} ms", lambda: self._time(index, limit)))
        return entries

    def _status(self, index: int, wanted: list[str]) -> LedgerEntry:
        response = self._need_exchange().response
        status = response.status or 0
        passed = any(str(status) == code or (code.endswith("xx") and str(status)[:1] == code[:1]) for code in wanted)
        actual = f"{status} {response.reason}".strip()
        if not passed and response.content:
            actual += f": {_clip(response.text)}"
        return self._entry(index, "http_status", " or ".join(wanted), actual, passed)

    def _time(self, index: int, limit: int) -> LedgerEntry:
        response = self._need_exchange().response
        return self._entry(index, "response_time_lt", f"{limit} ms", f"{response.duration_ms} ms", response.duration_ms < limit)

    def _row_count(self, index: int, operator: str, wanted: int) -> LedgerEntry:
        result = self._need_query().result
        count = len(result.rows)
        label = f"row_count_{operator}"
        if result.truncated and not (operator == "gte" and wanted <= count) and not (operator in ("eq", "lte") and wanted < count):
            return self._entry(index, label, str(wanted), f"error: more than {MAX_ROWS} rows; the count is not exact", False)
        passed = count == wanted if operator == "eq" else count >= wanted if operator == "gte" else count <= wanted
        return self._entry(index, label, str(wanted), f"{f'more than {MAX_ROWS}' if result.truncated else count} row(s)", passed)

    def _expected(self, check: JsonCheck | HeaderCheck) -> Any:
        if "value" not in check.model_fields_set and check.value is None:
            return api_assert.NOT_GIVEN
        return self._value(check.value, "expected value")

    def _json_check(self, index: int, check: JsonCheck, subject: str, guard: bool) -> LedgerEntry:
        document = self._document(subject)
        path = self._text(check.path, "json_path")
        verdict = api_assert.evaluate(document, path, check.op, self._expected(check), variables=self.data.captured, guard=guard)
        label = f"{'sql' if subject == 'query' else 'json'}_{check.op}"
        return self._from_verdict(index, label, _shown(path, verdict, check.op), verdict, secret=is_secret_name(path))

    def _header_check(self, index: int, check: HeaderCheck) -> LedgerEntry:
        value = self._need_exchange().response.header(self._text(check.name, "header name"))
        verdict = api_assert.evaluate({} if value is None else {"value": value}, "$.value", check.op, self._expected(check), guard=False)
        return self._from_verdict(index, f"header_{check.op}", _shown(check.name, verdict, check.op), verdict, secret=is_secret_name(check.name))

    def _body_contains(self, index: int, text: str) -> LedgerEntry:
        wanted = self._text(text, "body_contains")
        body = self._need_exchange().response.text
        return self._entry(index, "body_contains", wanted, "found" if wanted in body else f"not found in: {_clip(body)}", wanted in body)

    def _schema_verdict(self, schema: str | dict[str, Any] | None) -> dict[str, Any]:
        document = self._document("response")
        if isinstance(schema, dict):
            return api_schema.validate(document, self._value(schema, "json_schema"))
        name, _, fragment = self._text(schema or "", "json_schema").partition("#")
        try:
            spec = self._load_spec(name)
            target = api_schema.pointer(spec, fragment) if fragment else spec
        except (PathConfinementError, api_schema.SpecError) as exc:
            return {"assertion": "ERROR", "reason": str(exc)}
        return api_schema.validate(document, target, root=spec)

    def _openapi_verdict(self, name: str) -> dict[str, Any]:
        exchange = self._need_exchange()
        document = self._document("response")
        try:
            spec = self._load_spec(self._text(name, "openapi"))
        except (PathConfinementError, api_schema.SpecError) as exc:
            return {"assertion": "ERROR", "reason": str(exc)}
        request_path = urlsplit(exchange.response.url).path or "/"
        template = api_schema.match_template(spec, request_path, (urlsplit(self.base_url).path,) if self.base_url else ())
        if template is None:
            return {"assertion": "ERROR", "reason": f"no path in {name} matches {request_path}"}
        return api_schema.validate_operation(document, spec, template, exchange.request.method, exchange.response.status or 0)

    def _load_spec(self, name: str) -> dict[str, Any]:
        path = confine(name, self._roots())
        mtime = path.stat().st_mtime
        cached = self._specs.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        document = api_schema.load_document(path)
        self._specs[path] = (mtime, document)
        return document

    def _from_verdict(self, index: int, label: str, expected: str, verdict: dict[str, Any], *, secret: bool = False) -> LedgerEntry:
        state = verdict.get("assertion")
        if state == "PASSED":
            actual = "conforms to the schema" if "violations" in verdict else REDACTED if secret else _clip(api_assert.stringify(verdict.get("actual")))
            return self._entry(index, label, expected, actual, True)
        if state == "FAILED":
            return self._entry(index, label, expected, _failure_text(verdict, secret), False)
        return self._entry(index, label, expected, f"error: {verdict.get('reason') or 'the check could not be evaluated'}", False)

    def _entry(self, index: int, check: str, expected: str, actual: str, passed: bool) -> LedgerEntry:
        redact = self.data.redact
        return LedgerEntry(step=index, check=check, expected=redact(_clip(expected)), actual=redact(_clip(actual)), verdict="passed" if passed else "failed")

    def _save_checks(self, index: int, ledger: list[LedgerEntry]) -> str:
        return self.evidence.save_json(index, "checks.json", {"checks": [entry.model_dump() for entry in ledger]})


# --------------------------------------------------------------------------- helpers


def _text_of(step: Step) -> str:
    if step.action.strip().lower() == VERIFY_ACTION and step.expected:
        return step.expected
    return f"{step.action}\nExpected: {step.expected}" if step.expected else step.action


def _has_checks(call: ApiCall | SqlCall) -> bool:
    expect = call.expect
    if expect is None:
        return False
    return any(value not in (None, []) for value in expect.model_dump().values())


def _unverified(step: Step, call: ApiCall | SqlCall) -> str | None:
    """The step's ``expected`` text when the payload has no checks to verify it."""
    return step.expected if step.expected is not None and not _has_checks(call) else None


def _clip(text: str) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _CLIP else flat[: _CLIP - 1] + "…"


def _shown(target: str, verdict: dict[str, Any], operator: str) -> str:
    if operator in api_assert.WITHOUT_EXPECTED:
        return target
    value = REDACTED if is_secret_name(target) else api_assert.stringify(verdict.get("expected"))
    return f"{target} {value}"


def _failure_text(verdict: dict[str, Any], secret: bool) -> str:
    violations = verdict.get("violations")
    if violations is not None:
        listed = "; ".join(f"{item['path']}: {item['message']}" for item in violations[:3])
        count = verdict.get("violation_count", len(violations))
        more = f" (+{count - 3} more)" if count > 3 else ""
        return f"{count} schema violation(s): {listed}{more}" if violations else str(verdict.get("reason") or "schema violation")
    actual = REDACTED if secret else api_assert.stringify(verdict.get("actual"))
    reason = verdict.get("reason")
    return f"got {actual}" + (f" ({reason})" if reason else "")
