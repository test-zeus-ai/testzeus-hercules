"""Text written by ``hercules init``: the agent skill, the Claude Code subagent, the Cursor and Copilot rules and the
AGENTS.md / GEMINI.md block.

Every agent gets the same guidance, built from the sections below: when to test, the run loop over the MCP tools,
the case format (web, Gherkin, native app), how to read a failure, extra checks, keeping a passed run as code,
exploring for bugs (and the triage rule), the CLI for agents without MCP, and the rules that keep tests honest.
"""

from __future__ import annotations

TOOLS = (
    "hercules_run",
    "hercules_run_status",
    "hercules_run_result",
    "hercules_quality_report",
    "hercules_export_code",
    "hercules_plan",
    "hercules_run_cancel",
    "hercules_approve",
    "hercules_rerun",
    "hercules_list_runs",
    "hercules_list_cases",
    "hercules_get_case",
    "hercules_validate_case",
    "hercules_create_case",
    "hercules_update_case",
    "hercules_delete_case",
    "hercules_get_evidence",
    "hercules_check_env",
    "hercules_explore",
    "hercules_explore_status",
    "hercules_explore_stop",
    "hercules_explore_sessions",
    "hercules_explore_report",
    "hercules_explore_findings",
    "hercules_explore_finding",
    "hercules_explore_triage",
    "hercules_explore_issue",
    "hercules_explore_reverify",
)

SKILL_NAME = "hercules-test"

_DESCRIPTION = (
    "Test a web or native app end to end with Hercules, an open-source QA engine by TestZeus. "
    'Use whenever the user asks to test, QA, verify, check or try out something a user sees or does ("test the login", '
    '"does checkout still work?", "reproduce this bug", "run the e2e tests"), after changing user-facing behaviour, '
    "to check a page for accessibility or visual regressions, or to turn a working flow into a Playwright or Appium test. "
    'Also use it to explore an app for bugs nobody wrote a test for ("explore the app", "bug hunt", "find bugs", "what\'s broken?"). '
    "Writes a plain-language test case, runs it "
    "in a real browser or device through the hercules MCP tools, reads the failing step and its screenshot, fixes, and "
    "re-runs until it passes."
)

_WHEN = """\
**Every end-to-end test goes through Hercules.** When the user asks you to test, QA, verify, check or try out
something a user sees or does, use the hercules tools (or the `hercules` command below). Do not write a throwaway
Playwright, Selenium or Puppeteer script, drive a browser with other tools, or answer from reading the code alone:
Hercules records every run with its evidence, shows it live in the tester UI (`hercules ui`) where the user can
watch, pause and replay it, and turns a passed run into a maintained test. The repository's own unit tests still run
with its own test command.

Test with Hercules when:

- the user asks for a test of a page, a form, a flow or a whole feature ("test the login", "QA the checkout",
  "does the signup still work?", "run the e2e tests");
- you changed something a user sees or does (a page, a form, a flow, a redirect, a message), before calling the work done;
- the user reports a UI bug: write a case that reproduces it first, watch it fail, then fix and watch it pass;
- the user asks for an accessibility or visual check of a page;
- a flow works and should become an automated test in the repository.

Skip it for pure refactors with no visible change, back-end code already covered by unit tests, and copy edits.
"""

_BEFORE = """\
1. Make sure the app under test is running and note its base URL (for example `http://127.0.0.1:3000`).
   Start it yourself if this repository has a dev server command.
2. Call `hercules_check_env` once per session. It starts the Hercules daemon if needed and says whether the model
   server is ready and which project the daemon serves. If it is not ready, tell the user to run `hercules doctor`
   and follow its `fix:` lines; do not try to work around it.
"""

