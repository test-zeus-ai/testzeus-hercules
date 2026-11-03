"""Static checks for the composite GitHub Action (.github/actions/run-tests/action.yml)."""

import pathlib
import re

import yaml

ACTION = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / ".github" / "actions" / "run-tests" / "action.yml").read_text(encoding="utf-8"))


def test_default_image_is_docker_hub() -> None:
    assert ACTION["inputs"]["image"]["default"] == "testzeus/hercules:latest"


def test_no_expression_is_interpolated_into_shell_scripts() -> None:
    """`${{ ... }}` inside a `run:` body is a script-injection vector; inputs must arrive via `env:`."""
    offenders = [step.get("name") for step in ACTION["runs"]["steps"] if "run" in step and re.search(r"\$\{\{", step["run"])]
    assert offenders == []


def test_run_step_builds_argv_array_not_shell_string() -> None:
    run_step = next(step for step in ACTION["runs"]["steps"] if step.get("id") == "run")["run"]
    assert "bash -lec" not in run_step and "/bin/bash -lc" not in run_step
    assert "CMD=(" in run_step


def test_failure_details_are_opt_in() -> None:
    assert ACTION["inputs"]["pr-comment-failure-details"]["default"] == "false"


# ── behaviour of the run step, executed with fake docker/uv binaries ─────────

import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

RUN_SCRIPT = next(step for step in ACTION["runs"]["steps"] if step.get("id") == "run")["run"]
FAKE_BIN = '#!/bin/sh\n{ for a in "$@"; do printf "%s\\n" "$a"; done; echo "--ENV--"; env; } > "$FAKE_LOG"\n'


def _run_step(tmp_path, runner="docker", mode="single", **inputs):
    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("docker", "uv"):
        fake = bin_dir / name
        fake.write_text(FAKE_BIN, encoding="utf-8")
        fake.chmod(0o755)
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": str(tmp_path / "argv.txt"),
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_MODE": runner,
        "IMAGE": "testzeus/hercules:latest",
        "RUN_MODE": mode,
        "INPUT_FEATURE": "tests/a.feature",
        "INPUT_SUITE": "smoke",
        "SUITE_CONFIG": ".hercules/suites.yaml",
        "MAX_PARALLEL": "1",
        "LLM_CONFIG": "",
        "LLM_CONFIG_REF_KEY": "",
        "BASE_URL": "",
        "BLOCKING": "true",
        "REPORTS": "true",
        "HERCULES_PROJECT": "/action/../../..",
        "HERCULES_PASSTHROUGH_VARS": "",
        **inputs,
    }
    result = subprocess.run(["bash", "-c", RUN_SCRIPT], cwd=workspace, env=env, capture_output=True, text=True, timeout=60)
    log = tmp_path / "argv.txt"
    argv, _, child_env = (log.read_text(encoding="utf-8") if log.exists() else "").partition("--ENV--\n")
    return result, argv.splitlines(), child_env.splitlines(), workspace


def test_uv_runner_uses_the_action_project() -> None:
    install = next(step for step in ACTION["runs"]["steps"] if step.get("name") == "Install Hercules dependencies (uv)")
    assert 'uv sync --project "$HERCULES_PROJECT"' in install["run"]
    assert 'uv run --project "$HERCULES_PROJECT"' in install["run"]
    assert install["env"]["HERCULES_PROJECT"].startswith("${{ github.action_path }}")


def test_uv_run_passes_project(tmp_path) -> None:
    result, argv, _env, _ws = _run_step(tmp_path, runner="uv")
    assert result.returncode == 0, result.stderr
    assert argv[:4] == ["run", "--project", "/action/../../..", "--"]


def test_bulk_points_project_source_root_at_the_workspace(tmp_path) -> None:
    result, argv, _env, _ws = _run_step(tmp_path, mode="bulk")
    assert result.returncode == 0, result.stderr
    assert "PROJECT_SOURCE_ROOT=/workspace" in argv and "EXECUTE_BULK=true" in argv
    result, _argv, child_env, ws = _run_step(tmp_path, runner="uv", mode="bulk")
    assert f"PROJECT_SOURCE_ROOT={ws}" in child_env


def test_bulk_keeps_a_user_project_source_root(tmp_path) -> None:
    _result, argv, _env, _ws = _run_step(tmp_path, mode="bulk", PROJECT_SOURCE_ROOT="/workspace/e2e")
    assert not any(arg.startswith("PROJECT_SOURCE_ROOT=") for arg in argv)


@pytest.mark.parametrize("mode,extra", [("suite", {}), ("single", {"MAX_PARALLEL": "3"})])
def test_suite_and_parallel_write_merged_junit_under_opt_output(tmp_path, mode, extra) -> None:
    result, argv, _env, _ws = _run_step(tmp_path, mode=mode, **extra)
    assert result.returncode == 0, result.stderr
    index = argv.index("--out-dir")
    assert argv[index + 1] == "opt/output/merged"


def test_base_url_reaches_the_run_as_app_url(tmp_path) -> None:
    _result, argv, _env, _ws = _run_step(tmp_path, BASE_URL="https://staging.example.com")
    assert "APP_URL=https://staging.example.com" in argv
    _result, argv, _env, _ws = _run_step(tmp_path)
    assert not any(arg.startswith("APP_URL=") for arg in argv)


def test_reports_input_enables_reporting_flags(tmp_path) -> None:
    _result, argv, _env, _ws = _run_step(tmp_path)
    for flag in ("ENABLE_RUN_REPORTING=true", "ENABLE_FAILURE_BUNDLES=true", "ENABLE_RUN_HISTORY=true", "ENABLE_FAILURE_CLASSIFICATION=true"):
        assert flag in argv
    assert "HERCULES_HISTORY_DB=/workspace/opt/hercules_history.db" in argv
    _result, argv, _env, _ws = _run_step(tmp_path, REPORTS="false")
    assert not any(arg.startswith("ENABLE_") for arg in argv)


def test_docker_llm_config_absolute_paths(tmp_path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "cfg.json").write_text("{}", encoding="utf-8")
    _result, argv, _env, _ws = _run_step(tmp_path, LLM_CONFIG=str(ws / "cfg.json"), LLM_CONFIG_REF_KEY="openai")
    assert "AGENTS_LLM_CONFIG_FILE=/workspace/cfg.json" in argv

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    result, _argv, _env, _ws = _run_step(tmp_path, LLM_CONFIG=str(outside), LLM_CONFIG_REF_KEY="openai")
    assert result.returncode != 0 and "outside the workspace" in result.stderr


def test_artifacts_and_result_discovery_cover_bulk_suite_and_parallel() -> None:
    upload = next(step for step in ACTION["runs"]["steps"] if step.get("name") == "Upload results artifact")
    paths = upload["with"]["path"].split()
    for expected in ("opt/output", "tests/*/output", "tests/*/proofs", ".hercules/suite_runs", ".hercules/parallel_*"):
        assert expected in paths
    collect = next(step for step in ACTION["runs"]["steps"] if step.get("id") == "results")["run"]
    assert "tests/*/output/*/*_result.xml" in collect


def test_examples_reference_the_published_action() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / ".github"
    workflow = yaml.safe_load((root / "examples" / "hercules-workflow.yml").read_text(encoding="utf-8"))
    uses = [step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "run-tests" in str(step.get("uses", ""))]
    assert uses and all(value.startswith("test-zeus-ai/testzeus-hercules/.github/actions/run-tests@") for value in uses)
    readme = (root / "actions" / "run-tests" / "README.md").read_text(encoding="utf-8")
    assert "testzeus/hercules/.github" not in readme and "github.com/testzeus/hercules" not in readme
