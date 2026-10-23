"""Shared redaction helpers for log output and log artifacts.

Single home for the userinfo-URL strip (moved from orchestrator.session) and
the header / post-data secret redaction (moved from core.playwright_manager),
so every logger that writes credentials into files or artifacts uses the same
implementation. Keep this module dependency-light (stdlib + utils.logger) so it
can be imported from anywhere, including orchestrator.session.
"""

import json
import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional

from testzeus_hercules.utils.logger import logger

# Fallback userinfo strip for URLs that urlsplit rejects (bad IPv6 brackets,
# NFKC-host forms, ...): everything from "//" up to and including the next "@"
# (before any "/", "@") is dropped, so credentials can never survive.
_USERINFO_RE = re.compile(r"//[^/@\s]*@")


def redact_url(url: str) -> str:
    """Strip userinfo and redact sensitive query values from a URL; never raises.

    "https://user:token@host/path" becomes "https://host/path" and the values
    of password-like query parameters ("…?token=sekret") become [REDACTED] so
    secrets never reach persisted state or log output. URLs that urlsplit
    rejects (e.g. "https://user:pass@[::1" or NFKC-host forms) fall back to a
    regex userinfo strip plus query redaction — the raw credentialed URL is
    never returned.
    """
    if not url:
        return url
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return _redact_url_fallback(url)
    redacted_query = _redact_query(parsed.query)
    if parsed.username is None and parsed.password is None and redacted_query == parsed.query:
        return url
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if host and ":" in host:  # IPv6 literal needs brackets back
        netloc = f"[{host}]"
    else:
        netloc = host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, redacted_query, parsed.fragment))


def redact_webhook_url(url: str) -> str:
    """Log-safe form of a webhook URL: ``scheme://host[:port]`` plus a masked path; never raises.

    Webhook URLs carry their secret in the path (Slack ``/services/T/B/SECRET``,
    Discord ``/api/webhooks/<id>/<token>``) or the query (``?token=...``), so
    only the origin survives. Userinfo, path segments, query and fragment are
    dropped; a non-empty path becomes ``/[REDACTED]``.
    """
    if not url:
        return url
    try:
        parsed = urllib.parse.urlsplit(str(url).strip())
        host = parsed.hostname or ""
        if not parsed.scheme or not host:
            return "[unparsable-url]"
        netloc = f"[{host}]" if ":" in host else host
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            netloc = f"{netloc}:{port}"
        path = f"/{REDACTED_PLACEHOLDER}" if parsed.path.strip("/") else ""
        return f"{parsed.scheme}://{netloc}{path}"
    except Exception:
        return "[unparsable-url]"


_URL_IN_TEXT_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+")


def redact_webhook_text(text: str, url: str = "") -> str:
    """Mask every URL inside ``text`` (e.g. an httpx error message) with :func:`redact_webhook_url`.

    The exact ``url`` is replaced first so URLs the regex would cut short
    (spaces, quotes) cannot leak either.
    """
    try:
        out = str(text)
        if url:
            out = out.replace(url, redact_webhook_url(url))
        return _URL_IN_TEXT_RE.sub(lambda match: redact_webhook_url(match.group(0)), out)
    except Exception:
        return "[redacted]"


# Query parameter names whose values must never reach log output. Matched
# case-insensitively against the decoded parameter name: exactly for these
# short names (so a non-secret like "monitor" or "keyword" is kept), and by the
# secret-name fragments of `is_sensitive_name` ("client_secret",
# "refresh_token", "id_token", ...).
REDACTED_QUERY_PARAM_NAMES = frozenset(("token", "access_token", "api_key", "apikey", "key", "secret", "password", "passwd", "sig", "signature", "auth"))


def _redact_query_pair(pair: str) -> str:
    """Redact the value of one raw `name=value` query pair when the name is sensitive."""
    name, sep, _ = pair.partition("=")
    if not sep:
        return pair
    try:
        decoded_name = urllib.parse.unquote_plus(name)
    except Exception:
        decoded_name = name
    if decoded_name.lower() in REDACTED_QUERY_PARAM_NAMES or is_sensitive_name(decoded_name):
        return f"{name}={REDACTED_PLACEHOLDER}"
    return pair


def _redact_query(query: str) -> str:
    """Redact sensitive query parameter values; every other character is kept verbatim."""
    if not query:
        return query
    pairs = query.split("&")
    redacted = [_redact_query_pair(pair) for pair in pairs]
    if redacted == pairs:
        return query
    return "&".join(redacted)