_FLOW = """\
1. Write a test case for the behaviour you changed and save it as `.hercules/cases/<id>.json` (or a
   single-scenario `.feature` file). One user-visible flow per case, usually 3 to 12 steps.
2. Optionally call `hercules_plan` with `case_path`: it shows the plan (actions, success criteria, risks) and lint
   findings without opening a browser. Fix the case on lint errors; they are cheaper to fix before the run.
3. Call `hercules_run` with `case_path` (or `test_cases`, `gherkin`) and `base_url`. It returns a `run_id` at once;
   runs are queued and execute in a real browser.
4. Call `hercules_run_status` with the `run_id` and `wait_s: 50` until `finished` is true. Every call returns in
   under a minute, so it is safe with tool timeouts. If the status is `needs_approval` or `needs_plan_approval`,
   read the question (or the plan with `hercules_plan` and `run_id`) and answer with `hercules_approve`.
5. Call `hercules_run_result`. A failed run names the failing step with its expected and actual outcome, hints, a
   failure category and one screenshot; other evidence (`observation.txt`, `decisions.jsonl`) is linked as
   `hercules://runs/...` resources that `hercules_get_evidence` reads.
6. Decide what is wrong (see "Reading a failure"), fix it, then call `hercules_rerun` with `scope: "failed_only"`.
   Repeat until the run passes. After three failed attempts at the same step, stop and report what you found.
7. Report the final status, the run id, the `ui_url` (the user can watch and replay the run in `hercules ui`) and
   what you changed.
"""

_CASE_FORMAT = """\
```json
{
  "id": "login-success",
  "title": "Registered user signs in",
  "environment": {"base_url": "http://127.0.0.1:3000"},
  "test_data": {"email": "demo@example.com", "password": {"secret": "APP_TEST_PASSWORD"}},
  "steps": [
    {"action": "Open \\"/login\\""},
    {"action": "Fill \\"{{email}}\\" in \\"Email\\""},
    {"action": "Fill \\"{{password}}\\" in \\"Password\\""},
    {"action": "Click the \\"Sign in\\" button", "expected": "The text \\"Welcome back\\" is shown"},
    {"action": "verify", "expected": "the url contains \\"/dashboard\\""}
  ]
}
```

- `id` is lowercase letters, digits, `.`, `_` or `-`. One action per step, quoting the visible label the user sees
  (button text, field label), not a CSS selector.
- Put what must be true in `expected`; a step with `"action": "verify"` only checks. Every case needs at least one
  `expected`, or a run can only show that nothing crashed.
- Secrets are never written into cases: use `{"secret": "ENV_NAME"}` in `test_data` and `{{name}}` in steps. The
  daemon reads the variable from its own environment.
- Gherkin works too (one scenario per file or per `gherkin` text):

  ```gherkin
  Feature: Sign in
    Scenario: Registered user signs in
      Given I open "/login"
      When I fill "demo@example.com" in "Email"
      And I click the "Sign in" button
      Then the text "Welcome back" is shown
  ```

- Another browser or browser setup goes in `environment`: `"browser": "firefox"` (or `webkit`, `chrome`, `msedge`;
  Chromium by default) and `"browser_options"` with Playwright's option names, for example
  `{"locale": "de-DE", "timezone_id": "Europe/Berlin", "storage_state": ".hercules/auth/admin.json",
  "extra_http_headers": {"Authorization": {"secret": "APP_AUTH_HEADER"}}}`. `storage_state` is a signed-in auth JSON file in
  the project; secrets are `{"secret": "ENV_NAME"}`, never values.
- A native app case replaces `base_url` with `environment.mobile`, for example
  `{"platform": "android", "package": "com.android.settings", "activity": ".Settings"}` or an app file
  (`"app": "apps/demo.apk"`); add `"cloud": "browserstack"` (or `saucelabs`, `testmu`) with `device` to run it on a
  device cloud. It needs an Appium server, or a connected cloud.
- `hercules_run` also accepts a plain-language `description`, one step per line. It becomes a draft that waits for
  `hercules_approve`; prefer writing the case yourself so it is saved in the repository.
"""

_TRIAGE = """\
`hercules_run_result` classifies a failure. Use the category, the expected and actual outcome and the screenshot:

| Category | Usually means | What to do |
|---|---|---|
| `product_bug` | The app did not do what the step expects | Fix the product code, then re-run |
| `contract_violation` | An API or page broke a stated rule | Fix the code that broke it |
| `test_fragility` | The case names a label or path that is ambiguous or changed on purpose | Fix the case: quote the current visible label |
| `test_data` | The data the case relies on is missing or wrong | Fix `test_data`, the seed, or the environment |
| `environment` | The app, the browser or the model server was not reachable | Check the base URL and `hercules_check_env`; do not change code |
| `unclassified` | Not sure | Read the screenshot and `observation.txt` before deciding |

If an element was not found, read the step's `observation.txt` with `hercules_get_evidence`: it lists what the page
actually showed.
"""

