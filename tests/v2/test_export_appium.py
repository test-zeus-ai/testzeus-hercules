"""The Appium exporter (``appium-py``): a native app run as a pytest file with the Appium Python client.

A golden file in ``fixtures/export`` holds the full export of one spec that uses every device action, every
locator case (unique, inside a row, several matches, none), test data, a secret, checks in code and judged
checks, and API, SQL and security steps. Regenerate it after an intended change with
``HERCULES_UPDATE_GOLDEN=1``. The golden compiles and is formatted as the repository formats its code
(black, 200 columns). The run-time recording (locators with their counts, typed values as templates) is
checked through the run graph against the fake Appium server.
"""

from __future__ import annotations

import ast
import io
import py_compile
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.automation import AutomationCheck, AutomationData, AutomationSpec, AutomationStep, DeviceAction, DeviceElement, DeviceLocator
from testzeus_hercules.cases.models import Step, TestCase
from testzeus_hercules.cli.main import main
from testzeus_hercules.export import ExportError, default_format, export_run
from testzeus_hercules.export.appium import AppiumWriter, appium_server, ranked
from testzeus_hercules.export.check import CheckError, appium_status, run_appium_check
from testzeus_hercules.export.common import EnvVar, ExportResult
from testzeus_hercules.mobile.screen import parse

from tests.v2.fixtures.fake_appium import FakeDevice, serve_fake_appium
from tests.v2.test_export_render import check_golden, result
from tests.v2.test_mobile_run import project, run
from tests.v2.test_mobile_session import ScriptedModel, tap, type_into

SECRET = "4417-secret-pin"
APP = {"platform": "android", "package": "com.example.shop", "activity": ".Main", "device": "emulator-5554"}
REPO = Path(__file__).resolve().parents[2]


def loc(using: str, value: str, count: int = 1, position: int = 1, inner: bool = False) -> DeviceLocator:
    return DeviceLocator(using=using, value=value, count=count, position=position, inner=inner)  # type: ignore[arg-type]


def element(name: str, *locators: DeviceLocator, role: str = "button", bounds: list[int] | None = None) -> DeviceElement:
    return DeviceElement(role=role, name=name, kind="View", locators=list(locators), bounds=bounds or [0, 100, 1080, 200], screen=[1080, 2400])


def app_spec(
    steps: list[AutomationStep], *, app: dict[str, Any] | None = None, appium: dict[str, Any] | None = None, data: dict[str, AutomationData] | None = None, base_url: str | None = None
) -> AutomationSpec:
    return AutomationSpec(
        case_id="shop-checkout",
        title="Check out on the phone",
        app=app or APP,
        appium=appium if appium is not None else {"url": "http://127.0.0.1:4723", "provider": "local"},
        browser=False,
        base_url=base_url,
        data=data or {},
        steps=steps,
    )


def mobile(index: int, action: str, *device: DeviceAction, checks: list[AutomationCheck] | None = None, notes: list[str] | None = None) -> AutomationStep:
    return AutomationStep(index=index, action=action, status="passed", kind="mobile", device=list(device), checks=checks or [], notes=notes or [])


