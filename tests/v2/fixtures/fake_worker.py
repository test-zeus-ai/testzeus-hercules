"""Stand-in for ``hercules worker`` in daemon and MCP tests: no browser and no model.

It takes the same flags as the real worker, streams the same JSON-lines events on
stdout and writes ``run.json`` and step evidence where the engine would. Behaviour
is chosen by the case's tags:

* ``fake-fail``: step 2 fails (with ``plain.jpg``/``som.jpg``/``observation.txt``);
* ``fake-crash``: exits with code 3 after ``run_started``, without ``run.json``;
* ``fake-hang``: never finishes (until it is signalled);
* ``fake-browser``: first starts a stand-in browser in its own process group (as Playwright starts
  Chromium) and records it in ``browser.json``, like the real worker;
* ``fake-slow``: each step takes 0.4 s;
* ``fake-error-event``: reports an ``error`` event and exits 2 without ``run.json``;
* ``fake-control``: each step takes 0.3 s and the worker obeys ``pause``/``resume``/``step``/
  ``skip`` messages on stdin before each step (``paused``/``resumed`` events);
* ``fake-approval``: before step 2 it asks (``needs_approval``) and waits for the answer
  (reject blocks step 2; edit replaces its text);
* ``fake-frames``: while a screencast is requested it writes a small JPEG frame every 0.1 s
  to ``--frames-fd``;
* ``fake-quality`` (or ``--a11y``): the result carries a quality section (an accessibility audit
  with a verified contrast fix and an annotated PNG, and a visual comparison); with
  ``--quality-fail-on`` the run fails on it;
* ``--plan-review ask``: before step 1 it writes ``plan.json`` and asks for the plan's approval
  (``needs_approval`` of kind ``plan``); reject cancels the run, edit reads ``plan.edited.json`` (its
  plan steps' texts are recorded in ``plan_edit_seen.json``);
* ``--explore JOB``: an exploration child; it writes what the explorer would under ``explore/``: a recon charter
  sees two page states, an explore charter covers one lever there; nothing is ever wrong (no clues);
* ``--plan-only --plan-out FILE``: writes the case's plan (one click per step, an inferred criterion on
  the first, a lint warning) to FILE and exits; the tag ``fake-plan-fail`` makes it fail instead.

Standard library and Pillow only, so it starts fast.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path


def _jpeg(color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1000), color).save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _explore_files(run_dir: Path, job_path: Path) -> None:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    charter = job.get("charter") or {}
    out = run_dir / "explore"
    out.mkdir(parents=True, exist_ok=True)
    states = {
        "s-cart": {"route": "/cart", "url": "http://127.0.0.1:9/cart", "elements": ["Quantity", "Save", "Checkout"]},
        "s-home": {"route": "/", "url": "http://127.0.0.1:9/", "elements": ["Cart"]},
    }
    (out / "states.json").write_text(json.dumps(states), encoding="utf-8")
    lever = (charter.get("levers") or ["inputs"])[0]
    coverage = {} if charter.get("kind") == "recon" else {"s-cart": {lever: [f"{charter.get('id')}-v"]}}
    (out / "coverage.json").write_text(json.dumps(coverage), encoding="utf-8")
    (out / "clues.jsonl").write_text("", encoding="utf-8")
    (out / "turns.jsonl").write_text("", encoding="utf-8")
    debrief = {
        "charter": charter.get("title", "?"),
        "turns": 1,
        "by_lever": {lever: 1},
        "states": 1,
        "ended": "done",
        "clues": [],
        "not_reached": [],
        "time_split": {},
        "confidence": "low",
        "confidence_reason": "a fake worker",
    }
    (out / "debrief.json").write_text(json.dumps(debrief), encoding="utf-8")
    time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--frames-fd", type=int, default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--plan-out", default=None)
    args, extra = parser.parse_known_args()
    case = json.loads(Path(args.case).read_text(encoding="utf-8"))
    tags = set(case.get("tags", []))
    if args.plan_only:
        if "fake-plan-fail" in tags:
            sys.stderr.write("error: the model server is not reachable\n")
            return 2
        Path(args.plan_out).write_text(json.dumps(_plan(case)), encoding="utf-8")
        return 0
    run_dir = Path(args.project) / ".hercules" / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "worker_args.json").write_text(json.dumps(extra), encoding="utf-8")
    # Where the case came from, and whether it held real values (the run directory's copy has URL credentials masked).
    text = Path(args.case).read_text(encoding="utf-8")
    (run_dir / "case_input.json").write_text(json.dumps({"path": args.case, "masked": "[REDACTED]" in text}), encoding="utf-8")

    def emit(kind: str, /, **data: object) -> None:
        sys.stdout.write(json.dumps({"kind": kind, "data": data}) + "\n")
        sys.stdout.flush()

    if "--explore" in extra:
        _explore_files(run_dir, Path(extra[extra.index("--explore") + 1]))
    sys.stdout.write("fake worker starting (not an event)\n")
    emit("run_started", run_id=args.run_id, case_id=case["id"], steps=len(case["steps"]))
    if "fake-browser" in tags:
        import subprocess

        from testzeus_hercules.engine.browser_process import BROWSER_FILE, describe_process

        browser = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", f"--user-data-dir={run_dir / 'profile'}"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        recorded = describe_process(browser.pid)
        (run_dir / BROWSER_FILE).write_text(json.dumps(recorded.__dict__ if recorded else {}), encoding="utf-8")
    if "fake-crash" in tags:
        os._exit(3)
    if "fake-error-event" in tags:
        emit("error", message="the model server is not reachable")
        return 2
    if "fake-hang" in tags:
        emit("step_started", step=1, action=case["steps"][0]["action"])
        time.sleep(120)
        return 1
    ask_plan = "--plan-review" in extra and extra[extra.index("--plan-review") + 1] == "ask"
    channel = _Channel(emit, args.frames_fd) if tags & {"fake-control", "fake-approval", "fake-frames"} or ask_plan else None
    if ask_plan and channel is not None:
        (run_dir / "plan.json").write_text(json.dumps(_plan(case)), encoding="utf-8")
        answer = channel.ask(0, None, kind="plan")
        if answer.get("decision") == "reject":
            run = {"run_id": args.run_id, "status": "cancelled", "case_id": case["id"], "hints": ["the plan was not approved"], "duration_s": 0.1}
            (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
            emit("run_finished", run_id=args.run_id, status="cancelled")
            return 1
        if answer.get("decision") == "edit":
            edited = json.loads((run_dir / "plan.edited.json").read_text(encoding="utf-8"))
            (run_dir / "plan_edit_seen.json").write_text(json.dumps([step["text"] for step in edited["steps"]]), encoding="utf-8")
    delay = 0.4 if "fake-slow" in tags else 0.3 if channel is not None else 0.0
    fail_at = 2 if "fake-fail" in tags else None
    results = []
    failed = False
    skipped_by_tester = False
    for index, step in enumerate(case["steps"], start=1):
        if failed:
            results.append({"index": index, "action": step["action"], "status": "skipped", "expected": step.get("expected")})
            continue
        status = "passed"
        if channel is not None:
            if channel.before_step(index, step["action"]) == "skip":
                emit("step_finished", step=index, status="skipped", duration_ms=0)
                results.append({"index": index, "action": step["action"], "status": "skipped", "expected": step.get("expected"), "actual": "skipped by the tester"})
                skipped_by_tester = True
                continue
            if "fake-approval" in tags and index == 2:
                step_dir = run_dir / "steps" / str(index)
                step_dir.mkdir(parents=True, exist_ok=True)
                (step_dir / "plain.jpg").write_bytes(_jpeg((240, 200, 40)))
                answer = channel.ask(index, f"hercules://runs/{args.run_id}/steps/{index}/plain.jpg")
                if answer.get("decision") == "edit":
                    step = {**step, "action": answer.get("step_text")}
                elif answer.get("decision") != "approve":
                    status, failed = "blocked", True
        emit("step_started", step=index, action=step["action"])
        time.sleep(delay)
        evidence = []
        if index == fail_at and status == "passed":
            status, failed = "failed", True
            step_dir = run_dir / "steps" / str(index)
            step_dir.mkdir(parents=True, exist_ok=True)
            (step_dir / "plain.jpg").write_bytes(_jpeg((200, 30, 30)))
            (step_dir / "som.jpg").write_bytes(_jpeg((30, 30, 200)))
            (step_dir / "observation.txt").write_text("page: Sign in\n[md_1] button Sign in\n", encoding="utf-8")
            evidence = [f"hercules://runs/{args.run_id}/steps/{index}/{name}" for name in ("plain.jpg", "som.jpg", "observation.txt")]
            emit("screenshot", step=index, evidence=evidence[:2])
        emit("action", step=index, action="click", ok=status == "passed")
        failed = failed or status != "passed"
        emit("step_finished", step=index, status=status, duration_ms=5)
        result = {"index": index, "action": step["action"], "status": status, "expected": step.get("expected"), "duration_ms": 5, "evidence": evidence}
        if status == "failed":
            result["actual"] = "the text 'Welcome' was not found"
        results.append(result)
    status = "failed" if failed or skipped_by_tester else "passed"
    run = {
        "run_id": args.run_id,
        "status": status,
        "case_id": case["id"],
        "verdict_source": "ledger",
        "steps": results,
        "hints": ["check the welcome message"] if failed else [],
        "engines": {"decider": "fake", "egress": "local", "mode": "test"},
        "duration_s": 0.1,
    }
    run["automation"] = _automation(case, results)
    if "fake-quality" in tags or "--a11y" in extra:
        run["quality"] = _quality(args.run_id, run_dir, gated="--quality-fail-on" in extra)
        if "--quality-fail-on" in extra:
            run["status"] = status = "failed"
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    emit("run_finished", run_id=args.run_id, status=status)
    return 0 if status == "passed" else 1


def _plan(case: dict) -> dict:
    """The case's plan as the engine would make it: one click per step, an inferred criterion on the first, a lint warning."""
    import hashlib

    digest = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
    sources = [{"number": number, "action": step["action"], **({"expected": step["expected"]} if step.get("expected") else {})} for number, step in enumerate(case["steps"], start=1)]
    steps = []
    for number, step in enumerate(case["steps"], start=1):
        item = {"id": f"{number}.1", "step": number, "kind": "click", "text": f'click "{step["action"]}"', "target": step["action"]}
        if number == 1:
            item["criteria"] = [{"id": "1.1.c1", "text": "a dialog is open", "origin": "inferred", "check": "element_state", "state": "open"}]
        steps.append(item)
    lint = [{"severity": "warning", "code": "suspicious_value", "step": 1, "message": "the value looks cut off", "suggestion": "write the whole value", "source": "model"}]
    return {"case_id": case["id"], "case_hash": digest, "prompt_version": "case-7", "model": "fake-model", "source_steps": sources, "steps": steps, "lint": lint}


