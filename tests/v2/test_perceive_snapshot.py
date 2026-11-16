"""take_snapshot on real Chromium against the static fixture pages."""

from __future__ import annotations

import io
import time
from itertools import combinations

import pytest
from PIL import Image
from testzeus_hercules.perceive import Element, Snapshot, take_snapshot
from testzeus_hercules.perceive.painter import COVERAGE_LIMIT

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

ALL_PAGES = ("login.html", "form.html", "controls.html", "iframe.html", "shadow.html", "modal.html", "long.html", "links.html", "labels.html")


def _snapshot(harness: BrowserHarness, name: str, **kw: object) -> Snapshot:
    async def scenario() -> Snapshot:
        async with harness.page(name) as page:
            return await take_snapshot(page, seq=1, **kw)  # type: ignore[arg-type]

    return harness.run(scenario())


def _named(snap: Snapshot, name: str, role: str | None = None) -> Element:
    found = [e for e in snap.elements if e.name == name and (role is None or e.role == role)]
    assert len(found) == 1, f"expected one element named {name!r}, got {found} in {[(e.role, e.name) for e in snap.elements]}"
    return found[0]


def test_login_page_basics(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "login.html")
    assert snap.title == "Sign in" and snap.viewport == (1280, 800) and snap.seq == 1
    user = _named(snap, "Username", "textbox")
    assert user.tier == 0 and user.tag == "input" and user.in_viewport and user.frame is None
    assert user.attrs["placeholder"] == "you@example.com"
    assert user.attrs["aria-required"] == "true"
    assert user.value == ""
    assert _named(snap, "Password", "textbox").attrs["type"] == "password"
    remember = _named(snap, "Remember me", "checkbox")
    assert remember.checked is False and remember.tier == 0
    assert _named(snap, "Sign in", "button").tier == 1
    link = _named(snap, "Forgot password?", "link")
    assert link.attrs["href"] == "/forgot-password"
    assert len({e.md for e in snap.elements}) == len(snap.elements)


def test_form_values_and_states(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "form.html")
    assert _named(snap, "Full name").value == "Ada Lovelace"
    assert _named(snap, "Password").value == "***", "secrets must not reach the model"
    country = _named(snap, "Country", "combobox")
    assert country.value == "India" and country.tier == 0
    assert _named(snap, "Subscribe to newsletter", "checkbox").checked is True
    assert _named(snap, "Free plan", "radio").checked is True
    assert _named(snap, "Pro plan", "radio").checked is False
    assert _named(snap, "Bio", "textbox").attrs["placeholder"] == "Tell us about yourself"
    assert _named(snap, "Birthday").attrs["type"] == "date"
    assert _named(snap, "Delete profile", "button").disabled is True
    assert _named(snap, "Save profile", "button").disabled is False
    assert not [e for e in snap.elements if e.role == "option"], "options inside a native select are not marked"


def test_names_for_icon_buttons_and_generic_controls(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "controls.html")
    assert _named(snap, "Bold", "button")
    assert _named(snap, "Italic", "button")
    assert _named(snap, "Insert link", "button")
    assert _named(snap, "Share document", "button")
    assert _named(snap, "Settings page", "link").attrs["href"] == "/settings"
    assert _named(snap, "Reports", "link").attrs["href"] == "/reports/2026"
    assert _named(snap, "Custom action", "button").tier == 1
    card = [e for e in snap.elements if e.name.startswith("Clickable card")]
    assert len(card) == 1 and card[0].tier == 3, "pointer cursor marks the card once, not its children"
    assert _named(snap, "More options", "button")
    overview = _named(snap, "Overview", "tab")
    assert overview.attrs["aria-selected"] == "true"


def test_iframe_elements_carry_frame_label_and_page_coordinates(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "iframe.html")
    pay = _named(snap, "Pay now", "button")
    card = _named(snap, "Card number", "textbox")
    outer = _named(snap, "Back to cart", "button")
    assert pay.frame == card.frame == "payment"
    assert outer.frame is None
    assert pay.in_viewport and pay.bbox is not None and outer.bbox is not None
    assert pay.bbox.y > outer.bbox.y + 20, "frame elements are offset into top-level coordinates"
    assert pay.bbox.x >= 24 + 4
    mds = [e.md for e in snap.elements]
    assert len(mds) == len(set(mds))


def test_cross_origin_frame_is_walked(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "iframe.html")
    chat = _named(snap, "Chat with us", "button")
    pay = _named(snap, "Pay now", "button")
    assert chat.frame == "Help widget" and chat.in_viewport
    assert chat.md > pay.md, "md counter continues across frames"
    assert chat.bbox is not None and pay.bbox is not None and chat.bbox.x > pay.bbox.x + 300


