"""Tool definitions (names, descriptions, input and output schemas) and the server instructions."""

from __future__ import annotations

from typing import Any

MAX_WAIT_S = 50
RUN_STATUSES = ["queued", "running", "paused", "needs_approval", "needs_plan_approval", "passed", "failed", "error", "cancelled"]

INSTRUCTIONS = """\
Hercules runs end-to-end tests of a web app in a real browser (or a native app on a device) on this machine and
reports what failed, with a screenshot of the failing step.

Use Hercules for every end-to-end, UI, browser, QA or acceptance test the user asks for ("test this", "check the
login works", "reproduce this bug", "QA the checkout", "does the page still work?"): write and run the case through
these tools rather than writing a throwaway browser script, driving a browser with other tools, or only reading the
code. Each run is recorded with its evidence and shown live in the tester UI (hercules ui), where the user can watch,
pause and replay it. Unit tests the repository already has still run with its own test command.

Companion flow after you change user-facing behaviour:
1. Write a TestCase for the change (save it in .hercules/cases/<id>.json): id, title,
   environment.base_url, and steps such as {"action": "Click the \\"Sign in\\" button",
   "expected": "The text \\"Welcome\\" is shown"}. Gherkin (one scenario) also works.
   For a phone-sized browser add environment.device (a Playwright profile such as "iPhone 15");
   for a native Android/iOS app use environment.mobile ({"platform": "android", "package": ...,
   "activity": ...}) instead of base_url; see docs/guide/mobile-testing.md.
   Saved cases: hercules_list_cases, hercules_get_case, hercules_validate_case, hercules_create_case and
   hercules_update_case (both validate first), hercules_delete_case (only when the user asks).
   Folders of cases: hercules_list_folders, hercules_get_folder, hercules_create_folder, hercules_update_folder
   (rename or move), hercules_delete_folder (only when the user asks), and hercules_run_folder to run every
   ready case in a folder as one batch (follow it with hercules_list_runs batch=<batch>).
   Environments (where cases run: base URL, browser, device or native app, and {{variables}}):
   hercules_list_environments, hercules_get_environment, hercules_create_environment,
   hercules_update_environment, hercules_delete_environment (only when the user asks). A case names one
   with environment.ref (Gherkin: @env:<name>); hercules_run and hercules_run_folder take environment.
   Secret values never go through these tools: an environment lists secret_names, and the user saves
   the values in the tester UI or sets them as environment variables.
2. hercules_run with test_cases (or gherkin) and base_url: returns run_id at once.
3. hercules_run_status with run_id and wait_s (at most 50) until finished is true.
4. hercules_run_result: status, the failing step (expected vs actual), hints, one screenshot,
   and links to other evidence (read them with hercules_get_evidence).
5. Fix the code (or the case if it was wrong), then hercules_rerun with scope "failed_only".
A plain-language description becomes a draft (needs_approval): check the steps, then hercules_approve.
Plans: before the browser starts, the case is turned into a plan (each step's actions, success criteria marked
author or inferred, risks) that is checked for coverage and linted. hercules_plan returns the plan and its lint
without running; fix the case on lint errors. A run with plan_review "ask" (the default for drafts) waits in
needs_plan_approval: read the plan with hercules_plan (run_id), then hercules_approve (approve, reject, or edit
with plan: the edited plan). hercules_run also takes plan (an edited plan) and strict (lint errors stop the run).
Accessibility and visual checks: a step with a "quality" payload ({"kind": "a11y"}, {"kind": "visual", "baseline":
"checkout"}, {"kind": "layout"}) or a plain step such as "the page should have no accessibility violations"; or
hercules_run with a11y / visual true to audit every page. hercules_quality_report lists the findings with fix values
that were applied in the page and re-checked (verified true/false) and the CSS/HTML snippet to apply.
A running run can also stop in needs_approval (a destructive click, or a step it could not do): the status
shows the reason and a screenshot; answer with hercules_approve (approve, reject, or edit with the step's new
text). A run paused by a tester in the UI shows status paused; keep polling.
To keep a passed run as a test in the repository, call hercules_export_code (format playwright-ts, playwright-py,
appium-py for a native app run, or spec): it returns a reference script with locators that were unique on the page, the environment variables it reads
and what to review; write the final test in the repository's own conventions and run it.
If a run errors before its first step, hercules_check_env shows whether the model server is ready.
Every call returns within about a minute; long runs are polled, never awaited in one call."""

