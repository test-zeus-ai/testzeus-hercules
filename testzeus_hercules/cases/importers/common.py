"""Shared pieces of the importers: the report, planned files, safe writing and secret handling.

Importers never write directly. They build a list of :class:`PlannedFile` and hand
it to :func:`write_plan`, which keeps every path inside the project's ``.hercules``
folder, refuses to replace an existing file unless ``force`` is set, writes nothing
in a dry run, and records each outcome in the :class:`ImportReport`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from testzeus_hercules.cases.gherkin import slugify
from testzeus_hercules.cases.models import RESERVED_PLACEHOLDERS, SECRET_NAME_PATTERN, TestCase

PROJECT_DIRNAME = ".hercules"
FileKind = Literal["case", "env", "data", "doc"]

# A key names a credential when one of its words (camelCase and snake_case split, case-insensitive) is one of these ...
_SECRET_WORDS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "passcode",
        "pass",
        "pwd",
        "pin",
        "pincode",
        "secret",
        "secrets",
        "token",
        "tokens",
        "credential",
        "credentials",
        "creds",
        "auth",
        "authorization",
        "cookie",
        "cookies",
        "session",
        "sessionid",
        "sid",
        "jwt",
        "otp",
        "totp",
        "hotp",
        "mfa",
        "bearer",
        "apikey",
        "apikeys",
        "privatekey",
        "accesskey",
        "secretkey",
        "signature",
    }
)
# ... or two adjacent words are one of these ("api key", "private key", "client secret") ...
_SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"), ("client", "secret"), ("signing", "key"), ("encryption", "key")})
# ... or a word ends with one of these (userpassword, dbpassword, apitoken, clientsecret).
_SECRET_SUFFIXES = ("password", "passwd", "passphrase", "passcode", "token", "secret", "apikey", "privatekey", "accesskey", "secretkey", "credential", "credentials")
_SECRET_PREFIXES = ("password", "passwd", "passphrase", "secretkey", "apikey", "privatekey", "accesskey")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
# A value that is itself a credential: a URL with a password, a private key, a JWT, a bearer token.
_SECRET_VALUE = re.compile(
    r"^[a-z][a-z0-9+.\-]*://[^/\s:@]*:[^/\s@]+@|-----BEGIN [A-Z ]*PRIVATE KEY-----|^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.|^bearer\s+\S{8,}",
    re.IGNORECASE,
)
# A data file that holds credentials rather than test data: never copied into the project.
_SENSITIVE_FILE = re.compile(
    r"^\.env|^id_[a-z0-9]+|credential|secret|service[-_]?account|\.(?:pem|key|p12|pfx|jks|keystore|ppk|kdbx|asc|gpg)$|^\.netrc$|^\.pgpass$|^\.npmrc$|^\.pypirc$",
    re.IGNORECASE,
)
_SENSITIVE_CONTENT = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|\"private_key\"\s*:|\"type\"\s*:\s*\"service_account\"")
_NOT_NAME = re.compile(r"[^A-Za-z0-9_]+")
_SECRET_NAME = re.compile(SECRET_NAME_PATTERN)
_MAX_CASE_ID = 100


class ImporterError(ValueError):
    """The import cannot start or the source cannot be read; the message is safe to show."""


@dataclass(frozen=True)
class Converted:
    """A file the import wrote (or would write in a dry run), relative to the project."""

    kind: FileKind
    path: str
    source: str


@dataclass(frozen=True)
class Skipped:
    """Something that was not converted, and why. Never carries a secret value."""

    source: str
    reason: str


@dataclass
class ImportReport:
    """What an import converted, skipped and noticed."""

    title: str
    dry_run: bool = False
    converted: list[Converted] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    secret_names: list[str] = field(default_factory=list)

    def skip(self, source: str, reason: str) -> None:
        self.skipped.append(Skipped(source=source, reason=reason))

    def add_secret(self, name: str) -> None:
        if name not in self.secret_names:
            self.secret_names.append(name)

    @property
    def cases_converted(self) -> int:
        return sum(item.kind == "case" for item in self.converted)

    def render(self) -> str:
        """Plain-text report for the terminal."""
        heading = self.title + (" (dry run: nothing was written)" if self.dry_run else "")
        verb = "Would write" if self.dry_run else "Converted"
        lines = [heading, f"{verb} ({len(self.converted)}):"]
        lines += [f"  {item.kind:<4}  {item.path}  (from {item.source})" for item in self.converted] or ["  nothing"]
        if self.skipped:
            lines.append(f"Skipped ({len(self.skipped)}):")
            lines += [f"  {item.source}: {item.reason}" for item in self.skipped]
        if self.secret_names:
            lines.append("Secrets referenced by name (set these environment variables before running): " + ", ".join(sorted(self.secret_names)))
        if self.notes:
            lines.append("Notes:")
            lines += [f"  {note}" for note in self.notes]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class PlannedFile:
    """A file an importer wants to write, relative to the project's ``.hercules`` folder."""

    kind: FileKind
    relative: str
    content: str | bytes
    source: str