def golden_spec() -> AutomationSpec:
    row = element("Network & internet · Wi-Fi", loc("-android uiautomator", 'new UiSelector().text("Network & internet")', inner=True), loc("xpath", "//*[@text='Network & internet']", inner=True))
    search = element("Search", loc("accessibility id", "Search"), loc("id", "com.example.shop:id/search"))
    field = element("Search products", loc("id", "com.example.shop:id/query"), loc("xpath", "//*[@hint='Search products']"), role="textbox")
    pin = element("PIN", loc("id", "com.example.shop:id/pin"), role="textbox")
    second = element("Add to cart", loc("id", "com.example.shop:id/add", count=3, position=2), loc("xpath", "//*[@text='Add to cart']", count=3, position=2))
    switch = element("Gift wrap", bounds=[900, 1200, 120, 80], role="switch")
    steps = [
        mobile(1, "Open the network settings", DeviceAction(kind="tap", target=row), checks=[AutomationCheck(kind="text", expected="Internet")]),
        mobile(
            2,
            "Search for {{product}}",
            DeviceAction(kind="tap", target=search, at_center=True),
            DeviceAction(kind="type", target=field, value="{{product}}"),
            DeviceAction(kind="clear", target=field),
            DeviceAction(kind="type", target=field, value="{{product}} {{run_id}}"),
            checks=[AutomationCheck(kind="text", expected="{{product}}"), AutomationCheck(kind="not_text", expected="No results")],
        ),
        mobile(3, "Scroll to the second item and add it", DeviceAction(kind="scroll", direction="down"), DeviceAction(kind="tap", target=second)),
        mobile(
            4,
            "Turn on gift wrap and enter the PIN",
            DeviceAction(kind="long_press", target=switch, faithful=False, note='no locator finds the element (switch "Gift wrap") again, so the run used where it was drawn'),
            DeviceAction(kind="type", target=pin, value="{{pin}}"),
            DeviceAction(kind="swipe", direction="left"),
            DeviceAction(kind="wait"),
            checks=[
                AutomationCheck(kind="semantic", expected="the cart shows one gift-wrapped item", source="llm", faithful=False, note="judged on the screen (p=0.93); write an assertion for it in code")
            ],
        ),
        mobile(5, "Leave the app", DeviceAction(kind="back"), DeviceAction(kind="home"), notes=['not repeated: tap [9] button "Close" (failed: no such element)']),
        AutomationStep(
            index=6,
            action="The order is in the API",
            status="passed",
            kind="api",
            api={"method": "GET", "path": "/api/orders/latest", "expect": {"status": 200, "json_path": [{"path": "$.status", "op": "eq", "value": "paid"}]}},
        ),
        AutomationStep(index=7, action="The order row exists", status="passed", kind="sql", sql={"db": "orders_db", "query": "SELECT id FROM orders", "expect": {"min_rows": 1}}, sql_dialect="sqlite"),
        AutomationStep(index=8, action="Scan the API", status="passed", kind="security", security={"tags": ["cve"], "fail_on": "high"}, faithful=False),
        mobile(9, "verify", checks=[AutomationCheck(kind="text", expected="Thanks, {{pin}}")]),
    ]
    data = {
        "product": AutomationData(kind="value", value="Blue mug"),
        "pin": AutomationData(kind="secret", env="SHOP_PIN"),
        "run_id": AutomationData(kind="builtin"),
        "orders_db": AutomationData(kind="value", value="sqlite:///orders.db"),
    }
    return app_spec(steps, data=data, base_url="http://shop.test")


def code_of(spec: AutomationSpec) -> str:
    return export_run(result(automation=spec), "appium-py").code


# ---------------------------------------------------------------------------- the golden file


def test_appium_golden() -> None:
    exported = export_run(result(automation=golden_spec()), "appium-py")
    check_golden("golden_appium.py.txt", exported.code)
    assert exported.format == "appium-py" and exported.filename == "tests/test_shop_checkout.py"
    names = [item.name for item in exported.env]
    assert names == ["APPIUM_URL", "APPIUM_DEVICE_NAME", "APPIUM_UDID", "APPIUM_PLATFORM_VERSION", "BASE_URL", "SHOP_PIN"]
    assert [item.name for item in exported.env if item.required] == ["SHOP_PIN"]
    assert SECRET not in exported.code


def long_locator_spec() -> AutomationSpec:
    """Calls longer than a line: three long locators, and a long expected text."""
    long_id = "com.google.android.settings.intelligence:id/open_search_view_edit_text_" + "x" * 40
    field = element(
        "Search", loc("id", long_id), loc("-android uiautomator", 'new UiSelector().text("Search settings and more")'), loc("xpath", "//*[@text='Search settings and more']"), role="textbox"
    )
    text = "A long expected text " * 5
    return app_spec(
        [mobile(1, "Type", DeviceAction(kind="type", target=field, value="{{product}}"), DeviceAction(kind="tap", target=field), checks=[AutomationCheck(kind="text", expected=text)])],
        data={"product": AutomationData(kind="value", value="mug")},
    )


