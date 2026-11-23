# Contributing to Hercules

Contributions of every size are welcome: bug reports, docs fixes, test cases that break
Hercules, and code. Open an issue first for larger changes so we can agree on the
approach. By contributing you agree that your work is licensed under the
[AGPL-3.0](LICENSE).

Read [Architecture](docs/guide/architecture.md) before you change the engine.

## Set up

You need Python 3.12 (the package supports 3.11 to 3.15), [uv](https://docs.astral.sh/uv/),
and Node.js 20 or newer if you work on the tester UI.

```bash
git clone git@github.com:YOUR_GIT_USERNAME/testzeus-hercules.git
cd testzeus-hercules
git remote add upstream https://github.com/test-zeus-ai/testzeus-hercules
uv sync --python 3.12 --all-extras
uv run python -m playwright install chromium
uv run hercules --help
```

`make help` lists the other shortcuts (`make install`, `make lint`, `make test`, `make ui`).

## Run the tests

Tests live in `tests/v2/`, one file per module or feature (`test_engine_step_graph.py`,
`test_daemon_api.py`, ...). The few tests at the top of `tests/` cover repository tooling.

The offline suite needs no model and no key:

```bash
uv run --python 3.12 python -m pytest -q -p no:warnings -m "not live" tests/v2 \
  tests/test_dependency_diet.py tests/test_backmerge_script.py
```

Browser tests use Playwright's Chromium. To use another Chromium or Chrome build, set
`HERCULES_TEST_CHROMIUM` to its executable. `make test` runs the same suite with
coverage, after `make lint`.

Tests marked `live` talk to real models and are skipped unless you turn them on:

- `HERCULES_LIVE_LLM=1` runs them against a real model server, chosen by the usual
  settings (`HERCULES_LLM_PROVIDER`, `HERCULES_LLM_BASE_URL`, `HERCULES_LLM_MODEL`).
- `HERCULES_LIVE_JEV=1` runs the tests that call JEV on OpenRouter. They need the key in
  `OPENROUTER_API_KEY` (or in the variable `HERCULES_JEV_KEY_ENV` names). JEV is a paid
  service.

```bash
HERCULES_LIVE_LLM=1 HERCULES_LIVE_JEV=1 uv run --python 3.12 python -m pytest -m live tests/v2
```

Run them when your change touches how runs talk to a model. The benches in `bench/`
measure end-to-end accuracy; see [bench/README.md](bench/README.md).

## Work on the tester UI

The UI's source is in `ui/` (React, TypeScript, Vite). With a daemon running
(`hercules serve` in some project):

```bash
cd ui
npm ci
npm run dev          # http://localhost:5173, proxies /api to the daemon
npm test -- --run    # vitest
npm run typecheck
```

The dev server reads the daemon's token from `~/.hercules/daemon.json` on the Node side,
so the browser never sees it. `make ui` (or `scripts/build_ui.sh`) builds the bundle into
`testzeus_hercules/ui/static/`, which the daemon serves and the wheel ships. The built
bundle is not committed.

## Code style

- Format with black, line length 200: `make fmt`. CI runs `make lint`, which checks it
  (`black --target-version py311 -l 200 --check`).
- Keep modules small and focused, use type annotations, and prefer immutable data
  (frozen dataclasses, pydantic models).
- Keep `cli/main.py`, `mcp/` and package `__init__` files cheap to import; import heavy
  modules where they are used.
- Use [conventional commit](https://www.conventionalcommits.org/en/v1.0.0/) messages,
  for example `fix(engine): ...` or `docs: ...`.

## Design rule: no coded deciders

Every step reading and every decision comes from a model. Do not add rules that read
step text (keyword matching, regular expressions over actions or outcomes) or that pick,
rank or drop page elements by matching words. When a model reads or decides badly, fix
the prompt, the information it gets (the snapshot, the element rows, the context), the
question it is asked, or the recovery around it.

Deterministic code is right for executing a structured payload (a browser action on a
chosen element, an HTTP request, a SQL query, a quality check) and for checking observed
facts (page text, URL, a response, measurements). When a model is unsure, the result is
`unverified` or the step recovers; code never guesses in its place.

## Keep the repository public-safe

This is a public repository:

- Never commit secrets: API keys, tokens, passwords, connection strings or `.env` files.
  Tests use obviously fake values; cases reference secrets as `{"secret": "NAME"}`.
- No private hostnames, internal tools, customer names or real personal data in code,
  tests, fixtures, docs or commit messages.
- Live tests read keys from the environment only; never print them.

## Changes to docs

User-facing behaviour is documented in `docs/guide/` and summarised in `README.md`.
Update both with your change, and keep command examples runnable (`hercules <command>
--help` is the reference for flags).

## Submit a pull request

1. Branch from the default branch: `git checkout -b my-change`.
2. Make your change with tests.
3. Run `make lint` and the offline tests.
4. Push to your fork and open a pull request. Fill in the template.

A maintainer reviews it once CI passes.

## Releases

A release is a version tag. Maintainers run, on an up-to-date `main`:

```bash
make release VERSION=2.1.0      # or 2.1.0rc1 for a pre-release
git push origin main 2.1.0       # the command make release prints
```

`make release` checks the version (PEP 440, newer than the current one, not a `.dev`
version), writes it into `pyproject.toml`, the Dockerfile and `uv.lock`, commits
`release: 2.1.0` and creates the annotated tag. It pushes nothing.

Pushing the tag starts the [Release workflow](.github/workflows/release.yml). Each step runs only
if the ones before it passed:

1. The tag must equal the version in `pyproject.toml`.
2. The tester UI and the sdist and wheel are built; `scripts/check_dist.sh` runs twine's strict
   check, checks the wheel carries the UI and schemas, and installs it in a fresh environment
   on Python 3.11 to 3.15 and macOS. The PyPI description gets absolute links pinned to the tag
   (`scripts/pypi_readme.py`), so pictures and links work on PyPI.
3. The linter and the offline test suite run.
4. The files are published to PyPI with trusted publishing, which also attaches signed
   attestations of where they were built.
5. The GitHub release is created with the release notes and the files attached (marked as a
   pre-release for `a`, `b` and `rc` versions).
6. The Docker image is built natively for amd64 and arm64 and published to Docker Hub as
   `testzeus/hercules`, tagged with the version, `major.minor` and `latest` (pre-releases get
   only their version), with SBOM, provenance and a build attestation. The Docker Hub login is
   checked in the first step, so a missing or rejected token stops the release before anything
   is published.

Run the workflow by hand from the Actions tab to do every check without publishing anything: it
also logs in to Docker Hub and asks PyPI to accept the workflow as its trusted publisher, so a
broken token or publisher setup shows up before a release.
`make build` runs step 2's build and check locally.

One-time setup, done by a repository admin:

- **PyPI trusted publisher**: on PyPI, *testzeus-hercules → Manage → Publishing → Add a new
  publisher → GitHub*, with owner `test-zeus-ai`, repository `testzeus-hercules`, workflow
  `release.yml` and environment `pypi`. In the repository settings, add required reviewers to the
  `pypi` environment if releases should wait for an approval. No PyPI token is stored; delete the
  old `PYPI_API_TOKEN` secret once a release has gone through.
- **Docker Hub**: the `DOCKERHUB_USERNAME` variable and the `DOCKERHUB_TOKEN` secret (an access
  token with read and write access to `testzeus/hercules`). A tagged release stops without them.
- **Private vulnerability reporting**: enable it under *Settings → Code security*, so the
  process in [SECURITY.md](SECURITY.md) works.
