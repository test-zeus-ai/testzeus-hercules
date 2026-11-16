"""Test data for one run: ``{{name}}`` substitution, secret resolution and redaction.

Secrets are referenced by environment variable name in the case and read only
here, at run time. Their values never reach prompts, logs or evidence: anything
written out goes through :meth:`CaseData.redact` first.

Values an API or SQL step saves during the run (:meth:`CaseData.capture`) are used
by later steps as ``{{name}}`` (``${name}`` also works, as in v1 JSONPath checks); a
saved value replaces a test-data value of the same name. A saved value whose name
looks secret (token, password, ...) is redacted like a secret.

Credentials in the case's URLs (a session id or token in a query string or fragment, a
password in userinfo; see :func:`~testzeus_hercules.act.redact.is_credential_parameter`)
are protected the same way: :func:`protect_case` replaces each such value in the step
texts with a ``{{url_credential_N}}`` placeholder before the run, so models, events and
evidence only ever see the placeholder, and the browser gets the real value when the
placeholder is resolved. :meth:`CaseData.redact` also masks credential parameters of any
URL by name (a page's own address, a redirect), not only the values the case declared.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from testzeus_hercules.act.redact import mask_url_credentials, rewrite_url_credentials, url_credential_values
from testzeus_hercules.cases.models import SecretRef, TestCase

REDACTED = "******"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_CAPTURED = re.compile(r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")
_SECRET_NAME = re.compile(r"password|passwd|pwd|secret|token|authorization|api[-_]?key|cookie|credential|bearer|private[-_]?key", re.IGNORECASE)
_MIN_SECRET_CHARS = 3
# A secret shorter than this is masked only where it stands alone (not inside a longer word or number), so a secret
# such as ``123`` does not turn ``1230`` into ``******0``.
_ANYWHERE_SECRET_CHARS = 8
URL_CREDENTIAL_PREFIX = "url_credential_"


class MissingSecretError(LookupError):
    """A secret's environment variable is not set; the message names the variable only."""


