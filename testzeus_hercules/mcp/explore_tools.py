"""MCP tools for exploratory testing: start and follow a time-boxed session, read its report and the project's findings,
triage on the user's word, rerun the kept cases.

A session is long (minutes to hours), so it is never awaited in one call: ``hercules_explore`` returns the session
id at once, ``hercules_explore_status`` long-polls (at most 50 s per call) and ``hercules_explore_report`` returns the
report once the session ended. The swarm size is the user's: the agent passes what the user asked for (or the
defaults) and Hercules never changes it; a size this machine cannot hold is refused with the numbers, not cut.

Triage stays the user's decision: ``hercules_explore_triage`` needs the user's name and their own words authorising
the change, and records it as ``"<name> via coding agent"`` with those words in the audit trail.
"""

from __future__ import annotations

import re
from typing import Any

from testzeus_hercules.mcp.explore_specs import EXPLORE_INSTRUCTIONS, EXPLORE_TOOLS, MAX_WAIT_S

__all__ = ["EXPLORE_INSTRUCTIONS", "EXPLORE_TOOLS", "ExploreToolsMixin"]

_SESSION = re.compile(r"^x_[A-Za-z0-9_-]{1,64}$")
_FINDING = re.compile(r"^f[0-9a-f]{3,10}$")
_JOB = re.compile(r"^rv_[0-9a-f]{8}$")
_DEFAULT_WAIT_S = 30.0
_FINISHED = ("finished", "failed", "stopped")
_REPORT_CLIP = 20_000
_STATUS_FINDINGS = 20
_RECENT_EVENTS = 8
_AGENT = "a coding agent"
_SUFFIX = " via coding agent"
_TRIAGE_HINT = "Show each finding to the user (id, severity and whether it is proposed or confirmed, title, expected vs actual, ui_url) and ask what they decide; triage only on their explicit word."
_SPEC_VALUES = (
    "base_url",
    "environment",
    "start_case",
    "start_url",
    "mission",
    "prompt",
    "workers",
    "reproducers",
    "severity_floor",
    "changed_paths",
    "personas",
    "levers",
    "teardown_case",
    "dry_run",
)


def explore_spec(args: dict[str, Any]) -> dict[str, Any]:
    """The daemon's ExploreSpec body from the tool's arguments."""
    spec: dict[str, Any] = {key: args[key] for key in _SPEC_VALUES if args.get(key) not in (None, "", [])}
    budget: dict[str, Any] = {}
    if args.get("time_s") is not None:
        budget["time_s"] = int(args["time_s"])
    if args.get("max_cost_usd") is not None:
        budget["max_cost_usd"] = float(args["max_cost_usd"])
    if args.get("target_findings") is not None:
        budget["target_findings"] = int(args["target_findings"])
    if budget:
        spec["budget"] = budget
    scope = {key: args[key] for key in ("deny_paths", "forbid", "allow_hosts") if args.get(key)}
    if scope:
        spec["scope"] = scope
    if args.get("i_own_this_environment"):
        spec["production_ok"] = True
    return spec


def _severity_label(item: dict[str, Any]) -> str:
    return f"{item['severity']} (confirmed)" if item.get("severity") else f"{item.get('proposed_severity')} (proposed)"


def _finding_ui(ui_base: str | None, finding_id: str) -> str:
    return f"{ui_base}/explore?tab=findings&finding={finding_id}" if ui_base else "the Hercules UI (hercules ui), Explore, Findings"


def _finding_brief(item: dict[str, Any], ui_base: str | None = None) -> dict[str, Any]:
    keys = ("id", "title", "proposed_severity", "severity", "state", "case", "case_path", "expected", "actual", "regression")
    brief = {key: item.get(key) for key in keys if key in item}
    if "id" in item:
        brief["severity_label"] = _severity_label(item)
        brief["ui_url"] = _finding_ui(ui_base, str(item["id"]))
    return brief