@pytest.mark.parametrize("make", [golden_spec, long_locator_spec])
def test_exports_compile_and_are_black_formatted(tmp_path: Path, make: Any) -> None:
    path = tmp_path / "test_shop_checkout.py"
    path.write_text(code_of(make()), encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    black = subprocess.run([sys.executable, "-m", "black", "--target-version", "py311", "-l", "200", "--check", "--diff", "--quiet", str(path)], capture_output=True, text=True)
    if black.returncode == 1 and "No module named black" in black.stderr:
        pytest.skip("black is not installed")
    assert black.returncode == 0, black.stdout + black.stderr


def test_every_name_the_golden_uses_is_imported_or_defined() -> None:
    tree = ast.parse(code_of(golden_spec()))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    defined |= {alias.asname or alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    defined |= {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    defined |= {arg.arg for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.Lambda)) for arg in [*node.args.args, *node.args.kwonlyargs, node.args.vararg] if arg is not None}
    defined |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    builtins = set(dir(__import__("builtins")))
    assert used - defined - builtins == set()


def test_rendering_is_deterministic() -> None:
    assert code_of(golden_spec()) == code_of(golden_spec())


# ---------------------------------------------------------------------------- locators


def test_the_most_robust_locator_comes_first() -> None:
    target = element(
        "Pay",
        loc("xpath", "//*[@text='Pay']", count=2, position=1),
        loc("-android uiautomator", 'new UiSelector().text("Pay")', inner=True),
        loc("id", "shop:id/pay"),
        loc("accessibility id", "Pay now"),
    )
    assert [item.using for item in ranked(target)] == ["accessibility id", "id", "-android uiautomator", "xpath"]
    assert [item.using for item in ranked(target, inner=False)] == ["accessibility id", "id", "xpath"]


def test_a_row_is_tapped_by_the_text_inside_it() -> None:
    code = code_of(golden_spec())
    assert "tap(driver, (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text(\"Network & internet\")'), (AppiumBy.XPATH, \"//*[@text='Network & internet']\"))" in code
    assert "is found by the text drawn inside it" in code


def test_a_locator_that_matched_several_elements_picks_the_one_the_run_used() -> None:
    code = code_of(golden_spec())
    assert "tap(driver, (AppiumBy.XPATH, \"(//*[@resource-id='com.example.shop:id/add'])[2]\"), (AppiumBy.XPATH, \"(//*[@text='Add to cart'])[2]\"))" in code
    assert "found by its position among the matches" in code
    selector = element("Pay", loc("-android uiautomator", 'new UiSelector().text("Pay")', count=4, position=3), loc("accessibility id", "Pay", count=2, position=2))
    code = code_of(app_spec([mobile(1, "Pay", DeviceAction(kind="tap", target=selector))]))
    assert "(AppiumBy.XPATH, \"(//*[@content-desc='Pay'])[2]\")" in code
    assert "'new UiSelector().text(\"Pay\").instance(2)'" in code


def test_an_element_without_a_locator_is_used_where_it_was_drawn_with_a_todo() -> None:
    exported = export_run(result(automation=golden_spec()), "appium-py")
    assert "gesture(driver, (960, 1240), hold_ms=1000)" in exported.code
    assert any('no locator found switch "Gift wrap" again; it is pressed where it was drawn on a 1080x2400 screen' in note for note in exported.notes)
    blind = DeviceElement(role="textbox", name="Code", bounds=[0, 0, 100, 50])
    code = code_of(app_spec([mobile(1, "Enter the code", DeviceAction(kind="type", target=blind, value="1234"))]))
    assert 'tap_at(driver, 50, 25)\n    type_focused(driver, "1234")' in code
    nowhere = code_of(app_spec([mobile(1, "Tap", DeviceAction(kind="tap", target=DeviceElement(role="button", name="Ghost")))]))
    assert "TODO: no locator and no position were recorded for the element to tap" in nowhere


def test_a_check_whose_text_held_a_secret_is_a_todo_not_a_wait() -> None:
    masked = AutomationCheck(kind="text", expected="Welcome ******", faithful=False, note="the value holds a secret within other text; set it by hand")
    step = AutomationStep(index=1, action="Sign in", status="passed", kind="mobile", device=[], checks=[masked])
    code = code_of(app_spec([step]))
    assert "Welcome ******" not in code and "shows(driver" not in code
    assert "TODO: the value holds a secret within other text; set it by hand" in code


def test_a_server_url_with_credentials_is_read_from_the_environment_only() -> None:
    steps = [mobile(1, "Pay", DeviceAction(kind="tap", target=element("Pay", loc("accessibility id", "Pay"))))]
    spec = app_spec(steps, appium={"url": "https://alice:s3cret@grid.example.com:4444/wd/hub", "provider": "local"})
    for fmt in ("appium-py", "spec"):
        exported = export_run(result(automation=spec), fmt)
        assert "s3cret" not in exported.code and "alice" not in exported.code and "s3cret" not in str(exported.spec)
    code = export_run(result(automation=spec), "appium-py").code
    assert 'APPIUM_URL = os.environ["APPIUM_URL"]  # the server\'s URL with its credentials, e.g. https://grid.example.com:4444/wd/hub' in code
    assert "APPIUM_URL (required)" in code
    assert appium_server(export_run(result(automation=spec), "spec").spec) == ("https://grid.example.com:4444/wd/hub", True)
    recorded = app_spec(steps, appium={"url": "https://grid.example.com:4444/wd/hub", "provider": "local", "url_credentials": "true"})
    assert 'APPIUM_URL = os.environ["APPIUM_URL"]' in export_run(result(automation=recorded), "appium-py").code


def test_ios_finds_by_name_and_has_no_back_action() -> None:
    ios = {"platform": "ios", "bundle_id": "com.example.Shop", "device": "iPhone 15", "platform_version": "17.5"}
    shared = element("Buy", loc("accessibility id", "buy", count=2, position=2), loc("xpath", "//XCUIElementTypeButton[@label='Buy']", count=2, position=2))
    exported = export_run(
        result(automation=app_spec([mobile(1, "Buy and leave", DeviceAction(kind="tap", target=shared), DeviceAction(kind="back"), DeviceAction(kind="home"))], app=ios)), "appium-py"
    )
    code = exported.code
    assert '"platformName": "iOS"' in code and '"appium:automationName": "XCUITest"' in code and '"appium:bundleId": "com.example.Shop"' in code
    assert '"appium:isHeadless": True' in code and '"appium:wdaLaunchTimeout": 120_000' in code  # a local simulator: no window, time to start
    assert "uiautomator2ServerLaunchTimeout" not in code and "appPackage" not in code
    assert "(//*[@name='buy'])[2]" in code and "(//XCUIElementTypeButton[@label='Buy'])[2]" in code
    assert 'driver.execute_script("mobile: pressButton", {"name": "home"})' in code
    assert "back(driver)" not in code and any("iOS has no system back action" in note for note in exported.notes)
    assert '"appium:platformVersion": os.environ.get("APPIUM_PLATFORM_VERSION", "17.5")' in code


def test_screen_locators_carry_counts_positions_and_the_texts_inside_rows() -> None:
    source = (
        '<hierarchy rotation="0" width="1080" height="2400">'
        '<node class="android.widget.LinearLayout" clickable="true" bounds="[0,100][1080,300]">'
        '<node class="android.widget.TextView" text="Wi-Fi" resource-id="android:id/title" bounds="[40,120][600,180]"/></node>'
        '<node class="android.widget.TextView" text="Wi-Fi" bounds="[0,400][300,450]"/>'
        '<node class="android.widget.EditText" text="" hint="Say &quot;hi&quot;" resource-id="app:id/q" clickable="true" bounds="[0,500][1080,600]"/>'
        '<node class="android.widget.ImageButton" content-desc="Search" clickable="true" bounds="[0,0][100,100]"/>'
        "</hierarchy>"
    )
    screen = parse(source, "android")
    assert screen.size == (1080, 2400)
    row, title, other, field, search = screen.elements
    assert [(item.using, item.matched, item.position, item.inner) for item in row.candidates] == [("-android uiautomator", 2, 1, True), ("xpath", 2, 1, True)]
    assert (other.candidates[1].value, other.candidates[1].position) == ("//*[@text='Wi-Fi']", 2)
    assert [item.using for item in field.candidates] == ["id", "xpath"] and field.candidates[1].value == "//*[@hint='Say \"hi\"']"
    assert search.candidates[0] == ("accessibility id", "Search", 1, 1, False)
    ios = parse(
        '<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Shop" width="390" height="844"><XCUIElementTypeButton type="XCUIElementTypeButton" name="buy" label="Buy" visible="true" x="1" y="2" width="3" height="4"/></XCUIElementTypeApplication>',
        "ios",
    )
    assert ios.size == (390, 844) and [item.using for item in ios.elements[0].candidates] == ["accessibility id", "xpath"]


# ---------------------------------------------------------------------------- actions, data and checks


def test_every_device_action_is_written() -> None:
    code = code_of(golden_spec())
    for line in (
        'tap(driver, (AppiumBy.ACCESSIBILITY_ID, "Search"), (AppiumBy.ID, "com.example.shop:id/search"), at=(540, 200))',
        "    if not settle(driver, before) and at is not None:\n        tap_at(driver, *at)",
        'type_text(driver, DATA["product"], (AppiumBy.ID, "com.example.shop:id/query"), (AppiumBy.XPATH, "//*[@hint=\'Search products\']"))',
        'clear(driver, (AppiumBy.ID, "com.example.shop:id/query"), (AppiumBy.XPATH, "//*[@hint=\'Search products\']"))',
        'scroll(driver, "down")',
        'swipe(driver, "left")',
        "time.sleep(2)",
        "back(driver)",
        "home(driver)",
        "driver.press_keycode(3)",
    ):
        assert line in code, line
    assert "# not repeated: tap [9] button" in code


def test_test_data_and_secrets_come_from_the_code_and_the_environment() -> None:
    code = code_of(golden_spec())
    assert 'DATA = {\n    "product": "Blue mug",' in code
    assert 'type_text(driver, os.environ["SHOP_PIN"], (AppiumBy.ID, "com.example.shop:id/pin"))' in code
    assert 'DATA["product"] + " " + RUN_ID' in code
    # A check on a secret names it, never its value, in the failure message.
    assert 'shows(driver, "Thanks, " + os.environ["SHOP_PIN"]), "expected on the screen: Thanks, {{pin}}"' in code


def test_quoted_texts_are_waited_for_and_judged_checks_are_todos() -> None:
    exported = export_run(result(automation=golden_spec()), "appium-py")
    assert 'WebDriverWait(driver, WAIT_S).until(lambda driver: shows(driver, "Internet"), "expected on the screen: Internet")' in exported.code
    assert 'WebDriverWait(driver, WAIT_S).until(lambda driver: not shows(driver, "No results"), "expected not on the screen: No results")' in exported.code
    assert "# TODO: assert in code: the cart shows one gift-wrapped item" in exported.code
    assert "step 4: assert in code: the cart shows one gift-wrapped item" in exported.notes


def test_api_sql_and_security_steps_in_a_native_app_case() -> None:
    code = code_of(golden_spec())
    assert "def test_shop_checkout(driver: WebDriver, api: APIRequestContext) -> None:" in code
    assert "from playwright.sync_api import APIRequestContext, sync_playwright" in code
    assert 'response = api.fetch(api_url("/api/orders/latest"), method="GET", max_redirects=0)' in code
    assert 'rows = sqlite_rows(DATA["orders_db"], "SELECT id FROM orders", {})' in code
    assert "# TODO: a security scan is not test code" in code


def test_a_native_app_only_case_needs_no_playwright() -> None:
    code = code_of(app_spec([mobile(1, "Go back", DeviceAction(kind="back"))]))
    assert "playwright" not in code and "def test_shop_checkout(driver: WebDriver) -> None:" in code
    assert "AppiumBy" not in code and "WebElement" not in code, "unused imports are left out"


def test_a_step_recorded_before_device_actions_were_kept_is_a_todo() -> None:
    old = mobile(1, "Tap Pay", notes=["a native app step: its device actions are listed here, not exported as Playwright code"])
    exported = export_run(result(automation=app_spec([old])), "appium-py")
    assert any("run the case again with this version of Hercules" in note for note in exported.notes)


def test_hostile_texts_stay_inside_strings_and_comments() -> None:
    evil = 'x")\nimport os; os.system("boom")  #'
    target = element(evil, loc("xpath", f"//*[@text={evil!r}]"))
    exported = export_run(result(automation=app_spec([mobile(1, evil, DeviceAction(kind="tap", target=target), checks=[AutomationCheck(kind="text", expected=evil)])])), "appium-py")
    tree = ast.parse(exported.code)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "system"]
    assert calls == []


