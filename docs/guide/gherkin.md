# Writing Gherkin

A Hercules test case can be one Gherkin scenario in a `.feature` file. This page shows how
to write one that reads reliably, how to give it an environment and test data, and how the
Gherkin editor on the **Cases** screen helps. The full rules of the format are in
[Writing tests](writing-tests.md#gherkin-feature-files).

## A complete example

```gherkin
@env:lumen_beach
Feature: Checkout

  Background:
    Given I open "/index.html"

  Scenario: A signed-in customer places an order
    When I fill "{{email}}" in "Email"
    And I fill "{{password}}" in "Password"
    And I click the "Sign in" button
    Then I should see "Welcome back"
    When I click the "Shop" link
    And I click the "Ryzen 5 9600X" link
    And I click the "Add to cart" button
    And I upload "case.png" to "Case clearance photo"
    And I click the "Place order" button
    Then I should see the heading "Order placed"
    And the url should contain "/receipt"
```

- `@env:lumen_beach` names the saved environment: its base URL, variables and secrets.
- `Given` and `When` lines (and the `And`, `But` and `*` lines after them) are actions.
  `Then` lines (and the lines after them) are checks of what the page shows.
- `Background` steps run before the scenario's steps.
- One scenario per file. `Scenario Outline`, `Examples`, `Rule` and data tables are not
  supported in a runnable file; [Importing](importing.md) turns them into separate cases.

## Quote what the step names

Put every label and every value in double quotes, placeholders included:

| Write | Not |
|---|---|
| `When I fill "{{email}}" in "Email"` | `When I fill {{email}} in Email` |
| `And I click the "Sign in" button` | `And I click the Sign in button` |
| `Then I should see "Order placed"` | `Then I should see Order placed` |

A quoted label is matched to the element with exactly that name, and a quoted value is typed
exactly as written. Without quotes the model has to guess where a label ends and a value
begins, and a placeholder may be read as text. When you save a case whose steps hold an
unquoted `{{placeholder}}`, the editor lists them and offers **Add quotes and save** or
**Save as is**.

Name a control by the text a person sees on it ("Sign in", "Email"), not by its place on the
page ("the button on the right"). The [Steps](writing-tests.md#steps) table lists phrasings
that read reliably for each kind of action.

## Choose the environment

The environment gives the case its base URL, browser settings, variables and secrets.

- In the file: a tag line `@env:<name>` above `Feature`. Relative
  addresses such as `"/index.html"` open under that environment's base URL.
- When you start a run: **Start a run** preselects the case's environment; pick another one
  there to run the same case against it.
- On the command line: `hercules run checkout.feature --env lumen_beach`, or
  `--base-url http://localhost:8000` for an address without a saved environment.

In the editor, type `@env:` on a tag line to choose from the saved environments, or open the
help next to **Environment** for the same steps.

## Test data and placeholders

`{{name}}` in a step is filled in when the run starts, from:

| Source | Where it is set |
|---|---|
| The case's test data | The **Form** tab, kept beside the file in `<name>.data.json` |
| The environment's variables and secrets | The **Environments** screen |
| Built-ins | `{{run_id}}`, `{{timestamp}}` (a unique value for this run) |
| Downloads | `{{downloaded_file}}` and `{{downloaded_file_name}}`, after a download step |
| Saved values | `{{order_code}}` after a step such as `I save the response JSON "$.code" as "order_code"` |

```json
{"test_data": {"email": "ada@example.com", "password": {"secret": "SHOP_PASSWORD"}}}
```

A secret is never written in the file: the test data names the environment variable that
holds it, and models see the placeholder, never the value. A placeholder no source defines
is an error when you save. TestZeus references such as `[$.testdata.login.email]` are read as
`{{email}}`, so features copied from TestZeus run unchanged. See
[Test data and placeholders](writing-tests.md#test-data-and-placeholders) and
[Secrets](writing-tests.md#secrets).

## Checks

Write what the page should show, quoted, so it is checked in code:

```gherkin
Then I should see "Order placed"
And I should not see "Payment failed"
And the url should contain "/receipt"
And I should see the heading "Receipt"
```

An outcome only a reader could judge ("the page looks right") is judged by a model on the
page instead, and is slower and less certain. See
[Expected outcomes](writing-tests.md#expected-outcomes).

## Files

```gherkin
When I upload "case.png" to "Case clearance photo"
When I download "report.csv" by clicking "Export CSV"
Then the downloaded file has 12 rows
And column "Status" is "Paid" for Order ID 1002 in the downloaded file
When I upload "{{downloaded_file}}" to "Attachment"
```

Write `I download "<file name>" by clicking "<control>"` (or `I download the file from
"<address>"`) so the step is read as a download, even when the control opens the file in a
new tab. See [Files for upload steps](writing-tests.md#files-for-upload-steps) and
[Downloading and checking files](writing-tests.md#downloading-and-checking-files).

## The Gherkin editor

The **Gherkin** tab of the **Cases** screen checks the scenario as you type and shows the case
it becomes in the **Form** and **TestCase JSON** tabs.

**Suggestions.** Type `{{` for the test data, the chosen environment's variables and secrets,
and the built-ins; type `@env:` on a tag line for the saved environments. Arrow keys choose,
Enter or Tab inserts, Escape closes.

**Colours.** Keywords (`Given`, `When`, `Then`, `Feature`, ...) are light blue, environment
variables blue, test data brown, `@env:<name>` and `[$.input]` references purple, and
`[$.output]` references and built-ins green, so a misspelt placeholder stands out.

**Keys.**

| Keys | Does |
|---|---|
| Tab / Shift+Tab | Indent / outdent the line or the selected lines |
| Escape, then Tab | Leave the editor (Tab indents otherwise) |
| Alt+Up / Alt+Down (Option on a Mac) | Move the line or the selected lines up or down |
| Ctrl+Z (Command+Z) | Undo |
| Ctrl+Y or Ctrl+Shift+Z (Command+Shift+Z) | Redo |

**Saving.** Save checks the scenario: a line it cannot read, an unknown placeholder or an
unquoted one is reported with its line before anything is written.
