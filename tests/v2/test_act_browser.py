"""Tests for the browser action tools, against a real headless Chromium."""

import asyncio
import contextlib
import http.server
import threading
import time
from typing import Any, Awaitable, Callable

import pytest

from testzeus_hercules.act import browser as act
from testzeus_hercules.act.browser import ActionResult, InvalidURLError, resolve_url

from tests.v2.engine_fixtures.browser import FIXTURES_DIR, open_page

FORM = """
<html><body>
  <label for="name">Name</label><input id="name" value="old">
  <input id="locked" disabled value="x">
  <input id="ro" readonly value="y">
  <select id="country"><option value="in">India</option><option value="fr">France</option></select>
  <input type="checkbox" id="agree"><label for="agree">Agree</label>
  <input type="checkbox" id="off" disabled>
  <button id="go" onclick="document.getElementById('out').textContent='Clicked!'">Go</button>
  <button id="dead" disabled>Dead</button>
  <div id="menu" onmouseover="document.getElementById('out').textContent='Hovered'">Menu</div>
  <div id="out"></div>
  <script>setTimeout(() => { const p = document.createElement('p'); p.textContent = 'Late text'; document.body.appendChild(p); }, 300);</script>
</body></html>
"""


def run(body: Callable[[Any], Awaitable[None]], html: str = FORM) -> None:
    async def go() -> None:
        async with open_page(html) as page:
            await body(page)

    asyncio.run(go())


def test_resolve_url() -> None:
    assert resolve_url("/login", "http://127.0.0.1:8000/app/") == "http://127.0.0.1:8000/login"
    assert resolve_url("login", "http://127.0.0.1:8000/app/") == "http://127.0.0.1:8000/app/login"
    assert resolve_url("https://other.test/x", "http://127.0.0.1:8000") == "https://other.test/x"
    assert resolve_url("file:///tmp/a.html", None, file_roots=["/tmp"]) == "file:///tmp/a.html"
    for bad in ("javascript:alert(1)", "ftp://host/x", "data:text/html,hi"):
        with pytest.raises(InvalidURLError):
            resolve_url(bad, None)
    with pytest.raises(InvalidURLError, match="base_url"):
        resolve_url("/login", None)


def test_open_url_file_page() -> None:
    async def body(page: Any) -> None:
        result = await act.open_url(page, (FIXTURES_DIR / "login.html").as_uri(), file_roots=[FIXTURES_DIR])
        assert result.ok, result.detail
        assert result.url_after.endswith("login.html")

    run(body)


def test_click_fill_select_check_hover() -> None:
    async def body(page: Any) -> None:
        assert (await act.fill(page.locator("#name"), "Ada")).ok
        assert await page.input_value("#name") == "Ada"
        assert (await act.select(page.locator("#country"), "France")).ok
        assert await page.input_value("#country") == "fr"
        assert (await act.select(page.locator("#country"), "in")).ok
        assert await page.input_value("#country") == "in"
        assert (await act.check(page.locator("#agree"))).ok
        assert await page.is_checked("#agree")
        assert (await act.uncheck(page.locator("#agree"))).ok
        assert not await page.is_checked("#agree")
        click = await act.click(page.locator("#go"))
        assert isinstance(click, ActionResult) and click.ok
        assert await page.text_content("#out") == "Clicked!"
        assert (await act.hover(page.locator("#menu"))).ok
        assert await page.text_content("#out") == "Hovered"
        assert await act.read_value(page.locator("#name")) == "Ada"
        assert await act.read_value(page.locator("#out")) == "Hovered"

    run(body)


def test_disabled_and_readonly_fail_clearly() -> None:
    async def body(page: Any) -> None:
        locked = await act.fill(page.locator("#locked"), "z")
        assert not locked.ok and "disabled" in locked.detail
        readonly = await act.fill(page.locator("#ro"), "z")
        assert not readonly.ok and "not editable" in readonly.detail
        dead = await act.click(page.locator("#dead"))
        assert not dead.ok and "disabled" in dead.detail
        off = await act.check(page.locator("#off"))
        assert not off.ok and "disabled" in off.detail

    run(body)


