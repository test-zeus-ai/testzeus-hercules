"""MCP tool handlers: each maps onto the daemon API and returns structured content plus a short text.

Every call finishes within ``CALL_BUDGET_S`` (55 s): long runs are polled with
``wait_s`` (at most 50 s), never awaited in one call, so clients with a 60 s tool
timeout are safe. Only the failing step's screenshot is inlined, downscaled; other
evidence is returned as ``hercules://`` resource links and local file paths.

Accessibility and visual findings (``RunResult.quality``) are summarised in
``hercules_run_result`` (counts and the top fixes with their verified values) and listed in
full by ``hercules_quality_report``; both inline the worst finding's annotated screenshot
(or its visual diff).

A run that waits for a person (``needs_approval`` while running) is reported with
the question and its screenshot inline. When the client supports MCP elicitation,
``hercules_run_status`` asks the user directly (within the call budget) and sends
the answer; otherwise the agent answers with ``hercules_approve``.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import mcp.types as types

from testzeus_hercules.mcp.daemon_client import DaemonClient, DaemonUnavailable
from testzeus_hercules.mcp.explore_tools import ExploreToolsMixin
from testzeus_hercules.mcp.specs import MAX_WAIT_S, RUN_SUMMARY

CALL_BUDGET_S = 55.0
POLL_SLICE_S = 10.0
DEFAULT_STATUS_WAIT_S = 20.0
IMAGE_SIDE = 1024
TEXT_CLIP = 20_000
MAX_RESULT_JSON = 30_000
MAX_LINKS = 12
_URI = re.compile(r"^hercules://runs/([A-Za-z0-9_.-]{1,128})/([A-Za-z0-9._/-]{1,512})$")
_PROBLEM = ("failed", "error", "blocked", "unverified")
IMPACTS = ("critical", "serious", "moderate", "minor")
DEFAULT_QUALITY_LIMIT = 20
TOP_FIXES = 3
_RUN_FLAGS = ("a11y", "visual", "confirm_destructive", "ask_on_failure", "strict")
_RUN_VALUES = ("environment", "base_url", "mode", "egress", "fail_on", "destructive_terms", "approval_timeout_s", "plan_review")
_SUMMARY_KEYS = tuple(RUN_SUMMARY["properties"])

Progress = Callable[[float, float | None, str | None], Awaitable[None]]
# Ask the user (MCP elicitation): message, flat JSON schema, seconds → the accepted answer, or None.
Elicit = Callable[[str, dict[str, Any], float], Awaitable[dict[str, Any] | None]]
ELICIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reject", "edit"], "description": "approve: go ahead; reject: do not; edit: replace the step with new text"},
        "step_text": {"type": "string", "description": "The step's new text (only for edit)"},
    },
    "required": ["decision"],
}
_MIN_ELICIT_S = 5.0
# Questions only a person may answer yes to: the agent can reject them, but approving or editing goes through elicitation.
HUMAN_ONLY_KINDS = ("destructive", "recovery_exhausted")


class ToolError(Exception):
    """Reported to the agent as a tool error (``isError``) with this message."""


@dataclass
class ToolOutput:
    structured: dict[str, Any]
    text: str
    extra: list[Any] = field(default_factory=list)

    def content(self) -> list[Any]:
        return [types.TextContent(type="text", text=self.text), *self.extra]


async def _no_progress(progress: float, total: float | None, message: str | None) -> None:
    return None


def summary(view: dict[str, Any]) -> dict[str, Any]:
    return {key: view[key] for key in _SUMMARY_KEYS if key in view}


class HerculesTools(ExploreToolsMixin):
    def __init__(self, client: DaemonClient) -> None:
        self.client = client
        self._elicit: Elicit | None = None

    async def call(self, name: str, arguments: dict[str, Any], progress: Progress = _no_progress, elicit: Elicit | None = None) -> ToolOutput:
        handler = getattr(self, f"_{name.removeprefix('hercules_')}", None)
        if handler is None or not name.startswith("hercules_"):
            raise ToolError(f"unknown tool {name}")
        started = time.monotonic()
        self._elicit = elicit
        try:
            return await handler(arguments, progress, started)
        except DaemonUnavailable as exc:
            raise ToolError(str(exc)) from None
        except httpx.HTTPError as exc:
            raise ToolError(f"the Hercules daemon did not answer ({type(exc).__name__}); check it with hercules_check_env") from None

    # ------------------------------------------------------------------ daemon helpers

    async def _api(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        hints: dict[str, str] | None = None,
        prefix: str = "",
    ) -> Any:
        """The daemon's JSON answer; an error answer becomes a ToolError (``prefix`` + message, and the hint for its
        ``detail.code`` from ``hints``)."""
        response = await self.client.request(method, path, json=json_body, params=params, timeout_s=timeout_s)
        if response.status_code >= 400:
            code = None
            try:
                answer = response.json()
                message = answer.get("error") or response.text
                code = (answer.get("detail") or {}).get("code") if isinstance(answer.get("detail"), dict) else None
            except ValueError:
                message = response.text
            hint = f" ({hints[code]})" if hints and code in hints else ""
            if response.status_code == 404:
                raise ToolError(f"{prefix}not found: {message}{hint}")
            raise ToolError(f"{prefix}{str(message)[:1000]}{hint}")
        return response.json()

    async def _view(self, run_id: str, *, wait_s: float = 0.0, after_seq: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"wait_s": round(wait_s, 2)}
        if after_seq is not None:
            params["after_seq"] = after_seq
        return await self._api("GET", f"/api/runs/{_run_id(run_id)}", params=params, timeout_s=wait_s + 10)

    async def _wait(self, run_ids: list[str], wait_s: float, started: float, progress: Progress) -> list[dict[str, Any]]:
        """Poll the runs until they settle or ``wait_s`` runs out, reporting step progress."""
        deadline = min(time.monotonic() + wait_s, started + CALL_BUDGET_S - 3)
        views = [await self._view(run_id) for run_id in run_ids]
        while True:
            open_runs = [index for index, view in enumerate(views) if not view["finished"] and view["status"] not in ("needs_approval", "needs_plan_approval")]
            remaining = deadline - time.monotonic()
            if not open_runs or remaining <= 0:
                return views
            index = open_runs[0]
            view = views[index]
            views[index] = await self._view(view["run_id"], wait_s=min(remaining, POLL_SLICE_S), after_seq=view["last_seq"])
            await self._report(views[index], progress, len(run_ids))

    @staticmethod
    async def _report(view: dict[str, Any], progress: Progress, runs: int) -> None:
        total = view.get("total_steps")
        step = view.get("current_step")
        prefix = f"{view['run_id']} " if runs > 1 else ""
        if view["finished"]:
            message = f"{prefix}{view['status']}"
        elif step:
            message = f"{prefix}step {step}/{total or '?'}: {view.get('current_action') or ''}".strip()
        else:
            message = f"{prefix}{view['status']}"
        await progress(float(view.get("steps_done") or 0), float(total) if total else None, message[:200])

    def _runs_output(self, views: list[dict[str, Any]]) -> ToolOutput:
        runs = [summary(view) for view in views]
        all_finished = all(view["finished"] for view in views)
        lines = []
        for view in views:
            lines.append(view["summary"])
            if view.get("approval"):
                lines.append(_approval_text(view))
            elif view["status"] == "needs_approval":
                steps = "\n".join(f"  {number}. {action}" for number, action in enumerate(view.get("draft_steps") or [], start=1))
                lines.append(f"Drafted steps (review them, then call hercules_approve with run_id {view['run_id']}):\n{steps}")
        if not all_finished:
            lines.append("Poll hercules_run_status with each run_id (wait_s up to 50) until finished, then call hercules_run_result.")
        elif any(view["status"] != "passed" for view in views):
            lines.append("Call hercules_run_result for the failing step, its screenshot and hints.")
        return ToolOutput({"runs": runs, "all_finished": all_finished}, "\n".join(lines))

    # ------------------------------------------------------------------ tools

    async def _run(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        bodies = _run_bodies(args)
        created = []
        for number, body in enumerate(bodies, start=1):
            try:
                created.append(await self._api("POST", "/api/runs", json_body=body))
            except ToolError as exc:
                where = f"case {number} of {len(bodies)}: " if len(bodies) > 1 else ""
                queued = f" (already queued: {', '.join(item['run_id'] for item in created)})" if created else ""
                raise ToolError(f"{where}{exc}{queued}") from None
        views = await self._wait([item["run_id"] for item in created], float(args.get("wait_s") or 0), started, progress)
        return self._runs_output(views)

    async def _run_status(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        wait_s = _wait_arg(args, DEFAULT_STATUS_WAIT_S)
        view = (await self._wait([args["run_id"]], wait_s, started, progress))[0]
        answered = None
        if view.get("approval") and self._elicit is not None:
            answered = await self._ask_user(view, started)
            if answered is not None:
                view = await self._view(view["run_id"])
        structured = {**summary(view), "last_seq": view["last_seq"]}
        text = view["summary"]
        extra: list[Any] = []
        if answered is not None:
            text += f"\nThe user answered: {answered}."
        if view["finished"]:
            text += "\nFinished: call hercules_run_result for details." if view["status"] != "passed" else ""
        elif view.get("approval"):
            text += "\n" + _approval_text(view)
            image = await self._evidence_image(view["run_id"], view["approval"].get("screenshot"))
            if image is not None:
                extra.append(image)
                text += "\nScreenshot of the page attached."
        elif view["status"] == "needs_approval":
            text += "\nWaiting for approval: call hercules_approve."
        elif view["status"] == "needs_plan_approval":
            text += f"\nWaiting for its plan's approval: read it with hercules_plan (run_id {view['run_id']}), then hercules_approve."
        elif view["status"] == "paused":
            text += "\nPaused by a tester in the UI: call hercules_run_status again."
        else:
            text += "\nStill running: call hercules_run_status again."
        return ToolOutput(structured, text, extra)

    async def _ask_user(self, view: dict[str, Any], started: float) -> str | None:
        """Elicit the user's decision on the run's question; None when they declined or time ran out."""
        assert self._elicit is not None
        remaining = started + CALL_BUDGET_S - 5 - time.monotonic()
        if remaining < _MIN_ELICIT_S:
            return None
        approval = view["approval"]
        message = f"Hercules run {view['run_id']} (step {approval.get('step')}) needs your decision: {approval.get('reason')}"
        if approval.get("action"):
            message += f"\nAction: {approval['action']}"
        try:
            answer = await self._elicit(message[:1000], ELICIT_SCHEMA, remaining)
        except Exception:  # an elicitation the client could not show: fall back to hercules_approve
            return None
        decision = (answer or {}).get("decision")
        if decision not in ("approve", "reject", "edit"):
            return None
        body: dict[str, Any] = {"decision": decision}
        if approval.get("id"):
            body["approval_id"] = approval["id"]
        if decision == "edit":
            text = str((answer or {}).get("step_text") or "").strip()
            if not text:
                return None
            body["step_text"] = text
        try:
            await self._api("POST", f"/api/runs/{_run_id(view['run_id'])}/approve", json_body=body)
        except ToolError:
            return None
        return decision

    async def _evidence_image(self, run_id: str, uri: str | None) -> types.ImageContent | None:
        parsed = _URI.match(str(uri or ""))
        if parsed is None or parsed.group(1) != run_id:
            return None
        response = await self.client.request("GET", f"/api/runs/{_run_id(run_id)}/evidence/{parsed.group(2)}", params={"max_side": IMAGE_SIDE})
        if response.status_code != 200:
            return None
        return types.ImageContent(type="image", data=base64.b64encode(response.content).decode(), mimeType="image/jpeg")

    async def _run_result(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        view = await self._view(args["run_id"])
        run_id, run_dir = view["run_id"], Path(view["run_dir"])
        base = {"run_id": run_id, "status": view["status"], "finished": view["finished"], "summary": view["summary"], "reason": view.get("reason"), "run_dir": str(run_dir), "ui_url": view["ui_url"]}
        if not view["finished"]:
            return ToolOutput({**base, "evidence": [], "hints": [], "result": None, "failing_step": None}, view["summary"] + "\nNot finished yet: poll hercules_run_status first.")
        result = view.get("result")
        evidence = _evidence_entries(run_id, run_dir, result)
        failing = next((step for step in (result or {}).get("steps", []) if step.get("status") in _PROBLEM), None)
        compact, trimmed = _compact_result(result)
        structured = {**base, "result": compact, "steps_trimmed": trimmed, "failing_step": failing, "hints": list((result or {}).get("hints", [])), "evidence": evidence}
        quality = (result or {}).get("quality")
        has_quality = bool(quality and quality.get("pages"))
        lines = [_without_quality_lines(view["summary"]) if has_quality else view["summary"]]
        extra: list[Any] = []
        if failing is not None:
            image = await self._failing_image(run_id, failing, args.get("image", "plain"))
            if image is not None:
                extra.append(image)
                lines.append(f"Screenshot of step {failing['index']} attached.")
        if has_quality:
            structured["quality"] = _quality_brief(quality)
            lines.extend(_quality_lines(quality))
            image = await self._worst_image(run_id, quality)
            if image is not None and len([item for item in extra if isinstance(item, types.ImageContent)]) < 2:
                extra.append(image)
                lines.append("Screenshot of the worst quality finding attached.")
        lines.append(f"Evidence: {run_dir}")
        extra.extend(_links(evidence, failing))
        return ToolOutput(structured, "\n".join(lines), extra)

    async def _worst_image(self, run_id: str, quality: dict[str, Any]) -> types.ImageContent | None:
        """The annotated screenshot (or visual diff) of the page holding the most severe finding."""
        ranked = _ranked_findings(quality)
        for entry in ranked[:5]:
            for page in entry["pages_raw"]:
                uri = (page.get("visual") or {}).get("images", {}).get("diff") if page.get("kind") == "visual" else None
                uri = uri or next((item for item in page.get("evidence", []) if str(item).endswith(".annotated.png")), None)
                image = await self._evidence_image(run_id, uri)
                if image is not None:
                    return image
        return None

    async def _quality_report(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        view = await self._view(args["run_id"])
        run_id = view["run_id"]
        base: dict[str, Any] = {
            "run_id": run_id,
            "status": view["status"],
            "finished": view["finished"],
            "counts": None,
            "fail_on": None,
            "findings": [],
            "visual": [],
            "pages": [],
            "total": 0,
            "truncated": False,
        }
        if not view["finished"]:
            return ToolOutput(base, view["summary"] + "\nNot finished yet: poll hercules_run_status first.")
        quality = (view.get("result") or {}).get("quality")
        if not quality or not quality.get("pages"):
            return ToolOutput(base, f"Run {run_id} has no quality results: add a quality step, or run with a11y or visual.")
        gate = str(args.get("min_impact") or "minor")
        if gate not in IMPACTS:
            raise ToolError("min_impact must be critical, serious, moderate or minor")
        kind = args.get("kind")
        limit = int(args.get("limit") or DEFAULT_QUALITY_LIMIT)
        if not 1 <= limit <= 100:
            raise ToolError("limit must be between 1 and 100")
        ranked = [entry for entry in _ranked_findings(quality) if IMPACTS.index(entry["finding"]["impact"]) <= IMPACTS.index(gate) and (kind is None or entry["finding"]["kind"] == kind)]
        findings = [{**entry["finding"], "fixes": entry["fixes"], "found_on": entry["found_on"]} for entry in ranked[:limit]]
        visual = [{"step": page["step"], "source": page["source"], "url": page.get("url"), **page["visual"]} for page in quality["pages"] if page.get("visual")]
        pages = [_page_brief(page) for page in quality["pages"]]
        structured = {
            **base,
            "counts": quality.get("counts"),
            "fail_on": quality.get("fail_on"),
            "findings": findings,
            "visual": visual,
            "pages": pages,
            "total": len(ranked),
            "truncated": len(ranked) > limit,
        }
        lines = [_quality_lines(quality)[0]]
        for number, item in enumerate(findings, start=1):
            lines.append(_finding_line(number, item))
        for item in visual:
            lines.append(_visual_line(item))
        if len(ranked) > limit:
            lines.append(f"... {len(ranked) - limit} more; raise limit or filter with min_impact / kind.")
        extra: list[Any] = []
        if args.get("image", True) and ranked:
            image = await self._worst_image(run_id, {"pages": ranked[0]["pages_raw"]})
            if image is not None:
                extra.append(image)
                lines.append("Screenshot of the worst finding attached (numbered boxes follow each page's findings order).")
        return ToolOutput(structured, "\n".join(lines)[:TEXT_CLIP], extra)

    async def _export_code(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        params: dict[str, Any] = {"format": args["format"]} if args.get("format") else {}  # the daemon picks the run's default
        if args.get("allow_failed"):
            params["allow_failed"] = "true"
        if args.get("target_dir"):
            params["target_dir"] = str(args["target_dir"])
        exported = await self._api("GET", f"/api/runs/{_run_id(args['run_id'])}/export", params=params)
        keys = ("run_id", "format", "case_id", "filename", "code", "spec", "env", "notes", "partial", "steps_exported", "steps_total", "guidance")
        structured = {key: exported.get(key) for key in keys}
        lines = [
            f"Run {exported['run_id']} exported as {exported['format']} ({exported['steps_exported']} of {exported['steps_total']} steps). Suggested file: {exported['filename']}",
            exported["guidance"],
        ]
        lines += [f"Environment: {item['name']} ({'required' if item['required'] else 'optional'}): {item['purpose']}" for item in exported.get("env", [])]
        lines += [f"Review: {note}" for note in exported.get("notes", [])]
        fence = {"playwright-ts": "ts", "playwright-py": "python", "appium-py": "python"}.get(exported["format"], "json")
        lines += ["", f"```{fence}", exported["code"].rstrip("\n"), "```"]
        return ToolOutput(structured, "\n".join(lines))

    async def _failing_image(self, run_id: str, step: dict[str, Any], choice: str) -> types.ImageContent | None:
        if choice == "none":
            return None
        order = ("som.jpg", "plain.jpg") if choice == "som" else ("plain.jpg", "som.jpg")
        uris = [str(uri) for uri in step.get("evidence", [])]
        for name in order:
            uri = next((item for item in uris if item.endswith("/" + name)), None)
            if uri is None:
                continue
            parsed = _URI.match(uri)
            if parsed is None:
                continue
            response = await self.client.request("GET", f"/api/runs/{_run_id(run_id)}/evidence/{parsed.group(2)}", params={"max_side": IMAGE_SIDE})
            if response.status_code == 200:
                return types.ImageContent(type="image", data=base64.b64encode(response.content).decode(), mimeType="image/jpeg")
        return None

    async def _run_cancel(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        view = await self._api("POST", f"/api/runs/{_run_id(args['run_id'])}/cancel")
        return ToolOutput(summary(view), f"{view['summary']}\nCancelling; poll hercules_run_status to see it finish." if not view["finished"] else view["summary"])

    async def _approve(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        decision = str(args.get("decision") or "approve")
        if decision not in ("approve", "reject", "edit"):
            raise ToolError("decision must be approve, reject or edit")
        run_id = _run_id(args["run_id"])
        approval = (await self._view(run_id)).get("approval") or {}
        if approval.get("kind") in HUMAN_ONLY_KINDS and decision != "reject":
            what = "a destructive action" if approval["kind"] == "destructive" else "retrying a step it could not do"
            raise ToolError(
                f"run {run_id} asks a person to approve {what} (step {approval.get('step')}): an agent may only reject it. "
                f"Ask the user to answer it in the UI ({_ui_url(run_id, self.client)}), or call hercules_run_status, which asks them directly "
                "when your client supports elicitation."
            )
        body: dict[str, Any] = {"decision": decision}
        if approval.get("id"):
            body["approval_id"] = approval["id"]  # answer this question only, never one asked after it
        if decision == "edit" and isinstance(args.get("plan"), dict):
            body["plan"] = args["plan"]
        elif decision == "edit":
            text = str(args.get("step_text") or "").strip()
            if not text:
                raise ToolError("edit needs step_text (the step's new text) or plan (the edited plan)")
            body["step_text"] = text
        view = await self._api("POST", f"/api/runs/{run_id}/approve", json_body=body)
        done = {"approve": "Approved", "reject": "Rejected", "edit": "Edited"}[decision]
        return ToolOutput(summary(view), f"{view['summary']}\n{done}; poll hercules_run_status.")

    async def _plan(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        if args.get("run_id"):
            run_id = _run_id(args["run_id"])
            diff_params = {"against": _run_id(args["against"])} if args.get("against") else None
            view = await self._api("GET", f"/api/runs/{run_id}/plan")
            diff = (await self._api("GET", f"/api/runs/{run_id}/plan/diff", params=diff_params))["diff"]
            structured = {"plan": view["plan"], "lint_errors": view["lint_errors"], "lint_warnings": view["lint_warnings"], "approved": view["approved"], "diff": diff}
            changes = f"{len(diff['changed'])} changed, {len(diff['added'])} added, {len(diff['removed'])} removed plan steps" + (" (order changed)" if diff.get("order") else "")
            what = f"against run {args['against']}" if args.get("against") else "by the run (amendments)"
            return ToolOutput(structured, f"{view['summary']}\nChanges {what}: {changes}.")
        given = [key for key in ("test_cases", "gherkin", "case_path") if args.get(key) not in (None, "", [])]
        if len(given) != 1:
            raise ToolError(f"give exactly one of test_cases, gherkin, case_path or run_id (got {', '.join(given) or 'none'})")
        key = given[0]
        body: dict[str, Any] = {"test_case" if key == "test_cases" else key: args[key]}
        body.update({name: args[name] for name in ("environment", "base_url", "mode", "egress") if args.get(name) not in (None, "")})
        body.update({name: True for name in ("strict", "approve") if args.get(name) is True})
        await progress(0.0, None, "making the plan")
        view = await self._api("POST", "/api/plans", json_body=body, timeout_s=CALL_BUDGET_S - 2)
        structured = {"plan": view["plan"], "lint_errors": view["lint_errors"], "lint_warnings": view["lint_warnings"], "approved": view["approved"], "diff": None}
        advice = (
            "\nLint errors: fix the case (or pass the edited plan to hercules_run with plan) before running it."
            if view["lint_errors"]
            else "\nRun it with hercules_run (pass plan to run this plan as edited)."
        )
        return ToolOutput(structured, view["summary"] + advice)

    async def _rerun(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        scope = args.get("scope", "failed_only")
        if scope == "failed_only":
            view = await self._view(args["run_id"])
            if view["status"] == "passed":
                text = f"Run {view['run_id']} passed: nothing failed, so there is nothing to re-run. Use scope \"whole\" to run it again."
                return ToolOutput({"runs": [], "all_finished": True}, text)
        _wait_arg(args, 0)
        # Options given change the new run's (false included: it turns a check off); the daemon validates them.
        overrides = {key: args[key] for key in (*_RUN_VALUES, *_RUN_FLAGS) if key in args}
        created = await self._api("POST", f"/api/runs/{_run_id(args['run_id'])}/rerun", json_body={"scope": scope, **overrides})
        views = await self._wait([created["run_id"]], float(args.get("wait_s") or 0), started, progress)
        return self._runs_output(views)

    async def _list_runs(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        params: dict[str, Any] = {"limit": int(args.get("limit") or 10)}
        for key in ("status", "batch"):
            if args.get(key):
                params[key] = args[key]
        views = (await self._api("GET", "/api/runs", params=params))["runs"]
        text = "\n".join(view["summary"].splitlines()[0] for view in views) or "No runs yet."
        return ToolOutput({"runs": [summary(view) for view in views]}, text)

    async def _list_cases(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        limit = int(args.get("limit") or 50)
        params = {key: str(args[key]).strip() for key in ("folder", "query") if str(args.get(key) or "").strip()}
        listing = await self._api("GET", "/api/cases", params=params or None)
        found = listing.get("cases", [])
        fields = ("path", "folder", "kind", "valid", "id", "title", "status", "steps", "environment", "error")
        cases = [{key: entry.get(key) for key in fields} for entry in found[:limit]]
        for case in cases:
            case["folder"], case["kind"], case["valid"] = str(case["folder"] or ""), str(case["kind"] or ""), bool(case["valid"])
        lines = []
        for case in cases:
            where = f"[{case['folder']}] " if case["folder"] else ""
            if case["valid"]:
                extra = ", ".join(
                    part
                    for part in (
                        f"{case['steps']} steps" if case["steps"] is not None else "",
                        f"env {case['environment']}" if case["environment"] else "",
                        "draft" if case["status"] == "draft" else "",
                    )
                    if part
                )
                lines.append(f"- {where}{case['title']}: {case['path']}" + (f" ({extra})" if extra else ""))
            else:
                lines.append(f"- {where}{case['path']}: invalid ({case['error']})")
        truncated = bool(listing.get("truncated"))
        count = f"{len(found)}{'+' if truncated else ''}"
        if not lines:
            text = "No cases match." if params else "No saved cases yet: save .json or .feature files in .hercules/cases."
        else:
            notes = []
            if len(found) > limit:
                notes.append(f"… {len(found) - limit} more; narrow it with folder or query, or raise limit.")
            if truncated:
                notes.append(f"The project has more than {len(found)} matching cases; narrow it with folder or query to see the rest.")
            text = f"{count} case(s); run one with hercules_run case_path=<path>:\n" + "\n".join(lines) + "".join(f"\n{note}" for note in notes)
        return ToolOutput({"cases": cases, "folders": list(listing.get("folders", [])), "total": len(found), "truncated": truncated}, text)

    @staticmethod
    def _case_source(args: dict[str, Any]) -> dict[str, Any]:
        """The one case source in ``args`` (gherkin, with its test_data, or test_case) as the daemon takes it."""
        given = [key for key in ("gherkin", "test_case") if args.get(key) not in (None, "", {})]
        if len(given) != 1:
            raise ToolError(f"give exactly one of gherkin or test_case (got {', '.join(given) or 'none'})")
        if args.get("test_data") is not None and given[0] != "gherkin":
            raise ToolError("test_data goes with gherkin; a test_case carries its own test_data")
        body: dict[str, Any] = {given[0]: args[given[0]]}
        if args.get("test_data") is not None:
            body["test_data"] = args["test_data"]
        return body

    async def _get_case(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _case_name(args.get("name"))
        view = await self._api("GET", f"/api/cases/{_quote_case(name)}")
        structured = {
            "name": view["name"],
            "path": f"{_CASES_PREFIX}{view['name']}",
            "folder": view.get("folder") or "",
            "kind": view["kind"],
            "source": view.get("source"),
            "test_case": view["test_case"],
        }
        case = view["test_case"]
        head = f"{structured['path']}: {case.get('title')} ({len(case.get('steps') or [])} steps"
        head += f", env {case['environment']['ref']})" if (case.get("environment") or {}).get("ref") else ")"
        return ToolOutput(structured, head + "\n\n" + (view.get("source") or json.dumps(case, indent=2)))

    async def _validate_case(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        source = self._case_source(args)
        checked = await self._api("POST", "/api/cases/validate", json_body=source)
        case = checked.get("test_case") or {}
        valid = bool(checked.get("valid"))
        result: dict[str, Any] = {"valid": valid, "error": checked.get("error"), "id": None, "title": None, "steps": None, "environment": None, "file": None}
        if valid:
            result.update(
                id=case.get("id"),
                title=case.get("title"),
                steps=len(case.get("steps") or []),
                environment=(case.get("environment") or {}).get("ref"),
                file=f"{case.get('id')}.{'feature' if 'gherkin' in source else 'json'}",
            )
            env = f", env {result['environment']}" if result["environment"] else ""
            text = f"Valid: {result['title']} ({result['steps']} steps{env}); it would be saved as {result['file']}."
        else:
            text = f"Not valid: {result['error']}"
        return ToolOutput(result, text)

    async def _create_case(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        body = {**self._case_source(args), "overwrite": False, "folder": str(args.get("folder") or "").strip().strip("/")}
        saved = await self._api("POST", "/api/cases", json_body=body, hints=_CASE_HINTS, prefix="nothing was saved: ")
        structured = {"name": saved["name"], "path": saved["path"], "id": saved["id"]}
        return ToolOutput(structured, f"Created {saved['path']} ({saved.get('steps')} steps). Run it with hercules_run case_path={saved['path']}.")

    async def _update_case(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _case_name(args.get("name"))
        body = {**self._case_source(args), "replace": name}
        saved = await self._api("POST", "/api/cases", json_body=body, hints=_CASE_HINTS, prefix=f"{name} was left as it was: ")
        structured = {"name": saved["name"], "path": saved["path"], "id": saved["id"]}
        return ToolOutput(structured, f"Updated {saved['path']} ({saved.get('steps')} steps).")

    async def _delete_case(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _case_name(args.get("name"))
        removed = await self._api("DELETE", f"/api/cases/{_quote_case(name)}")
        return ToolOutput({"name": removed["name"], "removed": bool(removed.get("removed"))}, f"Deleted {_CASES_PREFIX}{removed['name']}.")

    # ------------------------------------------------------------------ folders of cases

    async def _list_folders(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        listing = await self._api("GET", "/api/cases")
        cases = listing.get("cases", [])
        folders = []
        for folder in listing.get("folders", []):
            held = [case for case in cases if _inside(case.get("folder") or "", folder)]
            folders.append({"folder": folder, "cases": len(held), "ready": sum(map(_ready, held))})
        top = sum(1 for case in cases if not case.get("folder"))
        truncated = bool(listing.get("truncated"))
        structured = {"folders": folders, "top_level_cases": top, "truncated": truncated}
        if not folders:
            return ToolOutput(structured, f"No folders yet ({top} case(s) at the top level); make one with hercules_create_folder.")
        lines = [f"- {item['folder']}: {item['cases']} case(s), {item['ready']} ready" for item in folders]
        notes = f"\n{top} case(s) at the top level." if top else ""
        if truncated:
            notes += "\nThe project has more cases than were counted; hercules_get_folder lists one folder's."
        return ToolOutput(structured, f"{len(folders)} folder(s); run one with hercules_run_folder folder=<folder>:\n" + "\n".join(lines) + notes)

    async def _get_folder(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        asked = _folder_name(args.get("folder"))
        listing = await self._api("GET", "/api/cases", params={"folder": asked})
        # The daemon matches a folder's name in any case; answer with it as it is spelled on disk.
        folder = next((name for name in listing.get("folders", []) if name.casefold() == asked.casefold()), asked)
        subfolders = [name for name in listing.get("folders", []) if name.rpartition("/")[0] == folder]
        fields = ("path", "folder", "valid", "title", "status", "steps")
        cases = [{key: entry.get(key) for key in fields} | {"folder": entry.get("folder") or "", "valid": bool(entry.get("valid"))} for entry in listing.get("cases", [])]
        ready = sum(map(_ready, cases))
        truncated = bool(listing.get("truncated"))
        lines = [f"Folder {folder}: {len(cases)} case(s), {ready} ready to run with hercules_run_folder."]
        if subfolders:
            lines.append("Subfolders: " + ", ".join(subfolders))
        for case in cases:
            state = "draft" if case["status"] == "draft" else "ready" if case["valid"] else "does not load"
            lines.append(f"- {case['title'] or case['path']}: {case['path']} ({state})")
        if truncated:
            lines.append("The folder has more cases than the listing holds.")
        return ToolOutput({"folder": folder, "subfolders": subfolders, "cases": cases, "ready": ready, "truncated": truncated}, "\n".join(lines))

    async def _create_folder(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        parent = _folder_name(args.get("parent"), top_level=True)
        made = await self._api("POST", "/api/folders", json_body={"parent": parent, "name": str(args.get("name") or "").strip()}, prefix="no folder was made: ")
        return ToolOutput({"folder": made["folder"]}, f"Created folder {made['folder']}. Save cases into it with hercules_create_case folder={made['folder']}.")

    async def _update_folder(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        folder = _folder_name(args.get("folder"))
        given = [key for key in ("name", "parent") if args.get(key) is not None]
        if len(given) != 1:
            raise ToolError(f"give exactly one of name (to rename it) or parent (to move it) (got {', '.join(given) or 'none'})")
        body = {"name": str(args["name"]).strip()} if given == ["name"] else {"parent": _folder_name(args["parent"], top_level=True)}
        moved = await self._api("PATCH", f"/api/folders/{_quote_case(folder)}", json_body=body, prefix=f"{folder} was left as it was: ")
        verb = "Renamed" if given == ["name"] else "Moved"
        return ToolOutput({"folder": moved["folder"], "was": moved["was"]}, f"{verb} {moved['was']} to {moved['folder']}; its cases' paths now start with {_CASES_PREFIX}{moved['folder']}/.")

    async def _delete_folder(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        folder = _folder_name(args.get("folder"))
        mode = args.get("mode") or "move_up"
        removed = await self._api("DELETE", f"/api/folders/{_quote_case(folder)}", params={"mode": mode}, prefix=f"{folder} was left as it was: ")
        structured = {key: removed[key] for key in ("folder", "mode", "moved", "deleted")}
        if mode == "move_up":
            text = f"Deleted folder {removed['folder']}; {removed['moved']} item(s) moved to the folder above."
        else:
            text = f"Deleted folder {removed['folder']} and its {removed['deleted']} case(s)."
        return ToolOutput(structured, text)

    async def _run_folder(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        _wait_arg(args, 0)
        body: dict[str, Any] = {"folder": _folder_name(args.get("folder"), top_level=True), "subfolders": args.get("subfolders", True) is not False}
        body.update({key: args[key] for key in _RUN_VALUES if args.get(key) not in (None, "", [])})
        body.update({key: True for key in _RUN_FLAGS if args.get(key) is True})
        created = await self._api("POST", "/api/runs/batch", json_body=body, prefix="nothing was run: ")
        skipped = [{"name": item["name"], "reason": item["reason"]} for item in created.get("skipped", [])]
        where = created["folder"] or "every saved case"
        skips = "".join(f"\nSkipped {item['name']}: {item['reason']}" for item in skipped)
        if not created["runs"]:
            return ToolOutput({"runs": [], "all_finished": True, "batch": created["batch"], "folder": created["folder"], "skipped": skipped}, f"Nothing was run in {where}." + skips)
        views = await self._wait([item["run_id"] for item in created["runs"]], float(args.get("wait_s") or 0), started, progress)
        output = self._runs_output(views)
        head = f"Folder run {created['batch']} of {where}: {len(views)} run(s) queued; hercules_list_runs batch={created['batch']} shows how they are doing."
        return ToolOutput({**output.structured, "batch": created["batch"], "folder": created["folder"], "skipped": skipped}, head + skips + "\n" + output.text, output.extra)

    # ------------------------------------------------------------------ environments

    async def _cases_using(self, name: str) -> list[str]:
        """The saved cases that name environment ``name``."""
        listing = await self._api("GET", "/api/cases")
        return [case["path"] for case in listing.get("cases", []) if case.get("environment") == name]

    async def _list_environments(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        envs = (await self._api("GET", "/api/environments"))["environments"]
        items = [_env_brief(env) for env in envs]
        if not items:
            return ToolOutput({"environments": []}, "No saved environments yet; make one with hercules_create_environment.")
        lines = []
        for item in items:
            where = item["base_url"] or f"{item['platform']} app"
            extra = ", ".join(
                part
                for part in (
                    item["browser"],
                    item["device"] and f"device {item['device']}",
                    item["variables"] and f"variables {', '.join(item['variables'])}",
                    item["secret_names"] and f"secrets {', '.join(item['secret_names'])}",
                )
                if part
            )
            lines.append(f"- {item['name']}: {where}" + (f" ({extra})" if extra else ""))
        return ToolOutput({"environments": items}, f"{len(items)} environment(s); run a case in one with hercules_run environment=<name>:\n" + "\n".join(lines))

    async def _get_environment(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _env_name(args.get("name"))
        env = await self._api("GET", f"/api/environments/{name}")
        return await self._env_output(env, f"Environment {name}")

    async def _env_output(self, env: dict[str, Any], head: str) -> ToolOutput:
        used_by = await self._cases_using(env["name"])
        shown = {key: value for key, value in env.items() if value not in (None, [], {})}
        used = f"\nUsed by {len(used_by)} case(s): {', '.join(used_by[:10])}{' and more' if len(used_by) > 10 else ''}" if used_by else "\nNo saved case names it yet."
        return ToolOutput({"environment": env, "used_by": used_by}, f"{head}:\n{json.dumps(shown, indent=2)}{used}")

    async def _create_environment(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _env_name(args.get("name"))
        body = {key: args[key] for key in _ENV_KEYS if args.get(key) is not None}
        saved = await self._save_environment({**body, "name": name}, overwrite=False, prefix="nothing was saved: ")
        return await self._env_output(saved, f"Created environment {name}")

    async def _update_environment(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _env_name(args.get("name"))
        changes = {key: args[key] for key in _ENV_KEYS if key in args}
        if not changes:
            raise ToolError(f"give at least one field to change ({', '.join(_ENV_KEYS)}); null clears one")
        current = await self._api("GET", f"/api/environments/{name}")
        merged = {key: value for key, value in {**current, **changes}.items() if value is not None}
        saved = await self._save_environment({**merged, "name": name}, overwrite=True, prefix=f"{name} was left as it was: ")
        return await self._env_output(saved, f"Updated environment {name} ({', '.join(changes)})")

    async def _save_environment(self, body: dict[str, Any], *, overwrite: bool, prefix: str) -> dict[str, Any]:
        try:
            saved: dict[str, Any] = await self._api("POST", "/api/environments", json_body={**body, "overwrite": overwrite}, prefix=prefix)
        except ToolError as exc:
            if "already exists" in str(exc):
                raise ToolError(f"{prefix}environment {body['name']} already exists; change it with hercules_update_environment") from None
            raise
        return saved

    async def _delete_environment(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        name = _env_name(args.get("name"))
        removed = await self._api("DELETE", f"/api/environments/{name}")
        used_by = await self._cases_using(name)
        text = f"Deleted environment {name}."
        if used_by:
            text += f" {len(used_by)} case(s) still name it and will not validate until it exists again: {', '.join(used_by[:10])}{' and more' if len(used_by) > 10 else ''}."
        return ToolOutput({"name": name, "removed": bool(removed.get("removed")), "used_by": used_by}, text)

    async def _get_evidence(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        uri = str(args["uri"])
        parsed = _URI.match(uri)
        if parsed is None or ".." in uri or "//" in uri[len("hercules://") :]:
            raise ToolError("expected a URI such as hercules://runs/<run_id>/steps/2/observation.txt")
        run_id, relative = parsed.groups()
        max_side = int(args.get("max_side") or IMAGE_SIDE)
        is_image = relative.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        response = await self.client.request("GET", f"/api/runs/{_run_id(run_id)}/evidence/{relative}", params={"max_side": max_side} if is_image else None)
        if response.status_code != 200:
            raise ToolError(f"evidence not found: {uri}")
        mime = response.headers.get("content-type", "application/octet-stream").split(";")[0]
        structured: dict[str, Any] = {"uri": uri, "mime_type": mime, "size_bytes": len(response.content), "truncated": False}
        view = await self._view(run_id)
        structured["file"] = str(Path(view["run_dir"]) / relative)
        if mime.startswith("image/"):
            image = types.ImageContent(type="image", data=base64.b64encode(response.content).decode(), mimeType=mime)
            return ToolOutput(structured, f"{uri} ({mime})", [image])
        if mime.startswith("text/") or mime in ("application/json", "application/x-ndjson", "application/xml"):
            text = response.text
            if len(text) > TEXT_CLIP:
                text, structured["truncated"] = text[:TEXT_CLIP] + "\n… (truncated; read the file for the rest)", True
            return ToolOutput(structured, text)
        return ToolOutput(structured, f"{uri} is binary ({mime}, {len(response.content)} bytes); open {structured['file']}")

    async def _check_env(self, args: dict[str, Any], progress: Progress, started: float) -> ToolOutput:
        try:
            await self.client.ensure()
        except DaemonUnavailable as exc:
            daemon = {"reachable": False, "reason": str(exc), "started_now": False}
            return ToolOutput({"daemon": daemon, "models": None, "modes": {}}, f"Daemon: not reachable ({exc})")
        health = await self._api("GET", "/api/health")
        models = await self._api("GET", "/api/models")
        info = self.client.info
        daemon = {
            "reachable": True,
            "started_now": self.client.started_now,
            "url": info.url if info else None,
            "pid": health.get("pid"),
            "version": health.get("version"),
            "project_dir": health.get("project_dir"),
            "queue": health.get("queue"),
            "parallel_runs": health.get("parallel_runs"),
            "this_project": str(self.client.project_dir.resolve()) == str(Path(health.get("project_dir", "")).resolve()),
        }
        modes = models.get("modes", {})
        lines = [
            f"Daemon: {daemon['url']} (pid {daemon['pid']}, project {daemon['project_dir']}{'' if daemon['this_project'] else ' - a different project than this one'})",
            f"Model: {models.get('provider')} / {models.get('model')} at {(models.get('endpoint') or {}).get('base_url', '?')}; egress policy {models.get('egress_policy')}",
        ]
        for name, mode in modes.items():
            lines.append(f"Mode {name}: {'available' if mode.get('available') else 'not available'}{'' if not mode.get('reason') else ' - ' + str(mode['reason'])}")
        return ToolOutput({"daemon": daemon, "models": {key: value for key, value in models.items() if key != "modes"}, "modes": modes}, "\n".join(lines))


def _approval_text(view: dict[str, Any]) -> str:
    approval = view.get("approval") or {}
    kind = {"destructive": "a destructive action", "recovery_exhausted": "a step it could not do", "draft": "a drafted case", "plan": "its plan, before it starts"}.get(
        str(approval.get("kind")), "a question"
    )
    lines = [f"Waiting for approval at step {approval.get('step')} ({kind}): {approval.get('reason')}"]
    if approval.get("action"):
        lines.append(f"Action: {approval['action']}")
    edit = ", or edit with step_text" if approval.get("allow_edit", True) else ""
    if approval.get("kind") == "plan":
        lines.append(f"Read the plan with hercules_plan (run_id {view['run_id']}).")
        edit = ", or edit with plan (the edited plan)"
    if approval.get("kind") in HUMAN_ONLY_KINDS:
        lines.append(f"Only a person may approve this: ask the user to answer it in the UI ({view.get('ui_url')}); hercules_approve may only reject it (run_id {view['run_id']}).")
    else:
        lines.append(f"Answer with hercules_approve (run_id {view['run_id']}, decision approve or reject{edit}).")
    return "\n".join(lines)


def _run_id(value: Any) -> str:
    """A run id safe to put in a URL path (one segment of ``[A-Za-z0-9_.-]``); a ToolError otherwise."""
    from testzeus_hercules.kernel.paths import validate_run_id  # here, not at import: the shim starts without settings

    try:
        return validate_run_id(str(value))
    except ValueError:
        raise ToolError(f"not a run id: {str(value)[:80]!r} (run ids look like r_20261025_141502_9f3a1c)") from None


def _ui_url(run_id: str, client: DaemonClient) -> str:
    info = client.info
    return f"{info.url}/runs/{run_id}" if info is not None else "the Hercules UI"


def _wait_arg(args: dict[str, Any], default: float) -> float:
    value = float(args.get("wait_s", default))
    if not 0 <= value <= MAX_WAIT_S:
        raise ToolError(f"wait_s must be between 0 and {MAX_WAIT_S}")
    return value


def _run_bodies(args: dict[str, Any]) -> list[dict[str, Any]]:
    given = [key for key in ("test_cases", "gherkin", "description", "case_path") if args.get(key) not in (None, "", [])]
    if len(given) != 1:
        raise ToolError(f"give exactly one of test_cases, gherkin, description or case_path (got {', '.join(given) or 'none'})")
    _wait_arg(args, 0)
    common = {key: args[key] for key in _RUN_VALUES if args.get(key) not in (None, "", [])}
    common.update({key: True for key in _RUN_FLAGS if args.get(key) is True})
    if "fail_on" in common and not (common.get("a11y") or common.get("visual")):
        raise ToolError("fail_on needs a11y or visual: it gates the findings of those audits")
    kind = given[0]
    value = args[kind]
    if isinstance(args.get("plan"), dict):
        if kind == "test_cases" and isinstance(value, list) and len(value) != 1 or kind == "gherkin" and isinstance(value, list) and len(value) != 1:
            raise ToolError("plan runs one case: give one test case (or one Gherkin scenario) with it")
        common["plan"] = args["plan"]
    if kind == "test_cases":
        cases = value if isinstance(value, list) else [value]
        return [{"test_case": case, **common} for case in cases]
    if kind == "gherkin":
        texts = value if isinstance(value, list) else [value]
        return [{"gherkin": text, **common} for text in texts]
    if kind == "description":
        return [{"description": value, "auto_approve": bool(args.get("auto_approve")), **common}]
    return [{"case_path": value, **common}]


def _evidence_entries(run_id: str, run_dir: Path, result: dict[str, Any] | None) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for name in ("run.json", "case.json"):
        if (run_dir / name).is_file():
            entries[f"hercules://runs/{run_id}/{name}"] = {"uri": f"hercules://runs/{run_id}/{name}", "file": str(run_dir / name), "mime_type": "application/json"}
    for step in (result or {}).get("steps", []):
        for uri in step.get("evidence", []):
            parsed = _URI.match(str(uri))
            if parsed is None or parsed.group(1) != run_id:
                continue
            entries[uri] = {"uri": uri, "file": str(run_dir / parsed.group(2)), "mime_type": _mime(parsed.group(2))}
    return list(entries.values())[:200]


def _mime(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith(".json"):
        return "application/json"
    if lowered.endswith(".jsonl"):
        return "application/x-ndjson"
    if lowered.endswith((".txt", ".log", ".md")):
        return "text/plain"
    return "application/octet-stream"


def _links(evidence: list[dict[str, Any]], failing: dict[str, Any] | None) -> list[types.ResourceLink]:
    wanted = set(failing.get("evidence", [])) if failing else set()
    chosen = [entry for entry in evidence if entry["uri"] in wanted or entry["uri"].endswith("/run.json")]
    return [
        types.ResourceLink(type="resource_link", uri=entry["uri"], name=entry["uri"].rsplit("/", 2)[-2] + "/" + entry["uri"].rsplit("/", 1)[-1], mimeType=entry["mime_type"])
        for entry in chosen[:MAX_LINKS]
    ]


def _compact_result(result: dict[str, Any] | None) -> tuple[dict[str, Any] | None, bool]:
    """The RunResult, with passed and skipped steps dropped when it would be too large for an agent."""
    if result is not None and result.get("quality"):
        result = {**result, "quality": _quality_brief(result["quality"])}  # the full findings: hercules_quality_report
    if result is not None and result.get("automation") is not None:
        result = {key: value for key, value in result.items() if key != "automation"}  # the code and spec: hercules_export_code
    if result is None or len(json.dumps(result)) <= MAX_RESULT_JSON:
        return result, False
    steps = [step for step in result.get("steps", []) if step.get("status") not in ("passed", "skipped")]
    return {**result, "steps": steps}, True


# --------------------------------------------------------------------------- quality


def _ranked_findings(quality: dict[str, Any]) -> list[dict[str, Any]]:
    """Distinct findings (by id) most severe first, each with its fixes (verified first) and the pages it was found on."""
    entries: dict[str, dict[str, Any]] = {}
    for page in quality.get("pages", []):
        fixes = page.get("fixes", [])
        for finding in page.get("findings", []):
            entry = entries.setdefault(finding["id"], {"finding": finding, "fixes": [], "found_on": [], "pages_raw": []})
            entry["found_on"].append({"step": page.get("step"), "source": page.get("source"), "url": page.get("url")})
            entry["pages_raw"].append(page)
            for fix in fixes:
                if fix.get("finding_ref") == finding["id"] and fix not in entry["fixes"]:
                    entry["fixes"].append(fix)
    for entry in entries.values():
        entry["fixes"].sort(key=lambda fix: not fix.get("verified"))
    return sorted(entries.values(), key=lambda entry: (IMPACTS.index(entry["finding"].get("impact", "moderate")), not any(fix.get("verified") for fix in entry["fixes"])))


def _page_brief(page: dict[str, Any]) -> dict[str, Any]:
    visual = page.get("visual") or {}
    return {
        "kind": page.get("kind"),
        "source": page.get("source"),
        "step": page.get("step"),
        "url": page.get("url"),
        "findings": len(page.get("findings", [])),
        "baseline": visual.get("baseline"),
        "baseline_created": visual.get("created"),
        "error": page.get("error"),
        "evidence": page.get("evidence", []),
    }


def _quality_brief(quality: dict[str, Any]) -> dict[str, Any]:
    top = []
    for entry in _ranked_findings(quality)[:TOP_FIXES]:
        finding, fix = entry["finding"], (entry["fixes"] or [None])[0]
        top.append(
            {
                "id": finding["id"],
                "rule": finding["rule"],
                "impact": finding["impact"],
                "selector": finding["selector"],
                "message": finding["message"],
                "fix": None if fix is None else {key: fix.get(key) for key in ("property_or_attr", "current", "suggested", "snippet", "verified", "likely_file")},
            }
        )
    return {"counts": quality.get("counts"), "fail_on": quality.get("fail_on"), "pages": [_page_brief(page) for page in quality.get("pages", [])], "top": top}


def _without_quality_lines(text: str) -> str:
    """The run summary without its own quality headline and fix lines: ``_quality_lines`` replaces them with a richer block."""
    return "\n".join(line for line in text.splitlines() if not line.startswith(("Quality: ", "Fix (")))


def _quality_lines(quality: dict[str, Any]) -> list[str]:
    counts = quality.get("counts") or {}
    by_impact = ", ".join(f"{counts.get(impact)} {impact}" for impact in IMPACTS if counts.get(impact))
    gate = f"; fail_on {quality['fail_on']}" if quality.get("fail_on") else ""
    lines = [
        f"Quality: {counts.get('findings', 0)} finding(s) on {counts.get('pages', 0)} page(s){f' ({by_impact})' if by_impact else ''}; {counts.get('verified_fixes', 0)}/{counts.get('fixes', 0)} fixes verified{gate}"
    ]
    for number, entry in enumerate(_ranked_findings(quality)[:TOP_FIXES], start=1):
        lines.append(_finding_line(number, {**entry["finding"], "fixes": entry["fixes"]}))
    if counts.get("findings"):
        lines.append("All findings with fixes: hercules_quality_report.")
    return lines


def _finding_line(number: int, item: dict[str, Any]) -> str:
    wcag = f" WCAG {item['wcag']}" if item.get("wcag") else ""
    line = f"{number}. [{item['impact']}] {item['rule']}{wcag} on {item['selector']}: {str(item.get('message', ''))[:200]}"
    fix = (item.get("fixes") or [None])[0]
    if fix is not None:
        state = "verified in the page" if fix.get("verified") else "NOT verified"
        line += f"\n   Fix ({state}): {fix.get('snippet') or (str(fix.get('property_or_attr')) + ': ' + str(fix.get('suggested')))}"
        if fix.get("likely_file"):
            line += f" in {fix['likely_file']}"
    return line


def _visual_line(item: dict[str, Any]) -> str:
    if item.get("created"):
        return f"Visual: baseline {item['baseline']} created at step {item['step']} (compared from the next run)."
    verdict = (item.get("classification") or {}).get("label") or item.get("note") or "not classified"
    deltas = ", ".join(f"{d['property']} {d['before']} -> {d['after']}" for d in item.get("style_deltas", [])[:4])
    return f"Visual: baseline {item['baseline']} at step {item['step']}: {item.get('diff_ratio', 0):.2%} changed (gate {item.get('threshold', 0):.2%}); {verdict}{'; ' + deltas if deltas else ''}"


_CASES_PREFIX = ".hercules/cases/"
_CASE_HINTS = {code: "to change that case, use hercules_update_case with its path" for code in ("case_id_taken", "case_exists")}


def _case_name(raw: Any) -> str:
    """A case's name under .hercules/cases from what hercules_list_cases shows (the project path or the path inside the folder)."""
    name = str(raw or "").strip().replace("\\", "/")
    name = name.removeprefix("./").removeprefix(_CASES_PREFIX)
    parts = name.split("/")
    if not name or name.startswith("/") or any(part in ("", ".", "..") for part in parts) or not name.lower().endswith((".feature", ".json")):
        raise ToolError("give the case's path as hercules_list_cases shows it, e.g. .hercules/cases/Checkout/pay.feature")
    return name


