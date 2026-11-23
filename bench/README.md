# Hercules local bench

An end-to-end test bench for `hercules run`: a small static web app ("Demo Shop"), 30 scenarios with known
correct outcomes, and a runner that scores what Hercules reports against those outcomes. It runs fully offline.

```
bench/
├── apps/          Demo Shop: plain HTML, CSS and vanilla JS (no build, no network, no CDN)
├── scenarios/     .feature files, TestCase .json files and manifest.yaml (expected outcomes)
├── serve.py       serves apps/ on localhost
├── run_bench.py   runs every scenario with `hercules run --json` and writes a report
└── results/       one folder per bench run (git-ignored)
```

## The Demo Shop app

State lives in `localStorage`, so a flow persists across pages within one browser context (each Hercules run
starts with a fresh one). The demo account is `demo@example.com` / `demo1234`.

| Page | What it exercises |
|---|---|
| `login.html` | field validation errors, a wrong-password message after a 400 ms delay, redirect on success |
| `products.html` | live search, an icon-only "Clear search" button, checkbox / select / radio filters, wishlist icon buttons |
| `product.html?id=N` | detail page, quantity input, "Add to cart" (disabled when out of stock), a toast that disappears after 3 s |
| `cart.html` | per-row quantity and remove buttons (named by `aria-label`), totals, a hover tooltip, and `<coupon-box>`: a custom element with its input, button and message in a shadow root (code `SAVE10`) |
| `checkout.html` | text / email / tel fields with inline errors, country select, shipping radios, a native date input, a terms checkbox, a submit button disabled until the form is valid, a same-origin iframe card widget (`payment-frame.html`), and a confirmation modal dialog |
| `success.html` | order confirmation |
| `orders.html` | data table with per-row View / Cancel buttons and Previous / Next pagination (23 orders, 10 per page) |
| `spa.html` | `history.pushState` routing with content loaded after 300–1500 ms and a "Load more" button |
| `faq.html` | a long page whose last question is far below the fold; answers are hidden until a question is clicked |
| `account.html` | a display name that persists only after Save (shown as "Hello, …" on `index.html`) |

Serve it on its own to explore it in a browser:

```bash
uv run python bench/serve.py              # prints the URL (a free port)
uv run python bench/serve.py --port 8000
```

## Scenarios

`scenarios/manifest.yaml` lists every scenario with:

- `expected_status` — what a careful human tester would report: `passed` or `failed`;
- `tags` — `form`, `iframe`, `shadow`, `modal`, `spa`, `async`, `table`, `negative`, `needs_llm`, plus a few
  finer ones (`toast`, `scroll`, `icon`, `hover`, `date`, `navigation`, `secret`);
- `fail_step` — for scenarios that should fail, the 1-based step that should fail;
- `notes`.

Scenarios tagged `negative` must fail (wrong expected text, an element that does not exist, a button that stays
disabled, a wrong URL, a value that was never saved, a toast that has already gone, an item that is not in the cart,
a wrong total, an invalid coupon). They measure **false passes**. Scenarios tagged `needs_llm` use looser natural
phrasing. Hercules reads every step with a model, so the tag only groups them for the per-tag accuracy table.

URLs in scenarios are relative (`/login.html`). TestCase JSON files get the app's address in
`environment.base_url`; `.feature` files get it through `hercules run --base-url`. (With an older `hercules` that
lacks `--base-url`, the runner rewrites the relative URLs of Given/When steps in a copy of the file instead.) `09_checkout_full.json`
reads its password from the `DEMO_SHOP_PASSWORD` environment variable (the runner sets it to the demo password
unless it is already set).

## Running the bench

```bash
uv run python bench/run_bench.py --cli-arg=--mode=pure  # the local model does everything
uv run python bench/run_bench.py --cli-arg=--mode=hybrid --cli-arg=--egress=hybrid-text   # JEV decides (needs OPENROUTER_API_KEY)
uv run python bench/run_bench.py --tags shadow,iframe   # scenarios having any of these tags
uv run python bench/run_bench.py --only 01_login,n0     # scenarios whose name starts with these
uv run python bench/run_bench.py --repeat 3             # run each scenario 3 times (flakiness)
uv run python bench/run_bench.py --list                 # show the selection and exit
```

Every run needs the local model server (it reads every step; see `hercules doctor`): there is no model-free
mode, because Hercules has no coded step reader or decider to fall back to.

Other options: `--timeout-s` (per run, default 240), `--port`, `--headed`, `--hercules PATH`,
`--cli-arg=ARG` (extra `hercules run` argument, repeatable), `--results-dir` and `--cache-dir DIR`. With
`--cache-dir`, every run's case-reading cache (`.hercules/cache`) is that one directory, so running the bench a
second time with the same directory measures the cached path (no reading call before the first step).

Chromium comes from `HERCULES_CHROMIUM` when set (otherwise the runner tries the local Playwright cache, then
Playwright's default). Telemetry is off for bench runs unless `HERCULES_TELEMETRY` is set explicitly.

Each scenario runs as its own `hercules run --json` subprocess in a fresh project directory
(`results/<timestamp>/projects/<scenario>/`), so runs never share state; the run's evidence (screenshots,
observations, decisions, `run.json`) stays there for inspection.

## Reading the report

`results/<timestamp>/report.md` (and the same data in `results.json`) has:

- **Summary** — accuracy (runs whose status matches `expected_status`), **false passes** (passed although the
  correct outcome is failed — the most serious kind of error), **false fails** (did not pass although a human can
  complete the scenario), errors (the run could not complete), **failed for the wrong reason** (an expected failure
  at a step other than `fail_step`), and the median / p95 wall-clock duration per run; model calls per case
  (local model and JEV/Clef) and how many case readings came from the cache.
- **Accuracy by tag** — where the engine is weak (e.g. shadow DOM, date inputs, natural phrasing).
- **Scenarios** — expected vs actual status, steps passed / total, duration, the first failing step and its reason.

To dig into a miss, open `projects/<scenario>/.hercules/runs/<run_id>/steps/<n>/`: `observation.txt` is what the
decider saw, `decisions.jsonl` what it chose, and `plain.jpg` / `som.jpg` the page at that moment.

The runner exits 0 when every run is correct and 1 otherwise.
