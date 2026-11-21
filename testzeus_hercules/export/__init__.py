"""Export a run as automation code (``hercules export``, the ``hercules_export_code`` MCP tool, the UI).

A run's ``run.json`` carries its automation spec (:class:`~testzeus_hercules.cases.automation.AutomationSpec`):
what it did, with locators proven unique on the live page and values as templates. This package renders
it as:

* ``spec``: the spec itself (JSON), for a coding agent to write the test in the project's own style;
* ``playwright-ts``: a Playwright Test file (TypeScript);
* ``playwright-py``: a pytest-playwright file (Python, sync API);
* ``appium-py``: for a native app run, a pytest file driving the device with the Appium Python client.

Only passed runs are exported. With ``allow_failed`` a run that did not pass is exported up to its last
passed step (the steps before the first one that did not pass), and the file says so. Rendering is
deterministic and reads only ``run.json``: nothing is sent anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

from testzeus_hercules.cases.automation import AutomationSpec
from testzeus_hercules.cases.models import RunResult
from testzeus_hercules.export.appium import AppiumWriter
from testzeus_hercules.export.common import APP_FORMATS, BASE_URL_ENV, FORMATS, WEB_FORMATS, EnvVar, ExportError, ExportResult, Format, env_vars, safe_id, snake
from testzeus_hercules.export.python import PythonWriter
from testzeus_hercules.export.typescript import TypeScriptWriter
from testzeus_hercules.mobile.target import without_userinfo

__all__ = ["APP_FORMATS", "FORMATS", "WEB_FORMATS", "ExportError", "ExportResult", "Format", "GUIDANCE", "default_format", "export_run", "read_run"]

GUIDANCE = (
    "This test was generated from what a passed Hercules run did, with locators that were unique on the page (or the device's screen) during the run. "
    "Adapt it to this repository's test conventions (location, fixtures, page objects, config, naming), keep secrets in "
    "environment variables, resolve every TODO (checks a model judged by meaning, steps that could not be exported), "
    "then run it and make sure it passes before keeping it."
)


def read_run(run_dir: Path) -> RunResult:
    """The run's ``run.json``; :class:`ExportError` when it is missing or not a RunResult."""
    path = run_dir / "run.json"
    if path.is_symlink() or not path.is_file():
        raise ExportError(f"no run.json in {run_dir}: the run has not finished (or the folder is not a Hercules run)")
    try:
        return RunResult.model_validate_json(path.read_bytes())
    except ValueError as exc:
        raise ExportError(f"{path} is not a valid run result: {str(exc).splitlines()[0]}") from None


def _selected(result: RunResult, allow_failed: bool) -> tuple[AutomationSpec, str | None]:
    spec = result.automation
    if spec is None:
        raise ExportError(f"run {result.run_id} has no automation record (it was made by an older Hercules, or stopped before its first step); run the case again")
    if result.status == "passed":
        return spec, None
    first = next((step for step in result.steps if step.status != "passed"), None)
    where = f" at step {first.index} ({first.status})" if first is not None else ""
    if not allow_failed:
        raise ExportError(
            f"run {result.run_id} did not pass ({result.status}{where}): only passed runs are exported. "
            "Fix the case or the app and run it again, or export the steps that passed with allow_failed (--allow-failed)."
        )
    kept = []
    for step in spec.steps:
        if step.status != "passed":
            break
        kept.append(step)
    if not kept:
        raise ExportError(f"run {result.run_id} did not pass ({result.status}{where}) and no step passed before it: nothing to export")
    note = f"PARTIAL: the run {result.status}{where}; only steps 1-{kept[-1].index} (the steps that passed) are exported."
    return spec.model_copy(update={"steps": kept}), note


def _spec_notes(spec: AutomationSpec) -> list[str]:
    notes: list[str] = []
    for step in spec.steps:
        texts = [*step.notes, *(item.note for item in step.actions if not item.faithful and item.note), *(item.note for item in step.checks if not item.faithful and item.note)]
        notes.extend(f"step {step.index}: {text}" for text in texts if text)
    return notes


def default_format(result: RunResult) -> Format:
    """The format a run exports to when none is named: Appium code for a native app run, else Playwright Test."""
    return "appium-py" if result.automation is not None and result.automation.app is not None else "playwright-ts"


def _filename(fmt: Format, case_id: str, target_dir: str | None) -> str:
    case_id = safe_id(case_id)  # never a path: no "/", no ".."
    name = {"spec": f"{case_id}.automation.json", "playwright-ts": f"{case_id}.spec.ts", "playwright-py": f"test_{snake(case_id)}.py", "appium-py": f"test_{snake(case_id)}.py"}[fmt]
    directory = (target_dir or ("" if fmt == "spec" else "tests")).strip().rstrip("/")
    return f"{directory}/{name}" if directory else name


def export_run(result: RunResult, fmt: Format, *, allow_failed: bool = False, target_dir: str | None = None) -> ExportResult:
    """Render a finished run in ``fmt``; :class:`ExportError` when it cannot be exported (see the module docs)."""
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
    if target_dir is not None and (".." in Path(target_dir).parts or Path(target_dir).is_absolute()):
        raise ExportError("the target test directory must be a relative path inside the project, without '..'")
    spec, partial = _selected(result, allow_failed)
    if spec.app is not None and fmt in WEB_FORMATS:
        raise ExportError(
            f"run {result.run_id} tested a native app on a device: Playwright code cannot drive it. Export it as Appium code "
            "(format appium-py: pytest with the Appium Python client), or as the spec (format spec)"
        )
    if spec.app is None and fmt in APP_FORMATS:
        raise ExportError(f"run {result.run_id} tested a web app in a browser: Appium code is for native app runs. Use format playwright-ts or playwright-py")
    payload = spec.model_dump(mode="json", exclude_none=True)
    server = payload.get("appium")
    if isinstance(server, dict) and isinstance(server.get("url"), str):
        url, credentials = without_userinfo(server["url"])
        if credentials:  # a run recorded before credentials were stripped: never print them
            payload["appium"] = {**server, "url": url, "url_credentials": "true"}
    common = {"format": fmt, "run_id": result.run_id, "case_id": spec.case_id, "run_status": result.status, "filename": _filename(fmt, spec.case_id, target_dir), "spec": payload}
    counts = {"steps_exported": len(spec.steps), "steps_total": len(result.steps), "partial": partial is not None}
    if fmt == "spec":
        env = ((EnvVar(BASE_URL_ENV, False, f"base URL of the app under test (default {spec.base_url})"),) if spec.base_url else ()) + tuple(
            EnvVar(entry.env, True, f"secret {{{{{name}}}}}") for name, entry in sorted(spec.data.items()) if entry.kind == "secret" and entry.env
        )
        notes = ([partial] if partial else []) + _spec_notes(spec)
        return ExportResult(code=json.dumps(payload, indent=2, ensure_ascii=False) + "\n", env=env, notes=tuple(notes), **common, **counts)  # type: ignore[arg-type]
    if fmt == "appium-py":
        appium = AppiumWriter(spec, run_id=result.run_id, partial_note=partial)
        code = appium.render()
        notes = ([partial] if partial else []) + appium.notes
        return ExportResult(code=code, env=appium.env(), notes=tuple(notes), **common, **counts)  # type: ignore[arg-type]
    writer = (TypeScriptWriter if fmt == "playwright-ts" else PythonWriter)(spec, run_id=result.run_id, partial_note=partial)
    code = writer.render()
    notes = ([partial] if partial else []) + writer.notes
    return ExportResult(code=code, env=env_vars(writer), notes=tuple(notes), **common, **counts)  # type: ignore[arg-type]
