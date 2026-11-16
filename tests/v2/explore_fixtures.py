"""Model stand-ins and helpers for exploration tests: a step reader for the lever library's move texts, a decider that
picks targets by label and answers exploration questions from a script, and the seeded-bug app."""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from tests.v2.engine_fixtures.fakes import LabelDecider, ScriptedInterpreter, clicks, fills, intent, opens

ROOT = Path(__file__).resolve().parents[2]
_OPEN = re.compile(r'^Open "(?P<url>[^"]*)"$')
_FILL = re.compile(r'^Fill (?P<value>"(?:[^"\\]|\\.)*") in "(?P<field>[^"]+)"(?: and click the "(?P<button>[^"]+)" button)?$')
_CLEAR = re.compile(r'^Clear "(?P<field>[^"]+)"(?: and click the "(?P<button>[^"]+)" button)?$')
_CLICK = re.compile(r'^Click the "(?P<name>[^"]+)" (?P<role>link|button)$')


class MoveReader(ScriptedInterpreter):
    """Reads the lever library's step texts (and any scripted extras) without a model."""

    def __init__(self, readings: Mapping[str, Any] | None = None) -> None:
        super().__init__(readings or {}, case_reading=False)

    def _lookup(self, text: str, source: str) -> Any:
        import json

        if self._normalise(text) in self.readings:
            return super()._lookup(text, source)
        found = _OPEN.match(text)
        if found:
            return opens(found["url"]).model_copy(update={"source": source})
        found = _FILL.match(text) or _CLEAR.match(text)
        if found:
            value = json.loads(found["value"]) if "value" in found.groupdict() and found["value"] else ""
            parts = [fills(found["field"], value)]
            if found["button"]:
                parts.append(clicks(found["button"], role="button"))
            first, *rest = parts
            return first.model_copy(update={"source": source, "then": tuple(part.model_copy(update={"source": source}) for part in rest)})
        found = _CLICK.match(text)
        if found:
            return clicks(found["name"], role=found["role"]).model_copy(update={"source": source})
        return intent("unknown", text).model_copy(update={"source": source})


class ExploreDecider(LabelDecider):
    """Targets by label; exploration questions answered by ``moves`` (a function of the offered options) and ``judge_wrong``."""

    def __init__(self, *, pick: Callable[[dict[str, Any]], str] | None = None, judge_wrong: float = 0.0, severity: str | None = None) -> None:
        super().__init__(judge=0.0)
        self.pick = pick
        self.judge_wrong = judge_wrong
        self.severity = severity

    async def decide(self, request: Any, *, purpose: str) -> Any:
        from testzeus_hercules.decide.contract import ChoiceAnswer, Decision, NoulAnswer

        if purpose == "explore_next" and self.pick is not None:
            options = dict(request.questions["move"].criteria)
            choice = self.pick(options)
            self.calls.append((request, purpose))
            answer = ChoiceAnswer(choice=choice, confidence=0.9, probabilities={name: 0.9 if name == choice else 0.1 / max(1, len(options) - 1) for name in options})
            return Decision(answers={"move": answer}, engine=self.engine, model_version="scripted", latency_ms=0, cost_usd=0.0)
        if purpose == "explore_judge":
            self.calls.append((request, purpose))
            answers = {"wrong": NoulAnswer(noul=self.judge_wrong), "opinion": NoulAnswer(noul=0.0), "what": NoulAnswer(noul=1.0 if self.judge_wrong else 0.0)}
            return Decision(answers=answers, engine=self.engine, model_version="scripted", latency_ms=0, cost_usd=0.0)
        if purpose == "explore_severity" and self.severity is not None:
            self.calls.append((request, purpose))
            question = request.questions["severity"]
            answer = ChoiceAnswer(choice=self.severity, confidence=0.9, probabilities={name: 0.9 if name == self.severity else 0.1 / 3 for name in question.criteria})
            return Decision(
                answers={"severity": answer, **{qid: _default(q) for qid, q in request.questions.items() if qid != "severity"}},
                engine=self.engine,
                model_version="scripted",
                latency_ms=0,
                cost_usd=0.0,
            )
        return await super().decide(_counted_target(request), purpose=purpose)


def _counted_target(request: Any) -> Any:
    """Like a model, read a quoted "Cart" as the "Cart (0)" link when no row is named exactly "Cart"."""
    from tests.v2.engine_fixtures.fakes import _labels

    step = dict(request.state.get("step") or {})
    wanted = " ".join(str(step.get("target") or "").split()).casefold()
    rows = list(request.state.get("rows") or ())
    if not wanted or any(wanted in _labels(row) for row in rows):
        return request
    counted = re.compile(re.escape(wanted) + r" \(-?\d+\)$")
    for row in rows:
        for label in _labels(row):
            if counted.match(label):
                return request.model_copy(update={"state": {**request.state, "step": {**step, "target": label}}})
    return request


def _default(question: Any) -> Any:
    from testzeus_hercules.decide.backends.stub import default_answer

    return default_answer(question)


def prefer(*words: str) -> Callable[[dict[str, Any]], str]:
    """A move picker: the first offered move whose description holds every word, in order of ``words`` groups; else m1."""

    def pick(options: dict[str, Any]) -> str:
        for name, text in options.items():
            if name != "done" and "(tried already)" not in str(text) and all(word.lower() in str(text).lower() for word in words):
                return name
        return "m1"

    return pick


@contextlib.contextmanager
def pantry(bugs: str = "all") -> Iterator[str]:
    """The seeded-bug app (bench/explore/app.py), served for the test."""
    sys.path.insert(0, str(ROOT / "bench" / "explore"))
    try:
        import app as pantry_app  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    with pantry_app.serve(pantry_app.parse_bugs(bugs)) as url:
        yield url