_EXTRAS = """\
- Accessibility and visual checks: pass `a11y: true` and/or `visual: true` to `hercules_run`. Read every finding
  with `hercules_quality_report`; each comes with a fix (`snippet`, `likely_file`). Add `fail_on: "serious"` when
  the user wants findings to fail the run.
- Risky clicks: pass `confirm_destructive: true` so the run stops for approval before clicking anything that looks
  like delete, pay or transfer, when testing against shared data.
- Keep a passed run as a test: call `hercules_export_code` with the `run_id` and `format` (`playwright-ts`,
  `playwright-py`, or `appium-py` for a native app run). Write the file where this repository keeps its tests, in
  its conventions, resolve the `notes` it lists, and run it with the repository's test command.
- Device clouds: a passed run can run on BrowserStack, Sauce Labs or TestMu AI with
  `hercules cloud run <run_id> --cloud <cloud>` once the user has connected the cloud (`hercules cloud list` shows
  which are). Never ask for or handle the cloud's access key yourself.
"""

_EXPLORE = """\
When the user asks to explore, bug-hunt, "find bugs" or "what's broken" without giving steps, start an exploration;
when they name a known flow with known steps, write a case and run it (the loop above).

**Safety first.** Explore test hosts only (local, staging, test, qa, dev): explorers fill in and submit forms and
change data. Never explore a host that looks like production unless the user explicitly said it is theirs; only then
pass `i_own_this_environment: true`. Use the time and swarm size the user asked for, else the defaults; show the
estimate `hercules_explore` returns and get the user's OK before a session over 30 minutes or over 2 explorers.

1. `hercules_explore` with `base_url` (or `environment`, `start_case`), `mission` (`p1` by default, `forms`,
   `changed` with `changed_paths`, ...) and `time_s`. It returns a `session_id` at once.
2. Poll `hercules_explore_status` with `wait_s: 50` until `finished` is true; its `next` field says what to do.
   `hercules_explore_stop` ends a session early with what it has.
3. Read the summary from `hercules_explore_report` (`full: true` adds the markdown report).
4. List `hercules_explore_findings` with the `session_id` and show each finding to the user: its id, severity
   (say whether it is proposed by the model or confirmed by a person), title, expected vs actual, and the UI link.
   `hercules_explore_finding` gives the steps, the evidence (read it with `hercules_get_evidence`) and the audit trail.
5. **No findings is not a pass.** Say so, and list the coverage gaps and the charters that did not run.

**Triage is the user's decision.** Call `hercules_explore_triage` (confirm, set a severity, dismiss, keep or reject
a case, mark a duplicate) only on the user's explicit decision about that finding: pass their name as `by` and their
own words as `user_said`. It is recorded as "<name> via coding agent". Never triage on your own judgement, and never
change findings the user did not decide. `hercules_explore_issue` writes a finding as an issue; show it to the user
and file it only when they ask. After a fix ships, run `hercules_explore_reverify`: a kept case that passes moves its
finding to `verify_fixed`, a regression case that fails reopens it, and an inconclusive run changes nothing.
"""

_CLI = """\
If the hercules MCP tools are not available, use the command line; it does the same work:

```bash
hercules doctor                                    # is the model server ready? follow its fix: lines
hercules plan .hercules/cases/login-success.json   # the plan and lint findings, no browser
hercules run .hercules/cases/login-success.json --base-url http://127.0.0.1:3000 --json
hercules export <run_id> --format playwright-ts --out tests/e2e/login-success.spec.ts
```

`hercules run` exits 0 when the run passed, 1 when it failed and 2 when it could not run (bad case, no browser,
model server down). With `--json` standard output is one RunResult document; progress goes to standard error. A run
can take a few minutes: give the command a timeout of at least 10 minutes, or run it in the background.
"""

_RULES = """\
- Never weaken a case just to make it pass (dropping an `expected`, loosening a check, deleting a step). Change the
  case only when the case itself was wrong, and say so in your report.
- Never write passwords, keys or other secrets into cases, prompts or commits; reference them by variable name.
- Do not commit `.hercules/runs/` (evidence) or `~/.hercules`; do commit `.hercules/cases/`.
- Test against a local or test environment, never production, unless the user says otherwise.
"""


