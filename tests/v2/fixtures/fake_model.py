"""A fake local model for whole-run tests: answers the requests Hercules sends, by schema.

It stands in for the OpenAI-compatible model server in tests that run the CLI or the
worker end to end (in process through :class:`~tests.v2.test_llm_fakes.FakeServer`, or in
a subprocess through :meth:`FakeModel.serve`):

* the warm-up gets ``OK``;
* a whole-case reading (``CaseReadingReply``) and a one-step reading (``IntentPlan``)
  get the actions scripted for each step text (``unknown`` when none is scripted);
* a continuation (``ContinuationPlan``) gets the next scripted reply of ``continuations`` (no further actions when
  none is left); its prompts are kept in ``continue_prompts``;
* a decision (``DecisionReply``) picks the row labelled exactly as the step's target
  (else ``no_match``), says yes with ``judge`` to yes/no questions (``meaning`` overrides it
  per proposed value when two field values are compared), and gives the first option of any
  other choice question;
* a whole-case reading carries the ``needs`` scripted per step text (``step_N_needs``);
* a rejected field value (``FieldValueReply``) gets the next scripted reply of ``field_replies``
  (``cannot`` when none is left) and a field dependency (``RunFirstReply``) the next of
  ``run_first_replies`` (none when none is left); their prompts are kept in ``field_prompts``
  and ``run_first_prompts``.

This is a test double; Hercules itself has no coded reader or decider.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import re
import threading
from typing import Any, Iterator, Mapping

import httpx

from testzeus_hercules.engine.intent import normalise_step_text

NULLS = {"target": None, "value": None, "url": None, "expected": None, "match": None, "role": None}
_ITEM = re.compile(r"^(step_\d+_(?:action|expected)): (.*)$")


def action(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **NULLS, **fields}


# How the fake model reads the step phrasings the tests use; tests add their own.
COMMON_READINGS: dict[str, list[dict[str, Any]]] = {
    "Open /login": [action("navigate", url="/login")],
    'click "Sign in"': [action("click", target="Sign in")],
    'Click the "Sign in" button': [action("click", target="Sign in", role="button")],
    'I fill "alice" in "Username"': [action("fill", target="Username", value="alice")],
    'Fill "alice" in "Username"': [action("fill", target="Username", value="alice")],
    'I fill "s3cret" in "Password"': [action("fill", target="Password", value="s3cret")],
    'I click the "Sign in" button': [action("click", target="Sign in", role="button")],
    'Open "/form.html"': [action("navigate", url="/form.html")],
    'Fill "Grace Hopper" in "Full name"': [action("fill", target="Full name", value="Grace Hopper")],
    "Wait 4 seconds": [action("wait", value="4")],
    'Click "Save profile"': [action("click", target="Save profile")],
    'I should see "Edit profile"': [action("assert_text", expected="Edit profile")],
    'I should see "Welcome"': [action("assert_text", expected="Welcome")],
    'I should see "Goodbye"': [action("assert_text", expected="Goodbye")],
}


def _completion(content: str) -> dict[str, Any]:
    return {
        "id": "cmpl-fake",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    }


def _labels(row: Mapping[str, Any]) -> set[str]:
    attrs = row.get("attrs") or {}
    names = [row.get("name"), attrs.get("label"), attrs.get("aria-label"), attrs.get("placeholder")]
    return {" ".join(str(name).split()).casefold() for name in names if name}


_OPTION_ROW = re.compile(r'^- "(?P<id>md_\d+)": \S+ "(?P<name>(?:[^"\\]|\\.)*)"(?P<rest>.*?)(?: (?P<extra>\{.*\}))?$')


def _option_rows(user: str) -> list[dict[str, Any]]:
    """The rows of a prompt that lists each row with its option (the local decider's compact form)."""
    rows = []
    for line in user.splitlines():
        found = _OPTION_ROW.match(line)
        if found is None:
            continue
        extra = json.loads(found.group("extra")) if found.group("extra") else {}
        label = re.search(r' labelled "([^"]*)"', found.group("rest"))
        attrs = {**extra.get("attrs", {}), **({"label": label.group(1)} if label else {})}
        rows.append({"id": found.group("id"), "name": found.group("name"), "attrs": attrs})
    return rows


class FakeModel:
    def __init__(self, readings: Mapping[str, list[dict[str, Any]]] | None = None, *, judge: float = 0.9) -> None:
        merged = {**COMMON_READINGS, **(readings or {})}
        self.readings = {normalise_step_text(text): actions for text, actions in merged.items()}
        self.judge = judge
        self.schemas: list[str | None] = []
        # Scripted continuation replies, in order (each a list of actions with "because"), and the prompts asked.
        self.continuations: list[list[dict[str, Any]]] = []
        self.continue_prompts: list[str] = []
        # Per step text: the step numbers its field needs set first (the case reading's step_N_needs).
        self.needs: dict[str, list[int]] = {}
        self.field_replies: list[dict[str, Any]] = []
        self.field_prompts: list[str] = []
        self.run_first_replies: list[dict[str, Any]] = []
        self.run_first_prompts: list[str] = []
        # Per proposed value (value_b): the probability that it means the same as the step's value.
        self.meaning: dict[str, float] = {}
        # The plan: per step text, the case reading's criteria, risk and lint for it; per step number, the coverage
        # audit's probability that the plan covers it (default: ``judge``).
        self.criteria: dict[str, list[dict[str, Any]]] = {}
        self.risks: dict[str, dict[str, Any]] = {}
        self.lint: dict[str, list[dict[str, Any]]] = {}
        self.coverage: dict[int, float] = {}
        self.coverage_prompts: list[str] = []

    def add(self, text: str, *actions: dict[str, Any]) -> None:
        """Script how the model reads ``text``."""
        self.readings[normalise_step_text(text)] = list(actions)

    # ------------------------------------------------------------------ replies

    def reply(self, body: dict[str, Any]) -> dict[str, Any]:
        """The completion body for one chat request."""
        response_format = body.get("response_format") or {}
        name = (response_format.get("json_schema") or {}).get("name")
        self.schemas.append(name)
        user = next((message["content"] for message in reversed(body["messages"]) if message["role"] == "user"), "")
        user = user if isinstance(user, str) else " ".join(part.get("text", "") for part in user if isinstance(part, dict))
        if name is None:
            return _completion("OK")
        if name == "CaseReadingReply":
            return _completion(json.dumps(self._case(user)))
        if name == "IntentPlan":
            text = user.rsplit("Step: ", 1)[-1].strip()
            return _completion(json.dumps({"actions": self._read(text)}))
        if name == "ContinuationPlan":
            self.continue_prompts.append(user)
            return _completion(json.dumps({"actions": self.continuations.pop(0) if self.continuations else []}))
        if name == "FieldValueReply":
            self.field_prompts.append(user)
            reply = self.field_replies.pop(0) if self.field_replies else {"outcome": "cannot", "value": None, "same_meaning": False, "because": ""}
            return _completion(json.dumps(reply))
        if name == "RunFirstReply":
            self.run_first_prompts.append(user)
            return _completion(json.dumps(self.run_first_replies.pop(0) if self.run_first_replies else {"run_first": None, "because": ""}))
        return _completion(json.dumps(self._decide(user, response_format["json_schema"]["schema"])))

    def response(self, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json=self.reply(body))

    def _read(self, text: str) -> list[dict[str, Any]]:
        return self.readings.get(normalise_step_text(text)) or [action("unknown", target=text)]

    def _case(self, user: str) -> dict[str, Any]:
        items = [found for line in user.split("ITEMS:\n", 1)[-1].splitlines() if (found := _ITEM.match(line))]
        reply: dict[str, Any] = {}
        texts: dict[str, list[str]] = {}
        for found in items:
            key, text = found.group(1), normalise_step_text(found.group(2))
            base = key.rsplit("_", 1)[0]
            texts.setdefault(base, []).append(text)
            reply[key] = self._read(found.group(2))
            if key.endswith("_action"):
                reply[base + "_needs"] = self.needs.get(text, [])
                reply[base + "_criteria"] = [
                    {"after": 1, "origin": "inferred", "expected": None, "target": None, "value": None, "state": None, "count": None, **item} for item in self.criteria.get(text, [])
                ]
                reply[base + "_risk"] = self.risks.get(text, {"flags": [], "reason": None})
        for base, step_texts in texts.items():
            reply[base + "_lint"] = [finding for text in step_texts for finding in self.lint.get(text, [])][:2]
        return reply

    def _decide(self, user: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        state = json.loads(user.split("\n", 2)[1])
        step = state.get("step") or {}
        wanted = " ".join(str(step.get("target") or "").split()).casefold()
        listed = state.get("rows") if isinstance(state.get("rows"), list) else _option_rows(user)
        rows = [row["id"] for row in listed or () if wanted and wanted in _labels(row)]
        answers: dict[str, Any] = {}
        for qid, question in (schema.get("properties") or {}).items():
            fields = question.get("properties") or {}
            if "yes_probability" in fields:
                meaning = self.meaning.get(str(state.get("value_b"))) if "value_b" in state else None
                covered = self.coverage.get(int(qid.split("_")[1])) if qid.startswith("step_") and "steps" in state else None
                if qid.startswith("step_") and "steps" in state and qid == next(iter(schema.get("properties") or {}), None):
                    self.coverage_prompts.append(user)
                answers[qid] = {"yes_probability": (0.05 if rows else 0.9) if qid == "target_absent" else (meaning if meaning is not None else covered if covered is not None else self.judge)}
            elif "probabilities" in fields:
                options = list((fields["probabilities"].get("properties") or {}))
                choice = (rows[0] if rows else "no_match") if qid == "target" else options[0]
                answers[qid] = {"probabilities": {option: (0.94 if option == choice else 0.06 / max(1, len(options) - 1)) for option in options}}
            else:
                answers[qid] = {"top": 0.0, "bottom": 1.0, "left": 0.0, "right": 1.0}
        return answers

    # ------------------------------------------------------------------ serving

    @contextlib.contextmanager
    def env(self, model_id: str = "fake-gemma") -> Iterator[dict[str, str]]:
        """Serve the model and yield the environment that points a ``hercules`` subprocess at it (pure mode)."""
        with self.serve(model_id) as base_url:
            yield {"HERCULES_LLM_PROVIDER": "llamacpp", "HERCULES_LLM_BASE_URL": base_url, "HERCULES_LLM_MODEL": model_id}

    @contextlib.contextmanager
    def serve(self, model_id: str = "fake-gemma") -> Iterator[str]:
        """An OpenAI-compatible HTTP server on 127.0.0.1 (for subprocess tests); yields its ``/v1`` base URL."""
        model = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return None

            def _send(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    self._send({"object": "list", "data": [{"id": model_id, "object": "model"}]})
                else:
                    self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                self._send(model.reply(body))

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/v1"
        finally:
            server.shutdown()
            server.server_close()
