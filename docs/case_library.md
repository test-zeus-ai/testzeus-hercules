# Case library: one library, per-version suites

This convention keeps all E2E cases in a single library and *assembles* a
runnable project base for the version (or selection) you want to exercise. It
answers three questions continuously:

1. What is actually written? (the library)
2. What will run for version V? (the assembled base)
3. What cannot run right now, and why? (`blocked.json`, a number someone can watch)

All tooling is offline and stdlib-only: `scripts/assemble_version.py`,
`scripts/lint_gherkin.py`, `scripts/e2e_gate.py`.

## Directory layout

```
opt/cases/
  api/
    auth/
      1.0.feature            # cases introduced by version 1.0
      2.0.feature            # cases introduced by version 2.0
      test_data/             # optional; copied verbatim into every assembly
    payments/
      1.0.feature
  web/
    smoke/
      10.0.feature           # integer ordering: 10.0 sorts after 9.0
  regression.txt             # optional regression manifest (see below)
  known_failures.txt         # optional waiver list (see below)
```

Rules:

- Case type directories (`api`, `web`, `ui`, ...) are configurable via
  `--types` (default `api,web,ui`). Type names must be simple names — letters or
  digits first, then letters/digits/`.`/`_`/`-`; anything with path separators or
  a leading dot aborts the assembly.
- One `<version>.feature` per version per module. The file name must match
  `v?<digits>(.<digits>)*.feature` (a leading `v` is tolerated: `v2.0.feature`).
- A version runs **only its own file's cases** — there is no accumulation.
  Cross-release coverage belongs in the regression manifest.

## Blocked cases

A case that cannot run right now is marked at the source, directly above the
scenario, with exactly two lines (a Gherkin tag cannot carry the reason text):

```gherkin
@blocked:fixture
# blocked: test card pool is not provisioned in this environment
Scenario: PAY-CHARGE-02 charge a saved card
  Given a saved card
  ...
```

Categories are free-form but should stay a small set (`tooling`, `fixture`,
`product`, `env` are good starting points). Excluded cases are printed during
assembly and recorded in `opt/.run/<target>/blocked.json` — the file is written
**even when nothing is blocked**, so "file missing" always means "the assembler
never ran", never "nothing was excluded".

Malformed lookalikes **abort the assembly** instead of being tolerated:

- `@block:x` or any `@block*` tag that is not `@blocked:<category>`;
- two tags on one line when a blocked lookalike is among them (`@blocked:fixture @wip`;
  a plain `@ui @desktop` line is an ordinary tag and is kept as-is);
- a `@blocked:` tag without its `# blocked:` reason directly below (and the
  pair must sit directly above the `Scenario:` line);
- a `@blocked:`/`# blocked:` pair separated from its `Scenario:` line by a blank
  line — the detached pair would be emitted verbatim and the case would run;
- a reason without a tag above it;
- a legacy free-form `# not executable ...` comment.

