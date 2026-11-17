# Exporting runs as code

> **Watch it:** [Export a passed run as code (1:33)](videos.md#9-export-a-passed-run-as-code).

A passed run can become a test your team keeps in the repository. Hercules writes what the
run did as a Playwright Test file (TypeScript), a pytest-playwright file (Python), or a
framework-neutral automation spec that a coding agent turns into a test in your project's
own style. A native app run (Android or iOS, through Appium) becomes a pytest file that uses
the Appium Python client.

The export only describes what the run did. It does not decide anything again: the
elements are the ones the run acted on, the locators are the ones that matched only that
element on the live page, the checks are the ones the run verified. Anything it cannot
write as code faithfully is kept in the file as a `TODO` comment and listed in the notes.

## Export a run

```bash
hercules export r_20261026_141502_9f3a1c --format playwright-ts --out tests/checkout.spec.ts
hercules export r_20261026_141502_9f3a1c --format playwright-py > tests/test_checkout.py
hercules export .hercules/runs/r_20261026_141502_9f3a1c --format spec
hercules export r_20261105_192704_1a494d --format appium-py --out tests/test_settings.py
```

| Option | Meaning |
|---|---|
| `RUN` | A run id (from `.hercules/runs/`) or a run folder |
| `--format {playwright-ts,playwright-py,appium-py,spec}` | What to write (default `playwright-ts`; `appium-py` for a native app run) |
| `--out FILE` | Write to this file instead of standard output |
| `--force` | Replace `--out` when it exists |
| `--allow-failed` | Export a run that did not pass, up to its last passed step |
| `--project DIR` | Project directory (default: the current directory) |

The code goes to standard output (or `--out`). The suggested file name, the environment
variables the code reads and the notes to review go to standard error. Exit code `2`
means the run could not be exported, with the reason.

Other ways to export:

- **Tester UI**: the run page of a passed run has an **Export as code** button. Pick the
  format, read the code, copy or download it.
- **Coding agents**: the `hercules_export_code` MCP tool (see
  [Coding agents](coding-agents.md#hercules_export_code)).
- **Daemon API**: `GET /api/runs/{run_id}/export?format=playwright-ts` (also `allow_failed`
  and `target_dir`), described in `/api/openapi.json`. Without `format`, a native app run
  exports as `appium-py`.

Exporting reads only the run's `run.json`. Nothing is sent anywhere.

## Which runs are exported

Only passed runs. A run that failed, errored or was cancelled is refused with the step
where it stopped. With `--allow-failed` (`allow_failed` in the tool and the API) it is
exported up to its last passed step: the steps before the first one that did not pass.
The file then starts with a `PARTIAL` line that says so.

Runs made by a Hercules version without exports have no automation record and are
refused; run the case again.

## What a run records

While a run executes, Hercules records for each step (in `run.json`, section
`automation`, and in `steps/<n>/locators.json`):

- **Every action that succeeded**: navigations (relative to the case's base URL when they
  are under it), clicks, fills, selects (and how the option was chosen), checks, hovers,
  key presses and waits. Actions that failed and were retried are not recorded.
- **Locators proven on the live page** for every element it acted on or checked. When the
  engine is about to use an element, it lists the ways a test could find it, best first,
  and evaluates each in the element's frame:

  | Order | Candidate | Playwright |
  |---|---|---|
  | 1 | A test id attribute: `data-testid`, `data-test-id`, `data-test`, `data-qa`, `data-cy` | `getByTestId()`, or `locator('[data-qa="…"]')` |
  | 2 | The role with its accessible name | `getByRole(role, { name, exact: true })` |
  | 3 | A label (`<label>` or `aria-label`) | `getByLabel(…, { exact: true })` |
  | 4 | The placeholder | `getByPlaceholder(…, { exact: true })` |
  | 5 | The exact text (not for fields) | `getByText(…, { exact: true })` |
  | 6 | The alt text, the title | `getByAltText()`, `getByTitle()` |
  | 7 | CSS: an id that does not look generated, the `name` attribute, the element's path | `locator(…)` |

  A candidate is kept only when it matched exactly one element and that element is the one
  acted on. `locators.json` lists every candidate with its match count, kept or not. The
  first kept one becomes the locator; up to three more are listed as alternatives in a
  comment. Elements inside iframes get the iframe's own unique locator (its name, title or
  id) first; open shadow roots need nothing extra, since Playwright locators pierce them.
- **Values as templates**: what a step typed is recorded as the case wrote it, with its
  `{{name}}` placeholders, never as the value the run used.
- **Checks** from the step's assertion ledger, with how each was verified.
- **API, SQL and quality payloads** as executed, placeholders kept.

Recording never fails a step; a problem while recording is noted and the step goes on.

## Secrets and data

| In the case | In the exported code |
|---|---|
| `{{name}}` of a secret (`{"secret": "APP_PASSWORD"}`) | `process.env` / `os.environ["APP_PASSWORD"]`, by name only |
| `{{name}}` of a plain value | A `DATA` constant at the top of the file |
| `{{run_id}}`, `{{timestamp}}` | `RUN_ID`, `TIMESTAMP`, made when the test starts |
| `{{name}}` saved by an earlier API or SQL step | A `saved` variable the step that saves it sets |
| A relative address (`/login`) | Resolved against `BASE_URL` (environment, default the run's base URL) |

A secret value is never written: not in `run.json`, not in `locators.json`, not in the
code. A value that holds a secret's value without referring to it by name is left out and
listed in the notes; locators whose text holds a secret value are dropped. The export
lists the environment variables the code reads.

## Checks

| The run checked | The code |
|---|---|
| A text is shown | `expect(page.getByText(…)).toBeVisible()` (with `.first()` when several elements show it, or through its iframe), else `toContainText` on the body |
| A text is not shown | `expect(page.getByText(…)).toHaveCount(0)`, else `not.toContainText` on the body |
| The address contains / equals | `expect(page).toHaveURL(…)` |
| An element is visible | `expect(locator).toBeVisible()` |
| An API response | Assertions on the status, the JSON body (simple JSONPaths: names and indexes), headers, body text and time |
| SQLite rows (Python) | `sqlite3`, read-only, with the same row and JSONPath checks |

For each literal text check that passed, Hercules also tried the Playwright forms on the
live page and records the one that held. When none held (for example a text that is in the
page's text but hidden, such as a closed drop-down's option), the assertion is written as a
comment under a `TODO`, so the exported test does not fail on something the run never saw.

## What cannot be exported faithfully

Each of these becomes a `TODO` comment in the code and a note in the result:

- **Checks a model judged** by meaning (`semantic`, or an outcome with no literal text) or
  on a screenshot (how the page looks). The comment gives the expectation, the run's
  verdict and the page it was judged on (its title and address). Write an assertion for it.
- **Elements without a unique locator** (several same-named elements and no attribute that
  tells them apart). The code uses a best guess.
- **A drop-down option found by its text** rather than its role.
- **JSONPaths with wildcards, filters or slices**, and JSON Schema or OpenAPI checks:
  validate them with your project's tools.
- **SQL steps** in TypeScript, and databases other than SQLite in Python.
- **Accessibility, visual and layout checks**: Hercules runs its own checks; the comment
  shows what was checked (and, in TypeScript, how to start with `@axe-core/playwright`).
- **Files and binary request bodies.**

## Running an exported test

TypeScript (Playwright Test 1.43 or later):

```bash
BASE_URL=http://127.0.0.1:3000 APP_PASSWORD=... npx playwright test tests/checkout.spec.ts
```

Python (pytest-playwright):

```bash
pip install pytest-playwright
BASE_URL=http://127.0.0.1:3000 APP_PASSWORD=... pytest tests/test_checkout.py
```

API steps use their own request context (the `request` fixture in TypeScript; an `api`
fixture defined in the Python file), as Hercules sends API requests apart from the browser.
Requests do not follow redirects, as in the run.

Both files run the browser named in `HERCULES_CHROMIUM` when it is set (a Chrome for Testing
build, as Hercules itself may use), else Playwright's own downloaded browser. A credential the
run took out of a URL in the case (a session id in a sign-in link) is read from
`URL_CREDENTIAL_1`, `URL_CREDENTIAL_2` ...

## Checking an export

`--check` runs the pytest-playwright export once, to see that it passes on its own:

```bash
pip install pytest-playwright
hercules export RUN --check
```

It writes the test to `<run>/export-check/`, runs it with pytest in a child process with a
Playwright trace, and prints whether it passed and how long it took (exit code 0 when it
passed, 1 when it failed or ran past `--check-timeout`, 600 s by default; the browser is
stopped with it). Secrets come from the environment by name; credentials the run took out
of the case's URLs come from the case file (found by the run's case id, or `--case FILE`),
and no value is printed.

- `recording.gif`: the run as the screen showed it, made from the trace's frames. It shows
  the page only, so it can be shared.
- `results/.../trace.zip`: the step-by-step trace (`playwright show-trace` opens it). It holds
  what the browser sent, secrets included: keep it private.

Video is not recorded: Playwright needs its ffmpeg helper for it, and without the helper the
browser page never opens and pytest waits while closing it. Run pytest with `--video on`
yourself after `playwright install ffmpeg` if you want one.

## Native app runs (Appium)

A native app run exports as `appium-py`: a pytest file with the Appium Python client. It is
the default format for such runs. Playwright formats are refused for them, and `appium-py`
is refused for a browser run.

```bash
pip install pytest Appium-Python-Client
appium --address 127.0.0.1 --port 4723 &
pytest tests/test_settings.py
```

Run pytest from the project folder: an app file in the case is a path inside the project.

**The session.** A `driver` fixture starts the session the run started and ends it after the
test. Its capabilities are the run's, and these variables override them:

| Variable | Default |
|---|---|
| `APPIUM_URL` | The Appium server or device cloud hub the run used |
| `APPIUM_DEVICE_NAME`, `APPIUM_UDID`, `APPIUM_PLATFORM_VERSION` | The case's `device`, `udid`, `platform_version` |
| `APPIUM_APP` | The case's `app` (required for a device cloud run that uploaded a file: set it to the cloud's app id) |

A device cloud run reads its user name and access key from the variables the run read them
from (`mobile.browserstack.username_env`, ...). They are never written in the file.

To run an export on BrowserStack, Sauce Labs or TestMu AI, or to get it as a project with
the cloud's CI configuration, see [Running tests on clouds](clouds.md).

**What a native app run records.** For each action that worked: the kind (tap, type, clear,
long press, swipe, scroll, back, home, wait), the typed text as a template, and the element
it used, with every Appium locator that finds it, how many elements on the screen each one
matched, and where the element was drawn. For each check: the quoted text as the case
wrote it, or that a model judged it.

**Locators**, the most robust first:

| Order | Locator | When |
|---|---|---|
| 1 | Accessibility id (`content-desc` on Android, `name` on iOS) | It matched only this element |
| 2 | Resource id (Android) | It matched only this element (ids shared by every row are skipped) |
| 3 | The text: `UiSelector().text(…)` (Android), then XPath on the text, hint or label | It matched only this element |
| 4 | A text inside the element | A list row is found by its title; a tap on the title reaches the row |
| 5 | Any of these with a position: `(//…)[2]`, `.instance(1)` | It matched several elements; the position is the one the run used |
| 6 | Where the element was drawn, as a tap at that point | Nothing else matched; written with a `TODO` |

The first three that apply are passed to a `find()` helper, which tries them in order and
waits up to `WAIT_S` seconds. After each action the test waits for the screen to change and
settle. When the run saw no change after a tap, it also tapped where the element was drawn.
The test does that too, but only when its own tap changed nothing (`tap(..., at=(x, y))`).

**Actions.** Tap (`click()`), type (`clear()`, `send_keys()`, then hide the keyboard), clear,
long press and swipe (W3C pointer actions), scroll (a swipe the other way), back, home, and
wait. Test data comes from `DATA`, secrets from `os.environ` by name.

**Checks.** A quoted text in `expected` becomes `WebDriverWait(driver, WAIT_S).until(...)`
on the screen's texts, descriptions and labels (case and spacing ignored, as in the run).
"is not visible" wordings wait for the text to be gone. An outcome a model judged becomes a
`TODO`.

**Other steps.** API steps use Playwright's request client (no browser needed; install
`playwright`), and SQLite steps the standard library, as in the pytest-playwright export.
Other steps become comments.

