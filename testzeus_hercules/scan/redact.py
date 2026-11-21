"""Redaction of the scanner's command line and output.

The scanner echoes requests it sends (with ``-v``) and writes findings that may quote
headers, cookies and URLs. Everything it prints or writes passes through
:func:`redact_tool_output` before it is logged, kept as evidence or shown: known secret
values, the values of credential headers wherever they appear on a line, and URL
credentials. A finding line whose template id merely contains a word like ``token``
(``[jwt-token-exposure:bearer]``) is not a header and is kept as written.

All functions run in linear time on any input and never raise.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from testzeus_hercules.act.redact import REDACTED, SECRET_HEADER_PARTS, is_secret_name, mask_url_credentials, redact_url, scrub_values

__all__ = ["credential_header_values", "is_credential_header", "redact_command_for_log", "redact_tool_output"]

# A secret-bearing header name: any name containing one of the header fragments (authorization, cookie, token,
# api-key, ...), with at most _NAME_CONTEXT_CHARS further name characters after the fragment. Matching is a single
# linear pass over the lines that mention a fragment (no regex backtracking).
_NAME_CONTEXT_CHARS = 64
_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_HEADER_FRAGMENTS = tuple(sorted(set(SECRET_HEADER_PARTS), key=len, reverse=True))
_MAX_FRAGMENT_LEN = max(len(fragment) for fragment in _HEADER_FRAGMENTS)
# Invisible characters inserted inside a header name to dodge the match: NUL, soft hyphen, Arabic letter mark,
# Mongolian vowel separator, zero-width and bidi marks, word joiners, BOM.
_INVISIBLE_CHARS = dict.fromkeys([0x00, 0xAD, 0x061C, 0x180E, *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2065), 0xFEFF])
_QUOTES = "'\""
# A header name counts as a header only when it stands alone: at the start of the line or after whitespace, a quote
# or one of these separators. Anything else (the "[" of a "[jwt-token-exposure:bearer]" finding, the "/" of a URL
# path "/token:") is a template id, a path or free text that merely contains the word.
_HEADER_NAME_LEADERS = frozenset(" \t\f\v\r'\"(,=>*")
_HEADER_FLAGS = ("-H", "-header")
_TARGET_FLAGS = ("-u", "-target")


def is_credential_header(name: str) -> bool:
    """True for headers that carry a credential (Authorization, Cookie, X-Api-Key, X-Auth-Token, ...)."""
    lowered = str(name).lower()
    return is_secret_name(lowered) or any(part in lowered for part in SECRET_HEADER_PARTS)


def credential_header_values(headers: Iterable[tuple[str, str]] | None) -> list[str]:
    """Values of the credential headers sent to the scanner, plus the bare credential after a scheme ("Bearer <token>").

    Ordinary headers (Accept, User-Agent, ...) are never secrets: scrubbing "application/json" from the whole output
    would make every finding that mentions it unreadable.
    """
    secrets: list[str] = []
    for name, value in headers or ():
        if not is_credential_header(name):
            continue
        secrets.append(value)
        secrets.extend(value.split(" ", 1)[1:])
    return secrets


def redact_command_for_log(command: Sequence[str]) -> str:
    """A scanner command line for logs and evidence: header values and URL credentials are masked.

    Only the returned text is affected; the command that runs is never changed.
    """
    parts: list[str] = []
    redact_next_header = False
    redact_next_url = False
    for arg in command:
        if redact_next_header:
            name, sep, _ = arg.partition(":")
            parts.append(f"{name}{sep} {REDACTED}" if sep else REDACTED)
            redact_next_header = False
        elif redact_next_url:
            parts.append(redact_url(arg))
            redact_next_url = False
        else:
            parts.append(arg)
            redact_next_header = arg in _HEADER_FLAGS
            redact_next_url = arg in _TARGET_FLAGS
    return " ".join(parts)


def _secret_name_before(line: str, colon: int) -> int | None:
    """Start of the secret header name that ends right before ``line[colon]`` (whitespace allowed before the colon)."""
    end = colon
    while end > 0 and line[end - 1] in " \t\f\v":
        end -= 1
    start = end
    limit = max(0, end - (_NAME_CONTEXT_CHARS + _MAX_FRAGMENT_LEN))
    while start > limit and line[start - 1] in _NAME_CHARS:
        start -= 1
    if start == end:
        return None
    # A name longer than the scan window was cut short by the bound: its real start is unknown, so no leader check.
    if limit < start and line[start - 1] not in _HEADER_NAME_LEADERS:
        return None
    window = line[start:end].lower()
    cutoff = len(window) - _NAME_CONTEXT_CHARS
    for fragment in _HEADER_FRAGMENTS:
        index = window.rfind(fragment)
        if index != -1 and index + len(fragment) >= cutoff:
            return start
    return None


class _ForwardFinder:
    """``line.find(char, start)`` for non-decreasing ``start`` values, scanning each character's tail at most once."""

    def __init__(self, line: str) -> None:
        self._line = line
        self._next: dict[str, int] = {}

    def find(self, char: str, start: int) -> int:
        cached = self._next.get(char)
        if cached is None or (cached != -1 and cached < start):
            cached = self._line.find(char, start)
            self._next[char] = cached
        return cached


