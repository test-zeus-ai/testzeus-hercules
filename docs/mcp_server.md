# Hercules MCP Server

Hercules can run as an MCP server over the `streamable-http` transport, so MCP
clients such as Cursor, Claude Code, or Windsurf can drive test generation and
execution directly. The server runs Hercules **in-process** (no subprocess per
call) and groups work in **sessions**: a session records the app under test,
the project path, the generated artifacts (code summary, standardized PRD, test
plan) and the run results.

## Starting the server

```bash
testzeus-hercules-mcp
# or
python -m testzeus_hercules.mcp_server
```

Both start a FastMCP streamable-http server at `http://127.0.0.1:8000/mcp` by
default (loopback only).

> **Security note:** the MCP server exposes an **unauthenticated** tool
> endpoint that can drive test execution (browsers, API calls, LLM usage) with
> the privileges of the server process. The default loopback bind keeps it
> local to your machine. If you set `MCP_HOST` to a non-loopback address, the
> endpoint becomes reachable by anyone who can reach that interface — restrict
> it with a firewall and/or put an authenticating reverse proxy in front, and
> bind non-loopback only as an explicit, deliberate opt-in.

### Environment variables (all optional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | Bind host for the MCP server. Non-loopback values expose the unauthenticated endpoint on that interface — see the security note above. |
| `MCP_PORT` | `8000` | Bind port. |
| `MCP_PATH` | `/mcp` | URL path of the MCP endpoint. |
| `TESTZEUS_ROOT` | current working directory | Project root; `opt/` and `.hercules/` are resolved under it. |
| `HERCULES_MCP_EXEC_TIMEOUT` | `1800` | Per-run execution deadline in seconds for the run tools (`hercules_generate_and_execute`, `hercules_rerun`). Invalid values fall back to the default. Note: the description-to-Gherkin LLM generation step has a separate, fixed 120-second deadline (`_GHERKIN_GENERATION_TIMEOUT_S`, not configurable); on timeout the tool returns an error dict before execution starts. |
| `TESTZEUS_PYTHON` | `sys.executable` | Reserved helper variable. The in-process server does not shell out, so it is currently unused; kept for compatibility with external tooling. |

### LLM configuration (required for LLM-backed tools)

The MCP server does **not** reuse the plain `LLM_MODEL_*` setup. Every
LLM-backed tool — `hercules_generate_code_summary` (LLM enrichment),
`hercules_generate_standardized_prd`, `hercules_generate_test_plan`,
`hercules_generate_and_execute(description=...)`, `generate_gherkin`, and the
`ping_model` check in `hercules_check_config` — requires a litellm-profile
config:

```bash
AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
AGENTS_LLM_CONFIG_FILE_REF_KEY=litellm
```

With `LLM_MODEL_*` variables alone, the LLM-backed tools return an error like
`LiteLLM is required. Set AGENTS_LLM_CONFIG_FILE to agents_llm_config.json with
a 'litellm' provider section and AGENTS_LLM_CONFIG_FILE_REF_KEY=litellm.`
Config-free tools still work without any LLM setup: raw-Gherkin execution
(`hercules_generate_and_execute(gherkin=...)`), result inspection
(`hercules_get_run_results`), and `hercules_open_dashboard`. See
`docs/run_guide.md` for the config file schema and provider examples.

## Connecting a client

Copy `mcp_hercules.example.json` (repo root) or add this block to your client's
MCP config:

```json
{
  "mcpServers": {
    "testzeus-hercules": {
      "transport": "streamable-http",
      "url": "http://localhost:8000/mcp",
      "env": {
        "AGENTS_LLM_CONFIG_FILE": "agents_llm_config.json",
        "AGENTS_LLM_CONFIG_FILE_REF_KEY": "litellm"
      }
    }
  }
}
```

Adjust `url` when you changed `MCP_HOST`/`MCP_PORT`/`MCP_PATH`.

## Tools