def test_shadow_and_slotted_elements(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "shadow.html")
    query = _named(snap, "Query", "searchbox")
    assert query.tier == 0 and query.in_viewport
    assert _named(snap, "Go", "button").in_viewport
    assert _named(snap, "Panel button", "button")
    assert _named(snap, "Slotted action", "button").in_viewport
    mds = [e.md for e in snap.elements]
    assert len(mds) == len(set(mds))


def test_element_covered_by_modal_is_not_in_viewport(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "modal.html")
    assert _named(snap, "Delete account").in_viewport is False
    assert _named(snap, "Confirm").in_viewport is True
    assert _named(snap, "Cancel").in_viewport is True
    text = snap.observation_text()
    assert "Delete account" not in text and '"Confirm"' in text


def test_below_the_fold_elements_stay_actionable(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "long.html")
    assert _named(snap, "Start trial").in_viewport
    zip_code = _named(snap, "Postal code")
    assert zip_code.in_viewport is False and zip_code.bbox is not None and zip_code.bbox.y > 800
    assert _named(snap, "Close account").in_viewport is False
    assert snap.below[:2] == ("Billing", "Danger zone")
    assert snap.above == ()
    text = snap.observation_text()
    assert "# below: Billing, Danger zone" in text and "Postal code" not in text


