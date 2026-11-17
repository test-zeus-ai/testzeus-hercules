from __future__ import annotations

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.act.file_contents import file_table, file_type
from testzeus_hercules.cases.automation import AutomationCheck, AutomationSpec, AutomationStep
from testzeus_hercules.cases.models import RunResult, StepResult
from testzeus_hercules.engine.verify import Check, run_file_check
from testzeus_hercules.export import export_run
from testzeus_hercules.export.typescript import HELPERS

from tests.v2.export_harness import UI_TYPESCRIPT, node

ORDERS = 'Order  id, Customer ,Status\n1001,"Lovelace, Ada",paid\n1002,"Hopper,\nGrace",PAID\n1003,Linus,Pending\n'
LEDGER = 'Name;Amount\n"Smith; J";10\nDoe;20\n'
DOCUMENT = {"count": 2.0, "status": " Paid ", "items": [1, 2], "owner": {"name": "Ada"}, "none": None}

CASES: list[tuple[str, Check, bool]] = [
    ("orders", Check(kind="file_name", expected="orders.csv", match="equals"), True),
    ("orders", Check(kind="file_rows", expected="3"), True),
    ("orders", Check(kind="file_column", expected="Paid", match="equals", subject="status", where="Order ID = 1002"), True),
    ("orders", Check(kind="file_column", expected="grace", match="contains", subject="Customer", where="order id=1002"), True),
    ("orders", Check(kind="file_column", expected="Paid", match="equals", subject="Status"), False),
    ("orders", Check(kind="file_column", expected="Paid", match="equals", subject="State"), False),
    ("orders", Check(kind="file_column", expected="Paid", match="equals", subject="Status", where="Order ID = 9999"), False),
    ("ledger", Check(kind="file_rows", expected="2"), True),
    ("ledger", Check(kind="file_column", expected="10", match="equals", subject="Amount", where="Name = Smith; J"), True),
    ("json", Check(kind="file_json", expected="2", match="equals", subject="$.count"), True),
    ("json", Check(kind="file_json", expected="paid", match="equals", subject="$.status"), True),
    ("json", Check(kind="file_json", expected="[1, 2]", match="equals", subject="$.items"), True),
    ("json", Check(kind="file_json", expected='{"name": "Ada"}', match="equals", subject="$.owner"), True),
    ("json", Check(kind="file_json", expected="", match="equals", subject="$.none"), True),
    ("json", Check(kind="file_json", expected="", match="equals", subject="$.absent"), False),
    ("xlsx", Check(kind="file_type", expected="an Excel file"), True),
    ("xlsx", Check(kind="file_type", expected="zip"), True),
    ("xlsx", Check(kind="file_type", expected="Word"), False),
    ("docx", Check(kind="file_type", expected="Word document"), True),
    ("docx", Check(kind="file_type", expected="Excel"), False),
    ("zip", Check(kind="file_type", expected="Excel"), False),
    ("zip", Check(kind="file_type", expected="Word"), False),
    ("zip", Check(kind="file_type", expected="ZIP"), True),
    ("pdf", Check(kind="file_type", expected="PDF"), True),
    ("pdf", Check(kind="file_type", expected="image"), False),
]


def _zip(path: Path, entries: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in entries:
            archive.writestr(name, "<x/>")
    return path


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "Orders.CSV").write_text(ORDERS, encoding="utf-8")
    (folder / "ledger.csv").write_text(LEDGER, encoding="utf-8")
    (folder / "orders.json").write_text(json.dumps(DOCUMENT), encoding="utf-8")
    (folder / "invoice.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return {
        "orders": folder / "Orders.CSV",
        "ledger": folder / "ledger.csv",
        "json": folder / "orders.json",
        "pdf": folder / "invoice.pdf",
        "xlsx": _zip(folder / "book.xlsx", ["[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"]),
        "docx": _zip(folder / "letter.docx", ["[Content_Types].xml", "word/document.xml"]),
        "zip": _zip(folder / "bundle.zip", ["notes.txt", "backup/word/document.xml.bak", "backup/xl/workbook.xml.bak"]),
    }


