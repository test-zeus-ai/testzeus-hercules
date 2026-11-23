"""The explore tools' MCP definitions and the server instructions for them (handlers: ``explore_tools``).

Kept apart from the handlers so ``specs`` can list every tool without importing them; nothing here imports the daemon.
"""

from __future__ import annotations

from typing import Any

__all__ = ["EXPLORE_INSTRUCTIONS", "EXPLORE_TOOLS", "MAX_WAIT_S"]

MAX_WAIT_S = 50  # the same per-call wait as the run tools (specs.MAX_WAIT_S; specs imports this module)

MISSIONS = ["p1", "forms", "changed", "smoke-plus", "a11y", "conditions"]
PERSONAS = ["impatient", "careful", "confused", "screen-reader"]
SEVERITIES = ["P0", "P1", "P2", "P3"]
LEVERS = ["inputs", "order", "conditions", "time", "people"]
STATES = ["new", "confirmed", "duplicate", "by_design", "wont_fix", "cannot_reproduce", "known_issue", "in_tracker", "verify_fixed", "closed"]
CASES = ["proposed", "kept", "regression", "rejected"]
TRACKERS = ["github", "jira", "linear", "markdown"]
_HOST = r"^[A-Za-z0-9.-]+(:\d{1,5})?$"
_SESSION_ID = {"type": "string", "pattern": r"^x_[A-Za-z0-9_-]{1,64}$", "description": "Session id returned by hercules_explore"}
_FINDING_ID = {"type": "string", "pattern": r"^f[0-9a-f]{3,10}$", "description": "A finding id (f + hex); an unambiguous prefix of 4 or more characters works"}
_WAIT = {"type": "number", "minimum": 0, "maximum": MAX_WAIT_S}
_NEXT = {"type": "string", "description": "What to do next"}

EXPLORE_INSTRUCTIONS = """\
Exploratory testing: when the user asks to "explore", "bug-hunt", "find bugs", "what's broken" or "QA" an app without
giving test steps, call hercules_explore (base_url, or a saved environment or start case; a mission such as p1 or
forms; time_s). A known flow with known steps is a test run (hercules_run), not an exploration. Explore only test
hosts: explorers fill and submit forms and change data. Never explore a host that looks like production unless the user
explicitly said it is theirs (then pass i_own_this_environment). Use the swarm size (workers, reproducers) the user asked
for, else the defaults; never pick a bigger one yourself, and get the user's OK on the estimate before a session over
30 minutes or over 2 explorers. Poll hercules_explore_status (wait_s at most 50) until finished, read the
hercules_explore_report summary, list hercules_explore_findings and show each finding to the user: id, severity (proposed
or confirmed), title, expected vs actual, ui_url. No findings is not a pass: report the coverage gaps. Triage and case
decisions (hercules_explore_triage) only on the user's explicit decision, with their name and their words, never on
your own judgement. After a fix, run hercules_explore_reverify."""

_FINDING_BRIEF: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "severity_label": {"type": "string", "description": "e.g. 'P1 (proposed)' or 'P0 (confirmed)'"},
        "proposed_severity": {"type": "string"},
        "severity": {"type": ["string", "null"]},
        "state": {"type": "string"},
        "case": {"type": "string"},
        "expected": {"type": "string"},
        "actual": {"type": "string"},
        "ui_url": {"type": "string"},
    },
}

_SESSION_SUMMARY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "status": {"type": "string"},
        "finished": {"type": "boolean"},
        "phase": {"type": "string"},
        "workers": {"type": "integer"},
        "reproducers": {"type": "integer"},
        "elapsed_s": {"type": "number"},
        "time_s": {"type": ["integer", "null"]},
        "clues": {"type": "integer"},
        "findings": {"type": "array", "items": _FINDING_BRIEF, "description": "At most 20; findings_total has the count"},
        "findings_total": {"type": "integer"},
        "charters": {"type": "object", "description": "Charter counts by state"},
        "recent": {"type": "array", "items": {"type": "string"}, "description": "The latest session events"},
        "message": {"type": "string"},
        "exit_code": {"type": ["integer", "null"]},
        "ui_url": {"type": "string"},
        "next": _NEXT,
    },
    "required": ["session_id", "status", "finished", "next"],
}

_MISSION = {
    "type": "string",
    "maxLength": 60,
    "anyOf": [
        {"enum": MISSIONS},
        {"pattern": f"^persona:({'|'.join(PERSONAS)})$"},
        {"pattern": r"^[a-z0-9][a-z0-9_-]{0,40}(:[a-z-]{1,30})?$", "description": "a mission defined in the project"},
    ],
    "description": f"One of {', '.join(MISSIONS)} (default p1: bugs at P1 or worse; changed needs changed_paths), persona:<{'|'.join(PERSONAS)}>, or a project mission",
}

