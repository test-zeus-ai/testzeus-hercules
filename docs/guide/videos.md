# Feature videos

Short videos of each part of Hercules, with a spoken walkthrough, recorded on a real machine against Acme Pantry, the demo shop in
`bench/explore` (started with no seeded bugs: `uv run python bench/explore/app.py --bugs none --port 8201`;
its sign-in page takes any name). Each video plays on this page; the MP4 files are also in
`docs/assets/videos/features`. The steps under each one do the same on your machine. For exploratory testing, see its own [walkthrough videos](explore.md#walkthrough-videos).

| # | Video | Length |
|---|---|---|
| 1 | [One line to install and set up](#1-one-line-to-install-and-set-up) | 1:28 |
| 2 | [From an empty project to a passing test](#2-from-an-empty-project-to-a-passing-test) | 2:02 |
| 3 | [Review the plan before the run starts](#3-review-the-plan-before-the-run-starts) | 1:41 |
| 4 | [Debug a run: pause, take over, answer its questions](#4-debug-a-run-pause-take-over-answer-its-questions) | 1:43 |
| 5 | [Run tests from the terminal](#5-run-tests-from-the-terminal) | 1:51 |
| 6 | [Test from your coding agent](#6-test-from-your-coding-agent) | 1:14 |
| 7 | [Browsers and browser options](#7-browsers-and-browser-options) | 1:26 |
| 8 | [Test your web app on a phone](#8-test-your-web-app-on-a-phone) | 1:22 |
| 9 | [Export a passed run as code](#9-export-a-passed-run-as-code) | 1:34 |
| 10 | [Run a passed test on a cloud](#10-run-a-passed-test-on-a-cloud) | 1:42 |
| 11 | [Import v1 projects, OpenAPI specs and requirement documents](#11-import-v1-projects-openapi-specs-and-requirement-documents) | 1:33 |
| 12 | [Visual and accessibility checks](#12-visual-and-accessibility-checks) | 1:33 |
| 13 | [Security scans](#13-security-scans) | 1:20 |
| 14 | [Run Hercules in Docker](#14-run-hercules-in-docker) | 1:46 |

## 1. One line to install and set up

https://github.com/user-attachments/assets/d7ef9efc-e6b9-4628-9693-e94e1f044662

<sub>[Download the MP4](../assets/videos/features/01-one-line-start.mp4)</sub>

`uvx testzeus-hercules` in your app's folder installs the `hercules` command, sets up the browsers you pick, connects a model (local, OpenRouter or any OpenAI-compatible endpoint) and your coding agent, asking before each change.

1. Open a terminal in your app's project folder. You need [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. If you will use a hosted OpenAI-compatible endpoint, put its key in the environment first, for example `export HERCULES_LLM_API_KEY=...`. Setup reports where it found the key and never shows it.
3. Run `uvx testzeus-hercules`.
4. Answer **Install it?** with Enter: it runs `uv tool install` so `hercules` stays on your PATH.
5. At **Browsers to set up**, type the browsers you want, for example `chromium,webkit` (Enter keeps Chromium).
6. At **Use which?**, pick a local server, `o` for OpenRouter, or `u` for another OpenAI-compatible endpoint; for `u`, give its URL (ending in `/v1`) and pick a model from the list it returns.
7. Leave the OpenRouter key empty to stay in pure mode, keep the egress policy, and choose telemetry on or off.
8. Say yes to the coding agents you use. Hercules writes `.mcp.json` and a skill into the project.
9. Run `hercules version`, then `hercules doctor` to check the model.

## 2. From an empty project to a passing test

https://github.com/user-attachments/assets/2de79358-1988-4d1f-8af8-e57383aacd25

<sub>[Download the MP4](../assets/videos/features/02-tester-ui-quick-start.mp4)</sub>

Start from an empty project in the tester UI: save an environment for the app, write a case the way a tester would, run it and watch it live, then read every step, decision and screenshot it kept.

Any web app; the video uses the demo shop in `bench/explore`.

1. Start the demo shop: `uv run python bench/explore/app.py --bugs none --port 8201`. Its sign-in page takes any name.
2. In an empty project folder, start the daemon with `hercules serve`, and in a second terminal open the UI with `hercules ui`.
3. Open **Environments**. In **New environment**, set **Name** to `shop` and **Base URL** to `http://127.0.0.1:8201`, then click **Save environment**.
4. Open **Cases**, click **Form**, and fill in **Case id** `add-tea`, **Title** `Add green tea to the cart` and **Environment** `shop`.
5. Add four steps (click **Add step** for each new one):
   1. `Open "/login"`
   2. `Fill "ana" in "Name" and click the "Sign in" button`, expected outcome `I should see "Welcome, ana"`
   3. `Open "/shop" and click the "Add Green tea to cart" button`
   4. `Open "/cart"`, expected outcome `I should see "Items in cart: 1"`
6. Open **TestCase JSON** to see the file that will be saved, then click **Save**. The case is written to `.hercules/cases/add-tea.json`.
7. Click **Run**. In **Start a run**, pick the `shop` environment, keep **Mode** at **Auto**, and click **Start run**.
8. Watch the live view while the steps tick off. Press **Space** to pause and **N** to run one step at a time.
9. When it says **Passed** (4/4 steps, 2/2 assertions), click a step to see its screenshot, **Expected** and **Actual**, and the **Decision inspector**: which element the model picked and how sure it was.
10. Scroll down to **Evidence**: everything the run saw is in `.hercules/runs/<run id>/`.

## 3. Review the plan before the run starts

https://github.com/user-attachments/assets/b84005e4-1ef4-4686-854b-6ff0c4eda146

<sub>[Download the MP4](../assets/videos/features/03-review-the-plan.mp4)</sub>

Every case becomes a plan: the actions, the success criteria the page must meet after each one, risks and lint. Review it, edit a criterion, approve it, read every criterion's result afterwards, and let strict mode refuse a plan with lint errors.

The video uses the demo shop, `uv run python bench/explore/app.py --bugs none --port 8201`, and an environment `shop` for it, as in the tester UI quick start.

1. Save a case `place-order` with four steps: `Open "/login"`; `Fill "ana" in "Name" and click the "Sign in" button` (expected `I should see "Welcome, ana"`); `Open "/shop" and click the "Add Green tea to cart" button`; `Open "/cart" and click the "Place order" button` (expected `I should see "Orders placed: 1"`).
2. Save a second case `coupon` whose test data has `"coupon": {"secret": "SHOP_COUPON"}` and a step `Fill "{{coupon}}" in "Coupon" and click the "Apply coupon" button`. Leave `SHOP_COUPON` unset.
3. In `hercules ui`, click **Start a run**, pick **Order green tea**, set **Review the plan** to **Ask before the run starts**, and click **Start run**.
4. The run waits with **Review the plan before the run starts**: plan steps under each step, **author** criteria (the case's own words, which fail the step) and **inferred** ones (the model's expectations, which drive recovery), risks such as **irreversible**, and lint (here an `unconfirmed_risk` warning: placing the order cannot be undone and the run does not ask first).
5. Click **Edit plan**, reword criterion 4.3 to `the page lists one order of $4` (a reworded criterion is judged by its meaning), and click **Save and approve**.
6. When the run has passed, open its **Plan** tab: every criterion's result, and your edit shown as before → after.
7. Click **Start a run** again, pick **Apply the team coupon**, set **Review the plan** to **Run the plan as made**, tick **Strict**, and click **Start run**. The run ends with **Error** before the browser starts, naming the missing secret and how to fix it.
8. For CI, `hercules plan .hercules/cases/coupon.json --strict` exits 1 on the same lint error.

## 4. Debug a run: pause, take over, answer its questions

https://github.com/user-attachments/assets/5fa435ff-7600-4035-9213-f4a88e514339

<sub>[Download the MP4](../assets/videos/features/04-debug-and-take-over.mp4)</sub>

Pause with Space, step with N, take over the run's own browser to do what the case forgot, fix a step that could not be done by editing its text, and approve an irreversible click before it happens.

The video uses the demo shop, `uv run python bench/explore/app.py --bugs none --port 8201`, and an environment `shop` for it, as in the tester UI quick start.

1. Save a case `reorder` with two deliberate mistakes: `Open "/login"`; `Fill "ana" in "Name"`; `Click the "Sign in" button` (expected `I should see "Welcome, ana"`); `Click the "Add Matcha to cart" button` (it never opens the shop, and the shop sells no matcha); `Open "/cart" and click the "Place order" button` (expected `I should see "Orders placed: 1"`).
2. In `hercules ui`, click **Start a run**, pick **Order three green teas**, and under **Human in the loop** tick **Confirm destructive clicks** and **Ask when a step fails**. Click **Start run**.
3. Press **Space** as soon as the run page opens: the run pauses before the next step. Press **N** three times to run one step at a time, until it says **Paused before step 4**.
4. Click **Take over** above the live view. Click **Shop** in the live view, click the quantity box for Green tea, then use the controls under the live view: **Backspace**, type `3` in **Type into the focused field**, and click **Type**.
5. Click **Stop driving**, then **Resume**.
6. Step 4 cannot find "Add Matcha to cart", so the run asks: **A step could not be done**. Click **Edit step…**, change the text to `Click the "Add Green tea to cart" button`, and click **Run edited step**.
7. Before clicking **Place order**, which the plan flags as irreversible, the run asks: **Confirm a destructive action**. Click **Approve**.
8. The run passes with three green teas ordered. The edited step and your answers are kept with the run; fix the case in **Cases** and use **Re-run**, which reads the case file again.

## 5. Run tests from the terminal

https://github.com/user-attachments/assets/d629ef9f-eb4e-4ba5-a78b-7af301804808

<sub>[Download the MP4](../assets/videos/features/05-run-from-the-terminal.mp4)</sub>

`hercules run` runs a case in a real browser, shows each step as it goes, and exits 0, 1 or 2 for scripts and CI. A failed step says what was expected and what the page showed, and every run keeps its evidence: screenshots, the page as text and every decision.

1. Start the demo shop: `uv run python bench/explore/app.py --bugs none --port 8201` (sign in takes any name).
2. Save a case as `.hercules/cases/add-to-cart.json` with `"environment": {"base_url": "http://127.0.0.1:8201"}` and four steps: open `/login`, sign in as `ana`, add Green tea in the Shop, and open the Cart expecting "The cart lists Green tea and the total is $4".
3. Run `hercules run .hercules/cases/add-to-cart.json`, then `echo $?`: `0` means passed.
4. Save a second case, `coupon.json`, whose last step fills `SAVE10` in **Coupon**, clicks **Apply coupon** and expects "The cart says the coupon was applied". Run it: it fails with exit code `1`, and the summary shows the expected outcome and the page text ("That coupon is not valid").
5. Look at the evidence: `ls .hercules/runs/<run_id> .hercules/runs/<run_id>/steps/4`, and `jq '.steps[3]' .hercules/runs/<run_id>/run.json`.
6. Fix the case (the valid code is `PANTRY10`, and the page says "Coupon applied") and run it again with `--json` for CI: `hercules run .hercules/cases/coupon.json --json | jq '{status, steps: [.steps[].status]}'`.

## 6. Test from your coding agent

https://github.com/user-attachments/assets/e21a2f45-1960-40f9-b6c0-16734bbf9d9f

<sub>[Download the MP4](../assets/videos/features/06-test-from-your-coding-agent.mp4)</sub>

One command gives Claude Code (or Codex, Cursor, GitHub Copilot, Gemini CLI) the Hercules tools; then ask for a test in plain words and the agent writes the case, runs it in a real browser and reports the result.

1. In your app's project, connect your agent: `hercules init --agent claude` (or `codex`, `cursor`, `copilot`, `gemini`). It writes `.mcp.json`, a Hercules skill and a QA agent for Claude Code.
2. Start the agent in the project (`claude`) and allow the project's Hercules MCP server when asked.
3. Ask for a test in your own words, for example: "Write a Hercules test for the shop at http://127.0.0.1:8201: sign in as ana, add green tea to the cart, and check the cart total is $4. Use Hercules: save the case under .hercules/cases, run it, and tell me the result."
4. The agent saves the case (here `.hercules/cases/green-tea-cart-total.json`), runs it through the Hercules tools and reports the steps, assertions and a link to watch the replay in the tester UI.
5. Rerun the saved case after your next change, from the agent or with `hercules run .hercules/cases/green-tea-cart-total.json`.

Recorded with the demo shop from `bench/explore` (`uv run python bench/explore/app.py --bugs none --port 8201`) and Claude Code; Claude Code's start banner was edited to leave out the account name.

## 7. Browsers and browser options

https://github.com/user-attachments/assets/52f6135c-46e0-41bc-9226-cf5c564e364e

<sub>[Download the MP4](../assets/videos/features/07-browsers-and-options.mp4)</sub>

Save an environment that runs WebKit in German, on Berlin time, already signed in from an auth JSON and sending an extra header, then run a case on it and see every option arrive.

1. Save a signed-in state for your app with Playwright's `context.storage_state(path=".hercules/auth/ana.json")` (see [Signed-in state](browsers.md#signed-in-state-auth-json)), and install WebKit: `python -m playwright install webkit`.
2. Run `hercules serve`, then `hercules ui`, and open **Environments → New environment**.
3. Name it `staging-de`, set the base URL, and pick **WebKit (Safari's engine)** under **Browser**.
4. Open **Browser options**: set **Locale** `de-DE`, **Time zone** `Europe/Berlin` and **Signed-in state (auth JSON)** `.hercules/auth/ana.json`.
5. Under **More Playwright options (JSON)** add `{"extra_http_headers": {"X-Tenant": "acme"}}` (a header value can be `{"secret": "ENV_VAR"}` instead), then click **Save environment**.
6. Open **Cases**, pick a case that starts on a signed-in page (no sign-in step), click **Run**, choose `staging-de` under **Environment** and click **Start run**.
7. Watch WebKit run it in the live view (Firefox and WebKit send screenshots), then open each step's screenshot.

In the video, step 2 of the case opens a small local page that prints the browser's language, time zone, engine and request headers, to show the options reached the browser.

## 8. Test your web app on a phone

https://github.com/user-attachments/assets/39fde2a1-5193-483b-b21c-0147e99f9aa3

<sub>[Download the MP4](../assets/videos/features/08-mobile-web.mp4)</sub>

Give an environment a Playwright device profile such as `iPhone 15`, run any web case on it, and watch the phone-sized run live; every step keeps the phone's screenshot.

1. Run `hercules serve`, then `hercules ui`, and open **Environments → New environment**.
2. Name it `iphone`, keep **Web app (browser)**, set the base URL, and type `iPhone 15` under **Phone or tablet** (any Playwright device name: `Pixel 7`, `iPad Pro 11`, ...). Leave **Viewport** empty so the device's own screen size is used, then click **Save environment**.
3. Open **Cases**, pick a case (nothing in it needs to be phone-specific), click **Run**, choose `iphone` under **Environment** and click **Start run**.
4. Watch the live view: its header shows the phone's screen size (393 × 659 for an iPhone 15).
5. When it passes, open a step and choose **Plain** to see its phone-sized screenshot.

From the terminal: `hercules run .hercules/cases/your-case.json --env iphone`. Native Android and iOS apps run through Appium; see [Mobile testing](mobile-testing.md).

In the video, the last step opens a small local page that prints the screen size, pixel ratio and device, to show the profile reached the browser.

## 9. Export a passed run as code

https://github.com/user-attachments/assets/95eb6377-6cb6-4bcb-96d4-f82f8b525260

<sub>[Download the MP4](../assets/videos/features/09-export-as-code.mp4)</sub>

Turn a passed run into a Playwright Test or pytest-playwright file with real locators and expects, then `hercules export --check` runs it once and tells you it passes, with no Hercules or model needed at test time.

1. In the tester UI, open a run that passed and click **Export as code**.
2. Read the **Playwright (TypeScript)** file: the suggested file name, how many steps were exported, the environment variables it reads (`BASE_URL`), and a locator plus `expect` for every step, with other unique locators in comments.
3. Click **pytest (Python)** for the pytest-playwright version, and **Download** (or **Copy**) the one you want.
4. Or, from the terminal, write it and check it in one go (needs `pip install pytest-playwright`):

   ```bash
   hercules export RUN_ID --format playwright-py --out tests/test_order_green_tea.py --check
   ```

   It writes the file, runs it once in the run's browser and prints `Export check: passed`, with the test file, a recording and a trace under the run's `export-check/` folder.
5. `hercules export RUN_ID --out tests/order-green-tea.spec.ts` writes the Playwright Test version.

See [Exporting runs as code](exporting-code.md).

## 10. Run a passed test on a cloud

https://github.com/user-attachments/assets/3487e4f0-acd3-459e-89d6-5230f739bdce

<sub>[Download the MP4](../assets/videos/features/10-run-on-a-cloud.mp4)</sub>

Connect BrowserStack, Sauce Labs or TestMu AI and run any passed test on the cloud's browsers from its run page, or download it as a ready-made project for that cloud's CI. The video has no cloud account, so a local Playwright server stands in for the cloud.

1. Open **Integrations** in the tester UI, enter your cloud's user name and access key, and click **Connect** (keys are saved in `~/.hercules/clouds.env`, readable only by you).
2. Open a run that passed and click **Run on a cloud**. Pick the cloud, the browser (default: the run's own) and the operating system.
3. With a connected cloud, click **Run on BrowserStack** (or Sauce Labs, TestMu AI) and follow it to the session link. Without one, click **Download project for BrowserStack CI (.zip)**, or run `hercules cloud bundle RUN_ID --cloud browserstack --out order-green-tea.zip`.
4. Unzip it: `tests/test_<case>.py`, `conftest.py`, `requirements.txt`, `browserstack.yml` and a `README.md`. Install with `pip install -r requirements.txt`.
5. In CI, set `BROWSERSTACK_USERNAME` and `BROWSERSTACK_ACCESS_KEY` and run `pytest tests`.
6. To try it with no account, start a local Playwright server and point the project at it:

   ```bash
   python -m playwright run-server --port 3000 --host 127.0.0.1 &
   HERCULES_CLOUD_WS_ENDPOINT=ws://127.0.0.1:3000/ pytest tests -q
   ```

See [Running tests on clouds](clouds.md).

## 11. Import v1 projects, OpenAPI specs and requirement documents

https://github.com/user-attachments/assets/39da031d-ecb8-470a-b861-323c4d2722bc

<sub>[Download the MP4](../assets/videos/features/11-import-v1-and-specs.mp4)</sub>

`hercules import` turns what you already have into test cases: a Hercules v1 project's Gherkin features, test data and `.env`; an OpenAPI spec, one API case per operation; and a requirements document, which your model drafts into cases marked draft for review. Credentials become secret references by name, never values.

1. In your project folder, import a v1 project (the folder that holds `opt/`): `hercules import v1 ../my-v1-project`. Add `--dry-run` first to see what would be written.
2. Read the report: one case per scenario (one per `Examples` row of a `Scenario Outline`), an environment from `.env`, the v1 engine settings it did not carry over, and the secrets to set (here `PASSWORD`).
3. Look at a converted case: `jq '{title, test_data, steps: [.steps[] | .expected // .action]}' .hercules/cases/<case>.json`. The password is `{"secret": "PASSWORD"}`.
4. Import an OpenAPI 3.x spec: `hercules import openapi docs/openapi.yaml` (add `--base-url` if the spec's server is not where the API runs). Each case sends the request and checks the documented status and response schema; the API key header reads a secret by name.
5. Import a requirements document (Markdown, text, PDF or Word): `hercules import doc docs/coupons.md --draft`. The model proposes cases with `"status": "draft"`.
6. Review each draft, fix its steps, set `"status": "ready"`, then run it with `hercules run`.

## 12. Visual and accessibility checks

https://github.com/user-attachments/assets/bc28ae53-a0c2-4996-893a-35f3630cb22e

<sub>[Download the MP4](../assets/videos/features/12-visual-and-accessibility.mp4)</sub>

Turn on **Accessibility** and **Visual** when you start a run and every page the case reaches is audited: WCAG findings with a selector and a fix that was tried in the page, layout problems at phone width, and a baseline screenshot that the next run is compared against.

1. Open a saved case in the tester UI (**Cases**) and click **Run**.
2. Under **Quality checks**, tick **Accessibility** and **Visual**. Set **Fail the run on** to **Serious or worse** so serious findings fail the run (without it, findings are only reported).
3. Click **Start run**. When the run ends, scroll to **Quality checks**: the counts by impact, then each finding with its rule, WCAG criterion, the element's selector and a **Suggested fix**. **Verified** means the fix was applied in the live page and the check passed again; copy the snippet into your code.
4. The first run saves a visual baseline for each page under `.hercules/baselines/`. Change the page, then click **Re-run**: **Visual comparisons** shows the baseline, this run and the difference (changed pixels in red).
5. The same from the terminal or CI: `hercules run .hercules/cases/checkout.json --a11y --visual --quality-fail-on serious`.

Recorded against a small checkout page with a few accessibility problems on purpose (an icon button without a name, images without alt text, a low-contrast button, a card wider than a phone screen); the model was a hosted text-only model, so the visual change is reported but not classified.

## 13. Security scans

https://github.com/user-attachments/assets/1c37e682-4a4b-44ed-8ce1-1b10172a69f7

<sub>[Download the MP4](../assets/videos/features/13-security-scan.mp4)</sub>

`hercules scan` checks your app for known security issues with the open-source nuclei scanner, only against the app under test and without intrusive templates unless you ask. Put the same scan in a test case so it runs with your other tests and fails the run on a finding at or above the severity you choose.

1. Install nuclei (`brew install nuclei`, or `hercules scan --install`), then `hercules scan --update-templates`. `hercules doctor` shows the scanner on its `scanner` line.
2. Scan an app you own: `hercules scan --base-url http://127.0.0.1:8214 --tags exposure,misconfig --fail-on medium`. The demo serves a site that leaks its `.git/config`.
3. Read the result: the findings at or above `medium` first (here `git-config`), the rest below. `echo $?` prints `1` because a finding reached `--fail-on`.
4. Look at the evidence in `.hercules/scans/<id>/`: `summary.txt`, `findings.jsonl` (for example `jq -c '{severity, template_id, matched_at}' .hercules/scans/*/findings.jsonl`) and `scan.json`.
5. Save the scan as a case step, `.hercules/cases/site-security.json`, with `"security": {"tags": ["exposure", "misconfig"], "fail_on": "medium"}` and `"timeout_s": 600`.
6. Run it: `hercules run .hercules/cases/site-security.json`. The step fails as a product bug and lists the finding; the run's `Security:` line counts the findings by severity.

## 14. Run Hercules in Docker

https://github.com/user-attachments/assets/0bb2c518-9474-4e5a-bfbd-e558aab29232

<sub>[Download the MP4](../assets/videos/features/14-docker.mp4)</sub>

Build the Hercules image, serve the tester UI from a container with your project mounted and the model key passed by name, then sign in and run a case: the same UI, runs and evidence as without Docker.

1. From a clone of the repository, build the image (a few minutes the first time): `docker build -t hercules .`
2. In your app's project, put the model settings in `.hercules/config.yaml`. They name the key's variable (`api_key_env`), never the key:

   ```yaml
   llm:
     provider: openai-compatible
     base_url: https://api.example.com/v1
     model: your-model
     api_key_env: MODEL_API_KEY
   ```

3. Start the tester UI in a container, passing the key by name only:

   ```bash
   docker run -d --name hercules --shm-size=1g -p 127.0.0.1:7414:7414 \
     -v "$PWD:/work" -v hercules-home:/home/hercules/.hercules -e MODEL_API_KEY \
     hercules serve --project /work --host 0.0.0.0 --port 7414 --allow-remote
   ```

   (With the default port, `docker run ... -p 127.0.0.1:7373:7373 ... hercules` is enough.)
4. `docker logs hercules` prints a one-time sign-in link. Open it in your browser (`docker exec hercules hercules ui --print` gives a new one).
5. Open **Cases**, pick a case and click **Run**, then **Start run**. A case that tests an app on your machine reaches it as `host.docker.internal` (for example `http://host.docker.internal:8201`).
6. Runs and evidence are written to `.hercules/runs/` in your project folder. In CI: `docker run --rm -v "$PWD:/work" -e MODEL_API_KEY hercules run .hercules/cases/add-to-cart.json` exits with Hercules' own code.

Recorded with the demo shop from `bench/explore` (`uv run python bench/explore/app.py --bugs none --port 8201`) and a hosted OpenAI-compatible model.
