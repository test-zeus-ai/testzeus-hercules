# Mobile Device Automation (Appium)

This framework can drive native Android and iOS applications through
[Appium](https://appium.io) in addition to browser automation. Mobile support is
fully optional: it is only active when explicitly enabled, and the Appium client
is imported lazily, so environments without it are unaffected.

## Installation

Install the framework with the `mobile` extra to pull in the Appium client:

```bash
pip install "testzeus-hercules[mobile]"
```

From a source checkout use `uv sync --extra mobile` (or `--all-extras`).

> **The Appium server is not started or managed by Hercules.** You must run
> one yourself (Appium 2.x) and point `APPIUM_SERVER_URL` at it, with the driver
> matching your target platform installed (`appium driver install uiautomator2`
> for Android, `appium driver install xcuitest` for iOS), plus a connected
> device/emulator (Android) or booted simulator (iOS). Hercules never spawns an
> Appium server or boots an emulator.

## Tested Android setup

Verified end to end on a real Android emulator (Appium 2.15 with the
`uiautomator2` 4.0 driver, an Android 35 `google_apis_playstore` arm64 AVD):

```bash
# prerequisites: a JDK and the Android SDK (platform-tools, emulator, a system image)
export ANDROID_HOME="$HOME/Library/Android/sdk"          # your SDK location
export PATH="$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"

npm install -g appium@2
appium driver install uiautomator2

# create (once) and boot an emulator, then wait until adb sees it
avdmanager create avd -n test_hercules -k "system-images;android-35;google_apis_playstore;arm64-v8a"
emulator -avd test_hercules -no-snapshot -no-audio &
adb wait-for-device && adb shell getprop sys.boot_completed   # prints 1 when booted

appium --port 4723 &                                        # started by you, not by Hercules

export DEVICE_MANAGER=appium DEVICE_OS=android
export APPIUM_SERVER_URL=http://127.0.0.1:4723
export APPIUM_DEVICE_UUID=emulator-5554                     # from `adb devices`
```

**iOS was not verified in testing** (no simulator runtime was available), so
the iOS code paths are covered by offline tests only. Running iOS needs macOS
with Xcode and its licence accepted, a booted simulator or a device
(`APPIUM_DEVICE_UUID`), `appium driver install xcuitest` and the
WebDriverAgent build prerequisites; expect to debug first-run WDA issues.

## Quickstart

1. Start an Appium server, e.g.:

   ```bash
   appium --port 4723
   ```

2. Point the framework at it and enable the mobile device manager:

   ```bash
   export APPIUM_SERVER_URL="http://127.0.0.1:4723"
   export DEVICE_MANAGER="appium"
   export DEVICE_OS="android"            # or "ios"
   ```

3. Run your test as usual. When mobile mode is enabled, a dedicated mobile
   navigation agent is instantiated and the planner gains a `mobile` helper it
   can route native-app steps to.

When `DEVICE_MANAGER` is not set to `appium` (the default is the browser-based
manager) or the Appium client is not installed, no mobile tools are registered,
no mobile agent is created, and behavior is unchanged.

## Environment variables

| Variable                 | Purpose                                              | Default                                        |
| ------------------------ | ---------------------------------------------------- | ---------------------------------------------- |
| `DEVICE_MANAGER`         | `appium` enables mobile automation                    | `playwright`                                   |
| `DEVICE_OS`              | Target platform: `android` or `ios` (case-insensitive; an `ios*` prefix such as `ios17` also selects iOS). Any other value is a configuration error when the session starts | `android` |
| `APPIUM_SERVER_URL`      | Appium server base URL (a running server is required); `http://` is prefixed when the scheme is missing | `http://127.0.0.1:<APPIUM_SERVER_PORT>` |
| `APPIUM_SERVER_PORT`     | Server port used for the default URL (1-65535; an invalid value is reported and `4723` is used) | `4723`              |
| `APPIUM_COMMAND_TIMEOUT_SECONDS` | Max seconds one Appium command may take before the tool returns an error | `120`                       |
| `APPIUM_SESSION_START_TIMEOUT_SECONDS` | Max seconds session creation may take (emulator boot, app install) | `180`                  |
| `DEVICE_NAME`            | `deviceName` capability                               | `emulator-5554` / `iPhone Simulator`           |
| `AUTOMATION_NAME`        | `automationName` capability                           | `UiAutomator2` / `XCUITest`                    |
| `APP_PATH`               | Path or URL of the app under test (the `app` capability) | unset                                       |
| `APPIUM_APK_PATH`        | Android app package path; **`APP_PATH` wins** when both are set | unset                                |
| `APPIUM_DEVICE_UUID`     | `udid` capability (specific device/simulator)         | unset                                          |
| `APPIUM_PLATFORM_VERSION`| `platformVersion` capability                          | unset                                          |
| `APPIUM_CAPABILITIES`    | JSON object merged over the built-in capabilities     | `{}`                                           |

The `platformName` capability is always derived from `DEVICE_OS`. The former
`PLATFORM_NAME` setting is deprecated and ignored: when it is set, a warning is
logged at session start. A `platformName` inside `APPIUM_CAPABILITIES` that
disagrees with `DEVICE_OS` is ignored with a warning (the element lookup,
gestures and agent prompt all follow `DEVICE_OS`).

## Launching a specific app

Hercules does not have an "open app" tool: the app under test is chosen when
the session is created, through capabilities.

- **Install and launch an app file**: set `APP_PATH` (or, Android only,
  `APPIUM_APK_PATH`; `APP_PATH` takes precedence when both are set) to a local
  `.apk` / `.app` / `.ipa` path or URL.
- **Launch an app that is already installed**, Android:

  ```bash
  export APPIUM_CAPABILITIES='{"appium:appPackage": "com.android.settings", "appium:appActivity": ".Settings"}'
  ```

- **Launch an app that is already installed**, iOS:

  ```bash
  export APPIUM_CAPABILITIES='{"appium:bundleId": "com.apple.Preferences"}'
  ```

Without any of these Appium starts the session on the device's current screen
(home screen), and since there is no app-launch tool the agent can only reach an
app by tapping its icon. There is also no page-source tool: the agent reads the
screen through `read_mobile_screen` only.

Settings are resolved **at use time** (not frozen at import) through the global
configuration, with the process environment as fallback. A key therefore works
from the environment, from a `.env` file, or from a `--config` YAML/JSON file
(e.g. `device_manager: appium`); a value present in the config wins over the
environment. `ENABLE_UI_ASSERTIONS=true` (see below) is read the same way.

## Capabilities

The manager builds a W3C capabilities dict automatically:

- **Android base**: `platformName: Android`, `automationName: UiAutomator2`,
  `noReset: false`, `newCommandTimeout: 300`, `settings[waitForIdleTimeout]: 0`
  (keeps UiAutomator2 from waiting on idle).
- **iOS base**: `platformName: iOS`, `automationName: XCUITest`, `noReset: false`
  (same as Android, which is also the Appium default), `newCommandTimeout: 300`,
  plus WDA launch/connection timeouts. Heavier WebDriverAgent lifecycle options
  are not defaulted; opt in when you need them, e.g.
  `APPIUM_CAPABILITIES='{"useNewWDA": true, "clearSystemFiles": true}'`.

Permissions are **not** granted automatically and `startIWDP` is not enabled;
opt in per run when your app needs them, e.g.
`APPIUM_CAPABILITIES='{"autoGrantPermissions": true}'` (Android) or
`'{"startIWDP": true}'` (iOS web views).

Optional keys (`app`, `udid`, `platformVersion`) are added when their
environment variables are set.

### Capability overrides

Any key can be overridden or added with `APPIUM_CAPABILITIES` (JSON object),
which is applied **last** and therefore wins over everything else. Values keep
their JSON types, so object and array capabilities (`processArguments`,
`otherApps`, `permissions`, ...) reach the server as structures, and a `null`
value removes a capability:

```bash
export APPIUM_CAPABILITIES='{"appium:noReset": true, "appActivity": ".MainActivity"}'
```

In a `--config` YAML/JSON file `appium_capabilities` may be written as a
mapping instead of a JSON string. A malformed payload is ignored gracefully (logged, treated as `{}`) so a
bad value can never block session creation.

## Available tools

When enabled, the `mobile_nav_agent` helper exposes these tools:

- **Session**: `start_mobile_session`, `close_mobile_session`
- **Gestures**: `tap`, `swipe`, `scroll_up`, `scroll_down`, `long_press_element`
  (`scroll_down` reveals content below: the finger drags from the lower to the
  upper part of the screen; `scroll_up` is the opposite)
- **Elements**: `click_element`, `enter_text`, `clear_text` (and
  `long_press_element`) take one identifier from the screen legend and try, in
  order: on Android the accessibility id (content-desc), the resource-id (bare
  or package-qualified), then the exact visible text (UiAutomator
  `UiSelector().text(...)`); on iOS the accessibility id (the element `name`,
  shown as `id=`), then the exact visible label (predicate `label == "..."`).
  Quotes and backslashes in the identifier are escaped.
- **Keys**: `press_key`, `press_hardware_key`, `press_key_sequence`,
  `press_back_button`, `press_home_button`
- **Reading**: `read_mobile_screen` (accessibility-tree legend; the reading is
  reused until an action changes the screen, pass `refresh=true` to force a
  fresh one when the app may have changed on its own),
  `get_mobile_viewport_size`, `take_mobile_screenshot`
- **Recording**: `start_screen_recording`, `stop_screen_recording`

Taps and long presses are sent as W3C touch pointer actions (the old Appium
`TouchAction` API no longer exists in the supported client versions), and
modifier combinations such as `Control+A` are sent as the main key's Android
meta-state (the client has no `key_down`/`key_up`).

Key names supported by `press_key`: `enter`, `tab`, `space`, `backspace`,
`delete`, `escape`, `up`, `down`, `left`, `right`. Hardware buttons supported by
`press_hardware_key`: `volume_up`, `volume_down`, `power`, `camera`, `call`,
`end_call`, `menu`, `back`, `home`, `app_switch` (iOS supports only
`volume_up`, `volume_down` and `home`, sent as `mobile: pressButton` with the
XCUITest names `volumeUp`/`volumeDown`/`home`). iOS has no hardware back button:
`press_back_button` taps a navigation-bar button identified as a back button
(`name`/`label` `Back`, or a `name` containing `Back`/`back` such as
`BackButton`; never an Edit/Cancel/Close button) and otherwise swipes from the
left screen edge. `press_key_sequence` accepts
comma-separated segments with `+`-joined modifiers, e.g. `Control+A,Enter`
(Android uses modifier keycodes; unknown names are rejected). On iOS only keys
with a unicode keyboard mapping can be used in sequences; unmapped keys are
reported as errors naming the unsupported key instead of being silently
skipped, and modifier combinations (e.g. `Control+A`) are rejected with an
error instead of pressing the bare key.

### UI assertions (opt-in)

`assert_ui` and `capture_ui_value` are opt-in tools (`ENABLE_UI_ASSERTIONS=true`,
see the API verification docs). The mobile agent's prompt mentions them only
when the flag is on, so the agent is never told about tools it does not have.

All tools return human-readable strings and never raise: without an active
session they answer with a "No active Appium session" hint, and without the
Appium client they answer with an install hint. Arguments are validated before
the device is touched: element tools reject an empty identifier, `tap`/`swipe`
reject non-numeric coordinates, negative `duration_ms` / `wait_before_action`
values are rejected (not clamped), and gesture coordinates are checked against
the current viewport. Error text returned to the model has the Appium server
stack trace removed and is capped at 500 characters; the full details stay in
the Hercules log. `start_screen_recording` reports an error when
`RECORD_VIDEO=false` and `take_mobile_screenshot` when `TAKE_SCREENSHOTS=false`
instead of pretending to succeed.

A custom navigation system prompt (`other_settings.system_prompt`) is appended
to the mobile agent's prompt as "Additional instructions"; it never replaces
the mobile tool rules.

## Timeouts and unresponsive servers

Every call to the Appium server is bounded. Session creation may take
`APPIUM_SESSION_START_TIMEOUT_SECONDS` (default 180), every later command
`APPIUM_COMMAND_TIMEOUT_SECONDS` (default 120). The same limit is applied to
the HTTP client socket and to the wait in Hercules, so a server that accepts
the connection and never answers produces a tool error naming
`APPIUM_SERVER_URL` instead of hanging the run. Connection-refused and
non-Appium replies produce errors that mention `APPIUM_SERVER_URL` too.

## Orphaned sessions

Active Appium sessions are quit synchronously (each call limited to 5 s) when
the process exits normally and on `SIGTERM` / `SIGINT`; any previously
installed signal handler (for example telemetry's) still runs afterwards, and
the default termination still happens. `SIGKILL` and hard crashes cannot be
intercepted: the Appium server then ends the orphaned session only after the
`newCommandTimeout` capability expires (`300` seconds in the built-in
capabilities; override it with `APPIUM_CAPABILITIES` if you want a shorter
window).

## Recording and proof artifacts

Mobile proof artifacts land under the proof folder of the current test run —
the same `proofs/<test_id>/<timestamp>/` directory the browser evidence uses
(the Appium manager is bound to the current test id):

- Videos: `proofs/<test_id>/<timestamp>/videos/<test_id>_<epoch>.mp4`
- Screenshots: `proofs/<test_id>/<timestamp>/screenshots/`

Recording starts automatically when a session is created and stops (best
effort) when the session closes. Both are gated by the existing
`RECORD_VIDEO` configuration; screenshots honor `TAKE_SCREENSHOTS`. Set
`RECORD_VIDEO=false` to disable video capture entirely. Any sessions still
open when the runner shuts down are quit best-effort (pending recording
saved, driver session ended) along with their worker pools, so an aborted
scenario cannot leak the device session.

## Safety notes

- Accessibility-tree XML from the device is parsed with DTD/entity declarations
  and oversized documents rejected up front.
- Session creation is serialized per manager; a driver that returns without a
  session id is quit rather than leaked.
- With `DEVICE_MANAGER` unset (the default) the mobile subsystem is inert: no
  tools or agent are registered, the planner prompt is unchanged and runner
  teardown touches no device session.

## Troubleshooting

- **Tools answer with an install hint** — the Appium client is missing:
  `pip install "testzeus-hercules[mobile]"`, then re-run.
- **No mobile tools / no mobile agent** — check that `DEVICE_MANAGER=appium`
  is exported in the environment of the framework process.
- **"Appium did not respond within ..."** — the server is hung or unreachable;
  check `APPIUM_SERVER_URL`, the Appium log and the two timeout variables.
- **Session creation fails** — verify `APPIUM_SERVER_URL` points at a live
  Appium server (`curl http://127.0.0.1:4723/status`) and that the matching
  driver is installed (`appium driver list --installed`).
- **Element not found** — re-read the screen with `read_mobile_screen`;
  identifiers change between screens, and the tree is re-fetched after
  every mutating action.
- **Android session hangs** — some apps keep the UI thread busy; the Android
  base capabilities already disable the idle wait (`waitForIdleTimeout: 0`).
  If hangs persist, check `adb devices` output and emulator state.
- **iOS session fails** — ensure Xcode and the platform simulator toolchain are
  installed and licensed, a booted simulator (or `APPIUM_DEVICE_UUID`) is
  available, and the WebDriverAgent dependencies build (`xcode-select --install`,
  accept the Xcode license, and pre-build WDA once manually if needed).
