"""pytest with the Appium Python client from a native app run's automation spec: one ``test_<case>`` function.

The test starts an Appium session with the capabilities the run used (its server from ``APPIUM_URL``, the
device and app overridable by environment variables, a device cloud's credentials read from the variables
the run read them from) and repeats each device action on the element the run used. An element is found
with the locators that matched it on the screen during the run, the most robust first: a locator that
matched only that element (accessibility id, resource id, then its text as a UiSelector or XPath), then one
that matched several (with the element's position among them), and its position on the screen only as a
last resort, with a TODO. Every find waits (``WebDriverWait``), and after each action the test waits for
the screen to settle. A quoted text the run checked in code becomes a wait for that text to show (or to be
gone); a check a model judged becomes a TODO, as in the Playwright exporters.

A run can be exported for another device cloud than the one it ran on (or for a cloud when it ran on a local
server): :func:`for_cloud` retargets the spec, and ``AppiumWriter(..., provider=...)`` uses it. The clouds:

* BrowserStack: ``bstack:options`` with the credentials, on ``hub-cloud.browserstack.com``;
* Sauce Labs: ``sauce:options`` with the credentials, on ``ondemand.<region>.saucelabs.com``;
* TestMu AI (formerly LambdaTest): ``lt:options`` with the credentials (``LT_USERNAME`` / ``LT_ACCESS_KEY``), on
  ``mobile-hub.lambdatest.com``; the test marks the session passed or failed (``lambda-status=...``) before it ends.

Credentials are only ever read from environment variables in the generated code.

API and SQLite steps are written as the pytest-playwright exporter writes them (API requests through
Playwright's request client, which needs no browser); other steps become comments.
"""

from __future__ import annotations

import json
from typing import Any

from testzeus_hercules.cases.automation import AutomationCheck, AutomationSpec, AutomationStep, DeviceAction, DeviceElement, DeviceLocator
from testzeus_hercules.export.common import BASE_URL_ENV, EnvVar, one_line, safe_id, snake
from testzeus_hercules.export.python import HELPERS, PythonWriter
from testzeus_hercules.mobile.screen import xpath_literal
from testzeus_hercules.mobile.target import without_userinfo

APPIUM_URL_ENV = "APPIUM_URL"
DEFAULT_APPIUM_URL = "http://127.0.0.1:4723"
# The device and app the run used, overridable by these variables (the capability each sets).
DEVICE_ENV = (
    ("APPIUM_DEVICE_NAME", "appium:deviceName", "device name"),
    ("APPIUM_UDID", "appium:udid", "the device's id (adb serial or simulator UDID)"),
    ("APPIUM_PLATFORM_VERSION", "appium:platformVersion", "OS version"),
)
APP_ENV = "APPIUM_APP"
# The device clouds an export can target: their hub, credential variables (defaults), app id prefix and name.
CLOUDS = ("browserstack", "saucelabs", "testmu")
CLOUD_HUBS = {
    "browserstack": "https://hub-cloud.browserstack.com/wd/hub",
    "saucelabs": "https://ondemand.us-west-1.saucelabs.com:443/wd/hub",
    "testmu": "https://mobile-hub.lambdatest.com/wd/hub",
}
CLOUD_CREDENTIALS = {"browserstack": ("BROWSERSTACK_USERNAME", "BROWSERSTACK_ACCESS_KEY"), "saucelabs": ("SAUCE_USERNAME", "SAUCE_ACCESS_KEY"), "testmu": ("LT_USERNAME", "LT_ACCESS_KEY")}
CLOUD_APP_PREFIXES = {"browserstack": ("bs://",), "saucelabs": ("storage:",), "testmu": ("lt://",)}
CLOUD_NAMES = {"browserstack": "BrowserStack", "saucelabs": "Sauce Labs", "testmu": "TestMu AI"}
# Capabilities only one cloud reads: dropped when a run is exported for another.
_VENDOR_OPTIONS = {"browserstack": "bstack:", "saucelabs": "sauce:", "testmu": "lt:"}
WAIT_S = 10
MAX_LOCATORS = 3
LINE_LIMIT = 200
# The strategies by robustness (a locator that matched only the element comes before any that matched several).
_RANK = {"accessibility id": 0, "id": 1, "-android uiautomator": 2, "xpath": 3}
_BY = {"accessibility id": "AppiumBy.ACCESSIBILITY_ID", "id": "AppiumBy.ID", "-android uiautomator": "AppiumBy.ANDROID_UIAUTOMATOR", "xpath": "AppiumBy.XPATH"}
_ELEMENT_KINDS = frozenset({"tap", "type", "clear", "long_press"})