def _exported_python_holds(check: Check, file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> bool:
    automation = AutomationCheck(kind="file", file_check=check.kind.removeprefix("file_"), expected=check.expected, match=check.match, subject=check.subject, where=check.where)  # type: ignore[arg-type]
    step = AutomationStep(index=1, action="check the downloaded file", status="passed", checks=[automation])
    run = RunResult(
        run_id="r_20261118_120000_abcdef",
        status="passed",
        case_id="files",
        steps=[StepResult(index=1, action=step.action, status="passed")],
        automation=AutomationSpec(case_id="files", title="Files", steps=[step]),
    )
    code = export_run(run, "playwright-py").code
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    namespace: dict[str, Any] = {}
    exec(compile(code, "test_files.py", "exec"), namespace)
    namespace["downloaded"].append(file)
    try:
        namespace["test_files"](None)
    except AssertionError:
        return False
    return True


@pytest.mark.parametrize(("name", "check", "holds"), CASES, ids=[f"{index}-{name}-{check.kind}" for index, (name, check, _) in enumerate(CASES)])
def test_an_exported_python_file_check_gives_the_runs_verdict(name: str, check: Check, holds: bool, files: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    file = files[name]

    assert run_file_check(check, [file], step=1, redact=lambda text: text).verdict == ("passed" if holds else "failed")
    assert _exported_python_holds(check, file, monkeypatch, tmp_path) is holds


_STUBS = """
const expect = (actual: unknown, message?: string) => ({
  toBeGreaterThanOrEqual(bound: number) {
    if (!((actual as number) >= bound)) throw new Error(message);
  },
  not: {
    toBeUndefined() {
      if (actual === undefined) throw new Error(message);
    },
  },
});
const test = { info: () => ({ outputPath: (name: string) => name }) };
"""

_DRIVER = """
async function main(): Promise<void> {
  const calls: Record<string, (...args: any[]) => Promise<unknown>> = { rows: downloadedRows, cells: downloadedCells, type: downloadedType, value: downloadedValue };
  const results: unknown[] = [];
  for (const [file, call, ...args] of JSON.parse(process.argv[2]) as Array<[string, string, ...unknown[]]>) {
    downloadedFile = file;
    try {
      results.push({ value: await calls[call](...args) });
    } catch (error) {
      results.push({ error: String(error) });
    }
  }
  process.stdout.write(JSON.stringify(results));
}
main();
"""

_TRANSPILE = """
const ts = require(process.argv[2]);
const fs = require("fs");
const out = ts.transpileModule(fs.readFileSync(process.argv[3], "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
fs.writeFileSync(process.argv[4], out.outputText);
"""


def _typescript_helpers(jobs: list[list[Any]], folder: Path) -> list[dict[str, Any]]:
    helpers = "\n".join(HELPERS.emit({"download_rows", "download_cells", "download_type", "download_value"}))
    (folder / "helpers.ts").write_text(_STUBS + helpers + _DRIVER, encoding="utf-8")
    (folder / "transpile.cjs").write_text(_TRANSPILE, encoding="utf-8")
    subprocess.run([node() or "node", str(folder / "transpile.cjs"), str(UI_TYPESCRIPT), str(folder / "helpers.ts"), str(folder / "helpers.cjs")], check=True, timeout=120)
    done = subprocess.run([node() or "node", str(folder / "helpers.cjs"), json.dumps(jobs)], capture_output=True, text=True, timeout=120, check=True)
    return list(json.loads(done.stdout))


@pytest.mark.skipif(node() is None or not UI_TYPESCRIPT.is_dir(), reason="node and ui/node_modules/typescript are needed to run the TypeScript helpers")
def test_the_exported_typescript_helpers_read_files_as_the_run_does(files: dict[str, Path], tmp_path: Path) -> None:
    kinds = ["xlsx", "docx", "zip", "pdf", "json"]
    jobs: list[list[Any]] = [
        [str(files["orders"]), "rows"],
        [str(files["ledger"]), "rows"],
        [str(files["orders"]), "cells", "status", ["Order ID", "1002"]],
        [str(files["orders"]), "cells", "customer", ["order  ID", "1002"]],
        [str(files["orders"]), "cells", "Status"],
        [str(files["orders"]), "cells", "State"],
        [str(files["ledger"]), "cells", "amount", ["name", "smith; j"]],
        *[[str(files[kind]), "type"] for kind in kinds],
        *[[str(files["json"]), "value", [key]] for key in ("count", "status", "items", "owner", "none", "absent")],
    ]

    results = _typescript_helpers(jobs, tmp_path)

    orders, ledger = file_table(files["orders"]), file_table(files["ledger"])
    assert results[0] == {"value": {"names": orders[0], "rows": orders[1]}}
    assert results[1] == {"value": {"names": ledger[0], "rows": ledger[1]}}
    assert orders[1][1] == ["1002", "Hopper,\nGrace", "PAID"] and ledger[1][0] == ["Smith; J", "10"]
    assert results[2:5] == [{"value": ["PAID"]}, {"value": ["Hopper,\nGrace"]}, {"value": ["paid", "PAID", "Pending"]}]
    assert "has no column" in results[5]["error"]
    assert results[6] == {"value": ["10"]}
    assert [result["value"] for result in results[7:12]] == [file_type(files[kind]) for kind in kinds[:4]] + ["other"]
    assert [result.get("value") for result in results[12:17]] == ["2", " Paid ", "[1, 2]", '{"name": "Ada"}', ""]
    assert "has no value" in results[17]["error"]
