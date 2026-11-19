"""OpenAPI 3.1 description of the daemon API, built from the pydantic models (served at ``/api/openapi.json``)."""

from __future__ import annotations

import functools
from typing import Any

from pydantic.json_schema import models_json_schema

from testzeus_hercules.daemon.clouds import CloudKeys, CloudRegion, CloudRunCreate
from testzeus_hercules.daemon.secrets import SecretValue
from testzeus_hercules.daemon.models import (
    ApprovalRequest,
    CaseCheck,
    CaseSave,
    CasesMove,
    CaseValidate,
    ControlRequest,
    Environment,
    EnvironmentSave,
    ErrorBody,
    ExportView,
    FolderCreate,
    FolderUpdate,
    InputRequest,
    PlanCreate,
    PlanView,
    RerunRequest,
    RunBatchCreate,
    RunBatchCreated,
    RunCreate,
    RunCreated,
    RunList,
    RunSettings,
    RunView,
    StepDecisions,
    HttpCalls,
)

_REF = "#/components/schemas/{model}"
_MODELS = (
    RunCreate,
    RunCreated,
    RunView,
    RunList,
    RunSettings,
    RerunRequest,
    ControlRequest,
    ApprovalRequest,
    InputRequest,
    PlanCreate,
    PlanView,
    CaseSave,
    CaseValidate,
    CaseCheck,
    CasesMove,
    FolderCreate,
    FolderUpdate,
    RunBatchCreate,
    RunBatchCreated,
    StepDecisions,
    HttpCalls,
    ExportView,
    CloudKeys,
    CloudRegion,
    SecretValue,
    CloudRunCreate,
    Environment,
    EnvironmentSave,
    ErrorBody,
)


def _ref(name: str) -> dict[str, str]:
    return {"$ref": _REF.format(model=name)}


def _json(schema: dict[str, Any], description: str = "OK") -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


def _errors(*codes: int) -> dict[str, Any]:
    texts = {
        400: "Body is not JSON",
        401: "Missing or wrong token",
        403: "Forbidden Host or Origin",
        404: "Not found",
        409: "Conflicts with the run's state",
        413: "Body too large",
        415: "Wrong content type",
        422: "Invalid input",
        502: "The cloud refused or could not be reached",
    }
    return {str(code): _json(_ref("ErrorBody"), texts[code]) for code in (401, 403, *codes)}


def _param(name: str, where: str, schema: dict[str, Any], description: str, required: bool = False) -> dict[str, Any]:
    return {"name": name, "in": where, "required": required or where == "path", "schema": schema, "description": description}


_RUN_ID = _param("run_id", "path", {"type": "string"}, "Run id such as r_20261025_141502_9f3a1c")