# The device helpers, written only when the test uses them (with the helpers they use).
DEVICE_HELPERS: dict[str, str] = {
    "find": '''
def find(driver: WebDriver, *locators: tuple[str, str]) -> WebElement:
    """The element the first locator finds (tried in order, the most robust first), waiting up to WAIT_S seconds."""

    def first(driver: WebDriver) -> WebElement | bool:
        for using, value in locators:
            found = driver.find_elements(using, value)
            if found:
                return found[0]
        return False

    return WebDriverWait(driver, WAIT_S).until(first, f"no element found by {locators}")
''',
    "on_element": '''
def on_element(driver: WebDriver, locators: tuple[tuple[str, str], ...], act: Callable[[WebElement], object]) -> None:
    """Find the element and act on it; found again when the screen redrew it in between (a stale element)."""
    for attempt in range(3):
        try:
            act(find(driver, *locators))
            return
        except StaleElementReferenceException:
            if attempt == 2:
                raise
''',
    "settle": '''
def settle(driver: WebDriver, before: str) -> bool:
    """Wait for the screen to change after an action (some change nothing), then to stop changing (up to STILL_S:
    an animation never stops); True when it changed."""
    source = driver.page_source
    deadline = time.monotonic() + CHANGE_S
    while source == before and time.monotonic() < deadline:
        time.sleep(POLL_S)
        source = driver.page_source
    deadline = time.monotonic() + STILL_S
    while time.monotonic() < deadline:
        time.sleep(POLL_S)
        again = driver.page_source
        if again == source:
            break
        source = again
    return source != before
''',
    "gesture": '''
def gesture(driver: WebDriver, start: tuple[int, int], end: tuple[int, int] | None = None, hold_ms: int = 50) -> None:
    """One finger: down at start, held for hold_ms, moved to end (when given), up (W3C actions)."""
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(*start).pointer_down().pause(hold_ms / 1000)
    if end is not None:
        actions.pointer_action.move_to_location(*end)
    actions.pointer_action.release()
    actions.perform()
''',
    "tap": '''
def tap(driver: WebDriver, *locators: tuple[str, str], at: tuple[int, int] | None = None) -> None:
    """Tap the element; when that changed nothing and at is given, tap there too (where the run saw it drawn)."""
    before = driver.page_source
    on_element(driver, locators, lambda element: element.click())
    if not settle(driver, before) and at is not None:
        tap_at(driver, *at)
''',
    "tap_at": """
def tap_at(driver: WebDriver, x: int, y: int) -> None:
    before = driver.page_source
    gesture(driver, (x, y))
    settle(driver, before)
""",
    "long_press": '''
def long_press(driver: WebDriver, *locators: tuple[str, str]) -> None:
    """Press and hold the element for a second."""
    before = driver.page_source

    def hold(element: WebElement) -> None:
        rect = element.rect
        gesture(driver, (rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2), hold_ms=1000)

    on_element(driver, locators, hold)
    settle(driver, before)
''',
    "type_text": '''
def type_text(driver: WebDriver, text: str, *locators: tuple[str, str]) -> None:
    """Replace the field's text (and hide the keyboard, which may cover what comes next)."""

    def replace(field: WebElement) -> None:
        field.clear()
        field.send_keys(text)

    on_element(driver, locators, replace)
    with contextlib.suppress(WebDriverException):  # no keyboard to hide is fine
        driver.hide_keyboard()
''',
    "clear": """
def clear(driver: WebDriver, *locators: tuple[str, str]) -> None:
    on_element(driver, locators, lambda field: field.clear())
""",
    "type_focused": '''
def type_focused(driver: WebDriver, text: str) -> None:
    """Replace the text of the field that has the focus (and hide the keyboard)."""
    field = driver.switch_to.active_element
    field.clear()
    field.send_keys(text)
    with contextlib.suppress(WebDriverException):  # no keyboard to hide is fine
        driver.hide_keyboard()
''',
    "swipe": '''
def swipe(driver: WebDriver, direction: str) -> None:
    """Move a finger across the middle of the screen the named way (up, down, left or right)."""
    size = driver.get_window_size()
    cx, cy, dx, dy = size["width"] // 2, size["height"] // 2, int(size["width"] * 0.3), int(size["height"] * 0.25)
    moves = {
        "up": ((cx, cy + dy), (cx, cy - dy)),
        "down": ((cx, cy - dy), (cx, cy + dy)),
        "left": ((cx + dx, cy), (cx - dx, cy)),
        "right": ((cx - dx, cy), (cx + dx, cy)),
    }
    if direction not in moves:
        raise ValueError(f"direction must be up, down, left or right, not {direction!r}")
    start, end = moves[direction]
    before = driver.page_source
    gesture(driver, start, end, hold_ms=100)
    settle(driver, before)
''',
    "scroll": '''
def scroll(driver: WebDriver, direction: str) -> None:
    """Bring what is further in direction into view (down shows what is below: the finger moves up)."""
    swipe(driver, {"down": "up", "up": "down", "left": "right", "right": "left"}.get(direction, direction))
''',
    "back": """
def back(driver: WebDriver) -> None:
    before = driver.page_source
    driver.back()
    settle(driver, before)
""",
    "home_android": """
def home(driver: WebDriver) -> None:
    driver.press_keycode(3)  # HOME
""",
    "home_ios": """
def home(driver: WebDriver) -> None:
    driver.execute_script("mobile: pressButton", {"name": "home"})
""",
    "shows": '''
def shows(driver: WebDriver, text: str) -> bool:
    """True when an element on the screen shows text (in its text, description, label or name; case and spacing
    ignored), as the run checked it."""
    wanted = " ".join(text.split()).casefold()
    names = re.findall(r'\\b(?:text|content-desc|hint|label|name|value)="([^"]*)"', driver.page_source)
    return any(wanted in " ".join(html.unescape(name).split()).casefold() for name in names)
''',
}
_DEVICE_ORDER = ("find", "on_element", "settle", "gesture", "tap", "tap_at", "long_press", "type_text", "clear", "type_focused", "swipe", "scroll", "back", "home_android", "home_ios", "shows")
_DEVICE_REQUIRES = {
    "on_element": ("find",),
    "tap": ("settle", "on_element", "tap_at"),
    "type_text": ("on_element",),
    "clear": ("on_element",),
    "tap_at": ("settle", "gesture"),
    "long_press": ("settle", "gesture", "on_element"),
    "swipe": ("settle", "gesture"),
    "scroll": ("swipe",),
    "back": ("settle",),
}