def _quote_before(line: str, name_start: int) -> str | None:
    if name_start > 0 and line[name_start - 1] in _QUOTES:
        return line[name_start - 1]
    before = name_start
    while before > 0 and line[before - 1] in " \t\f\v":
        before -= 1
    return line[before - 1] if before > 0 and line[before - 1] in _QUOTES else None


def _redact_header_values_in_line(line: str, xml: bool) -> str:
    """Mask the value of every secret-named ``Name: value`` header in one line (linear in the line length)."""
    lowered = line.lower()
    if ":" not in line or not any(fragment in lowered for fragment in _HEADER_FRAGMENTS):
        return line
    eol = len(line.rstrip("\r\n"))  # a trailing "\r" stays out of the value
    finder = _ForwardFinder(line)
    out: list[str] = []
    cursor = 0
    colon = line.find(":")
    while colon != -1:
        name_start = _secret_name_before(line, colon)
        if name_start is None:
            colon = line.find(":", colon + 1)
            continue
        value_start = colon + 1
        while value_start < len(line) and line[value_start] in " \t\f\v":
            value_start += 1
        if line.startswith(REDACTED, value_start):
            colon = line.find(":", value_start + len(REDACTED))
            continue
        # The value ends at a markup or quote boundary (xml), at the matching quote when the header sits in a quoted
        # argument ('Authorization: Bearer x'), else at the end of the line.
        value_end = eol
        opened_by = _quote_before(line, name_start)
        quoted_end = finder.find(opened_by, value_start) if opened_by else -1
        if xml:
            boundary = min((index for index in (finder.find(char, value_start) for char in ("<", '"', "'")) if index != -1), default=eol)
            value_end = min(boundary, eol)
            if value_end <= value_start:
                colon = line.find(":", colon + 1)
                continue
        elif quoted_end != -1 and quoted_end <= eol:
            value_end = quoted_end
        elif value_end <= value_start:
            colon = line.find(":", colon + 1)
            continue
        out.append(line[cursor:value_start])
        out.append(REDACTED)
        cursor = value_end
        colon = line.find(":", value_end)
    out.append(line[cursor:])
    return "".join(out)


def redact_tool_output(text: str, secret_values: Iterable[str] = (), xml: bool = False) -> str:
    """Scanner output with secrets masked: known secret values, credential header values wherever they appear on a
    line (``> `` request echoes, quoted ``-H '...'`` arguments, mentions in error messages) and URL credentials.

    Invisible characters (NUL, zero-width and bidi marks, soft hyphens) are removed first, so one inserted inside a
    header name cannot evade the match. ``xml`` ends a value at the next markup or quote boundary.
    """
    try:
        cleaned = scrub_values(str(text), secret_values).translate(_INVISIBLE_CHARS)
        cleaned = "\n".join(_redact_header_values_in_line(line, xml) for line in cleaned.split("\n"))
        return mask_url_credentials(cleaned)
    except Exception:
        return REDACTED
