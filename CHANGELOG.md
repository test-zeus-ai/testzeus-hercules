# Changelog

## 2.0.1 (unreleased)

- **API and SQL steps work in the default install**: JSONPath and OpenAPI checking are core
  dependencies now. In 2.0.0 they were in the `api` extra, so `headers`, `json_path` and
  `openapi` checks and `save` values failed with `jsonpath_ng is not installed` after a plain
  `pip install` or `uvx testzeus-hercules`. The `api` extra stays as an empty alias.
- **`hercules explore reverify` runs plans as made**: a kept case whose plan had lint errors
  waited for a plan review nobody could answer and was reported inconclusive without running.

## 2.0.0 (2026-11-23)

Hercules v2 replaces v1: a new engine, command line, configuration and test format.
See [Migrating from v1](docs/guide/migrating-from-v1.md).

- **Command line**: `hercules` with `setup`, `doctor`, `run`, `plan`, `serve`, `ui`, `mcp`,
  `init`, `import`, `export` and `version`.
- **Models**: any OpenAI-compatible endpoint: a local server (Ollama, mlx-vlm, mlx-lm,
  LM Studio, llama.cpp, vLLM), a hosted API or a LiteLLM proxy. Extra request fields,
  headers and cache control per endpoint; `hercules doctor` measures what the endpoint
  supports. Hardware tiers pick a local model and budgets from the machine's memory.
- **Modes**: hybrid (the default when an OpenRouter key is configured: the LLM reads the
  steps, JEV makes the decisions, with zero data retention requested and a per-decision
  fallback to the LLM) and pure (the LLM does everything, or a local Clef server
  decides). Egress policies `local`, `hybrid-text` and `hybrid`.
- **No coded deciders**: every step reading and every decision comes from a model;
  code only executes structured payloads and checks what the page shows.
- **Engine**: LangGraph run and step graphs; Set-of-Mark perception over frames and
  shadow roots; page settling; a confidence gate; bounded recovery and re-reading;
  strict verdicts with an assertion ledger (unverified fails).
- **Daemon and UI**: `hercules serve` (loopback, token, SQLite queue, one worker process
  per run), a React tester UI with live screencast, pause, step, skip, retry, approvals,
  edit and take over, checkpoints and resume after a crash.
- **Environment tools for coding agents**: the MCP server gains `hercules_list_environments`,
  `hercules_get_environment`, `hercules_create_environment`, `hercules_update_environment` (an
  edit made meanwhile in the UI is read again, not overwritten) and
  `hercules_delete_environment` (refused while saved cases run in it, unless the user
  confirms). Secret values never go through them: an environment names its secrets, and their
  values stay in the tester UI or the shell.
