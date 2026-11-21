"""Locator candidates proven on the live page (real headless Chromium): test ids, roles, labels, duplicates, frames, shadow DOM.

Every kept candidate matched exactly one element on the page and that element is the one acted on;
a candidate that matches several elements, or another element, is tried and dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any

from testzeus_hercules.cases.automation import LocatorSpec
from testzeus_hercules.engine.ports import DefaultPerceiver
from testzeus_hercules.perceive.locators import build_locator, css_identifier, element_candidates, text_form
from testzeus_hercules.perceive.snapshot import take_snapshot, resolve

from tests.v2.engine_fixtures.browser import open_page

PAGE = """
<main>
  <h1>Account</h1>
  <form>
    <label for="email">Email</label><input id="email" name="email" placeholder="you@example.com">
    <label for="gen">Nickname</label><input id=":r1:" name="nick">
    <button type="button" data-testid="save-profile">Save</button>
    <button type="button">Save</button>
    <a href="/help" data-qa="help-link">Help</a>
  </form>
  <p>Saved items</p><p>Saved items</p>
  <p hidden>Secret banner</p>
  <select id="country"><option>Choose a country</option><option>Canada</option></select>
</main>
"""

FRAMED = """
<h1>Checkout</h1>
<iframe title="Card details" srcdoc='<label for="n">Card number</label><input id="n"><button>Save card</button><p>Card saved</p>'></iframe>
"""

SHADOW = """
<h1>Cart</h1>
<coupon-box></coupon-box>
<script>
  customElements.define("coupon-box", class extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" }).innerHTML = '<label for="code">Coupon code</label><input id="code"><button id="apply">Apply</button>';
    }
  });
</script>
"""


async def _target(page: Any, role: str, name: str, *, nth: int = 0) -> Any:
    """Record the element with this role and name the way the engine does: snapshot, resolve, record."""
    snapshot = await take_snapshot(page, seq=1)
    matches = [element for element in snapshot.elements if element.role == role and element.name == name]
    element = matches[nth]
    locator = await resolve(page, snapshot, element.md)
    return await DefaultPerceiver().locators(page, snapshot, element.md, locator)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_a_test_id_comes_first_and_a_duplicated_name_is_dropped() -> None:
    async def go() -> Any:
        async with open_page(PAGE) as page:
            return await _target(page, "button", "Save")

    spec, tried = run(go())
    assert spec.locator == LocatorSpec(strategy="test_id", attribute="data-testid", value="save-profile")
    role = next(item for item in tried["candidates"] if item["strategy"] == "role")
    assert (role["count"], role["kept"]) == (2, False), "two buttons are named Save"
    assert all(item.strategy != "role" for item in spec.fallbacks)
    assert spec.checked == len(tried["candidates"])


def test_the_second_same_named_element_is_found_by_its_path_only() -> None:
    async def go() -> Any:
        async with open_page(PAGE) as page:
            spec, _ = await _target(page, "button", "Save", nth=1)
            assert spec.locator is not None
            assert await build_locator(page, spec.locator).count() == 1
            return spec

    spec = run(go())
    assert spec.locator is not None and spec.locator.strategy == "css"


def test_a_field_is_found_by_role_label_placeholder_id_and_name() -> None:
    async def go() -> Any:
        async with open_page(PAGE) as page:
            return await _target(page, "textbox", "Email")

    spec, _ = run(go())
    assert spec.locator == LocatorSpec(strategy="role", role="textbox", name="Email")
    strategies = [(item.strategy, item.value or item.selectors) for item in spec.fallbacks]
    assert strategies == [("label", "Email"), ("placeholder", "you@example.com"), ("css", ["#email"])]


def test_a_generated_id_is_not_offered() -> None:
    async def go() -> Any:
        async with open_page(PAGE) as page:
            return await _target(page, "textbox", "Nickname")

    _, tried = run(go())
    assert not any(item.get("selectors") == ["#\\3a r1\\3a "] or ":r1:" in str(item.get("selectors")) for item in tried["candidates"])


def test_other_test_id_attributes_are_css_candidates() -> None:
    async def go() -> Any:
        async with open_page(PAGE) as page:
            return await _target(page, "link", "Help")

    spec, _ = run(go())
    assert spec.locator == LocatorSpec(strategy="test_id", attribute="data-qa", value="help-link")


def test_an_element_in_an_iframe_is_found_through_the_iframe() -> None:
    async def go() -> Any:
        async with open_page(FRAMED) as page:
            await page.wait_for_timeout(200)
            spec, _ = await _target(page, "textbox", "Card number")
            scope = page
            for frame in spec.frames:
                scope = build_locator(scope, frame.locator).content_frame
            assert await build_locator(scope, spec.locator).count() == 1
            return spec

    spec = run(go())
    assert [frame.locator for frame in spec.frames] == [LocatorSpec(strategy="css", selectors=['iframe[title="Card details"]'])]
    assert spec.locator == LocatorSpec(strategy="role", role="textbox", name="Card number")


def test_an_element_in_a_shadow_root_is_found() -> None:
    async def go() -> Any:
        async with open_page(SHADOW) as page:
            spec, _ = await _target(page, "button", "Apply")
            for item in [spec.locator, *spec.fallbacks]:
                assert await build_locator(page, item).count() == 1, item
            return spec

    spec = run(go())
    assert spec.frames == []
    assert spec.locator == LocatorSpec(strategy="role", role="button", name="Apply")
    assert any(item.strategy == "css" and len(item.selectors) == 2 for item in spec.fallbacks), "the path goes through the shadow host"


def test_text_forms_are_the_ones_that_hold_on_the_page() -> None:
    async def go() -> dict[str, Any]:
        async with open_page(PAGE) as page:
            return {
                "unique": await text_form(page, "Account", present=True),
                "twice": await text_form(page, "Saved items", present=True),
                "absent": await text_form(page, "Order placed", present=False),
                "hidden": await text_form(page, "Secret banner", present=False),
                "option": await text_form(page, "Choose a country", present=False),
            }

    forms = run(go())
    assert forms["unique"] == ("text", [])
    assert forms["twice"] == ("text_first", [])
    assert forms["absent"] == ("count_zero", [])
    assert forms["hidden"] == (None, []), "the hidden text is in the page's text: no simple form holds"
    assert forms["option"] == (None, [])


def test_a_text_in_an_iframe_is_checked_through_the_iframe() -> None:
    async def go() -> Any:
        async with open_page(FRAMED) as page:
            await page.wait_for_timeout(200)
            return await text_form(page, "Card saved", present=True)

    form, frames = run(go())
    assert form == "text"
    assert [frame.locator for frame in frames] == [LocatorSpec(strategy="css", selectors=['iframe[title="Card details"]'])]


def test_candidates_are_listed_best_first() -> None:
    facts = {"test_ids": {"data-testid": "x"}, "labels": ["Email"], "aria_label": "Email", "placeholder": "p", "text": "", "alt": None, "title": "t", "id": "email", "name": "email", "tag": "input"}
    order = [item.strategy for item in element_candidates(facts, "textbox", "Email", ["form > input"])]
    assert order == ["test_id", "role", "label", "placeholder", "title", "css", "css", "css"]
    assert css_identifier("a.b") == "a\\2e b"
