"""``GET /api/runs/{id}/steps/{n}/decisions``: a step's ``decisions.jsonl`` as a compact, UI-ready list.

Each record the engine wrote holds the full request (questions and page rows) and the
decider's answers. The view keeps what a tester needs to judge a decision: each
question with every option, its label and probability, the chosen answer, the engine,
model, latency, cost and the fallback reason in hybrid mode. Page rows and images are
left out; the raw file stays available as evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from testzeus_hercules.daemon.evidence import resolve_evidence

MAX_RECORDS = 200
MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_LABEL = 300
_MAX_TEXT = 1000
_CALL_FIELDS = ("engine", "images", "model", "latency_ms", "cost_usd", "confidence", "error")


def _text(value: Any, limit: int = _MAX_LABEL) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[:limit]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _choice(question: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    probabilities = _mapping(answer.get("probabilities"))
    criteria = _mapping(question.get("criteria"))
    names = list(criteria) + [name for name in probabilities if name not in criteria]
    options = [{"id": str(name), "label": _text(criteria.get(name)), "probability": _number(probabilities.get(name))} for name in names]
    options.sort(key=lambda option: -(option["probability"] or 0.0))
    return {"options": options, "answer": _text(answer.get("choice")), "confidence": _number(answer.get("confidence"))}


def _noul(question: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    criteria = _mapping(question.get("criteria"))
    yes = _number(answer.get("noul"))
    options = [
        {"id": "true", "label": _text(criteria.get("true")) or "yes", "probability": yes},
        {"id": "false", "label": _text(criteria.get("false")) or "no", "probability": None if yes is None else round(1.0 - yes, 6)},
    ]
    answered = None if yes is None else ("true" if yes >= 0.5 else "false")
    return {"options": options, "answer": answered, "confidence": None if yes is None else max(yes, 1.0 - yes)}


def _score(question: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    probabilities = _mapping(answer.get("probabilities"))
    criteria = question.get("criteria")
    labels = list(criteria) if isinstance(criteria, list) else []
    options = [{"id": str(name), "label": _text(labels[int(name)]) if str(name).isdigit() and int(name) < len(labels) else None, "probability": _number(p)} for name, p in probabilities.items()]
    return {"options": options, "answer": _text(answer.get("score")), "confidence": _number(answer.get("confidence"))}


def _box(question: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    return {"options": [], "answer": _text(answer.get("box")), "confidence": _number(answer.get("confidence"))}


_BY_TYPE = {"choice": _choice, "noul": _noul, "score": _score, "box": _box}


def _questions(request: Mapping[str, Any], decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    answers = _mapping(decision.get("answers"))
    found = []
    for name, raw in _mapping(request.get("questions")).items():
        question = _mapping(raw)
        answer = _mapping(answers.get(name))
        kind = str(question.get("type") or answer.get("type") or "choice")
        shape = _BY_TYPE.get(kind, _box)(question, answer)
        found.append({"id": str(name), "type": kind, "instructions": _text(question.get("instructions"), _MAX_TEXT), **shape})
    return found


def _calls(route: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls = route.get("calls")
    if not isinstance(calls, list):
        return []
    return [{name: call.get(name) for name in _CALL_FIELDS if name in call} for call in calls if isinstance(call, Mapping)]


def summarize(record: Mapping[str, Any]) -> dict[str, Any]:
    """The UI view of one ``decisions.jsonl`` record."""
    request = _mapping(record.get("request"))
    decision = _mapping(record.get("decision"))
    state = _mapping(request.get("state"))
    page = _mapping(state.get("page"))
    spec = _mapping(state.get("step"))
    route = _mapping(record.get("route"))
    return {
        "purpose": _text(record.get("purpose")),
        "attempt": record.get("attempt") if isinstance(record.get("attempt"), int) else 0,
        "engine": _text(record.get("engine") or decision.get("engine")),
        "model": _text(record.get("model") or decision.get("model_version")),
        "latency_ms": _number(record.get("latency_ms") if record.get("latency_ms") is not None else decision.get("latency_ms")),
        "wall_ms": _number(record.get("wall_ms")),
        "cost_usd": _number(record.get("cost_usd") if record.get("cost_usd") is not None else decision.get("cost_usd")),
        "cached": bool(decision.get("cached", False)),
        "choice": _text(record.get("choice")),
        "confidence": _number(record.get("confidence")),
        "rows": record.get("rows") if isinstance(record.get("rows"), int) else None,
        "prompt_tokens_est": record.get("prompt_tokens_est") if isinstance(record.get("prompt_tokens_est"), int) else None,
        "fallback": _text(record.get("fallback") or route.get("fallback"), _MAX_TEXT),
        "answered_by": _text(route.get("answered_by")),
        "calls": _calls(route),
        "page": {"title": _text(page.get("title")), "url": _text(page.get("url"), 2000)} if page else None,
        "step": {key: _text(spec.get(key), _MAX_TEXT) for key in ("kind", "role", "target", "text") if spec.get(key) is not None} or None,
        "questions": _questions(request, decision),
    }


def read_step_decisions(runs_dir: Path, run_id: str, step: int) -> list[dict[str, Any]]:
    """The step's decisions in the order they were made (empty when the step asked none)."""
    path = resolve_evidence(runs_dir, run_id, f"steps/{step}/decisions.jsonl")
    if path is None:
        return []
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found = []
    for line in lines[:MAX_RECORDS]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            found.append(summarize(record))
    return found