def test_select_unknown_option_fails() -> None:
    async def body(page: Any) -> None:
        result = await act.select(page.locator("#country"), "Atlantis", timeout_ms=500)
        assert not result.ok
        assert "Atlantis" in result.detail

    run(body)


def test_missing_element_fails_within_timeout() -> None:
    async def body(page: Any) -> None:
        result = await act.click(page.locator("#nope"), timeout_ms=300)
        assert not result.ok

    run(body)


def test_press_and_wait_for_text() -> None:
    async def body(page: Any) -> None:
        await page.focus("#name")
        assert (await act.press(page, "End")).ok
        assert (await act.wait_for_text(page, "Late text", timeout_ms=3000)).ok
        missing = await act.wait_for_text(page, "Never shown", timeout_ms=300)
        assert not missing.ok and "Never shown" in missing.detail

    run(body)


def test_fill_value_is_not_echoed_in_details() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#name"), "hunter2-secret")
        assert "hunter2" not in result.detail

    run(body)


SHADOW = """
<html><body>
  <p>Light text</p>
  <coupon-box></coupon-box>
  <iframe srcdoc="<p>Frame text</p>"></iframe>
  <script>
    const host = document.querySelector('coupon-box');
    const root = host.attachShadow({mode: 'open'});
    root.innerHTML = '<p>Coupon applied: 10% off</p><p style="display:none">Hidden shadow text</p><inner-box></inner-box>';
    const inner = root.querySelector('inner-box').attachShadow({mode: 'open'});
    inner.innerHTML = '<span>Nested shadow text</span>';
  </script>
</body></html>
"""


def test_page_text_includes_open_shadow_roots_and_same_origin_frames() -> None:
    async def body(page: Any) -> None:
        await page.wait_for_function("document.querySelector('iframe').contentDocument && document.querySelector('iframe').contentDocument.body.innerText.length > 0")
        text = await act.page_text(page)
        assert "Light text" in text
        assert "Coupon applied: 10% off" in text
        assert "Nested shadow text" in text
        assert "Frame text" in text
        assert "Hidden shadow text" not in text
        assert (await act.wait_for_text(page, "Coupon applied", timeout_ms=500)).ok

    run(body, SHADOW)


INPUTS = """
<html><body>
  <label for="when">Delivery date</label><input id="when" type="date">
  <input id="at" type="time"><input id="local" type="datetime-local"><input id="month" type="month"><input id="week" type="week">
  <input id="colour" type="color"><input id="level" type="range" min="0" max="100">
  <input id="short" maxlength="3">
  <input id="phone" oninput="this.value = this.value.replace(/\\D/g, '').replace(/(\\d{3})(?=\\d)/g, '$1 ')">
  <input id="pin" type="password" maxlength="2">
</body></html>
"""


@pytest.mark.parametrize(
    ("selector", "value"),
    [("#when", "2026-12-01"), ("#at", "10:30"), ("#local", "2026-12-01T10:30"), ("#month", "2026-12"), ("#week", "2026-W49"), ("#colour", "#ff0000"), ("#level", "42")],
)
def test_fill_sets_special_input_types_directly(selector: str, value: str) -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator(selector), value)
        assert result.ok, result.detail
        assert await page.input_value(selector) == value

    run(body, INPUTS)


def test_fill_fails_when_the_field_does_not_hold_the_value_afterwards() -> None:
    async def body(page: Any) -> None:
        await page.evaluate("document.querySelector('#phone').addEventListener('input', (e) => { e.target.value = e.target.value.slice(0, 2) + 'x'; })")
        result = await act.fill(page.locator("#phone"), "123456")
        assert not result.ok
        assert result.kind == "value_mismatch"
        assert "holds 3 characters" in result.detail and "12" not in result.detail, "lengths only, never the value"
        assert result.retry is False

    run(body, INPUTS)


def test_fill_of_a_value_longer_than_the_field_accepts_says_so() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#short"), "abcdef")
        assert not result.ok and result.kind == "too_long" and result.retry is False
        assert result.detail == "fill failed: the field accepts at most 3 characters; the step's value has 6"

    run(body, INPUTS)


def test_fill_read_back_tolerates_input_formatting() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#phone"), "5551234567")
        assert result.ok, result.detail

    run(body, INPUTS)