def _automation(case: dict, results: list[dict]) -> dict:
    """What the steps did, as the engine records it: each step clicks a button named like the step (a unique role locator)."""
    steps = []
    for result in results:
        target = {"role": "button", "name": result["action"], "locator": {"strategy": "role", "role": "button", "name": result["action"]}, "checked": 1}
        steps.append({"index": result["index"], "action": result["action"], "status": result["status"], "actions": [{"kind": "click", "target": target}]})
    return {"case_id": case["id"], "title": case["title"], "base_url": (case.get("environment") or {}).get("base_url"), "steps": steps}


def _quality(run_id: str, run_dir: Path, *, gated: bool) -> dict:
    from PIL import Image

    folder = run_dir / "steps" / "1" / "quality"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 800), (250, 250, 250)).save(folder / "run-a11y.annotated.png")
    for name in ("before", "after", "diff"):
        Image.new("RGB", (640, 400), (240, 240, 255)).save(folder / f"home.{name}.png")
    uri = f"hercules://runs/{run_id}/steps/1/quality"
    finding = {
        "id": "color-contrast:abc123",
        "kind": "a11y",
        "rule": "color-contrast",
        "source": "axe",
        "wcag": "1.4.3",
        "level": "AA",
        "impact": "serious",
        "selector": "#subscribe",
        "element_desc": 'button "Subscribe"',
        "message": "Text contrast 3.19:1 is below 4.5:1",
        "measured": {"ratio": 3.19, "bbox": [24, 120, 110, 40]},
    }
    fix = {
        "finding_ref": "color-contrast:abc123",
        "property_or_attr": "background-color",
        "current": "#4a90e2",
        "suggested": "#3278c9",
        "changes": {"background-color": "#3278c9"},
        "after_fix": {"ratio": 4.5},
        "verified": True,
        "method": "css_override",
        "snippet": "#subscribe { background-color: #3278c9; }",
        "likely_file": "src/components/SubscribeButton.tsx",
        "rationale": "Nearest passing colour.",
        "confidence": 0.9,
    }
    visual = {"baseline": "home", "diff_ratio": 0.02, "threshold": 0.001, "changed": True, "images": {name: f"{uri}/home.{name}.png" for name in ("before", "after", "diff")}}
    return {
        "a11y": True,
        "fail_on": "serious" if gated else None,
        "pages": [
            {"kind": "a11y", "source": "run", "step": 1, "url": "http://127.0.0.1/", "findings": [finding], "fixes": [fix], "evidence": [f"{uri}/run-a11y.annotated.png"]},
            {"kind": "visual", "source": "run", "step": 1, "url": "http://127.0.0.1/", "visual": visual, "evidence": list(visual["images"].values())},
        ],
    }