# ---------------------------------------------------------------------------- sessions: local and clouds


def test_a_local_session_uses_the_run_s_server_and_device_overridable_by_environment() -> None:
    exported = export_run(result(automation=app_spec([mobile(1, "Back", DeviceAction(kind="back"))], appium={"url": "http://10.0.0.5:4723", "provider": "local"})), "appium-py")
    code = exported.code
    assert 'APPIUM_URL = os.environ.get("APPIUM_URL", "http://10.0.0.5:4723")' in code
    assert '"appium:appPackage": "com.example.shop",\n        "appium:appActivity": ".Main",' in code
    assert '"appium:deviceName": os.environ.get("APPIUM_DEVICE_NAME", "emulator-5554"),' in code
    assert "session = webdriver.Remote(APPIUM_URL, options=options)" in code and "session.quit()" in code
    assert appium_server(exported.spec) == ("http://10.0.0.5:4723", True)


def test_an_app_file_is_read_from_the_environment_with_the_case_s_path_as_default() -> None:
    app = {"platform": "android", "app": "apps/shop.apk", "capabilities": {"appium:language": "fr", "appium:locale": "FR"}}
    exported = export_run(result(automation=app_spec([mobile(1, "Back", DeviceAction(kind="back"))], app=app)), "appium-py")
    assert 'caps["appium:app"] = os.environ.get("APPIUM_APP", "apps/shop.apk")' in exported.code
    assert 'caps.update({"appium:language": "fr", "appium:locale": "FR"})  # the case\'s own capabilities' in exported.code
    assert next(item for item in exported.env if item.name == "APPIUM_APP").required is False