def _paths() -> dict[str, Any]:
    run_ops = {"parameters": [_RUN_ID]}
    return {
        "/api/dashboard": {
            "get": {
                "summary": "The home page's numbers: runs, pass rate, median duration and model cost over the last days next to the period before, and runs per day for 14 days",
                "parameters": [
                    _param("days", "query", {"type": "integer", "minimum": 1, "maximum": 90, "default": 7}, "Length of the period, in days"),
                    _param("tz", "query", {"type": "string"}, "IANA time zone for the calendar days (default UTC)"),
                ],
                "responses": {"200": _json({"type": "object"}), **_errors(422)},
            },
        },
        "/api/health": {"get": {"summary": "Daemon status, project and queue counts", "responses": {"200": _json({"type": "object"}), **_errors()}}},
        "/api/models": {
            "get": {
                "summary": "Model server, cached capabilities and available run modes (never key values)",
                "parameters": [_param("probe", "query", {"type": "boolean"}, "Also run the doctor checks against the model server")],
                "responses": {"200": _json({"type": "object"}), **_errors()},
            }
        },
        "/api/login-url": {"post": {"summary": "One-time URL that signs a browser into the UI with an HttpOnly cookie", "responses": {"200": _json({"type": "object"}), **_errors()}}},
        "/api/runs": {
            "get": {
                "summary": "Recent runs, newest first",
                "parameters": [
                    _param(
                        "status",
                        "query",
                        {"type": "string", "enum": ["queued", "running", "paused", "needs_approval", "needs_plan_approval", "passed", "failed", "error", "cancelled"]},
                        "Only runs in this state",
                    ),
                    _param("limit", "query", {"type": "integer", "minimum": 1, "maximum": 200}, "At most this many runs (default 50)"),
                    _param("with_result", "query", {"type": "boolean"}, "Include each finished run's RunResult (duration, cost, failing step)"),
                    _param("batch", "query", {"type": "string"}, "Only the runs of this folder run (the batch id from POST /api/runs/batch)"),
                ],
                "responses": {"200": _json(_ref("RunList")), **_errors(422)},
            },
            "post": {
                "summary": "Queue a run from a TestCase, Gherkin text, a case file in the project or a plain-language description",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("RunCreate")}}},
                "responses": {"201": _json(_ref("RunCreated"), "Queued (or waiting for approval)"), **_errors(400, 422)},
            },
        },
        "/api/runs/batch": {
            "post": {
                "summary": "Run a folder: queue one run per ready case of the folder (and its subfolders), sharing a batch; drafts and cases that do not load are skipped",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("RunBatchCreate")}}},
                "responses": {"201": _json(_ref("RunBatchCreated"), "Queued"), "200": _json(_ref("RunBatchCreated"), "Nothing to run: every case was skipped"), **_errors(400, 404, 422)},
            },
        },
        "/api/runs/{run_id}": {
            **run_ops,
            "get": {
                "summary": "Live status, current step and, once finished, the RunResult; long-polls with wait_s",
                "parameters": [
                    _param("wait_s", "query", {"type": "number", "minimum": 0, "maximum": 50}, "Wait up to this long for the run to finish (or for a new event after after_seq)"),
                    _param("after_seq", "query", {"type": "integer", "minimum": 0}, "Return as soon as an event newer than this arrives"),
                ],
                "responses": {"200": _json(_ref("RunView")), **_errors(404, 422)},
            },
            "delete": {"summary": "Delete a finished run: its record, events and evidence folder", "responses": {"200": _json({"type": "object"}), **_errors(404, 409)}},
        },
        "/api/runs/{run_id}/events": {
            **run_ops,
            "get": {
                "summary": "Server-sent events of the run; resumes after Last-Event-ID (or ?after=) and ends when the run finishes",
                "parameters": [_param("Last-Event-ID", "header", {"type": "integer"}, "Last event number already received"), _param("after", "query", {"type": "integer"}, "Same as Last-Event-ID")],
                "responses": {"200": {"description": "text/event-stream", "content": {"text/event-stream": {"schema": {"type": "string"}}}}, **_errors(404, 422)},
            },
        },
        "/api/runs/{run_id}/events/list": {**run_ops, "get": {"summary": "The run's events as JSON", "responses": {"200": _json({"type": "object"}), **_errors(404, 422)}}},
        "/api/runs/{run_id}/cancel": {
            **run_ops,
            "post": {
                "summary": "Cancel a queued or running run (SIGTERM to the worker's process group, SIGKILL after a grace period)",
                "responses": {"200": _json(_ref("RunView")), **_errors(404, 409)},
            },
        },
        "/api/runs/{run_id}/approve": {
            **run_ops,
            "post": {
                "summary": "Answer a run in needs_approval (a drafted case, or a running run's question): approve, reject, or edit the step's text; or a run in "
                "needs_plan_approval: approve, reject, or edit with the edited plan. An empty body approves.",
                "requestBody": {"required": False, "content": {"application/json": {"schema": _ref("ApprovalRequest")}}},
                "responses": {"200": _json(_ref("RunView")), **_errors(404, 409, 422)},
            },
        },
        "/api/runs/{run_id}/control": {
            **run_ops,
            "post": {
                "summary": "Debug a running run: pause, resume, step (one step, then pause), skip the next step or retry the previous one (both while paused)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("ControlRequest")}}},
                "responses": {"202": _json(_ref("RunView"), "Sent to the worker; the run's events report when it takes effect"), **_errors(404, 409, 422)},
            },
        },
        "/api/runs/{run_id}/input": {
            **run_ops,
            "post": {
                "summary": "Drive a paused run's browser (take over): click or scroll at a point of the live view, type text, press a key",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("InputRequest")}}},
                "responses": {"202": _json({"type": "object"}, "Sent; an input_result event reports the outcome"), **_errors(404, 409, 422)},
            },
        },
        "/api/runs/{run_id}/resume": {
            **run_ops,
            "post": {"summary": "Queue a run whose worker died again; it continues from its last completed step", "responses": {"200": _json(_ref("RunView")), **_errors(404, 409)}},
        },
        "/api/runs/{run_id}/screencast": {
            **run_ops,
            "get": {
                "summary": "Live view (WebSocket): binary messages are frames (16-byte header: JPEG length, width, height, capture time; then the JPEG); text messages are {type: end|idle}",
                "responses": {"101": {"description": "Switching to WebSocket"}, **_errors(404)},
            },
        },
        "/api/runs/{run_id}/rerun": {
            **run_ops,
            "post": {
                "summary": "Run the case again: whole, or failed_only (only when the run did not pass). Case files are read again. Options of POST /api/runs given here change them for the new run; the rest are inherited.",
                "requestBody": {"required": False, "content": {"application/json": {"schema": _ref("RerunRequest")}}},
                "responses": {"201": _json(_ref("RunCreated")), **_errors(404, 409, 422)},
            },
        },
        "/api/runs/{run_id}/evidence/{path}": {
            "parameters": [_RUN_ID, _param("path", "path", {"type": "string"}, "File inside the run directory, e.g. steps/2/som.jpg")],
            "get": {
                "summary": "One evidence file of the run (confined to the run directory; no symbolic links)",
                "parameters": [_param("max_side", "query", {"type": "integer", "minimum": 64, "maximum": 4096}, "Downscale an image to this many pixels on its longer side (JPEG)")],
                "responses": {"200": {"description": "The file with its content type"}, **_errors(404, 422)},
            },
        },
        "/api/runs/{run_id}/case": {
            **run_ops,
            "get": {"summary": "The TestCase the run executes (after environment resolution) and where it came from", "responses": {"200": _json({"type": "object"}), **_errors(404)}},
        },
        "/api/runs/{run_id}/export": {
            **run_ops,
            "get": {
                "summary": "The run as automation code: a Playwright Test or pytest-playwright file, or its automation spec (passed runs only; read-only)",
                "parameters": [
                    _param(
                        "format", "query", {"type": "string", "enum": ["spec", "playwright-ts", "playwright-py", "appium-py"]}, "What to write (default playwright-ts; appium-py for a native app run)"
                    ),
                    _param("allow_failed", "query", {"type": "boolean"}, "Export a run that did not pass, up to its last passed step"),
                    _param("target_dir", "query", {"type": "string"}, "The project's test folder, for the suggested file name (relative)"),
                ],
                "responses": {"200": _json(_ref("ExportView")), **_errors(404, 409, 422)},
            },
        },
        "/api/runs/{run_id}/steps/{step}/decisions": {
            "parameters": [_RUN_ID, _param("step", "path", {"type": "integer", "minimum": 1}, "Step number (1-based)")],
            "get": {
                "summary": "The step's decisions: each question with every option's probability, the answer, engine, latency, cost and fallback reason",
                "responses": {"200": _json(_ref("StepDecisions")), **_errors(404, 422)},
            },
        },
        "/api/runs/{run_id}/http": {
            **run_ops,
            "get": {
                "summary": "The API calls the run sent (each step's http.json): request, response, duration, and the checks and saves that used each response",
                "responses": {"200": _json(_ref("HttpCalls")), **_errors(404)},
            },
        },
        "/api/runs/{run_id}/plan": {
            **run_ops,
            "get": {
                "summary": "The run's plan (plan.json): plan steps, success criteria with their results, amendments, coverage and lint",
                "responses": {"200": _json(_ref("PlanView")), **_errors(404)},
            },
        },
        "/api/runs/{run_id}/plan/diff": {
            "parameters": [_RUN_ID, _param("against", "query", {"type": "string"}, "Another run's id: compare its plan with this run's (default: this run's plan with its amendments)")],
            "get": {"summary": "What changed between two runs' plans, or what the run changed in its plan", "responses": {"200": _json({"type": "object"}), **_errors(404)}},
        },
        "/api/plans": {
            "post": {
                "summary": "Make a case's plan and lint it without running it (the model reads the case; the decider audits its coverage)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("PlanCreate")}}},
                "responses": {"200": _json(_ref("PlanView")), **_errors(400, 409, 422)},
            },
        },
        "/api/cases/{name}/plan": {
            "parameters": [_param("name", "path", {"type": "string"}, "The case's path under .hercules/cases, such as Checkout/login.feature")],
            "get": {"summary": "The latest plan made for a saved case, and whether it is current", "responses": {"200": _json(_ref("PlanView")), **_errors(404, 422)}},
        },
        "/api/cases": {
            "get": {"summary": "Cases in .hercules/cases and its folders with validation status, and every folder", "responses": {"200": _json({"type": "object"}), **_errors()}},
            "post": {
                "summary": "Save a TestCase (<id>.json) or import Gherkin (<scenario>.feature)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("CaseSave")}}},
                "responses": {"201": _json({"type": "object"}), **_errors(400, 409, 422)},
            },
        },
        "/api/cases/validate": {
            "post": {
                "summary": "Check Gherkin or a TestCase without saving it; returns the compiled TestCase or the problem",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("CaseValidate")}}},
                "responses": {"200": _json(_ref("CaseCheck")), **_errors(400, 422)},
            },
        },
        "/api/cases/move": {
            "post": {
                "summary": "Move saved cases (with a Gherkin case's test data) into a folder",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("CasesMove")}}},
                "responses": {"200": _json({"type": "object"}), **_errors(400, 404, 409, 422)},
            },
        },
        "/api/folders": {
            "post": {
                "summary": "Make a folder for cases under .hercules/cases",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("FolderCreate")}}},
                "responses": {"201": _json({"type": "object"}), **_errors(400, 404, 409, 422)},
            },
        },
        "/api/folders/{folder}": {
            "parameters": [_param("folder", "path", {"type": "string"}, "The folder's path, such as Checkout/Payments", required=True)],
            "patch": {
                "summary": "Rename a folder or move it into another folder",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("FolderUpdate")}}},
                "responses": {"200": _json({"type": "object"}), **_errors(400, 404, 409, 422)},
            },
            "delete": {
                "summary": "Delete a folder: move what it holds into its parent (mode=move_up, the default) or delete its cases too (mode=delete_cases)",
                "parameters": [_param("mode", "query", {"type": "string", "enum": ["move_up", "delete_cases"]}, "What happens to what the folder holds")],
                "responses": {"200": _json({"type": "object"}), **_errors(404, 409, 422)},
            },
        },
        "/api/cases/{name}": {
            "parameters": [_param("name", "path", {"type": "string"}, "The case's path under .hercules/cases, such as Checkout/login.feature")],
            "get": {"summary": "One saved case: the TestCase, the file kind and its source text", "responses": {"200": _json({"type": "object"}), **_errors(404, 422)}},
            "delete": {"summary": "Delete a saved case with its test data file and its files for upload steps", "responses": {"200": _json({"type": "object"}), **_errors(404, 422)}},
        },
        "/api/environments": {
            "get": {"summary": "Saved environments", "responses": {"200": _json({"type": "object"}), **_errors()}},
            "post": {
                "summary": "Save an environment (base URL, viewport, browser, device profile or native app, secret names)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("EnvironmentSave")}}},
                "responses": {"201": _json(_ref("Environment")), **_errors(400, 409, 422)},
            },
        },
        "/api/environments/{name}": {
            "parameters": [_param("name", "path", {"type": "string"}, "Environment name")],
            "get": {"summary": "One environment", "responses": {"200": _json(_ref("Environment")), **_errors(404)}},
            "delete": {"summary": "Delete an environment and its files for upload steps (cases that name it no longer load it)", "responses": {"200": _json({"type": "object"}), **_errors(404)}},
        },
        "/api/devices": {
            "get": {"summary": "Device profiles for mobile web emulation (Playwright's names, for environment.device)", "responses": {"200": _json({"type": "object"}), **_errors()}},
        },
        **_cloud_paths(run_ops),
    }