class _Channel:
    """Control messages on stdin, and frames on the frames pipe while someone watches."""

    def __init__(self, emit: object, frames_fd: int | None) -> None:
        import threading

        self.emit = emit
        self.lock = threading.Condition()
        self.paused = False
        self.stepping = False
        self.directive: str | None = None
        self.answer: dict | None = None
        self.streaming = False
        self.frames_fd = frames_fd
        threading.Thread(target=self._read, daemon=True).start()
        if frames_fd is not None:
            threading.Thread(target=self._frames, daemon=True).start()

    def _read(self) -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                kind = message.get("type")
                action = message.get("action")
                if kind == "screencast":
                    self.streaming = bool(message.get("on"))
                elif kind == "approval":
                    self.answer = message
                elif kind == "input":
                    self.emit("input_result", ok=self.paused, kind=message.get("kind"), detail="clicked" if self.paused else "pause first")
                elif action == "pause":
                    self.paused, self.stepping = True, False
                elif action == "resume":
                    self.paused, self.stepping = False, False
                elif action == "step":
                    self.paused, self.stepping = False, True
                elif action in ("skip", "retry"):
                    self.directive = action
                self.lock.notify_all()

    def _frames(self) -> None:
        import struct

        jpeg = _jpeg((20, 120, 200))
        while True:
            time.sleep(0.1)
            with self.lock:
                on = self.streaming
            if not on:
                continue
            try:
                os.write(self.frames_fd, struct.pack(">IHHd", len(jpeg), 1600, 1000, time.time()) + jpeg)
            except OSError:
                return

    def before_step(self, index: int, action: str) -> str:
        with self.lock:
            if self.stepping and index > 1:
                self.paused, self.stepping = True, False
            if not self.paused:
                return "run"
            self.emit("paused", step=index, where="step", action=action, state="paused")
            while self.paused and self.directive is None:
                self.lock.wait()
            directive, self.directive = self.directive, None
            if directive == "skip":
                return "skip"
            self.emit("resumed", step=index, state="running")
            return "run"

    def ask(self, index: int, screenshot: str | None = None, kind: str = "destructive") -> dict:
        with self.lock:
            self.answer = None
            plan = kind == "plan"
            self.emit(
                "needs_approval",
                id="q1",
                kind=kind,
                step=index,
                reason="review the run's plan before it starts" if plan else 'about to click "Delete", which looks destructive ("delete")',
                action="" if plan else 'click button "Delete"',
                target=None if plan else "Delete",
                screenshot=screenshot,
                timeout_s=600,
                allow_edit=True,
            )
            while self.answer is None:
                self.lock.wait()
            answer = self.answer
            self.emit(
                "approval_result",
                id="q1",
                kind=kind,
                step=index,
                decision=answer.get("decision"),
                step_text=answer.get("step_text"),
                timed_out=False,
                state="paused" if self.paused else "running",
            )
            return answer


if __name__ == "__main__":
    sys.exit(main())