_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_INT = {"type": ["integer", "null"]}
_RUN_ID = {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,128}$", "description": "Run id returned by hercules_run"}
_WAIT = {"type": "number", "minimum": 0, "maximum": MAX_WAIT_S}

RUN_SUMMARY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string"},
        "status": {"type": "string", "enum": RUN_STATUSES},
        "finished": {"type": "boolean"},
        "case_id": _NULLABLE_STRING,
        "title": _NULLABLE_STRING,
        "total_steps": _NULLABLE_INT,
        "steps_done": {"type": "integer"},
        "current_step": _NULLABLE_INT,
        "current_action": _NULLABLE_STRING,
        "reason": _NULLABLE_STRING,
        "parent_run_id": _NULLABLE_STRING,
        "ui_url": {"type": "string"},
        "summary": {"type": "string"},
        "draft_steps": {"type": ["array", "null"], "items": {"type": "string"}},
        "approval": {
            "type": ["object", "null"],
            "description": "The question a running run waits on: kind (destructive, recovery_exhausted, draft), step, reason, screenshot",
        },
        "paused": {"type": ["object", "null"], "description": "Where a tester paused the run"},
    },
    "required": ["run_id", "status", "finished"],
}
_RUNS_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {"runs": {"type": "array", "items": RUN_SUMMARY}, "all_finished": {"type": "boolean"}},
    "required": ["runs", "all_finished"],
}
_STATUS_OUTPUT = {**RUN_SUMMARY, "properties": {**RUN_SUMMARY["properties"], "last_seq": {"type": "integer"}}}
_EVIDENCE_ITEM = {"type": "object", "properties": {"uri": {"type": "string"}, "file": {"type": "string"}, "mime_type": {"type": "string"}}, "required": ["uri"]}
_RUN_OPTIONS = {
    "environment": {"type": "string", "description": "Name of a saved environment (.hercules/envs/<name>.json)"},
    "base_url": {"type": "string", "description": "Base URL of the app under test, e.g. http://127.0.0.1:3000 (overrides the case)"},
    "mode": {
        "type": "string",
        "enum": ["auto", "pure", "hybrid"],
        "description": "pure: the local model does everything; hybrid: a hosted decision model decides (needs its key and egress); auto picks",
    },
    "egress": {"type": "string", "enum": ["local", "hybrid-text", "hybrid"], "description": "What may leave the machine for this run (default: the project's policy)"},
    "a11y": {"type": "boolean", "description": "Audit every page the run reaches for accessibility (WCAG 2.2 AA) with verified fix values"},
    "visual": {"type": "boolean", "description": "Compare every page the run reaches with its visual baseline (created on the first run) and check the layout at 375/768/1280 px"},
    "fail_on": {"type": "string", "enum": ["critical", "serious", "moderate", "minor"], "description": "With a11y/visual: findings at or above this impact fail the run (default: report only)"},
    "confirm_destructive": {"type": "boolean", "description": "Stop for approval before clicking elements whose names suggest deleting, paying or transferring"},
    "destructive_terms": {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 60, "pattern": "^[^,\\n]+$"},
        "maxItems": 50,
        "description": "Words that mark a destructive click (default: a built-in list)",
    },
    "ask_on_failure": {"type": "boolean", "description": "Stop for approval (retry, edit or reject) when a step's recovery is exhausted"},
    "approval_timeout_s": {"type": "number", "exclusiveMinimum": 0, "maximum": 86400, "description": "How long a run waits for an approval before failing the step (default 600)"},
    "plan_review": {
        "type": "string",
        "enum": ["auto", "ask"],
        "description": "ask: the run waits in needs_plan_approval until its plan is approved (hercules_approve); auto runs the plan as made (default: ask for drafts, else auto)",
    },
    "strict": {"type": "boolean", "description": "Strict plan mode: plan lint errors stop the run before the browser starts, and inferred criteria still unmet after recovery fail their step"},
}

_FOLDER = {
    "type": "string",
    "minLength": 1,
    "maxLength": 1000,
    "description": "A folder as hercules_list_folders shows it, e.g. Checkout/Payments (or .hercules/cases/Checkout/Payments)",
}
_FOLDER_CASE = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "folder": {"type": "string"},
        "valid": {"type": "boolean"},
        "title": {"type": ["string", "null"]},
        "status": {"type": ["string", "null"]},
        "steps": {"type": ["integer", "null"]},
    },
    "required": ["path", "folder", "valid"],
}

