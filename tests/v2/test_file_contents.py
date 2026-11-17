from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from testzeus_hercules.act.file_contents import FileReadError, file_json, file_table, file_text, file_type, type_matches
from testzeus_hercules.engine.verify import Check, run_file_check
from tests.v2.fixtures.download_server import REPORT_CSV, orders_xlsx, tiny_pdf


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _docx(text: str) -> bytes:
    body = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", body)
    return out.getvalue()


@pytest.mark.parametrize(
    ("name", "content", "kind"),
    [
        ("invoice.pdf", tiny_pdf("Total Due: $950"), "pdf"),
        ("orders.xlsx", orders_xlsx(), "xlsx"),
        ("letter.docx", _docx("Dear Ada"), "docx"),
        ("archive.zip", b"PK\x03\x04" + b"\x00" * 30, "binary"),
        ("report.csv", REPORT_CSV, "csv"),
        ("report.txt", REPORT_CSV, "csv"),
        ("semicolons.dat", b"a;b;c\n1;2;3\n4;5;6\n", "csv"),
        ("orders.json", b'{"count": 2}', "json"),
        ("notes.txt", "Café opens at 9\n".encode(), "text"),
        ("photo.bin", b"\x89PNG\r\n\x1a\n\x00", "png"),
        ("data.bin", b"\x00\x01\x02\xff", "binary"),
        ("renamed.csv", tiny_pdf("not a table"), "pdf"),
    ],
)
def test_the_type_comes_from_the_content_not_the_name(tmp_path: Path, name: str, content: bytes, kind: str) -> None:
    assert file_type(_write(tmp_path, name, content)) == kind


def test_type_names_a_step_may_use() -> None:
    assert type_matches("pdf", "PDF") and type_matches("pdf", "a PDF file")
    assert type_matches("xlsx", "Excel") and type_matches("xlsx", "spreadsheet") and type_matches("csv", "spreadsheet")
    assert type_matches("png", "image") and not type_matches("pdf", "image") and not type_matches("csv", "PDF")


def test_text_rows_and_json_are_read_from_each_format(tmp_path: Path) -> None:
    assert "Total Due: $950" in file_text(_write(tmp_path, "invoice.pdf", tiny_pdf("Total Due: $950")))
    assert file_text(_write(tmp_path, "letter.docx", _docx("Dear Ada"))) == "Dear Ada"
    assert "1002\tPaid\t80.5" in file_text(_write(tmp_path, "orders.xlsx", orders_xlsx()))
    names, rows = file_table(tmp_path / "orders.xlsx")
    assert names == ["Order ID", "Status", "Total"] and rows[0] == ["1001", "Paid", "120"] and len(rows) == 3
    names, rows = file_table(_write(tmp_path, "bom.csv", b"\xef\xbb\xbf" + REPORT_CSV))
    assert names[0] == "Order ID" and len(rows) == 3
    assert file_json(_write(tmp_path, "orders.json", b'{"count": 2}')) == {"count": 2}
    with pytest.raises(FileReadError, match="no text"):
        file_text(_write(tmp_path, "photo.png", b"\x89PNG\r\n\x1a\n\x00"))
    with pytest.raises(FileReadError, match="not a CSV or a spreadsheet"):
        file_table(tmp_path / "invoice.pdf")


def _check(kind: str, expected: str, **fields: str) -> Check:
    return Check(kind=kind, expected=expected, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("check", "verdict", "seen"),
    [
        (_check("file_name", "report.csv", match="equals"), "passed", "report.csv"),
        (_check("file_name", "report", match="contains"), "passed", "report.csv"),
        (_check("file_name", "orders.csv", match="equals"), "failed", "report.csv"),
        (_check("file_type", "CSV"), "passed", "a csv file"),
        (_check("file_type", "PDF"), "failed", "a csv file"),
        (_check("file_text", "Grace"), "passed", "found"),
        (_check("file_not_text", "Refunded"), "passed", "not in the file"),
        (_check("file_not_text", "Linus"), "failed", "holds this text"),
        (_check("file_rows", "3"), "passed", "3 rows"),
        (_check("file_rows", ">= 2"), "passed", "3 rows"),
        (_check("file_rows", "at most 2"), "failed", "3 rows"),
        (_check("file_column", "Paid", subject="Status", match="equals"), "failed", "1 of 3 rows differ"),
        (_check("file_column", "Paid", subject="status", where="Order ID = 1002", match="equals"), "passed", "1 row hold"),
        (_check("file_column", "Paid", subject="Status", where="Order ID = 9999", match="equals"), "failed", "no row has Order ID = 9999"),
        (_check("file_column", "Paid", subject="State", match="equals"), "failed", "no column 'State'"),
    ],
)
def test_file_checks_on_a_downloaded_csv(tmp_path: Path, check: Check, verdict: str, seen: str) -> None:
    entry = run_file_check(check, [_write(tmp_path, "report.csv", REPORT_CSV)], step=4, redact=lambda text: text)
    assert entry.verdict == verdict and seen in entry.actual, entry
    assert entry.source == "deterministic"


def test_json_pdf_and_missing_downloads(tmp_path: Path) -> None:
    orders = _write(tmp_path, "orders.json", b'{"orders": [{"id": 1001, "total": 120.0}], "count": 2}')
    pdf = _write(tmp_path, "invoice.pdf", tiny_pdf("Invoice INV-77 Total Due: $950"))
    identity = lambda text: text  # noqa: E731

    assert run_file_check(_check("file_json", "120", subject="$.orders[0].total", match="equals"), [orders], step=1, redact=identity).verdict == "passed"
    assert run_file_check(_check("file_json", "2", subject="count", match="equals"), [orders], step=1, redact=identity).verdict == "passed"
    missing = run_file_check(_check("file_json", "1", subject="$.nothing", match="equals"), [orders], step=1, redact=identity)
    assert missing.verdict == "failed" and "no value at $.nothing" in missing.actual
    assert run_file_check(_check("file_text", "total due: $950"), [orders, pdf], step=1, redact=identity).verdict == "passed", "the latest download is checked"
    assert run_file_check(_check("file_rows", "1"), [pdf], step=1, redact=identity).verdict == "failed"
    none = run_file_check(_check("file_name", "x.csv"), [], step=1, redact=identity)
    assert none.verdict == "failed" and none.actual == "no file was downloaded before this check"
    masked = run_file_check(_check("file_text", "INV-77"), [pdf], step=1, redact=lambda text: text.replace("INV-77", "******"))
    assert "INV-77" not in masked.expected + masked.actual
