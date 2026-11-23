"""What a downloaded file holds: its type from its first bytes, its text, its rows and its JSON values.

Read in code only; no model ever receives a downloaded file.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Literal

from testzeus_hercules.cases.importers.common import ImporterError
from testzeus_hercules.cases.importers.doc import _docx_text, _pdf_text

FileType = Literal["pdf", "docx", "xlsx", "zip", "csv", "json", "text", "png", "jpeg", "gif", "binary"]
MAX_READ_BYTES = 50 * 1024 * 1024
MAX_ROWS = 100_000
_HEAD = 8192
_TABLE_SUFFIXES = frozenset({".csv", ".tsv"})

TYPE_NAMES: dict[str, frozenset[str]] = {
    "pdf": frozenset({"pdf"}),
    "csv": frozenset({"csv"}),
    "tsv": frozenset({"csv"}),
    "excel": frozenset({"xlsx"}),
    "xlsx": frozenset({"xlsx"}),
    "spreadsheet": frozenset({"xlsx", "csv"}),
    "word": frozenset({"docx"}),
    "docx": frozenset({"docx"}),
    "json": frozenset({"json"}),
    "text": frozenset({"text", "csv", "json"}),
    "txt": frozenset({"text"}),
    "zip": frozenset({"zip", "docx", "xlsx"}),
    "png": frozenset({"png"}),
    "jpeg": frozenset({"jpeg"}),
    "jpg": frozenset({"jpeg"}),
    "gif": frozenset({"gif"}),
    "image": frozenset({"png", "jpeg", "gif"}),
}


class FileReadError(ValueError):
    """The file cannot be read the way a check asks; the message is safe to show."""


def _head(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(_HEAD)


def _decoded(data: bytes) -> str | None:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _zip_type(path: Path) -> FileType:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return "binary"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def _looks_like_table(text: str) -> bool:
    lines = [line for line in text.splitlines()[:20] if line.strip()]
    if len(lines) < 2:
        return False
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",;\t|")
    except csv.Error:
        return False
    widths = {len(row) for row in csv.reader(lines, dialect)}
    return len(widths) == 1 and widths.pop() > 1


def file_type(path: Path) -> FileType:
    """The file's type from its content (its name is only a hint for tables)."""
    head = _head(path)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _zip_type(path)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    text = None
    if b"\x00" not in head:
        text = next((decoded for cut in range(4) if (decoded := _decoded(head[: len(head) - cut])) is not None), None)
    if text is None:
        return "binary"
    stripped = text.lstrip()
    if stripped[:1] in ("{", "[") and path.stat().st_size <= MAX_READ_BYTES:
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
            return "json"
        except (ValueError, UnicodeDecodeError):
            pass
    if path.suffix.lower() in _TABLE_SUFFIXES or _looks_like_table(text):
        return "csv"
    return "text"


def type_matches(actual: FileType, wanted: str) -> bool:
    """True when ``actual`` is the type a step names (``PDF``, ``an Excel file``, ``CSV``, ``image``)."""
    words = [word.strip(".").lower() for word in wanted.replace("-", " ").split()]
    accepted = set().union(*(TYPE_NAMES.get(word, frozenset()) for word in words)) if words else set()
    return actual in accepted


def _checked_size(path: Path) -> None:
    if path.stat().st_size > MAX_READ_BYTES:
        raise FileReadError(f"{path.name} is larger than {MAX_READ_BYTES // (1024 * 1024)} MB; it is not read")


def file_text(path: Path) -> str:
    """The file's text: a PDF's or Word file's text, a sheet's cells, or the file itself as UTF-8."""
    _checked_size(path)
    kind = file_type(path)
    try:
        if kind == "pdf":
            text = _pdf_text(path)
            if not text:
                raise FileReadError(f"{path.name} is a PDF with no text in it (a scan?); its text cannot be checked")
            return text
        if kind == "docx":
            return _docx_text(path)
    except ImporterError as exc:
        raise FileReadError(str(exc)) from None
    if kind == "xlsx":
        return "\n".join("\t".join(row) for row in file_rows(path, header=False))
    if kind in ("csv", "json", "text"):
        return path.read_text(encoding="utf-8-sig", errors="replace")
    raise FileReadError(f"{path.name} is a {kind} file; it has no text to check")


def _csv_rows(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = "\n".join(text.splitlines()[:20])
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = "excel-tab" if path.suffix.lower() == ".tsv" else "excel"
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text), dialect):
        if len(rows) > MAX_ROWS:
            raise FileReadError(f"{path.name} has more than {MAX_ROWS:,} rows; it is not read")
        if any(cell.strip() for cell in row):
            rows.append([cell.strip() for cell in row])
    return rows


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _xlsx_rows(path: Path, sheet: str | None) -> list[list[str]]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for damaged files
        raise FileReadError(f"cannot read the spreadsheet {path.name} ({type(exc).__name__})") from None
    try:
        if sheet is not None:
            match = next((name for name in book.sheetnames if name.casefold() == sheet.casefold()), None)
            if match is None:
                raise FileReadError(f"{path.name} has no sheet named {sheet!r} (sheets: {', '.join(book.sheetnames)})")
            worksheet = book[match]
        else:
            worksheet = book.worksheets[0]
        rows: list[list[str]] = []
        for values in worksheet.iter_rows(values_only=True):
            if len(rows) > MAX_ROWS:
                raise FileReadError(f"{path.name} has more than {MAX_ROWS:,} rows; it is not read")
            row = [_cell(value) for value in values]
            if any(row):
                rows.append(row)
        return rows
    finally:
        book.close()


def file_rows(path: Path, *, sheet: str | None = None, header: bool = True) -> list[list[str]]:
    """A CSV's or a sheet's non-empty rows, cells stripped; with ``header`` the first row is the column names."""
    _checked_size(path)
    kind = file_type(path)
    if kind == "csv":
        return _csv_rows(path)
    if kind == "xlsx":
        return _xlsx_rows(path, sheet)
    raise FileReadError(f"{path.name} is a {kind} file, not a CSV or a spreadsheet; it has no rows")


def file_table(path: Path, *, sheet: str | None = None) -> tuple[list[str], list[list[str]]]:
    """The column names (the first row) and the data rows of a CSV or sheet."""
    rows = file_rows(path, sheet=sheet)
    if not rows:
        raise FileReadError(f"{path.name} is empty")
    return rows[0], rows[1:]


def file_json(path: Path) -> Any:
    _checked_size(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise FileReadError(f"{path.name} is not valid JSON") from None
