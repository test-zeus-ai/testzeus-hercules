"""md resolution back to Playwright locators, plus the walker's no-side-effect guarantees."""

from __future__ import annotations

import pytest
from testzeus_hercules.perceive import ElementGone, Snapshot, resolve, take_snapshot

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401


def _md(snap: Snapshot, name: str) -> int:
    found = [e.md for e in snap.elements if e.name == name]
    assert len(found) == 1, (name, [(e.role, e.name) for e in snap.elements])
    return found[0]


def test_click_inside_iframe_via_resolve(browser: BrowserHarness) -> None:
    async def scenario() -> str:
        async with browser.page("iframe.html") as page:
            snap = await take_snapshot(page, seq=1)
            card = await resolve(page, snap, _md(snap, "Card number"))
            await card.fill("4242")
            await (await resolve(page, snap, _md(snap, "Pay now"))).click()
            frame = page.frame(name="payment")
            assert frame is not None
            assert await frame.input_value("#card") == "4242"
            status = await frame.inner_text("#status")
            chat = await resolve(page, snap, _md(snap, "Chat with us"))
            await chat.click()
            assert await chat.inner_text() == "Connecting"
            return status

    assert browser.run(scenario()) == "paid"


def test_fill_and_click_inside_shadow_root_via_resolve(browser: BrowserHarness) -> None:
    async def scenario() -> str:
        async with browser.page("shadow.html") as page:
            snap = await take_snapshot(page, seq=1)
            await (await resolve(page, snap, _md(snap, "Query"))).fill("walker")
            await (await resolve(page, snap, _md(snap, "Go"))).click()
            slotted = await resolve(page, snap, _md(snap, "Slotted action"))
            assert await slotted.get_attribute("id") == "slotted"
            panel = await resolve(page, snap, _md(snap, "Panel button"))
            assert await panel.inner_text() == "Panel button"
            return await page.inner_text("#result")

    assert browser.run(scenario()) == "searched:walker"


def test_resolve_top_level_and_offscreen(browser: BrowserHarness) -> None:
    async def scenario() -> None:
        async with browser.page("long.html") as page:
            snap = await take_snapshot(page, seq=1)
            postal = await resolve(page, snap, _md(snap, "Postal code"))
            await postal.fill("10115")
            assert await page.input_value("#billing-zip") == "10115"

    browser.run(scenario())


def test_resolve_raises_element_gone(browser: BrowserHarness) -> None:
    async def scenario() -> None:
        async with browser.page("login.html") as page:
            snap = await take_snapshot(page, seq=1)
            with pytest.raises(ElementGone):
                await resolve(page, snap, 9999)
            sign_in = _md(snap, "Sign in")
            await page.evaluate("document.querySelector('button[type=submit]').textContent = 'Log out'")
            with pytest.raises(ElementGone):
                await resolve(page, snap, sign_in)
            forgot = _md(snap, "Forgot password?")
            await page.evaluate("document.querySelector('a').remove()")
            with pytest.raises(ElementGone):
                await resolve(page, snap, forgot)

    browser.run(scenario())


def test_resolve_survives_unrelated_dom_changes(browser: BrowserHarness) -> None:
    async def scenario() -> None:
        async with browser.page("controls.html") as page:
            snap = await take_snapshot(page, seq=1)
            bold = _md(snap, "Bold")
            await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<p>footer</p>')")
            locator = await resolve(page, snap, bold)
            assert await locator.get_attribute("aria-label") == "Bold"

    browser.run(scenario())


_RECTS_JS = """() => {
  const out = [];
  const visit = (root) => {
    for (const el of root.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      out.push([el.tagName, r.x, r.y, r.width, r.height]);
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(document);
  return [window.scrollX, window.scrollY, out];
}"""

_OBSERVE_JS = """() => {
  window.__seenMutations = [];
  const opts = { attributes: true, childList: true, subtree: true, characterData: true };
  const observer = new MutationObserver((records) => {
    for (const r of records) window.__seenMutations.push([r.type, r.target.nodeName, r.attributeName]);
  });
  observer.observe(document, opts);
  for (const el of document.querySelectorAll('*')) if (el.shadowRoot) observer.observe(el.shadowRoot, opts);
  window.__globalsBefore = null;
  window.__globalsBefore = Object.getOwnPropertyNames(window);
}"""


@pytest.mark.parametrize("name", ["form.html", "shadow.html", "iframe.html", "modal.html", "long.html"])
def test_snapshot_does_not_touch_the_page(browser: BrowserHarness, name: str) -> None:
    async def scenario() -> None:
        async with browser.page(name) as page:
            await page.evaluate("window.scrollTo(0, 120)")
            before = await page.evaluate(_RECTS_JS)
            await page.evaluate(_OBSERVE_JS)
            frame_before = [await f.evaluate(_RECTS_JS) for f in page.frames[1:]]
            await take_snapshot(page, seq=1)
            await page.evaluate("() => new Promise((r) => setTimeout(r, 50))")
            assert await page.evaluate("window.__seenMutations") == []
            added = await page.evaluate("Object.getOwnPropertyNames(window).filter((n) => !window.__globalsBefore.includes(n))")
            assert added == [], f"snapshot leaked globals: {added}"
            assert await page.evaluate(_RECTS_JS) == before
            assert [await f.evaluate(_RECTS_JS) for f in page.frames[1:]] == frame_before

    browser.run(scenario())