The nine `hercules_*` tools each return a JSON dict. Errors are returned as
`{"error": "<message>", ...}` instead of raising. The three legacy aliases
(`generate_gherkin`, `run_test`, `get_test_results`) return plain strings
instead (`get_test_results` returns the result dict serialized as JSON text).
Every tool accepts an optional `session_id`; when omitted, the most recent
session is used. `hercules_get_run_results`, `hercules_open_dashboard`, and
`hercules_check_config` accept `session_id` only for symmetry and ignore it:
results are read from `opt/output/` and configuration checks are process-wide.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `hercules_configure_session` | `app_url` (required), `project_path`, `test_type` (`frontend`/`backend`/`mixed`), `test_scope` (`codebase`/`diff`), `credentials` (dict, stored redacted), `prd_path` | `session_id`, `app_url`, `app_url_reachable` (true/false/null), `project_path`, `test_type`, `test_scope`, `prd_path`, `next_action` |
| `hercules_generate_code_summary` | `session_id`, `project_path` | `summary` (frameworks, languages, entry point/route counts), `total_files`, `code_summary_path`, optional `llm_notes`, `next_action` |
| `hercules_generate_standardized_prd` | `session_id`, `prd_path` | `requirement_count`, `project_name`, `standard_prd_path`, `requirements` (first 50 `{id, title}`), `next_action`, optional `warning` (set when some PRD chunks failed extraction; the requirement set may be incomplete) |
| `hercules_generate_test_plan` | `session_id`, `prd_path`, `cases_per_requirement` (default 3) | `test_plan_path`, `case_count`, `coverage` (requirement matrix totals), `test_cases` (`{id, title, category, priority, tags}`), `next_action` |
| `hercules_generate_and_execute` | `session_id`, `test_ids` (plan subset), `gherkin`, `description`, `additional_instructions` | `success`, `duration_s`, `summaries`, `run_report_md`, `bundles_count`, `progress_events` (last 20), `steps_completed`, `feature_file`, `source`, optional `error`, `next_action`, and `additional_instructions_note` (set when `additional_instructions` were appended as Gherkin comments). With a `description`, Gherkin generation has a separate fixed 120-second deadline (`_GHERKIN_GENERATION_TIMEOUT_S`, not configurable); on timeout the tool returns an error dict before execution starts |
| `hercules_get_run_results` | `session_id`, `run_id` (omit for newest run) | `run_id`, `junit_xml`, `total`/`passed`/`failed`/`errors`, `report_md`, `html`, `bundles`, `test_cases` (`{name, status, time, failure_message?}`) |
| `hercules_open_dashboard` | `session_id` | `dashboard: "not_running"`, `report_url` (`file://` URI of the newest markdown/HTML report), `hint` (start `hercules-dashboard`) |
| `hercules_check_config` | `session_id`, `ping_model` (send a tiny prompt and report latency) | `ok`, `checks`: `llm_config`, `litellm_model`, `playwright`, optional `model_ping` |
| `hercules_rerun` | `session_id`, `test_ids` | Same shape as `hercules_generate_and_execute`; prefers the session test-plan subset, falls back to the last executed feature file |

### Deprecated aliases

Legacy tools are still registered for backwards compatibility but are
deprecated:

- `generate_gherkin(description)` — use `hercules_generate_and_execute(description=...)`.
- `run_test(gherkin, description)` — use `hercules_configure_session` + `hercules_generate_and_execute`.
- `get_test_results(run_id)` — use `hercules_get_run_results`.

## Typical flow (Cursor / Claude Code)

1. **Configure** — `hercules_configure_session` with the app URL and project
   path. The tool probes `app_url` reachability and returns the session id.
2. **Scan the codebase** — `hercules_generate_code_summary` detects
   languages/frameworks/routes/entry points.
3. **Normalize requirements** — `hercules_generate_standardized_prd` with a PRD
   path/URL (falls back to the session PRD, then to the project `README.md`).
4. **Plan** — `hercules_generate_test_plan` produces `TC001...` cases with a
   requirement coverage matrix.
5. **Execute** — `hercules_generate_and_execute`, either with raw `gherkin`,
   a plain-English `description`, or `test_ids` selecting plan cases.
6. **Inspect** — `hercules_get_run_results` for per-scenario statuses, the
   markdown report, and failure bundle paths; `hercules_rerun` to re-run.

## Session persistence

Session state (including artifacts) is stored under the project root:

- `.hercules/sessions.json` — session registry; credentials are stored redacted (`***`).
- `.hercules/<session_id>/code_summary.json` — code scan output.
- `.hercules/<session_id>/standard_prd.json` — normalized PRD.
- `.hercules/<session_id>/test_plan.json` — generated test plan.

Artifacts other than the registry are per-session: each one lives under the
session's own directory `.hercules/<session_id>/`.

Executed runs each get a unique feature file
`opt/input/hercules_mcp_run_<8-hex>.feature` (a fresh 8-hex suffix per run, so
concurrent runs cannot clobber each other's input file), and results land in
`opt/output/run_<timestamp>/` like any other Hercules run.

Step-level progress is streamed while a run executes: the runner publishes
events of the shape `{"message": <str>, "type": <str>}` (for example
`type: "step_completed"` after every agent step) on the process-wide
notification hub (`testzeus_hercules.core.notification_manager.notification_hub`).
The event list is capped at the newest 500 events per run; the execute/rerun
tools return the last 20 of them as `progress_events` plus a `steps_completed`
count (the total number of `step_completed` events observed during the run).
