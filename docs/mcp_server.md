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

> **Security model:** the MCP server exposes tools that can drive test execution
> (browsers, API calls, LLM usage) with the privileges of the server process. It
> therefore binds **loopback only** by default and keeps DNS-rebinding protection
> (the `Host`/`Origin` headers must be loopback names). To bind any other address
> (`MCP_HOST=0.0.0.0`, a LAN IP, ...) you **must** set `MCP_AUTH_TOKEN` to a random
> secret of at least 16 characters; the server refuses to start otherwise. When a
> token is set it is enforced on every request, loopback included: clients send
> `Authorization: Bearer <token>`. Terminate TLS in front of the server when it
> leaves the machine, because the token travels in a request header.
>
> Client-supplied filesystem paths (`project_path`, local `prd_path`) must resolve
> inside the Hercules root (`TESTZEUS_ROOT`/cwd), `PROJECT_SOURCE_ROOT`,
> `TEST_DATA_PATH` or a directory listed in `HERCULES_MCP_ALLOWED_ROOTS`. The
> filesystem root `/` and the user's home directory are never accepted as allowed
> roots (they are dropped with a warning), and a local `prd_path` must be a
> `.md`, `.markdown`, `.txt`, `.rst`, `.pdf` or `.docx` file whose name is not a
> dotfile or a config/secret file (`agents_llm_config*`, `*.env`, `.env*`, `*.pem`,
> `*.key`, `sessions.json`, `hercules_history.db*`). PRD URLs
> may only be public `http(s)` hosts (no loopback/private/link-local/metadata
> addresses, every redirect re-checked, size/time bounded).
>
> `hercules_generate_and_execute` **executes what you give it** (`gherkin` or the
> Gherkin generated from `description`). A test plan generated from a PRD is
> untrusted LLM output and is **not** executed until you review it and pass
> `approve_plan=true` (or set `HERCULES_MCP_AUTO_APPROVE_PLAN=true`). Approval is
> bound to the SHA-256 of the plan file: regenerating or editing the plan
> invalidates it. When the HTTP server enforces a bearer token (`MCP_AUTH_TOKEN`,
> or a token passed to `build_http_app`), every tool call must pass an explicit
> `session_id` (the "most recent session" shortcut is for unauthenticated
> loopback use only).

### Environment variables (all optional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | Bind host (applied to the HTTP server). Non-loopback values require `MCP_AUTH_TOKEN`. |
| `MCP_PORT` | `8000` | Bind port (applied to the HTTP server). |
| `MCP_PATH` | `/mcp` | URL path of the MCP endpoint (applied to the HTTP server). |
| `MCP_AUTH_TOKEN` | *(unset)* | Bearer token required on every request when set (>= 16 chars, mandatory for non-loopback binds). |
| `MCP_ALLOWED_HOSTS` | *(unset)* | Extra comma-separated `Host` header names to accept (e.g. a public DNS name). With a wildcard bind (`0.0.0.0`) and no list, the Host check is skipped and the token is the only gate. |
| `HERCULES_MCP_ALLOWED_ROOTS` | *(unset)* | `os.pathsep`-separated extra directories that `project_path`/`prd_path` may point into. |
| `HERCULES_MCP_AUTO_APPROVE_PLAN` | `false` | Skip the `approve_plan` confirmation for PRD-derived test plans. |
| `PRD_FETCH_ALLOW_PRIVATE_HOSTS` | `false` | Allow PRD URLs on private/loopback hosts (e.g. an intranet wiki). |
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