@pytest.mark.parametrize(
    ("provider", "app_ref", "hub", "options"),
    [
        ("browserstack", "apps/shop.apk", "https://hub-cloud.browserstack.com/wd/hub", '"userName": os.environ["BS_USER"],\n        "accessKey": os.environ["BS_KEY"],'),
        ("saucelabs", "storage:abc-123", "https://ondemand.eu-central-1.saucelabs.com:443/wd/hub", '"sauce:options"] = {"username": os.environ["BS_USER"], "accessKey": os.environ["BS_KEY"]'),
    ],
)
def test_a_device_cloud_session_reads_its_credentials_from_the_variables_the_run_used(provider: str, app_ref: str, hub: str, options: str) -> None:
    app = {"platform": "android", "app": app_ref, "cloud": provider, "device": "Pixel 7", "platform_version": "14"}
    server = {"url": hub, "provider": provider, "username_env": "BS_USER", "access_key_env": "BS_KEY", "app_ref": "bs://uploaded-123"}
    exported = export_run(result(automation=app_spec([mobile(1, "Back", DeviceAction(kind="back"))], app=app, appium=server)), "appium-py")
    code = exported.code
    assert f'APPIUM_URL = os.environ.get("APPIUM_URL", "{hub}")' in code
    assert options in code
    assert "uploaded-123" not in code, "an uploaded app's id is the run's, not the test's"
    required = {item.name for item in exported.env if item.required}
    assert {"BS_USER", "BS_KEY"} <= required
    assert "APPIUM_UDID" not in {item.name for item in exported.env}, "a cloud picks its own device"
    if provider == "browserstack":
        assert 'caps["appium:app"] = os.environ["APPIUM_APP"]' in code and "APPIUM_APP" in required
        assert 'options["deviceName"] = os.environ.get("APPIUM_DEVICE_NAME", "Pixel 7")' in code and 'caps["bstack:options"]' in code
    else:
        assert 'caps["appium:app"] = os.environ.get("APPIUM_APP", "storage:abc-123")' in code
        assert '"appium:deviceName": os.environ.get("APPIUM_DEVICE_NAME", "Pixel 7"),' in code
    assert appium_server(exported.spec) == (hub, False)


