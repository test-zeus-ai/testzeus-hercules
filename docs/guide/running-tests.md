# Running tests

> **Watch it:** [Debug a run: pause, take over, answer its questions (1:51)](videos.md#4-debug-a-run-pause-take-over-answer-its-questions); [Run tests from the terminal (1:47)](videos.md#5-run-tests-from-the-terminal).

There are two ways to run a case:

- `hercules run` runs one case in the foreground and prints the result. Use it in a
  terminal or in CI.
- `hercules serve` starts the daemon: a run queue, the tester UI and the API that coding
  agents use. Use it to watch runs live, debug them step by step and answer questions a
  run asks.

Both write the same evidence to `.hercules/runs/<run_id>/` in your project.

## `hercules run`

```bash
hercules run CASE [options]
```

`CASE` is a TestCase `.json` file or a `.feature` file with one scenario.

| Option | Meaning |
|---|---|
| `--project DIR` | Project directory (default: the current directory). Settings and evidence come from `DIR/.hercules/`. |
| `--headed` | Show the browser window |
| `--mode {auto,pure,hybrid}` | Who makes decisions. `pure`: the local model does everything (or a local Clef server decides, when configured). `hybrid`: a hosted decision model decides and the local model does the rest (needs `--egress hybrid-text` or `hybrid`). `auto` (default): hybrid when an OpenRouter key is found (environment or `~/.hercules/openrouter.env`) and the egress policy allows it, else pure with a one-line notice. See [Modes and privacy](modes-and-privacy.md). |
| `--egress {local,hybrid-text,hybrid}` | What may leave the machine for this run. Default: `egress.policy` from settings (`hybrid`). |
| `--jev-key-env NAME` | Environment variable that holds the hybrid-mode API key (default `OPENROUTER_API_KEY`) |
| `--base-url URL` | Base URL of the app under test; replaces the case's `environment.base_url` (and the saved environment's) |
| `--env NAME` | Saved environment to run against (`.hercules/envs/NAME.json`). Default: the case's `environment.ref`, if any. |
| `--allow-draft` | Run a case whose `status` is `draft`. Without it, `hercules run` refuses drafts; review the steps and set `"status": "ready"`. |
| `--max-steps N` | Run at most N steps; the rest are reported as skipped |
| `--step-timeout-s S` | Time limit per step in seconds (default 120; 300 in pure mode, where a local model makes every decision). A step's own `timeout_s` wins. |
| `--json` | Print the RunResult as JSON on stdout. The mode line and engine summary go to stderr. |
| `--a11y` | Audit every page the run reaches for accessibility. See [Visual and accessibility checks](quality-checks.md#run-audits). |
| `--visual` | Compare every page the run reaches with its visual baseline and check the layout |
| `--quality-fail-on {critical,serious,moderate,minor}` | Fail the run when an `--a11y` or `--visual` finding is at or above this impact. Needs `--a11y` or `--visual`. Without it, audit findings are only reported. |
| `--strict` | Strict plan mode: lint errors in the run's plan stop it before the browser starts, and an inferred success criterion still unmet after recovery makes its step unverified. Default: `plan.strict` from settings. See [Plans](plans.md#strict-mode). |
| `--plan FILE` | Run this plan (the output of `hercules plan --json`, possibly edited) instead of making one. It must be a plan of this case (same content). |

Examples:

```bash
# Run a case against a local dev server and watch the browser
hercules run .hercules/cases/sign-in.json --base-url http://localhost:3000 --headed

# Machine-readable result for a script or CI job
hercules run .hercules/cases/sign-in.json --json > result.json
```

Every step is read and every decision made by a model; there is no coded fallback. When
the local model server cannot be reached (and in hybrid mode it is still needed to read
the steps), `hercules run` exits with code 2 and says to check the server with
`hercules doctor` or configure one with `hercules setup`.

### What it prints

Before the run, Hercules prints the mode and why it was chosen, any notes, and how long
the model took to load. During the run it shows each step as it goes: on a terminal, the
current step on one line that turns into `✓` or `✗` with its time when the step ends; in
a pipe or CI log, one plain line per finished step and per recovery. With `--json` the
progress goes to standard error, so standard output stays one JSON document. After the
run it prints:

- the run summary: status, the first step that did not pass with its expected and actual
  outcome, and the first hint;
- which engine answered each step's decisions;
- for hybrid mode, the number of hosted decision calls, how many carried a screenshot,
  the cost, and how many fell back to the local model;
- local model calls (the case reading, any re-reading of a step, and decisions in pure
  mode): count, total and mean latency, tokens in and out;
- one line about the run's plan: its criteria (author and inferred) and how many held,
  how many drove a recovery, the amendments, the lint findings and how long planning took
  (see [Plans](plans.md));
- the evidence folder.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The run passed |
| `1` | The run failed or was cancelled |
| `2` | The run could not run: invalid case file or settings, the browser could not start, the model server is unavailable, or the run ended with status `error` |

## Results and evidence

Each run gets an id such as `r_20261026_101500_ab12cd` and a folder:

```text
.hercules/runs/<run_id>/
  run.json                 the RunResult
  plan.json                the run's plan: plan steps, success criteria and their results, amendments, coverage, lint
  junit.xml                the steps as JUnit XML (one testcase per step)
  console.jsonl            the page's console messages and uncaught errors (browser runs)
  network.jsonl            the page's requests: method, URL, status, request headers; failed requests (browser runs)
  case.json                the case as it was run (daemon runs)
  worker.log               the worker's error output (daemon runs)
  preconditions/           the page the preconditions were judged on and the judgement (cases with preconditions)
  steps/<n>/
    plain.jpg              screenshot of the page
    som.jpg                the same screenshot with numbered marks on the elements Hercules saw
    observation.txt        the page as text: the element list the decider saw
    decisions.jsonl        every decision for the step: engine, model, latency, cost, choice, confidence
    plan.jsonl             how the model read the step: from the case reading (or its cache), on the page, any continuation or re-reading, each criterion checked and each amendment
```

`console.jsonl` and `network.jsonl` hold at most 5000 records each. Secret values,
credentials in URLs, secret query values and credential headers (`Authorization`,
cookies, API keys, tokens) are masked in them, as in every other evidence file.

Step folders are numbered from 1. Each step keeps the snapshot of its last attempt.
Secret values are replaced with `******` in every text file.

Inside Hercules, evidence is referenced by URI:
`hercules://runs/<run_id>/steps/<n>/<file>` and `hercules://runs/<run_id>/run.json`.
The UI and the coding-agent tools use these URIs.

### RunResult

`run.json` (and `hercules run --json`) holds:

| Field | Meaning |
|---|---|
| `run_id`, `case_id` | Ids of the run and the case |
| `status` | `passed`, `failed`, `error` or `cancelled` (`queued`, `running`, `needs_approval` and `needs_plan_approval` appear only while a run is live) |
| `verdict_source` | What produced the verdict: `ledger`, `decider`, `llm` or `none` |
| `steps` | One entry per step: `index` (from 1), `action`, `status`, `expected`, `actual`, `duration_ms`, `decisions` (count), `evidence` (URIs) |
| `assertions` | Totals from the assertion ledger: `total`, `passed`, `failed`, `unverified` |
| `hints` | Notes for the reader, for example why the browser did not start, a precondition that was not confirmed, or that the run was resumed |
| `failure` | For a run that did not pass (not a cancelled one): `category` (`environment`, `contract_violation`, `test_fragility`, `product_bug`, `test_data`, or `unclassified`), `confidence`, `summary` and `signals` (below) |
| `evidence` | Evidence files relative to the run folder: `junit`, `console`, `network`. `trace`, `video`, `report_md` and `bundle` are not produced by this version and stay null. |
| `engines` | `mode` (`pure`, `hybrid`, or `test` for the stub test double), local model `llm`, `decider`, `decider_model`, `egress`, hosted decision calls `jev_calls`, decisions asked of a local Clef server `clef_calls`, and `fallbacks` to the local model |
| `cost` | Local model `tokens_in` and `tokens_out` (exact); `decision_tokens_est`, the estimated prompt tokens sent to JEV or a Clef server (they report cost, not token counts; null when none was asked); and `usd` for hosted decisions (null when nothing was billed) |
| `duration_s` | Wall time of the run |
| `ui_url` | Link to the run in the tester UI (daemon runs) |
| `plan` | The run's plan in numbers: plan steps, criteria (author, inferred, passed, failed, unverified, not run), recoveries, amendments, lint errors and warnings, coverage gaps, strict, how it was reviewed, planning time. The plan itself is `plan.json`. |

Step statuses: `passed`, `failed` (a check did not hold), `error` (the step could not be
done), `blocked` (a person rejected it), `unverified` (a check could not be made, or a
model's judgement was not confident either way) and `skipped`.

`failure` is the decider's classification (JEV in hybrid mode, the local model
in pure mode) of the run's observed facts: the first step that did not pass, what was
expected and what happened, the assertion counts and the hints. A pick below 50 %
confidence, or no answer, is `unclassified`. `summary` states what was observed (the
failing step and its outcome); it is not written by a model. `signals` names the engine
that classified it.

The full schema is in `testzeus_hercules/cases/schemas/run_result.schema.json`.

## The daemon: `hercules serve`

```bash
cd my-project
hercules serve
```

```text
Hercules daemon on http://127.0.0.1:7373 (project /path/to/my-project, 1 parallel run(s))
Open the UI once with: http://127.0.0.1:7373/login?code=...
(the link works once; run `hercules ui` for a new one)
```

The daemon serves one project. It queues runs and starts each one in its own worker
process, so a crash in one run does not affect the others. Stop it with Ctrl+C; running
workers are stopped with it.

| Option | Meaning |
|---|---|
| `--project DIR` | Project directory (default: the current directory) |
| `--host HOST` | Address to listen on (default `127.0.0.1`). Any non-loopback address needs `--allow-remote`. |
| `--port PORT` | Port (default `7373`, from `ui.port`). `0` picks a free port. |
| `--allow-remote` | Allow a non-loopback `--host`. Requires `HERCULES_DAEMON_TOKEN` set to a random value of at least 32 characters. The daemon speaks plain HTTP: put a TLS proxy in front of it (the session cookie is marked `Secure` when the proxy sends `X-Forwarded-Proto: https`). Requests must name a loopback host, the bound host, or a hostname listed in `HERCULES_DAEMON_ALLOWED_HOSTS` (comma-separated). |
| `--parallel N` | Runs at a time, 1 to 8 (default: from the hardware tier, usually 1) |
| `--fallback-port` | Use a free port when the requested one is busy |

Exit codes: `0` after a clean stop, `1` when a daemon is already running, `2` when it
cannot start (invalid settings, refused address, port in use).

How clients find the daemon: on start it writes `~/.hercules/daemon.json` (readable only
by you) with its port, process id and a random API token, and removes it on exit. Only
one daemon runs per Hercules home folder. To work on another project, stop the daemon
and start it again in that project.

Security: every request needs the token (or the UI's session cookie), and requests must
come from a loopback address and origin. The token is never shown in the UI or in links.

### Run options available through the daemon

Runs started through the daemon (UI, coding agents, or the HTTP API) accept the same
choices as `hercules run` (`mode`, `egress`, `base_url`, `headed`, `max_steps`,
`step_timeout_s`, `a11y`, `visual`, `fail_on` for `--quality-fail-on`)
plus:

| Option | Meaning |
|---|---|
| `environment` | Name of a saved environment in `.hercules/envs/` (`--env` in `hercules run`) |
| `description` | Plain-language steps, one per line. Becomes a draft that waits for approval. |
| `auto_approve` | Run a drafted description without waiting for approval |
| `confirm_destructive` | Ask before clicking elements whose names suggest deleting, paying or transferring |
| `destructive_terms` | Your own list of words that mark a destructive click |
| `ask_on_failure` | When a step could not be done after its recoveries, ask whether to retry it |
| `approval_timeout_s` | How long a question waits for an answer (default 600 seconds). No answer counts as a rejection. |
| `plan_review` | `ask`: the run waits in `needs_plan_approval` for its plan to be approved, edited or rejected before the browser starts; `auto`: the plan runs as made. Default: `ask` for drafted cases, else `plan.review` from settings (`auto`). |
| `strict` | Strict plan mode (`--strict` in `hercules run`) |
| `plan` | A plan to run (`POST /api/runs` only; from `POST /api/plans`, possibly edited) |

You can set `confirm_destructive`, `destructive_terms`, `ask_on_failure` and
`approval_timeout_s` in the UI's start-run dialog (under **Human in the loop**), on the
`hercules_run` coding-agent tool, or through the HTTP API (`POST /api/runs`).
`hercules run` has no flags for them.

### HTTP API

The daemon describes its API at `GET /api/openapi.json`. Send the token from
`~/.hercules/daemon.json` as `Authorization: Bearer <token>`. Run events stream as
server-sent events from `GET /api/runs/{run_id}/events`. Plans: `POST /api/plans` makes a
case's plan without running it, `GET /api/runs/{run_id}/plan` returns the plan a run used,
`GET /api/runs/{run_id}/plan/diff` compares it with another run's (`against`) or with the
run's amendments, and `GET /api/cases/{name}/plan` returns the latest plan of a saved case.

### Running a folder

**Run folder** (a folder's **•••** menu on the Cases screen, or **Folder** in the start-run
dialog) queues one run per case in the folder, its subfolders included unless you clear
**Include subfolders**. Drafts and cases that do not load are skipped. The runs share a
batch: the Runs screen opens filtered to it (`/?batch=<id>`). Each case runs in its own
environment unless you pick one for all of them.

Over HTTP: `POST /api/runs/batch` with `folder`, `subfolders` and the usual run options
returns the batch id, the queued runs and the skipped cases; `GET /api/runs?batch=<id>`
lists the batch's runs.

## The tester UI: `hercules ui`

With the daemon running:

```bash
hercules ui
```

This prints a one-time sign-in link (valid for 5 minutes) and opens it in your browser.
Use `--print` to only print it. The link signs the browser in with a session cookie; the
API token never reaches the browser. Exit code `1` means no daemon is running.

New to the UI? [Quick start: the tester UI](tester-ui-quick-start.md) walks through writing,
running and finishing one case.

The UI has four sections:

- **Runs**: recent runs; start a run from a saved case, pasted Gherkin, or plain-language
  steps (a draft).
- **Cases**: browse, write and validate cases in `.hercules/cases/` and its
  [folders](writing-tests.md#folders), as a form or as Gherkin.
- **Environments**: saved environments (base URL, viewport, browser, expected secret
  names).
- **Models**: the model server settings, the egress policy, and whether the hybrid-mode
  key variable is set. Key values are never shown.

### Notifications

Wherever you are in the UI, it tells you when:

- **a run ends**: passed, failed, errored or cancelled (a folder run is one notification, when
  its last run ends; a rerun of one of its runs gets its own). Runs a coding agent starts
  through `hercules mcp` count too;
- **a run waits for you**: a step or a plan to approve. The notification stays until the run
  moves on, whether you answer it in the UI, from the terminal or it times out;
- **an Explore session ends**, with its findings count and how many are P0 or P1.

Failures, approvals and sessions with P0 or P1 findings stay until you close them; the rest
close after 10 seconds, unless they arrive while you are in another tab or app, so they are
still there when you come back. While the page is in the background, the tab title shows a
count (or *Approval needed*), and with desktop notifications allowed, your system shows them
too; click one to open the run. Desktop notifications need the UI opened at `127.0.0.1` or
`localhost`. Keep the tab open: if it reloads (or the browser puts it to sleep and wakes it),
it still tells you what ended meanwhile, but a tab opened afresh starts quiet. In a tab left
in the background for a while, the browser slows the page down, so a notification can come
up to a minute late.

Everything is on, with a chime. The first notification asks once whether this browser may
show desktop notifications (**Desktop alerts**). To turn something off, set it in the
project's `.hercules/config.yaml` (or `~/.hercules/config.yaml` for every project); the UI
picks the change up within 15 seconds, without a restart:

```yaml
ui:
  notifications:
    runs: failures   # all (default), failures or off
    approvals: true  # runs waiting for a step or plan approval
    explore: true    # Explore sessions that end
    sound: false     # the chime
    desktop: true    # system notifications while the UI is in the background
```

Runs started with `hercules run` run outside the daemon, so the UI does not see them.

## Debugging a run in the UI

Open a run from the Runs list. While it runs, the run page shows:

- **Live view**: the run's browser, streamed while you watch. When the stream is not
  available it shows the latest screenshot.
- **Timeline**: each step with its status, screenshots (plain or with element marks),
  observation and decisions. Use the up and down arrow keys to move between steps.
- **Debug controls**: pause, resume, step, skip and retry.
- **Plan tab**: the run's plan: plan steps with their state, success criteria with author
  and inferred badges and their results, risks, lint, and the run's amendments as before →
  after (see [Plans](plans.md)).

| Control | Key | What it does |
|---|---|---|
| Pause | Space | Stop before the next step or the next action inside a step |
| Resume | Space | Continue the run |
| Step | N | Run one step, then pause |
| Skip | | While paused: skip the next step (it is reported as skipped) |
| Retry | | While paused: run the previous step again, then pause |

The keys work anywhere on the page except in text fields.

While you debug a run (after you paused or stepped it), a step that cannot be done after
its recoveries stops and asks you what to do instead of failing straight away.

### Approvals

A run stops in `needs_approval` when it has a question:

| Question | When |
|---|---|
| Approve the drafted case | A draft (from a plain-language description or an import) is about to start |
| Confirm a destructive action | The run is about to click something whose name suggests deleting, paying or transferring (only with `confirm_destructive`) |
| A step could not be done | A step failed after its recoveries (with `ask_on_failure`, or while you debug the run) |

The approval card shows the reason, the action and a screenshot. Answer with:

- **Approve**: go ahead (for a failed step: retry it; for a draft: start the run).
- **Reject**: do not do it. A rejected destructive click is reported `blocked`; a rejected
  retry keeps the step's failure; a rejected draft is cancelled. The run stops.
- **Edit step**: replace the step's text and run it again.

A run with `plan_review: ask` (the default for drafted cases) waits in
`needs_plan_approval` before its browser starts. The run page then shows its plan: approve
it, edit it (a plan step's text, criteria, the order of the steps) and approve, or reject
it (the run is cancelled). An approved plan is reused for later runs of the same case
version with the same model, without asking again. See [Plans](plans.md#review-and-approval).

A drafted case can only be approved or rejected. A question that gets no answer within
the approval timeout counts as a rejection. Time spent waiting for you does not count
against step or case timeouts.

### Take over the browser

While a run is paused, select **Take over** in the live view to drive the browser
yourself: clicks on the live view go to the page, and once you have clicked it, the keys you
press go there too (characters, Enter, Tab, Backspace, Escape and the arrow keys; shortcuts
with Ctrl, Cmd or Alt stay with the tester UI). The box under the live view types a longer
text at once. The run stays paused until you
resume it. Use this to get past something the test cannot handle, then let the run
continue.

### Resume after a crash

Runs through the daemon save a checkpoint after every completed step (with the page's
URL, cookies and local storage) in `.hercules/checkpoints/<run_id>.db`. If a run's worker
dies without a result (for example the browser or the model server crashed), the run is
marked `error` and the run page shows **Resume run**. Resuming continues from the last
saved step; a step that finished just before the crash may run again. The checkpoint is removed when the run finishes. `hercules run` does not
keep checkpoints.

### Re-run

A finished run can be run again from its page (or from its row in the runs list):
**Re-run** runs the whole case again, **Re-run if failed** does the same only if the run
did not pass. A run started from a saved case file reads the file again, so your edits
are used; a run started from pasted Gherkin or a description repeats the same case.

#### Re-run with changed options

Both buttons open the **Start a run** dialog filled in with the run's options: environment,
base URL, mode, egress, the quality checks (accessibility, visual, fail on) and the
human-in-the-loop options, and whether the browser window is shown. Change any of them,
for example switch from `pure` to `hybrid` mode or turn the accessibility audit on, then
**Start re-run**. The new run links back to the one it re-runs. The run page header shows
the mode the run used (and the mode it asked for, when it asked for `auto`) and the
decider that answered its questions; the decider is known once the run finishes.

Over the HTTP API, `POST /api/runs/{run_id}/rerun` takes the same options as
`POST /api/runs` next to `scope`. Options you leave out keep the run's values; options you
give are checked the same way as for a new run (an unknown option, an unknown environment
or `fail_on` without `a11y` or `visual` is a 422 and nothing is queued). `null` clears an
option that has no value by default, such as `egress` or `fail_on`. Turning both audits off
also drops an inherited `fail_on`. `scope: "failed_only"` on a run that passed is still a
409.

```bash
curl -s -X POST http://127.0.0.1:7373/api/runs/$RUN_ID/rerun \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scope": "failed_only", "mode": "hybrid", "a11y": true, "fail_on": "serious"}'
```

The `hercules_rerun` coding-agent tool takes the same options as `hercules_run`; `false`
turns a check off for the new run.
