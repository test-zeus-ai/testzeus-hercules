"""Pre-run lint: problems with the test itself, found before the browser starts.

Findings come from two places:

* **the model** (``source: model``): the case reading lists, per step, a value with a stray
  bracket or quote, markup such as a Markdown link where a plain value is expected, a value
  that looks cut off, a reference several controls could match, an outcome that cannot be
  checked as written (see the case prompt in :mod:`testzeus_hercules.engine.intent`);
* **facts** (``source: fact``), established in code without judging meaning: a secret whose
  environment variable is not set, a credential written in a URL (it should be a secret
  reference), a ``{{placeholder}}`` in the plan that no test data, built-in or earlier step
  provides, a database query step with no database connection in the case, a step the plan
  flags destructive or irreversible while the run does not ask before such actions, a
  coverage gap the repair did not close, and a step the model could not read before the run.

A finding of severity ``error`` blocks the run in strict mode (``plan.strict``, ``--strict``);
otherwise every finding is a warning shown with the plan and the run.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from testzeus_hercules.cases.models import BUILTIN_PLACEHOLDERS, DOWNLOAD_PLACEHOLDERS, SecretRef, TestCase, saved_names_in
from testzeus_hercules.cases.plan import LintFinding, Plan

__all__ = ["fact_findings", "model_findings", "lint_plan"]

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_HIDDEN = re.compile(r"^url_credential_\d+$")
_CONNECTION = re.compile(r"^[a-z][a-z0-9+]*://", re.IGNORECASE)
_SQLITE = re.compile(r"^sqlite(\+[a-z0-9]+)?:///", re.IGNORECASE)
_RISK_WORDS = {"destructive": "may delete or remove data", "irreversible": "cannot be undone", "external_side_effect": "reaches outside the app"}
_KIND_CODES = {"suspicious_value": "suspicious_value", "ambiguous_reference": "ambiguous_reference", "unclear_outcome": "unclear_outcome"}
_CLIP = 900


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _CLIP else flat[: _CLIP - 1] + "…"


def _texts_of(case: TestCase, number: int) -> str:
    step = case.steps[number - 1]
    return " ".join(filter(None, (step.action, step.expected, step.model_dump_json(include={"api", "sql", "quality", "security"}))))


def _steps_naming(case: TestCase, name: str) -> list[int]:
    pattern = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*\}\}")
    return [number for number in range(1, len(case.steps) + 1) if pattern.search(_texts_of(case, number))]


def _saved_names(case: TestCase) -> set[str]:
    names: set[str] = set()
    for step in case.steps:
        payload = step.api or step.sql
        if payload is not None:
            names |= {capture.name for capture in payload.save}
        names |= saved_names_in(step.action)
    return names


def fact_findings(case: TestCase, plan: Plan, *, env: Mapping[str, str], hidden: Iterable[str] = (), confirm_destructive: bool = False) -> list[LintFinding]:
    """The findings established in code (see the module docstring); ``case`` is the case as the run uses it (credentials in
    URLs already replaced by ``{{url_credential_N}}`` placeholders, named in ``hidden``)."""
    findings: list[LintFinding] = []
    for name, value in case.test_data.items():
        if isinstance(value, SecretRef) and not env.get(value.secret):
            steps = _steps_naming(case, name)
            findings.append(
                LintFinding(
                    severity="error",
                    code="missing_secret",
                    step=steps[0] if steps else None,
                    message=f"test data {name!r} is the secret {value.secret}, and {value.secret} is not set in the environment",
                    suggestion=f"set {value.secret} before the run (in the shell, CI secrets or the daemon's environment), or save its value on the Environments screen (kept in ~/.hercules/secrets.env)",
                )
            )
    for name in sorted(set(hidden)):
        steps = _steps_naming(case, name)
        where = f"step {steps[0]}" if steps else "the case's base URL"
        findings.append(
            LintFinding(
                severity="error",
                code="credential_in_url",
                step=steps[0] if steps else None,
                message=f"a credential is written in a URL in {where} (masked as {{{{{name}}}}} for this run)",
                suggestion='make it a secret of the environment (or of the case\'s test_data: {"secret": "ENV_VAR_NAME"}) and write {{name}} in the URL; this works in .feature files too',
            )
        )
    known = set(BUILTIN_PLACEHOLDERS) | set(case.test_data) | set(hidden) | _saved_names(case) | (set(DOWNLOAD_PLACEHOLDERS) if any(item.kind == "download" for item in plan.steps) else set())
    for item in plan.steps:
        texts = [item.target, item.value, item.url, item.expected, *(criterion.expected for criterion in item.criteria), *(criterion.value for criterion in item.criteria)]
        unknown = sorted({name for text in texts if text for name in _PLACEHOLDER.findall(text) if name not in known and not _HIDDEN.match(name)})
        if unknown:
            findings.append(
                LintFinding(
                    severity="error",
                    code="unresolved_placeholder",
                    step=item.step,
                    message=f"plan step {item.id} uses {', '.join('{{' + name + '}}' for name in unknown)}, which no test data, built-in or earlier step provides",
                    suggestion="add it to the case's test_data or to its environment's variables and secrets, or write the value in the step",
                )
            )
    findings += _unsupported(case, plan, env)
    if not confirm_destructive:
        for number in sorted({item.step for item in plan.steps if item.risk.flags}):
            flagged = [item for item in plan.steps_of(number) if item.risk.flags]
            flags = sorted({flag for item in flagged for flag in item.risk.flags})
            reason = flagged[0].risk.reason
            findings.append(
                LintFinding(
                    severity="warning",
                    code="unconfirmed_risk",
                    step=number,
                    message=_clip(f"step {number} {' and '.join(_RISK_WORDS[flag] for flag in flags)}" + (f" ({reason})" if reason else "") + ", and the run does not ask before it"),
                    suggestion="run with confirm_destructive (UI or MCP run option) to be asked before such actions, or point the test at disposable data",
                )
            )
    for gap in plan.coverage.open_gaps:
        findings.append(
            LintFinding(
                severity="error",
                code="coverage_gap",
                step=gap.step,
                message=f"the coverage audit found that the plan may not cover every obligation of step {gap.step} ({gap.probability:.0%} likely covered), even after it was read again",
                suggestion="split the step into one action per step, or state its outcome as a quoted text the page shows",
            )
        )
    for step in sorted({item.step for item in plan.steps if item.read == "page"}):  # once per case step, not per plan step
        findings.append(
            LintFinding(
                severity="warning",
                code="other",
                step=step,
                message=f"step {step} was not read before the run (the model gave no reading for it); it is read on the page when it runs, without a plan",
                suggestion="rephrase the step, or check the model server with `hercules doctor`",
            )
        )
    return findings


def _connections(case: TestCase, env: Mapping[str, str]) -> list[str]:
    """Test data that is a database connection: a connection URL, written in the case or held by a secret's variable."""
    found = []
    for name, value in case.test_data.items():
        text = env.get(value.secret, "") if isinstance(value, SecretRef) else value
        if _CONNECTION.match(text) or _SQLITE.match(text):
            found.append(name)
    return found


