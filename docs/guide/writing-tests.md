# Writing tests

A Hercules test case is a JSON file: where the app runs, the data the test uses, and
ordered steps in plain language. You can also write a single Gherkin scenario in a
`.feature` file.

Save cases in `.hercules/cases/` inside your project, in [folders](#folders) if you like.
The tester UI lists and edits cases there, and the coding-agent setup tells agents to save
cases there. `hercules run` accepts a case file from any path.

## A complete example

```json
{
  "id": "checkout-guest",
  "title": "Guest checks out one item",
  "description": "Covers the guest checkout path added in the cart redesign.",
  "priority": "high",
  "tags": ["checkout", "smoke"],
  "environment": {"base_url": "http://localhost:3000", "viewport": "1280x800"},
  "test_data": {
    "email": "guest+{{run_id}}@example.test",
    "card_number": {"secret": "TEST_CARD_NUMBER"}
  },
  "steps": [
    {"action": "Open \"/products/1\""},
    {"action": "Click the \"Add to cart\" button", "expected": "I should see \"1 item in your cart\""},
    {"action": "Click the \"Checkout\" link"},
    {"action": "Fill \"{{email}}\" in \"Email\""},
    {"action": "Fill \"{{card_number}}\" in \"Card number\""},
    {"action": "Click the \"Place order\" button", "timeout_s": 60},
    {"action": "verify", "expected": "the url should contain \"/order/confirmation\""}
  ],
  "timeout_s": 300
}
```

## Case fields

| Field | Required | Default | Meaning |
|---|---|---|---|
| `id` | yes | | Lowercase letters, digits, `.`, `_` or `-`; starts with a letter or digit; up to 100 characters |
| `title` | yes | | Up to 300 characters |
| `description` | no | | Free text |
| `priority` | no | `medium` | `low`, `medium`, `high` or `critical` |
| `tags` | no | `[]` | Free-form labels |
| `environment` | no | `{}` | Where the case runs (below) |
| `preconditions` | no | `[]` | What must already be true when the case starts. A model checks each one on the page before step 1 (see [Preconditions](#preconditions)). |
| `test_data` | no | `{}` | Values and secret references for `{{placeholders}}` (below) |
| `steps` | yes | | 1 to 200 steps |
| `timeout_s` | no | `300` | Time limit for running the case's steps, 10 to 7200 seconds (making its plan before the run is not counted) |
| `source` | no | | Provenance for cases a coding agent wrote: `agent`, `commit`, `files_changed` |
| `status` | no | `ready` | `ready`, `draft` or `known_bug` (see [Draft and ready cases](#draft-and-ready-cases)) |

Unknown fields are rejected, so a typo such as `"step"` instead of `"steps"` gives a
clear error instead of being ignored.

### `environment`

| Field | Meaning |
|---|---|
| `base_url` | The app's address, for example `http://localhost:3000`. Relative URLs in steps (`Open "/login"`) are joined to it. |
| `ref` | Name of a saved environment in `.hercules/envs/<name>.json` |
| `viewport` | `WIDTHxHEIGHT`, for example `1280x800` (the default) |
| `browser` | `chromium` (default), `chrome`, `msedge`, `firefox` or `webkit`. See [Browsers and browser options](browsers.md). |
| `browser_options` | Playwright launch and context options: locale, time zone, signed-in state, headers, proxy, ... See [Browsers and browser options](browsers.md). |
| `device` | A phone or tablet to emulate in the browser, for example `iPhone 15` (a Playwright device profile). See [Mobile testing](mobile-testing.md). |
| `mobile` | A native Android or iOS app on a device, through Appium, instead of a web page. See [Mobile testing](mobile-testing.md). |

`hercules run --env NAME` runs the case against a saved environment, and
`hercules run --base-url URL` replaces the case's base URL for one run. The daemon, the
UI and the coding-agent tools accept the same two per run.

A saved environment is a small JSON file in `.hercules/envs/`:

```json
{
  "name": "local",
  "base_url": "http://localhost:3000",
  "viewport": "1280x800",
  "browser": "chromium",
  "variables": {
    "user": "ana",
    "password": {"secret": "APP_TEST_PASSWORD"}
  }
}
```

`variables` are values and secrets that every case run with the environment can use as
`{{name}}`, in JSON cases, `.feature` files and the base URL. A case's own `test_data`
entry with the same name wins. A secret names an environment variable; its value is never
stored in the environment (see [Secrets](#secrets)). Older environments list
`secret_names`, which still work: each name is a secret of the same name. You can create
environments in the tester UI.

Every way of running a case (`hercules run`, the daemon, the UI and the coding-agent
tools) applies a saved environment the same way:

- The environment named per run (`--env`) wins over the case's `ref`, and its base URL
  replaces the case's.
- With only `ref`, the case's own `base_url` wins when it has one; otherwise the saved
  environment's base URL is used.
- The saved `viewport`, `browser` and `device` fill in what the case leaves out. A saved
  `mobile` app applies when the case sets none of `mobile`, `device` and `viewport`; a case's
  own target always wins.
- `--base-url` (or a base URL given per run) wins over both.
- An unknown environment name is an error, unless it only comes from `ref` and the case
  has its own `base_url`.

### Steps

| Field | Required | Meaning |
|---|---|---|
| `action` | yes | One instruction in plain language, up to 2000 characters |
| `expected` | no | What must be true after the action |
| `semantic` | no | Compare `expected` by meaning instead of literally (see below) |
| `timeout_s` | no | Time limit for this step, 1 to 7200 seconds (default 120) |
| `api`, `sql`, `quality`, `security` | no | A structured API call, read-only query, page check or security scan (see [API and SQL steps](#api-and-sql-steps), [Accessibility, visual and layout steps](#accessibility-visual-and-layout-steps) and [Security scans](security-scan.md)) |

A step with `"action": "verify"` performs no action; it only checks `expected`.

Write one action per step and quote the visible label of the element:

```json
{"action": "Click the \"Sign in\" button"}
{"action": "Fill \"{{email}}\" in \"Email\""}
{"action": "Select \"Germany\" from \"Country\""}
```

Every step is read by the local model; there are no built-in parsing rules. Before the
first step the model reads the whole case in one call (each step's action and, separately,
its `expected` outcome, with the other steps and your test data names as context). The
reading is cached in `.hercules/cache/interpret/`, keyed by the model, the version of
Hercules' reading prompt and the text of the steps, so running an unchanged case again
does not read it again. A step you edit during a run, and a step whose reading does not
work on the page (its element is not found, or a value is missing), is read again on its
own with the page's controls as context. The reading also says, per step, whether the
step names every control it uses (`I click the "Login" button`) or describes what a
person does in their own words (`they use the side menu to go back to all items`). When a
step of the second kind, or one the model read as several actions, has done its actions,
the model is asked once, with the page, whether the step still needs something (a menu
entry that only appeared, a submit a reading made without the page missed, a field still
empty or an error message on the form). A step that only checks something (`Then I should
see "..."`) is read as checks: a quoted text is checked literally, and only an outcome
with no text, address or element to look for is judged by its meaning. `plan.jsonl` in
the step's evidence shows each reading and where it came from.

Before Hercules reads the page (to pick an element, to continue a step, or to check an
outcome) it lets the page settle, within a few seconds at most: a form submission still on
its way, the page's own data requests, network activity, elements still being added,
animations and transitions still running, and visible loading indicators (`aria-busy`,
an indeterminate progress bar).

Phrasings like these are read reliably:

| Kind | Examples |
|---|---|
| Open a page | `Open "/login"`, `Go to "http://localhost:3000/cart"`, `Navigate to /settings` |
| Click | `Click the "Save" button`, `Click on "Profile"`, `Tap "Menu"` |
| Fill | `Fill "{{email}}" in "Email"`, `Type "hello" into "Message"`, `Fill "Name" with "Ada"` |
| Select | `Select "Germany" from "Country"`, `Choose "Large" in "Size"` |
| Upload | `Upload "case.png" to "Photo"`, `Attach "fixtures/invoice.pdf" and "case.png" to "Documents"` ([Files for upload steps](#files-for-upload-steps)) |
| Download | `Click "Export CSV" to download the report`, `Download the file from "/reports/q3.pdf"` ([Downloading and checking files](#downloading-and-checking-files)) |
| Check boxes | `Check "I agree"`, `Uncheck "Subscribe"` |
| Hover | `Hover over "Account"` |
| Keys | `Press Enter`, `Press the key "Escape"` |
| Wait | `Wait 2 seconds`, `Wait for "Saved" to appear` |

A step that does several things ("sign in with X and Y") is split by the model into
separate actions. Separate steps are more reliable and easier to debug.

### Field values: formats, length limits and dependent fields

Every value Hercules types or chooses is checked the way a person would see it: after the
field is filled, focus leaves the field (as tabbing away does), the page gets a moment to
validate it, and the field is read again: what it holds, whether the page marks it invalid,
and the error text next to it.

- **Formats.** When a field rejects the value as written (a date field that wants
  day/month/year gets `12/31/2027`), the model is asked to write *the same value* the way the
  field asks for it, using the field's error text, placeholder or pattern. It may only change
  how the value is written (date order, separators, phone formatting), never what it means,
  and a second model must agree that both values mean the same before the new one is
  entered. The step then passes with a note, for example
  `entered "31/12/2027" for "12/31/2027" in the "Expiry Date" field to match the field's format`,
  which also appears in the run's hints. If the field still rejects it, the step fails with
  the field's own error. To avoid the rewrite, write values in the format the app expects.
- **Fields that reformat themselves.** A phone field that turns `5551234567` into
  `(555) 123-4567` is fine. When the field shows something that differs more than spacing
  and punctuation, a model judges whether it is the same value; if not, the step fails.
- **Values the test expects to be rejected.** In a negative test, say so in the step or in
  the next step (`Then I should see "Enter a valid email address"`): the model then reports
  the rejection as expected instead of trying another format.
- **Length limits.** A value longer than the field accepts (its `maxlength`, or a field that
  cuts it short) fails the step with `the field accepts at most 10 characters; the step's
  value has 12` (the value itself is never echoed) and the run is classified as `test_data`. Hercules never
  shortens the value: that would change what the test checks. Fix the test's data.
- **Dependent fields.** Write fields in any order. When a field depends on another one
  (a state or region list that follows the country, a sub-type that follows the type, an end
  date checked against the start date), the model notices it while reading the case and the
  run sets the other field first, within the same run of consecutive field steps (no click,
  check or navigation between them). If it only shows at run time (the region is disabled or
  empty), the model may ask to run the later field step first; the step then runs again
  after it. Steps keep their numbers; the result reports when each one ran (`sequence`) and
  why (`notes`), and the UI timeline shows it.

### Expected outcomes

The model reads what `expected` asks for; Hercules then checks it against the page in
code and records each check in an assertion ledger. Outcomes that name exact text, an
address or an element become literal checks:

| Check | Examples |
|---|---|
| Text is shown | `I should see "Welcome back"`, `The page should contain "3 results"`, `"Log out" should be visible` |
| Text is not shown | `I should not see "Error"`, `"Loading" should not be visible` |
| URL | `The url should contain "/dashboard"`, `The url should be "http://localhost:3000/"` |

A check is kept only when its text comes from the step itself: the model can restate
what you expect, never replace it with what the page happens to show.

An outcome with no exact text to look for ("the order is confirmed") is judged by a
model by its meaning, the same way as `semantic: true` below.

Expectations about appearance (photos, images, icons, logos, colours, layout,
alignment) cannot be checked against page text. The model reads them as appearance
checks, judged on a screenshot, which needs a decider that can see images: a
local model server with image support, or hybrid mode with egress `hybrid`. Without one,
the check is reported `unverified`, which fails the run.

`semantic: true` asks for a comparison by meaning. A model (JEV in hybrid mode, the local
model in pure mode) gets the expected outcome, the page's address, title and visible text
(secret values masked) and, when images may be sent, the screenshot, and answers how
likely it is that the outcome holds. 70 % or more passes, 30 % or less fails, and
anything in between is `unverified`: an unsure judgement never passes. The judgement is
recorded in the step's `decisions.jsonl` with the purpose `semantic`.

### What the plan adds

Before the browser starts, the case becomes a plan (see [Plans](plans.md)): each step's
actions, the checks its expected outcome states (author criteria), and what the model
infers the page should show after each action (inferred criteria: after clicking "New" a
dialog is open). You do not write the inferred criteria; they make a run notice an action
that did not take effect, and recover from it. To get the most from them:

- state outcomes as text the page shows (`I should see "Saved"`): they become author
  criteria checked in code;
- name controls by their visible labels, in quotes;
- write plain values: a value with a stray bracket or quote, a Markdown link where a plain
  value is expected, or a value that looks cut off is flagged by the plan's lint;
- keep credentials out of URLs (use a secret reference), and give a database step a
  connection in `test_data`;
- run `hercules plan CASE` to see the plan and its lint before the first run; `--strict`
  makes lint errors stop a run.

### Preconditions

`preconditions` lists what must already be true when the case starts ("the user is signed
out", "the cart is empty"). Before step 1 the browser opens the case's base URL (when it
is still on a blank page) and a model judges each precondition on that page, the same
way as a semantic check. A precondition that clearly does not hold ends the run with
status `error` before any step runs, and the hint names it. One the model cannot confirm
either way is noted in the run's hints and the run goes on. Cases that open no browser
(only API or SQL steps) cannot have their preconditions checked; the run notes that.
The judgement is saved in `preconditions/` in the run's evidence.

Verdicts are strict. A run passes only when every step passed and no assertion failed
or stayed unverified. The run stops at the first step that does not pass and reports the
remaining steps as `skipped`.

## Test data and placeholders

`test_data` maps names to values. Use a name in a step as `{{name}}`.

```json
"test_data": {
  "email": "user+{{run_id}}@example.test",
  "plan": "Pro"
},
"steps": [
  {"action": "Fill \"{{email}}\" in \"Email\""},
  {"action": "Select \"{{plan}}\" from \"Plan\""}
]
```

- Names use letters, digits and `_`, and start with a letter or `_`.
- Two placeholders are built in: `{{run_id}}` (the run's id) and `{{timestamp}}` (UTC,
  `YYYYMMDDhhmmss`). Use them to make values unique per run. You cannot define
  `test_data` entries with these names.
- Plain values may contain the built-in placeholders, as in the `email` example.
- Every placeholder must be defined. A case that uses an unknown name, such as
  `{{emial}}`, is rejected when it is loaded and the error lists the known names.
- Placeholders are replaced when the step runs. The model sees the placeholder name, not
  the value.

## Secrets

Never write a password, token or card number into a case. Reference an environment
variable instead:

```json
"test_data": {
  "password": {"secret": "APP_TEST_PASSWORD"}
}
```

- The variable name uses capital letters, digits and `_`.
- The value is read from the environment of the process that runs the case: your shell
  for `hercules run`, or the environment `hercules serve` was started in for runs through
  the daemon, the UI and coding agents.
- Otherwise it is read from `~/.hercules/secrets.env` (readable by you only), where the
  tester UI saves values pasted on the **Environments** screen. A value saved there is
  used by the next run without restarting `hercules serve`; an environment variable of
  the same name wins. The UI shows each secret as set in the environment, saved in
  Hercules, or not set, and never shows a value again.
- If the variable is not set, the step fails with an error that names the variable.
- Secret values never reach the model, and they are replaced with `******` in
  observations, decision records and `run.json`.

A login link that carries a session id or token (a "front door" URL, a magic link) is a
secret too. Keep the token in a secret and build the address from it:

```json
"test_data": {
  "session": {"secret": "APP_SESSION_ID"}
},
"steps": [
  {"action": "I open \"https://app.example/auth/link?sid={{session}}&next=/home\""}
]
```

The same works in an environment's base URL, so every case on that environment starts
signed in: set the base URL to
`https://acme.my.salesforce.com/secur/frontdoor.jsp?sid={{sid}}` and add `sid` to the
environment's variables as a secret. The browser opens the address with the value; exported
code keeps `{{sid}}`.

A token written straight into a step still never reaches a model or the evidence (it is
replaced with a `{{url_credential_N}}` placeholder for the run and masked by name; see
[Credentials in URLs](modes-and-privacy.md#credentials-in-urls)), but the case file itself
then holds it, and an exported test cannot know which variable to read it from.

## Files for upload steps

A step that uploads names its files: `I upload "case.png" to "Case clearance photo"`, or
several at once, `I upload "fixtures/invoice.pdf" and "case.png" to "Documents"` (the
field must accept several). Hercules looks for each name in this order:

1. the case's own files, `.hercules/case-files/<case id>/`;
2. the files of the case's environment, `.hercules/files/<environment>/`;
3. the project folder, for a path such as `fixtures/invoice.pdf`.

Add files to a case on the **Form** tab of the **Cases** screen, and to an environment on
the **Environments** screen (**Add files**; files picked before the first save are added
when it is saved), or with `PUT /api/cases/<saved file name>/files/<file>` and
`PUT /api/environments/<name>/files/<file>` (the raw file, `application/octet-stream`).

- Any file type, up to 20 files of 25 MB each per case and per environment.
- Key and credential files (`.env`, `*.pem`, private keys by content) are refused, and a
  step can never attach a file outside these folders or the project.
- **Never read by a model.** The files go only to the app under test; no model, local or
  hosted, receives their contents. The file's name is part of the step's text, which is read
  like any other step.
- The field can be a file input, a label of one (also when the input itself is hidden), or a
  button that opens the browser's file picker. A file that cannot be found fails the step at
  once; a field that takes one file fails when the step names several.
- Exported code attaches the same files by their path in the project
  (`setInputFiles([...])`, `set_input_files([...])`), and a cloud run's project carries them.

## Downloading and checking files

A step that downloads a file clicks the control that starts it, or names the file's
address:

```gherkin
When I click "Export CSV" to download the report
When I download the file from "/reports/q3.pdf"
When I download "invoice.pdf" by clicking "Invoice"
```

The file is saved in the run's evidence, in `steps/<n>/downloads/`, under the name the
site gives it (letters, digits, `.`, `_` and `-` only; `_2`, `_3` when a name repeats), and
listed with the step on the run page. A step that names the file fails when the site sends
another name. A click that starts no download (and opens no file in a new tab) within 30
seconds counts as the wrong control: Hercules tries another, then fails the step. An address
is fetched with the browser's cookies and the page stays where it is; an address that answers
with a web page or an error fails the step. Files are kept up to 50 MB.

Later steps check the newest download, in code:

| Check | Example |
|---|---|
| Name | `Then the downloaded file is named "report.csv"` |
| Type | `Then the downloaded file is a PDF` (read from the file's first bytes, not its name) |
| Text | `Then the downloaded file contains "Total Due: $950"`, `... does not contain "Refunded"` |
| Rows | `Then the downloaded CSV has 12 rows`, `... has at least 3 rows` (CSV or the first sheet of an XLSX, without the header row) |
| Column | `Then column "Status" is "Paid" in every row`, `Then column "Status" is "Paid" for Order ID 1002` |
| JSON | `Then the downloaded JSON has "$.total" equal to "1240"` |

Text is read from PDF, DOCX, XLSX, CSV, JSON and plain text files (a PDF with no text in it,
such as a scan, fails a text check). `{{downloaded_file}}` (the newest download's path) and
`{{downloaded_file_name}}` work in later steps, for example to upload it again:
`When I upload "{{downloaded_file}}" to "Attachment"`.

- **Never read by a model.** Downloaded files are checked in code only; no model, local or
  hosted, receives their contents. An outcome only a reader could judge ("the report says
  the order was approved") is not a file check: write what the file should contain instead.
- Exported code downloads with `waitForEvent("download")` / `expect_download()` and repeats
  the name, type, text, row, column and JSON checks on text and CSV files. Text inside a PDF,
  DOCX or XLSX, and a file the site showed in the browser instead of downloading, become TODO
  lines in the export.

## Gherkin `.feature` files

A `.feature` file with exactly one scenario can be run directly. [Writing Gherkin](gherkin.md)
shows how to write one that reads reliably and how the Gherkin editor helps.

```gherkin
@smoke
Feature: Sign in

  Background:
    Given I open "/login"

  Scenario: Registered user signs in
    When I fill "demo@example.com" in "Email"
    And I click the "Sign in" button
    Then I should see "Welcome back"
    And the url should contain "/dashboard"
```

```bash
hercules run sign-in.feature --base-url http://localhost:3000
```

How the file becomes a test case:

- The case `id` is the scenario name in lowercase with `-` between words
  (`registered-user-signs-in`), and the title is the scenario name.
- The feature name becomes the case description; tags become case tags.
- `Background` steps are put before the scenario's steps.
- Each `Given` and `When` line, and the `And`, `But` and `*` lines after them, becomes a
  step.
- Each `Then` line, and the `And`, `But` and `*` lines after it, becomes a `verify` step
  whose `expected` is the line's text.
- Lines starting with `#` are comments.

Not supported yet in a runnable `.feature` file: more than one scenario, `Scenario
Outline` and `Examples`, `Rule`, doc strings (`"""`) except after a step that ends with
`:` (see [API and SQL steps](#api-and-sql-steps)), and data tables (`|`). The error
names the file and line. `hercules import v1` accepts all of these and writes one case
per scenario or example row; see [Importing](importing.md).

A `.feature` file's `{{name}}` placeholders come from its test data, its environment's
variables and secrets (from its `@env:` tag, or the environment chosen for the run) and the
built-ins `{{run_id}}` and `{{timestamp}}`. The test data of `sign-in.feature` is kept
beside it in `sign-in.data.json`, so the Gherkin stays plain; edit it in the **Form** tab
of the **Cases** screen, or write it by hand:

```json
{"test_data": {"user": "demo@example.com", "password": {"secret": "SHOP_PASSWORD"}}}
```

`hercules run sign-in.feature` reads it too. Over the API, send `test_data` next to
`gherkin` (`POST /api/cases`, `/api/cases/validate`, `/api/runs`, `/api/plans`). TestZeus references
`[$.environment.<env>.<key>]` and `[$.testdata.<name>.<key>]` are read as `{{key}}`, so
features copied from TestZeus run unchanged once the environment defines `key`.

## Folders

Group cases in folders: directories under `.hercules/cases/`, up to 8 levels deep.

```text
.hercules/cases/
├── Checkout/
│   ├── Payments/
│   │   └── card-declined.feature
│   ├── place-an-order.feature
│   └── place-an-order.data.json
└── login-success.json
```

- A saved case is named by its path there, such as `Checkout/Payments/card-declined.feature`.
  The UI's address for it is `/cases/Checkout/Payments/card-declined.feature`.
- A case's id (its file name) is unique across all folders and both file types: saving a
  case whose id another case already has (`login.json` beside `login.feature`, or in another
  folder) is refused. Its test data (`<name>.data.json`) and its
  [files for upload steps](#files-for-upload-steps) follow it when it moves, so it keeps
  them and its past runs.
- A folder name has at most 120 characters, does not start with a dot, does not end with a
  dot or a space, and holds none of `/ \ : * ? " < > |`. Names are unique within a folder,
  ignoring case. Folders starting with a dot (`.git`) and symbolic links are not listed.

On the **Cases** screen the folders and cases form one tree; click a case to open it. The
**New** button and each folder's **•••** menu make, rename, move and delete folders, add
existing cases, start a case in the folder, and run the folder (see
[Running a folder](running-tests.md#running-a-folder)). Deleting a folder moves what it
holds into its parent unless you choose to delete its cases too.

Through the HTTP API: `GET /api/cases` lists `folders` and each case's `folder`;
`POST /api/folders` makes one, `PATCH /api/folders/{folder}` renames (`name`) or moves
(`parent`) it, `DELETE /api/folders/{folder}?mode=move_up|delete_cases` deletes it,
`POST /api/cases/move` moves cases, and `POST /api/cases` takes a `folder` to save into.

## Draft and ready cases

A case's `status` is `ready` (the default) or `draft`.

Drafts are cases nobody has reviewed yet: cases written from a plain-language
description in the UI or by a coding agent, and cases created by `hercules import doc`.
When a draft is started through the daemon (the tester UI or a coding agent), the run
waits in `needs_approval` with the drafted steps until a person approves or rejects it.
Approving runs it and marks it ready for that run. A drafted case's plan is then reviewed
too (`plan_review` defaults to `ask` for drafts): the run waits in `needs_plan_approval`
until its plan is approved, edited or rejected (see [Plans](plans.md#review-and-approval)).

`hercules run` refuses a draft: it prints an error and runs nothing. Review the steps,
then mark the case ready, or pass `--allow-draft` to run it once as it is.

To mark a reviewed case ready, change `"status": "draft"` to `"status": "ready"` (or
remove the field).

`known_bug` cases are written by [exploratory testing](explore.md): each reproduces an open
finding. `hercules run` still runs it, but its failure is expected and exits 0 with a note; when
it passes, the bug may be fixed (`hercules explore reverify` moves the finding on). Closing the
finding after the fix turns it into a `ready` regression test (see
[Keeping the cases](explore.md#keeping-the-cases)).

## JSON Schema

The case and result formats are published as JSON Schema (draft 2020-12) in the package:

- `testzeus_hercules/cases/schemas/test_case.schema.json`
- `testzeus_hercules/cases/schemas/run_result.schema.json`

Point your editor at `test_case.schema.json` to get completion and validation while you
write cases.

## API and SQL steps

A step can call an HTTP API or read a database instead of using the browser. Give it a
structured `api` or `sql` payload; `action` stays the human-readable sentence. The
request or query runs as written and every check is judged in code, one assertion-ledger
entry per check. A step takes at most one payload (`api`, `sql`, `quality` or `security`).

```json
{
  "id": "pet-lifecycle",
  "title": "Create a pet and read it back",
  "environment": {"base_url": "http://localhost:8000/v1"},
  "test_data": {"key": {"secret": "PETS_API_KEY"}},
  "steps": [
    {
      "action": "Create a pet called Bella",
      "api": {
        "method": "POST",
        "path": "/pets",
        "headers": {"X-API-Key": "{{key}}"},
        "body": {"name": "Bella", "tag": "dog"},
        "expect": {"status": 201},
        "save": [{"name": "pet_id", "json_path": "$.id"}]
      }
    },
    {
      "action": "Read Bella back",
      "api": {
        "path": "/pets/{{pet_id}}",
        "expect": {"status": 200, "json_path": [{"path": "$.name", "value": "Bella"}]}
      }
    }
  ]
}
```

### `api`

| Field | Meaning |
|---|---|
| `method` | `GET` (default), `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD` or `OPTIONS` |
| `path` | Relative to `environment.base_url` (its path prefix is kept), or an absolute `http(s)` URL. Without `path`, the checks and saves apply to the previous response |
| `headers`, `query` | Name to value; a query value may be a list |
| `body` | JSON for `body_mode` `json` (the default), text for `raw`, a field map for `urlencoded` and `multipart`. A multipart field `{"file_path": "fixtures/avatar.png"}` uploads a file from inside the project |
| `body_mode` | `json`, `raw`, `urlencoded` or `multipart` |
| `auth` | `{"type": "basic", "value": "user:password"}` or `{"type": "bearer", "value": "{{token}}"}` |
| `expect` | The checks on the response (below) |
| `save` | Values to keep for later steps: `{"name": "pet_id", "json_path": "$.id"}` or `{"name": "etag", "header": "ETag"}` |

`expect` fields:

| Field | Checks |
|---|---|
| `status` | `200`, a class such as `"2xx"`, or a list of accepted codes |
| `json_schema` | The body against an inline JSON Schema, or a schema in a spec file: `"petstore.yaml#/components/schemas/Pet"` |
| `openapi` | The body against the schema a spec file declares for this request's operation and status |
| `json_path` | A list of `{"path": "$.items[0].id", "op": "eq", "value": 1}` checks |
| `headers` | A list of `{"name": "Content-Type", "op": "contains", "value": "json"}` checks (names are case-insensitive) |
| `body_contains` | Texts the raw body must contain |
| `max_time_ms` | The response took less than this many milliseconds |

`json_path` operators: `eq` (default), `ne`, `contains`, `not_contains`, `matches` (a
regular expression), `gt`, `lt`, `exists`, `not_exists`, `not_empty` (these three take
no value), `all_gte` and `all_lte` (every match), `count_eq` (the number of elements of
an array, or of matches) and `eq_path` (equal to the value at another JSONPath given as
`value`). A per-value operator must hold for every match.

Requests do not follow redirects, so a step sees the server's own status. Responses are
read up to 10 MB. Status codes are data: a 404 fails the `status` check, it is not an
error. A refused connection or a timeout (30 s per request) makes the step `error`.

### `sql`

| Field | Meaning |
|---|---|
| `db` | The `test_data` entry that holds the connection string, normally a secret reference such as `{"secret": "APP_DB_URL"}` |
| `query` | One read-only `SELECT` (or `WITH ... SELECT`). Without `query`, the checks and saves apply to the previous rows |
| `params` | Bound parameters, used in the query as `:name` |
| `expect` | `rows` (exact count), `min_rows`, `max_rows`, and `json_path` checks over the rows, which are a list of column-to-value objects: `$[0].status` |
| `save` | `{"name": "order_id", "json_path": "$[0].id"}` |

```json
"test_data": {"shop": {"secret": "SHOP_DB_URL"}},
"steps": [
  {
    "action": "Ada has two orders",
    "sql": {
      "db": "shop",
      "query": "SELECT id, total, status FROM orders WHERE customer = :who ORDER BY id",
      "params": {"who": "ada"},
      "expect": {"rows": 2, "json_path": [{"path": "$[*].total", "op": "all_gte", "value": 7}]}
    }
  }
]
```

Only one `SELECT` statement runs: anything that writes (`INSERT`, `UPDATE`, `DELETE`,
`SELECT ... INTO`, a second statement) is refused before connecting, and the session is
made read-only with a 30 s statement timeout where the database supports it. SQLite files
are opened read-only, relative to the project folder. At most 1000 rows are kept; a row
count beyond that is reported as not exact. Connection strings never appear in evidence.

### Saved values

A value a step saves is available to later steps as `{{name}}`. In a JSON value, an exact
`"{{name}}"` keeps the saved value's type (a number stays a number). A saved value whose
name looks secret (`token`, `password`, `api_key`...) is masked like a secret.

In plain language, a step that says it saves something `as "<name>"` (save, store, remember,
keep or capture) defines `{{<name>}}` for the steps after it, without test data:

```gherkin
When I save the response JSON "$.code" as "order_code"
And I send a GET request to "/api/orders/{{order_code}}"
```

Using the name before the step that saves it is reported as an unknown placeholder.

### Plain-language API and SQL steps

A step without a payload in a case tagged `kind:api` or `kind:sql`, or a step the model
reads as an API call or a query, is read by the model into the same payload; the request
and the checks still run in code. The model's reading is recorded in the step's
`plan.jsonl`. A step whose `expected` text has no structured check is reported
`unverified`. In a `.feature` file, a doc string may follow a step that ends with `:` (a
request body, a query); it is appended to that step's text.

API- and SQL-only cases (every step has a payload, or the case is tagged `kind:api` or
`kind:sql`) run without a browser. Requests and responses (`http.json`), queries and rows
(`sql.json`) and the checks (`checks.json`) are written next to the step's evidence, with
credentials masked. `hercules import openapi` writes one such case per operation; see
[Importing](importing.md).

## Accessibility, visual and layout steps

A step with a `quality` payload checks the page as it is at that point of the case:

```json
{"action": "The sign-up page is accessible", "quality": {"kind": "a11y"}}
{"action": "The checkout looks as before", "quality": {"kind": "visual", "baseline": "checkout", "mask": [".clock"]}}
{"action": "Nothing overlaps on small screens", "quality": {"kind": "layout", "viewports": [375]}}
```

| Field | Kind | Meaning |
|---|---|---|
| `kind` | all | `a11y`, `visual` or `layout` |
| `fail_on` | all | Findings at or above this impact fail the step: `critical`, `serious` (default), `moderate` or `minor` |
| `scope` | a11y, layout | Only report elements inside this CSS selector |
| `level` | a11y | WCAG level `A`, `AA` (default) or `AAA` |
| `rules` | a11y | Only these rules, such as `color-contrast`, `image-alt`, `label`, `button-name`, `target-size` |
| `keyboard` | a11y | Also walk the page with Tab for reachability and visible focus (default `true`) |
| `baseline` | visual | The baseline's name (required); baselines live in `.hercules/baselines/` |
| `mask` | visual | CSS selectors of regions that change on their own (clocks, ads) |
| `key_elements` | visual | CSS selectors whose computed styles are compared property by property |
| `threshold` | visual | Share of compared pixels that may differ (default 0.001) |
| `full_page` | visual | The whole scrollable page instead of the viewport |
| `viewports` | layout | Widths in CSS px (default 375, 768 and 1280) |

A plain step such as `Then the page should have no accessibility violations` is read by
the model into this payload. See [Visual and accessibility checks](quality-checks.md) for
the verdicts, the fix values and phrasing examples.