@dataclass(frozen=True)
class CaseData:
    """Plain values and secret references available to a case's steps."""

    values: Mapping[str, str] = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict)  # placeholder name -> env var name
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    templates: Mapping[str, str] = field(default_factory=dict)  # plain values as written in the case
    captured: dict[str, Any] = field(default_factory=dict, repr=False)  # values saved by steps during the run
    # Credentials found in the case's URLs: placeholder name -> value (see protect_case); masked like secrets.
    hidden: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_case(cls, case: TestCase, *, env: Mapping[str, str], run_id: str = "run", now: datetime | None = None, hidden: Mapping[str, str] | None = None) -> CaseData:
        moment = now or datetime.now(timezone.utc)
        builtins = {"run_id": run_id, "timestamp": f"{moment:%Y%m%d%H%M%S}"}
        values = {key: value for key, value in case.test_data.items() if isinstance(value, str)}
        secrets = {key: value.secret for key, value in case.test_data.items() if isinstance(value, SecretRef)}
        resolved = {**builtins, **values}
        # Plain values may reference built-ins (e.g. "user+{{run_id}}@example.test").
        resolved = {key: _PLACEHOLDER.sub(lambda m: builtins.get(m.group(1), m.group(0)), value) for key, value in resolved.items()}
        found = dict(hidden or {})
        # Credentials inside plain test-data values and the base URL are never shown to a model (only their names
        # are), yet they reach the browser: keep them so every copy written out is masked.
        for text in [*values.values(), case.environment.base_url or ""]:
            for value in url_credential_values(text):
                if value not in found.values():
                    found[f"{URL_CREDENTIAL_PREFIX}{len(found) + 1}"] = value
        return cls(values=resolved, secrets=secrets, env=dict(env), templates=values, hidden=found)

    def resolve(self, text: str, *, captured: bool = True) -> str:
        """Substitute every known placeholder; unknown ones are left as written.

        ``captured`` False leaves ``${name}`` alone (an assertion substitutes it itself, keeping the value's type).
        """
        resolved = _PLACEHOLDER.sub(lambda match: self._value(match.group(1), match.group(0)), text)
        if not captured or not self.captured:
            return resolved
        return _CAPTURED.sub(lambda match: _text(self.captured[match.group(1)]) if match.group(1) in self.captured else match.group(0), resolved)

    def capture(self, name: str, value: Any) -> None:
        """Keep a value a step saved, for ``${name}`` (and ``{{name}}``) in later steps."""
        self.captured[name] = value

    def lookup(self, name: str) -> str | None:
        """The value of one name (a saved value, a plain value or a secret), or None when the case has no such name."""
        if name not in self.captured and name not in self.values and name not in self.secrets and name not in self.hidden:
            return None
        return self._value(name, "")

    def _value(self, name: str, original: str) -> str:
        if name in self.captured:
            return _text(self.captured[name])
        if name in self.values:
            return self.values[name]
        if name in self.secrets:
            var = self.secrets[name]
            secret = self.env.get(var)
            if secret is None:
                raise MissingSecretError(f"secret {name!r} needs the environment variable {var}, which is not set")
            return secret
        if name in self.hidden:
            return self.hidden[name]
        return original

    def secret_values(self) -> tuple[str, ...]:
        """Values of the secrets that are set and of saved values with secret-looking names, longest first (for redaction)."""
        found = {self.env[var] for var in self.secrets.values() if self.env.get(var)}
        found |= {_text(value) for name, value in self.captured.items() if _SECRET_NAME.search(name) and len(_text(value)) >= _MIN_SECRET_CHARS}
        found |= {value for value in self.hidden.values() if len(value) >= _MIN_SECRET_CHARS}
        return tuple(sorted(found, key=len, reverse=True))

    def redact(self, text: str) -> str:
        """Replace every secret value in ``text`` with a fixed mask, and mask the credential parameters of its URLs.

        A short secret (under 8 characters) is masked only where it is not part of a longer word or number."""
        for secret in self.secret_values():
            if len(secret) >= _ANYWHERE_SECRET_CHARS:
                text = text.replace(secret, REDACTED)
            elif secret in text:
                text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])", REDACTED, text)
        return mask_url_credentials(text)

    def protect(self, text: str) -> str:
        """``text`` with the credentials in its URLs replaced by placeholders (see :func:`protect_text`); for a step text
        that arrives during the run (a tester's edit)."""
        return protect_text(text, self.hidden)

    def fingerprint(self) -> str:
        """Stable hash of the case's plain values and secret names (never secret values, run ids or times)."""
        canonical = json.dumps({"values": dict(self.templates), "secrets": dict(self.secrets)}, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def protect_text(text: str, table: dict[str, str]) -> str:
    """``text`` with each credential in its URLs replaced by a ``{{url_credential_N}}`` placeholder, added to ``table``
    (placeholder name -> value; one name per distinct value). The rest of each URL stays readable."""

    def placeholder(value: str) -> str:
        for name, known in table.items():
            if known == value:
                return "{{" + name + "}}"
        name = f"{URL_CREDENTIAL_PREFIX}{len(table) + 1}"
        while name in table:
            name += "_"
        table[name] = value
        return "{{" + name + "}}"

    return rewrite_url_credentials(text, placeholder)


def protect_case(case: TestCase) -> tuple[TestCase, dict[str, str]]:
    """The case with the credentials in its step and precondition texts replaced by placeholders, and the placeholders' values.

    A case without such credentials is returned unchanged (the same object) with an empty table.
    """
    table: dict[str, str] = {}
    steps = []
    for step in case.steps:
        updates = {name: protect_text(getattr(step, name), table) for name in ("action", "expected") if getattr(step, name)}
        changed = {name: text for name, text in updates.items() if text != getattr(step, name)}
        steps.append(step.model_copy(update=changed) if changed else step)
    preconditions = [protect_text(text, table) for text in case.preconditions]
    if not table:
        return case, table
    return case.model_copy(update={"steps": steps, "preconditions": preconditions}), table


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)