_ENV_KEYS = ("base_url", "browser", "viewport", "device", "browser_options", "mobile", "variables", "secret_names")
_ENV_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _env_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not _ENV_NAME.match(name):
        raise ToolError("give the environment's name as hercules_list_environments shows it: lowercase letters, digits, - and _")
    return name


def _env_brief(env: dict[str, Any]) -> dict[str, Any]:
    mobile = env.get("mobile") or None
    return {
        "name": env["name"],
        "kind": "app" if mobile else "web",
        "base_url": env.get("base_url"),
        "browser": env.get("browser"),
        "device": env.get("device") or (mobile or {}).get("device"),
        "platform": (mobile or {}).get("platform"),
        "variables": sorted(env.get("variables") or {}),
        "secret_names": list(env.get("secret_names") or []),
    }


def _folder_name(raw: Any, *, top_level: bool = False) -> str:
    """A folder under .hercules/cases from what hercules_list_folders shows; empty (the top level) only when ``top_level``."""
    name = str(raw or "").strip().replace("\\", "/").removeprefix("./")
    name = name.removeprefix(_CASES_PREFIX.rstrip("/")).strip("/")
    if not name and not top_level:
        raise ToolError("give the folder as hercules_list_folders shows it, e.g. Checkout/Payments")
    if name and any(part in ("", ".", "..") for part in name.split("/")):
        raise ToolError("give the folder as hercules_list_folders shows it, e.g. Checkout/Payments")
    return name


def _inside(case_folder: str, folder: str) -> bool:
    return case_folder == folder or case_folder.startswith(folder + "/")


def _ready(case: dict[str, Any]) -> bool:
    """Whether a folder run would run this case: it loads and is not a draft."""
    return bool(case.get("valid")) and case.get("status") != "draft"


def _quote_case(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="/")
