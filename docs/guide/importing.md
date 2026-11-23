# Importing existing tests and specs

> **Watch it:** [Import v1 projects, OpenAPI specs and requirement documents (1:28)](videos.md#11-import-v1-projects-openapi-specs-and-requirement-documents).

`hercules import` turns material you already have into Hercules test cases in your
project's `.hercules/` folder.

```bash
hercules import v1 DIR          # a Hercules v1 project
hercules import openapi SPEC    # an OpenAPI 3.x spec
hercules import doc FILE        # a requirements document
```

Every importer takes these options:

| Option | Meaning |
|---|---|
| `--project DIR` | Project to import into (default: the current directory) |
| `--dry-run` | Show what would be written and write nothing |
| `--force` | Replace files that already exist (otherwise they are skipped) |

Importers only write inside `<project>/.hercules/`. They never copy a secret value: keys
that look like credentials become secret references by name, and the report lists the
environment variables to set before running.

The report lists every file written (or that would be written) and everything skipped,
with the reason. For example:

```text
Import of the v1 project /path/to/old-project
Converted (3):
  case  .hercules/cases/valid-user-signs-in.json  (from opt/input/login.feature)
  case  .hercules/cases/locked-accounts-are-refused-1.json  (from opt/input/login.feature)
  env   .hercules/envs/default.json  (from .env)
Skipped (1):
  .env: 4 v1 engine setting(s) not carried over (...)
Secrets referenced by name (set these environment variables before running): PASSWORD
```

Exit codes: `0` when the import ran, `1` when it ran but converted no case and skipped
something, `2` when the source cannot be imported at all.

## v1 projects

```bash
hercules import v1 path/to/v1-project --dry-run
hercules import v1 path/to/v1-project
```

`DIR` is the v1 project folder that holds `opt/`, or `opt/` itself.

| Option | Meaning |
|---|---|
| `--base-url URL` | Base URL for the environment (default: `BASE_URL` in `.env`, else the first URL in the feature files) |
| `--env-name NAME` | Name of the environment to write (default `default`) |
| `--env-file FILE` | Read variables from this file instead of `.env` |

What is converted:

- **Feature files** (`opt/input/*.feature`): every scenario becomes a case in
  `.hercules/cases/<id>.json`, tagged `imported:v1`. A `Scenario Outline` becomes one case
  per `Examples` row. `Rule` blocks are accepted, and doc strings and data tables are
  folded into the step text. `Then` lines become `verify` steps, as in
  [Gherkin files](writing-tests.md#gherkin-feature-files).
- **Test data** (`opt/test_data/*`): v1 gave every scenario all of its test data, so flat
  key/value files (a JSON object of plain values, or `KEY=VALUE` lines) become
  `test_data` entries on every case. Keys that name a credential (password, token, API
  key and similar) become secret references; the variable name is the key in capitals.
  Other files are copied to `.hercules/data/` and attached as `<name>_file` entries to the
  cases whose steps mention them.
- **`.env`**: the base URL and the names of the secrets the cases use become
  `.hercules/envs/<name>.json`. The imported cases refer to it with `environment.ref`.
- **Security features** (the feature or scenario name or tags say "security"): each
  scenario becomes a case with a security scan step whose tag comes from the scenario's
  topic (CVEs, sensitive data exposure, XSS, SQL injection, ...). See
  [Security scans](security-scan.md#from-v1).

What is not carried over: v1 engine settings (model, browser, logging and similar
switches), plain variables from `.env` (add them to a case's `test_data` if a step needs
them), sub-folders of the input and data folders, and symbolic links. The report says
which.

After the import:

```bash
export PASSWORD=...                    # each secret the report lists
hercules doctor
hercules run .hercules/cases/valid-user-signs-in.json
```

Every way of running a case (`hercules run`, the daemon, the UI, a coding agent) uses
the saved environment the case refers to. Pass `--env NAME` to pick another one, or
`--base-url URL` to override the address for one run.

Steps are imported as written. v1 steps that relied on the agent guessing, such as "I
open the sign in page" with no address, may need small edits before they pass; see
[Tidy up the steps](migrating-from-v1.md#5-tidy-up-the-steps). For how each v1 command,
flag and setting maps to v2, see [Command changes](migrating-from-v1.md#command-changes).

## OpenAPI specs

```bash
hercules import openapi openapi.yaml --base-url http://localhost:8000
```

| Option | Meaning |
|---|---|
| `--base-url URL` | Where the API is served (default: the spec's first server) |
| `--tag TAG` | Only operations with this tag. Repeat for several tags. |

Writes one case per operation of an OpenAPI 3.x spec (`.yaml`, `.yml` or `.json`),
tagged `kind:api` and `openapi` plus the operation's tags. Each case:

- sends the operation's request, with path and required query parameters filled from
  the spec's examples, defaults, or placeholders by type, and a JSON body built the same
  way;
- turns path parameters into `test_data` entries, so you edit them in one place;
- turns security schemes into secret references by name;
- checks the first documented 2xx status and, when that response has a JSON body, its
  schema.

Only references inside the spec (`#/...`) are resolved; external references are listed
in the report. Long request bodies are shortened and the report says which to complete.

The generated steps use the API step format described in
[API and SQL steps](writing-tests.md#api-and-sql-steps).

## Requirement documents

```bash
hercules import doc requirements.md --draft
```

| Option | Meaning |
|---|---|
| `--draft` | Ask the local model to propose draft cases from the document. Without it, or when the model cannot answer, a template case with TODO markers is written. |

Reads Markdown, plain text, PDF and Word (`.docx`) files. The extracted text is saved to
`.hercules/data/docs/<name>.txt`. Files over 25 MB, PDFs over 500 pages and Word
documents with a DTD are refused.

Every case written from a document has `"status": "draft"` and the tags `imported:doc`
and `draft`. Review the steps, fix anything the model got wrong, and set the status to
`ready`. `hercules run` refuses a draft unless you pass `--allow-draft`, and a draft
started through the daemon waits for approval before it runs; see
[Draft and ready cases](writing-tests.md#draft-and-ready-cases).

`--draft` sends the document's text to the local model and respects the egress policy:
with a model server on your machine nothing leaves it, and under the policy `local` a
remote endpoint is refused. JEV is not used. Run `hercules doctor` first.
