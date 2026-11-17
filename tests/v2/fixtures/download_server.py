"""A local site that hands out files: attachments, an inline PDF in a new tab, a script-made file, JSON shown inline."""

from __future__ import annotations

import contextlib
import http.server
import io
import threading
from typing import Iterator

REPORT_CSV = b"Order ID,Customer,Status,Total\n1001,Ada,Paid,120.00\n1002,Grace,Paid,80.50\n1003,Linus,Pending,40\n"
ORDERS_JSON = b'{"orders": [{"id": 1001, "total": 120.0}, {"id": 1002, "total": 80.5}], "count": 2, "currency": "USD"}'
NOTES_TXT = b"Notes: orders 1001 and 1002 are paid, order 1003 waits for the customer's card to clear."

PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Reports</title></head>
<body>
<h1>Reports</h1>
<p><a id="csv" href="/files/report.csv">Export CSV</a></p>
<p><a id="csv-again" href="/files/report.csv">Export CSV again</a></p>
<p><a id="invoice" href="/files/invoice.pdf">Download invoice</a></p>
<p><a id="statement" href="/view/statement.pdf" target="_blank">Open statement</a></p>
<p><a id="notes" href="/view/notes.txt" target="_blank">Open notes</a></p>
<p><a id="sheet" href="/files/orders.xlsx">Download spreadsheet</a></p>
<p><a id="empty" href="/files/empty.csv">Export empty report</a></p>
<p><button id="summary" type="button" onclick="makeSummary()">Generate summary</button></p>
<p><button id="nothing" type="button">Refresh</button></p>
<p><a id="plain" href="/plain.html">About reports</a></p>
<p><label for="attach">Attachment</label> <input id="attach" type="file" onchange="document.getElementById('attached').textContent = 'Attached: ' + this.files[0].name"></p>
<p id="attached" role="status"></p>
<script>
  function makeSummary() {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob(["Summary: 3 orders, 2 paid"], { type: "text/plain" }));
    link.download = "summary.txt";
    document.body.appendChild(link);
    link.click();
  }
</script>
</body></html>"""


def tiny_pdf(text: str) -> bytes:
    """A one-page PDF whose page shows ``text`` (readable by pypdf)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    start = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start))
    return out.getvalue()


def orders_xlsx() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Orders"
    for row in (["Order ID", "Status", "Total"], [1001, "Paid", 120], [1002, "Paid", 80.5], [1003, "Pending", 40]):
        sheet.append(row)
    book.create_sheet("Notes").append(["Generated for the test"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


FILES: dict[str, tuple[bytes, str, str]] = {
    "/downloads.html": (PAGE, "text/html; charset=utf-8", ""),
    "/plain.html": (b"<!doctype html><title>About</title><p>Reports are made nightly.</p>", "text/html", ""),
    "/files/report.csv": (REPORT_CSV, "text/csv", 'attachment; filename="report.csv"'),
    "/files/invoice.pdf": (tiny_pdf("Invoice INV-77 Total Due: $950"), "application/pdf", "attachment; filename*=UTF-8''invoice%20INV-77.pdf"),
    "/view/statement.pdf": (tiny_pdf("Statement for November"), "application/pdf", "inline"),
    "/files/orders.xlsx": (b"", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 'attachment; filename="orders.xlsx"'),
    "/files/empty.csv": (b"", "text/csv", 'attachment; filename="empty.csv"'),
    "/data/orders.json": (ORDERS_JSON, "application/json", ""),
    "/view/notes.txt": (NOTES_TXT, "text/plain", "inline"),
    "/data/unsized.txt": (NOTES_TXT, "text/plain", ""),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature from the base class
        return None

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        found = FILES.get(self.path.split("?")[0])
        if found is None:
            self.send_error(404)
            return
        body, kind, disposition = found
        if self.path.startswith("/files/orders.xlsx"):
            body = orders_xlsx()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        if not self.path.startswith("/data/unsized"):
            self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def serve_downloads() -> Iterator[str]:
    """Serve the download site on a free localhost port; yields the base URL."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
