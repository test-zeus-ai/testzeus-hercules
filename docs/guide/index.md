# Hercules user guide

Hercules, an open-source QA engine by [TestZeus](https://testzeus.com), runs end-to-end tests of your web app in a real browser on your machine,
and of native Android and iOS apps through Appium. You
write the steps in plain language; language models read them and decide what to do, and
code carries out the actions and checks the results. Use it from the command line, from
its local tester UI, or from a coding agent such as Claude Code, Codex, Cursor, GitHub Copilot or Gemini CLI.

## Start here

```bash
uvx testzeus-hercules
```

One line installs Hercules and the browsers you choose and walks you through setup, asking before each
change. Or let your coding agent do it: copy the prompt in
[Set up Hercules with your coding agent](agent-setup.md).

## Terms used in this guide

| Term | Meaning |
|---|---|
| Local model | The chat model configured under `llm` in the settings. Usually a server on your machine (Ollama, LM Studio, ...), but any OpenAI-compatible endpoint works, hosted ones included. It reads the steps in every mode. |
| Decider | The engine that makes decisions: which element, which tool, did the step work. JEV in hybrid mode, the local model (or a local Clef server) in pure mode. |
| Hybrid and pure mode | Hybrid: JEV, a hosted decision model, decides and the local model does the rest. Pure: the local model does everything. See [Modes and privacy](modes-and-privacy.md). |
| Case reading | The local model's reading of a case's steps into typed actions and checks, made once before the run and cached. |
| Plan | The case reading made explicit before the browser starts: each step's actions, success criteria (author or inferred), risks, coverage and lint. See [Plans](plans.md). |
| Evidence | What a run writes to `.hercules/runs/<run_id>/`: screenshots, the page as text, every decision, `run.json`. |

## Pages

| Page | What it covers |
|---|---|
| [Set up with your coding agent](agent-setup.md) | The prompt to paste into your coding agent, and what `uvx testzeus-hercules` does |
| [Quick start: the tester UI](tester-ui-quick-start.md) | From an empty project to a passing test case in the browser: environment, case, run, debug, finish |
| [Feature videos](videos.md) | Fourteen short videos, one per feature: install, the tester UI, plans, debugging, the terminal, coding agents, browsers, phones, export, clouds, import, quality checks, security scans and Docker |
| [Getting started](getting-started.md) | Install, hardware tiers, model servers and other endpoints, `hercules setup`, `hercules doctor`, your first run |
| [Browsers and browser options](browsers.md) | Chromium, Chrome, Edge, Firefox and WebKit; locale, time zone, signed-in auth JSON, headers, proxy and every other Playwright option, locally, in exported code and on clouds |
| [Writing tests](writing-tests.md) | The TestCase format, steps and expected outcomes, test data, secrets, Gherkin, drafts |
| [Writing Gherkin](gherkin.md) | One scenario per `.feature` file: quoting labels and placeholders, choosing the environment with `@env:`, test data, checks, files, and the Gherkin editor's suggestions, colours and keys |
| [Exploratory testing](explore.md) | `hercules explore`: time-boxed bug hunts with a swarm size you choose, reproduced findings with failing cases, triage and the findings ledger; [walkthrough videos](explore.md#walkthrough-videos) |
| [Plans](plans.md) | The plan made before each run: success criteria, coverage audit, lint, review, strict mode, amendments |
| [Running tests](running-tests.md) | `hercules run`, results and evidence, exit codes, the daemon, the tester UI, debugging, approvals, resuming |
| [Modes and privacy](modes-and-privacy.md) | Pure and hybrid mode, the egress policy, the hybrid-mode key, telemetry and how to opt out |
| [Coding agents](coding-agents.md) | `hercules init`, `hercules mcp`, the MCP tools, timeouts, the recommended agent loop |
| [Importing](importing.md) | `hercules import` for v1 projects, OpenAPI specs and requirement documents |
| [Visual and accessibility checks](quality-checks.md) | Checks on how pages look and whether they are accessible |
| [Mobile testing](mobile-testing.md) | Web apps on an emulated phone, and native Android and iOS apps through Appium, locally or on BrowserStack, Sauce Labs and TestMu AI |
| [Security scans](security-scan.md) | `hercules scan` and security steps: scan your app with the nuclei scanner, safely |
| [Running in Docker](docker.md) | The Hercules image: build it, serve the tester UI from a container, sign in, models and keys, your project's files, scans, mobile |
| [Exporting runs as code](exporting-code.md) | `hercules export`: a passed run as a Playwright Test or pytest-playwright file, or its automation spec |
| [Running tests on clouds](clouds.md) | Connect BrowserStack, Sauce Labs or TestMu AI, run a passed run's test on it from the UI or `hercules cloud run`, and get the project for CI |
| [Migrating from v1](migrating-from-v1.md) | What changed from v1, how each v1 command, flag and setting maps to v2, how to move your v1 test cases, what is not in v2 yet |
| [Troubleshooting](troubleshooting.md) | Doctor checks, model server problems, slow runs, browser and daemon issues |
| [Architecture](architecture.md) | How Hercules is built: processes, the run and step graphs, perception, deciders, verification, security, package layout |

## Commands at a glance

| Command | What it does |
|---|---|
| `hercules setup` | Guided first-time setup: model server, OpenRouter key, egress policy, telemetry |
| `hercules doctor` | Check the configuration, hardware tier and model server |
| `hercules run CASE` | Run a test case (`.json`) or a one-scenario `.feature` file and print the result |
| `hercules explore start` | Explore the app for bugs for a set time with the explorers you choose; findings are reproduced before they are reported |
| `hercules explore {status,report,stop,size,charter,findings,triage,issue,reverify}` | Follow and steer a session, triage its findings, write them as issues, check fixes |
| `hercules plan CASE` | Make a case's plan (steps, success criteria, risks), audit its coverage and lint it, without running it |
| `hercules serve` | Start the local daemon: run queue, workers, HTTP API and tester UI |
| `hercules ui` | Print and open a one-time sign-in link for the tester UI |
| `hercules mcp` | MCP server on stdio for coding agents |
| `hercules init --agent {claude,codex,cursor,copilot,gemini,generic}` | Set up a coding agent to use Hercules |
| `hercules completion {bash,zsh}` | Print a shell completion script |
| `hercules import {v1,openapi,doc}` | Import v1 projects, OpenAPI specs or requirement documents as test cases |
| `hercules export RUN` | Write a passed run as automation code (Playwright Test, pytest-playwright) or its spec |
| `hercules cloud {list,connect,check,disconnect,run,bundle}` | Connect BrowserStack, Sauce Labs or TestMu AI; run a passed run's test on one; write it as a project for the cloud's CI |
| `hercules scan` | Scan your app for security issues with nuclei (installed separately; `--install` gets the pinned release) |
| `hercules version` | Print the Hercules version |

Every command has `--help`, and a mistyped command or choice gets a "did you mean" hint. Output is coloured on a terminal only; set `NO_COLOR=1` to turn colour off, or `FORCE_COLOR=1` to keep it in a CI log that shows colour. `hercules worker` also exists: the daemon starts it to run
one queued case; you do not run it yourself.

## Files Hercules uses

```text
~/.hercules/                 your Hercules home (set HERCULES_HOME to move it)
  config.yaml                user settings
  openrouter.env             the hybrid-mode key, if you saved one (readable only by you)
  llm.env                    a hosted model endpoint's key, if you saved one (readable only by you)
  capabilities.json          what `hercules doctor` learned about your model server
  cache/nuclei/              the scanner installed by `hercules scan --install`
  daemon.json                how clients find the running daemon (port, pid, token)
  daemon.log                 output of a daemon started by a coding agent
  installation_id            random id sent with anonymous telemetry

<project>/.hercules/
  config.yaml                project settings
  cases/                     test cases (.json and .feature)
  envs/                      saved environments
  data/                      imported data files and documents
  specs/                     copies of OpenAPI specs imported with `hercules import openapi`
  explore/                   exploration sessions, their reports, promises and the findings ledger
  baselines/                 visual baselines (<name>.png and <name>.json)
  cache/interpret/           case readings, reused while the steps and the model stay the same
  plans/                     generated and approved plans, reused while the case and the model stay the same
  runs/<run_id>/             results and evidence of each run
  scans/<id>/                evidence of each `hercules scan`
  checkpoints/               resume points of runs in progress (daemon)
  history.db                 the daemon's run history
```
