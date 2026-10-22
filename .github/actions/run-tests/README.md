# TestZeus Hercules GitHub Action (`run-tests`)

A composite GitHub Action that runs [TestZeus Hercules](https://github.com/test-zeus-ai/testzeus-hercules)
agentic tests in your repository's CI: single feature, bulk `tests/` layout, named
suite, or parallel scenarios — with report artifacts and an optional PR results
comment.

```yaml
- uses: actions/checkout@v4
  with:
    persist-credentials: false   # Hercules never pushes; keep the token out of .git/config
# Pin to a full commit SHA of test-zeus-ai/testzeus-hercules (see Security notes).
- uses: test-zeus-ai/testzeus-hercules/.github/actions/run-tests@<commit-sha>
  with:
    feature: tests/login.feature
    llm-config-ref-key: openai_gpt
    env-vars: |
      LLM_MODEL_API_KEY=${{ secrets.LLM_API_KEY }}
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `feature` | *(empty)* | Path to a single `.feature` file (copied to `opt/input/test.feature`). Used by single and parallel modes. |
| `bulk` | `false` | Run the whole `tests/` directory layout (`tests/<name>/input/<name>.feature`). |
| `suite` | *(empty)* | Run a named suite via `hercules-suite <name>` (highest precedence). |
| `suite-config` | `.hercules/suites.yaml` | Suites YAML used with `suite`. |
| `max-parallel` | `1` | Parallel scenarios: passed to `run_manager --max-parallel` in parallel mode, or as `--max-parallel` override in suite mode. `> 1` with `feature` switches to parallel mode. |
| `runner` | `docker` | `docker` (pull `image`) or `uv` (installs Hercules from the action's own checkout with `uv sync --project <action repo>` and runs it with `uv run --project` in your workspace). |
| `image` | `testzeus/hercules:latest` | Docker image (Docker Hub) used by `runner: docker`. Must contain Hercules + Playwright browsers (see notes below). |
| `llm-config` | `agents_llm_config.json` | Path to the LLM config file in your repo; exported as `AGENTS_LLM_CONFIG_FILE`. If the file does not exist, it is skipped (configure LLMs via `env-vars`, e.g. `LLM_MODEL_NAME` / `LLM_MODEL_API_KEY`). |
| `llm-config-ref-key` | *(empty)* | Provider key inside the config file (e.g. `openai_gpt`, `gemini`); exported as `AGENTS_LLM_CONFIG_FILE_REF_KEY`. **Required together with `llm-config`** — Hercules validates that both are set. |
| `base-url` | *(empty)* | Convenience only: when non-empty, passed to the Hercules run (docker or uv) as the `APP_URL` env var. Hercules itself takes the target URL from the feature steps ("When I navigate to ..."), so put the URL in the feature. |
| `reports` | `true` | Enables the opt-in reporting features for the run: `ENABLE_RUN_REPORTING` (`Hercules_Test_Report.md`), `ENABLE_FAILURE_BUNDLES`, `ENABLE_RUN_HISTORY` (DB at `opt/hercules_history.db` unless `HERCULES_HISTORY_DB` is set via `env-vars`) and `ENABLE_FAILURE_CLASSIFICATION`. Set `false` to control them yourself through `env-vars`. |
| `blocking` | `true` | `true` runs Hercules with `STRICT_EXIT_CODE=true` so test failures fail the job. `false` runs it with `EXIT_CODE_COMPAT=1` so failures do **not** fail the job (legacy exit-code behaviour). |
| `python-version` | `3.12` | Python version used by `runner: uv`. |
| `env-vars` | *(empty)* | Multiline `KEY=VALUE` lines passed through to Hercules (secrets welcome). `#` comments and blank lines are ignored. |
| `artifact-name` | `hercules-results` | Name of the uploaded artifact. |
| `pr-comment` | `false` | Post/update a results summary comment on the PR (pull_request events only). |
| `pr-comment-failure-details` | `false` | Include redacted, truncated failure messages in the PR comment. Off by default: raw failure text can contain secrets/private data and PR comments are visible to everyone who can read the PR. |
| `github-token` | `${{ github.token }}` | Token for the PR comment. Needs `pull-requests: write` in workflow permissions. |

Mode precedence: `suite` > `bulk` > `feature`. Exactly one should be set; with
`max-parallel > 1` and `feature`, the feature runs through
`python -m testzeus_hercules.orchestrator.run_manager`.

## Outputs

| Output | Description |
| --- | --- |
| `outcome` | `success` or `failure` of the test run. |
| `junit-path` | Newest JUnit XML found (`opt/output/*/*_result.xml`, which includes the suite/parallel merged XML in `opt/output/merged/`, or bulk-mode `tests/*/output/*/*_result.xml`). Empty if none. |
| `report-path` | Newest `Hercules_Test_Report.md` (written when `reports: true` or `ENABLE_RUN_REPORTING=true`). Empty if none. |

## What the action produces

- **Artifacts** (always uploaded, 14-day retention, `if-no-files-found: ignore`):
  `opt/output/` (JUnit XML, HTML report, and — with `reports: true` —
  `Hercules_Test_Report.md` and failure `bundles/`; suite/parallel runs write their
  merged JUnit XML to `opt/output/merged/`), `opt/proofs/`, `opt/hercules_history.db`
  (run history SQLite, with `reports: true`), bulk-mode `tests/*/output` and
  `tests/*/proofs`, and the suite/parallel scenario workspaces under
  `.hercules/suite_runs/` and `.hercules/parallel_*/` (only failed scenarios keep
  theirs).
- **Bulk mode** runs every `tests/<name>/input/<name>.feature` under the repository
  root (`PROJECT_SOURCE_ROOT` is set to the workspace unless you pass one in `env-vars`).
- **PR comment** (with `pr-comment: true`, pull_request events): a status header
  (PASSED/FAILED), totals, a per-case table (status, duration, first 50 cases; failure
  messages only with `pr-comment-failure-details: true`, then redacted and truncated to 300 chars), and a link to the workflow run. The
  comment is identified by the `<!-- hercules-results -->` marker and **updated in
  place** on re-runs instead of duplicated.
- Any produced JUnit XML/HTML/Markdown report file paths are available via the
  action outputs for downstream steps (e.g. test reporting actions).

## Blocking semantics (`STRICT_EXIT_CODE` / `EXIT_CODE_COMPAT`)

With `blocking: true` (default) the action runs Hercules with `STRICT_EXIT_CODE=true`,
so it exits non-zero when tests fail and the job fails — use this to gate PRs.
(Plain `python -m testzeus_hercules` keeps its legacy exit code unless that flag is set.) Set `blocking: false` to run Hercules with
`EXIT_CODE_COMPAT=1`, which restores the legacy "always exit 0" behaviour: results
and artifacts are still produced, failures show up in the PR comment/artifacts,
but the job stays green. `hercules-suite` and `run_manager` exit non-zero on
failures by default too, but they also honour `EXIT_CODE_COMPAT=1` (so `blocking: false`
keeps the job green in suite/parallel mode as well).

## Requirements

- **LLM config**: an `agents_llm_config.json` in your repo plus `llm-config-ref-key`,
  **or** direct LLM env vars via `env-vars` (`LLM_MODEL_NAME`, `LLM_MODEL_API_KEY`, ...).
  API keys should come from secrets, e.g. `env-vars: LLM_MODEL_API_KEY=${{ secrets.LLM_API_KEY }}`.
- **`runner: docker`** (default) pulls `testzeus/hercules:latest` from Docker Hub. The
  image must contain a Hercules version that provides the `hercules-suite` entry point
  when you use `suite`; or build it yourself with
  `docker build -t <you>/hercules .` and pass `image:` accordingly, or use
  `runner: uv`. On self-hosted runners, Docker must be available and able to mount
  `$GITHUB_WORKSPACE` at `/workspace`.
- **`runner: uv`** installs from source in the job (needs network): `astral-sh/setup-uv@v5`,
  `uv sync --project <action checkout> --all-extras`, `uv run --project <action checkout> --
  playwright install --with-deps`. The action's checkout (the repository root above
  `.github/actions/run-tests`) is the Hercules project, so this works from any consumer
  repository. Slower per run; no image required.