def _redact_url_fallback(url: str) -> str:
    """Best-effort userinfo strip for unparsable URLs; never returns credentials.

    If even the regex strip fails, a scheme-only placeholder is returned — the
    raw credentialed URL must never survive redaction. The query string is
    redacted either way, so secrets there cannot leak through the fallback.
    """
    try:
        redacted = _USERINFO_RE.sub("//", url)
    except Exception:
        scheme, sep, _ = url.partition("://")
        if not sep:
            return "[unparsable-url]"
        redacted = f"{scheme}://[unparsable]"
    head, qmark, query = redacted.partition("?")
    if qmark:
        return head + "?" + _redact_query(query)
    return redacted


# Header keys whose values must never reach the network logs. Matched as
# case-insensitive substrings so variants like "X-Api-Key", "Session-Token"
# or "Set-Cookie" are covered too.
REDACTED_HEADER_PATTERNS = ("authorization", "cookie", "proxy-authorization", "token", "api-key", "api_key", "apikey", "secret", "form-login")
# Key patterns redacted inside request post data (case-insensitive substrings).
# "apikey" covers the separator-free camel forms ("apiKey"); the plain
# "secret"/"token" substrings already cover "secretKey"/"accessToken".
REDACTED_POST_DATA_PATTERNS = ("password", "passwd", "pwd", "secret", "token", "authorization", "api-key", "api_key", "apikey")
REDACTED_PLACEHOLDER = "[REDACTED]"
# Bounded effort: parsed JSON bodies are only walked this many levels deep
# (browser network logs, which can be large and numerous).
POST_DATA_REDACTION_MAX_DEPTH = 1
# The API tool logs (one request/response per call) walk deeper; the node budget
# bounds the work on very wide bodies. Levels up to POST_DATA_REDACTION_MAX_DEPTH
# are always walked, whatever the budget, so deep mode never redacts less.
API_LOG_REDACTION_MAX_DEPTH = 8
API_LOG_REDACTION_MAX_NODES = 10_000

_MULTIPART_SECRET_FIELD_RE = re.compile(
    r'(name="[^"]*(?:' + "|".join(REDACTED_POST_DATA_PATTERNS) + r')[^"]*"[^>\r\n]*\r?\n\r?\n)([^\r\n]*)',
    re.IGNORECASE,
)


def _redact_sensitive_headers(headers: Any) -> Any:
    """Return headers with sensitive values replaced by "[REDACTED]" (never raises)."""
    if not isinstance(headers, dict):
        return headers
    redacted: Dict[Any, Any] = {}
    for key, value in headers.items():
        key_lower = str(key).lower()
        if any(pattern in key_lower for pattern in REDACTED_HEADER_PATTERNS):
            redacted[key] = REDACTED_PLACEHOLDER
        else:
            redacted[key] = value
    return redacted


def _redact_json_secrets(value: Any, depth: int = 0, max_depth: int = POST_DATA_REDACTION_MAX_DEPTH, _budget: Optional[List[int]] = None) -> Any:
    """Redact sensitive keys inside a parsed JSON object, walking nested containers up to `max_depth` levels."""
    if not isinstance(value, dict):
        return value
    budget = _budget if _budget is not None else [API_LOG_REDACTION_MAX_NODES]
    redacted: Dict[Any, Any] = {}
    for key, item in value.items():
        key_lower = str(key).lower()
        if any(pattern in key_lower for pattern in REDACTED_POST_DATA_PATTERNS):
            redacted[key] = REDACTED_PLACEHOLDER
        elif depth < max_depth:
            redacted[key] = _redact_nested(item, depth + 1, max_depth, budget)
        else:
            redacted[key] = item
    return redacted


def _redact_nested(item: Any, depth: int, max_depth: int, budget: List[int]) -> Any:
    """Descend into a dict/list found at `depth` (lists do not add a level for their dict entries)."""
    if not isinstance(item, (dict, list)):
        return item
    if depth > POST_DATA_REDACTION_MAX_DEPTH:
        if budget[0] <= 0:
            return item
        budget[0] -= 1
    if isinstance(item, dict):
        return _redact_json_secrets(item, depth, max_depth, budget)
    entries = []
    for entry in item:
        if isinstance(entry, dict):
            entries.append(_redact_json_secrets(entry, depth, max_depth, budget))
        elif isinstance(entry, list) and depth < max_depth:
            entries.append(_redact_nested(entry, depth + 1, max_depth, budget))
        else:
            entries.append(entry)
    return entries