def test_a_cloud_run_without_a_server_record_falls_back_to_the_default_names() -> None:
    app = {"platform": "android", "app": "bs://abc", "cloud": "browserstack"}
    exported = export_run(result(automation=app_spec([mobile(1, "Back", DeviceAction(kind="back"))], app=app, appium={})), "appium-py")
    assert 'os.environ["BROWSERSTACK_USERNAME"]' in exported.code and "https://hub-cloud.browserstack.com/wd/hub" in exported.code


# ---------------------------------------------------------------------------- formats


def test_formats_follow_where_the_run_ran() -> None:
    native = result(automation=golden_spec())
    with pytest.raises(ExportError, match="appium-py"):
        export_run(native, "playwright-py")
    web = result()
    with pytest.raises(ExportError, match="native app runs"):
        export_run(web, "appium-py")
    assert default_format(native) == "appium-py" and default_format(web) == "playwright-ts"
    assert '"device": [' in export_run(native, "spec").code


def test_a_partial_export_says_so() -> None:
    spec = golden_spec()
    failed = spec.model_copy(update={"steps": [*spec.steps[:2], spec.steps[2].model_copy(update={"status": "failed"})]})
    exported = export_run(result("failed", automation=failed, step_statuses=["passed", "passed", "failed"]), "appium-py", allow_failed=True)
    assert exported.partial and "PARTIAL: the run failed at step 3" in exported.code and "scroll(driver" not in exported.code


# ---------------------------------------------------------------------------- recorded by a run


def search_case() -> TestCase:
    return TestCase(
        id="android-search",
        title="Search the settings",
        environment={"mobile": {"platform": "android", "package": "com.example.settings", "activity": ".Settings", "device": "emulator-5554"}},  # type: ignore[arg-type]
        test_data={"query": "wifi", "pin": {"secret": "SETTINGS_PIN"}},  # type: ignore[dict-item]
        steps=[
            Step(action="Tap 'Network & internet'", expected="'Internet' is visible"),
            Step(action="Go back"),
            Step(action="Type {{query}} into the search field", expected="'{{query}}' is shown"),
            Step(action="Type {{pin}} into the search field"),
        ],
    )