- **`llm-config` with `runner: docker`**: only the workspace is mounted, so use a path
  inside the repository (relative, or absolute under `$GITHUB_WORKSPACE`); other absolute
  paths are rejected.
- Optional runtime log file: pass `LOG_FILE_PATH=/workspace/hercules.log` (or any
  path inside the workspace) via `env-vars` so it lands in the artifact.

## Example workflow

See [`.github/examples/hercules-workflow.yml`](../../examples/hercules-workflow.yml)
for a full example with one blocking job and one advisory (`blocking: false`,
`pr-comment: true`) job.

## Security notes

- Workflow inputs are never interpolated into shell scripts: they reach the step as
  environment variables and the Hercules command is executed as an argument array
  (`docker run --entrypoint <venv binary> ... <args>` / `uv run -- <args>`), so quotes or
  shell metacharacters in `suite`, `feature`, `suite-config`, ... cannot change the command.
  Values starting with `-` are rejected for positional inputs.
- `env-vars` secrets are passed to the container by NAME (`docker run -e NAME`), never on the
  command line. Names must be plain identifiers and protected variables (`PATH`, `LD_*`,
  `BASH_ENV`, `GITHUB_*`, ...) are ignored.
- PR comments never contain raw failure text unless you opt in with
  `pr-comment-failure-details: true`; even then credentials/tokens (and the values of your
  `env-vars` secrets) are masked and `@mentions` are defused.
- The PR comment is only updated in place when the existing marker comment was authored by
  `github-actions[bot]` (the identity of the default `github.token`); a marker planted by any other
  user is ignored and a new comment is created instead. If you pass a `github-token` from another
  identity (PAT / GitHub App), each run therefore posts a fresh comment.
- Always check out with `persist-credentials: false` (see the snippet at the top and the example
  workflow) so the workflow token is not left in `.git/config` for later steps.
- The action uses third-party actions pinned by **tag** (`astral-sh/setup-uv@v5`,
  `actions/upload-artifact@v4`), as do the examples (`actions/checkout@v4`). Tags are mutable;
  for supply-chain safety pin them (and this action itself) to full commit SHAs and let
  Dependabot propose updates.