_ENV_NAME = {
    "type": "string",
    "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$",
    "description": "The environment's name: lowercase letters, digits, - and _ (saved as .hercules/envs/<name>.json)",
}
_SECRET_NAME = {"type": "string", "pattern": "^[A-Z_][A-Z0-9_]*$"}
# Null clears a field in hercules_update_environment.
_ENV_FIELDS: dict[str, Any] = {
    "base_url": {"type": ["string", "null"], "maxLength": 2000, "description": "Where the app under test is, e.g. http://127.0.0.1:3000; it may use {{variables}}. A web environment needs it"},
    "browser": {"enum": ["chromium", "chrome", "msedge", "firefox", "webkit", None], "description": "Default chromium"},
    "viewport": {"type": ["string", "null"], "pattern": "^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$", "description": "Browser window size, e.g. 1280x720"},
    "device": {"type": ["string", "null"], "maxLength": 100, "description": "Phone or tablet emulation: a Playwright device profile, e.g. iPhone 15"},
    "browser_options": {"type": ["object", "null"], "description": "Playwright launch and context options (locale, timezone_id, color_scheme, headers, ...)"},
    "mobile": {
        "type": ["object", "null"],
        "description": "A native app instead of a website: platform (android or ios), and app (a file in the project) or package and activity (Android) or bundle_id (iOS); device, udid, platform_version, appium_url or cloud. See docs/guide/mobile-testing.md",
    },
    "variables": {
        "type": ["object", "null"],
        "additionalProperties": {"anyOf": [{"type": "string"}, {"type": "object", "properties": {"secret": _SECRET_NAME}, "required": ["secret"], "additionalProperties": False}]},
        "description": 'Values every case run here can use as {{name}} (a case\'s own test data wins); {"secret": "NAME"} reads the secret NAME at run time',
    },
    "secret_names": {
        "type": ["array", "null"],
        "items": _SECRET_NAME,
        "maxItems": 50,
        "description": "Secrets cases can use as {{NAME}}, by name only: the user saves their values in the tester UI or sets them as environment variables",
    },
}
_ENV_OUTPUT = {
    "type": "object",
    "properties": {
        "environment": {"type": "object"},
        "used_by": {"type": "array", "items": {"type": "string"}, "description": "Saved cases that run in this environment (one that does not load: its file names it)"},
        "used_by_truncated": {"type": "boolean", "description": "More cases name it than the listing holds"},
    },
    "required": ["environment", "used_by"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "hercules_run",
        "title": "Start a test run",
        "description": "Queue end-to-end test runs in a real browser and return their run_ids at once. Give exactly one of test_cases (TestCase JSON object or list), gherkin (one scenario per text), description (plain language, one step per line; becomes a draft needing hercules_approve unless auto_approve) or case_path (file in the project). Optional wait_s (at most 50) returns the results if they finish in time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "test_cases": {
                    "description": "TestCase object or a list of them (at most 20)",
                    "anyOf": [{"type": "object"}, {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 20}],
                },
                "gherkin": {
                    "description": "Gherkin text with one scenario, or a list of such texts",
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}],
                },
                "description": {"type": "string", "description": "Plain-language steps, one per line"},
                "case_path": {"type": "string", "description": "Path of a .json or .feature case inside the project"},
                **_RUN_OPTIONS,
                "auto_approve": {"type": "boolean", "description": "Run a drafted description without waiting for approval"},
                "plan": {"type": "object", "description": "A plan to run (from hercules_plan, possibly edited: step text, criteria, order); it must be a plan of the one case given"},
                "wait_s": {**_WAIT, "description": "Wait up to this many seconds for the runs to finish (default 0)"},
            },
            "additionalProperties": False,
        },
        "outputSchema": _RUNS_OUTPUT,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_run_status",
        "title": "Wait for a run",
        "description": "Status of a run, long-polling up to wait_s seconds (at most 50, default 20) until it finishes; sends progress notifications per step while waiting. Call again until finished is true, then call hercules_run_result.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": _RUN_ID, "wait_s": {**_WAIT, "description": "Seconds to wait for the run to finish (0-50, default 20)"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": _STATUS_OUTPUT,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_run_result",
        "title": "Read a run's result",
        "description": "The RunResult of a finished run: status, the failing step with expected and actual outcome, hints, and the failing step's screenshot inline (downscaled JPEG); other evidence as hercules:// resource links and local file paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": _RUN_ID,
                "image": {"type": "string", "enum": ["plain", "som", "none"], "description": "Screenshot to inline for the failing step: plain (default), som (with element marks) or none"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "status": {"type": "string", "enum": RUN_STATUSES},
                "finished": {"type": "boolean"},
                "summary": {"type": "string"},
                "reason": _NULLABLE_STRING,
                "failing_step": {"type": ["object", "null"]},
                "hints": {"type": "array", "items": {"type": "string"}},
                "result": {"type": ["object", "null"]},
                "steps_trimmed": {"type": "boolean"},
                "quality": {"type": ["object", "null"], "description": "Quality counts by impact, audited pages and the top fixes; full detail with hercules_quality_report"},
                "evidence": {"type": "array", "items": _EVIDENCE_ITEM},
                "run_dir": {"type": "string"},
                "ui_url": {"type": "string"},
            },
            "required": ["run_id", "status", "finished", "summary", "evidence"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_quality_report",
        "title": "Read a run's accessibility and visual findings",
        "description": "Findings of a finished run's quality steps and a11y/visual audits, most severe first: rule, WCAG criterion, element and selector, measured values, and the fix (property, current and suggested value, a CSS/HTML snippet, verified: whether applying it in the page resolved the finding, likely_file). Visual comparisons with style deltas and the decider's classification. The worst finding's annotated screenshot (or visual diff) inline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": _RUN_ID,
                "min_impact": {"type": "string", "enum": ["critical", "serious", "moderate", "minor"], "description": "Only findings at or above this impact (default minor: all)"},
                "kind": {"type": "string", "enum": ["a11y", "visual", "layout"], "description": "Only findings of this kind"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Most findings to return (default 20)"},
                "image": {"type": "boolean", "description": "Inline the worst finding's screenshot (default true)"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "status": {"type": "string", "enum": RUN_STATUSES},
                "finished": {"type": "boolean"},
                "counts": {"type": ["object", "null"]},
                "fail_on": _NULLABLE_STRING,
                "findings": {"type": "array", "items": {"type": "object"}},
                "visual": {"type": "array", "items": {"type": "object"}},
                "pages": {"type": "array", "items": {"type": "object"}},
                "total": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
            "required": ["run_id", "status", "finished", "findings", "visual", "pages", "total", "truncated"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_export_code",
        "title": "Export a passed run as test code",
        "description": "Turn a passed run into automation code: format playwright-ts (Playwright Test), playwright-py (pytest-playwright), appium-py (a native app run: pytest with the Appium Python client) or spec (the framework-neutral automation spec: steps, locators that were unique on the live page, values with secrets as environment references, checks, API/SQL calls). Returns the code, a suggested file path, the environment variables it reads and the steps to review (checks a model judged by meaning, steps that could not be exported). Adapt it to the repository's test conventions, then run it. Failed runs are refused unless allow_failed (exports up to the last passed step). Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": _RUN_ID,
                "format": {"type": "string", "enum": ["playwright-ts", "playwright-py", "appium-py", "spec"], "description": "Default playwright-ts; appium-py for a native app run"},
                "target_dir": {"type": "string", "maxLength": 300, "description": "The repository's test folder (relative), used for the suggested file path, e.g. e2e or tests/e2e"},
                "allow_failed": {"type": "boolean", "description": "Export a run that did not pass, up to its last passed step (marked as partial)"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "format": {"type": "string"},
                "case_id": {"type": "string"},
                "filename": {"type": "string", "description": "Suggested path, relative to the repository"},
                "code": {"type": "string"},
                "spec": {"type": "object", "description": "The exported automation spec"},
                "env": {"type": "array", "items": {"type": "object"}},
                "notes": {"type": "array", "items": {"type": "string"}},
                "partial": {"type": "boolean"},
                "steps_exported": {"type": "integer"},
                "steps_total": {"type": "integer"},
                "guidance": {"type": "string"},
            },
            "required": ["run_id", "format", "filename", "code", "spec", "env", "notes", "guidance"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_run_cancel",
        "title": "Cancel a run",
        "description": "Cancel a queued or running run (the browser is closed).",
        "inputSchema": {"type": "object", "properties": {"run_id": _RUN_ID}, "required": ["run_id"], "additionalProperties": False},
        "outputSchema": RUN_SUMMARY,
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_approve",
        "title": "Answer a run that needs approval",
        "description": "Answer a run in needs_approval: a case drafted from a description (approve starts it, reject cancels it), or a running run's question (a destructive click, a step it could not do): approve, reject, or edit with step_text to replace the step and run it again. A run in needs_plan_approval: approve or reject its plan, or edit with plan (the edited plan from hercules_plan).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": _RUN_ID,
                "decision": {"type": "string", "enum": ["approve", "reject", "edit"], "description": "Default approve"},
                "step_text": {"type": "string", "maxLength": 2000, "description": "The step's new text (edit only)"},
                "plan": {"type": "object", "description": "The edited plan (edit of a run in needs_plan_approval): change plan steps' text, criteria or order; it is validated"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": RUN_SUMMARY,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_plan",
        "title": "Make or read a plan",
        "description": "The plan of a test case without running it: each step's plan steps (kind, target, value with placeholders, needs, risk flags), success criteria (author: from the case's words; inferred: the model's expectation), the coverage audit and lint findings (errors block strict runs). Give one of test_cases (one TestCase), gherkin or case_path to make a plan, or run_id to read the plan a run used (with each criterion's result and the run's amendments; against: another run_id to diff the two plans). approve true stores it as approved for this case version.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "test_cases": {"type": "object", "description": "One TestCase object"},
                "gherkin": {"type": "string", "description": "Gherkin text with one scenario"},
                "case_path": {"type": "string", "description": "Path of a .json or .feature case inside the project"},
                "run_id": {**_RUN_ID, "description": "Read the plan this run used instead of making one"},
                "against": {**_RUN_ID, "description": "With run_id: another run whose plan to compare"},
                "environment": _RUN_OPTIONS["environment"],
                "base_url": _RUN_OPTIONS["base_url"],
                "mode": _RUN_OPTIONS["mode"],
                "egress": _RUN_OPTIONS["egress"],
                "strict": _RUN_OPTIONS["strict"],
                "approve": {"type": "boolean", "description": "Store the plan as approved (runs with plan_review ask reuse it until the case or model changes)"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": ["object", "null"], "description": "The Plan (cases/schemas/plan.schema.json)"},
                "lint_errors": {"type": "integer"},
                "lint_warnings": {"type": "integer"},
                "approved": {"type": "boolean"},
                "diff": {"type": ["object", "null"], "description": "With run_id: the plan's changes (against another run, or the run's amendments)"},
            },
            "required": ["plan", "lint_errors", "lint_warnings"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_rerun",
        "title": "Re-run a case",
        "description": "Run a finished run's case again: scope whole, or failed_only (only if it did not pass; for a passed run it starts nothing and says so). Case files are read again, so edits to the case are used. The new run keeps the run's options (mode, egress, environment, base_url, a11y, visual, fail_on, human-in-the-loop); any of them given here is changed for the new run (false turns a check off). Optional wait_s (at most 50).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": _RUN_ID,
                "scope": {"type": "string", "enum": ["whole", "failed_only"], "description": "Default failed_only"},
                **_RUN_OPTIONS,
                "wait_s": {**_WAIT, "description": "Wait up to this many seconds for the new run"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": _RUNS_OUTPUT,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_list_runs",
        "title": "List runs",
        "description": "Recent runs, newest first, optionally only those with one status or of one folder run (batch).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": RUN_STATUSES},
                "batch": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,128}$", "description": "Only the runs of this folder run (the batch from hercules_run_folder)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 10"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"runs": {"type": "array", "items": RUN_SUMMARY}}, "required": ["runs"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_list_cases",
        "title": "List cases",
        "description": (
            "The project's saved test cases (.hercules/cases), by folder: the path to give hercules_run or hercules_plan as case_path, the title, "
            "steps, environment and whether the case is valid. Optionally only one folder (and its subfolders) or cases whose title, id or path "
            "contains query."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "maxLength": 300, "description": "Only this folder and its subfolders, e.g. Pre-Seeded"},
                "query": {"type": "string", "maxLength": 200, "description": "Words the title, id or path contains (case-insensitive)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Default 50"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "folder": {"type": "string"},
                            "kind": {"type": "string"},
                            "valid": {"type": "boolean"},
                            "id": {"type": ["string", "null"]},
                            "title": {"type": ["string", "null"]},
                            "status": {"type": ["string", "null"]},
                            "steps": {"type": ["integer", "null"]},
                            "environment": {"type": ["string", "null"]},
                            "error": {"type": ["string", "null"]},
                        },
                        "required": ["path", "folder", "kind", "valid"],
                    },
                },
                "folders": {"type": "array", "items": {"type": "string"}},
                "total": {"type": "integer", "description": "Cases that match, before the limit"},
                "truncated": {"type": "boolean", "description": "The project has more matching cases than the listing holds; narrow it with folder or query"},
            },
            "required": ["cases", "folders", "total", "truncated"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_get_case",
        "title": "Read a case",
        "description": "One saved case by its path (from hercules_list_cases): its file as written (Gherkin or JSON) and the TestCase it compiles to.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": "The case's path from hercules_list_cases, e.g. .hercules/cases/Checkout/pay.feature (or Checkout/pay.feature)",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string"},
                "folder": {"type": "string"},
                "kind": {"type": "string"},
                "source": {"type": ["string", "null"]},
                "test_case": {"type": "object"},
            },
            "required": ["name", "path", "folder", "kind", "test_case"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_validate_case",
        "title": "Validate a case",
        "description": (
            "Check a case without saving it: the Gherkin or TestCase compiles, every {{placeholder}} is defined (test data, the environment's "
            "variables, saved values), and the environment it names exists. Give exactly one of gherkin or test_case."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gherkin": {"type": "string", "minLength": 1, "maxLength": 200000, "description": "Gherkin with one scenario (saved as <scenario>.feature)"},
                "test_case": {"type": "object", "description": "A TestCase object (saved as <id>.json): id, title, environment, steps"},
                "test_data": {"type": "object", "description": "Gherkin only: test data for its {{placeholders}} (kept beside the .feature)"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "valid": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
                "id": {"type": ["string", "null"]},
                "title": {"type": ["string", "null"]},
                "steps": {"type": ["integer", "null"]},
                "environment": {"type": ["string", "null"]},
                "file": {"type": ["string", "null"], "description": "The file name it would be saved as"},
            },
            "required": ["valid", "error"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_create_case",
        "title": "Create a case",
        "description": (
            "Save a new case in .hercules/cases (optionally in a folder that exists). It is validated first, as hercules_validate_case does, and "
            "nothing is written when it is not valid. Refused when a case with the same id exists: change that one with hercules_update_case."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gherkin": {"type": "string", "minLength": 1, "maxLength": 200000, "description": "Gherkin with one scenario (saved as <scenario>.feature)"},
                "test_case": {"type": "object", "description": "A TestCase object (saved as <id>.json): id, title, environment, steps"},
                "test_data": {"type": "object", "description": "Gherkin only: test data for its {{placeholders}} (kept beside the .feature)"},
                "folder": {"type": "string", "maxLength": 300, "description": "An existing folder, e.g. Checkout (default: the top level)"},
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "path": {"type": "string"}, "id": {"type": "string"}},
            "required": ["name", "path", "id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_update_case",
        "title": "Update a case",
        "description": (
            "Replace a saved case's content (by its path from hercules_list_cases), also to repair a case that does not load. The new content is "
            "validated first and nothing is written when it is not valid. It is written to the same file: same kind (gherkin for a .feature, "
            "test_case for a .json) and same id (the scenario name or id); to rename, create a new case and delete this one. A .feature keeps "
            "its saved test data unless test_data is given ({} clears it)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": "The case's path from hercules_list_cases, e.g. .hercules/cases/Checkout/pay.feature (or Checkout/pay.feature)",
                },
                "gherkin": {"type": "string", "minLength": 1, "maxLength": 200000, "description": "Gherkin with one scenario (saved as <scenario>.feature)"},
                "test_case": {"type": "object", "description": "A TestCase object (saved as <id>.json): id, title, environment, steps"},
                "test_data": {"type": "object", "description": "Gherkin only: test data for its {{placeholders}} (kept beside the .feature)"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "path": {"type": "string"}, "id": {"type": "string"}},
            "required": ["name", "path", "id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_delete_case",
        "title": "Delete a case",
        "description": "Delete a saved case by its path (with its test data file and the files attached to it). Only when the user asked for it; runs that used it are kept.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": "The case's path from hercules_list_cases, e.g. .hercules/cases/Checkout/pay.feature (or Checkout/pay.feature)",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "removed": {"type": "boolean"}}, "required": ["name", "removed"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_list_folders",
        "title": "List folders",
        "description": "The folders that hold the project's saved cases (.hercules/cases), with how many cases each holds (subfolders included) and how many of them hercules_run_folder would run, as the daemon decides it (a dry run: drafts, cases that do not load and cases whose environment cannot be applied are skipped).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {
                "folders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"folder": {"type": "string"}, "cases": {"type": "integer"}, "ready": {"type": "integer"}},
                        "required": ["folder", "cases", "ready"],
                    },
                },
                "top_level_cases": {"type": "integer", "description": "Cases outside every folder"},
                "truncated": {"type": "boolean", "description": "The project has more cases than were counted"},
            },
            "required": ["folders", "top_level_cases", "truncated"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_get_folder",
        "title": "Read a folder",
        "description": "One folder: its subfolders and its cases (subfolders included), each with the path to give hercules_get_case or hercules_run, and which of them hercules_run_folder would run.",
        "inputSchema": {"type": "object", "properties": {"folder": _FOLDER}, "required": ["folder"], "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
                "subfolders": {"type": "array", "items": {"type": "string"}},
                "cases": {"type": "array", "items": _FOLDER_CASE},
                "ready": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
            "required": ["folder", "subfolders", "cases", "ready", "truncated"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_create_folder",
        "title": "Create a folder",
        "description": "Make a folder for cases in .hercules/cases, at the top level or inside parent (which must exist). Save cases into it with hercules_create_case folder=<folder>.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "The new folder's name, e.g. Payments (no slashes)"},
                "parent": {"type": "string", "maxLength": 1000, "description": "The folder to make it in, e.g. Checkout (default: the top level)"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_update_folder",
        "title": "Rename or move a folder",
        "description": "Rename a folder (name) or move it, with its cases and subfolders, into another folder (parent; empty: the top level). Give exactly one. The cases' paths change with it; runs that used them are kept.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": _FOLDER,
                "name": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Its new name (no slashes)"},
                "parent": {"type": "string", "maxLength": 1000, "description": "The folder to move it into (empty: the top level)"},
            },
            "required": ["folder"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object", "properties": {"folder": {"type": "string"}, "was": {"type": "string"}}, "required": ["folder", "was"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_delete_folder",
        "title": "Delete a folder",
        "description": (
            "Delete a folder. Only when the user asked for it. mode move_up (the default) keeps its cases and subfolders, moving them into the "
            "folder above; delete_cases deletes its cases too (refused while it holds files that are not cases). Runs that used them are kept."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": _FOLDER,
                "mode": {"type": "string", "enum": ["move_up", "delete_cases"], "description": "Default move_up: keep the cases"},
            },
            "required": ["folder"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"folder": {"type": "string"}, "mode": {"type": "string"}, "moved": {"type": "integer"}, "deleted": {"type": "integer"}},
            "required": ["folder", "mode", "moved", "deleted"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_run_folder",
        "title": "Run a folder",
        "description": (
            "Queue a run of every ready case in a folder (subfolders too, unless subfolders is false) as one batch, like the tester UI's folder "
            "run, and return the batch and its run_ids at once. Drafts and cases that do not load are skipped and listed. environment and "
            "base_url, when given, apply to every case. Follow it with hercules_list_runs batch=<batch> or hercules_run_status per run; "
            "optional wait_s (at most 50)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {**_FOLDER, "minLength": 0, "description": "The folder, e.g. Checkout (empty: every saved case)"},
                "subfolders": {"type": "boolean", "description": "Run the cases of its subfolders too (default true)"},
                **_RUN_OPTIONS,
                "wait_s": {**_WAIT, "description": "Wait up to this many seconds for the runs to finish (default 0)"},
            },
            "required": ["folder"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                **_RUNS_OUTPUT["properties"],
                "batch": {"type": "string"},
                "folder": {"type": "string"},
                "skipped": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "reason": {"type": "string"}}, "required": ["name", "reason"]},
                },
            },
            "required": ["runs", "all_finished", "batch", "folder", "skipped"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_list_environments",
        "title": "List environments",
        "description": "The project's saved environments (.hercules/envs): where each runs (base URL, browser, device or native app) and the names of its variables and secrets. One whose file does not load is not listed.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {
                "environments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "kind": {"type": "string", "enum": ["web", "app"]},
                            "base_url": {"type": ["string", "null"]},
                            "browser": {"type": ["string", "null"]},
                            "device": {"type": ["string", "null"]},
                            "platform": {"type": ["string", "null"]},
                            "variables": {"type": "array", "items": {"type": "string"}},
                            "secret_names": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "kind", "variables", "secret_names"],
                    },
                }
            },
            "required": ["environments"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_get_environment",
        "title": "Read an environment",
        "description": "One saved environment in full (secrets by name only) and the saved cases that name it.",
        "inputSchema": {"type": "object", "properties": {"name": _ENV_NAME}, "required": ["name"], "additionalProperties": False},
        "outputSchema": _ENV_OUTPUT,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_create_environment",
        "title": "Create an environment",
        "description": (
            "Save a new environment in .hercules/envs. A web environment needs base_url; a native app environment sets mobile instead. It is "
            "validated first and nothing is written when it is not valid. Refused when the name is taken: change that one with "
            "hercules_update_environment."
        ),
        "inputSchema": {"type": "object", "properties": {"name": _ENV_NAME, **_ENV_FIELDS}, "required": ["name"], "additionalProperties": False},
        "outputSchema": _ENV_OUTPUT,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_update_environment",
        "title": "Update an environment",
        "description": (
            "Change a saved environment: each field given replaces that field (variables and secret_names as a whole), null clears it, and "
            "fields left out are kept. The result is validated first and nothing is written when it is not valid. The name stays: to rename, "
            "create a new environment and delete this one."
        ),
        "inputSchema": {"type": "object", "properties": {"name": _ENV_NAME, **_ENV_FIELDS}, "required": ["name"], "additionalProperties": False},
        "outputSchema": _ENV_OUTPUT,
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_delete_environment",
        "title": "Delete an environment",
        "description": (
            "Delete a saved environment with the files attached to it. Only when the user asked for it. Refused while saved cases run in it, "
            "naming them, unless force is true (only when the user confirms: those cases stop validating until it exists again). Runs that "
            "used it are kept."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": _ENV_NAME, "force": {"type": "boolean", "description": "Delete it although saved cases run in it (only when the user confirms)"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "removed": {"type": "boolean"}, "used_by": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "removed", "used_by"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "hercules_get_evidence",
        "title": "Read evidence",
        "description": "Read one evidence file by its hercules://runs/<run_id>/... URI: text (observations, decisions, run.json) or an image (downscaled).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "e.g. hercules://runs/<run_id>/steps/2/observation.txt"},
                "max_side": {"type": "integer", "minimum": 64, "maximum": 2048, "description": "Longest side of a returned image (default 1024)"},
            },
            "required": ["uri"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"uri": {"type": "string"}, "mime_type": {"type": "string"}, "size_bytes": {"type": "integer"}, "file": {"type": "string"}, "truncated": {"type": "boolean"}},
            "required": ["uri", "mime_type"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "hercules_check_env",
        "title": "Check the setup",
        "description": "Is the daemon running (it is started if needed), which project it serves, the model server settings, cached capabilities and which run modes (pure, hybrid) are available. Never returns key values.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"daemon": {"type": "object"}, "models": {"type": ["object", "null"]}, "modes": {"type": "object"}}, "required": ["daemon", "modes"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]

RESOURCE_TEMPLATES = [
    {"uriTemplate": "hercules://runs/{run_id}/run.json", "name": "run-result", "description": "RunResult of a finished run", "mimeType": "application/json"},
    {"uriTemplate": "hercules://runs/{run_id}/steps/{step}/{file}", "name": "step-evidence", "description": "Evidence of one step: plain.jpg, som.jpg, observation.txt, decisions.jsonl"},
]


def _with_explore() -> None:
    from testzeus_hercules.mcp.explore_specs import EXPLORE_INSTRUCTIONS, EXPLORE_TOOLS

    global INSTRUCTIONS
    TOOLS.extend(EXPLORE_TOOLS)
    INSTRUCTIONS = INSTRUCTIONS + "\n" + EXPLORE_INSTRUCTIONS


_with_explore()