def _cloud_paths(run_ops: dict[str, Any]) -> dict[str, Any]:
    cloud = _param("cloud", "path", {"type": "string", "enum": ["browserstack", "saucelabs", "testmu"]}, "The cloud")
    cloud_run = _param("cloud_run_id", "path", {"type": "string"}, "Cloud run id (12 hex digits)")
    return {
        "/api/clouds": {
            "get": {
                "summary": "BrowserStack, Sauce Labs and TestMu AI: whether each is connected (and from where) and what it can run here",
                "responses": {"200": _json({"type": "object"}), **_errors()},
            },
        },
        "/api/clouds/{cloud}": {
            "parameters": [cloud],
            "put": {
                "summary": "Save the cloud's user name and access key to <hercules home>/clouds.env (mode 600) and check them; the key is never returned",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("CloudKeys")}}},
                "responses": {"200": _json({"type": "object"}), **_errors(404, 422)},
            },
            "delete": {"summary": "Remove the cloud's saved credentials (environment variables are not touched)", "responses": {"200": _json({"type": "object"}), **_errors(404, 409)}},
        },
        "/api/clouds/{cloud}/check": {
            "parameters": [cloud],
            "post": {"summary": "Check that the cloud accepts the current credentials", "responses": {"200": _json({"type": "object"}), **_errors(404)}},
        },
        "/api/clouds/{cloud}/region": {
            "parameters": [cloud],
            "put": {
                "summary": "Save the Sauce Labs region (us-west-1, us-east-4, eu-central-1) or TestMu AI region (us, eu, ap) to <hercules home>/clouds.env and check the saved keys there",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("CloudRegion")}}},
                "responses": {"200": _json({"type": "object"}), **_errors(404, 422)},
            },
        },
        "/api/clouds/{cloud}/devices": {
            "parameters": [cloud],
            "get": {
                "summary": "The cloud's device list fetched last (mobile devices and desktop browsers), and whether it was fetched for other keys or another region",
                "responses": {"200": _json({"type": "object"}), **_errors(404)},
            },
            "post": {
                "summary": "Fetch the mobile devices and desktop browsers the cloud offers the account and save the list until the cloud is disconnected",
                "responses": {"200": _json({"type": "object"}), **_errors(404, 409, 502)},
            },
        },
        "/api/environments/{name}/files": {
            "parameters": [_param("name", "path", {"type": "string"}, "The environment")],
            "get": {
                "summary": "The environment's files for upload steps (name, size); never read by a model",
                "responses": {"200": _json({"type": "object"}), **_errors(404)},
            },
        },
        "/api/environments/{name}/files/{filename}": {
            "parameters": [_param("name", "path", {"type": "string"}, "The environment"), _param("filename", "path", {"type": "string"}, "The file name")],
            "put": {
                "summary": "Add or replace a file for upload steps (raw body, application/octet-stream, at most 25 MB; no credential or configuration files)",
                "requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
                "responses": {"200": _json({"type": "object"}), **_errors(404, 413, 415, 422)},
            },
            "delete": {"summary": "Remove a file for upload steps", "responses": {"200": _json({"type": "object"}), **_errors(404, 422)}},
        },
        "/api/cases/{name}/files": {
            "parameters": [_param("name", "path", {"type": "string"}, "The saved case file name")],
            "get": {
                "summary": "The case's files for upload steps (name, size); found before the environment's, never read by a model",
                "responses": {"200": _json({"type": "object"}), **_errors(404, 422)},
            },
        },
        "/api/cases/{name}/files/{filename}": {
            "parameters": [_param("name", "path", {"type": "string"}, "The saved case file name"), _param("filename", "path", {"type": "string"}, "The file name")],
            "put": {
                "summary": "Add or replace a case file for upload steps (raw body, application/octet-stream, at most 25 MB; no credential or configuration files)",
                "requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
                "responses": {"200": _json({"type": "object"}), **_errors(404, 413, 415, 422)},
            },
            "delete": {"summary": "Remove a case file for upload steps", "responses": {"200": _json({"type": "object"}), **_errors(404, 422)}},
        },
        "/api/secrets": {
            "get": {
                "summary": "Where each named secret comes from (env, file or none); values are never returned",
                "parameters": [_param("names", "query", {"type": "string"}, "Comma-separated environment variable names")],
                "responses": {"200": _json({"type": "object"}), **_errors(422)},
            },
        },
        "/api/secrets/{name}": {
            "parameters": [_param("name", "path", {"type": "string", "pattern": "^[A-Z_][A-Z0-9_]*$"}, "The environment variable name")],
            "put": {
                "summary": "Save a secret's value to <hercules home>/secrets.env (mode 600); the environment variable of the same name wins; the value is never returned",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("SecretValue")}}},
                "responses": {"200": _json({"type": "object"}), **_errors(422)},
            },
            "delete": {"summary": "Remove a saved secret value (environment variables are not touched)", "responses": {"200": _json({"type": "object"}), **_errors(409, 422)}},
        },
        "/api/runs/{run_id}/cloud-runs": {
            **run_ops,
            "get": {"summary": "The run's cloud runs, newest first", "responses": {"200": _json({"type": "object"}), **_errors(404)}},
            "post": {
                "summary": "Run the passed run's exported test on a cloud (web: in the cloud's browser; native app: on its device)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("CloudRunCreate")}}},
                "responses": {"202": _json({"type": "object"}, "Started"), **_errors(404, 409, 422)},
            },
        },
        "/api/runs/{run_id}/cloud-bundle": {
            **run_ops,
            "get": {
                "summary": "The run's exported test as a ready-to-run project for a cloud, with the cloud's CI configuration (ZIP)",
                "parameters": [_param("cloud", "query", {"type": "string", "enum": ["browserstack", "saucelabs", "testmu"]}, "The cloud", required=True)],
                "responses": {"200": {"description": "application/zip"}, **_errors(404, 409, 422)},
            },
        },
        "/api/cloud-runs/{cloud_run_id}": {
            "parameters": [cloud_run],
            "get": {"summary": "One cloud run with the tail of its output (credentials masked)", "responses": {"200": _json({"type": "object"}), **_errors(404)}},
        },
        "/api/cloud-runs/{cloud_run_id}/cancel": {"parameters": [cloud_run], "post": {"summary": "Stop a cloud run", "responses": {"200": _json({"type": "object"}), **_errors(404)}}},
    }


@functools.lru_cache(maxsize=4)
def build_openapi(version: str) -> dict[str, Any]:
    _, schemas = models_json_schema([(model, "validation") for model in _MODELS], ref_template=_REF)
    return {
        "openapi": "3.1.0",
        "info": {"title": "Hercules daemon API", "version": version, "description": "Local API of `hercules serve`. Send `Authorization: Bearer <token>` (the token is in ~/.hercules/daemon.json)."},
        "servers": [{"url": "/"}],
        "components": {
            "schemas": schemas.get("$defs", {}),
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}, "session": {"type": "apiKey", "in": "cookie", "name": "hercules_session"}},
        },
        "security": [{"bearer": []}, {"session": []}],
        "paths": _paths(),
    }