- **Folder tools for coding agents**: the MCP server gains `hercules_list_folders`,
  `hercules_get_folder`, `hercules_create_folder`, `hercules_update_folder` (rename or move),
  `hercules_delete_folder` and `hercules_run_folder` (every ready case of a folder as one
  batch, like the UI's folder run); `hercules_list_runs` takes `batch` to follow it.
- **Notifications in the UI**: a toast when a run ends (one per folder run), when a run waits
  for an approval (it stays until the run moves on) and when an Explore session ends; while the
  page is in the background, a desktop notification and a count in the tab title. All on, with
  a chime, by default; `ui.notifications` in config.yaml turns any of them off. Runs started
  with `hercules run` do not go through the UI.
- **Case folders**: cases in folders under `.hercules/cases/` (up to 8 deep), shown as a
  tree on the Cases screen with make, rename, move and delete, and **Run folder**, which
  queues one run per case as a batch.
- **Coding agents**: `hercules mcp` and `hercules init --agent claude|codex|cursor`.
- **Tests beyond the UI**: structured API and read-only SQL steps; accessibility, visual
  baseline and layout checks with fixes verified in the page (axe-core bundled).
- **Importers**: v1 projects, OpenAPI 3.x specs and requirement documents.
- **Security scans**: `hercules scan` and `security` steps run the nuclei scanner (an
  optional external tool; `hercules scan --install` downloads a pinned, checksum-checked
  release) against the app under test only: its base URL, or an OpenAPI spec's operations
  sent to it. Intrusive, denial-of-service and fuzzing templates need an explicit opt-in.
  Findings at or above `fail_on` fail the step; they are evidence, counted in `run.json`
  (`security`), JUnit and the UI. Credentials are masked in the command, the output and
  the findings. `hercules doctor` reports the scanner. Replaces the v1 security tools.
- **Export as code**: a passed run becomes a Playwright Test (TypeScript) or pytest-playwright
  (Python) file, or a framework-neutral automation spec (`RunResult.automation`): the actions
  it took, locators proven unique on the live page (frames and shadow DOM included), values
  with secrets as environment references, its checks, API and SQL calls. `hercules export`,
  the `hercules_export_code` MCP tool, `GET /api/runs/{id}/export` and an "Export as code"
  button in the UI. Checks a model judged and steps that cannot be written faithfully are
  kept as TODO comments and listed for review.
- **Plan phase**: before the browser starts, the case becomes an explicit plan
  (`plan.json`, schema `cases/schemas/plan.schema.json`): per step its actions, success
  criteria (author: the case's words; inferred: the model's expectation) with a check form
  evaluated on the settled page, needs and risk flags. The plan comes from the same
  whole-case reading (one structured call per part, cached); the decider audits its
  coverage and gaps are read again; lint reports the model's findings (stray brackets,
  markup in values, ambiguous references) and facts (missing secrets, credentials in URLs,
  unresolved placeholders, query steps with no connection, unconfirmed risky steps).
  Strict mode (`--strict`, `plan.strict`) stops a run with lint errors before the browser
  starts. Review: `plan_review: ask` (the default for drafts) waits in
  `needs_plan_approval`; approve, edit or reject in the UI or with `hercules_approve`;
  approved plans are reused until the case or the model changes. During the run author
  criteria fail their step, unmet inferred ones drive a recovery and are then warnings, and
  every plan change is recorded as an amendment. `hercules plan`, `hercules run --plan`,
  the `hercules_plan` MCP tool, `POST /api/plans`, `GET /api/runs/{id}/plan` and
  `/plan/diff`, a Plan tab in the UI; exports assert author criteria and note inferred
  ones. The daemon's `steps_done` counts finished steps.
- **Evidence**: screenshots, Set-of-Mark images, observations, decisions, step readings,
  console and network logs, `junit.xml` and `run.json` per run.
- **Test clouds**: BrowserStack, Sauce Labs and TestMu AI (formerly LambdaTest). Connect them
  on the tester UI's Integrations screen or with `hercules cloud connect` (keys saved in
  `~/.hercules/clouds.env`, mode 600; environment variables win). Run a passed run's exported
  test on a cloud from the run page or with `hercules cloud run`: web tests in the cloud's
  browsers (Sauce Labs through `saucectl`), native app tests on its devices, with the
  session's status and link on the run page. `hercules cloud bundle` and the UI's download
  write the test as a project with the cloud's CI configuration (`browserstack.yml`,
  `.sauce/config.yml`, `hyperexecute.yaml`). Native app runs on TestMu AI. The `cloud` extra
  installs the runner tools.
- **Telemetry**: anonymous events sent to PostHog (no person profiles, no GeoIP), on by
  default. Opt out with `--no-telemetry` on any command, `HERCULES_TELEMETRY=0`,
  `DO_NOT_TRACK=1` or `hercules setup --telemetry off`.
- **Install in one line**: `uvx testzeus-hercules` installs the `hercules` command, the
  browsers you choose and runs setup; a prompt in the docs lets a coding agent do the same.
  Python 3.13 to 3.15.
- **Browsers**: Chromium, Chrome, Edge, Firefox and WebKit, with Playwright's launch and
  context options (locale, time zone, signed-in state, headers, proxy, ...) in environments,
  exported code and cloud runs. Mobile web on Playwright device profiles; native Android and
  iOS apps through Appium.
- **Exploratory testing**: `hercules explore`, time-boxed bug hunts with reproduced findings.
- **Packaging and releases**: SPDX licence metadata; releases from version tags only, after the
  packages are checked and installed on every supported Python and the test suite passes;
  PyPI trusted publishing with attestations; the Docker image for amd64 and arm64 on Docker
  Hub (`testzeus/hercules`) with SBOM and provenance. CI runs the tests on every pull request.
