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

You also need a running Appium server (v2.x) with the driver matching your
target platform installed (`appium driver install uiautomator2` for Android,
`appium driver install xcuitest` for iOS), plus a connected device/emulator
(Android) or simulator (iOS).

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
| `DEVICE_OS`              | Target platform: `android` or `ios`                   | `android`                                      |
| `APPIUM_SERVER_URL`      | Appium server base URL (a running server is required) | `http://127.0.0.1:<APPIUM_SERVER_PORT>`        |
| `APPIUM_SERVER_PORT`     | Server port used for the default URL                  | `4723`                                         |
| `PLATFORM_NAME`          | `platformName` capability                             | `Android` / `iOS` (from `DEVICE_OS`)           |
| `DEVICE_NAME`            | `deviceName` capability                               | `emulator-5554` / `iPhone Simulator`           |
| `AUTOMATION_NAME`        | `automationName` capability                           | `UiAutomator2` / `XCUITest`                    |
| `APP_PATH`               | Path or URL of the app under test                     | unset                                          |
| `APPIUM_APK_PATH`        | Android app package path (alternative to `APP_PATH`)  | unset                                          |
| `APPIUM_DEVICE_UUID`     | `udid` capability (specific device/simulator)         | unset                                          |
| `APPIUM_PLATFORM_VERSION`| `platformVersion` capability                          | unset                                          |
| `APPIUM_CAPABILITIES`    | JSON object merged over the built-in capabilities     | `{}`                                           |

All variables are read directly from the environment; no framework config file
changes are needed.

## Capabilities

The manager builds a W3C capabilities dict automatically:

- **Android base**: `platformName: Android`, `automationName: UiAutomator2`,
  `autoGrantPermissions: true`, `noReset: false`, `newCommandTimeout: 300`,
  `settings[waitForIdleTimeout]: 0` (keeps UiAutomator2 from waiting on idle).
- **iOS base**: `platformName: iOS`, `automationName: XCUITest`, plus stable
  defaults for the WebDriverAgent lifecycle (`useNewWDA`, WDA timeouts,
  `preventWDAAttachments`, `clearSystemFiles`, etc.).

Optional keys (`app`, `udid`, `platformVersion`) are added when their
environment variables are set.

### Capability overrides

Any key can be overridden or added with `APPIUM_CAPABILITIES` (JSON object),
which is applied **last** and therefore wins over everything else:

```bash
export APPIUM_CAPABILITIES='{"appium:noReset": true, "appActivity": ".MainActivity"}'
```

A malformed JSON payload is ignored gracefully (logged, treated as `{}`) so a
bad value can never block session creation.

## Available tools

When enabled, the `mobile_nav_agent` helper exposes these tools:

- **Session**: `start_mobile_session`, `close_mobile_session`
- **Gestures**: `tap`, `swipe`, `scroll_up`, `scroll_down`, `long_press_element`
- **Elements** (by accessibility id): `click_element`, `enter_text`, `clear_text`
- **Keys**: `press_key`, `press_hardware_key`, `press_key_sequence`,
  `press_back_button`, `press_home_button`
- **Reading**: `read_mobile_screen` (accessibility-tree legend),
  `get_mobile_viewport_size`, `take_mobile_screenshot`
- **Recording**: `start_screen_recording`, `stop_screen_recording`

Key names supported by `press_key`: `enter`, `tab`, `space`, `backspace`,
`delete`, `escape`, `up`, `down`, `left`, `right`. Hardware buttons supported by
`press_hardware_key`: `volume_up`, `volume_down`, `power`, `camera`, `call`,
`end_call`, `menu`, `back`, `home`, `app_switch`. `press_key_sequence` accepts
comma-separated segments with `+`-joined modifiers, e.g. `Control+A,Enter`
(Android uses modifier keycodes; unknown names are rejected). On iOS only keys
with a unicode keyboard mapping can be used in sequences; unmapped keys are
reported as errors naming the unsupported key instead of being silently
skipped.

All tools return human-readable strings and never raise: without an active
session they answer with a "No active Appium session" hint, and without the
Appium client they answer with an install hint. Gesture coordinates are
validated against the current viewport.

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

## Troubleshooting

- **Tools answer with an install hint** — the Appium client is missing:
  `pip install "testzeus-hercules[mobile]"`, then re-run.
- **No mobile tools / no mobile agent** — check that `DEVICE_MANAGER=appium`
  is exported in the environment of the framework process.
- **Session creation fails** — verify `APPIUM_SERVER_URL` points at a live
  Appium server (`curl http://127.0.0.1:4723/status`) and that the matching
  driver is installed (`appium driver list --installed`).
- **Element not found** — re-read the screen with `read_mobile_screen`;
  accessibility ids change between screens, and the tree is re-fetched after
  every mutating action.
- **Android session hangs** — some apps keep the UI thread busy; the Android
  base capabilities already disable the idle wait (`waitForIdleTimeout: 0`).
  If hangs persist, check `adb devices` output and emulator state.
- **iOS session fails** — ensure Xcode and the platform simulator toolchain are
  installed and licensed, a booted simulator (or `APPIUM_DEVICE_UUID`) is
  available, and the WebDriverAgent dependencies build (`xcode-select --install`,
  accept the Xcode license, and pre-build WDA once manually if needed).