def ranked(target: DeviceElement, *, inner: bool = True) -> list[DeviceLocator]:
    """The element's locators, the most robust first: those that matched only it (its own before a text inside it),
    then those that matched several (found by position)."""
    usable = [item for item in target.locators if inner or not item.inner]
    return sorted(usable, key=lambda item: (item.count != 1, item.inner, _RANK.get(item.using, 9)))


def for_cloud(spec: AutomationSpec, cloud: str, *, device: str | None = None, platform_version: str | None = None) -> AutomationSpec:
    """``spec`` as if it had run on ``cloud``: its hub, the cloud's default credential variables (the run's own when
    it ran there), no local-only fields (udid, appium_url, a local device's name), no other cloud's capabilities, and
    an app the test reads from APPIUM_APP unless it is already that cloud's app id. ``device`` and
    ``platform_version`` replace the run's."""
    if cloud not in CLOUDS:
        raise ValueError(f"unknown device cloud {cloud!r}; use one of {', '.join(CLOUDS)}")
    if spec.app is None:
        raise ValueError("only a native app run can be exported for a device cloud")
    app: dict[str, Any] = {key: value for key, value in spec.app.items() if key not in ("udid", "appium_url")}
    server: dict[str, Any] = dict(spec.appium or {})
    ran_on = str(server.get("provider") or spec.app.get("cloud") or "local")
    app["cloud"] = cloud
    if ran_on not in CLOUDS:
        app.pop("device", None)  # a local device's name (an emulator, a simulator) names no cloud device
    if device:
        app["device"] = device
    if platform_version:
        app["platform_version"] = platform_version
    extra = app.get("capabilities")
    if isinstance(extra, dict):
        foreign = tuple(prefix for name, prefix in _VENDOR_OPTIONS.items() if name != cloud)
        app["capabilities"] = {key: value for key, value in extra.items() if not key.startswith(foreign)}
    if ran_on != cloud:
        reference = str(app.get("app") or "")
        if reference.startswith(tuple(prefix for prefixes in CLOUD_APP_PREFIXES.values() for prefix in prefixes)):
            app.pop("app")  # another cloud's id means nothing here: the test reads the app from APPIUM_APP
        server = {"provider": cloud, "url": CLOUD_HUBS[cloud]}
    else:
        server = {**{key: value for key, value in server.items() if key != "app_ref"}, "provider": cloud}
        if not server.get("url") or server.get("url_credentials"):
            server.pop("url_credentials", None)
            server["url"] = CLOUD_HUBS[cloud]
    return spec.model_copy(update={"app": app, "appium": server})


