# Hercules public demo-app bench

78 end-to-end scenarios against four public websites that exist for test-automation practice, with known
correct outcomes, a plain-Playwright validator that proves those outcomes, and a runner that scores what
`hercules run` reports. Unlike the offline Demo Shop bench in `bench/`, this one needs the internet and
measures Hercules on real, slow, shared, quirky apps.

```
bench/public/
├── scenarios/<app>/   single-scenario .feature files and TestCase .json files
├── manifest.yaml      expected outcome, fail_step, tags, base URL and site status per scenario
├── validate.py        lint (offline) + replay of every scenario with hand-written Playwright locators
├── pw_checks/         the replays, one module per app
├── run_public.py      runs the suite through `hercules run --json --base-url <app>` and writes a report
└── results/           validator verdicts and run reports (git-ignored)
```

## The apps

| App | Base URL | Scenarios | Demo credentials (published on the site) |
|---|---|---:|---|
| Sauce Labs "Swag Labs" | https://www.saucedemo.com | 26 | `standard_user`, `locked_out_user`, `problem_user`, `performance_glitch_user`, `error_user`, `visual_user` / `secret_sauce` |
| OrangeHRM open-source demo | https://opensource-demo.orangehrmlive.com | 20 | `Admin` / `admin123` |
| ParaBank | https://parabank.parasoft.com/parabank/ | 17 | `john` / `demo` |
| GlobalSQA "XYZ Bank" (AngularJS) | https://www.globalsqa.com (`/angularJs-protractor/BankingProject/#/login`) | 15 | none: customers are picked from a list |

These are the sites' own **public demo credentials**, printed on their login pages. They appear literally in
the `.feature` files. The three TestCase JSON cases show the secret-reference form instead and read the
password from the environment:

| JSON case | Secret | Default (set by `run_public.py` unless already in the environment) |
|---|---|---|
| `saucedemo/sd14_full_purchase_secret.json` | `SAUCE_PASSWORD` | `secret_sauce` |
| `orangehrm/oh09_admin_user_search_secret.json` | `ORANGEHRM_PASSWORD` | `admin123` |
| `parabank/pb05_transfer_funds_secret.json` | `PARABANK_PASSWORD` | `demo` |

## Etiquette

These demos are shared by everyone who practises test automation. The suite is written to leave them as
it found them:

- only the published demo accounts are used; nothing registers an account or submits real personal data
  (names such as "Ada Lovelace" and postcodes such as `10001` are obviously fictitious);
- OrangeHRM flows only read, search and filter; no employee, user or leave request is created or deleted,
  and "Forgot your password?" is only opened, never submitted with a username;
- ParaBank forms are only submitted when they will be rejected (bill pay, loan, contact info), except
  small transfers between john's own demo accounts (`pb05` and `pbn04` transfer an account to itself);
- Swag Labs and XYZ Bank keep their state in the browser, which is thrown away after each run;
- both `validate.py` and `run_public.py` run strictly one scenario at a time, with one browser, and pause
  between scenarios (`--pause-s`, default 2 s and 3 s). Do not parallelise them or loop them as a load test.

## Scenarios and the manifest

`manifest.yaml` lists, per scenario: `file`, `app`, `base_url`, `expected_status` (`passed` | `failed`),
`fail_step` (failed scenarios: the 1-based step that should fail, or a list of acceptable steps), `tags`,
`site_status` (`available` | `unavailable`) and `notes`. Scenario URLs are paths relative to the app's
base URL, which the runner passes with `hercules run --base-url`.

- **19 scenarios must fail** (tag `negative`) for a known reason: `locked_out_user` reaching the catalogue,
  `problem_user`'s broken sort, placeholder photos and last-name field, `error_user`'s dead Finish button,
  a wrong item total, a cart badge that counts an item never added, an invalid OrangeHRM login asserting the
  dashboard, an over-balance XYZ withdrawal asserted as successful, wrong balances and transfer amounts, and
  so on. They measure **false passes**.
- **18 scenarios** (tag `needs_llm`, about 23 %) use looser natural phrasing (`When they sort the products so
  the most expensive item comes first`). Hercules reads every step with a model, so the tag only groups them for
  the per-tag accuracy table.
