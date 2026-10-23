"""Form control state (checked / selected) and label-derived names in the DOM tools (#135).

The pure tests exercise the flattening used by ``get_interactive_elements``. The browser
test builds the real accessibility tree on a small form; it needs a Chromium that
Playwright can launch (the default install, or the binary named by HERCULES_TEST_CHROMIUM)
and is skipped otherwise.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from testzeus_hercules.core.tools.get_interactive_elements import compact_interactive_node, flatten_elements, is_form_field_node

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


def test_input_fields_lists_aria_form_widgets_that_are_not_form_tags() -> None:
    # get_input_fields shares the flattener: a <div role=switch> is a form control even though 'div' is not a form tag
    tree = _tree({"md": "9", "tag": "div", "role": "switch", "name": "Dark mode", "checked": True}, {"md": "10", "tag": "div", "name": "Just text"})

    assert [e["md"] for e in flatten_elements(tree, include=is_form_field_node, compact=False)] == ["9"]


def test_input_fields_keep_the_full_node_while_interactive_elements_are_compacted() -> None:
    node = {"md": "2", "tag": "input", "role": "textbox", "name": "Email", "data-testid": "email-input"}

    assert flatten_elements(_tree(node), include=is_form_field_node, compact=False)[0]["data-testid"] == "email-input"
    assert "data-testid" not in flatten_elements(_tree(node))[0]


def test_input_fields_tool_uses_the_shared_flattener() -> None:
    import importlib

    module = importlib.import_module("testzeus_hercules.core.tools.get_input_fields")  # the package re-exports the tool function under this name

    assert module.flatten_elements is flatten_elements  # no second copy to keep in sync


# ── real browser ─────────────────────────────────────────────────────────────

FORM = """<form>
<label><input type=checkbox id=c1 checked> Newsletter</label>
<label><input type=checkbox id=c2> Terms</label>
<input type=checkbox id=c3 name=remember>
<label><input type=checkbox id=c4 disabled> Locked</label>
<label><input type=radio name=colour id=r1 checked> Red</label>
<label><input type=radio name=colour id=r2> Blue</label>
<div role=switch aria-checked=true tabindex=0>Dark mode</div>
<div role=checkbox aria-checked=mixed tabindex=0>Select all</div>
<ul role=listbox><li role=option aria-selected=true tabindex=0>Small</li><li role=option aria-selected=false tabindex=0>Large</li></ul>
<label>Email <input id=email type=email name=user_email></label>
<label>Country <select id=country name=country><option>US</option><option>Canada</option><option>Mexico</option></select></label>
<label><select id=lang name=lang><option>EN</option><option>FR</option></select> Language</label>
<label><input type=checkbox role=switch id=dm name=dm> Night mode</label>
<fieldset disabled><label><input type=checkbox id=fs> Opt in</label></fieldset>
<button id=go type=button>Go</button><label for=go>Button label</label>
<span id=ql>Quantity of items</span><input id=qty type=number aria-labelledby=ql name=qty_field>
<input id=srch type=text placeholder="Search products" name=q>
<button id=sub type=submit name=submit_action value=1>Place order</button>
<label><input type=checkbox role=menuitem aria-checked=false name=menu_name> Menu entry</label>
<iframe srcdoc="<label>Framed field <input name=framed_name></label>"></iframe>
</form>"""


async def _launch(playwright):
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception as error:
        if "Executable doesn't exist" not in str(error):
            raise
        executable = os.environ.get("HERCULES_TEST_CHROMIUM")
        if executable and Path(executable).exists():
            return await playwright.chromium.launch(headless=True, executable_path=executable)
        if os.environ.get("CI"):
            raise  # CI installs the browsers; a silent skip here would hide a broken install
        pytest.skip("no launchable Chromium (install Playwright browsers or set HERCULES_TEST_CHROMIUM)")


def _controls(tree: dict) -> list[dict]:
    """The controls an agent receives from get_interactive_elements (same flattening)."""
    return flatten_elements(tree)


def test_real_tree_reports_live_state_labels_and_updates_after_clicks(tmp_path) -> None:
    asyncio.run(_check_real_tree(tmp_path))


async def _check_real_tree(tmp_path: Path) -> None:
    from playwright.async_api import async_playwright
    from testzeus_hercules.config import get_global_conf, set_global_conf_keys
    from testzeus_hercules.utils.get_detailed_accessibility_tree import do_get_accessibility_info

    previous_root = get_global_conf().get_config().get("PROJECT_SOURCE_ROOT")
    set_global_conf_keys({"PROJECT_SOURCE_ROOT": str(tmp_path)})
    try:
        async with async_playwright() as playwright:
            browser = await _launch(playwright)
            try:
                page = await browser.new_page()
                await page.set_content(FORM)

                full_tree = await do_get_accessibility_info(page, only_input_fields=False)
                before = _controls(full_tree)
                # nodes that skip the md handling (role=menuitem, content of a frame the injector missed) carry no transient keys either
                assert "label_text" not in json.dumps(full_tree)
                assert "Menu entry" in json.dumps(full_tree) and "Framed field" in json.dumps(full_tree)
                by_name = {c.get("name"): c for c in before if c.get("role") in {"checkbox", "radio", "switch", "option"}}
                assert by_name["Newsletter"]["checked"] is True
                assert by_name["Terms"]["checked"] is False
                assert by_name["Red"]["checked"] is True and by_name["Blue"]["checked"] is False
                assert by_name["Red"].get("field_name") == "colour"  # the label, not the form-group name, is the name
                assert by_name["Dark mode"]["checked"] is True
                assert by_name["Select all"]["checked"] == "mixed"
                assert by_name["Locked"]["disabled"] is True
                assert by_name["Small"]["selected"] is True and by_name["Large"]["selected"] is False
                # the label is the name for every control type; the HTML name attribute moves to field_name
                email = next(c for c in before if c.get("tag_type") == "email")
                assert email["name"] == "Email" and email["field_name"] == "user_email"
                assert by_name["Night mode"]["role"] == "switch" and by_name["Night mode"]["field_name"] == "dm"
                # a label wrapping a <select> names the control by its label text, not by the options
                selects = {c.get("name") for c in before if c.get("tag") == "select"}
                assert selects == {"Country", "Language"}
                # a control disabled through <fieldset disabled> is reported as disabled
                assert by_name["Opt in"]["disabled"] is True
                # an unlabelled checkbox is named after its form field, never after its value ("on")
                assert "on" not in {c.get("name") for c in before if c.get("role") == "checkbox"}
                assert any(c.get("name") == "remember" and c.get("role") == "checkbox" for c in before)
                # a <label for=...> pointing at a button must not replace the button's own text
                assert any(c.get("name") == "Go" and c.get("tag") == "button" for c in before)

                # the HTML name attribute never overwrites the accessible name, whatever names the control
                by_field = {c.get("field_name"): c for c in before if c.get("field_name")}
                assert by_field["qty_field"]["name"] == "Quantity of items"  # aria-labelledby
                assert by_field["q"]["name"] == "Search products"  # placeholder
                assert by_field["submit_action"]["name"] == "Place order"  # button text
                assert not any(c.get("name") == "qty_field" or c.get("name") == "q" for c in before)

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
            set_global_conf_keys({"PROJECT_SOURCE_ROOT": previous_root})
