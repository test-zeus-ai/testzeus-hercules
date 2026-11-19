# Plans: what a run will do, checked before it starts

> **Watch it:** [Review the plan before the run starts (1:57)](videos.md#3-review-the-plan-before-the-run-starts).

Before the browser starts, Hercules turns your case into an explicit **plan**: for every
step, the actions it will take, what the page should show once each action is done
(success criteria), and what is risky about it. The plan is checked for coverage and
linted, can be reviewed and edited, and the run is then executed against it: each
criterion is checked on the page, and every change the run makes to the plan is recorded.

A run therefore no longer relies only on the test being written well. A step that says
"click New" now also says, in the plan, "the New Record dialog is open"; when the click did
not take, Hercules notices and recovers, instead of failing two steps later in a way that
is hard to read.

Like everything else in Hercules, the plan is made by models (the local model reads the
case, the decider audits it); code only checks its structure, executes it and checks what
the page shows.

## What is in a plan

```text
Plan of records (5 plan steps; model, generated; model my-model, prompt case-7)
1. I open "/records.html"
   1.1 navigate: open /records.html
        ~ [inferred] the address contains "/records.html" (url_matches)
2. I click the "New" button
   2.1 click: click "New" button
        ~ [inferred] the "New Record" dialog is open (element_state)
3. I enter "Acme Corp" in the "Record Name" field
   3.1 fill: fill "Record Name" with "Acme Corp"
4. I click the "Save" button
   4.1 click: click "Save" button  [risk: irreversible - saves the record]
        ~ [inferred] the "New Record" dialog is closed (element_state)
5. I should see "was created"
   5.1 verify: check: I should see "was created"
        ✓ [author] the page shows "was created" (text_visible)
Coverage (jev-openrouter): 5 step(s) audited; gaps: none
Lint: 0 error(s), 1 warning(s)
  WARNING unconfirmed_risk (fact) step 4: step 4 cannot be undone (saves the record), and the run does not ask before it
```

For each **source step** (a step of your case) the plan has one or more **plan steps**,
numbered `<step>.<n>`:

| Field | Meaning |
|---|---|
| `kind` | `navigate`, `click`, `fill`, `select`, `upload`, `download`, `check`, `uncheck`, `hover`, `press`, `wait`, `api`, `sql`, `quality`, or `verify` (the step's expected outcome, checked) |
| `target`, `value`, `url` | As the model read them. Placeholders such as `{{password}}` stay placeholders: secret values never appear in a plan |
| `needs` | Steps whose field must be set first (a region list that depends on the country) |
| `risk` | `destructive`, `irreversible` or `external_side_effect`, with the model's reason |
| `criteria` | What the page should show once this plan step is done |
| `read` | `case` (read before the run), `page` (the model could not read it before the run; it is read on the page when it runs), `service` (an API, SQL or quality payload) |

The schema is published as `testzeus_hercules/cases/schemas/plan.schema.json`.

## Success criteria

Every criterion has an **origin**:

- **author**: the case's own words state it: the step's expected outcome, or words of
  the step itself. An author criterion that does not hold fails the step, like any check.
  The model may call a criterion author only when its text is words of the step; anything
  else is recorded as inferred.
- **inferred**: the model's expectation of what certainly follows from the action: after
  clicking "New" a dialog for the new record is open; after "Save" in a dialog the dialog is
  closed. An unmet inferred criterion drives recovery (below); when it still does not hold
  it is a **warning** on the step, and it fails the run only in strict mode.

Each criterion has a check form, evaluated on the settled page right after its plan step:

| Check | Fields | How it is checked |
|---|---|---|
| `text_visible` / `text_absent` | `expected` | The page's text, within a short wait (as a step's own checks) |
| `url_matches` | `expected`, `match` | The address contains (or equals) it |
| `field_value` | `target`, `value` | The field whose label is the target holds the value |
| `element_state` | `target`, `state` | `open` / `closed`: a dialog (modal or not) whose name holds the target is open; `visible`, `hidden`, `enabled`, `disabled`, `checked`, `unchecked`, `expanded`, `collapsed`: the element named exactly so (or the one element whose name holds it) |
| `count` | `target`, `count`, `op` | How many visible elements show the text (`eq`, `gte`, `lte`) |
| `judged` | `expected` | The decider judges it by meaning on the page's text and, when screenshots may be sent, the screenshot (`visual` criteria on the screenshot only) |

When the element a criterion names cannot be found unambiguously, the criterion is judged
by the decider (with the page's controls and their states), never guessed in code. A
judgement that is not confident either way is `unverified`.

The step's expected outcome stays checked exactly as before (the plan lists its checks as
author criteria of a `verify` plan step); the results of those checks are reported as the
results of the criteria.

## Coverage audit

The plan comes from the local model; an independent check, the decider (JEV in hybrid
mode, the local model in pure mode), is asked one yes/no question per step: does the plan
for this step carry out every action its words ask for and check every outcome they state?
Steps the audit finds not covered are read again by the local model in one call (told that
part of the step may be missing) and audited again. A gap that remains is a lint error.
API, SQL and quality payloads are not audited: they run as written.

## Lint

Problems with the test itself, found before the browser starts:

| Code | Found by | Example |
|---|---|---|
| `suspicious_value` | the model | a value with a stray bracket (`"[Acme"`), markup such as a Markdown link where a plain value is expected, a value that looks cut off |
| `ambiguous_reference` | the model | a control or record that several could match |
| `unclear_outcome` | the model | an expected outcome that cannot be checked as written |
| `missing_secret` | fact | test data is `{"secret": "APP_PASSWORD"}` and `APP_PASSWORD` is not set |
| `credential_in_url` | fact | `https://admin:pw@app.test/` in a step: use a secret reference instead |
| `unresolved_placeholder` | fact | the plan uses `{{company}}` and no test data, built-in or earlier step provides it |
| `unsupported_step` | fact | a database query step and no database connection in the case's test data |
| `unconfirmed_risk` | fact | a step the plan flags destructive or irreversible, and the run does not ask before such actions (`confirm_destructive`) |
| `coverage_gap` | fact | the coverage audit still finds a step not covered after it was read again |

Findings are shown with the plan, in the run's hints, in the UI and in MCP results. A
finding of severity `error` stops the run before the browser starts in **strict mode**.

## Strict mode

Turn it on per run (`hercules run --strict`, the `strict` run option in the UI, the API or
MCP) or for a project:

```yaml
# .hercules/config.yaml
plan:
  strict: true      # or HERCULES_PLAN_STRICT=1
```

In strict mode a plan with lint errors is refused before the browser starts (the run ends
with `error` and the first finding), and an inferred criterion still unmet after recovery
makes its step `unverified`, which fails the run. Without strict mode both are warnings.

## Review and approval

Runs started from the daemon (UI, API, MCP) take `plan_review`:

- by default (the setting `plan.review: errors`), a ready case's plan runs as made when it
  is clean, and waits in `needs_plan_approval` when its lint found errors (a strict run
  is refused instead, without asking);
- `auto`: the plan runs as made, lint errors or not;
- `ask` (the default for drafted cases): the run waits in `needs_plan_approval` until
  someone approves, edits or rejects its plan. The browser has not started yet.

A run started from a terminal without the daemon has nobody to ask: it runs the plan and
prints its lint (use `--strict` to stop on lint errors).

An approved plan is stored under `.hercules/plans/` and reused, without asking again, for
as long as the case and the model stay the same (its key is the SHA-256 of the case, the
reading prompt's version and the model id). A generated plan is stored the same way, so a
run of an unchanged case makes no planning calls at all.

Edits a reviewer can make: the text of a plan step (the model reads the new text again
into its kind, target and value), criteria (remove one, reword it, which makes it judged
by its words, or switch it between author and inferred) and the order of the steps. The
edited plan is checked against the case (same case version, every step planned) before it
runs, and every change is recorded as an amendment from the user.

## Execution against the plan, and amendments

After each planned action, on a settled page, its criteria are checked:

- an **author** criterion that fails fails the step (`AssertionFailed`);
- an **inferred** criterion that is unmet drives one recovery: the step is continued on the
  page with the expectation ("expected: the New Record dialog is open"), so an action that
  did not take effect is done again or a missing part of the step is planned. An action
  the plan flags as destructive, irreversible or with an external side effect is never
  repeated on its own. The criterion is checked again after the recovery; still unmet, it
  is a warning (strict mode: the step is unverified);
- with `confirm_destructive`, a click the plan flags destructive or irreversible is asked
  about, as are clicks whose names look destructive.

Every change to the plan while the run runs is an **amendment**, never silent:

| Kind | When |
|---|---|
| `continuation` | the step needed more on the page than its reading (a menu entry that only appeared, a submit) |
| `reread` | acting on the plan failed and the step was read again on the page |
| `recovery` | an unmet inferred criterion drove a continuation |
| `reorder` | a field depends on a field a later step sets, so steps ran in another order |
| `run_first` | at run time, a field could not be set until a later field step ran |
| `reformat` | a field rejected a value (when it was entered, or when a later step submitted the form) and it was entered the way the field asks for |
| `substitute` | the field cannot take the test's value at all (too long for it, a form it never takes): other sample data of the same kind that fits was entered. Only when no later step names the value, it is not a secret, and the run is not strict |
| `edit` | a person changed the plan (review) or a step's text (debugging) |

Each amendment has the plan step, before, after, the reason, its source (`model`, `user`
or `engine`) and the time. The step's `plan.jsonl` keeps the details (`stage`: `criterion`,
`recover`, `amend`, besides the existing stages).

## Where to find it

- `plan.json` beside `run.json`: the plan as the run used it, with every criterion's result
  and every amendment. `RunResult.plan` summarises it (criteria passed, failed, unverified,
  not run, recoveries, amendments, lint counts, planning time).
- `hercules run` prints one plan line after the result:
  `Plan (model): 5 plan steps; 4 criteria (1 author, 3 inferred): 4 held, 0 not met, 0 not run, 1 drove a recovery; 1 amendment(s); lint 0 error(s), 1 warning(s); planning 1.3 s (plan.json)`.
- The UI's run page has a **Plan** tab: plan steps with their state, criteria with author
  and inferred badges and results, risks, lint, and amendments shown as before → after.
- The daemon API: `GET /api/runs/{id}/plan`, `GET /api/runs/{id}/plan/diff` (with
  `against=<run_id>` the difference between two runs' plans; without it, the plan against
  the run's amendments), `POST /api/plans` (make a plan without running it), `GET
  /api/cases/{name}/plan` (the latest plan of a saved case).
- Exports: `hercules export` writes the author criteria the run checked as assertions and
  lists the inferred ones as comments ("plan (inferred, not asserted)").

## From the command line

```bash
hercules plan .hercules/cases/records.json            # the plan as text, with coverage and lint
hercules plan .hercules/cases/records.json --json > plan.json
hercules plan .hercules/cases/records.json --approve  # store it as approved for this case version and model
hercules plan CASE --strict                           # exit 1 when the plan has lint errors (for CI)
hercules run CASE --plan plan.json                    # run an (edited) plan; it must be a plan of this case
hercules run CASE --strict
```

`hercules plan` makes the plan exactly as a run does (reading, coverage audit, lint) and
prints what it cost (local model calls, decider calls and their cost). `--fresh` ignores a
stored plan; `--no-coverage` skips the audit.

## From a coding agent (MCP)

- `hercules_plan` with `test_cases` (one case), `gherkin` or `case_path`: the plan and its
  lint, without running (`approve: true` stores it as approved). With `run_id`: the plan a
  run used, its results and amendments (`against`: another run to compare with).
- `hercules_run` takes `plan_review`, `strict` and `plan` (an edited plan from
  `hercules_plan`).
- A run in `needs_plan_approval`: read its plan with `hercules_plan` (`run_id`), then
  `hercules_approve` with `approve`, `reject`, or `edit` and `plan` (the edited plan).

## Settings

```yaml
plan:
  strict: false     # HERCULES_PLAN_STRICT
  review: errors    # HERCULES_PLAN_REVIEW: the default plan_review of daemon runs of ready cases (auto | ask | errors)
  coverage: true    # HERCULES_PLAN_COVERAGE: the coverage audit
```

## Cost

Making a plan is part of the case reading the run already made: the same one structured
call per part of the case, with a larger reply (criteria, risks and lint per step), cached
like before. The coverage audit adds one decider call per case (two for cases of more than
12 steps). On the Demo Shop bench in hybrid mode (a hosted OpenAI-compatible endpoint for
the reading, JEV deciding), planning took a median 1.3 s per case (0.8 s reading, 0.5 s
audit), the audit cost about $0.00006 per case, and the median run grew from 7.6 s to 8.1 s.
A stored plan of an unchanged case costs nothing.