def _unsupported(case: TestCase, plan: Plan, env: Mapping[str, str]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    sql_steps = sorted({item.step for item in plan.steps if item.kind == "sql" and case.steps[item.step - 1].sql is None})
    if sql_steps and not _connections(case, env):
        for number in sql_steps:
            findings.append(
                LintFinding(
                    severity="error",
                    code="unsupported_step",
                    step=number,
                    message=f"step {number} is a database query, and the case configures no database connection",
                    suggestion='add the connection string to test_data as a secret reference (for example "db": {"secret": "APP_DB_URL"}) and name it in the step',
                )
            )
    return findings


def model_findings(extras: Mapping[int, Mapping[str, Any]]) -> list[LintFinding]:
    """The lint findings the model gave with its case reading (validated; unknown kinds dropped)."""
    findings: list[LintFinding] = []
    for number, extra in sorted(extras.items()):
        for raw in extra.get("lint") or ():
            if not isinstance(raw, Mapping) or raw.get("kind") not in _KIND_CODES or not str(raw.get("message") or "").strip():
                continue
            suggestion = str(raw.get("suggestion") or "").strip()
            findings.append(
                LintFinding(
                    severity="error" if raw.get("severity") == "error" else "warning",
                    code=_KIND_CODES[str(raw["kind"])],  # type: ignore[arg-type]
                    step=number if 1 <= number <= 200 else None,
                    message=_clip(str(raw["message"])),
                    suggestion=_clip(suggestion) if suggestion else None,
                    source="model",
                )
            )
    return findings


def lint_plan(case: TestCase, plan: Plan, *, env: Mapping[str, str], hidden: Iterable[str] = (), extras: Mapping[int, Mapping[str, Any]] | None = None, confirm_destructive: bool = False) -> Plan:
    """``plan`` with its lint findings: the model's (from the case reading's extras) and the facts, errors first."""
    findings = model_findings(extras or {}) + fact_findings(case, plan, env=env, hidden=hidden, confirm_destructive=confirm_destructive)
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda finding: (order[finding.severity], finding.step or 0))
    return plan.model_copy(update={"lint": findings})
