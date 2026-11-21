"""``GET /api/runs/{id}/http``: every API call a run sent, as a UI-ready list.

Each API step that sends a request writes ``steps/N/http.json`` (the request, the response
or the error, and the duration, with credentials already masked). A step's ``checks.json``
holds the checks on the response of the latest call before or at that step, which is
where the engine evaluates them; a step that queries a database (``sql.json``) starts a
new subject, so its checks are not attached to an earlier call. Saves (``saved ... as
{{name}}``) come from the finished run's step results. Nothing is unmasked here: the files
are read as written, and URL credentials are masked again on the way out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from testzeus_hercules.daemon.evidence import resolve_evidence
from testzeus_hercules.daemon.manager import read_result
from testzeus_hercules.kernel.paths import validate_run_id

MAX_CALLS = 500
MAX_STEPS = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024
_SAVED = re.compile(r"saved (.+?) as \{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")


def _json_file(runs_dir: Path, run_id: str, relative: str) -> Any:
    path = resolve_evidence(runs_dir, run_id, relative)
    if path is None:
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _headers(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    return [[str(pair[0]), str(pair[1])] for pair in value if isinstance(pair, (list, tuple)) and len(pair) == 2]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _step_numbers(runs_dir: Path, run_id: str) -> list[int]:
    try:
        validate_run_id(run_id)
        steps = runs_dir / run_id / "steps"
        if steps.is_symlink() or (runs_dir / run_id).is_symlink():
            return []
        names = [entry.name for entry in steps.iterdir() if entry.is_dir() and not entry.is_symlink()]
    except (OSError, ValueError):
        return []
    return sorted(int(name) for name in names if name.isdigit() and 0 < int(name) <= MAX_STEPS)


def _call(step: int, record: Mapping[str, Any]) -> dict[str, Any]:
    request = _mapping(record.get("request"))
    response = record.get("response")
    answer = _mapping(response)
    return {
        "step": step,
        "evidence": f"steps/{step}/http.json",
        "method": (_text(request.get("method")) or "GET").upper(),
        "url": _text(request.get("url")) or "",
        "duration_ms": _number(record.get("duration_ms")),
        "error": _text(record.get("error")),
        "request": {"headers": _headers(request.get("headers")), "body": _text(request.get("body"))},
        "response": (
            {
                "status": _number(answer.get("status")),
                "reason": _text(answer.get("reason")) or "",
                "headers": _headers(answer.get("headers")),
                "body": _text(answer.get("body")),
                "bytes": _number(answer.get("bytes")),
            }
            if isinstance(response, Mapping)
            else None
        ),
        "checks": [],
        "saves": [],
        "used_by": [step],
    }


def _check(entry: Any, step: int) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    verdict = entry.get("verdict")
    return {
        "step": _number(entry.get("step")) or step,
        "check": str(entry.get("check") or ""),
        "expected": str(entry.get("expected") or ""),
        "actual": str(entry.get("actual") or ""),
        "verdict": verdict if verdict in ("passed", "failed") else "failed",
    }


def read_http_calls(runs_dir: Path, run_id: str) -> dict[str, Any]:
    """The run's API calls in the order they were sent, each with the checks and saves that used its response."""
    result = read_result(runs_dir / run_id) if resolve_evidence(runs_dir, run_id, "run.json") is not None else None
    case = _mapping(_json_file(runs_dir, run_id, "case.json"))
    raw_steps = case.get("steps")
    case_steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []
    finished = {step.index: step for step in result.steps} if result is not None else {}

    def text_of(step: int) -> str:
        done = finished.get(step)
        if done is not None:
            return done.expected if done.action.strip().lower() == "verify" and done.expected else done.action
        source = _mapping(case_steps[step - 1]) if 0 < step <= len(case_steps) else {}
        action = str(source.get("action") or "")
        return str(source.get("expected") or action) if action.strip().lower() == "verify" else action

    calls: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    more = False
    for step in _step_numbers(runs_dir, run_id):
        record = _json_file(runs_dir, run_id, f"steps/{step}/http.json")
        if isinstance(record, Mapping):
            if len(calls) >= MAX_CALLS:
                more = True
                break
            current = _call(step, record)
            current["action"] = text_of(step)
            calls.append(current)
        elif resolve_evidence(runs_dir, run_id, f"steps/{step}/sql.json") is not None:
            current = None
            continue
        if current is None:
            continue
        checks = _mapping(_json_file(runs_dir, run_id, f"steps/{step}/checks.json")).get("checks")
        attached = [check for check in (_check(entry, step) for entry in (checks if isinstance(checks, list) else [])) if check is not None]
        done = finished.get(step)
        saves = [{"step": step, "name": name, "value": value} for value, name in _SAVED.findall(done.actual or "")] if done is not None else []
        if done is not None and current["step"] != step and f"steps/{current['step']}/http.json" not in " ".join(done.evidence):
            continue  # a later step that did not use this response (a browser step, say)
        current["checks"].extend({**check, "text": text_of(check["step"])} for check in attached)
        current["saves"].extend(saves)
        if (attached or saves) and step not in current["used_by"]:
            current["used_by"].append(step)
    return {"run_id": run_id, "calls": calls, "more": more}