Adjust `url` when you changed `MCP_HOST`/`MCP_PORT`/`MCP_PATH`, and add
`"headers": {"Authorization": "Bearer <MCP_AUTH_TOKEN>"}` when a token is configured.

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
| `hercules_configure_session` | `app_url` (required), `project_path`, `test_type` (informational label: `frontend`/`backend`/`mixed`), `test_scope` (only `codebase`; `diff` is rejected), `credentials` (deprecated, ignored and never stored), `prd_path` | `session_id`, `app_url`, `app_url_reachable` (true/false/null), `project_path`, `test_type`, `test_scope`, `prd_path`, `next_action` |
| `hercules_generate_code_summary` | `session_id`, `project_path` | `summary` (frameworks, languages, entry point/route counts), `total_files`, `code_summary_path`, optional `llm_notes`, `next_action` |
| `hercules_generate_standardized_prd` | `session_id`, `prd_path` | `requirement_count`, `project_name`, `standard_prd_path`, `requirements` (first 50 `{id, title}`), `next_action`, optional `warning` (set when some PRD chunks failed extraction; the requirement set may be incomplete) |
| `hercules_generate_test_plan` | `session_id`, `prd_path`, `cases_per_requirement` (default 3) | `test_plan_path`, `case_count`, `coverage` (requirement matrix totals), `test_cases` (`{id, title, category, priority, tags}`), `next_action` |
| `hercules_generate_and_execute` | `session_id`, `test_ids` (plan subset), `gherkin`, `description`, `additional_instructions`, `approve_plan` (required to run a PRD-derived plan) | `success`, `duration_s`, `summaries`, `run_report_md`, `bundles_count`, `progress_events` (last 20), `steps_completed`, `feature_file`, `source`, optional `error`, `next_action`, and `additional_instructions_note` (set when `additional_instructions` were appended as Gherkin comments). With a `description`, Gherkin generation has a separate fixed 120-second deadline (`_GHERKIN_GENERATION_TIMEOUT_S`, not configurable); on timeout the tool returns an error dict before execution starts |
| `hercules_get_run_results` | `session_id`, `run_id` (omit for newest run), `result_id` (select one result inside the run folder; see below) | `run_id`, `result_id`, optional `other_results`, `junit_xml`, `total`/`passed`/`failed`/`errors`, `report_md`, `html`, `bundles`, `test_cases` (`{name, status, time, failure_message?}`) |
| `hercules_open_dashboard` | `session_id` | `dashboard: "not_running"`, `report_path` (root-relative path of the newest markdown/HTML report; also returned as `report_url` for compatibility, it is no longer a `file://` URI so the host layout is not leaked), `hint` (start `hercules-dashboard`) |
| `hercules_check_config` | `session_id`, `ping_model` (send a tiny prompt and report latency) | `ok`, `checks`: `llm_config`, `litellm_model`, `playwright`, optional `model_ping` |
| `hercules_rerun` | `session_id`, `test_ids`, `approve_plan` | Same shape as `hercules_generate_and_execute`; prefers the session test-plan subset, falls back to the last executed feature file |

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
   a plain-English `description`, or `test_ids` selecting plan cases (after
   reviewing the plan, pass `approve_plan=true`).
6. **Inspect** — `hercules_get_run_results` for per-scenario statuses, the
   markdown report, and failure bundle paths; `hercules_rerun` to re-run.

## Session persistence

Session state (including artifacts) is stored under the project root:

- `.hercules/sessions.json` — session registry; `app_url` userinfo is redacted and credentials are never stored.
- `.hercules/<session_id>/code_summary.json` — code scan output.
- `.hercules/<session_id>/standard_prd.json` — normalized PRD.
- `.hercules/<session_id>/test_plan.json` — generated test plan.

Artifacts other than the registry are per-session: each one lives under the
session's own directory `.hercules/<session_id>/`.

