"""End to end on a claim form with file fields (``upload.html``), in real Chromium through a ``hercules worker`` with
the fake local model server: upload steps attach a file uploaded to the case, a project file and several files at
once, through a visible input and a label over a hidden one; the export records the files; no file reaches a model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from testzeus_hercules.cases.step_files import save_step_file
from testzeus_hercules.engine.browser_process import BROWSER_FILE, stop_browser
from testzeus_hercules.export import export_run
from testzeus_hercules.cases.models import RunResult

from tests.v2.browser_fixtures import FIXTURE_PAGES, chromium_executable
from tests.v2.engine_fixtures.browser import serve_fixtures
from tests.v2.fixtures.daemon_support import isolated_env
from tests.v2.fixtures.fake_model import FakeModel, action

CASE_ID = "upload-claim"
OPEN = 'I open "/upload.html"'
PHOTO = 'I upload "case.png" file to "Case clearance photo" field'
DOCUMENTS = 'I upload "fixtures/invoice.pdf" and "case.png" to "Supporting documents"'
RECEIPT = 'I upload "case.png" to "Drop the receipt here"'
MISSING = 'I upload "missing.png" to "Case clearance photo"'
SUBMIT = 'I click the "Submit claim" button'
RECEIVED = "Received: photo case.png; documents invoice.pdf, case.png; receipt case.png; extras none"
NOTES_TOKEN = "ZEBRA-PLUM-4721"


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
    model.add(OPEN, action("navigate", url="/upload.html"))
    model.add(PHOTO, action("upload", target="Case clearance photo", value="case.png"))
    model.add(DOCUMENTS, action("upload", target="Supporting documents", value="fixtures/invoice.pdf | case.png"))
    model.add(RECEIPT, action("upload", target="Drop the receipt here", value="case.png"))
    model.add(MISSING, action("upload", target="Case clearance photo", value="missing.png"))
    model.add(SUBMIT, action("click", target="Submit claim", role="button"))
    model.add(f'I should see "{RECEIVED}"', action("assert_text", expected=RECEIVED))
    return model


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "fixtures").mkdir(parents=True)
    (project / "fixtures" / "invoice.pdf").write_bytes(b"%PDF-1.4 invoice")
    save_step_file(project, CASE_ID, "case.png", b"\x89PNG\r\n\x1a\nclaim photo", scope="case")
    save_step_file(project, CASE_ID, "notes.txt", f"Internal notes: {NOTES_TOKEN}".encode(), scope="case")
    return project


def _run(tmp_path: Path, chromium: str, model: FakeModel, steps: list[str], name: str) -> tuple[dict[str, Any], Path]:
    project = _project(tmp_path)
    run_id = f"r_20261117_120000_{name}"
    with serve_fixtures(FIXTURE_PAGES) as base_url:
        case = {"id": CASE_ID, "title": "Claim upload", "environment": {"base_url": base_url}, "steps": [{"action": text} for text in steps]}
        case_path = tmp_path / "inputs" / f"{name}.json"
        case_path.parent.mkdir()
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


def test_upload_steps_attach_case_and_project_files_and_the_export_names_them(tmp_path: Path, chromium: str) -> None:
    model = _model()

    result, run_dir = _run(tmp_path, chromium, model, [OPEN, PHOTO, DOCUMENTS, RECEIPT, SUBMIT, f'I should see "{RECEIVED}"'], "attach")

    assert result["status"] == "passed", _why(result, run_dir)
    uploads = [action for step in result["automation"]["steps"] for action in step["actions"] if action["kind"] == "upload"]
    case_png = f".hercules/case-files/{CASE_ID}/case.png"
    assert [action["files"] for action in uploads] == [[case_png], ["fixtures/invoice.pdf", case_png], [case_png]]
    assert all(action["faithful"] for action in uploads), uploads
    code = export_run(RunResult.model_validate(result), "playwright-py").code
    assert f'set_input_files(["{case_png}"])' in code and f'set_input_files(["fixtures/invoice.pdf", "{case_png}"])' in code
    assert model.prompts and not any(NOTES_TOKEN in prompt or "claim photo" in prompt for prompt in model.prompts), "files for upload steps never reach a model"


def test_an_upload_of_a_file_that_is_nowhere_fails_without_trying_other_elements(tmp_path: Path, chromium: str) -> None:
    result, run_dir = _run(tmp_path, chromium, _model(), [OPEN, MISSING], "missing")

    assert result["status"] == "failed", _why(result, run_dir)
    step = result["steps"][1]
    assert step["status"] == "failed" and "missing.png" in (step["actual"] or "") and "the case's files" in (step["actual"] or ""), step
    decisions = (run_dir / "steps" / "2" / "decisions.jsonl").read_text().splitlines()
    assert len([line for line in decisions if json.loads(line)["purpose"] == "target"]) == 1, "a missing file is not a reason to pick another element"
