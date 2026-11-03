"""Redacted error-evidence persistence for harness failures.

When the agent graph (or the harness around it) fails, the raw exception and
its context often carry secret material: bearer tokens in URLs or headers,
provider API keys in client messages, credentials in tool arguments. This
module writes one evidence file per failure under
``<log_dir>/errors/<category>_<error_id>.log`` with that material redacted,
so a failure can be diagnosed without leaking credentials into reports,
junit output or failure bundles.

Redaction is intentionally coarse and one-directional: anything that looks
like a credential is replaced with ``***``. Losing some diagnostic detail is
acceptable; leaking a credential is not.
"""

from __future__ import annotations

import json
import os
import re
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Set

from testzeus_hercules.utils.redact import redact_url

_REDACTED = "***"

# Per-section size cap (~100KB): a huge message would otherwise be written
# twice (message section + traceback line), ballooning the evidence file.
_MAX_SECTION_CHARS = 100_000

# Header-style lines whose whole remainder after the colon is a credential
# ("Authorization: Basic dXNlcjpwYXNz", "X-Auth-Token: abc"): the value may
# contain spaces, so redacting only the first token would leak the rest of
# the line. Arbitrary text may precede the keyword (a traceback prefix like
# "RuntimeError: Authorization: ..."), so the match is not anchored to the
# start of the credential itself; the first colon after the keyword ends the
# key and everything up to end-of-line is treated as the value.
_AUTHZ_HEADER_LINE_RE = re.compile(r"(?im)^(?P<pre>.*?\b(?:proxy[-_ ]?authorization|www[-_ ]?authorization|authorization|token)\b[^\r\n:]*?:)[ \t]*(?P<value>[^\r\n]*)$")

# Any scheme://... URL — userinfo (postgres://admin:s3cr3t@db.host/prod) is
# stripped via the shared redact_url helper.
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+")

# "Authorization: Bearer abc...", "bearer xyz" (value charset covers JWT/base64 shapes).
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")

# Provider-style secret keys ("sk-...", as used by OpenAI-compatible endpoints).
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{4,}")

# Credential assignments in text: password=hunter2, "token": "abc", secret: x.
# The lookahead keeps already-redacted values ("***") untouched so repeated
# redaction passes are idempotent.
_ASSIGNMENT_RE = re.compile(r"(?i)([\"']?)(password|passwd|secret|token|api[_-]?key|authorization|cookie|private[_-]?key)\1(\s*[=:]\s*)" r"(?!\"?\*{3})(\"[^\"]*\"|'[^']*'|[^\s,;&}\"']+)")

# Tool-argument keys whose values must never reach an evidence file. Matching
# is substring based on purpose words so variants (access_token, secret_key,
# api-key) are covered; over-redaction of evidence is the safe direction.
_SENSITIVE_ARG_KEY_MARKERS = ("password", "passwd", "secret", "token", "api_key", "apikey", "authorization", "cookie", "credentials", "auth", "private_key")


def _is_sensitive_arg_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_ARG_KEY_MARKERS)


def _redact_header_value(match: Any) -> str:
    """Replace a whole header-line value with the marker, keeping its quoting shape."""
    pre, value = match.group("pre"), match.group("value")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        # Keep the JSON-ish shape: "token": "..." -> "token": "***".
        return f"{pre}{value[0]}{_REDACTED}{value[-1]}"
    return f"{pre} {_REDACTED}"


def _redact_error_text(text: str) -> str:
    """Redact credential-looking material from free text (messages, tracebacks).

    Coverage order: whole authorization/token-style header line values (they
    may contain spaces, e.g. "Basic dXNlcjpwYXNz"), userinfo in URLs (via the
    shared :func:`redact_url`), bearer values, sk- style keys, then generic
    key=value assignments (which also matches the JSON shape ``"token":
    "abc"``). The pass is idempotent: re-redacting already redacted text
    returns it unchanged.
    """
    if not isinstance(text, str):
        return text
    redacted = _AUTHZ_HEADER_LINE_RE.sub(_redact_header_value, text)
    redacted = _URL_RE.sub(lambda match: redact_url(match.group(0)), redacted)
    redacted = _BEARER_TOKEN_RE.sub(f"Bearer {_REDACTED}", redacted)
    redacted = _SK_KEY_RE.sub(f"sk-{_REDACTED}", redacted)
    redacted = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{match.group(1)}{match.group(3)}{_REDACTED}", redacted)
    return redacted


def _cap_section(text: str) -> str:
    """Cap ``text`` at the per-section limit, noting how many chars were dropped."""
    if len(text) <= _MAX_SECTION_CHARS:
        return text
    return f"{text[:_MAX_SECTION_CHARS]}... [truncated {len(text) - _MAX_SECTION_CHARS} chars]"


def _safe_tool_args(tool_args: Any, _seen: Optional[Set[int]] = None) -> Any:
    """Return a copy of tool arguments with sensitive keys masked.

    Nested dicts/lists are walked; sensitive keys (case/separator
    insensitive) have their values replaced wholesale instead of trusting
    any in-value redaction. Cyclic references render as ``<circular>``
    instead of recursing forever.
    """
    seen = _seen if _seen is not None else set()
    if isinstance(tool_args, dict):
        if id(tool_args) in seen:
            return "<circular>"
        seen = seen | {id(tool_args)}
        return {str(key): (_REDACTED if _is_sensitive_arg_key(str(key)) else _safe_tool_args(value, seen)) for key, value in tool_args.items()}
    if isinstance(tool_args, (list, tuple)):
        if id(tool_args) in seen:
            return "<circular>"
        seen = seen | {id(tool_args)}
        return [_safe_tool_args(item, seen) for item in tool_args]
    return tool_args


def _render_extra_context(extra_context: Any) -> str:
    payload = _safe_tool_args(extra_context) if isinstance(extra_context, (dict, list, tuple)) else extra_context
    try:
        rendered = json.dumps(payload, indent=2, default=str)
    except (TypeError, ValueError):
        rendered = str(payload)
    return _redact_error_text(rendered)


def persist_error_evidence(log_dir: str, error: BaseException, *, category: str, stage: str, extra_context: Any = None) -> str:
    """Write one redacted evidence file for ``error`` and return its path.

    The file lives at ``<log_dir>/errors/<category>_<error_id>.log`` and
    carries: a short error id, timestamp, category, stage, error type, the
    redacted message, the redacted traceback and optional extra context
    (e.g. tool arguments, which pass through :func:`_safe_tool_args` first).

    I/O problems propagate to the caller: evidence capture is best-effort at
    the hook site and must never mask the original failure being recorded.
    """
    error_id = uuid.uuid4().hex[:12]
    safe_category = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(category)).strip("_") or "error"
    errors_dir = os.path.join(str(log_dir), "errors")
    os.makedirs(errors_dir, exist_ok=True)
    evidence_path = os.path.join(errors_dir, f"{safe_category}_{error_id}.log")

    message = _cap_section(_redact_error_text(str(error)))
    if error.__traceback__ is not None:
        traceback_text = _cap_section(_redact_error_text("".join(traceback.format_exception(type(error), error, error.__traceback__)).rstrip()))
    else:
        traceback_text = "(no traceback captured)"

    sections = [
        f"error_id: {error_id}",
        f"timestamp: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"category: {category}",
        f"stage: {stage}",
        f"error_type: {type(error).__name__}",
        "",
        "message:",
        message,
        "",
        "traceback:",
        traceback_text,
    ]
    if extra_context is not None:
        sections.extend(["", "extra context:", _cap_section(_render_extra_context(extra_context))])

    with open(evidence_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sections) + "\n")
    return evidence_path