def test_a_run_records_what_the_export_needs_and_its_export_repeats_it(tmp_path: Path) -> None:
    device = FakeDevice()
    with serve_fake_appium(device) as (url, _):
        path = project(tmp_path, f"telemetry:\n  enabled: false\nmobile:\n  appium_url: {url}\n")
        model = ScriptedModel(
            tap("Network & internet"),
            {"action": "done"},
            tap("Navigate up"),
            {"action": "done"},
            type_into("textbox", "{{query}}"),
            {"action": "done"},
            type_into("textbox", "{{pin}}"),
            {"action": "done"},
        )
        outcome, _ = run(search_case(), path, model=model, env={"SETTINGS_PIN": SECRET})
    assert outcome.status == "passed", outcome.hints
    spec = outcome.automation
    assert spec is not None and spec.appium == {"url": url, "provider": "local"}
    first = spec.steps[0].device[0]  # type: ignore[index]
    assert first.kind == "tap" and first.target is not None and first.target.screen is None
    assert [(item.using, item.inner) for item in first.target.locators] == [("-android uiautomator", True), ("xpath", True)]
    assert spec.steps[0].checks[0].kind == "text" and spec.steps[0].checks[0].expected == "Internet"
    assert spec.steps[2].device[0].value == "{{query}}" and spec.steps[2].checks[0].expected == "{{query}}"  # type: ignore[index]
    assert spec.steps[3].device[0].value == "{{pin}}"  # type: ignore[index]
    assert set(spec.data) == {"query", "pin"} and spec.data["pin"].env == "SETTINGS_PIN"
    exported = export_run(outcome, "appium-py")
    assert SECRET not in outcome.model_dump_json() and SECRET not in exported.code
    assert 'os.environ["SETTINGS_PIN"],' in exported.code and 'DATA["query"],' in exported.code and exported.code.count("type_text(") == 3
    assert 'os.environ["SETTINGS_PIN"]' in exported.code and 'DATA["query"]' in exported.code
    assert f'APPIUM_URL = os.environ.get("APPIUM_URL", "{url}")' in exported.code
    compile(exported.code, "test_android_search.py", "exec")


def test_a_field_showing_a_secret_is_never_found_by_its_text(tmp_path: Path) -> None:
    device = FakeDevice(typed=SECRET)
    with serve_fake_appium(device) as (url, _):
        path = project(tmp_path, f"telemetry:\n  enabled: false\nmobile:\n  appium_url: {url}\n")
        case = search_case().model_copy(update={"steps": [Step(action="Type {{pin}} into the search field")]})
        outcome, _ = run(case, path, model=ScriptedModel(type_into("textbox", "{{pin}}"), {"action": "done"}), env={"SETTINGS_PIN": SECRET})
    assert outcome.status == "passed", outcome.hints
    assert SECRET not in outcome.model_dump_json()
    assert SECRET not in export_run(outcome, "appium-py").code


# ---------------------------------------------------------------------------- --check


FAKE_KEYS = ("FAKE_APPIUM_CLIENT", "FAKE_NO_DEVICE", "FAKE_EXIT")
FAKE_PYTHON = r"""#!{python}
import os, sys
if sys.argv[1:2] == ["-c"]:
    sys.exit(0 if os.environ.get("FAKE_APPIUM_CLIENT", "1") == "1" else 1)
print("args", " ".join(sys.argv[1:]))
print("server", os.environ.get("APPIUM_URL", "-"), "pin", os.environ.get("SHOP_PIN", "-"), "other", os.environ.get("OTHER_TOKEN", "-"))
if os.environ.get("FAKE_NO_DEVICE"):
    print("selenium.common.exceptions.SessionNotCreatedException: Could not find a connected Android device")
code = int(os.environ.get("FAKE_EXIT", "0"))
print("1 passed" if code == 0 else "1 failed")
sys.exit(code)
"""