Executed runs each get a unique feature file
`opt/input/hercules_mcp_run_<8-hex>.feature` (a fresh 8-hex suffix per run, so
concurrent runs cannot clobber each other's input file), and results land in
`opt/output/run_<timestamp>/` like any other Hercules run. These per-run
feature files are **kept** after the run: `hercules_rerun` re-executes the
session's last recorded feature file when the session has no test plan. Delete
old `hercules_mcp_run_*.feature` files manually if they pile up.

One MCP server process writes all its runs into a single `opt/output/run_<startup>/`
folder, one `<feature>_result.xml` / `<feature>_result.html` pair per executed
feature file. `hercules_get_run_results` returns the newest result in the
folder together with the HTML report of that same run; `result_id` (the XML
name without `.xml`, listed in `other_results`) selects an older one.

File paths in tool results (`project_path`, `prd_path`, `llm_config.path`, `report_path`, `code_summary_path`, `standard_prd_path`,
`test_plan_path`, `feature_file`, `run_report_md`, `junit_xml`, `report_md`,
`html`, `bundles`, and the paths inside `summaries`) are reported relative to
the Hercules root (`TESTZEUS_ROOT` or the working directory), or as a bare file
name when they lie outside it, so remote clients never see the host's directory
layout. Absolute Hercules-root paths inside error messages and JUnit
`failure_message` text are rewritten the same way (home directory becomes `~`).

`prd_path` URLs must not embed credentials (`user:pass@host` is rejected).
Query-string secrets such as `?token=...` are kept only in the in-memory
session so the fetch works; they are redacted in tool results and in
`.hercules/sessions.json` (a session reloaded from disk therefore needs the
URL passed again). `prd_path` URLs are recognised case-insensitively
(`HTTPS://`) and surrounding whitespace is ignored.

Step-level progress is streamed while a run executes: the runner publishes
events of the shape `{"message": <str>, "type": <str>}` (for example
`type: "step_completed"` after every agent step) on the process-wide
notification hub (`testzeus_hercules.core.notification_manager.notification_hub`).
The event list is capped at the newest 500 events per run; the execute/rerun
tools return the last 20 of them as `progress_events` plus a `steps_completed`
count (the total number of `step_completed` events observed during the run).

## Security notes / known limitations

The server is designed for a trusted operator driving it from a local or
authenticated client. The following risks are pre-existing or accepted; plan
your deployment around them rather than assuming they are mitigated.

- **`execute_python_sandbox` is not a security boundary.** It runs model-written
  Python from a `.py` file inside the workspace. In the default in-process mode
  the code runs inside the Hercules process with the full process environment
  (API keys, tokens, loaded config) and live Playwright objects. The opt-in
  `SANDBOX_MODE=subprocess` mode adds a timeout and resource limits but the
  child still inherits the environment and has normal filesystem and network
  access. Only run it where the model, and anything it can read, is trusted.
- **`sql_calls` accepts a model-supplied `connection_string`.** The target
  database is chosen by the model and is not confined to an allowlist, so it can
  reach any database the server's network position and the credentials in the
  string allow.
- **`generic_http_api`, `download_pdf` and `open_url` are not behind the SSRF
  guard.** Only PRD URL fetches go through `utils/url_guard` (public hosts only).
  Test steps can therefore reach loopback/private/link-local addresses, which is
  required for testing local apps but means a hostile prompt or page can too.
  Likewise the `app_url` reachability probe is only restricted to http(s), does not
  follow redirects and returns a boolean.
- **Tokens travel in cleartext on non-loopback HTTP binds.** `MCP_AUTH_TOKEN` (and
  `DASHBOARD_AUTH_TOKEN`) are plain bearer tokens over HTTP. When binding anything
  other than loopback, terminate TLS in front of the server (reverse proxy,
  tunnel, service mesh) and keep the Hercules port itself unreachable from outside.
- **`ENABLE_*` feature flags widen what is stored or sent.** Flags such as
  `ENABLE_RUN_HISTORY`, `ENABLE_FAILURE_BUNDLES`, `ENABLE_RUN_REPORTING`,
  `ENABLE_SELECTOR_RECORDING`, `ENABLE_PLAYWRIGHT_TRACING` and
  `ENABLE_BROWSER_LOGS` persist screenshots, videos, network logs and page content
  under the project root, which can include data typed or shown during tests.
  `ENABLE_PORTKEY` routes LLM traffic through a third-party gateway and
  `ENABLE_TELEMETRY` is on by default (set `ENABLE_TELEMETRY=0` to disable). Review
  each flag before enabling it on a shared host and protect the output directory
  (the dashboard refuses to serve history databases, selector/session stores and
  dotfiles, but anything else under the project root is downloadable by whoever can
  reach it).
