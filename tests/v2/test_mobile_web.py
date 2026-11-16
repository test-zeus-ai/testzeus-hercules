"""Mobile web emulation: the browser opens as the case's device, and exported tests open the same device."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from testzeus_hercules.cases.automation import AutomationAction, AutomationSpec, AutomationStep
from testzeus_hercules.cases.models import RunResult, StepResult
from testzeus_hercules.engine.run_graph import ChromiumLauncher, parse_viewport
from testzeus_hercules.export import export_run

from tests.v2.engine_fixtures.browser import require_chromium
from tests.v2.export_harness import UI_TYPESCRIPT, node, typescript_syntax_errors


def result(**spec: object) -> RunResult:
    automation = AutomationSpec(
        case_id="shop",
        title="Shop on a phone",
        base_url="http://127.0.0.1:8000",
        steps=[AutomationStep(index=1, action="Open the home page", status="passed", actions=[AutomationAction(kind="navigate", url="/")])],
        **spec,  # type: ignore[arg-type]
    )
    return RunResult(run_id="r_1", status="passed", case_id="shop", steps=[StepResult(index=1, action="Open the home page", status="passed")], automation=automation)


def test_the_viewport_comes_from_the_device_unless_the_case_sets_one() -> None:
    assert parse_viewport(None, "iPhone 15") == (393, 659)
    assert parse_viewport("800x600", "iPhone 15") == (800, 600)
    assert parse_viewport(None) == (1280, 800)


def test_playwright_test_export_uses_the_device(tmp_path: Path) -> None:
    code = export_run(result(device="iPhone 15"), "playwright-ts").code
    assert 'import { test, expect, devices } from "@playwright/test";' in code
    assert 'test.use({ ...devices["iPhone 15"], defaultBrowserType: "chromium" });' in code
    if node() is not None and UI_TYPESCRIPT.is_dir():
        assert typescript_syntax_errors(code, tmp_path) == []
    plain = export_run(result(), "playwright-ts").code
    assert "devices" not in plain and "viewport" not in plain
    sized = export_run(result(viewport="390x844"), "playwright-ts").code
    assert "test.use({ viewport: { width: 390, height: 844 } });" in sized


def test_pytest_playwright_export_uses_the_device() -> None:
    code = export_run(result(device="Pixel 7", viewport="412x800"), "playwright-py").code
    ast.parse(code)
    assert "def browser_context_args(browser_context_args: dict, playwright: Playwright) -> dict:" in code
    assert 'args.update(playwright.devices["Pixel 7"])' in code and 'args.pop("default_browser_type", None)' in code
    assert 'args["viewport"] = {"width": 412, "height": 800}' in code
    assert "Playwright" in code.split("from playwright.sync_api import ")[1].splitlines()[0]
    assert "browser_context_args" not in export_run(result(), "playwright-py").code


def test_chromium_opens_as_the_device() -> None:
    executable = require_chromium()

    async def probe() -> dict:
        async with ChromiumLauncher(executable).open(viewport=parse_viewport(None, "iPhone 15"), headless=True, device="iPhone 15") as page:
            await page.set_content('<meta name="viewport" content="width=device-width, initial-scale=1"><p>hi</p>')
            return await page.evaluate("({w: innerWidth, h: innerHeight, ua: navigator.userAgent, dpr: devicePixelRatio, touch: navigator.maxTouchPoints > 0})")

    seen = asyncio.run(probe())
    assert (seen["w"], seen["h"]) == (393, 659) and "iPhone" in seen["ua"] and seen["dpr"] == 3 and seen["touch"] is True


@pytest.mark.parametrize("name", ["iPhone 15", "Pixel 7", "iPad Pro 11"])
def test_known_devices_have_a_viewport(name: str) -> None:
    width, height = parse_viewport(None, name)
    assert 200 < width < 1400 and 200 < height < 1400
