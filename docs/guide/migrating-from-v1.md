# Migrating from v1

> **Watch it:** [Import v1 projects, OpenAPI specs and requirement documents (1:28)](videos.md#11-import-v1-projects-openapi-specs-and-requirement-documents).

Hercules v2 is a new product line. It keeps the idea of v1 (describe a test in plain
language, let an AI agent run it in a real browser) but replaces the engine, the
command line, the configuration and the test format. This page lists what changed, how
the commands map, and how to move your v1 test cases over.

## What changed

| Area | v1 | v2 |
|---|---|---|
| Engine | Multi-agent engine driven by the LLM you configure | New engine: a model reads the steps, a decider picks tools and elements over a numbered list of page elements, and code checks and records every assertion ([Architecture](architecture.md)) |
| Models | LLM providers configured in `.env` or an agents LLM config file | Any OpenAI-compatible endpoint as the local model (Gemma 4 on a server on your machine by default, or a hosted API or LiteLLM proxy), and JEV as the decider in hybrid mode ([Modes and privacy](modes-and-privacy.md)) |
| Command | `testzeus-hercules` with flags, plus `testzeus-hercules-mcp`, `hercules-suite`, `hercules-dashboard` and `hercules-monitor` | One `hercules` command with subcommands ([Command changes](#command-changes)) |
| Configuration | `.env` and an agents LLM config file | `~/.hercules/config.yaml`, `<project>/.hercules/config.yaml` and `HERCULES_*` environment variables ([Configuration](getting-started.md#configuration)) |
| Test format | Gherkin `.feature` files in `opt/input/`, test data in `opt/test_data/` | TestCase JSON in `.hercules/cases/`, or one-scenario `.feature` files ([Writing tests](writing-tests.md)) |
| Secrets | Values in `.env` or test data files | References to environment variables (`{"secret": "NAME"}`); values never stored in cases or evidence |
| Results | Output folder with reports and logs | `run.json` (RunResult), `junit.xml` and per-step evidence in `.hercules/runs/<run_id>/` ([Running tests](running-tests.md#results-and-evidence)) |
| MCP | `testzeus-hercules-mcp` and its tool names | `hercules mcp` with new `hercules_*` tools ([Coding agents](coding-agents.md)) |

v2 also adds a local tester UI with a live view of the browser and step-by-step
debugging ([Running tests](running-tests.md#the-tester-ui-hercules-ui)), plans that
you can review before a run (`hercules plan`), and export of a passed run as Playwright
or Appium code (`hercules export`).

There is no switch to run the v1 engine from v2, and v2 does not read v1 settings,
command-line flags or MCP tool names. Keep v1 installed until your cases pass on v2:
the two use different command names and can sit side by side.

## Command changes

### Commands

| v1 | v2 |
|---|---|
| `testzeus-hercules --input-file opt/input/login.feature` | `hercules run .hercules/cases/<id>.json` (after `hercules import v1`), or `hercules run login.feature` for a feature file with one scenario |
| `testzeus-hercules --bulk` | No single flag yet; run each case in a loop, or queue them on the daemon ([Run all your cases](#run-all-your-cases)) |
| `testzeus-hercules --guided --test "..."` | Write the case yourself, or draft one from a document: `hercules import doc requirements.md --draft` |
| `testzeus-hercules --guided --test "..." --dry-run` | `hercules plan CASE` shows the steps, success criteria and risks without running |
| `testzeus-hercules-mcp` | `hercules mcp`; `hercules init` sets up Claude Code, Codex or Cursor |
| `hercules-dashboard` | `hercules serve`, then `hercules ui` |
| `hercules-suite`, `hercules-monitor` | Not in v2 yet ([Not yet available](#not-yet-available-in-v2)) |
| v1 security tools | `hercules scan`, or a security step in a case ([Security scan](#security-scan)) |
| none | New: `hercules setup` (first-time setup), `hercules doctor` (check the setup), `hercules import` (v1 projects, OpenAPI specs, documents), `hercules export` (a passed run as code) |

### Flags

| v1 flag | v2 |
|---|---|
| `--input-file FILE` | The `CASE` argument of `hercules run` |
| `--project-base DIR` | `--project DIR` |
| `--output-path DIR` | No flag: every run writes to `.hercules/runs/<run_id>/` in the project |
| `--test-data-path DIR` | `test_data` in each case; `hercules import v1` copies it there |
| `--config FILE` | `<project>/.hercules/config.yaml` or `~/.hercules/config.yaml` |
| `--llm-model` | `llm.model` (`HERCULES_LLM_MODEL`) |
| `--llm-model-base-url`, `--llm-base-url` | `llm.base_url` (`HERCULES_LLM_BASE_URL`) |
| `--llm-model-api-type` | `llm.provider` (`HERCULES_LLM_PROVIDER`): `ollama`, `openai-compatible`, `litellm` and others ([Model servers](getting-started.md)) |
| `--llm-model-api-key` | `llm.api_key_env` (`HERCULES_LLM_API_KEY_ENV`) names the variable that holds the key; v2 never takes a key on the command line |
| `--llm-temperature` | `llm.request_params` (`HERCULES_LLM_REQUEST_PARAMS`), for example `{"temperature": 0}` |
| `--agents-llm-config-file`, `--agents-llm-config-file-ref-key` | Gone: v2 has one local model and, in hybrid mode, one decider ([Modes and privacy](modes-and-privacy.md)) |
| `--enable-portkey`, `--portkey-*` | Point `llm.base_url` at the gateway's OpenAI-compatible URL; put header values in variables and name them in `llm.headers` |
| `--browser-path` | `HERCULES_CHROMIUM` |
| `--browser-channel`, `--browser-version` | Gone: v2 runs Playwright's Chromium |
| `--enable-ublock`, `--disable-ublock`, `--auto-accept-screen-sharing`, `--disable-auto-accept-screen-sharing` | Gone |
| `--sandbox-tenant-id`, `--sandbox-custom-injections` | Gone |
| `HEADLESS=false` | `hercules run CASE --headed`, or watch it live in the tester UI |

New `hercules run` flags with no v1 equivalent: `--mode` and `--egress` (who decides and
what may leave the machine), `--env` and `--base-url` (which app to test), `--allow-draft`,
`--max-steps`, `--step-timeout-s`, `--json`, `--a11y`, `--visual`, `--strict` and `--plan`.
See [`hercules run`](running-tests.md#hercules-run).

### v1 settings in `.env`

`hercules import v1` reads the v1 `.env`, carries the app's address and the device
settings over, and lists every engine setting it skipped. Set those again in v2 like this:

| v1 `.env` | v2 |
|---|---|
| `LLM_MODEL_NAME`, `LLM_MODEL_BASE_URL`, `LLM_MODEL_API_TYPE` | `llm.model`, `llm.base_url`, `llm.provider` in `.hercules/config.yaml` |
| `LLM_MODEL_API_KEY` | Keep the key in an environment variable and set `llm.api_key_env` to its name |
| `AGENTS_LLM_CONFIG_FILE`, `AGENTS_LLM_CONFIG_FILE_REF_KEY` | Gone |
| `BASE_URL` (or `APP_URL`, `TARGET_URL`, ...) | The saved environment's `base_url` (`.hercules/envs/default.json`) |
| `HEADLESS` | `--headed` |
| `BROWSER_TYPE` | The environment's `browser`: `chromium`, `chrome`, `msedge`, `firefox` or `webkit` ([Browsers](browsers.md)) |
| `TAKE_SCREENSHOTS` | Always on: every step saves a screenshot |
| `RECORD_VIDEO` | The environment's `browser_options.record_video` ([Browsers](browsers.md)) |
| `ENABLE_PLAYWRIGHT_TRACING` | Not produced yet |
| `RUN_DEVICE` | The environment's `device` ([Mobile testing](mobile-testing.md)) |
| `DEVICE_MANAGER=appium`, `DEVICE_OS`, `DEVICE_NAME`, `APPIUM_*` | The environment's `mobile` ([Mobile testing](mobile-testing.md)) |
| `EXECUTE_BULK` | See [Run all your cases](#run-all-your-cases) |
| `ENABLE_TELEMETRY` | `telemetry.enabled` (`HERCULES_TELEMETRY`), or `--no-telemetry` on any command. v2 sends anonymous events to PostHog |
| `LOG_LEVEL`, `TIMEOUT` and other engine switches | Not carried over; `--step-timeout-s` and a case's `timeout_s` set time limits |

## Move your test cases

### 1. Set up v2

Install v2 and set up a model endpoint ([Getting started](getting-started.md)), then:

```bash
hercules setup
hercules doctor
```

Fix anything `hercules doctor` reports before you import: a case cannot run without a
model.

### 2. Preview the import

Run the import from the folder that will hold the v2 project. `DIR` is the v1 project
folder that holds `opt/`, or `opt/` itself.

```bash
cd my-v2-project
hercules import v1 path/to/v1-project --dry-run
```

The dry run writes nothing and lists what would be converted, what is skipped and why,
and which secrets the cases need. For a v1 project with one feature file that has a
`Background`, a `Scenario` and a `Scenario Outline` with two `Examples` rows:

```text
Import of the v1 project /path/to/v1-project (dry run: nothing was written)
Would write (4):
  case  .hercules/cases/valid-user-signs-in.json  (from opt/input/login.feature:6)
  case  .hercules/cases/bad-input-is-refused-1.json  (from opt/input/login.feature:12)
  case  .hercules/cases/bad-input-is-refused-2.json  (from opt/input/login.feature:12)
  env   .hercules/envs/default.json  (from .env)
Skipped (1):
  .env: 4 v1 engine setting(s) not carried over (configure v2 in .hercules/config.yaml and check with `hercules doctor`): HEADLESS, LLM_MODEL_API_KEY, LLM_MODEL_NAME, RECORD_VIDEO
Secrets referenced by name (set these environment variables before running): PASSWORD
```

### 3. Import

```bash
hercules import v1 path/to/v1-project
```

| Option | Meaning |
|---|---|
| `--base-url URL` | The app's address (default: `BASE_URL` in `.env`, else the first URL in the feature files) |
| `--env-name NAME` | Name of the saved environment to write (default `default`) |
| `--env-file FILE` | Read variables from this file instead of `.env` |

What happens to each part of a v1 project:

| v1 | v2 |
|---|---|
| Each `Scenario` | One case in `.hercules/cases/<id>.json`, tagged `imported:v1` |
| Each `Examples` row of a `Scenario Outline` | One case, with the row's values in the steps and the title |
| `Background` | Its steps are put at the start of every case of the feature |
| `Given` / `When` / `And` lines | Steps (`action`) |
| `Then` lines | `verify` steps, whose `expected` is checked |
| Doc strings and data tables | Folded into the step text |
| Flat test data (`opt/test_data/*.json` object, or `KEY=VALUE` lines) | `test_data` on every case, because v1 gave every scenario all of its test data |
| Test data keys that name a credential (password, token, API key, ...) | Secret references: `"password": {"secret": "PASSWORD"}`; the value is never copied |
| Other test data files | Copied to `.hercules/data/` and attached to the cases whose steps mention them |
| `.env` | A saved environment (`.hercules/envs/default.json`) with the base URL and the secret names |
| Security features | Scan cases ([Security scan](#security-scan)) |
| Sub-folders, symbolic links, v1 engine settings, plain `.env` variables | Not carried over; the report lists them |

For example, this v1 scenario and its test data:

```gherkin
Feature: Sign in to the demo shop

  Background:
    Given I open the sign in page

  Scenario: Valid user signs in
    When I enter the email from test data in the Email field
    And I enter the password from test data in the Password field
    And I click the "Sign in" button
    Then I should see "Signed in as demo@example.com"
```

```json
{"email": "demo@example.com", "password": "demo1234"}
```

become this case:

```json
{
  "id": "valid-user-signs-in",
  "title": "Valid user signs in",
  "tags": ["imported:v1"],
  "environment": {"ref": "default"},
  "test_data": {
    "email": "demo@example.com",
    "password": {"secret": "PASSWORD"}
  },
  "steps": [
    {"action": "I open the sign in page"},
    {"action": "I enter the email from test data in the Email field"},
    {"action": "I enter the password from test data in the Password field"},
    {"action": "I click the \"Sign in\" button"},
    {"action": "verify", "expected": "I should see \"Signed in as demo@example.com\""}
  ],
  "status": "ready"
}
```

(The file also has the fields every case has, such as `description`, `priority` and
`timeout_s`.)

### 4. Set the secrets and run one case

```bash
export PASSWORD=...        # each secret the report lists
hercules run .hercules/cases/valid-user-signs-in.json
```

Start with one case. When it passes, run the rest ([Run all your cases](#run-all-your-cases)).

### 5. Tidy up the steps

v1 phrasing often relied on the v1 agent guessing. v2 runs each step exactly as written
and its verdicts are strict, so a few edits are usually needed. In the example above, the
imported case stops at step 1: "I open the sign in page" names no address, and the base
URL from `.env` already pointed at the sign-in page, so the model looks for a link called
"sign in page" and finds none.

| v1 habit | Change it to |
|---|---|
| `BASE_URL` points at a page deep in the app | Set the environment's `base_url` to the app's root (`http://localhost:3000`) and open pages by path |
| "I open the sign in page", "I go to the cart" | `Open "/login"`, `Go to "/cart"`: a quoted path is joined to the base URL |
| "I enter the email from test data in the Email field" | `Fill "{{email}}" in "Email"`: name the test data entry as a placeholder |
| A password typed in the step | `Fill "{{password}}" in "Password"`, with `password` a secret reference |
| "Then I should see the home page" | `Then I should see "Welcome back"`: quoted text is checked in code; an outcome that cannot be checked is reported `unverified` and fails the run ([Expected outcomes](writing-tests.md#expected-outcomes)) |
| Several actions in one line | One action per step |
| A test that needs `firefox` or `webkit` | Keep it: set the environment's `browser` ([Browsers](browsers.md)) |

The tidied steps for the example:

```json
"steps": [
  {"action": "Open \"/login.html\""},
  {"action": "Fill \"{{email}}\" in \"Email\""},
  {"action": "Fill \"{{password}}\" in \"Password\""},
  {"action": "I click the \"Sign in\" button"},
  {"action": "verify", "expected": "I should see \"Signed in as demo@example.com\""}
]
```

with `"base_url": "http://localhost:3000"` in `.hercules/envs/default.json`. All three
imported cases of the example pass after these edits.

Other things to check:

- **Unused test data.** Every case gets all the flat test data, so a case may list a
  secret it never uses. Remove entries a case does not need; `hercules run` asks only
  for the secrets a case refers to.
- **Plans.** `hercules plan CASE` shows how v2 reads each step before you run it, and
  lints the steps.
- **Several environments.** v1 used one `.env` per checkout. In v2, import each one with
  `--env-name staging --env-file .env.staging` and pick it per run with `--env staging`.
- **Drafts.** Imported v1 cases are `ready`. Cases drafted from documents are `draft`
  and need `--allow-draft` or a review first ([Draft and ready cases](writing-tests.md#draft-and-ready-cases)).

### Run all your cases

v2 has no bulk flag yet. Run the cases one after another and stop CI on the first
failure, or collect the failures:

```bash
status=0
for case in .hercules/cases/*.json; do
  hercules run "$case" || status=1
done
exit $status
```

Each run writes its own `junit.xml` under `.hercules/runs/<run_id>/`, which CI can
collect. To run cases in parallel, start the daemon (`hercules serve --parallel N`) and
queue each case through its HTTP API or the tester UI
([The daemon](running-tests.md#the-daemon-hercules-serve)).

### Keep feature files

`hercules run` also takes a `.feature` file with a single scenario and no `Scenario
Outline`, data tables or doc strings. Such a file has no `test_data`, so only the
built-in placeholders `{{run_id}}` and `{{timestamp}}` work in it
([Gherkin files](writing-tests.md#gherkin-feature-files)). Import anything larger.

## Not yet available in v2

| Feature | Status |
|---|---|
| Browsers other than Chromium | Not supported: a case with `browser` set to `firefox` or `webkit` is rejected when it is loaded |
| Scenario Outline, data tables and doc strings in a runnable `.feature` file | Not supported in `hercules run`; `hercules import v1` converts them into separate cases |
| Running a whole folder or suite in one command (`--bulk`, `hercules-suite`) | Not yet; loop over the cases or queue them on the daemon ([Run all your cases](#run-all-your-cases)) |
| Scheduled runs and regression alerts (`hercules-monitor`) | Not yet; schedule the loop above in CI |
| HTML reports, Playwright traces and videos | Not produced yet; use `run.json`, `junit.xml` (written by every run) and the step screenshots |

### Security scan

The v1 security tools are replaced by `hercules scan` and security steps in test cases.
Both run the nuclei scanner, which you install separately (`brew install nuclei`, or
`hercules scan --install` for the pinned release), against the app under test only:

```bash
hercules scan --update-templates
hercules scan --base-url http://localhost:8000 --tags cve,exposure,misconfig --fail-on high
```

```json
{"action": "Scan the shop", "security": {"tags": ["cve", "exposure"], "fail_on": "high"}}
```

`hercules import v1` turns v1 security features into scan cases (one tag per scenario's
topic). Intrusive, denial-of-service and fuzzing templates no longer run unless a case
sets `intrusive: true`. See [Security scans](security-scan.md).

### Mobile testing

Mobile testing is in v2; see [Mobile testing](mobile-testing.md). v1's `RUN_DEVICE` is now
the environment's `device`, and v1's Appium settings (`DEVICE_MANAGER=appium`, ...) are now
its `mobile` app. `hercules import v1` converts both; app files are not copied.