def _body(title: str) -> str:
    return f"""\
# {title}

Hercules runs locally: the browser, the model and the evidence stay on this machine unless the project's egress
policy allows a hosted decision model.

## When to test

{_WHEN}
## Before the first run

{_BEFORE}
## The loop

{_FLOW}
## Writing a test case

{_CASE_FORMAT}
## Reading a failure

{_TRIAGE}
## More than pass or fail

{_EXTRAS}
## Exploring for bugs

{_EXPLORE}
## Without the MCP tools

{_CLI}
## Rules

{_RULES}"""


SKILL = f"""\
---
name: {SKILL_NAME}
description: {_DESCRIPTION}
---

{_body("Test a change with Hercules")}"""

QA_RUNNER = f"""\
---
name: qa-runner
description: Runs Hercules end-to-end browser and native app tests and reports the failing step, its evidence and the likely fix. Use whenever the user asks to test, QA or verify a page, flow or feature, after changing user-facing behaviour, or to reproduce a reported UI bug. Also use it to explore an app for bugs ("explore", "bug hunt", "find bugs", "what's broken"); it never triages a finding without the user's explicit decision.
tools: Read, Grep, Glob, Write, Edit, Bash, {", ".join(f"mcp__hercules__{tool}" for tool in TOOLS)}
---

You are a QA runner. You test what you are given with Hercules, and only with Hercules: never with a throwaway
browser script or other browser tools. Report what you found.
Fix the product code only when the task you were given says to; otherwise report the fix you recommend.

Report back in a few lines: the run id, pass or fail, the failing step with its expected and actual outcome,
the failure category, what the screenshot shows, and the fix you made or recommend.

{_body("Run the tests")}"""

CURSOR_RULE = f"""\
---
description: Every end-to-end, UI or QA test (test, verify, reproduce a UI bug, check a flow) and every bug hunt (explore, find bugs, what's broken) goes through the Hercules MCP tools
alwaysApply: false
---

{_body("Hercules end-to-end tests")}"""

COPILOT_INSTRUCTIONS = f"""\
---
applyTo: "**"
description: Test user-facing changes end to end with the Hercules MCP tools
---

{_body("Hercules end-to-end tests")}"""

AGENTS_START = "<!-- hercules:start -->"
AGENTS_END = "<!-- hercules:end -->"


def agents_block(skill_path: str) -> str:
    """The marked block for AGENTS.md or GEMINI.md: the loop in short, pointing at the full skill."""
    return f"""\
{AGENTS_START}
## End-to-end tests with Hercules

Every end-to-end, UI or QA test (when the user asks to test, verify or reproduce something a user sees or does,
and after you change user-facing behaviour) goes through the `hercules` MCP server, not a throwaway browser
script; the full guide is `{skill_path}`.

1. `hercules_check_env` once; if the model server is not ready, ask the user to run `hercules doctor`.
2. Write a case in `.hercules/cases/<id>.json` (quote visible labels, put what must be true in `expected`,
   reference secrets as `{{"secret": "ENV_NAME"}}`).
3. `hercules_run` with `case_path` and `base_url`, then `hercules_run_status` (`wait_s` 50) until `finished`.
4. `hercules_run_result`: fix the product code (or the case, if the case was wrong), then `hercules_rerun` with
   `scope: "failed_only"` until it passes. Never weaken a case just to make it pass.
5. Optionally keep it as a test with `hercules_export_code`.

To hunt for bugs nobody wrote a case for ("explore", "bug hunt", "find bugs", "what's broken"), use `hercules_explore`
on a test host only (never one that looks like production unless the user said it is theirs) with the time and swarm
size the user asked for (get their OK before more than 30 minutes or 2 explorers), poll `hercules_explore_status`,
read `hercules_explore_report`, and show each of `hercules_explore_findings` to the user (id, severity proposed or
confirmed, title, expected vs actual, UI link). No findings is not a pass: list the coverage gaps. Never triage
on your own judgement: call `hercules_explore_triage` only on the user's explicit decision, with their name and
their words. After a fix, run `hercules_explore_reverify`.

Without MCP: `hercules run .hercules/cases/<id>.json --base-url URL --json` (exit 0 passed, 1 failed, 2 could not run).
{AGENTS_END}
"""


AGENTS_BLOCK = agents_block(f".agents/skills/{SKILL_NAME}/SKILL.md")


def codex_snippet(command: str) -> str:
    return f'[mcp_servers.hercules]\ncommand = "{_toml_escape(command)}"\nargs = ["mcp"]\nstartup_timeout_sec = 10\ntool_timeout_sec = 60\n'


def _toml_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