def _finding_line(item: dict[str, Any]) -> str:
    line = f"- {item.get('id')} [{_severity_label(item)}] {item.get('title')} ({item.get('state', 'new')}, case {item.get('case', 'proposed')})"
    if item.get("expected") or item.get("actual"):
        line += f"\n  expected: {item.get('expected') or '?'} | actual: {item.get('actual') or '?'}"
    if item.get("ui_url"):
        line += f"\n  {item['ui_url']}"
    return line


def _event_text(event: dict[str, Any]) -> str:
    data = event.get("data") or {}
    kind = event.get("kind")
    if kind == "charter_started":
        return f"charter started: {data.get('title')}"
    if kind == "charter_finished":
        return f"charter done: {data.get('charter')} ({data.get('turns')} moves, {data.get('clues')} clue(s))"
    if kind == "clue":
        return f"clue ({data.get('tier')}): {data.get('title')}"
    if kind == "finding":
        return f"finding {data.get('severity')} (proposed, {data.get('outcome')}): {data.get('title')}"
    if kind == "size":
        return f"swarm size changed by {data.get('by')}"
    if kind == "finished":
        return f"session {data.get('status')}: {data.get('message') or ''}".strip()
    return str(kind or "")


def session_summary(view: dict[str, Any], ui_base: str | None, *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    charters: dict[str, int] = {}
    for charter in view.get("charters") or []:
        state = str(charter.get("status") or "queued")
        charters[state] = charters.get(state, 0) + 1
    findings = view.get("findings") or []
    finished = view["status"] in _FINISHED
    time_s = ((view.get("spec") or {}).get("budget") or {}).get("time_s")
    elapsed = round(float(view.get("elapsed_s") or 0), 1)
    if finished:
        hint = f"Call hercules_explore_report (session_id {view['id']}), then hercules_explore_findings with session_id. {_TRIAGE_HINT}"
    else:
        left = f"; about {max(0, int(time_s - elapsed))} s of the time box left" if time_s else ""
        hint = f"Still exploring{left}: call hercules_explore_status again with wait_s 50."
    return {
        "session_id": view["id"],
        "status": view["status"],
        "finished": finished,
        "phase": view.get("phase_detail") or "",
        "workers": view.get("workers"),
        "reproducers": view.get("reproducers"),
        "elapsed_s": elapsed,
        "time_s": time_s,
        "clues": view.get("clues", 0),
        "findings": [_finding_brief(item, ui_base) for item in findings[:_STATUS_FINDINGS]],
        "findings_total": len(findings),
        "charters": charters,
        "recent": [_event_text(event) for event in (events if events is not None else view.get("events") or [])][-_RECENT_EVENTS:],
        "message": view.get("message") or "",
        "exit_code": view.get("exit_code"),
        "ui_url": f"{ui_base}/explore/{view['id']}" if ui_base else "the Hercules UI (hercules ui)",
        "next": hint,
    }


def summary_text(data: dict[str, Any]) -> str:
    head = f"Exploration {data['session_id']}: {data['status']}"
    if data.get("phase"):
        head += f" ({data['phase']})"
    lines = [
        head,
        f"{data['elapsed_s']:.0f} s of {data.get('time_s') or '?'} s; {data['workers']} explorer(s), {data['reproducers']} reproducer(s); {data['clues']} clue(s), {data['findings_total']} finding(s).",
    ]
    lines += [_finding_line(item) for item in data["findings"][:10]]
    if data["findings_total"] > 10:
        lines.append(f"... and {data['findings_total'] - 10} more (hercules_explore_findings).")
    if data.get("recent"):
        lines.append("Recent: " + "; ".join(data["recent"]))
    if data.get("message"):
        lines.append(data["message"])
    lines.append(f"Watch it at {data['ui_url']}.")
    lines.append(data["next"])
    return "\n".join(lines)


def _check(pattern: re.Pattern[str], value: Any, message: str) -> str:
    from testzeus_hercules.mcp.tools import ToolError

    text = str(value or "")
    if not pattern.match(text):
        raise ToolError(message)
    return text


def _session_id(value: Any) -> str:
    return _check(_SESSION, value, "session_id is the x_... id hercules_explore returned")


def _finding_id(value: Any) -> str:
    return _check(_FINDING, value, "finding_id is a finding's id (f followed by hex digits; 4 or more characters of it are enough)")


def evidence_links(finding: dict[str, Any]) -> list[dict[str, Any]]:
    """The finding's evidence as ``hercules://runs/{run_id}/...`` URIs that hercules_get_evidence reads (paths it
    cannot map are listed with ``path`` only), plus each run's result."""
    from testzeus_hercules.mcp.tools import _URI

    run_ids = [str(run) for run in finding.get("run_ids") or []]
    links: list[dict[str, Any]] = []
    for raw in finding.get("evidence") or []:
        parts = [part for part in str(raw).replace("\\", "/").split("/") if part]
        run_id: str | None = None
        rest: list[str] = []
        if "runs" in parts and parts.index("runs") + 1 < len(parts):
            index = parts.index("runs")
            run_id, rest = parts[index + 1], parts[index + 2 :]
        elif parts and parts[0] in run_ids:
            run_id, rest = parts[0], parts[1:]
        uri = f"hercules://runs/{run_id}/{'/'.join(rest)}" if run_id and rest and ".." not in rest else None
        links.append({"uri": uri, "path": str(raw)} if uri and _URI.match(uri) else {"path": str(raw)})
    links += [{"uri": f"hercules://runs/{run_id}/run.json", "path": f"run {run_id} result"} for run_id in run_ids[-3:]]
    return links


class ExploreToolsMixin:
    """Handlers for the explore tools; mixed into ``HerculesTools`` (which provides ``_api`` and ``client``)."""

    client: Any

    async def _api(self, method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None, timeout_s: float | None = None) -> Any:  # pragma: no cover - provided by HerculesTools
        raise NotImplementedError

    def _ui_base(self) -> str | None:
        info = getattr(self.client, "info", None)
        return info.url if info is not None else None

    # ------------------------------------------------------------------ sessions

    async def _explore(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolError, ToolOutput

        spec = explore_spec(args)
        if not any(key in spec for key in ("base_url", "environment", "start_case")):
            raise ToolError("say where to explore: base_url, environment or start_case")
        check = await self._api("POST", "/api/explore/preflight", json_body=spec)
        if check.get("host_problem"):
            raise ToolError(str(check["host_problem"]))
        if check.get("problems"):
            problems = "; ".join(check["problems"])
            raise ToolError(
                f"this swarm size does not fit this machine: {problems}. Ask the user for a smaller size, or for them to start it in the UI or with `hercules explore start --allow-more-workers`."
            )
        view = await self._api("POST", "/api/explore", json_body={**spec, "by": _AGENT})
        data = session_summary(view, self._ui_base())
        estimate = check.get("estimate") or {}
        text = summary_text(data)
        if estimate:
            text += (
                f"\nEstimate: about {estimate.get('decisions')} model decisions"
                + (f", ${estimate.get('cost_usd')}" if estimate.get("cost_usd") else "")
                + (f" ({estimate['note']})" if estimate.get("note") else "")
                + "."
            )
        return ToolOutput({**data, "estimate": estimate}, text)

    async def _explore_status(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import CALL_BUDGET_S, ToolError, ToolOutput

        session_id = _session_id(args.get("session_id"))
        wait_s = float(args.get("wait_s", _DEFAULT_WAIT_S))
        if not 0 <= wait_s <= MAX_WAIT_S:
            raise ToolError(f"wait_s must be between 0 and {MAX_WAIT_S}")
        wait_s = max(0.0, min(wait_s, CALL_BUDGET_S - 5))
        first = await self._api("GET", f"/api/explore/{session_id}")
        view, events = first, list(first.get("events") or [])
        if wait_s and first["status"] not in _FINISHED:
            view = await self._api("GET", f"/api/explore/{session_id}", params={"wait_s": wait_s, "since": first.get("last_seq", 0)}, timeout_s=wait_s + 10)
            events += list(view.get("events") or [])
        data = session_summary(view, self._ui_base(), events=events)
        await progress(float(data["elapsed_s"]), float(data["time_s"]) if data.get("time_s") else None, f"{data['status']}: {data['clues']} clues, {data['findings_total']} findings")
        return ToolOutput(data, summary_text(data))

    async def _explore_stop(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolOutput

        session_id = _session_id(args.get("session_id"))
        await self._api("POST", f"/api/explore/{session_id}/stop", json_body={"by": _AGENT})
        data = session_summary(await self._api("GET", f"/api/explore/{session_id}"), self._ui_base())
        if not data["finished"]:
            data["next"] = "Stopping: it reports what it has. Call hercules_explore_status until finished is true, then hercules_explore_report."
        return ToolOutput(data, summary_text(data))

    async def _explore_sessions(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolOutput

        limit = max(1, min(int(args.get("limit", 10)), 50))
        listing = await self._api("GET", "/api/explore")
        ui_base = self._ui_base()
        sessions = []
        for view in (listing.get("sessions") or [])[:limit]:
            spec = view.get("spec") or {}
            sessions.append(
                {
                    "session_id": view.get("id"),
                    "status": view.get("status"),
                    "started_at": view.get("started_at"),
                    "mission": spec.get("mission"),
                    "base_url": spec.get("base_url") or (view.get("environment") or {}).get("base_url") or spec.get("environment") or spec.get("start_case"),
                    "findings": len(view.get("findings") or []),
                    "elapsed_s": view.get("elapsed_s"),
                    "ui_url": f"{ui_base}/explore/{view.get('id')}" if ui_base else "the Hercules UI (hercules ui)",
                }
            )
        hint = "Read one with hercules_explore_status or hercules_explore_report; the project's findings across sessions are in hercules_explore_findings."
        lines = [f"- {item['session_id']}: {item['status']}, {item['mission']} on {item['base_url']}, {item['findings']} finding(s), started {item['started_at']}" for item in sessions]
        return ToolOutput({"sessions": sessions, "total": len(listing.get("sessions") or []), "next": hint}, "\n".join([*lines, hint]) if lines else "No exploration sessions yet.")

    async def _explore_report(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolError, ToolOutput

        session_id = _session_id(args.get("session_id"))
        ui_base = self._ui_base()
        response = await self.client.request("GET", f"/api/explore/{session_id}/report", params={"format": "json"})
        if response.status_code == 404:
            try:
                view = await self._api("GET", f"/api/explore/{session_id}")
            except ToolError:
                raise ToolError(f"no exploration session {session_id}") from None
            data = session_summary(view, ui_base)
            out = {"session_id": session_id, "ready": False, "findings": data["findings"], "findings_total": data["findings_total"], "next": data["next"]}
            return ToolOutput(out, summary_text(data) + "\nThe report is written when the session ends.")
        if response.status_code >= 400:
            raise ToolError(response.text[:500])
        report = response.json()
        findings = report.get("findings") or []
        coverage = report.get("coverage") or {}
        out = {
            "session_id": session_id,
            "ready": True,
            "status": report.get("status"),
            "message": report.get("message") or "",
            "exit_code": report.get("exit_code"),
            "findings": [_finding_brief(item, ui_base) for item in findings[:_STATUS_FINDINGS]],
            "findings_total": len(findings),
            "not_reproduced": len(report.get("not_reproduced") or []),
            "coverage": {
                "routes_seen": len(coverage.get("routes_seen") or []),
                "states_seen": coverage.get("states_seen"),
                "moves_tried": coverage.get("moves_tried"),
                "gaps": (coverage.get("gaps") or [])[:10],
                "charters_not_run": (coverage.get("charters_not_run") or [])[:10],
            },
            "next": (_TRIAGE_HINT if findings else "No findings is not a pass: tell the user the coverage gaps and the charters not run.") + " Full markdown: call again with full true.",
        }
        if args.get("full"):
            markdown = await self.client.request("GET", f"/api/explore/{session_id}/report", params={"format": "md"})
            text = markdown.text if markdown.status_code == 200 else ""
            out["report"] = text[:_REPORT_CLIP] + ("\n[report clipped]" if len(text) > _REPORT_CLIP else "")
            return ToolOutput(out, out["report"] or f"{len(findings)} finding(s).")
        lines = [f"Exploration {session_id}: {out['status']} ({out['message'] or 'no message'}), {len(findings)} finding(s)."]
        lines += [_finding_line(item) for item in out["findings"]]
        if not findings:
            lines.append("No findings is not a pass: nothing reproducible was found in this time.")
        cov = out["coverage"]
        lines.append(f"Coverage: {cov['routes_seen']} route(s), {cov['states_seen']} state(s), {cov['moves_tried']} move(s) tried.")
        lines += [f"- gap: {gap.get('route')} without {', '.join(gap.get('levers_not_used') or [])}" for gap in cov["gaps"]]
        lines += [f"- not run: {title}" for title in cov["charters_not_run"]]
        lines.append(out["next"])
        return ToolOutput(out, "\n".join(lines))

    # ------------------------------------------------------------------ findings

    async def _explore_findings(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolOutput

        params = {key: str(args[key]) for key in ("state", "case") if args.get(key)}
        if args.get("session_id"):
            params["session"] = _session_id(args["session_id"])
        limit = max(1, min(int(args.get("limit", 20)), 50))
        data = await self._api("GET", "/api/explore/findings", params=params or None)
        ui_base = self._ui_base()
        found = data.get("findings") or []
        briefs = [_finding_brief(item, ui_base) for item in found[:limit]]
        hint = _TRIAGE_HINT if briefs else "No findings match."
        out = {"findings": briefs, "total": len(found), "precision": data.get("precision") or {}, "you": data.get("you") or "", "next": hint}
        lines = [f"{len(found)} finding(s)" + (f", showing {len(briefs)}" if len(briefs) < len(found) else "") + "."]
        lines += [_finding_line(item) for item in briefs]
        lines.append(hint)
        return ToolOutput(out, "\n".join(lines))

    async def _explore_finding(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolOutput

        item = await self._api("GET", f"/api/explore/findings/{_finding_id(args.get('finding_id'))}")
        keys = (
            "id",
            "title",
            "state",
            "case",
            "case_path",
            "severity",
            "proposed_severity",
            "severity_reason",
            "priority",
            "proposed_priority",
            "steps",
            "expected",
            "actual",
            "flaky",
            "confidence",
            "oracle",
            "category",
            "run_ids",
            "regression",
            "tracker",
            "duplicate_of",
            "sessions",
            "builds",
            "environment",
            "first_seen",
            "last_seen",
        )
        out = {key: item.get(key) for key in keys if key in item}
        out.update(severity_label=_severity_label(item), evidence=evidence_links(item), audit=(item.get("audit") or [])[-20:], ui_url=_finding_ui(self._ui_base(), item["id"]))
        out["next"] = "Show it to the user and ask what they decide (confirm, a severity, dismiss with a reason, keep or reject its case). Read evidence with hercules_get_evidence."
        lines = [f"{item['id']} [{out['severity_label']}] {item.get('title')}", f"state {item.get('state')}, case {item.get('case')}, flaky {item.get('flaky')}"]
        if item.get("severity_reason"):
            lines.append(f"Why this severity: {item['severity_reason']}")
        lines += [f"{index}. {step}" for index, step in enumerate(item.get("steps") or [], 1)]
        lines += [f"Expected: {item.get('expected') or '?'}", f"Actual: {item.get('actual') or '?'}"]
        lines += [f"Evidence: {link.get('uri') or link['path']}" for link in out["evidence"]]
        lines += [f"Audit: {event.get('at')} {event.get('by')} {event.get('field')} {event.get('before')} -> {event.get('after')} {event.get('reason') or ''}".rstrip() for event in out["audit"][-5:]]
        lines += [f"UI: {out['ui_url']}", out["next"]]
        return ToolOutput(out, "\n".join(lines))

    async def _explore_triage(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolError, ToolOutput

        finding_id = _finding_id(args.get("finding_id"))
        by = str(args.get("by") or "").strip()
        user_said = " ".join(str(args.get("user_said") or "").split())
        if not by:
            raise ToolError("by is the user's name: ask them who is deciding")
        if len(user_said) < 2:
            raise ToolError("user_said is the user's own words deciding this change; without the user's explicit decision, do not triage")
        changes = {key: args[key] for key in ("state", "severity", "priority", "case", "duplicate_of", "tracker") if args.get(key) not in (None, "")}
        if not changes:
            raise ToolError("nothing to change: give state, severity, priority, case, duplicate_of or tracker")
        reason = str(args.get("reason") or "").strip()
        audit_reason = (f"{reason} " if reason else "") + f'(the user said: "{user_said}")'
        body = {**changes, "by": f"{by[:120 - len(_SUFFIX)]}{_SUFFIX}", "reason": audit_reason[:1000]}
        finding = await self._api("POST", f"/api/explore/findings/{finding_id}/triage", json_body=body)
        brief = _finding_brief(finding, self._ui_base())
        last = [event for event in finding.get("audit") or [] if event.get("by") == body["by"]][-6:]
        out = {"finding": brief, "audit": last, "next": "Tell the user what changed. After a fix ships, run hercules_explore_reverify."}
        text = f"{finding['id']}: now {finding.get('state')}, {brief['severity_label']}, case {finding.get('case')}. Recorded as {body['by']}.\n{out['next']}"
        return ToolOutput(out, text)

    async def _explore_issue(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import ToolOutput

        finding_id = _finding_id(args.get("finding_id"))
        params = {"tracker": str(args.get("tracker") or "markdown")}
        if args.get("repository"):
            params["repository"] = str(args["repository"])
        data = await self._api("GET", f"/api/explore/findings/{finding_id}/issue", params=params)
        hint = "Show this issue text to the user. File it only if they ask; then record the tracker key with hercules_explore_triage, on their word."
        out = {**data, "next": hint}
        text = f"{data['title']}\n\n{data['body']}" + (f"\n\nPrefilled: {data['url']}" if data.get("url") else "") + f"\n\n{hint}"
        return ToolOutput(out, text)

    async def _explore_reverify(self, args: dict[str, Any], progress: Any, started: float) -> Any:
        from testzeus_hercules.mcp.tools import CALL_BUDGET_S, ToolError, ToolOutput

        wait_s = float(args.get("wait_s", 40))
        if not 0 <= wait_s <= MAX_WAIT_S:
            raise ToolError(f"wait_s must be between 0 and {MAX_WAIT_S}")
        wait_s = max(0.0, min(wait_s, CALL_BUDGET_S - 5))
        if args.get("job_id"):
            job = await self._api("GET", f"/api/explore/reverify/{_check(_JOB, args['job_id'], 'job_id is the rv_... id hercules_explore_reverify returned')}")
        else:
            body: dict[str, Any] = {"build": str(args.get("build") or "current")}
            if args.get("finding_ids"):
                body["findings"] = [_finding_id(value) for value in args["finding_ids"]]
            job = await self._api("POST", "/api/explore/reverify", json_body=body)
        if wait_s and not job.get("finished"):
            job = await self._api("GET", f"/api/explore/reverify/{job['id']}", params={"wait_s": wait_s, "since": len(job.get("lines") or [])}, timeout_s=wait_s + 10)
        finished = bool(job.get("finished"))
        if not finished:
            hint = f"Still running: call hercules_explore_reverify again with job_id {job['id']}."
        elif job.get("regressions"):
            hint = "A regression case failed: its finding is reopened. Tell the user; the fix did not hold or the bug came back."
        elif job.get("inconclusive"):
            hint = "Some runs were inconclusive (an error, an approval, cancelled or out of time): nothing changed for those findings. Tell the user and show the runs."
        else:
            hint = "Done. Findings whose case passed are verify_fixed; closing them is the user's decision (hercules_explore_triage on their word)."
        out = {
            "job_id": job["id"],
            "status": job.get("status"),
            "finished": finished,
            "results": [{key: item.get(key) for key in ("finding", "title", "case", "outcome", "run_id", "run_status")} for item in job.get("results") or []],
            "lines": job.get("lines") or [],
            "errors": job.get("errors") or [],
            "regressions": job.get("regressions", 0),
            "inconclusive": job.get("inconclusive", 0),
            "exit_code": job.get("exit_code"),
            "next": hint,
        }
        return ToolOutput(out, "\n".join([*out["lines"], *out["errors"], hint]))