def key_words(key: str) -> list[str]:
    """The words of a key, lower case: ``userPassword`` and ``user_password`` are both ``["user", "password"]``."""
    return [word.lower() for word in _WORD_SPLIT.split(_CAMEL.sub(" ", key)) if word]


def looks_secret(key: str, value: str | None = None) -> bool:
    """True when a variable or data key names a credential (password, token, API key, OTP, ...), or its value is one
    (a ``scheme://user:password@host`` URL, a private key, a JWT, a bearer token). Words are matched whole, so
    ``footprint`` is not ``otp`` and ``passport_number`` is not a password."""
    words = key_words(key)
    if any(word in _SECRET_WORDS or word.endswith(_SECRET_SUFFIXES) or word.startswith(_SECRET_PREFIXES) for word in words):
        return True
    if any(pair in _SECRET_PAIRS for pair in zip(words, words[1:])):
        return True
    return value is not None and _SECRET_VALUE.search(value.strip()) is not None


def sensitive_file(name: str, head: bytes = b"") -> bool:
    """True when a file (by its name, or the first bytes of its content) holds credentials: a key, a certificate
    store, a service-account file, a ``.env``. Such files are never copied into a project."""
    return _SENSITIVE_FILE.search(name) is not None or _SENSITIVE_CONTENT.search(head) is not None


def secret_name(key: str) -> str:
    """An environment-variable name for a secret key: upper case, ``[A-Z0-9_]`` only."""
    name = _NOT_NAME.sub("_", key).strip("_").upper() or "SECRET"
    if name[0].isdigit():
        name = "_" + name
    assert _SECRET_NAME.fullmatch(name)
    return name


def data_key(raw: str) -> str | None:
    """A valid ``test_data`` key for ``raw`` (non-name characters become ``_``), or None when reserved or empty."""
    key = _NOT_NAME.sub("_", raw).strip("_")
    if not key:
        return None
    if key[0].isdigit():
        key = "_" + key
    return None if key in RESERVED_PLACEHOLDERS else key


def unique_case_id(base: str, taken: set[str]) -> str:
    """``base`` (already a slug) or ``base-2``, ``base-3``... not in ``taken``; the result is added to ``taken``."""
    candidate = slugify(base)[:_MAX_CASE_ID].strip("-") or "case"
    number = 1
    while candidate in taken:
        number += 1
        suffix = f"-{number}"
        candidate = slugify(base)[: _MAX_CASE_ID - len(suffix)].strip("-") + suffix
    taken.add(candidate)
    return candidate


def case_file(case: TestCase, source: str) -> PlannedFile:
    return PlannedFile(kind="case", relative=f"cases/{case.id}.json", content=case.model_dump_json(indent=2, exclude_none=True) + "\n", source=source)


def write_plan(project_dir: Path, files: list[PlannedFile], report: ImportReport, *, dry_run: bool, force: bool) -> None:
    """Write ``files`` under ``<project>/.hercules`` (or only plan them in a dry run), recording each outcome."""
    root = (project_dir / PROJECT_DIRNAME).resolve()
    for planned in files:
        target = (root / planned.relative).resolve()
        shown = f"{PROJECT_DIRNAME}/{planned.relative}"
        if not target.is_relative_to(root) or target == root:
            report.skip(planned.source, f"refusing to write {shown}: outside the project's {PROJECT_DIRNAME} folder")
            continue
        unresolved = root / planned.relative
        if unresolved.is_symlink():
            report.skip(shown, "is a symbolic link; refusing to write through it")
            continue
        if unresolved.exists() and not force:
            report.skip(shown, "already exists; pass --force to replace it")
            continue
        if not dry_run:
            _write_atomic(unresolved, planned.content)
        report.converted.append(Converted(kind=planned.kind, path=shown, source=planned.source))


def _write_atomic(path: Path, content: str | bytes) -> None:
    """Write ``content`` to a temporary file beside ``path``, flush it to disk, then move it in place; the temporary
    file is removed when anything fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def relative_to(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name