**Check it.** `hercules export RUN --check` runs the export once on the device the run used.
It needs pytest and the Appium Python client in the Python that runs Hercules (for example
`uv run --with Appium-Python-Client hercules export RUN --check`). For a local server it
first checks that Appium answers, and stops with the command to start it when it does not.
When the server cannot start a session, the result says to check the device. The test runs
in `<run>/export-check/` with only the environment it needs, plus the `APPIUM_*` variables.

iOS needs the XCUITest driver and an iOS device or simulator. iOS has no system back action:
a back step becomes a `TODO` to tap the app's back button.

## For coding agents

The export is a reference, not the final test. `hercules_export_code` returns the code, the
spec, a suggested path (`target_dir` sets the folder), the environment variables and the
notes, with this guidance: adapt the code to the repository's test conventions (location,
fixtures, page objects, config, naming), keep secrets in environment variables, resolve
every note, then run the test and make sure it passes before keeping it. The `spec` format
gives the same information as data, for agents that prefer to write the test from scratch.

## Automation spec reference

`RunResult.automation` (schema: `cases/schemas/run_result.schema.json`, `AutomationSpec`):

| Field | Meaning |
|---|---|
| `version` | `1` |
| `case_id`, `title`, `base_url`, `viewport`, `browser` | The case; `browser` is false for API and SQL only cases |
| `data` | `{name: {kind, value, env, step}}`: `value` (the case's value as written), `secret` (`env`: the variable), `builtin`, `saved` (`step`: the step that saves it) |
| `app`, `appium` | Native app runs: the app and device (`environment.mobile`), and the server the run used (`url`, `provider`, and for a device cloud the names of its credential variables) |
| `steps[]` | `index`, `action`, `status`, `kind` (`browser`, `api`, `sql`, `quality`, `mobile`, `security`), `actions`, `checks`, `device`, `api`, `sql`, `sql_dialect`, `quality`, `payload_source`, `faithful`, `notes` |
| `device[]` | Native app steps: `kind` (`tap`, `type`, `clear`, `long_press`, `swipe`, `scroll`, `back`, `home`, `wait`), `target` (`role`, `name`, `kind`, `locators[]` with `using`, `value`, `count`, `position`, `inner`; `bounds`, `screen`), `value`, `direction`, `at_center`, `faithful`, `note` |
| `actions[]` | `kind` (`navigate`, `click`, `fill`, `select`, `upload`, `download`, `check`, `uncheck`, `hover`, `press`, `wait_text`, `wait_time`), `target`, `value`, `files` (upload: the files attached, relative to the project), `saved_as` (download: the file's name as saved), `url`, `key`, `seconds`, `text`, `select_by`, `option`, `option_role`, `option_exact`, `typed`, `navigated_to` (the path a navigation it caused ended on), `faithful`, `note` |
| `target` | `role`, `name`, `frames[]` (each `locator`, `fallbacks`, `label`), `locator`, `fallbacks`, `checked` (candidates evaluated) |
| `locator` | `strategy` (`test_id`, `role`, `label`, `placeholder`, `text`, `alt_text`, `title`, `css`), `value`, `attribute`, `role`, `name`, `exact`, `selectors` (CSS, one per shadow tree), `count` |
| `checks[]` | `kind` (`text`, `not_text`, `url`, `visible_text`, `element_visible`, `semantic`, `visual`, `file`), `expected` (a template), `match`, `file_check`, `subject`, `where` (file checks of the latest download), `form` (how it was written as code), `target`, `verdict`, `source`, `observed`, `faithful`, `note` |
