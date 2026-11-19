# Using Hercules from coding agents

> **Watch it:** [Test from your coding agent (1:14)](videos.md#6-test-from-your-coding-agent).

Hercules works as a QA companion for Claude Code, Codex, Cursor, GitHub Copilot (VS Code), Gemini CLI and any other agent that speaks MCP. After the agent
changes user-facing behaviour, it writes a test case, runs it in a real browser through
Hercules, reads the failing step and its screenshot, fixes the code, and runs it again.

The agent talks to Hercules over MCP. `hercules mcp` is a small stdio server that
forwards each tool call to the Hercules daemon, and starts the daemon for the current
project if it is not running.

## Fastest: let the agent set it up

Paste the [coding-agent setup prompt](agent-setup.md) into your agent in your project
folder. It asks you a few questions, runs `uvx testzeus-hercules` with your answers, and runs
a first test. Your API key never goes through the agent.

## Set up an agent: `hercules setup` or `hercules init`

`hercules setup`, run inside your project, finds the coding agents you use and asks,
one by one, whether to connect them (see [Guided setup](getting-started.md#guided-setup-hercules-setup)).
To connect one directly, run this in your project folder:

```bash
hercules init --agent claude    # or codex, cursor, copilot, gemini, generic
```

| Agent | What `hercules init` writes |
|---|---|
| `claude` (Claude Code) | `hercules` server in `.mcp.json`; skill `.claude/skills/hercules-test/SKILL.md`; subagent `.claude/agents/qa-runner.md` |
| `codex` | skill `.agents/skills/hercules-test/SKILL.md`; a marked Hercules block in `AGENTS.md`; the `[mcp_servers.hercules]` table for `~/.codex/config.toml` (printed, or written with `--write-global`) |
| `cursor` | `hercules` server in `.cursor/mcp.json`; rule `.cursor/rules/hercules.mdc` |
| `copilot` (GitHub Copilot in VS Code) | `hercules` server in `.vscode/mcp.json`; skill `.github/skills/hercules-test/SKILL.md`; instructions `.github/instructions/hercules.instructions.md` |
| `gemini` (Gemini CLI) | `hercules` server in `.gemini/settings.json`; skill `.agents/skills/hercules-test/SKILL.md`; a marked Hercules block in `GEMINI.md` |
| `generic` (any other MCP client) | skill `.agents/skills/hercules-test/SKILL.md`; a marked Hercules block in `AGENTS.md`; the server entry printed as JSON for the agent's own MCP config |

| Option | Meaning |
|---|---|
| `--agent {claude,codex,cursor,copilot,gemini,generic}` | Which agent to configure (required) |
| `--project DIR` | Project directory (default: the current directory) |
| `--print` | Print what would be written and change nothing |
| `--force` | Replace existing Hercules files and entries |
| `--write-global` | Codex only: also update `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) |
| `--command CMD` | Command that starts Hercules (default `hercules`) |

Existing files are never replaced without `--force`. JSON configs are merged, so other
MCP servers stay. Exit code `1` means an existing config could not be parsed; add the
server by hand.

If `hercules` is not on the agent's `PATH` (for example it lives in a virtual
environment), pass the full path:

```bash
hercules init --agent claude --command /path/to/project/.venv/bin/hercules
```

The Codex table that `init` prints looks like this:

```toml
[mcp_servers.hercules]
command = "hercules"
args = ["mcp"]
startup_timeout_sec = 10
tool_timeout_sec = 60
```

The skill, subagent and rules tell the agent:

- **when to test**: after a user-visible change, to reproduce a reported UI bug, for an
  accessibility or visual check, or to turn a working flow into a test;
- **how to start**: check the environment once with `hercules_check_env`;
- **the loop**: write a case, plan it, run it, poll, read the result, fix, re-run;
- **the case format**: JSON, Gherkin and native app cases, with secrets by variable name;
- **how to read a failure**: what each failure category usually means and what to change;
- **more than pass or fail**: accessibility and visual audits, risky-click approvals,
  exporting a passed run as code, and running it on a device cloud;
- **exploring for bugs**: when to explore rather than run, test hosts only, asking before a long
  or large session, showing you each finding, and triaging only on your explicit word
  ([watch it](explore.md#5-explore-from-your-coding-agent));
- **the command line** for agents without MCP, with its exit codes;
- **rules**: never weaken a case to make it pass, never write secrets into cases, never
  test production unless told to.

Commit them with your project if your team uses the same agent.

Before the first run, check the model server:

```bash
hercules doctor
```

## How the agent runs a test

Runs can take minutes, but agent tool calls time out, typically after 60 seconds. So
Hercules works like a job queue: start a run, poll its status, then read the result.
Every tool call returns within about 55 seconds.

1. **Start**: `hercules_run` with the case and `base_url`. It returns a `run_id` at once.
2. **Poll**: `hercules_run_status` with the `run_id` and `wait_s` (up to 50). It returns
   as soon as the run finishes, or when `wait_s` runs out. Call it again until `finished`
   is true. While waiting it sends a progress notification per step.
3. **Answer questions**: if the status is `needs_approval`, the run waits for a decision
   (a drafted case, a destructive click, a step it could not do). The status includes the
   reason and a screenshot. The agent answers with `hercules_approve`. If the agent's
   client supports MCP elicitation, `hercules_run_status` asks you directly instead. If
   the status is `needs_plan_approval`, the run waits for its plan (the default for
   drafted cases, or `plan_review: "ask"`): read it with `hercules_plan` (`run_id`), then
   approve, reject, or edit it with `hercules_approve`.
4. **Read the result**: `hercules_run_result` returns the status, the failing step with
   expected and actual outcome, hints, and the failing step's screenshot.
5. **Fix and re-run**: `hercules_rerun` with `scope: "failed_only"`.
6. **Keep it as a test**: once the run passes, `hercules_export_code` turns it into a
   Playwright Test or pytest-playwright file the agent adapts to the repository.

If a tester pauses the run in the UI, the status is `paused`; the agent keeps polling.

Every run an agent starts is recorded like any other: its evidence is in `.hercules/runs/`,
it shows live in the tester UI marked **via Claude Code** (or the agent's name), and you
can watch, pause, answer or replay it there. The skill and the MCP server's own
instructions tell the agent to send every end-to-end, UI or QA test through Hercules
(when you ask it to test, verify or reproduce something, and after it changes
user-facing behaviour) rather than writing a throwaway browser script.

## Tools

### `hercules_run`

Queue one or more runs and return their ids.

| Input | Meaning |
|---|---|
| `test_cases` | A TestCase object, or a list of up to 20 |
| `gherkin` | Gherkin text with one scenario, or a list of up to 20 |
| `description` | Plain-language steps, one per line. Becomes a draft that waits for `hercules_approve`. |
| `case_path` | Path of a `.json` or `.feature` case inside the project |
| `environment` | Name of a saved environment (`.hercules/envs/<name>.json`) |
| `base_url` | Base URL of the app, for example `http://127.0.0.1:3000` (overrides the case) |
| `mode` | `auto`, `pure` or `hybrid` |
| `egress` | `local`, `hybrid-text` or `hybrid` (default: the project's policy) |
| `auto_approve` | Run a drafted description without waiting for approval |
| `a11y` | Audit every page the run reaches for accessibility (see [Visual and accessibility checks](quality-checks.md)) |
| `visual` | Compare every page with its visual baseline and check the layout at 375/768/1280 px |
| `fail_on` | With `a11y` or `visual`: `critical`, `serious`, `moderate` or `minor`; findings at or above it fail the run (default: report only) |
| `confirm_destructive` | Stop for approval before clicking elements whose names suggest deleting, paying or transferring |
| `destructive_terms` | The words that mark such a click (default: a built-in list) |
| `ask_on_failure` | Stop for approval (retry, edit or reject) when a step's recovery is exhausted |
| `approval_timeout_s` | How long a question waits for an answer before the step fails (default 600) |
| `plan_review` | `ask`: wait in `needs_plan_approval` until the plan is approved; `auto`: run the plan as made (default: `ask` for drafts) |
| `strict` | Strict plan mode: lint errors stop the run; inferred criteria still unmet fail their step |
| `plan` | A plan to run (from `hercules_plan`, possibly edited), for one case |
| `wait_s` | Wait up to this many seconds (0 to 50, default 0) and return results that finish in time |

Give exactly one of `test_cases`, `gherkin`, `description` or `case_path`.

Output: `runs` (one summary per run: `run_id`, `status`, `finished`, step progress,
`reason`, `ui_url`, `summary`, `draft_steps`, `approval`, `paused`) and `all_finished`.

### `hercules_run_status`

Wait for a run.

| Input | Meaning |
|---|---|
| `run_id` | The run |
| `wait_s` | Seconds to wait for it to finish, 0 to 50 (default 20) |

Output: the run summary plus `last_seq`. When the run waits for a decision, the
question's screenshot is attached.

### `hercules_run_result`

The result of a finished run.

| Input | Meaning |
|---|---|
| `run_id` | The run |
| `image` | Screenshot to attach for the failing step: `plain` (default), `som` (with element marks) or `none` |

Output: `status`, `summary`, `reason`, `failing_step` (with `expected` and `actual`),
`hints`, `result` (the RunResult; passed and skipped steps are dropped when it is very
large, and `steps_trimmed` is true), `evidence` (each with a `hercules://` URI and its
local file path), `run_dir` and `ui_url`. The failing step's screenshot is attached as a
downscaled JPEG, and its other evidence as resource links.

When the run has accessibility or visual results, `quality` holds the counts by impact,
the audited pages and the top three findings with their fix (`snippet`, `suggested`,
`verified`, `likely_file`); the text lists those fixes, and the worst finding's annotated
screenshot (or visual diff) is attached. The full list comes from `hercules_quality_report`.

### `hercules_quality_report`

Every accessibility, visual and layout finding of a finished run, most severe first.

| Input | Meaning |
|---|---|
| `run_id` | The run |
| `min_impact` | Only findings at or above `critical`, `serious`, `moderate` or `minor` (default: all) |
| `kind` | Only `a11y`, `visual` or `layout` findings |
| `limit` | Most findings to return, 1 to 100 (default 20) |
| `image` | Attach the worst finding's annotated screenshot (default true) |

Output: `counts`, `fail_on`, `findings` (each with its `fixes`, verified ones first, and
`found_on`: the steps and pages), `visual` (each comparison with its baseline, diff ratio,
style deltas, classification and image URIs), `pages`, `total` and `truncated`. Apply a
fix by pasting its `snippet` into `likely_file`, then re-run.

### `hercules_approve`

Answer a run in `needs_approval`.

| Input | Meaning |
|---|---|
| `run_id` | The run |
| `decision` | `approve` (default), `reject` or `edit` |
| `step_text` | The step's new text (with `edit`) |
| `plan` | The edited plan (with `edit`, for a run in `needs_plan_approval`) |

For a drafted case, `approve` starts it and `reject` cancels it. For a running run's
question, `edit` replaces the step's text and runs it again. For a run waiting for its
plan, `approve` runs it, `reject` cancels the run, and `edit` with `plan` runs the edited
plan (checked against the case first).

### `hercules_plan`

The plan of a case without running it, or the plan a run used (see [Plans](plans.md)).

| Input | Meaning |
|---|---|
| `test_cases`, `gherkin`, `case_path` | One case to plan (give exactly one, or `run_id`) |
| `run_id` | Read the plan this run used, with each criterion's result and the run's amendments |
| `against` | With `run_id`: another run whose plan to compare |
| `environment`, `base_url`, `mode`, `egress` | As for `hercules_run` |
| `strict` | Plan for a strict run |
| `approve` | Store the plan as approved for this case version and model |

Output: `plan` (plan steps with kind, target, value with placeholders, needs, risk flags
and success criteria marked `author` or `inferred`; the coverage audit; lint findings),
`lint_errors`, `lint_warnings`, `approved`, and `diff` (with `run_id`). The text shows the
plan with `✓` for author and `~` for inferred criteria. On lint errors, fix the case
before running it, or edit the plan and pass it to `hercules_run` as `plan`.

### `hercules_rerun`

Run a finished run's case again.

| Input | Meaning |
|---|---|
| `run_id` | The finished run |
| `scope` | `failed_only` (default: only if the run did not pass; for a passed run it starts nothing and says so) or `whole` |
| `wait_s` | Wait up to this many seconds (0 to 50) |

A run started from `case_path` reads the case file again, so the agent's edits to the
case are used.

### `hercules_export_code`

Turn a passed run into automation code (see [Exporting runs as code](exporting-code.md)).

| Input | Meaning |
|---|---|
| `run_id` | The passed run |
| `format` | `playwright-ts` (default, Playwright Test), `playwright-py` (pytest-playwright), `appium-py` (a native app run: pytest with the Appium Python client; the default for such runs) or `spec` (the automation spec as JSON) |
| `target_dir` | The repository's test folder (relative), for the suggested file path |
| `allow_failed` | Export a run that did not pass, up to its last passed step (marked partial) |

Output: `code`, `filename` (suggested path), `spec` (the exported automation spec), `env`
(the environment variables the code reads, secrets by name), `notes` (what to review:
checks a model judged by meaning, steps that could not be exported faithfully), `partial`,
`steps_exported`, `steps_total` and `guidance`. The text holds the same and the code in a
fenced block. It is read-only; a failed run is refused with the step where it stopped.

The code is a reference that repeats what the run did, with locators that were unique on
the page. Write the final test where the repository keeps its tests, in its conventions,
resolve the notes, and run it.

### Other tools

| Tool | Input | What it does |
|---|---|---|
| `hercules_run_cancel` | `run_id` | Cancel a queued or running run; the browser is closed |
| `hercules_list_runs` | `status`, `limit` (1 to 50, default 10) | Recent runs, newest first |
| `hercules_list_cases` | `folder`, `query`, `limit` (1 to 200, default 50) | The project's saved cases by folder: the `case_path` to run, title, steps, environment, and whether each is valid |
| `hercules_get_case` | `name` (a path from `hercules_list_cases`) | One case: its file as written and the TestCase it compiles to |
| `hercules_validate_case` | `gherkin` or `test_case`; `test_data` (Gherkin only) | Checks a case without saving it: it compiles, every `{{placeholder}}` is defined, and the environment it names exists |
| `hercules_create_case` | `gherkin` or `test_case`; `test_data`, `folder` | Validates, then saves a new case; refused when the id is taken |
| `hercules_update_case` | `name`; `gherkin` (a `.feature`) or `test_case` (a `.json`); `test_data` | Validates, then replaces the case's content; the kind and id stay the same (to rename, create and delete) |
| `hercules_delete_case` | `name` | Deletes a case with its test data and attached files; agents call it only when the user asks |
| `hercules_get_evidence` | `uri`, `max_side` (64 to 2048, default 1024) | Read one evidence file: text (observation, decisions, `run.json`) or an image (downscaled) |
| `hercules_check_env` | none | Whether the daemon runs (it is started if needed), which project it serves, the model server settings, and which modes are available. Never returns key values. |
| `hercules_explore` | `base_url`, `environment` or `start_case`; `mission`, `prompt`, `time_s`, `workers`, `reproducers`, `max_cost_usd`, `target_findings`, `severity_floor`, `levers`, `changed_paths`, `deny_paths`, `allow_hosts`, `forbid`, `personas`, `teardown_case`, `i_own_this_environment`, `dry_run` | Start an [exploration session](explore.md) and return its id at once. The swarm size is the user's; a size over the machine's guard rail is refused with the numbers. A host that does not look like a test environment is refused unless `i_own_this_environment` (only when the user said the host is theirs) |
| `hercules_explore_status` | `session_id`, `wait_s` (at most 50, default 30) | The session's phase, charters, clues, up to 20 findings, the latest events and a `next` hint |
| `hercules_explore_stop` | `session_id` | Stop a running session; it reports what it has |
| `hercules_explore_sessions` | `limit` (1 to 50, default 10) | The project's sessions, newest first |
| `hercules_explore_report` | `session_id`, `full` | A finished session's summary (findings, coverage gaps, charters not run); `full` adds the markdown report. Read-only |
| `hercules_explore_findings` | `session_id`, `state`, `case`, `limit` (at most 50, default 20) | The findings ledger: id, severity (proposed or confirmed), title, expected and actual, state, case decision and UI link each, with the total and the precision measure |
| `hercules_explore_finding` | `finding_id` (an id or a unique prefix of 4 or more characters) | One finding: steps, expected and actual, why this severity, flakiness, runs, evidence as `hercules://runs/...` URIs for `hercules_get_evidence`, and the audit trail |
| `hercules_explore_triage` | `finding_id`, `by` and `user_said` (required); `state`, `severity`, `priority`, `case`, `duplicate_of`, `tracker`, `reason` | Record the user's triage decision, audited as "<name> via coding agent" with the user's words. Agents call it only on the user's explicit decision; a move the workflow does not allow is refused with the reason |
| `hercules_explore_issue` | `finding_id`, `tracker` (`github`, `jira`, `linear`, `markdown`), `repository` | A finding as issue text (and a prefilled GitHub URL); the agent shows it and files it only when asked |
| `hercules_explore_reverify` | `build`, `finding_ids`, `job_id`, `wait_s` | Rerun the kept and regression cases after a fix; per finding: `verify_fixed`, `regression`, `still_failing`, `still_passes`, `inconclusive` or `skipped`. Call again with `job_id` until finished |

### Resources

Finished runs are also MCP resources:

- `hercules://runs/{run_id}/run.json`: the RunResult
- `hercules://runs/{run_id}/steps/{step}/{file}`: step evidence (`plain.jpg`, `som.jpg`,
  `observation.txt`, `decisions.jsonl`)

## Timeouts

| Limit | Value |
|---|---|
| One tool call | returns within about 55 seconds |
| `wait_s` | at most 50 seconds |
| Starting the daemon on the first call | up to 8 seconds |
| `hercules mcp` startup | answers the agent's `initialize` without contacting the daemon |
| One step | 120 seconds unless the step sets `timeout_s` |
| One case | `timeout_s` in the case, default 300 seconds |

If the daemon cannot start, the tool error says why, and `~/.hercules/daemon.log` has
the daemon's output.

## A good loop for agents

1. Make sure the app under test is running and note its base URL.
2. Write a case for the behaviour you changed and save it as
   `.hercules/cases/<id>.json`. Keep it to the user-visible flow you touched. Quote the
   visible labels, put what must be true in `expected`, and reference secrets with
   `{"secret": "ENV_NAME"}`.
3. Optionally check it with `hercules_plan` first: lint errors (a value with a stray
   bracket, a credential in a URL, a database step with no connection) are cheaper to fix
   before the run, and the plan shows what the run will check.
4. Start it with `hercules_run` (`case_path` or `test_cases`, plus `base_url`).
5. Poll `hercules_run_status` with `wait_s: 50` until `finished`.
6. Read `hercules_run_result`. Look at the failing step, its expected and actual outcome,
   the screenshot, and `observation.txt` if the element was not found.
7. Decide whether the product code or the case is wrong. Do not weaken a case only to
   make it pass.
8. Fix, then `hercules_rerun` with `scope: "failed_only"`. Repeat until it passes.
9. To keep the flow as an automated test, call `hercules_export_code` and write the test
   in the repository's conventions; run it.
10. Report the final status, the run id and what changed.

If a run errors before its first step, call `hercules_check_env`: the model server may
be down, or the daemon may serve a different project.

## Notes

- The daemon reads secrets and the hybrid-mode key from its own environment. When
  `hercules mcp` starts the daemon, it inherits the agent's environment. To control it,
  start `hercules serve` yourself in a terminal where the variables are set.
- One daemon runs at a time. If `hercules_check_env` says it serves a different project,
  stop that daemon and let the agent start a new one, or start `hercules serve` in this
  project.
- Runs started by an agent appear in the tester UI (`hercules ui`), where you can watch
  them live, pause them and answer their questions.