def _redact_form_data_secrets(post_data: str) -> str:
    """Redact password-like field values in an urlencoded form body."""
    try:
        pairs = urllib.parse.parse_qsl(post_data, keep_blank_values=True)
    except ValueError:
        return post_data
    if not pairs:
        return post_data
    redacted_pairs = [(key, REDACTED_PLACEHOLDER) if any(pattern in key.lower() for pattern in REDACTED_POST_DATA_PATTERNS) else (key, value) for key, value in pairs]
    return urllib.parse.urlencode(redacted_pairs)


def _redact_post_data(post_data: Any, content_type: str = "", max_depth: int = POST_DATA_REDACTION_MAX_DEPTH) -> Any:
    """Best-effort redaction of secrets inside request post data (never raises).

    JSON bodies (objects and top-level arrays) have sensitive keys redacted at
    the top level and `max_depth` nesting levels (default one; the API tool
    logs pass API_LOG_REDACTION_MAX_DEPTH); urlencoded form bodies have
    password-like field values redacted; multipart bodies via a single bounded
    regex pass. Anything else (and any parse failure) is returned untouched so
    logging stays cheap.
    """
    if not isinstance(post_data, str) or not post_data:
        return post_data
    try:
        stripped = post_data.lstrip()
        if stripped.startswith("{"):
            parsed = json.loads(post_data)
            if isinstance(parsed, dict):
                return json.dumps(_redact_json_secrets(parsed, 0, max_depth), ensure_ascii=False)
            return post_data
        if stripped.startswith("["):
            parsed = json.loads(post_data)
            if isinstance(parsed, list):
                # Top-level array (e.g. [{"password": "..."}]): redact each dict
                # element at depth 0; non-dict elements pass through untouched.
                budget = [API_LOG_REDACTION_MAX_NODES]
                return json.dumps([_redact_json_secrets(entry, 0, max_depth, budget) if isinstance(entry, dict) else entry for entry in parsed], ensure_ascii=False)
            return post_data
        content_type_lower = (content_type or "").lower()
        if "x-www-form-urlencoded" in content_type_lower:
            return _redact_form_data_secrets(post_data)
        if "multipart/form-data" in content_type_lower:
            return _MULTIPART_SECRET_FIELD_RE.sub(lambda match: match.group(1) + REDACTED_PLACEHOLDER, post_data)
    except Exception as e:
        logger.debug(f"Skipping post-data redaction ({e}); logging body unchanged.")
    return post_data


# Name fragments that mark a variable / JSONPath / field as secret-bearing (the
# post-data patterns plus a few that only show up in names, not payload keys).
SENSITIVE_NAME_PARTS = REDACTED_POST_DATA_PATTERNS + ("cookie", "credential", "bearer", "private_key", "private-key")

# Secret values shorter than this are not scrubbed from free text (a 1-2 char
# "secret" would shred unrelated output).
MIN_SCRUBBED_SECRET_LENGTH = 3


def is_sensitive_name(name: Any) -> bool:
    """True when `name` (a variable name, JSONPath, field) suggests it holds a secret."""
    lowered = str(name or "").lower()
    return any(part in lowered for part in SENSITIVE_NAME_PARTS)


def scrub_secret_values(text: str, secret_values: Iterable[str]) -> str:
    """Replace every occurrence of each known secret value inside `text` (longest first)."""
    for value in sorted({str(item) for item in secret_values if len(str(item)) >= MIN_SCRUBBED_SECRET_LENGTH}, key=len, reverse=True):
        text = text.replace(value, REDACTED_PLACEHOLDER)
    return text


def redact_value(value: Any, key_hint: str = "", secret_values: Iterable[str] = ()) -> Any:
    """Best-effort redaction of one value destined for a persisted ledger (never raises).

    - a `key_hint` (JSONPath, variable name, ...) that is secret-bearing redacts the whole value;
    - dicts/lists have sensitive keys redacted (same rules as logged request bodies);
    - strings have known `secret_values` scrubbed and URL credentials/query secrets stripped.
    """
    try:
        if value is None or isinstance(value, bool):
            return value
        if is_sensitive_name(key_hint):
            return REDACTED_PLACEHOLDER
        if isinstance(value, (int, float)):
            return value
        secrets = list(secret_values)
        if isinstance(value, dict):
            return {key: redact_value(item, str(key), secrets) for key, item in _redact_json_secrets(value).items()}
        if isinstance(value, (list, tuple)):
            return [redact_value(item, "", secrets) for item in value]
        if isinstance(value, str):
            text = scrub_secret_values(value, secrets)
            if "://" in text:
                text = re.sub(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+", lambda match: redact_url(match.group(0)), text)
            return text
        return value
    except Exception:
        return REDACTED_PLACEHOLDER