- Other tags: `app:<name>`, `login`, `search`, `sort`, `form`, `validation`, `table`, `dropdown` (native
  select), `dropdown_custom`, `async`, `arithmetic`, `navigation`, `cart`, `secret`, `slow`.

## What is hard for an agent here

- **Custom dropdowns** (OrangeHRM Employment Status, Job Title, User Role): `div.oxd-select-text` plus
  `role=option` items, no native `<select>`. Labels are not tied to inputs, and two inputs on the PIM page
  share the placeholder "Type for hints...".
- **Autocomplete with a duplicate**: in the OrangeHRM Directory the suggestion "Peter Mac Anderson" is also a
  card on the page; only the `option` role tells them apart.
- **Unlabelled inputs**: ParaBank's login, transfer and bill-pay fields have no accessible names, only nearby
  captions ("Account #" and "Verify Account #" overlap). ParaBank binds its form handlers on window `load`;
  a click before `load` silently does nothing.
- **Same-named buttons**: XYZ Bank's "Deposit" tab and submit button share a name (the scenarios press Enter
  instead), and ParaBank's Find Transactions page has four "Find Transactions" buttons.
- **AngularJS timing**: XYZ Bank's Transactions list stays empty when the deposit and withdrawal (or the
  list itself) happen in the same second; `xb05` waits explicitly. Customer and account creation confirm
  with a JS `alert`, which Playwright auto-dismisses, so the scenarios verify through the Customers table.
- **Slow and stateful**: `performance_glitch_user` takes about 5 s to sign in; OrangeHRM pages take several
  seconds; other visitors edit OrangeHRM and ParaBank data (the admin's display name, balances, account
  numbers), so assertions use stable text and accounts are picked by position. OrangeHRM's admin password is
  occasionally changed by other visitors for a few minutes; re-run if every OrangeHRM login fails at once.
- **Text that only a careful check catches**: sort order (the scenarios pin the first product through the
  page-text order: the last sort-option label is immediately followed by the first product name),
  `problem_user`'s placeholder images (no text difference at all), and the cart badge (exposed as the cart
  button's accessible name "Cart, 2 items", not as page text).
- `visual_user` is deliberately not used for assertions: its prices are randomised on every load.

## Validating the scenarios (no Hercules)

```bash
uv run --frozen --python 3.12 python bench/public/validate.py --lint                # offline: files compile, step counts and fail steps match
uv run --frozen --python 3.12 python bench/public/validate.py                       # lint + replay all 78 (about 12 minutes)
uv run --frozen --python 3.12 python bench/public/validate.py --apps parabank --only pb0
```

The replay runs each scenario in a fresh headless Chromium context and reports whether it agrees with the
manifest: passing scenarios must complete every step, failing ones must fail at `fail_step`. Its text checks
mirror Hercules' (page `innerText`, whitespace collapsed, case folded), with stricter checks added where the
scenario's intent needs them (first product after sorting, every filtered row's status, image sources).
Apps that do not answer a probe are reported as skipped; mark their scenarios `site_status: unavailable` in
the manifest if the outage persists. Options: `--tags`, `--headed`, `--pause-s`, `--timeout-s`,
`--chromium PATH`, `--no-probe`, `--no-lint`, `--out FILE`.

## Running the suite with Hercules

```bash
uv run --frozen --python 3.12 python bench/public/run_public.py --list                       # show the selection
uv run --frozen --python 3.12 python bench/public/run_public.py --apps saucedemo,xyzbank
uv run --frozen --python 3.12 python bench/public/run_public.py --tags negative --repeat 3     # false passes, flakiness
uv run --frozen --python 3.12 python bench/public/run_public.py --mode <mode> --only sd0,ohn
```

`--mode` is passed to `hercules run` only when given; `--cli-arg=ARG` passes any other flag
(repeatable). Other options: `--timeout-s` (per run, default 300), `--pause-s`, `--include-unavailable`,
`--no-probe`, `--headed`, `--hercules PATH`, `--results-dir`.

Each run writes `results/<timestamp>/report.md` and `results.json`: accuracy, false passes, false fails,
errors, failed-for-the-wrong-reason, median / p95 duration, plus per-app and per-tag tables and per-scenario
decisions, tokens and cost read from each `RunResult`. Evidence stays under
`results/<timestamp>/projects/<scenario>/.hercules/runs/<run_id>/`.
