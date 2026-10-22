"""Form control state (checked / selected) and label-derived names in the DOM tools (#135).

The pure tests exercise the flattening used by ``get_interactive_elements``. The browser
test builds the real accessibility tree on a small form; it needs a Chromium that
Playwright can launch (the default install, or the binary named by HERCULES_TEST_CHROMIUM)
and is skipped otherwise.
"""

import asyncio
import os
from pathlib import Path

import pytest
from testzeus_hercules.core.tools.get_interactive_elements import compact_interactive_node, flatten_elements

# ── flattening ───────────────────────────────────────────────────────────────


def _tree(*children: dict) -> dict:
    return {"role": "document", "children": list(children)}


def test_checked_false_is_kept_not_dropped_as_empty() -> None:
    elements = flatten_elements(_tree({"md": "4", "tag": "input", "role": "checkbox", "name": "Terms", "checked": False}))

    assert elements == [{"md": "4", "tag": "input", "role": "checkbox", "name": "Terms", "checked": False}]


def test_mixed_and_selected_state_are_reported() -> None:
    elements = flatten_elements(
        _tree(
            {"md": "10", "tag": "div", "role": "checkbox", "name": "Select all", "checked": "mixed"},
            {"md": "11", "tag": "li", "role": "option", "name": "Blue", "selected": True},
        )
    )

    assert [(e["name"], e.get("checked"), e.get("selected")) for e in elements] == [("Select all", "mixed", None), ("Blue", None, True)]


def test_aria_widgets_with_full_role_key_are_listed() -> None:
    # role="switch" divs carry the full "role" key; only pruned nodes use the short "r" key.
    elements = flatten_elements(_tree({"md": "9", "tag": "div", "role": "switch", "name": "Dark mode", "checked": True}))

    assert [e["md"] for e in elements] == ["9"]


def test_short_role_key_is_still_recognised() -> None:
    elements = flatten_elements(_tree({"md": 9, "tag": "div", "r": "switch", "name": "Dark mode"}))

    assert [e["md"] for e in elements] == [9]


def test_form_field_name_is_exposed_separately() -> None:
    node = compact_interactive_node({"md": "6", "tag": "input", "role": "radio", "name": "Red", "field_name": "colour", "checked": True})

    assert node["name"] == "Red" and node["field_name"] == "colour" and node["checked"] is True


# ── real browser ─────────────────────────────────────────────────────────────

FORM = """<form>
<label><input type=checkbox id=c1 checked> Newsletter</label>
<label><input type=checkbox id=c2> Terms</label>
<label><input type=radio name=colour id=r1 checked> Red</label>
<label><input type=radio name=colour id=r2> Blue</label>
<div role=switch aria-checked=true tabindex=0>Dark mode</div>
<div role=checkbox aria-checked=mixed tabindex=0>Select all</div>
<label>Email <input id=email type=email></label>
</form>"""


async def _launch(playwright):
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception:
        executable = os.environ.get("HERCULES_TEST_CHROMIUM")
        if not executable or not Path(executable).exists():
            pytest.skip("no launchable Chromium (install Playwright browsers or set HERCULES_TEST_CHROMIUM)")
        return await playwright.chromium.launch(headless=True, executable_path=executable)


def _controls(tree: dict) -> list[dict]:
    """The controls an agent receives from get_interactive_elements (same flattening)."""
    return flatten_elements(tree)


def test_real_tree_reports_live_state_labels_and_updates_after_clicks(tmp_path) -> None:
    asyncio.run(_check_real_tree(tmp_path))


async def _check_real_tree(tmp_path: Path) -> None:
    from playwright.async_api import async_playwright
    from testzeus_hercules.config import get_global_conf, set_global_conf
    from testzeus_hercules.utils.get_detailed_accessibility_tree import do_get_accessibility_info

    previous_root = get_global_conf().get_config().get("PROJECT_SOURCE_ROOT")
    set_global_conf({"PROJECT_SOURCE_ROOT": str(tmp_path)}, override=True)
    try:
        async with async_playwright() as playwright:
            browser = await _launch(playwright)
            try:
                page = await browser.new_page()
                await page.set_content(FORM)

                before = _controls(await do_get_accessibility_info(page, only_input_fields=False))
                by_name = {c.get("name"): c for c in before if c.get("role") in {"checkbox", "radio", "switch"}}
                assert by_name["Newsletter"]["checked"] is True
                assert by_name["Terms"]["checked"] is False
                assert by_name["Red"]["checked"] is True and by_name["Blue"]["checked"] is False
                assert by_name["Red"].get("field_name") == "colour"  # the label, not the form-group name, is the name
                assert by_name["Dark mode"]["checked"] is True
                assert by_name["Select all"]["checked"] == "mixed"
                assert any(c.get("name") == "Email" and c.get("role") == "textbox" for c in before)

                await page.click("#c2")
                await page.click("#r2")
                after = _controls(await do_get_accessibility_info(page, only_input_fields=False))
                by_name = {c.get("name"): c for c in after if c.get("role") in {"checkbox", "radio"}}
                assert by_name["Terms"]["checked"] is True  # state change is observable
                assert by_name["Blue"]["checked"] is True and by_name["Red"]["checked"] is False  # both radios update
            finally:
                await browser.close()
    finally:
        if previous_root is not None:
            set_global_conf({"PROJECT_SOURCE_ROOT": previous_root}, override=True)
