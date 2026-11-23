"""``hercules export``: a run id or folder in, code (or the spec) out; failed runs refused unless --allow-failed."""

from __future__ import annotations

import io
import json
from pathlib import Path

from testzeus_hercules.cli.main import main

from tests.v2.test_export_render import result

RUN_ID = "r_20261026_120000_abcdef"


def project_with(tmp_path: Path, status: str = "passed") -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    run_dir = project / ".hercules" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    statuses = ["passed"] * 9 if status == "passed" else ["passed", "failed"] + ["skipped"] * 7
    (run_dir / "run.json").write_text(result(status, step_statuses=statuses).model_dump_json(indent=2), encoding="utf-8")
    return project, {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0"}


def export(*args: str, env: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["export", *args], env=env, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_a_run_id_is_exported_to_standard_output_with_what_it_needs(tmp_path: Path) -> None:
    project, env = project_with(tmp_path)

    code, out, err = export(RUN_ID, "--project", str(project), "--format", "playwright-py", env=env)

    assert code == 0, err
    assert out.startswith('"""Every construct') and "def test_golden_case(page: Page, api: APIRequestContext) -> None:" in out
    assert "Suggested file: tests/test_golden_case.py" in err
    assert "Environment: APP_PASSWORD (required): secret {{password}}" in err
    assert "Review: step 4: assert in code: the order is confirmed" in err


def test_a_run_folder_is_exported_to_a_file(tmp_path: Path) -> None:
    project, env = project_with(tmp_path)
    target = tmp_path / "e2e" / "checkout.spec.ts"

    code, out, err = export(str(project / ".hercules" / "runs" / RUN_ID), "--out", str(target), env=env)
    again, _, refused = export(str(project / ".hercules" / "runs" / RUN_ID), "--out", str(target), env=env)
    forced, _, _ = export(str(project / ".hercules" / "runs" / RUN_ID), "--out", str(target), "--force", env=env)

    assert code == 0 and out == "" and f"Wrote {target} (9 of 9 steps" in err
    assert target.read_text().startswith("// Every construct")
    assert again == 2 and "exists; use --force" in refused
    assert forced == 0


def test_the_spec_format_prints_the_automation_spec(tmp_path: Path) -> None:
    project, env = project_with(tmp_path)

    code, out, _ = export(RUN_ID, "--project", str(project), "--format", "spec", env=env)

    assert code == 0 and json.loads(out)["case_id"] == "golden-case"


def test_a_failed_run_is_refused_unless_allowed(tmp_path: Path) -> None:
    project, env = project_with(tmp_path, status="failed")

    refused, _, err = export(RUN_ID, "--project", str(project), env=env)
    allowed, out, notes = export(RUN_ID, "--project", str(project), "--allow-failed", env=env)

    assert refused == 2 and "only passed runs are exported" in err and "--allow-failed" in err
    assert allowed == 0 and "PARTIAL: the run failed at step 2 (failed); only steps 1-1" in out
    assert "Review: PARTIAL" in notes


def test_an_unknown_run_is_an_error(tmp_path: Path) -> None:
    project, env = project_with(tmp_path)

    missing, _, err = export("r_19990101_000000_000000", "--project", str(project), env=env)
    invalid, _, bad = export("../../etc", "--project", str(project), env=env)

    assert missing == 2 and "no run.json" in err
    assert invalid == 2 and "neither a run folder nor a run id" in bad