class AppiumWriter(PythonWriter):
    """pytest + Appium Python client; browser-free (see the module docstring). ``provider``: export for that device
    cloud instead of where the run ran (see :func:`for_cloud`)."""

    def __init__(self, spec: AutomationSpec, *, run_id: str, partial_note: str | None = None, provider: str | None = None) -> None:
        if provider is not None and provider != "local":
            spec = for_cloud(spec, provider)
        super().__init__(spec, run_id=run_id, partial_note=partial_note)
        self.device_helpers: set[str] = set()
        self.app = dict(spec.app or {})
        self.server = dict(spec.appium or {})
        self.platform = "ios" if self.app.get("platform") == "ios" else "android"
        self.provider = str(self.server.get("provider") or self.app.get("cloud") or "local")
        self.device_env: list[EnvVar] = []
        self.app_required = False

    def string(self, text: str) -> str:
        """A Python string literal in black's style: single quotes when that spares escaping double quotes."""
        literal = super().string(text)
        if '"' in text and "'" not in text:
            return "'" + literal[1:-1].replace('\\"', '"') + "'"
        return literal

    # ------------------------------------------------------------------ device steps

    def step(self, step: AutomationStep) -> None:
        if step.kind != "mobile":
            super().step(step)
            return
        self.open_step(step)
        self.depth += 1
        for note in step.notes:
            self.note(note)
        if not step.device and not step.checks and step.action.strip().lower() != "verify":
            if any(note.startswith("a native app step") for note in step.notes):
                self.todo(step, "this step was recorded before device actions were kept for export; run the case again with this version of Hercules")
            else:
                self.note("this step did nothing a test needs to repeat")
        for action in step.device or ():
            self.device_action(step, action)
        for check in step.checks:
            self.device_check(step, check)
        self.depth -= 1
        self.close_step(step)

    def use(self, *names: str) -> None:
        pending = list(names)
        while pending:
            name = pending.pop()
            if name not in self.device_helpers:
                self.device_helpers.add(name)
                pending.extend(_DEVICE_REQUIRES.get(name, ()))

    def device_action(self, step: AutomationStep, action: DeviceAction) -> None:
        for part in (action.note or "").split("; "):
            if not part or part.startswith("no locator finds"):
                continue  # told by the position fallback
            if action.faithful:
                self.note(part)
            else:
                self.todo(step, part)
        kind = action.kind
        if kind in _ELEMENT_KINDS:
            self.device_element_action(step, action)
        elif kind in ("swipe", "scroll"):
            self.use(kind)
            self.emit(f"{kind}(driver, {self.string(action.direction or ('down' if kind == 'scroll' else 'left'))})")
        elif kind == "back":
            if self.platform == "ios":
                self.todo(step, "iOS has no system back action; tap the app's back button")
                return
            self.use("back")
            self.emit("back(driver)")
        elif kind == "home":
            self.use("home_ios" if self.platform == "ios" else "home_android")
            self.emit("home(driver)")
        elif kind == "wait":
            self.timed = True
            self.emit("time.sleep(2)  # the run waited for the screen to finish loading")

    def device_element_action(self, step: AutomationStep, action: DeviceAction) -> None:
        kind = action.kind
        target = action.target
        found = self.find_expr(target, inner=kind in ("tap", "long_press")) if target is not None else None
        if found is None:
            self.position_action(step, action)
            return
        if kind == "tap":
            self.use("tap")
            bounds = target.bounds if target is not None else None
            if action.at_center and bounds is not None and len(bounds) == 4:
                # The screen had not changed yet when the run looked again, so it also tapped where the element was drawn:
                # the test does that only when the tap really changed nothing.
                where = f" on a {target.screen[0]}x{target.screen[1]} screen" if target is not None and target.screen and len(target.screen) == 2 else ""
                self.note(f"the run saw no change after this tap and also tapped where the element was drawn{where}; the test does too, only when the tap changes nothing")
                self.call("tap", ["driver", *found, f"at=({bounds[0] + bounds[2] // 2}, {bounds[1] + bounds[3] // 2})"])
            else:
                self.call("tap", ["driver", *found])
        elif kind == "long_press":
            self.use("long_press")
            self.call("long_press", ["driver", *found])
        elif kind == "type":
            if action.value is None:
                self.todo(step, "the text typed here was not recorded (it held a secret not referenced by name)")
                return
            self.use("type_text")
            self.call("type_text", ["driver", self.expr(action.value), *found])
        elif kind == "clear":
            self.use("clear")
            self.call("clear", ["driver", *found])

    def position_action(self, step: AutomationStep, action: DeviceAction) -> None:
        """An element no locator finds again: used where the run found it drawn (a last resort, with a TODO)."""
        target = action.target
        if target is None or target.bounds is None or len(target.bounds) != 4:
            self.todo(step, f"no locator and no position were recorded for the element to {action.kind}; write a locator by hand")
            return
        x, y, width, height = target.bounds
        center = (x + width // 2, y + height // 2)
        screen = f" on a {target.screen[0]}x{target.screen[1]} screen" if target.screen and len(target.screen) == 2 else ""
        how = "pressed" if action.kind == "long_press" else "tapped"
        self.todo(step, f'no locator found {target.role} "{target.name}" again; it is {how} where it was drawn{screen}: replace the position with a locator (an accessibility id)')
        if action.kind == "long_press":
            self.use("gesture")
            self.emit(f"gesture(driver, ({center[0]}, {center[1]}), hold_ms=1000)")
        elif action.kind == "tap":
            self.use("tap_at")
            self.emit(f"tap_at(driver, {center[0]}, {center[1]})")
        else:  # type or clear: tap the field, then use the field that has the focus
            self.use("tap_at")
            self.emit(f"tap_at(driver, {center[0]}, {center[1]})")
            if action.kind == "type" and action.value is not None:
                self.use("type_focused")
                self.emit(f"type_focused(driver, {self.expr(action.value)})")
            elif action.kind == "clear":
                self.emit("driver.switch_to.active_element.clear()")

    def call(self, name: str, args: list[str]) -> None:
        """``name(args)`` on one line when it fits, else wrapped as black wraps it (the repository's style, 200 columns)."""
        indent = self.indent_unit * self.depth
        line = f"{name}({', '.join(args)})"
        if len(indent) + len(line) <= LINE_LIMIT:
            self.emit(line)
            return
        inner = indent + self.indent_unit
        self.emit(f"{name}(")
        if len(inner) + len(", ".join(args)) <= LINE_LIMIT:
            self.lines.append(inner + ", ".join(args))
        else:
            self.lines.extend(f"{inner}{arg}," for arg in args)
        self.emit(")")

    def find_expr(self, target: DeviceElement, *, inner: bool) -> list[str] | None:
        """The element's best locators as arguments (``(AppiumBy.ID, "...")``), or None when none was recorded."""
        locators = ranked(target, inner=inner)[:MAX_LOCATORS]
        if not locators:
            return None
        if all(item.count != 1 for item in locators):
            self.note(f'no locator matched only {target.role} "{target.name}" during the run: it is found by its position among the matches')
        elif any(item.inner for item in locators if item.count == 1) and not any(not item.inner and item.count == 1 for item in locators):
            self.note(f'{target.role} "{target.name}" is found by the text drawn inside it (a tap on the text reaches it)')
        return [self.locator_tuple(item) for item in locators]

    def locator_tuple(self, item: DeviceLocator) -> str:
        """``(AppiumBy..., value)``; a locator that matched several elements picks the one the run used by position."""
        using, value = item.using, item.value
        if item.count != 1:
            if using == "-android uiautomator":
                value = f"{value}.instance({item.position - 1})"
            else:
                path = value if using == "xpath" else self.attribute_xpath(using, value)
                using, value = "xpath", f"({path})[{item.position}]"
        return f"({_BY[using]}, {self.string(value)})"

    def attribute_xpath(self, using: str, value: str) -> str:
        if using == "id":
            return f"//*[@resource-id={xpath_literal(value)}]"
        return f"//*[@{'name' if self.platform == 'ios' else 'content-desc'}={xpath_literal(value)}]"

    def device_check(self, step: AutomationStep, check: AutomationCheck) -> None:
        if check.kind in ("semantic", "visual"):
            self.note(f"Judged by its meaning by a model during the run: {json.dumps(check.expected, ensure_ascii=False)}")
            if check.note:
                self.note(f"The run's verdict: {check.note.split('; write')[0]}")
            self.todo(step, f"assert in code: {' '.join(check.expected.split())[:160]}")
            return
        if not check.faithful:
            # Its text held a secret within other text: the run kept it masked, so waiting for it could never pass.
            self.todo(step, check.note or f"check by hand: {one_line(check.expected)}")
            return
        expected = self.expr(check.expected)
        self.use("shows")
        shown = f"shows(driver, {expected})"
        if check.kind == "not_text":
            self.call("WebDriverWait(driver, WAIT_S).until", [f"lambda driver: not {shown}", self.message("expected not on the screen: ", check.expected)])
        else:
            self.call("WebDriverWait(driver, WAIT_S).until", [f"lambda driver: {shown}", self.message("expected on the screen: ", check.expected)])

    def message(self, prefix: str, template: str) -> str:
        """An assertion message naming the expected text (a secret by its name only)."""
        return self.string(prefix + one_line(template))

    # ------------------------------------------------------------------ the session

    def capability_lines(self) -> list[str]:
        """``capabilities()``: what the run asked the server for, with the device and app overridable."""
        app, cloud = self.app, self.provider if self.provider in CLOUDS else None
        caps: list[tuple[str, str]] = [
            ("platformName", self.string("iOS" if self.platform == "ios" else "Android")),
            ("appium:automationName", self.string("XCUITest" if self.platform == "ios" else "UiAutomator2")),
        ]
        for key, capability in (("package", "appium:appPackage"), ("activity", "appium:appActivity"), ("bundle_id", "appium:bundleId")):
            if app.get(key):
                caps.append((capability, self.string(str(app[key]))))
        caps.append(("appium:noReset", "True" if app.get("no_reset") else "False"))
        caps.append(("appium:newCommandTimeout", "300"))
        if self.platform == "android" and cloud is None:
            caps.append(("appium:uiautomator2ServerLaunchTimeout", "90_000"))
        if self.platform == "ios" and cloud is None:
            caps.append(("appium:isHeadless", "True"))
            caps.append(("appium:wdaLaunchTimeout", "120_000"))
        lines = ["def capabilities() -> dict[str, Any]:"]
        lines.append(f'    """The session the run started ({self.docstring(self.where())}); the APPIUM_* variables override the device and app."""')
        lines.append("    caps: dict[str, Any] = {")
        lines += [f"        {self.string(key)}: {value}," for key, value in caps]
        lines.append("    }")
        app_ref = self.app_default()
        if app_ref is not None:
            required, default = app_ref
            self.device_env.append(
                EnvVar(APP_ENV, required, "the app to install: a file (a path from the folder pytest runs in) or a device cloud app id" + ("" if required else f" (default {default})"))
            )
            self.app_required = required
            if required:
                lines.append(f'    caps["appium:app"] = os.environ[{self.string(APP_ENV)}]')
            else:
                lines.append(f'    caps["appium:app"] = os.environ.get({self.string(APP_ENV)}, {self.string(default)})')
        device = [(env, capability, purpose, str(app.get(field) or "")) for (env, capability, purpose), field in zip(DEVICE_ENV, ("device", "udid", "platform_version"))]
        if cloud is not None:
            device = [item for item in device if item[0] != "APPIUM_UDID"]
        for env, _, purpose, default in device:
            self.device_env.append(EnvVar(env, False, purpose + (f" (default {default})" if default else "")))
        if cloud == "browserstack":
            username, key = self.credentials("BROWSERSTACK_USERNAME", "BROWSERSTACK_ACCESS_KEY")
            lines.append("    options = {")
            lines.append(f'        "userName": os.environ[{self.string(username)}],')
            lines.append(f'        "accessKey": os.environ[{self.string(key)}],')
            lines.append(f'        "sessionName": {self.string(safe_id(self.spec.case_id))},')
            lines.append("    }")
            mapping = {"APPIUM_DEVICE_NAME": "deviceName", "APPIUM_PLATFORM_VERSION": "osVersion"}
            for env, _, _, default in device:
                lines.append(f"    options[{self.string(mapping[env])}] = os.environ.get({self.string(env)}, {self.string(default)})")
            lines.append('    caps["bstack:options"] = {name: value for name, value in options.items() if value}')
        elif cloud == "testmu":
            username, key = self.credentials("LT_USERNAME", "LT_ACCESS_KEY")
            lines.append("    options = {")
            lines.append(f'        "user": os.environ[{self.string(username)}],')
            lines.append(f'        "accessKey": os.environ[{self.string(key)}],')
            lines.append(f'        "platformName": {self.string("iOS" if self.platform == "ios" else "Android")},')
            lines.append('        "isRealMobile": True,')
            lines.append('        "build": "hercules",')
            lines.append(f'        "name": {self.string(safe_id(self.spec.case_id))},')
            lines.append('        "w3c": True,')
            lines.append("    }")
            mapping = {"APPIUM_DEVICE_NAME": "deviceName", "APPIUM_PLATFORM_VERSION": "platformVersion"}
            for env, _, _, default in device:
                lines.append(f"    options[{self.string(mapping[env])}] = os.environ.get({self.string(env)}, {self.string(default)})")
            lines.append('    caps["lt:options"] = {name: value for name, value in options.items() if value}')
        else:
            if cloud == "saucelabs":
                username, key = self.credentials("SAUCE_USERNAME", "SAUCE_ACCESS_KEY")
                lines.append(
                    f'    caps["sauce:options"] = {{"username": os.environ[{self.string(username)}], "accessKey": os.environ[{self.string(key)}], "name": {self.string(safe_id(self.spec.case_id))}}}'
                )
            lines.append("    device = {")
            for env, capability, _, default in device:
                lines.append(f"        {self.string(capability)}: os.environ.get({self.string(env)}, {self.string(default)}),")
            lines.append("    }")
            lines.append("    caps.update({name: value for name, value in device.items() if value})")
        extra = app.get("capabilities")
        if isinstance(extra, dict) and extra:
            lines.append(f"    caps.update({self.json_literal(extra)})  # the case's own capabilities")
        lines.append("    return caps")
        return lines

    def credentials(self, username: str, key: str) -> tuple[str, str]:
        names = (str(self.server.get("username_env") or username), str(self.server.get("access_key_env") or key))
        cloud = CLOUD_NAMES.get(self.provider, self.provider)
        self.device_env += [EnvVar(names[0], True, f"{cloud} user name"), EnvVar(names[1], True, f"{cloud} access key")]
        return names

    def app_default(self) -> tuple[bool, str] | None:
        """Whether the test needs APPIUM_APP (True) or has a default for it; None when the app is already installed."""
        reference = str(self.app.get("app") or "")
        if not reference:
            # A device cloud installs the app it is given: an export retargeted from an installed app needs one.
            return (True, "") if self.provider in CLOUDS else None
        if self.provider in CLOUDS and not reference.startswith(CLOUD_APP_PREFIXES[self.provider]):
            return True, ""  # the run uploaded a file (or used another cloud's app); the test needs the id this cloud gives it
        return False, reference

    def json_literal(self, node: Any) -> str:
        if isinstance(node, dict):
            return "{" + ", ".join(f"{self.string(str(key))}: {self.json_literal(value)}" for key, value in node.items()) + "}"
        if isinstance(node, list):
            return "[" + ", ".join(self.json_literal(item) for item in node) + "]"
        if isinstance(node, bool):
            return "True" if node else "False"
        if node is None:
            return "None"
        if isinstance(node, (int, float)):
            return json.dumps(node)
        return self.string(str(node))

    def where(self) -> str:
        app = self.app
        name = app.get("package") or app.get("bundle_id") or app.get("app") or self.platform
        device = app.get("device") or app.get("udid") or "the first available device"
        server = CLOUD_NAMES.get(self.provider, "a local Appium server")
        return f"{'iOS' if self.platform == 'ios' else 'Android'} app {name} on {device}, {server}"

    def default_url(self) -> str:
        """The server the test connects to unless APPIUM_URL says otherwise; never with credentials in it."""
        url = str(self.server.get("url") or self.app.get("appium_url") or "")
        if url:
            return without_userinfo(url)[0]
        return CLOUD_HUBS.get(self.provider, DEFAULT_APPIUM_URL)

    def url_required(self) -> bool:
        """True when the run's server URL held credentials: the test then reads the whole URL from APPIUM_URL."""
        url = str(self.server.get("url") or self.app.get("appium_url") or "")
        return bool(self.server.get("url_credentials")) or without_userinfo(url)[1]

    # ------------------------------------------------------------------ file structure

    def open_test(self) -> None:
        self.timed = False
        self.basic_auth = False
        fixtures = ["driver: WebDriver"] + (["api: APIRequestContext"] if self.api_used else [])
        self.emit(f"def test_{snake(self.spec.case_id)}({', '.join(fixtures)}) -> None:")
        self.depth += 1
        self.emit(f'"""{self.docstring(self.spec.title)}"""')
        if self.saved_used:
            self.emit("saved: dict[str, Any] = {}")
        self.depth -= 1

    def header(self) -> None:
        spec = self.spec
        used = HELPERS.closure(self.helpers)
        capability_lines = self.capability_lines()
        lines = [
            '"""' + self.docstring(spec.title),
            "",
            f"Exported by Hercules from run {safe_id(self.run_id)} (case {safe_id(spec.case_id)}). It repeats what the run did on the",
            "device, finding each element with the locators that matched it during the run (the most robust first). Adapt it",
            "to this project's tests (fixtures, page objects, conftest.py) before keeping it, then run it with pytest and the",
            "Appium Python client (pip install pytest Appium-Python-Client), with the Appium server and the device running.",
        ]
        if self.partial_note:
            lines.append(self.docstring(self.partial_note))
        env = [f"{APPIUM_URL_ENV} ({'required' if self.url_required() else 'optional'})"] + [f"{item.name} ({'required' if item.required else 'optional'})" for item in self.device_env]
        env += [f"{BASE_URL_ENV} (optional)"] if "base" in used else []
        env += [f"{name} (required)" for name in sorted(self.secrets)]
        lines.append(self.docstring("Environment: " + ", ".join(env) + "."))
        lines += ['"""', "", "from __future__ import annotations", ""]
        modules = {"os"}
        if self.device_helpers & {"settle"} or self.timed:
            modules.add("time")
        if "shows" in self.device_helpers:
            modules.update({"html", "re"})
        if self.device_helpers & {"type_text", "type_focused"} or self.provider == "testmu":
            modules.add("contextlib")
        if used & {"text"}:
            modules.add("json")
        if used & {"matches"}:
            modules.add("re")
        if "sqlite" in used:
            modules.add("sqlite3")
        if any(entry.kind == "builtin" for entry in spec.data.values()):
            modules.add("datetime")
        if self.basic_auth:
            modules.add("base64")
        lines += [f"import {name}" for name in sorted(modules)]
        lines.append("from collections.abc import Callable, Iterator" if "on_element" in self.device_helpers else "from collections.abc import Iterator")
        if "sqlite" in used:
            lines.append("from pathlib import Path")
        lines.append("from typing import Any")
        parse = sorted(
            {
                name
                for name, helper in (
                    ("urljoin", "url"),
                    ("parse_qsl", "with_query"),
                    ("urlencode", "with_query"),
                    ("urlsplit", "with_query"),
                    ("urlunsplit", "with_query"),
                    ("urlsplit", "api_url"),
                    ("urlunsplit", "api_url"),
                )
                if helper in used
            }
        )
        if parse:
            lines.append(f"from urllib.parse import {', '.join(parse)}")
        lines += ["", "import pytest", "from appium import webdriver", "from appium.options.common.base import AppiumOptions"]
        if "find" in self.device_helpers:
            lines.append("from appium.webdriver.common.appiumby import AppiumBy")
        lines.append("from appium.webdriver.webdriver import WebDriver")
        if self.api_used:
            lines.append("from playwright.sync_api import APIRequestContext, sync_playwright")
        exceptions = (["StaleElementReferenceException"] if "on_element" in self.device_helpers else []) + (
            ["WebDriverException"] if self.device_helpers & {"type_text", "type_focused"} or self.provider == "testmu" else []
        )
        if exceptions:
            lines.append(f"from selenium.common.exceptions import {', '.join(exceptions)}")
        if "gesture" in self.device_helpers:
            lines += [
                "from selenium.webdriver.common.actions import interaction",
                "from selenium.webdriver.common.actions.action_builder import ActionBuilder",
                "from selenium.webdriver.common.actions.pointer_input import PointerInput",
            ]
        if "find" in self.device_helpers:
            lines.append("from selenium.webdriver.remote.webelement import WebElement")
        lines += ["from selenium.webdriver.support.ui import WebDriverWait", ""]
        if self.url_required():
            lines.append(f"APPIUM_URL = os.environ[{self.string(APPIUM_URL_ENV)}]  # the server's URL with its credentials, e.g. {self.default_url()}")
        else:
            lines.append(f"APPIUM_URL = os.environ.get({self.string(APPIUM_URL_ENV)}, {self.string(self.default_url())})")
        lines.append(f"WAIT_S = {WAIT_S}  # how long to wait for an element or a text, in seconds")
        if "settle" in self.device_helpers:
            lines.append("CHANGE_S = 2  # how long an action may take to change the screen")
            lines.append("STILL_S = 3  # the longest to wait for the screen to stop changing")
            lines.append("POLL_S = 0.3")
        if "base" in used:
            lines.append(f"BASE_URL = os.environ.get({self.string(BASE_URL_ENV)}, {self.string(spec.base_url or '')})")
        builtins = [name for name, entry in spec.data.items() if entry.kind == "builtin"]
        if builtins:
            lines.append('TIMESTAMP = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")')
        if "run_id" in builtins:
            lines.append('RUN_ID = "run-" + TIMESTAMP')
        values = [(name, entry.value or "") for name, entry in spec.data.items() if entry.kind == "value"]
        if values:
            lines.append("DATA = {")
            for name, value in values:
                lines.append(f"    {self.string(name)}: {self.expr_builtins(value)},")
            lines.append("}")
        lines += ["", ""] + capability_lines
        lines += [
            "",
            "",
            "@pytest.fixture",
        ]
        if self.provider == "testmu":
            lines += [
                "def driver(request: pytest.FixtureRequest) -> Iterator[WebDriver]:",
                '    """An Appium session on the device, marked passed or failed on the TestMu AI dashboard and ended after the test."""',
                "    options = AppiumOptions()",
                "    options.load_capabilities(capabilities())",
                "    session = webdriver.Remote(APPIUM_URL, options=options)",
                "    failed_before = request.session.testsfailed",
                "    try:",
            ]
        else:
            lines += [
                "def driver() -> Iterator[WebDriver]:",
                '    """An Appium session on the device, ended after the test."""',
                "    options = AppiumOptions()",
                "    options.load_capabilities(capabilities())",
                "    session = webdriver.Remote(APPIUM_URL, options=options)",
                "    try:",
            ]
        if "settle" in self.device_helpers:
            # An app started cold is still drawing its first screen; a recorded scroll or swipe sent then moves less.
            lines.append('        settle(session, "")  # wait for the app\'s first screen to be drawn and still')
        lines += ["        yield session", "    finally:"]
        if self.provider == "testmu":
            lines += [
                "        with contextlib.suppress(WebDriverException):  # the dashboard status is a courtesy",
                '            session.execute_script("lambda-status=" + ("failed" if request.session.testsfailed > failed_before else "passed"))',
            ]
        lines.append("        session.quit()")
        if self.api_used:
            lines += [
                "",
                "",
                "@pytest.fixture",
                "def api() -> Iterator[APIRequestContext]:",
                '    """A request context of its own, as the run sent its API requests apart from the device (no browser needed)."""',
                "    with sync_playwright() as playwright:",
                "        context = playwright.request.new_context()",
                "        yield context",
                "        context.dispose()",
            ]
        helper_lines: list[str] = []
        for name in _DEVICE_ORDER:
            if name in self.device_helpers:
                helper_lines += ["", ""] + DEVICE_HELPERS[name].strip("\n").splitlines()
        lines += helper_lines
        for line in HELPERS.emit(self.helpers - {"base"}):
            if line and not line.startswith((" ", ")")) and lines[-1] == "" and lines[-2] != "":
                lines.append("")
            elif line and not line.startswith((" ", ")")) and lines[-1] != "":
                lines += ["", ""]
            lines.append(line)
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", ""]
        self.lines = lines

    def env(self) -> tuple[EnvVar, ...]:
        """What the test reads from the environment: the server, the device and app, a cloud's credentials, the base
        URL (API steps) and every secret."""
        server = EnvVar(APPIUM_URL_ENV, True, f"the Appium server's URL with its credentials ({self.default_url()} without them)")
        if not self.url_required():
            server = EnvVar(APPIUM_URL_ENV, False, f"the Appium server or device cloud hub (default {self.default_url()})")
        found = [server, *self.device_env]
        if self.uses_base():
            found.append(EnvVar(BASE_URL_ENV, False, f"base URL of the API (default {self.spec.base_url or 'none'})"))
        found += [EnvVar(name, True, purpose) for name, purpose in sorted(self.secrets.items())]
        return tuple(found)


def appium_server(spec: dict[str, Any]) -> tuple[str, bool]:
    """The Appium server an exported native app run's test connects to by default, and whether it is a local server
    (not a device cloud's hub); ``spec`` is the exported automation spec (JSON)."""
    writer = AppiumWriter(AutomationSpec.model_validate(spec), run_id="")
    return writer.default_url(), writer.provider not in CLOUDS
