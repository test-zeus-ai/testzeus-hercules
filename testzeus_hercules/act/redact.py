"""Redaction for API and SQL evidence: URL credentials, secret headers, secret body fields and known secret values.

Everything an API or SQL step writes (evidence files, step results, ledger entries)
passes through these helpers before the run's own secret-value redactor, so a
credential is masked by name even when it was never declared as a secret. All
functions are linear in their input and never raise.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable, Iterable

REDACTED = "[REDACTED]"
# Header names whose values never reach evidence (case-insensitive substrings: X-Api-Key, Set-Cookie, ...).
SECRET_HEADER_PARTS = ("authorization", "cookie", "proxy-authorization", "token", "api-key", "api_key", "apikey", "secret", "form-login")
# Body keys whose values never reach evidence (case-insensitive substrings).
SECRET_FIELD_PARTS = ("password", "passwd", "pwd", "secret", "token", "authorization", "api-key", "api_key", "apikey")
# Name fragments that mark a variable, JSONPath or column as secret-bearing.
SECRET_NAME_PARTS = SECRET_FIELD_PARTS + ("cookie", "credential", "bearer", "private_key", "private-key")
# Secret values shorter than this are not scrubbed from free text (they would shred unrelated output).
MIN_SCRUBBED_LENGTH = 3
BODY_MAX_DEPTH = 8
BODY_MAX_NODES = 10_000

# Query, fragment and matrix parameter names whose values are credentials (compared case-insensitively). This is
# redaction policy: a value under one of these names is masked wherever a URL leaves memory (evidence, logs, events,
# model requests), whether or not the case declared it as a secret.
_QUERY_NAMES = frozenset(
    (
        "token",
        "access_token",
        "id_token",
        "refresh_token",
        "auth_token",
        "session_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "sig",
        "signature",
        "auth",
        "authorization",
        "code",
        "jwt",
        "access_key",
        "accesskey",
        "session",
        "sessionid",
        "session_id",
        "sid",
        "jsessionid",
        "phpsessid",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
    )
)
_QUERY_SUFFIXES = ("signature", "_sig", "-sig", "_key", "-key", "_session", "-session", "_sid", "-sid")
_BENIGN_KEYS = frozenset(
    ("sort_key", "sort-key", "order_key", "order-key", "primary_key", "primary-key", "foreign_key", "foreign-key", "partition_key", "partition-key", "cache_key", "cache-key", "token_type")
)
_QUERY_SEPARATOR = re.compile(r"([&;])")
_USERINFO = re.compile(r"//[^/@\s]*@")
# A URL in free text ends at white space, a quote, an angle bracket or a backslash (so a URL inside a JSON string
# never takes the string's escaped closing quote with it).
_URL_IN_TEXT = re.compile(r"://[^\s'\"<>\\]+")
# A name=value pair after "?", "&", "#" or ";" outside an absolute URL: a relative address or a bare query string.
_QUERY_PAIR_IN_TEXT = re.compile(r"(?<=[?&#;])([A-Za-z0-9_.%\-]{1,64})=([^&#;\s'\"<>\\]*)")
# A value that already names its secret (a {{placeholder}} or ${saved} reference) or is already masked is kept.
_REFERENCE_VALUE = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}|\$\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}")
_MASKED_VALUES = frozenset((REDACTED, urllib.parse.quote(REDACTED), "******"))
_TRAILING_PUNCTUATION = ".,;:!?"
_CLOSERS = {")": "(", "]": "[", "}": "{"}
_SCHEME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+.-")
_SCHEME_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_MAX_SCHEME = 32
_PASSWORD_ASSIGNMENT = re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*(?!\[REDACTED\])\S+")
_MULTIPART_SECRET = re.compile(r'(name="[^"]*(?:' + "|".join(SECRET_FIELD_PARTS) + r')[^"]*"[^>\r\n]*\r?\n\r?\n)([^\r\n]*)', re.IGNORECASE)

_FIELD_ALTERNATION = "|".join(re.escape(part) for part in SECRET_FIELD_PARTS)
# Secret fields in text that is not (complete) JSON: "key": "value" / "key": 123 (a truncated body, a log line) ...
_JSON_FIELD_SECRET = re.compile(r'("[^"\\\r\n]{0,128}?(?:' + _FIELD_ALTERNATION + r')[^"\\\r\n]{0,128}"\s*:\s*)("(?:[^"\\]|\\.)*"?|[^\s,}\]]+)', re.IGNORECASE)
# ... key=value pairs (form bodies, query strings, plain text) ...
_PAIR_SECRET = re.compile(r"(\b[\w.\-]{0,64}?(?:" + _FIELD_ALTERNATION + r")[\w.\-]{0,64}\s*=\s*)([^&\s;,]+)", re.IGNORECASE)
# ... and XML elements (<password>...</password>).
_XML_SECRET = re.compile(r"(<([\w:.\-]{0,64}?(?:" + _FIELD_ALTERNATION + r")[\w:.\-]{0,64})(?:\s[^>]{0,512})?>)([^<]*)(</\2\s*>)", re.IGNORECASE)

Replace = Callable[[str], str]


def is_secret_name(name: Any) -> bool:
    """True when ``name`` (a header, variable, JSONPath or column) suggests it holds a secret."""
    lowered = str(name or "").lower()
    return any(part in lowered for part in SECRET_NAME_PARTS)


def scrub_values(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each known secret value in ``text`` (longest first)."""
    for value in sorted({str(item) for item in secrets if len(str(item)) >= MIN_SCRUBBED_LENGTH}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


# --------------------------------------------------------------------------- URLs


def is_credential_parameter(name: str) -> bool:
    """True when a URL query, fragment or matrix parameter of this name carries a credential (``sid``, ``token``, ...)."""
    try:
        lowered = urllib.parse.unquote_plus(name).lower().strip()
    except Exception:
        lowered = name.lower().strip()
    if lowered in _BENIGN_KEYS:
        return False
    return lowered in _QUERY_NAMES or lowered.endswith(_QUERY_SUFFIXES) or is_secret_name(lowered)


def _kept(value: str) -> bool:
    """A value that is empty, already masked, or a reference to a named value (never a credential itself)."""
    if not value or value in _MASKED_VALUES:
        return True
    try:
        decoded = urllib.parse.unquote(value)
    except Exception:
        decoded = value
    return decoded in _MASKED_VALUES or _REFERENCE_VALUE.fullmatch(decoded.strip()) is not None


def _mask(_value: str) -> str:
    return REDACTED


def _rewrite_pair(pair: str, replace: Replace) -> str:
    name, sep, value = pair.partition("=")
    if not sep or _kept(value) or not is_credential_parameter(name):
        return pair
    return f"{name}={replace(value)}"


def _rewrite_query(query: str, replace: Replace) -> str:
    if not query:
        return query
    parts = _QUERY_SEPARATOR.split(query)
    return "".join(part if index % 2 else _rewrite_pair(part, replace) for index, part in enumerate(parts))


def _rewrite_path(path: str, replace: Replace) -> str:
    if ";" not in path:
        return path
    segments = []
    for segment in path.split("/"):
        head, *params = segment.split(";")
        segments.append(";".join([head, *(_rewrite_pair(param, replace) for param in params)]))
    return "/".join(segments)


def _rewrite_fragment(fragment: str, replace: Replace) -> str:
    if not fragment or "=" not in fragment:
        return fragment
    head, mark, query = fragment.partition("?")
    return head + "?" + _rewrite_query(query, replace) if mark else _rewrite_query(fragment, replace)


def _redact_url_fallback(url: str) -> str:
    try:
        stripped = _USERINFO.sub("//", url)
    except Exception:
        scheme, sep, _ = url.partition("://")
        return f"{scheme}://[unparsable]" if sep else "[unparsable-url]"
    body, hash_mark, fragment = stripped.partition("#")
    head, mark, query = body.partition("?")
    out = _rewrite_path(head, _mask) + ("?" + _rewrite_query(query, _mask) if mark else "")
    return out + ("#" + _rewrite_fragment(fragment, _mask) if hash_mark else "")


def _netloc(parts: urllib.parse.SplitResult, userinfo: str = "") -> str:
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{userinfo}@{netloc}" if userinfo else netloc


def redact_url(url: str) -> str:
    """``url`` without userinfo and with secret query, matrix and fragment values masked; never the raw credentialed URL.

    Userinfo whose password is a ``{{placeholder}}`` (see :func:`rewrite_url_credentials`) is kept: it holds no secret.
    """
    if not url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return _redact_url_fallback(url)
    query, path, fragment = _rewrite_query(parts.query, _mask), _rewrite_path(parts.path, _mask), _rewrite_fragment(parts.fragment, _mask)
    placeholder_userinfo = parts.password is not None and _kept(parts.password)
    if (parts.username is None and parts.password is None) or placeholder_userinfo:
        if (query, path, fragment) == (parts.query, parts.path, parts.fragment):
            return url
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
    return urllib.parse.urlunsplit((parts.scheme, _netloc(parts), path, query, fragment))


def _rewrite_absolute(url: str, replace: Replace) -> str:
    """``url`` with each credential value (and a userinfo password) passed through ``replace``; the rest as written."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query, path, fragment = _rewrite_query(parts.query, replace), _rewrite_path(parts.path, replace), _rewrite_fragment(parts.fragment, replace)
    netloc = parts.netloc
    if parts.password and not _kept(parts.password):
        userinfo, _, host = netloc.rpartition("@")
        user, _, password = userinfo.partition(":")
        netloc = f"{user}:{replace(password)}@{host}"
    if (netloc, query, path, fragment) == (parts.netloc, parts.query, parts.path, parts.fragment):
        return url
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, fragment))


def _url_end(text: str, floor: int, end: int) -> int:
    """Where a URL found in free text ends: without trailing punctuation or an unbalanced closing bracket."""
    while end > floor:
        last = text[end - 1]
        if last in _TRAILING_PUNCTUATION or (last in _CLOSERS and text.count(last, floor, end) > text.count(_CLOSERS[last], floor, end)):
            end -= 1
            continue
        break
    return end


def _map_urls(text: str, rewrite: Callable[[str], str], between: Callable[[str], str] = lambda piece: piece) -> str:
    """``text`` with each absolute URL passed through ``rewrite`` and the text between URLs through ``between``.

    A URL's trailing punctuation (a full stop, a closing bracket) is not part of it.
    """
    pieces: list[str] = []
    cursor = 0
    for match in _URL_IN_TEXT.finditer(text):
        end_of_scheme = match.start()
        begin = end_of_scheme
        floor = max(cursor, end_of_scheme - _MAX_SCHEME)
        while begin > floor and text[begin - 1] in _SCHEME_CHARS:
            begin -= 1
        while begin < end_of_scheme and text[begin] not in _SCHEME_START:
            begin += 1
        if begin == end_of_scheme:
            continue
        end = _url_end(text, match.start() + 3, match.end())
        pieces.append(between(text[cursor:begin]))
        pieces.append(rewrite(text[begin:end]))
        cursor = end
    pieces.append(between(text[cursor:]))
    return "".join(pieces)


def _rewrite_relative(text: str, replace: Replace) -> str:
    if "=" not in text:
        return text

    def pair(match: re.Match[str]) -> str:
        name, value = match.group(1), match.group(2)
        return match.group(0) if _kept(value) or not is_credential_parameter(name) else f"{name}={replace(value)}"

    return _QUERY_PAIR_IN_TEXT.sub(pair, text)


def rewrite_url_credentials(text: str, replace: Replace) -> str:
    """``text`` with every URL credential value passed through ``replace``: query, fragment and matrix parameters with
    credential names (:func:`is_credential_parameter`), in absolute URLs, relative addresses and bare query strings,
    and the password of URL userinfo. Everything else is kept as written; references (``{{name}}``) are never touched."""
    return _map_urls(str(text), lambda url: _rewrite_absolute(url, replace), lambda piece: _rewrite_relative(piece, replace))


def mask_url_credentials(text: str) -> str:
    """``text`` with URL credentials masked (see :func:`redact_url`), in absolute and relative URLs; never raises."""
    try:
        return _map_urls(str(text), redact_url, lambda piece: _rewrite_relative(piece, _mask))
    except Exception:
        return REDACTED


def url_credential_values(text: str) -> list[str]:
    """The credential values in the URLs of ``text`` (as written, and percent-decoded when that differs), in order."""
    found: list[str] = []

    def keep(value: str) -> str:
        for candidate in (value, urllib.parse.unquote_plus(value)):
            if candidate and candidate not in found:
                found.append(candidate)
        return value

    try:
        rewrite_url_credentials(text, keep)
    except Exception:
        return found
    return found


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Free text (an error message, a driver exception) with secret values, URL credentials and ``password=...`` masked."""
    try:
        cleaned = mask_url_credentials(scrub_values(str(text), secrets))
        return _PASSWORD_ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", cleaned)
    except Exception:
        return REDACTED


# --------------------------------------------------------------------------- headers and bodies


def redact_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Header pairs with the values of credential-bearing headers masked."""
    return [(name, REDACTED if any(part in name.lower() for part in SECRET_HEADER_PARTS) else value) for name, value in headers]


def redact_tree(value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
    """A JSON-like value with secret-named keys masked, walking at most ``BODY_MAX_DEPTH`` levels and ``BODY_MAX_NODES``
    containers. A container past either limit is masked whole (it was not inspected, so it may hold a secret)."""
    budget = [BODY_MAX_NODES] if budget is None else budget
    if not isinstance(value, (dict, list)):
        return value
    if depth > BODY_MAX_DEPTH or budget[0] <= 0:
        return REDACTED
    budget[0] -= 1
    if isinstance(value, list):
        return [redact_tree(item, depth + 1, budget) for item in value]
    return {key: REDACTED if any(part in str(key).lower() for part in SECRET_FIELD_PARTS) else redact_tree(item, depth + 1, budget) for key, item in value.items()}


def redact_body_text(text: str, content_type: str = "") -> str:
    """A request or response body with secret fields masked: JSON keys, form fields and multipart parts."""
    if not text:
        return text
    try:
        stripped = text.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                return json.dumps(redact_tree(json.loads(text)), ensure_ascii=False)
            except (ValueError, RecursionError):
                return _redact_json_lines(text)  # NDJSON, or a truncated or malformed body
        lowered = content_type.lower()
        if "x-www-form-urlencoded" in lowered:
            pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
            if pairs:
                return urllib.parse.urlencode([(key, REDACTED if any(part in key.lower() for part in SECRET_FIELD_PARTS) else value) for key, value in pairs])
        if "multipart/form-data" in lowered:
            text = _MULTIPART_SECRET.sub(lambda match: match.group(1) + REDACTED, text)
        return _redact_loose(text)
    except Exception:
        return REDACTED


def _redact_json_lines(text: str) -> str:
    """A body that is not one JSON document: line by line as JSON (NDJSON), each line that does not parse by pattern."""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        try:
            parsed = json.loads(line) if line.strip() else None
        except (ValueError, RecursionError):
            out.append(_redact_loose(line))
            continue
        out.append(line if parsed is None else json.dumps(redact_tree(parsed), ensure_ascii=False))
    return "\n".join(out)


def _redact_loose(text: str) -> str:
    """Secret fields masked by pattern in text that could not be parsed: ``"key": value``, ``key=value``, ``<key>value</key>``."""
    text = _JSON_FIELD_SECRET.sub(lambda match: match.group(1) + ('"' + REDACTED + '"' if match.group(2).startswith('"') else REDACTED), text)
    text = _PAIR_SECRET.sub(lambda match: match.group(1) + REDACTED, text)
    return _XML_SECRET.sub(lambda match: match.group(1) + REDACTED + match.group(4), text)


def redact_value(value: Any, key_hint: str = "", secrets: Iterable[str] = ()) -> Any:
    """One value destined for a ledger or a result: masked whole under a secret-named key, else field by field."""
    try:
        if value is None or isinstance(value, bool):
            return value
        if is_secret_name(key_hint):
            return REDACTED
        if isinstance(value, (int, float)):
            return value
        known = list(secrets)
        if isinstance(value, dict):
            return {key: redact_value(item, str(key), known) for key, item in redact_tree(value).items()}
        if isinstance(value, (list, tuple)):
            return [redact_value(item, "", known) for item in value]
        if isinstance(value, str):
            return redact_text(value, known)
        return value
    except Exception:
        return REDACTED