Tolerating any of these is what recreates permanently red runs. Blocked cases
are stripped from the assembled output (the runner splits scenarios at
`Scenario:` lines, so leftover tag lines would leak into the previous
scenario's text).

## Assembling

```bash
# everything introduced by 10.0
python scripts/assemble_version.py --version 10.0

# an explicit hand-picked set
python scripts/assemble_version.py --regression                 # opt/cases/regression.txt
python scripts/assemble_version.py --regression path/to/manifest.txt

# useful flags
python scripts/assemble_version.py --version 10.0 --types api,web
python scripts/assemble_version.py --version 10.0 --dry-run     # print the plan, write nothing
python scripts/assemble_version.py --version 10.0 --include-blocked   # assemble blocked cases anyway
python scripts/assemble_version.py --library opt/cases --run-root opt/.run
```

Output (exactly the layout the runner's bulk loop iterates: `tests/<folder>/input/<folder>.feature`
plus `tests/<folder>/test_data`):

```
opt/.run/10.0/
  tests/api-auth/input/api-auth.feature
  tests/api-auth/test_data/...
  tests/web-smoke/input/web-smoke.feature
  blocked.json
```

Fail-safe behavior: an assembly that produces zero suites aborts (a mistyped
version must not silently produce an empty run).

## Regression manifest

`opt/cases/regression.txt`, one entry per line:

```
# <type>/<module> :: <scenario-title substring>   (TC IDs recommended)
api/auth :: AUTH-LOGIN-01
web/smoke :: WEB-SMOKE-01
```

- An entry that matches **nothing aborts the assembly**. A regression set that
  quietly loses a renamed or deleted case still reports green — that is the one
  outcome it must never produce. The error lists the scenario titles that do exist.
- An unknown module aborts, listing the modules that exist.
- An entry whose matches are all blocked cases aborts too (regression coverage
  must not point at excluded cases; `--include-blocked` does not override this).
- When merging picks the same scenario title from several versions, the first
  occurrence is kept and the duplicates are counted in the assembly summary.
- Output goes to `opt/.run/regression/`.

## Known failures (waivers) — two-way discipline

`opt/cases/known_failures.txt` lists one substring pattern per line (`#`
comments allowed). A pattern matches `classname::name` only when the character
right after the match is not a letter, digit, or hyphen: exact case IDs match,
a class prefix followed by the `::` separator matches, but a short waiver such
as `AUTH` does **not** green an unrelated `AUTH-OTHER-99`. It is consumed by
the gate, **not** by the assembler, and it is enforced two-way:

- a listed case that **fails** is waived (counted separately, reported as waived);
- a listed case that **passes** fails the gate — a stale waiver must be deleted.

That second direction is the only thing that keeps a waiver list from rotting.
Waive categories of flakiness, not individual reds you don't want to see.

## Judging results: the E2E gate

The runner's own exit code is not a reliable verdict signal. The gate judges
the results tree instead:

```bash
python scripts/e2e_gate.py --version 10.0
# or, pointing directly at a results directory:
python scripts/e2e_gate.py --results-dir opt/.run/10.0/results
```

The gate fails on any `<failure>`, any `<error>`, any `<skipped>` (E2E forbids
skip), and on **zero scanned cases** — a crashed first suite or a mis-assembled
selection also produces "zero failures".

Outputs, written next to the results directory whenever a verdict is reached:

- `e2e-summary.json` (schema `case-library-e2e-summary/v1`): totals, unwaived
  failures, waived cases, stale waivers, and the blocked-exclusion counts
  reported from `blocked.json`;
- `e2e-junit.xml`: all scanned cases merged into one report for CI consumption,
  with waived cases annotated. The merged report intentionally keeps the raw
  failure rows for waived cases; the gate's exit code is the authoritative
  verdict.

Exit codes:

| code | meaning                                                              |
|------|----------------------------------------------------------------------|
| 0    | pass                                                                 |
| 1    | failure (unwaived failure/error/skip, stale waiver, zero cases)      |
| 2    | could not judge (missing results dir, corrupt XML, unreadable blocked.json) |

## Linting the library

```bash
python scripts/lint_gherkin.py opt/cases            # informational, always exits 0
python scripts/lint_gherkin.py opt/cases --strict   # warnings and errors become fatal
```

Checks scenario titles, empty steps, duplicate titles within a file, the
blocked-marker rules, and — via `--manifest opt/cases/regression.txt` (the
default) — that every manifest entry still resolves to a scenario title.
Syntax is checked with `gherkin-official` when installed; otherwise a
regex-based structural lint runs and says so.

## Quickstart

```bash
# 1. write a case under the version that introduces it
$EDITOR opt/cases/api/auth/2.0.feature

# 2. lint it (informational)
python scripts/lint_gherkin.py opt/cases

# 3. assemble and inspect the plan
python scripts/assemble_version.py --version 2.0 --dry-run

# 4. assemble for real; blocked.json tells you what could not run
python scripts/assemble_version.py --version 2.0

# 5. run the base with the runner's bulk mode, then judge the results tree
python scripts/e2e_gate.py --version 2.0 && echo GREEN
```
