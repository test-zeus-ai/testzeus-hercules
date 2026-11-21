# Running tests on BrowserStack, Sauce Labs and TestMu AI

> **Watch it:** [Run a passed test on a cloud (1:53)](videos.md#10-run-a-passed-test-on-a-cloud).

Hercules turns a passed run into a plain test file ([Exporting runs as code](exporting-code.md)).
Connect a cloud once and Hercules sends that test to the cloud and runs it there, in the
cloud's browsers or on its real devices, then shows the result and a link to the cloud's
session. Hercules also writes the test as a ready-to-run project with the cloud's own CI
configuration, so the same test can run from your pipeline.

Supported clouds:

| Cloud | Web tests | Native app tests |
|---|---|---|
| [BrowserStack](https://www.browserstack.com) | Hercules runs the test in a BrowserStack browser | On BrowserStack devices (App Automate) |
| [Sauce Labs](https://saucelabs.com) | `saucectl` runs the test in Sauce's cloud | On Sauce Labs devices |
| [TestMu AI](https://www.testmuai.com) (formerly LambdaTest) | Hercules runs the test in a TestMu browser | On TestMu real devices |

## Before you start

- **Egress policy.** A cloud is a remote service, so the egress policy must be `hybrid-text`
  or `hybrid`. Under `local`, nothing is sent to a cloud
  ([Modes and privacy](modes-and-privacy.md)).
- **Runner tools.** Install the `cloud` extra once. It adds pytest, pytest-playwright and the
  Appium Python client, which run the exported tests:

  ```bash
  pip install 'testzeus-hercules[cloud]'
  ```

- **Sauce Labs web tests only:** install [saucectl](https://docs.saucelabs.com/dev/cli/saucectl/)
  (`npm i -g saucectl`). Sauce Labs runs Playwright tests only through its own runner;
  native app tests don't need it.

## Connect a cloud

### In the tester UI

Open **Integrations**. Each cloud has a card; its logo and name link to the cloud's website. Paste your user name and access key and choose
**Connect**: Hercules saves them and checks them with the cloud straight away. The card shows
whether the cloud is connected, as whom, and what it can run on this machine. If something is
missing, such as `saucectl` or the `cloud` extra, the card says what to install.

Where to find your key:

- **BrowserStack:** Account → Profile → Access key.
- **Sauce Labs:** User settings → Access key.
- **TestMu AI:** Account settings → Password & Security → Access key.

### Sauce Labs region

A Sauce Labs account lives in one data center, `us-west-1`, `us-east-4` or `eu-central-1`, and
its keys are refused in the others. Pick yours under **Region** on the Sauce Labs card: Hercules
saves it and checks the keys there. When the keys are refused, the check also asks the other
regions and names the one that accepts them.

The region is taken from, in order: the `SAUCE_REGION` environment variable, the region saved
from the card or with `hercules cloud connect saucelabs --region`, `mobile.saucelabs.region` in
the config, else `us-west-1`.

### TestMu AI region

TestMu AI keeps its devices in three regions, `us`, `eu` and `ap`. Pick yours under **Region** on
the TestMu AI card; the device list (below) shows that region's devices. The region is taken from,
in order: the `LT_REGION` environment variable, the region saved from the card or with
`hercules cloud connect testmu --region`, `mobile.testmu.region` in the config, else `us`.

### See the devices a cloud offers

Once a cloud is connected, choose **Fetch devices** on its card. Hercules asks the cloud which
devices and browsers your account can use and lists them in two tabs:

- **Mobile:** phones and tablets, with their OS version and whether each is a real device or an
  emulator or simulator. Filter by platform, phone or tablet, real or virtual, or search by name.
- **Desktop:** each operating system with the browsers it offers and their versions.

| Cloud | Mobile | Desktop |
|---|---|---|
| BrowserStack | Real devices (App Automate) | Browsers on Windows and macOS (Automate) |
| Sauce Labs | Emulators and simulators (the newest OS of each), and real devices when the account has them | Browsers on Windows, macOS and Linux |
| TestMu AI | Real devices in the chosen region; TestMu AI does not list its emulators and simulators | Browsers on Windows and macOS |

The list is saved in `~/.hercules/cache/cloud-devices-<cloud>.json` (readable only by you) and
stays until you disconnect the cloud or save other keys; **Refresh** in the list fetches it again.
When the user name or region has changed since, the card marks the list as from older settings.
If one part cannot be read (for example a plan without App Automate), the rest is shown with a
note. The Environments page is not changed by it: its device field stays free text.

### From the command line

```bash
echo "$MY_ACCESS_KEY" | hercules cloud connect browserstack --username my_user
echo "$MY_ACCESS_KEY" | hercules cloud connect saucelabs --username my_user --region eu-central-1
echo "$MY_ACCESS_KEY" | hercules cloud connect testmu --username my_user --region eu
hercules cloud check browserstack
hercules cloud devices browserstack            # the saved list, fetched the first time
hercules cloud devices saucelabs --refresh --json
hercules cloud list
hercules cloud disconnect browserstack
```

The access key is read from standard input, never from a command-line argument, so it stays
out of your shell history.

### With environment variables

Hercules reads each cloud's credentials from these variables first:

| Cloud | User name | Access key |
|---|---|---|
| BrowserStack | `BROWSERSTACK_USERNAME` | `BROWSERSTACK_ACCESS_KEY` |
| Sauce Labs | `SAUCE_USERNAME` | `SAUCE_ACCESS_KEY` |
| TestMu AI | `LT_USERNAME` | `LT_ACCESS_KEY` |

To use other variable names, set them in `mobile.<cloud>` in `.hercules/config.yaml` (see
[Mobile testing](mobile-testing.md#device-clouds)).

### Where keys are kept

Keys you connect in the UI or with `hercules cloud connect` are saved in
`~/.hercules/clouds.env`, readable only by you (mode 600). A file other users can read is
ignored. Environment variables win over the file. No Hercules API, page or log ever shows an
access key, and keys are masked in every cloud run's output.

## Run a test on a cloud

### In the tester UI

1. Run a case until it passes.
2. On the run's page, choose **Run on a cloud** (next to **Export as code**).
3. Pick the cloud. For a web test, also pick the browser (by default the run's own: Chrome for a
   Chromium run, Firefox or WebKit for those; see [Browsers](browsers.md)) and the OS
   (Windows or macOS). For a native app test, pick the device and OS version; they default
   to the run's.
4. Choose **Run on <cloud>**.

The run page's **Cloud runs** panel follows it to the end: status, how long it took, the
cloud's message, **Open session** (the session on the cloud, with its video and logs) and
**Open dashboard**. You can cancel a cloud run while it is in progress, and open its output.

### From the command line

```bash
hercules cloud run r_20261112_101500_ab12cd --cloud testmu --browser edge --os macos
hercules cloud run r_20261112_101500_ab12cd --cloud saucelabs
hercules cloud run r_20261112_103000_ef34ab --cloud browserstack --device "Google Pixel 8" --platform-version 14
```

`--browser` is `chrome`, `edge`, `chromium`, `firefox` or `webkit`; without it the run's own
browser is used (Chrome for a Chromium run). A Firefox or WebKit run stays in that browser,
and its environment's browser options (and its signed-in auth file) go with the test.

Exit codes: `0` passed, `1` failed, `2` it could not start (not connected, a missing tool,
egress `local`, a missing environment variable).

### How each cloud runs it

- **BrowserStack and TestMu AI, web:** Hercules writes the run as a pytest-playwright test and
  runs it on your machine. Its browser runs in the cloud: the test connects to it over a
  WebSocket. At the end, the test marks the cloud session passed or failed.
- **Sauce Labs, web:** Hercules writes the run as a Playwright Test (TypeScript) project, and
  `saucectl run` uploads it and runs it in Sauce's cloud.
- **Native apps, every cloud:** Hercules uploads the app the run used (when it was a file in
  the project), then runs the exported Appium test against the cloud's Appium hub.

The test gets only the environment it needs: what Python, Playwright and `saucectl` need to
start, the variables the exported test reads (the base URL and the case's secrets), and the
cloud's credentials. Your model keys and the daemon's token are never passed to it. If the
test needs a variable the daemon does not have, such as a secret the case uses, the run does
not start and the error names the variable.

Each cloud run is kept in `.hercules/cloud-runs/<id>/`: `project/` (the files that ran),
`output.log` and `record.json`.

## Run it from CI

**Download project for <cloud> CI** in the UI, or `hercules cloud bundle`, writes the same
project as a ZIP:

```bash
hercules cloud bundle r_20261112_101500_ab12cd --cloud browserstack --out signin-browserstack.zip
```

| Cloud | Files | Run it |
|---|---|---|
| BrowserStack | `tests/test_<case>.py`, `conftest.py`, `requirements.txt`, `browserstack.yml`, `README.md` | `pytest tests`, or `HERCULES_CLOUD_CONNECT=0 browserstack-sdk pytest tests` with the BrowserStack SDK |
| TestMu AI | `tests/test_<case>.py`, `conftest.py`, `requirements.txt`, `hyperexecute.yaml`, `README.md` | `pytest tests`, or HyperExecute with `hyperexecute.yaml` |
| Sauce Labs | `tests/<case>.spec.ts`, `package.json`, `playwright.config.ts`, `.sauce/config.yml`, `README.md` | `saucectl run` |
| Native app (any cloud) | `tests/test_<case>.py`, `requirements.txt`, `README.md` | `pytest tests` |

The `conftest.py` reads these variables:

| Variable | Meaning |
|---|---|
| `HERCULES_CLOUD_BROWSER` | `chrome`, `edge` or `chromium` (default: what you chose when exporting) |
| `HERCULES_CLOUD_OS` | `windows` or `macos` |
| `HERCULES_CLOUD_BUILD` | The build name shown on the cloud (default `hercules-<case>`) |
| `HERCULES_CLOUD_CONNECT` | `0` runs the test in a local browser, or in the one a cloud runner such as the BrowserStack SDK or HyperExecute provides |
| `HERCULES_CLOUD_WS_ENDPOINT` | Connect to this Playwright server instead of the cloud's, for example a self-hosted grid |

No file in the project holds a credential: every file reads them from the environment.
`browserstack.yml` and `hyperexecute.yaml` are starting points; adjust platforms and
parallelism to your plan.

## Apps on your own machine

A cloud's browser and devices run on the internet. An app at `localhost` or on a private
network is not reachable from them. Test a deployed address, or open the cloud's tunnel
first (BrowserStack Local, Sauce Connect, or the TestMu AI tunnel) and run with that.

## Showcase

`examples/clouds/` has a ready-made demo: a case against a public demo shop and a script
that runs it, then runs its test on every connected cloud. Its README walks through the same
flow in the tester UI.

## Troubleshooting

| Message | What to do |
|---|---|
| "… is not connected" | Connect the cloud on the Integrations screen, or set its variables and restart the daemon |
| "… refused the credentials (HTTP 401)" | Copy the access key again from the cloud's settings; it changes when you regenerate it |
| "Sauce Labs refused the credentials in region … but accepts them in …" | Choose that region on the Sauce Labs card, or connect again with `--region` |
| "running the exported test needs pytest and pytest-playwright" | `pip install 'testzeus-hercules[cloud]'` |
| "Sauce Labs runs Playwright tests with saucectl, which is not installed" | `npm i -g saucectl`, or run native app tests only |
| "egress policy 'local' forbids sending tests to a cloud" | Set `egress.policy: hybrid-text` (or `hybrid`) |
| "the exported test needs these environment variables …" | Set the named variables (usually the case's secrets) where the daemon runs, and restart it |
| The test fails on the cloud at the first page | The cloud cannot reach the app's address; see [Apps on your own machine](#apps-on-your-own-machine) |
| The cloud session shows no name or status | The link and status are set by the test after it connects; check `output.log` in the cloud run's folder |