EXPLORE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "hercules_explore",
        "title": "Explore an app for bugs",
        "description": (
            "Start a time-boxed exploratory testing session: explorers try unusual inputs, out-of-order moves and bad "
            "conditions on the app, and every clue is reproduced before it is reported. Returns the session id at once; "
            "poll hercules_explore_status. Give one of base_url, environment or start_case. Explorers fill and submit forms "
            "(never sign-out, delete, pay or similar unless allowed): test environments only. Show the user the estimate "
            "and get their OK before a session over 30 minutes or over 2 explorers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "pattern": r"^https?://[^\s/?#]+", "description": "The app to explore"},
                "environment": {"type": "string", "description": "A saved environment's name"},
                "start_case": {"type": "string", "description": "A case (path in the project) that reaches the starting state, e.g. signs in"},
                "start_url": {"type": "string", "description": "Where explorers begin (default: where the start case ends, else base_url)"},
                "mission": _MISSION,
                "prompt": {"type": "string", "maxLength": 4000, "description": "Replaces the mission's target text, e.g. 'find ways the cart total can be wrong'"},
                "time_s": {"type": "integer", "minimum": 60, "maximum": 86400, "description": "Time box in seconds (default 1800)"},
                "workers": {"type": "integer", "minimum": 1, "maximum": 64, "description": "Explorers at once: only what the user asked for (default 2)"},
                "reproducers": {"type": "integer", "minimum": 1, "maximum": 16, "description": "Reproducers at once: only what the user asked for (default 1)"},
                "max_cost_usd": {"type": "number", "minimum": 0, "description": "Stop when the model cost reaches this"},
                "target_findings": {"type": "integer", "minimum": 1, "description": "Stop once this many findings at or above the severity floor are confirmed"},
                "severity_floor": {"type": "string", "enum": SEVERITIES, "description": "Report findings at this severity or worse (default P2)"},
                "levers": {"type": "array", "items": {"type": "string", "enum": LEVERS}, "minItems": 1, "uniqueItems": True, "description": "What explorers push on (default all)"},
                "changed_paths": {"type": "array", "items": {"type": "string"}, "description": "For mission changed: routes, components or files that changed"},
                "deny_paths": {"type": "array", "items": {"type": "string", "pattern": "^/"}, "description": "Path prefixes never opened, e.g. /admin"},
                "allow_hosts": {"type": "array", "items": {"type": "string", "pattern": _HOST}, "description": "Other hosts explorers may reach (default: only the base URL's)"},
                "forbid": {"type": "array", "items": {"type": "string"}, "description": "Words of actions never done, added to the defaults"},
                "personas": {"type": "array", "items": {"type": "string", "pattern": "^[a-z-]{1,30}$"}, "description": "e.g. keyboard-only, impatient, first-time"},
                "teardown_case": {"type": "string", "description": "A case (path in the project) run once at the end to clean up test data"},
                "i_own_this_environment": {
                    "type": "boolean",
                    "description": "Only when the user explicitly said this host is theirs and safe to explore: lets a host that does not look like a test environment through",
                },
                "dry_run": {"type": "boolean", "description": "Recon and plan only: list the charters it would run"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {**_SESSION_SUMMARY["properties"], "estimate": {"type": "object"}},
            "required": ["session_id", "status", "finished", "next"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
    {
        "name": "hercules_explore_status",
        "title": "Exploration status",
        "description": "Status of an exploration session, long-polling up to wait_s seconds (at most 50, default 30): phase, charters, recent events, clues and up to 20 findings so far. Call again until finished is true, then hercules_explore_report.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _SESSION_ID, "wait_s": {**_WAIT, "description": "Wait up to this many seconds for news"}},
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "outputSchema": _SESSION_SUMMARY,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_stop",
        "title": "Stop an exploration",
        "description": "Stop a running exploration session; it reproduces what it has in a short grace period and writes its report. Poll hercules_explore_status until finished.",
        "inputSchema": {"type": "object", "properties": {"session_id": _SESSION_ID}, "required": ["session_id"], "additionalProperties": False},
        "outputSchema": _SESSION_SUMMARY,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_sessions",
        "title": "Exploration sessions",
        "description": "The project's exploration sessions, newest first: status, mission, target, findings count.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 10"}}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"sessions": {"type": "array", "items": {"type": "object"}}, "total": {"type": "integer"}, "next": _NEXT}, "required": ["sessions"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_report",
        "title": "Exploration report",
        "description": "A finished exploration session's report: by default a compact summary (findings, coverage gaps, charters not run); full true adds the markdown report. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _SESSION_ID, "full": {"type": "boolean", "description": "Also return the full markdown report (long)"}},
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "ready": {"type": "boolean"},
                "status": {"type": "string"},
                "findings": {"type": "array", "items": _FINDING_BRIEF},
                "findings_total": {"type": "integer"},
                "coverage": {"type": "object"},
                "report": {"type": "string"},
                "next": _NEXT,
            },
            "required": ["session_id", "ready"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_findings",
        "title": "Exploration findings",
        "description": "The project's findings ledger (every session), worst first: id, severity (proposed or confirmed), title, expected vs actual, triage state, case decision and a UI link each, plus the precision measure. Filter by session, state or case.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _SESSION_ID,
                "state": {"type": "string", "enum": STATES},
                "case": {"type": "string", "enum": CASES},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 20"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"findings": {"type": "array", "items": _FINDING_BRIEF}, "total": {"type": "integer"}, "precision": {"type": "object"}, "you": {"type": "string"}, "next": _NEXT},
            "required": ["findings", "total"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_finding",
        "title": "One exploration finding",
        "description": "One finding in full: steps, expected and actual, why this severity, flakiness, its runs, evidence as hercules://runs/... URIs (read with hercules_get_evidence) and the audit trail.",
        "inputSchema": {"type": "object", "properties": {"finding_id": _FINDING_ID}, "required": ["finding_id"], "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object"}}, "audit": {"type": "array", "items": {"type": "object"}}, "next": _NEXT},
            "required": ["id"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_triage",
        "title": "Triage a finding (user's decision)",
        "description": (
            "Record the USER's triage decision on a finding: state, severity, priority, case decision, duplicate or tracker key. "
            "Never call it without the user's explicit decision on this finding, and never on your own judgement. by is the "
            "user's name and user_said their own words deciding it; both go into the audit trail (as '<name> via coding agent'). "
            "A move the workflow does not allow is refused with the reason."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "finding_id": _FINDING_ID,
                "by": {"type": "string", "minLength": 1, "maxLength": 100, "description": "The user's name (who decided)"},
                "user_said": {"type": "string", "minLength": 2, "maxLength": 500, "description": "The user's own words authorising this change, quoted"},
                "state": {"type": "string", "enum": STATES},
                "severity": {"type": "string", "enum": SEVERITIES, "description": "The severity the user confirms"},
                "priority": {"type": "string", "enum": SEVERITIES},
                "case": {"type": "string", "enum": CASES, "description": "kept (a known_bug case in the suite), regression (must pass), rejected (never run), proposed"},
                "duplicate_of": {"type": "string", "pattern": r"^f[0-9a-f]{3,10}$", "description": "With state duplicate: the finding it duplicates"},
                "tracker": {"type": "string", "maxLength": 200, "description": "The issue key in the user's tracker (moves it to in_tracker)"},
                "reason": {"type": "string", "maxLength": 400, "description": "Why, in the user's terms (needed to dismiss or reject a case)"},
            },
            "required": ["finding_id", "by", "user_said"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"finding": _FINDING_BRIEF, "audit": {"type": "array", "items": {"type": "object"}}, "next": _NEXT}, "required": ["finding"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_issue",
        "title": "A finding as an issue",
        "description": "A finding written as an issue for GitHub, Jira, Linear or markdown (with a prefilled GitHub new-issue URL given repository). Show the text to the user; file it only when they ask.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "finding_id": _FINDING_ID,
                "tracker": {"type": "string", "enum": TRACKERS, "description": "Default markdown"},
                "repository": {"type": "string", "pattern": r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", "description": "github: owner/name, for a prefilled new-issue URL"},
            },
            "required": ["finding_id"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "url": {"type": "string"}, "next": _NEXT}, "required": ["title", "body"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_explore_reverify",
        "title": "Reverify kept cases",
        "description": (
            "After a fix: rerun the kept and regression cases of explore findings. A kept case that passes moves its finding to "
            "verify_fixed; a regression case that fails reopens its finding (regressions > 0); a run that errs, waits for approval or runs out of time is inconclusive and changes nothing. Starts a job and waits up to "
            "wait_s; call again with job_id until finished."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "build": {"type": "string", "maxLength": 200, "description": "The build being verified (recorded with the result; default current)"},
                "finding_ids": {"type": "array", "items": _FINDING_ID, "maxItems": 50, "description": "Only these findings (default: every kept and regression case)"},
                "job_id": {"type": "string", "pattern": r"^rv_[0-9a-f]{8}$", "description": "Follow a job this tool started"},
                "wait_s": {**_WAIT, "description": "Wait up to this many seconds (default 40)"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "status": {"type": "string"},
                "finished": {"type": "boolean"},
                "results": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Per finding: outcome regression, verify_fixed, still_failing, still_passes, inconclusive or skipped, with its run",
                },
                "lines": {"type": "array", "items": {"type": "string"}},
                "errors": {"type": "array", "items": {"type": "string"}},
                "regressions": {"type": "integer"},
                "inconclusive": {"type": "integer"},
                "exit_code": {"type": ["integer", "null"]},
                "next": _NEXT,
            },
            "required": ["job_id", "finished"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    },
]