def test_fill_mismatch_on_a_password_never_echoes_the_value() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#pin"), "hunter2")
        assert not result.ok and result.kind == "too_long"
        assert "hu" not in result.detail

    run(body, INPUTS)


def test_disabled_element_result_is_marked() -> None:
    async def body(page: Any) -> None:
        dead = await act.click(page.locator("#dead"))
        assert dead.kind == "disabled"

    run(body)


SELECTS = """
<html><body>
  <label for="who">Account holder</label>
  <select id="who"><option value="">---Account holder---</option><option>Alan Turing</option><option selected>Grace Hopper</option></select>
  <select id="colours" multiple><option>Red</option><option>Blue</option></select>
  <p>After the drop-downs</p>
</body></html>
"""


def test_closed_drop_down_texts_lists_what_innertext_adds_for_each_closed_drop_down() -> None:
    async def body(page: Any) -> None:
        assert "Alan Turing" in await act.page_text(page)
        ((text, shown),) = await act.closed_drop_down_texts(page)
        assert "Alan Turing" in text and "Grace Hopper" in text
        assert shown == "Grace Hopper"

    run(body, SELECTS)


CUSTOM_DROPDOWNS = """
<html><body>
  <div class="field"><label>Status</label>
    <div class="picker-display" tabindex="0" onclick="document.getElementById('status-list').style.display='block'">-- Select --</div>
    <div id="status-list" role="listbox" style="display:none">
      <div role="option" onclick="pick(this, 'status')">Permanent</div>
      <div role="option" onclick="pick(this, 'status')">Temporary</div>
    </div>
  </div>
  <div class="field"><label>Colour</label>
    <div class="dropdown" tabindex="0" onclick="document.getElementById('colour-list').style.display='block'">Choose</div>
    <ul id="colour-list" style="display:none"><li onclick="pick(this, 'colour')">Green</li><li onclick="pick(this, 'colour')">Greenish</li></ul>
  </div>
  <p>Chosen: <span id="status"></span> <span id="colour"></span></p>
  <script>
    function pick(el, out) { document.getElementById(out).textContent = el.textContent; el.parentElement.style.display = 'none'; }
  </script>
</body></html>
"""


def test_select_opens_a_custom_drop_down_and_picks_the_option() -> None:
    async def body(page: Any) -> None:
        result = await act.select(page.locator(".picker-display"), "Temporary")
        assert result.ok, result.detail
        assert await page.text_content("#status") == "Temporary"
        result = await act.select(page.locator(".dropdown"), "Green")
        assert result.ok, result.detail
        assert await page.text_content("#colour") == "Green"

    run(body, CUSTOM_DROPDOWNS)


def test_select_on_a_custom_drop_down_reports_a_missing_option() -> None:
    async def body(page: Any) -> None:
        result = await act.select(page.locator(".picker-display"), "Contractor", timeout_ms=800)
        assert not result.ok
        assert "Contractor" in result.detail

    run(body, CUSTOM_DROPDOWNS)


STUBBORN = """
<html><body>
  <input id="last" onkeydown="if (event.key.length === 1) event.preventDefault()">
</body></html>
"""


def test_fill_sets_the_value_directly_when_typed_keys_are_swallowed() -> None:
    async def body(page: Any) -> None:
        result = await act.fill(page.locator("#last"), "Lovelace")
        assert result.ok, result.detail
        assert await page.input_value("#last") == "Lovelace"

    run(body, STUBBORN)


