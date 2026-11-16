# Mobile testing

> **Watch it:** [Test your web app on a phone (1:17)](videos.md#8-test-your-web-app-on-a-phone).

Hercules tests mobile apps in three ways:

| Mode | What runs | Where the target is set |
|---|---|---|
| Mobile web | Your web app in Chromium, as a phone or tablet would show it | `environment.device` |
| Native app, local | An Android or iOS app on an emulator, simulator or device, through an Appium server on your machine | `environment.mobile` |
| Native app, device cloud | The same, on a BrowserStack, Sauce Labs or TestMu AI device | `environment.mobile` with `cloud` |

Steps are written in plain language in all three modes, as in any other case.

## Mobile web

Name a device profile in the case's environment. The browser opens with that device's
viewport, user agent, pixel ratio and touch input:

```json
{
  "id": "shop-on-a-phone",
  "title": "Browse products on an iPhone",
  "environment": {"base_url": "http://127.0.0.1:8000", "device": "iPhone 15"},
  "steps": [
    {"action": "Open the home page", "expected": "\"Welcome to Demo Shop\" is visible"},
    {"action": "Click \"Browse products\"", "expected": "a list of products is shown"}
  ]
}
```

- The names are Playwright's device profiles: `iPhone 15`, `iPhone 15 Pro Max`, `Pixel 7`,
  `Galaxy S9+`, `iPad Pro 11`, and more. Case and extra spaces do not matter. An unknown name is
  rejected when the case loads, with the closest names. The tester UI lists them all, and
  `GET /api/devices` on the daemon returns them.
- The browser is still Chromium, also for an iPhone profile.
- `viewport` together with `device` replaces the device's viewport and keeps the rest.
- Screenshots in the evidence have the device's size.
- In a `.feature` file, tag the scenario with `@device:iPhone_15` (`_` for spaces).
- `hercules export` writes the same device into the exported test:
  `test.use({ ...devices["iPhone 15"] })` for Playwright Test, and a
  `browser_context_args` fixture with `playwright.devices["iPhone 15"]` for pytest-playwright.

## Native apps

A native app case names the app and the device in `environment.mobile`. It opens no
browser: each step runs on the device.

```json
{
  "id": "android-settings",
  "title": "Open the network settings",
  "environment": {
    "mobile": {"platform": "android", "package": "com.android.settings", "activity": ".Settings", "device": "emulator-5554"}
  },
  "steps": [
    {"action": "Tap 'Network & internet'", "expected": "'Internet' is visible"},
    {"action": "Go back", "expected": "'Network & internet' is visible"}
  ]
}
```

### The `mobile` fields

| Field | Meaning |
|---|---|
| `platform` | `android` (UiAutomator2) or `ios` (XCUITest). Required. |
| `app` | An app file inside the project (`.apk`, `.aab`, `.app`, `.ipa`, `.zip`), or a device cloud app id (`bs://...`, `storage:...`, `lt://...`) |
| `package`, `activity` | Android: an installed app's package and the activity to start, for example `com.android.settings` and `.Settings` |
| `bundle_id` | iOS: an installed app's bundle id, for example `com.apple.Preferences` |
| `device` | The device name: `emulator-5554`, `Pixel 7`, `iPhone 15` |
| `udid` | A local device's id (an adb serial, a simulator UDID) |
| `platform_version` | The OS version: `14`, `17.5` |
| `appium_url` | The Appium server; default `http://127.0.0.1:4723` (`mobile.appium_url` in the config) |
| `cloud` | `browserstack`, `saucelabs` or `testmu` (TestMu AI, formerly LambdaTest) |
| `no_reset` | Keep the app's data between runs (default `false`) |
| `capabilities` | Extra Appium capabilities, for example `{"appium:language": "fr"}`. Never credentials. |

Name the app with `app`, `package` (Android) or `bundle_id` (iOS). An app file must be inside
the project; a path outside it, or a link that leads outside it, is refused.

A saved environment can hold `mobile` too, so cases only name it (`"environment": {"ref":
"android"}`, or `@env:android` in a `.feature` file). In the tester UI, open Environments and
choose the target "Native app". A native app environment needs no base URL; set one only when
the case also has API steps.

### What a step does

For each step, Hercules:

1. reads the screen: Appium's page source becomes a numbered list of elements (buttons, text,
   fields, switches, list rows), and it takes a screenshot;
2. asks the model for one action: tap, type, clear, long press, scroll, swipe, back, home or
   wait, or done when the step is complete;
3. carries out the action. A tap that changes nothing on the screen is tried once more where
   the element is drawn;
4. repeats until the model says done (at most 8 actions);
5. checks the step's `expected`.

Quoted text in `expected` is checked in code: `'Internet' is visible` passes when an element
on the screen shows "Internet", and `'SIMs' is not visible` passes when none does. Other
outcomes ("the search results list battery settings") are judged by the decider on the
element list, and on the screenshot when the egress policy allows images. Steps whose action
is `verify` (Gherkin `Then` lines) only check.

Placeholders work as in browser cases: `Type {{pin}} into the PIN field` types the value of
`{{pin}}`. The model sees the placeholder, never the value, and secret values are masked in
the evidence.

Preconditions are judged on the device's screen before step 1. API and SQL steps run beside
the device, as in a browser case. Accessibility, visual and layout checks run on web pages
only.

### Evidence

Each step writes, under `.hercules/runs/<run_id>/steps/<n>/`:

| File | Content |
|---|---|
| `plain.jpg` | The screen after the step |
| `observation.txt` | The element list the model read |
| `page_source.xml` | Appium's page source |
| `decisions.jsonl` | Each action the model chose, and the judge's answers |

`run.json`, `junit.xml` and the run timeline in the tester UI work as for browser runs.

### Exporting a native app run

A passed native app run exports as a pytest file that drives the device with the Appium
Python client:

```bash
hercules export RUN --out tests/test_android_settings.py     # appium-py is the default here
pip install pytest Appium-Python-Client
pytest tests/test_android_settings.py                         # Appium and the device running
hercules export RUN --check                                    # or let Hercules run it once
```

- The test starts the same session as the run: the app, the device, the extra capabilities.
  `APPIUM_URL`, `APPIUM_DEVICE_NAME`, `APPIUM_UDID`, `APPIUM_PLATFORM_VERSION` and
  `APPIUM_APP` override them.
- Each element is found with the locators that matched it on the screen during the run, the
  most robust first: accessibility id, resource id, then its text. A list row is found by
  the title inside it. When no locator matched, the test taps where the element was drawn,
  with a `TODO`.
- Quoted texts in `expected` become waits for the text to show (or to be gone). Outcomes a
  model judged become `TODO` comments.
- A device cloud run reads its credentials from the same variables the run used. An app
  file the run uploaded must be uploaded again: set `APPIUM_APP` to its cloud id.
- Playwright formats are refused for a native app run. `--format spec` gives the same steps
  as data. See [Exporting runs as code](exporting-code.md#native-app-runs-appium).

## Local Appium

Install Appium and the driver for your platform, then start the server:

```bash
npm install -g appium
appium driver install uiautomator2     # Android
appium driver install xcuitest         # iOS (macOS with Xcode)
appium --address 127.0.0.1 --port 4723
```

The newest XCUITest driver needs Appium 3; with Appium 2, install `xcuitest@9`.

Start an Android emulator (`emulator -avd <name>`) or connect a device (`adb devices` lists
it), or boot an iOS simulator (`xcrun simctl boot <udid>`; `xcrun simctl list devices` lists
them). `hercules doctor` says whether the Appium server answers.

A local iOS simulator runs without its window (`appium:isHeadless`), so a run does not depend
on where Xcode keeps the Simulator app. To watch it, add `"appium:isHeadless": false` to the
case's `capabilities` and open the Simulator app yourself. The first iOS session builds
WebDriverAgent onto the simulator, which takes a few minutes; on a simulator that just
booted it may take up to two minutes to start (`appium:wdaLaunchTimeout`).

If the run ends with "no Appium server answers", start Appium or set `mobile.appium_url`. If
it ends with "the device session could not start", the message is Appium's reason: no device
connected, a driver missing, or the app not installed.

An Appium server on another machine counts as remote: the `local` egress policy refuses it
unless its host is listed in `egress.local_hosts`.

## Device clouds

Set `cloud` and give the app as a file in the project (Hercules uploads it at the start of
the run) or as the app id the cloud gave you:

```json
"environment": {
  "mobile": {"platform": "android", "app": "apps/demo.apk", "cloud": "browserstack", "device": "Google Pixel 7", "platform_version": "13"}
}
```

Credentials never go in the case. Connect the cloud on the tester UI's **Integrations**
screen or with `hercules cloud connect` (saved in `~/.hercules/clouds.env`, readable only by
you), or set environment variables. The project's config names the variables; these are the
defaults:

```yaml
# .hercules/config.yaml
mobile:
  browserstack:
    username_env: BROWSERSTACK_USERNAME
    access_key_env: BROWSERSTACK_ACCESS_KEY
  saucelabs:
    username_env: SAUCE_USERNAME
    access_key_env: SAUCE_ACCESS_KEY
    region: us-west-1          # us-west-1, us-east-4 or eu-central-1; SAUCE_REGION or the Integrations screen win
  testmu:
    username_env: LT_USERNAME
    access_key_env: LT_ACCESS_KEY
```

`hub_url` and `api_url` under each cloud replace its WebDriver hub and REST API.

- A device cloud is a remote service: the `local` egress policy refuses it. Use `hybrid-text`
  or `hybrid`.
- At the end of the run, Hercules marks the cloud session passed or failed.
- A passed native run can be sent to any of the three clouds from the run page or with
  `hercules cloud run`, whichever cloud (or local device) it ran on: see
  [Running tests on clouds](clouds.md).
- Credential values are sent only to the cloud, in the HTTP authorization header; they never
  appear in evidence, results or errors.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `mobile.appium_url` (`HERCULES_APPIUM_URL`) | `http://127.0.0.1:4723` | The Appium server native app cases use when they name none |
| `mobile.session_timeout_s` | `300` | How long a device session may take to start |
| `mobile.command_timeout_s` | `120` | How long one Appium command may take |
| `mobile.browserstack`, `mobile.saucelabs`, `mobile.testmu` | see above | Credential variable names, hub, API, region (also used by web cloud runs) |

## Importing from v1

`hercules import v1` carries v1's device settings over: `RUN_DEVICE` becomes the
environment's `device`, and `DEVICE_MANAGER=appium` with `DEVICE_OS`, `DEVICE_NAME`,
`APPIUM_DEVICE_UUID`, `APPIUM_PLATFORM_VERSION`, `APPIUM_SERVER_URL` and the app's package or
bundle id from `APPIUM_CAPABILITIES` becomes its `mobile`. App files are not copied: put the
app inside the project and set `mobile.app`.
