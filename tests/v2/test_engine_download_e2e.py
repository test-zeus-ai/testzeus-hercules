"""End to end on a reports page (``download_server``), in real Chromium through a ``hercules worker`` with the fake local
model server: a click downloads a CSV and its rows and columns are checked in code, an address downloads JSON shown inline,
a PDF's text is checked, the downloaded file is uploaded again with ``{{downloaded_file}}``; no file's content reaches a model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.engine.browser_process import BROWSER_FILE, stop_browser
from testzeus_hercules.export import export_run

from tests.v2.browser_fixtures import chromium_executable
from tests.v2.fixtures.daemon_support import isolated_env
from tests.v2.fixtures.download_server import serve_downloads
from tests.v2.fixtures.fake_model import FakeModel, action

OPEN = 'I open "/downloads.html"'
EXPORT = 'I click "Export CSV" to download the report'
ROWS = "the downloaded file has 3 rows"
PAID = 'column "Status" is "Paid" for Order ID 1002 in the downloaded file'
NAMED = 'the downloaded file is named "report.csv"'
JSON_FILE = 'I download the file from "/data/orders.json"'
COUNT = 'the downloaded JSON has "$.count" equal to "2"'
INVOICE = 'I click "Download invoice"'
PDF = "the downloaded file is a PDF"
DUE = 'the downloaded file contains "Total Due: $950"'
ATTACH = 'I upload "{{downloaded_file}}" to "Attachment"'
NOTHING = 'I click "Refresh" to download the report'


@pytest.fixture()
def chromium(tmp_path: Path) -> Iterator[str]:
    executable = chromium_executable()
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for worker tests")
    yield executable or ""
    for record in tmp_path.glob(f"**/.hercules/runs/*/{BROWSER_FILE}"):
        stop_browser(record.parent)


def _model() -> FakeModel:
    model = FakeModel()
    model.add(OPEN, action("navigate", url="/downloads.html"))
    model.add(EXPORT, action("download", target="Export CSV", url=None, value=None))
    model.add(ROWS, action("assert_file_rows", expected="3", match="equals", target=None, value=None))
    model.add(PAID, action("assert_file_column", expected="Paid", match="equals", target="Status", value="Order ID = 1002"))
    model.add(NAMED, action("assert_file_name", expected="report.csv", match="equals", target=None, value=None))
    model.add(JSON_FILE, action("download", target=None, url="/data/orders.json", value=None))
    model.add(COUNT, action("assert_file_json", expected="2", match="equals", target="$.count", value=None))
    model.add(INVOICE, action("download", target="Download invoice", url=None, value=None))
    model.add(PDF, action("assert_file_type", expected="PDF", match="equals", target=None, value=None))
    model.add(DUE, action("assert_file_text", expected="Total Due: $950", match="contains", target=None, value=None))
    model.add(ATTACH, action("upload", target="Attachment", value="{{downloaded_file}}"))
    model.add(NOTHING, action("download", target="Refresh", url=None, value=None))
    model.add('I should see "Attached: invoice_INV-77.pdf"', action("assert_text", expected="Attached: invoice_INV-77.pdf"))
    return model


def _run(tmp_path: Path, chromium: str, model: FakeModel, steps: list[dict[str, str]], name: str) -> tuple[dict[str, Any], Path]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    run_id = f"r_20261117_130000_{name}"
    with serve_downloads() as base_url:
        case = {"id": f"downloads-{name}", "title": "Reports", "environment": {"base_url": base_url}, "steps": steps}
        case_path = tmp_path / "inputs" / f"{name}.json"
        case_path.parent.mkdir(exist_ok=True)
        case_path.write_text(json.dumps(case), encoding="utf-8")
        run_dir = project / ".hercules" / "runs" / run_id
        run_dir.mkdir(parents=True)
        with model.env() as model_env:
            env = isolated_env(tmp_path, HERCULES_CHROMIUM=chromium, **model_env)
            command = [sys.executable, "-m", "testzeus_hercules.cli.main", "worker", "--run-id", run_id, "--case", str(case_path), "--project", str(project)]
            with (run_dir / "worker.log").open("wb") as log:
                proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=log, env=env, cwd=str(project), timeout=240)
    assert proc.returncode in (0, 1), (run_dir / "worker.log").read_text()[-3000:]
    return json.loads((run_dir / "run.json").read_text()), run_dir


def _why(result: dict[str, Any], run_dir: Path) -> Any:
    return [(step["index"], step["status"], step.get("actual")) for step in result["steps"]], (run_dir / "worker.log").read_text()[-2000:]


def test_downloads_are_kept_checked_in_code_and_uploaded_again(tmp_path: Path, chromium: str) -> None:
    model = _model()
    steps = [
        {"action": OPEN},
        {"action": EXPORT, "expected": ROWS},
        {"action": "verify", "expected": PAID},
        {"action": "verify", "expected": NAMED},
        {"action": JSON_FILE, "expected": COUNT},
        {"action": INVOICE, "expected": PDF},
        {"action": "verify", "expected": DUE},
        {"action": ATTACH, "expected": 'I should see "Attached: invoice_INV-77.pdf"'},
    ]

    result, run_dir = _run(tmp_path, chromium, model, steps, "files")

    assert result["status"] == "passed", _why(result, run_dir)
    assert (run_dir / "steps" / "2" / "downloads" / "report.csv").is_file()
    assert (run_dir / "steps" / "6" / "downloads" / "invoice_INV-77.pdf").read_bytes().startswith(b"%PDF")
    assert any(uri.endswith("steps/2/downloads/report.csv") for uri in result["steps"][1]["evidence"])
    ledger = [json.loads(line) for line in (run_dir / "ledger.jsonl").read_text().splitlines()] if (run_dir / "ledger.jsonl").exists() else []
    if ledger:
        assert {entry["check"] for entry in ledger} >= {"file_rows", "file_column", "file_name_equals", "file_json", "file_type", "file_text"}
    results = json.loads((run_dir / "plan.json").read_text())["results"]
    assert [(entry["check"], entry["status"]) for entry in results if entry["check"].startswith("file_")] == [
        ("file_rows", "passed"),
        ("file_column", "passed"),
        ("file_name", "passed"),
        ("file_json", "passed"),
        ("file_type", "passed"),
        ("file_text", "passed"),
    ]
    contents = ("Linus", "Pending", "INV-77 Total", "1002,Grace", '"currency"')
    assert model.prompts and not any(token in prompt for prompt in model.prompts for token in contents), "no downloaded file reaches a model"
    code = export_run(RunResult.model_validate(result), "playwright-py").code
    assert 'keep_download(page, lambda: page.get_by_role("link", name="Export CSV"' in code and "assert len(downloaded_rows()[1]) == 3" in code
    assert "the export does not read the text of a pdf file" in code


def test_a_click_that_downloads_nothing_tries_other_elements_then_fails(tmp_path: Path, chromium: str) -> None:
    result, run_dir = _run(tmp_path, chromium, _model(), [{"action": OPEN}, {"action": NOTHING}], "nothing")

    assert result["status"] == "failed", _why(result, run_dir)
    assert "started no download" in (result["steps"][1]["actual"] or ""), _why(result, run_dir)