class _SlowFormHandler(http.server.BaseHTTPRequestHandler):
    delay_s = 2.0

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature from the base class
        return None

    def _send(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/data"):
            time.sleep(1.0)
            self._send("Peter Pan")
            return
        if self.path.startswith("/debounced"):
            # Typing shows "Searching..." at once and asks for suggestions 700 ms after the last key.
            self._send(
                "<html><body><input id=\"q\" oninput=\"const l = document.getElementById('list'); l.innerHTML = '<div role=option>Searching....</div>';"
                " clearTimeout(window.t); window.t = setTimeout(() => fetch('/data').then(r => r.text()).then(t => { l.innerHTML = '<div role=option>' + t + '</div>'; }), 700)\">"
                '<div id="list" role="listbox"></div></body></html>'
            )
            return
        if self.path.startswith("/suggest"):
            self._send(
                "<html><body><input id=\"q\" oninput=\"fetch('/data').then(r => r.text()).then(t => { const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = t; document.body.appendChild(o); })\"></body></html>"
            )
            return
        if self.path.startswith("/home"):
            self._send("<html><body><h1>Signed in</h1></body></html>")
        else:
            self._send('<html><body><form method="post" action="/login"><input name="u"><button id="go">Login</button></form></body></html>')

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        time.sleep(self.delay_s)
        self.send_response(303)
        self.send_header("Location", "/home")
        self.send_header("Content-Length", "0")
        self.end_headers()


@contextlib.contextmanager
def _slow_server() -> Any:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowFormHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_click_waits_for_the_navigation_it_starts_even_when_the_server_is_slow() -> None:
    async def go() -> None:
        with _slow_server() as base:
            async with open_page(url=f"{base}/") as page:
                result = await act.click(page.locator("#go"), timeout_ms=1000)
                assert result.ok, result.detail
                assert page.url.endswith("/home")
                assert "Signed in" in await act.page_text(page)
                opened = await act.open_url(page, "/", base_url=base)
                assert opened.ok, opened.detail

    asyncio.run(go())


SCRIPTED_SUBMIT = """
<html><body>
  <form method="post" action="/login"><input name="u"></form>
  <button id="go" onclick="setTimeout(() => document.forms[0].submit(), 30)">Login</button>
</body></html>
"""


def test_click_catches_a_submission_that_page_script_starts_just_after_the_click() -> None:
    async def go() -> None:
        with _slow_server() as base:
            async with open_page(url=f"{base}/") as page:
                await page.set_content(SCRIPTED_SUBMIT.replace('action="/login"', f'action="{base}/login"'))
                result = await act.click(page.locator("#go"), timeout_ms=1000)
                assert result.ok, result.detail
                assert page.url.endswith("/home")

    asyncio.run(go())


def test_opening_a_url_waits_for_a_submission_still_in_flight() -> None:
    async def go() -> None:
        with _slow_server() as base:
            async with open_page(url=f"{base}/") as page:
                act.watch_page(page)
                await page.evaluate("document.forms[0].submit()")  # submitted outside any action
                opened = await act.open_url(page, "/home?again=1", base_url=base)
                assert opened.ok, opened.detail
                assert page.url.endswith("/home?again=1")
                history = await page.evaluate("history.length")
                assert history >= 3, "the submission landed before the next page was opened"

    asyncio.run(go())


def test_press_enter_waits_for_the_form_submission() -> None:
    async def go() -> None:
        with _slow_server() as base:
            async with open_page(url=f"{base}/") as page:
                await page.focus("input[name=u]")
                result = await act.press(page, "Enter")
                assert result.ok, result.detail
                assert page.url.endswith("/home")

    asyncio.run(go())


DIALOGS = """
<html><body>
  <button id="add" onclick="alert('Customer added successfully with customer id :6')">Add</button>
  <button id="ask" onclick="document.getElementById('out').textContent = confirm('Delete it?') ? 'deleted' : 'kept'">Delete</button>
  <p id="out"></p>
</body></html>
"""


def test_dialogs_are_answered_and_their_message_is_part_of_what_the_page_showed() -> None:
    async def body(page: Any) -> None:
        act.watch_page(page)
        result = await act.click(page.locator("#add"))
        assert result.ok, result.detail
        assert "Customer added successfully" in result.detail
        assert "Customer added successfully" in await act.page_text(page)
        assert (await act.click(page.locator("#ask"))).ok
        assert await page.text_content("#out") == "kept", "a confirmation is declined, as a cautious tester would"
        assert "Customer added successfully" not in await act.page_text(page), "only the latest action's dialogs count"
        assert "Delete it?" in await act.page_text(page)

    run(body, DIALOGS)


def test_an_action_waits_for_the_requests_it_started_on_the_same_site() -> None:
    async def go() -> None:
        with _slow_server() as base:
            async with open_page(url=f"{base}/suggest") as page:
                act.watch_page(page)
                result = await act.fill(page.locator("#q"), "Pe")
                assert result.ok, result.detail
                assert await page.get_by_role("option", name="Peter Pan").count() >= 1, "the suggestions arrived before the action returned"

    asyncio.run(go())


def test_typing_that_opens_a_suggestion_list_waits_for_its_debounced_request() -> None:
    async def go() -> None:
        with _slow_server() as base:
            async with open_page(url=f"{base}/debounced") as page:
                act.watch_page(page)
                result = await act.fill(page.locator("#q"), "Pe")
                assert result.ok, result.detail
                assert await page.get_by_role("option", name="Peter Pan").count() == 1, "the suggestions arrived before the action returned"
                assert await page.get_by_role("option", name="Searching....").count() == 0

    asyncio.run(go())


def test_typing_without_a_suggestion_list_does_not_wait_for_requests() -> None:
    async def body(page: Any) -> None:
        act.watch_page(page)
        started = time.monotonic()
        assert (await act.fill(page.locator("#name"), "Ada")).ok
        assert time.monotonic() - started < act.SUGGESTIONS_START_GRACE_S

    run(body)


# --------------------------------------------------------------------------- the field after it lost focus


def _record_form(body: Callable[[Any], Awaitable[None]]) -> None:
    async def go() -> None:
        async with open_page((FIXTURES_DIR.parent / "fixtures" / "pages" / "record_form.html").read_text()) as page:
            act.watch_page(page)
            await body(page)

    asyncio.run(go())


def test_a_value_the_field_rejects_once_it_loses_focus_fails_with_the_fields_error_and_hints() -> None:
    async def body(page: Any) -> None:
        date = page.get_by_role("textbox", name="Expiry Date")
        result = await act.fill(date, "12/31/2027")
        assert not result.ok and result.kind == "invalid" and result.retry is False
        assert result.detail == "the field marked the value invalid after it lost focus: Enter the date as DD/MM/YYYY."
        assert result.field is not None and result.field.blurred and result.field.value == "12/31/2027"
        assert result.field.error_text == "Enter the date as DD/MM/YYYY."
        assert result.field.hints["placeholder"] == "DD/MM/YYYY"
        accepted = await act.fill(date, "31/12/2027")
        assert accepted.ok, accepted.detail
        assert accepted.field is not None and not accepted.field.invalid and accepted.field.hint == "Day, month and year"
        assert await page.evaluate("document.activeElement.id") == "expiry", "focus is given back to the field"

    _record_form(body)


def test_a_field_that_rewrites_the_value_on_blur_reports_what_it_holds() -> None:
    async def body(page: Any) -> None:
        phone = page.get_by_label("Phone")
        same = await act.fill(phone, "5551234567")
        assert same.ok, "(555) 123-4567 holds the same digits"
        changed = await act.fill(phone, "+1 555 123 4567")
        assert not changed.ok and changed.kind == "changed" and changed.field is not None and changed.field.value == "(555) 123-4567"

    _record_form(body)


def test_a_disabled_or_empty_dependent_drop_down_is_reported_as_such() -> None:
    async def body(page: Any) -> None:
        region = page.get_by_label("Region")
        assert (await act.select(region, "Ontario")).kind == "disabled"
        await page.evaluate("document.querySelector('#region').disabled = false")
        missing = await act.select(region, "Ontario")
        assert missing.kind == "option_missing" and "the drop-down has no options" in missing.detail
        assert (await act.select(page.get_by_label("Country"), "Canada")).ok
        chosen = await act.select(region, "Ontario")
        assert chosen.ok and chosen.field is not None and not chosen.field.invalid

    _record_form(body)


def test_a_form_that_opts_out_of_browser_validation_is_judged_by_its_own_marks_only() -> None:
    html = """
<html><body>
  <form novalidate><label for="a">Email</label><input id="a" type="email"></form>
  <form><label for="b">Other email</label><input id="b" type="email"></form>
</body></html>
"""

    async def body(page: Any) -> None:
        assert (await act.fill(page.locator("#a"), "not-an-email")).ok
        native = await act.fill(page.locator("#b"), "not-an-email")
        assert native.kind == "invalid" and native.field is not None and native.field.native

    run(body, html)


UPLOAD_PAGE = (FIXTURES_DIR.parent / "fixtures" / "pages" / "upload.html").read_text(encoding="utf-8")


def _upload_files(tmp_path: Any) -> list[Any]:
    root = tmp_path / "project"
    (root / "fixtures").mkdir(parents=True)
    (root / "fixtures" / "case.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (root / "fixtures" / "invoice.pdf").write_bytes(b"%PDF-1.4")
    (root / ".env").write_text("TOKEN=x")
    return [root]


async def _held(page: Any, selector: str) -> list[str]:
    return await page.eval_on_selector(selector, "el => Array.from(el.files, (file) => file.name)")


def test_upload_attaches_files_to_inputs_labels_and_file_pickers(tmp_path: Any) -> None:
    roots = _upload_files(tmp_path)

    async def body(page: Any) -> None:
        photo = await act.upload(page.locator("#photo"), ["fixtures/case.png"], roots=roots)
        assert photo.ok, photo.detail
        assert await _held(page, "#photo") == ["case.png"]
        assert photo.chosen == {"files": [str((roots[0] / "fixtures" / "case.png").resolve())]}
        documents = await act.upload(page.locator("#documents"), ["fixtures/case.png", "fixtures/invoice.pdf"], roots=roots)
        assert documents.ok and await _held(page, "#documents") == ["case.png", "invoice.pdf"]
        receipt = await act.upload(page.get_by_text("Drop the receipt here"), ["fixtures/invoice.pdf"], roots=roots)
        assert receipt.ok, receipt.detail
        assert await _held(page, "#receipt") == ["invoice.pdf"]
        extras = await act.upload(page.locator("#choose"), ["fixtures/case.png", "fixtures/invoice.pdf"], roots=roots)
        assert extras.ok and "file picker" in extras.detail, extras.detail
        assert await _held(page, "#extras") == ["case.png", "invoice.pdf"]

    run(body, UPLOAD_PAGE)


def test_upload_failures_say_whether_another_element_can_help(tmp_path: Any) -> None:
    roots = _upload_files(tmp_path)

    async def body(page: Any) -> None:
        missing = await act.upload(page.locator("#photo"), ["nowhere.png"], roots=roots)
        assert (missing.ok, missing.kind, missing.retry) == (False, "file_missing", False) and "nowhere.png" in missing.detail and "project folder" in missing.detail
        secret = await act.upload(page.locator("#photo"), [".env"], roots=roots)
        assert (secret.ok, secret.kind, secret.retry) == (False, "file_missing", False)
        outside = await act.upload(page.locator("#photo"), ["../outside.png"], roots=roots)
        assert not outside.ok and not outside.retry
        too_many = await act.upload(page.locator("#photo"), ["fixtures/case.png", "fixtures/invoice.pdf"], roots=roots)
        assert (too_many.ok, too_many.kind, too_many.retry) == (False, "too_many_files", False) and "takes one file" in too_many.detail
        picker_one = await act.upload(page.locator("#single-picker"), ["fixtures/case.png", "fixtures/invoice.pdf"], roots=roots)
        assert (picker_one.ok, picker_one.kind) == (False, "too_many_files")
        none = await act.upload(page.locator("#photo"), [], roots=roots)
        assert not none.ok and not none.retry
        plain = await act.upload(page.locator("#help"), ["fixtures/case.png"], roots=roots, timeout_ms=1_000)
        assert not plain.ok and plain.retry and "opened no file picker" in plain.detail
        assert await _held(page, "#photo") == []

    run(body, UPLOAD_PAGE)


def _downloads(body: Callable[[Any, str], Awaitable[None]]) -> None:
    from tests.v2.fixtures.download_server import serve_downloads

    async def go() -> None:
        with serve_downloads() as base:
            async with open_page(url=f"{base}/downloads.html") as page:
                await body(page, base)

    asyncio.run(go())


def test_download_by_click_keeps_attachments_files_in_new_tabs_and_script_made_files(tmp_path: Any) -> None:
    from tests.v2.fixtures.download_server import REPORT_CSV

    folder = tmp_path / "downloads"

    async def body(page: Any, base: str) -> None:
        csv_file = await act.download_by_click(page.locator("#csv"), folder, expected_name="report.csv")
        assert csv_file.ok, csv_file.detail
        assert csv_file.chosen is not None and (csv_file.chosen["name"], csv_file.chosen["type"], csv_file.chosen["via"]) == ("report.csv", "csv", "download")
        assert (folder / "report.csv").read_bytes() == REPORT_CSV and csv_file.chosen["size"] == len(REPORT_CSV)
        again = await act.download_by_click(page.locator("#csv-again"), folder)
        assert again.ok and again.chosen is not None and again.chosen["name"] == "report_2.csv"
        invoice = await act.download_by_click(page.locator("#invoice"), folder)
        assert invoice.ok and invoice.chosen is not None and (invoice.chosen["name"], invoice.chosen["type"]) == ("invoice_INV-77.pdf", "pdf")
        statement = await act.download_by_click(page.locator("#statement"), folder)
        assert statement.ok, statement.detail
        assert statement.chosen is not None and statement.chosen["type"] == "pdf" and statement.chosen["name"].startswith("statement")
        summary = await act.download_by_click(page.locator("#summary"), folder)
        assert summary.ok and (folder / "summary.txt").read_text() == "Summary: 3 orders, 2 paid"
        assert len(page.context.pages) == 1, "a tab opened only to show a file is closed"

    _downloads(body)


def test_download_by_click_failures_say_whether_another_element_can_help(tmp_path: Any) -> None:
    folder = tmp_path / "downloads"

    async def body(page: Any, base: str) -> None:
        nothing = await act.download_by_click(page.locator("#nothing"), folder, timeout_ms=1_500)
        assert (nothing.ok, nothing.kind, nothing.retry) == (False, "no_download", True) and "started no download" in nothing.detail
        empty = await act.download_by_click(page.locator("#empty"), folder)
        assert (empty.ok, empty.kind, empty.retry) == (False, "wrong_file", False) and "empty" in empty.detail
        named = await act.download_by_click(page.locator("#csv"), folder, expected_name="orders.csv")
        assert (named.ok, named.retry) == (False, False) and "named 'report.csv', not 'orders.csv'" in named.detail
        page_link = await act.download_by_click(page.locator("#plain"), folder, timeout_ms=1_500)
        assert not page_link.ok and page_link.retry
        assert sorted(path.name for path in folder.iterdir()) == ["report.csv"]

    _downloads(body)


def test_download_from_url_keeps_attachments_and_inline_files_and_refuses_pages(tmp_path: Any) -> None:
    from tests.v2.fixtures.download_server import ORDERS_JSON

    folder = tmp_path / "downloads"

    async def body(page: Any, base: str) -> None:
        sheet = await act.download_from_url(page, "/files/orders.xlsx", folder, base_url=base)
        assert sheet.ok and sheet.chosen is not None and (sheet.chosen["name"], sheet.chosen["type"]) == ("orders.xlsx", "xlsx"), sheet.detail
        shown = await act.download_from_url(page, f"{base}/data/orders.json", folder)
        assert shown.ok and shown.chosen is not None and (shown.chosen["name"], shown.chosen["via"], shown.chosen["type"]) == ("orders.json", "response", "json")
        assert (folder / "orders.json").read_bytes() == ORDERS_JSON
        web_page = await act.download_from_url(page, "/plain.html", folder, base_url=base)
        assert (web_page.ok, web_page.kind, web_page.retry) == (False, "not_a_file", False) and "is a web page, not a file" in web_page.detail
        assert page.url.endswith("/downloads.html"), "downloading from an address leaves the page where it is"
        missing = await act.download_from_url(page, "/files/nope.csv", folder, base_url=base)
        assert not missing.ok and not missing.retry and "HTTP 404" in missing.detail
        assert act.download_name("../../etc/pass wd?.csv") == "etc_pass_wd_.csv" and act.download_name("...") == "download"

    _downloads(body)


def test_a_file_that_says_it_is_too_large_is_refused_before_its_bytes_are_read(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.async_api import APIResponse

    folder = tmp_path / "downloads"
    read: list[str] = []
    real_body = APIResponse.body

    async def counted_body(self: APIResponse) -> bytes:
        read.append(self.url)
        return await real_body(self)

    monkeypatch.setattr(APIResponse, "body", counted_body)
    monkeypatch.setattr(act, "MAX_DOWNLOAD_BYTES", 64)

    async def body(page: Any, base: str) -> None:
        shown = await act.download_from_url(page, f"{base}/data/orders.json", folder)
        assert (shown.ok, shown.kind, shown.retry) == (False, "wrong_file", False) and "'orders.json' is larger than" in shown.detail
        notes = await act.download_by_click(page.locator("#notes"), folder)
        assert (notes.ok, notes.kind, notes.retry) == (False, "wrong_file", False) and "'notes.txt' is larger than" in notes.detail
        assert len(page.context.pages) == 1 and read == [] and not folder.exists()
        unsized = await act.download_from_url(page, f"{base}/data/unsized.txt", folder)
        assert (unsized.ok, unsized.kind) == (False, "wrong_file") and "'unsized.txt' is larger than" in unsized.detail
        assert read == [f"{base}/data/unsized.txt"] and list(folder.iterdir()) == []

    _downloads(body)


SKIP_LINK_PAGE = """
<html><head><style>
  .skip { position: absolute; left: 1rem; top: -3rem; }
  .skip:focus { top: 1rem; }
</style></head><body>
  <a class="skip" href="#main">Skip to content</a>
  <nav><a href="#nav">Shop</a></nav>
  <main id="main" tabindex="-1"><h1>Quote</h1></main>
</body></html>
"""


def test_a_link_off_the_page_until_focused_is_activated_with_the_keyboard() -> None:
    async def body(page: Any) -> None:
        started = time.monotonic()
        result = await act.click(page.get_by_role("link", name="Skip to content"))
        assert result.ok and "keyboard" in result.detail, result.detail
        assert page.url.endswith("#main") and time.monotonic() - started < 3
        shop = await act.click(page.get_by_role("link", name="Shop"))
        assert shop.ok and shop.detail == "clicked" and page.url.endswith("#nav")

    run(body, SKIP_LINK_PAGE)


SCROLLED_PAGE = """
<html><head><style>
  .skip { position: absolute; left: 1rem; top: -3rem; }
  .skip:focus { top: 1rem; }
  #list { height: 80px; overflow: auto; }
  #list p { height: 60px; margin: 0; }
</style></head><body>
  <a class="skip" href="#main">Skip to content</a>
  <input type="checkbox" id="agree"><label for="agree">Agree</label>
  <div id="menu" onclick="this.textContent = 'Menu opened'">Menu</div>
  <div id="list"><p onclick="this.textContent = 'First picked'">First</p><p>Second</p><p>Third</p><p>Fourth</p></div>
  <div id="host"></div>
  <div style="height: 3000px"></div>
  <main id="main" tabindex="-1"><h1>Quote</h1></main>
  <script>
    const save = document.createElement('button');
    save.textContent = 'Save';
    save.onclick = () => { save.textContent = 'Saved'; };
    document.getElementById('host').attachShadow({ mode: 'open' }).append(save);
  </script>
</body></html>
"""


def test_an_element_scrolled_above_the_view_is_clicked_and_a_skip_link_still_gets_the_keyboard() -> None:
    async def scroll_away(page: Any) -> None:
        await page.evaluate("document.getElementById('list').scrollTop = 200; window.scrollTo(0, document.body.scrollHeight)")

    async def body(page: Any) -> None:
        await scroll_away(page)
        agree = await act.click(page.locator("#agree"))
        assert agree.detail == "clicked" and await page.locator("#agree").is_checked()
        await scroll_away(page)
        menu = await act.click(page.locator("#menu"))
        assert menu.detail == "clicked" and await page.locator("#menu").inner_text() == "Menu opened"
        await scroll_away(page)
        first = await act.click(page.get_by_text("First"))
        assert first.detail == "clicked" and await page.locator("#list p").first.inner_text() == "First picked"
        await scroll_away(page)
        save = await act.click(page.get_by_role("button", name="Save"))
        assert save.detail == "clicked" and await page.get_by_role("button", name="Saved").count() == 1
        await scroll_away(page)
        skip = await act.click(page.get_by_role("link", name="Skip to content"))
        assert skip.ok and "keyboard" in skip.detail and page.url.endswith("#main"), skip.detail

    run(body, SCROLLED_PAGE)