@pytest.fixture
def fake_python(tmp_path: Path) -> str:
    script = tmp_path / "bin" / "python"
    script.parent.mkdir()
    script.write_text(FAKE_PYTHON.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def appium_export() -> ExportResult:
    env = (EnvVar("APPIUM_URL", False, "server"), EnvVar("APPIUM_UDID", False, "device"), EnvVar("SHOP_PIN", True, "secret {{pin}}"))
    return ExportResult(format="appium-py", run_id="r_1", case_id="shop", run_status="passed", filename="tests/test_shop.py", code="def test_x():\n    pass\n", spec={}, env=env)


def test_the_check_runs_the_export_on_the_device_with_only_what_it_needs(tmp_path: Path, fake_python: str) -> None:
    with serve_fake_appium() as (url, _):
        env = {"PATH": "/usr/bin:/bin", "SHOP_PIN": SECRET, "OTHER_TOKEN": "not-for-the-test"}
        checked = run_appium_check(appium_export(), tmp_path / "check", env=env, appium_url=url, local=True, python=fake_python, keep=FAKE_KEYS)
    assert checked.passed and checked.exit_code == 0 and checked.hint is None
    assert checked.test_file == tmp_path / "check" / "test_shop.py" and checked.test_file.read_text() == "def test_x():\n    pass\n"
    assert "args -m pytest -c pytest.ini --rootdir . -q -p no:cacheprovider test_shop.py" in checked.output
    assert "other -" in checked.output, "the caller's other variables are not passed on"
    assert SECRET not in checked.output and "pin ******" in checked.output


def test_a_failed_check_says_when_the_device_was_the_problem(tmp_path: Path, fake_python: str) -> None:
    with serve_fake_appium() as (url, _):
        env = {"PATH": "/usr/bin:/bin", "SHOP_PIN": SECRET, "FAKE_EXIT": "1", "FAKE_NO_DEVICE": "1"}
        checked = run_appium_check(appium_export(), tmp_path / "check", env=env, appium_url=url, local=True, python=fake_python, keep=FAKE_KEYS)
    assert not checked.passed and checked.exit_code == 1
    assert checked.hint is not None and "device (or emulator) is running" in checked.hint


def test_no_appium_server_no_client_or_a_missing_secret_stops_the_check_before_it_starts(tmp_path: Path, fake_python: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "SHOP_PIN": SECRET}
    with pytest.raises(CheckError, match=r"no Appium server answers at http://127\.0\.0\.1:9 .*appium --address 127\.0\.0\.1 --port 4723"):
        run_appium_check(appium_export(), tmp_path / "a", env=env, appium_url="http://127.0.0.1:9", local=True, python=fake_python, keep=FAKE_KEYS)
    with pytest.raises(CheckError, match="pip install pytest Appium-Python-Client"):
        run_appium_check(appium_export(), tmp_path / "b", env=env | {"FAKE_APPIUM_CLIENT": "0"}, appium_url="http://127.0.0.1:9", local=True, python=fake_python, keep=FAKE_KEYS)
    with pytest.raises(CheckError, match="needs SHOP_PIN"):
        run_appium_check(appium_export(), tmp_path / "c", env={"PATH": "/usr/bin:/bin"}, appium_url="http://127.0.0.1:9", local=True, python=fake_python, keep=FAKE_KEYS)
    with pytest.raises(CheckError, match="appium-py"):
        run_appium_check(result_of_web(), tmp_path / "d", env=env, appium_url="http://127.0.0.1:9", local=True, python=fake_python, keep=FAKE_KEYS)
    assert not (tmp_path / "a").exists()


def result_of_web() -> ExportResult:
    return ExportResult(format="playwright-py", run_id="r_1", case_id="shop", run_status="passed", filename="tests/test_shop.py", code="", spec={})


def test_a_device_cloud_hub_is_not_probed(tmp_path: Path, fake_python: str) -> None:
    checked = run_appium_check(appium_export(), tmp_path / "check", env={"PATH": "/usr/bin:/bin", "SHOP_PIN": SECRET}, appium_url="http://127.0.0.1:9", local=False, python=fake_python, keep=FAKE_KEYS)
    assert checked.passed


def test_appium_status_reads_the_ready_flag() -> None:
    with serve_fake_appium() as (url, _):
        assert appium_status(url) is None
    assert appium_status("http://127.0.0.1:9") is not None


# ---------------------------------------------------------------------------- the CLI


def cli(*args: str, env: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["export", *args], env=env, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def native_project(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    native = result(automation=golden_spec())
    run_dir = tmp_path / "project" / ".hercules" / "runs" / native.run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(native.model_dump_json(), encoding="utf-8")
    return tmp_path / "project", {"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_TELEMETRY": "0", "PATH": "/usr/bin:/bin"}, native.run_id


def test_the_cli_exports_a_native_app_run_as_appium_code_by_default(tmp_path: Path) -> None:
    project_dir, env, run_id = native_project(tmp_path)
    code, out, err = cli(run_id, "--project", str(project_dir), env=env)
    assert code == 0 and "def test_shop_checkout(driver: WebDriver, api: APIRequestContext) -> None:" in out
    assert "Suggested file: tests/test_shop_checkout.py" in err and "Environment: SHOP_PIN (required)" in err
    code, _, err = cli(run_id, "--project", str(project_dir), "--format", "playwright-ts", env=env)
    assert code == 2 and "format appium-py" in err
    code, _, err = cli(run_id, "--project", str(project_dir), "--check", "--format", "spec", env=env)
    assert code == 2 and "appium-py for a native app run" in err


def test_the_cli_check_needs_the_appium_server(tmp_path: Path) -> None:
    project_dir, env, run_id = native_project(tmp_path)
    code, _, err = cli(run_id, "--project", str(project_dir), "--check", env=env | {"SHOP_PIN": SECRET, "APPIUM_URL": "http://127.0.0.1:9"})
    assert code == 2
    assert "Checking the export: pytest with the Appium Python client" in err
    assert "no Appium server answers at http://127.0.0.1:9" in err or "the Appium Python client or pytest is not installed" in err
    assert SECRET not in err