def test_above_lists_sections_scrolled_past(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page("long.html") as page:
            await page.evaluate("window.scrollTo(0, 1000)")
            return await take_snapshot(page, seq=2)

    snap = browser.run(scenario())
    assert "Overview" in snap.above
    assert _named(snap, "Postal code").in_viewport is True
    assert _named(snap, "Start trial").in_viewport is False


def test_md_ids_unique_across_frames_and_shadow_roots(browser: BrowserHarness) -> None:
    for name in ("iframe.html", "shadow.html"):
        snap = _snapshot(browser, name)
        mds = [e.md for e in snap.elements]
        assert len(mds) == len(set(mds)) and min(mds) >= 1


def test_images_are_jpeg_and_marks_painted(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "form.html")
    plain = Image.open(io.BytesIO(snap.plain_jpeg))
    som = Image.open(io.BytesIO(snap.som_jpeg))
    assert plain.format == som.format == "JPEG"
    assert som.size == plain.size == (1024, 640)
    assert snap.paint is not None
    fields = {e.md for e in snap.elements if e.tier == 0 and e.in_viewport}
    assert fields and fields <= set(snap.paint.painted)
    assert snap.paint.coverage <= COVERAGE_LIMIT
    for (_, a), (_, b) in combinations(snap.paint.badges.items(), 2):
        assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def test_dense_link_page_is_legible(browser: BrowserHarness) -> None:
    links = _snapshot(browser, "links.html")
    form = _snapshot(browser, "form.html")
    assert links.paint is not None and form.paint is not None
    link_mds = {e.md for e in links.elements if e.role == "link"}
    assert len(link_mds) == 60
    assert links.paint.font_size < form.paint.font_size
    unpainted = link_mds - set(links.paint.painted)
    assert unpainted, "some links give way to keep badges legible"
    text = links.observation_text()
    for md in unpainted:
        assert f"[{md}] link" in text, "unpainted links still appear in the text list"


def test_focus_terms_reach_the_observation(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "links.html", focus_terms=("Item 60",))
    target = _named(snap, "Item 60")
    assert target.md in snap.paint.painted  # type: ignore[union-attr]
    assert f'[{target.md}] link "Item 60"' in snap.observation_text(max_elements=3)


@pytest.mark.parametrize("name", ALL_PAGES)
def test_snapshot_is_fast(browser: BrowserHarness, name: str) -> None:
    async def scenario() -> float:
        async with browser.page(name) as page:
            await take_snapshot(page, seq=0)  # warm-up
            start = time.perf_counter()
            await take_snapshot(page, seq=1)
            return time.perf_counter() - start

    elapsed = browser.run(scenario())
    print(f"snapshot {name}: {elapsed * 1000:.0f} ms")
    assert elapsed < 1.5


def _field(snap: Snapshot, element_id: str) -> Element:
    found = [e for e in snap.elements if e.attrs.get("id") == element_id]
    assert len(found) == 1, (element_id, [(e.role, e.name, dict(e.attrs)) for e in snap.elements])
    return found[0]


def test_fields_without_a_tied_label_get_the_nearest_preceding_caption(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    employee, supervisor = _field(snap, "employee"), _field(snap, "supervisor")
    assert employee.role == supervisor.role == "textbox"
    assert employee.name == supervisor.name == "Start typing..."  # the accessible name stays the placeholder
    assert employee.attrs["label"] == "Person Name"
    assert supervisor.attrs["label"] == "Manager Name"
    assert _field(snap, "account").name == "Card number"
    assert _field(snap, "verify").name == "Confirm card number"
    assert _field(snap, "login-user").name == "Username"


def test_a_focusable_select_lookalike_is_a_combobox_named_by_its_caption(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    status = _field(snap, "status")
    assert status.role == "combobox" and status.tier == 0
    assert status.name == "Contract Type"
    assert status.value == "-- Select --"


def test_snapshot_waits_while_the_page_is_still_rendering(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page() as page:
            await page.set_content(
                "<div id=app></div><script>let n = 0; const add = () => { const b = document.createElement('button');"
                " b.textContent = n < 4 ? 'Loading ' + n : 'Ready'; document.getElementById('app').appendChild(b); if (n++ < 4) setTimeout(add, 120); };"
                " setTimeout(add, 50);</script>"
            )
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())
    assert any(e.name == "Ready" for e in snap.elements), [e.name for e in snap.elements]


def test_same_named_elements_carry_the_text_around_them(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    removes = [e for e in snap.elements if e.name == "Remove"]
    assert [e.attrs.get("near") for e in removes] == ["Lamp $9.99", "Daypack $29.99"]
    assert "near" not in _field(snap, "account").attrs
    assert 'near="Daypack $29.99"' in snap.observation_text()


def test_a_submit_button_is_told_apart_from_a_same_named_tab(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    tab, submit = _field(snap, "deposit-tab"), _field(snap, "deposit-submit")
    assert tab.name == submit.name == "Deposit"
    assert submit.attrs.get("type") == "submit" and "type" not in tab.attrs
    assert submit.attrs["near"] == "Amount to send"
    assert tab.attrs["near"] == "Withdraw"


def test_a_link_before_a_field_is_not_its_caption(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    search = _field(snap, "top-search")
    assert search.name == "Search" and "label" not in search.attrs


def test_the_inside_of_a_select_lookalike_is_not_a_second_combobox(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    assert [e.attrs.get("id") for e in snap.elements if e.role == "combobox"] == ["status"]


def test_a_button_in_a_form_names_the_forms_fields(browser: BrowserHarness) -> None:
    snap = _snapshot(browser, "labels.html")
    tab, submit = _field(snap, "deposit-tab"), _field(snap, "deposit-submit")
    assert "Amount to send" in submit.attrs["form"], submit.attrs
    assert "form" not in tab.attrs, "a tab outside the form has no form fields"


def test_the_snapshot_lists_what_the_page_announces(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page() as page:
            await page.set_content(
                "<h1>Checkout</h1><div role=alert>Error: Last Name is required</div><p aria-live=polite>2 items in your cart</p>"
                "<label for=zip>Zip</label><input id=zip aria-invalid=true aria-errormessage=zip-error><span id=zip-error>Enter a 5-digit zip</span>"
                "<div role=alert style='display:none'>hidden alert</div>"
            )
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())
    assert snap.messages == ("Error: Last Name is required", "2 items in your cart", "Enter a 5-digit zip")


def test_the_snapshot_waits_for_loading_indicators_and_transitions_to_finish(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[Snapshot, int]:
        async with browser.page() as page:
            await page.set_content(
                "<style>#panel { transition: opacity 700ms; opacity: 0 } #panel.open { opacity: 1 }</style>"
                "<div id=busy aria-busy=true>Loading…</div><div id=panel><button>Ready</button></div>"
                "<script>requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById('panel').classList.add('open')));"
                " setTimeout(() => { const b = document.getElementById('busy'); b.removeAttribute('aria-busy'); b.textContent = 'Results: 3'; }, 900);</script>"
            )
            snap = await take_snapshot(page, seq=1)
            running = await page.evaluate("document.getAnimations().filter((a) => a.playState === 'running').length")
            return snap, running

    snap, running = browser.run(scenario())
    assert running == 0, "no transition was still running when the page was read"
    assert not any(e.attrs.get("aria-busy") == "true" for e in snap.elements)
    assert "Results: 3" in snap.observation_text() or any(e.name == "Ready" for e in snap.elements)


def test_a_fields_error_help_text_and_format_hints_are_read(browser: BrowserHarness) -> None:
    from testzeus_hercules.perceive.model import row_text

    async def scenario() -> Snapshot:
        async with browser.page("record_form.html") as page:
            date = page.get_by_role("textbox", name="Expiry Date")  # type: ignore[attr-defined]
            await date.fill("12/31/2027")
            await date.evaluate("el => el.blur()")
            return await take_snapshot(page, seq=1)  # type: ignore[arg-type]

    snap = browser.run(scenario())
    date = _named(snap, "Expiry Date", "textbox")
    assert date.attrs["invalid"] == "true" and date.attrs["error"] == "Enter the date as DD/MM/YYYY." and date.attrs["placeholder"] == "DD/MM/YYYY"
    assert 'error="Enter the date as DD/MM/YYYY."' in row_text(date) and "invalid" in row_text(date)
    serial = _named(snap, "Serial Number", "textbox")
    assert serial.attrs["maxlength"] == "10" and "invalid" not in serial.attrs
