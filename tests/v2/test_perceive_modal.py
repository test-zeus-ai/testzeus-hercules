"""Open modal dialogs, section context and re-found elements, on real Chromium.

``records.html`` imitates a list page built from web components: a data grid behind a modal dialog (aria-modal) whose
long form scrolls inside the dialog, in sections, and re-renders its fields (new nodes, new generated ids).
"""

from __future__ import annotations

from typing import Any

from testzeus_hercules.decide.locate import describe_locations
from testzeus_hercules.decide.questions import TargetSpec, build_step_requests
from testzeus_hercules.perceive import Element, ElementGone, Snapshot, resolve, take_snapshot

import pytest

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

FIELDS_IN_FORM = 23


def _named(snap: Snapshot, name: str, role: str | None = None) -> Element:
    found = [e for e in snap.elements if e.name == name and (role is None or e.role == role)]
    assert len(found) == 1, (name, [(e.role, e.name) for e in snap.elements])
    return found[0]


async def _open_new(page: Any) -> Snapshot:
    await page.get_by_role("button", name="New").click()
    return await take_snapshot(page, seq=2)


def test_only_the_modal_dialog_is_offered_and_the_header_says_so(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[Snapshot, Snapshot]:
        async with browser.page("records.html") as page:
            before = await take_snapshot(page, seq=1)
            return before, await _open_new(page)

    before, snap = browser.run(scenario())
    assert before.modal is None and len(before.offered) == len(before.elements), "no modal: every element is offered"
    assert "# modal" not in before.observation_text()

    assert snap.modal == "New Record"
    offered = {(e.role, e.name) for e in snap.offered}
    assert ("textbox", "Record Name") in offered and ("button", "Save") in offered and ("button", "Cancel") in offered
    assert ("button", "New") not in offered and ("button", "Row actions") not in offered
    assert not any(e.name.startswith("Select Item") for e in snap.offered), "the grid's row checkboxes are behind the modal"
    assert len(snap.offered) == FIELDS_IN_FORM + 3
    behind = [e for e in snap.elements if e.blocked == "behind_modal"]
    assert len(behind) == snap.not_offered == len(snap.elements) - len(snap.offered) > 100
    assert _named(snap, "New", "button").blocked == "behind_modal", "behind elements stay in the snapshot, marked"

    text = snap.observation_text()
    assert f'# modal: "New Record" open; {len(behind)} elements behind it not offered' in text
    assert '"Record Name"' in text and '"New"' not in text and "Row actions" not in text

    rows = [row for group in build_step_requests(TargetSpec(kind="fill", target="Record Name"), snap) for row in group.rows]
    assert {row.md for row in rows} == {e.md for e in snap.offered}, "the decider sees only the modal's elements"


def test_location_text_comes_from_the_dialog_and_section_not_the_screen(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page("records.html") as page:
            return await _open_new(page)

    snap = browser.run(scenario())
    where = describe_locations(snap)
    name = where[_named(snap, "Record Name").md]
    assert 'in dialog "New Record", under "Details"' in name
    assert "All records" not in name and "Records" not in name
    assert 'under "Address"' in where[_named(snap, "Street").md]
    assert 'under "Notes"' in where[_named(snap, "Description").md]
    save = where[_named(snap, "Save").md]
    assert 'in dialog "New Record"' in save and "under" not in save, "the dialog's buttons are not in the form's last section"


def test_a_field_scrolled_out_of_the_dialogs_scroll_area_says_so(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[Snapshot, Snapshot]:
        async with browser.page("records.html") as page:
            snap = await _open_new(page)
            await page.locator("x-modal").evaluate("(host) => { const body = host.shadowRoot.getElementById('body'); body.scrollTop = body.scrollHeight; }")
            await page.wait_for_timeout(300)  # the form re-renders after its scroll area is scrolled
            return snap, await take_snapshot(page, seq=3)

    top, bottom = browser.run(scenario())
    description = _named(top, "Description")
    assert description.scroll == "below" and description.scroll_in_dialog and not description.in_viewport
    text = describe_locations(top)[description.md]
    assert "in the dialog's scroll area, below the visible part" in text
    assert "below the visible area" not in text and "(x " not in text, "no page position for an element hidden in its scroll area"
    assert _named(top, "Record Name").scroll == ""

    record_name = _named(bottom, "Record Name")
    assert record_name.scroll == "above"
    assert "in the dialog's scroll area, above the visible part" in describe_locations(bottom)[record_name.md]
    assert _named(bottom, "Description").scroll == ""


def test_resolve_finds_a_rerendered_field_by_its_role_and_name_and_fills_it(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[dict[str, Any], str]:
        async with browser.page("records.html") as page:
            snap = await _open_new(page)
            md = _named(snap, "Record Name").md
            await page.evaluate("window.__rerender()")
            report: dict[str, Any] = {}
            locator = await resolve(page, snap, md, report=report)
            await locator.fill("Acme")
            await page.locator("x-modal").evaluate("(host) => host.shadowRoot.getElementById('save').click()")
            return report, await page.locator("#toast").inner_text()

    report, toast = browser.run(scenario())
    assert report["via"] == "locator" and report["strategy"] == "role" and report["name"] == "Record Name"
    assert report["within"] == "dialog" and "path_error" in report
    assert toast == 'Record "Acme" was created.'


def test_resolve_by_path_reports_the_path(browser: BrowserHarness) -> None:
    async def scenario() -> dict[str, Any]:
        async with browser.page("records.html") as page:
            snap = await _open_new(page)
            report: dict[str, Any] = {}
            await resolve(page, snap, _named(snap, "Phone").md, report=report)
            return report

    assert browser.run(scenario()) == {"via": "path"}


_CAPTION_ONLY = """<!doctype html><html><body>
<button>Behind</button>
<div role="dialog" aria-modal="true" aria-label="Edit profile" style="position:fixed;top:40px;left:40px;width:400px;background:#fff">
  <div id="form"></div>
</div>
<script>
  let n = 0;
  window.draw = () => {
    n += 1;
    const fields = '<div><span>Nickname</span><input class="f' + n + '"></div><div><span>Motto</span><input class="g' + n + '"></div>';
    document.getElementById("form").innerHTML = n > 1 ? "<section>" + fields + "</section>" : fields;
  };
  draw();
</script>
</body></html>"""


def test_resolve_falls_back_to_a_fresh_read_when_no_locator_names_the_field(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[dict[str, Any], str]:
        async with browser.page() as page:
            await page.set_content(_CAPTION_ONLY)
            snap = await take_snapshot(page, seq=1)
            field = _named(snap, "Nickname", "textbox")
            assert field.dialog == "Edit profile" and field.attrs.get("label") is None
            await page.evaluate("draw()")
            report: dict[str, Any] = {}
            locator = await resolve(page, snap, field.md, report=report)
            await locator.fill("ace")
            return report, await page.input_value("input.f2")

    report, value = browser.run(scenario())
    assert report["via"] == "fresh_read" and report["name"] == "Nickname"
    assert value == "ace"


def test_resolve_still_reports_a_field_that_is_gone(browser: BrowserHarness) -> None:
    async def scenario() -> None:
        async with browser.page("records.html") as page:
            snap = await _open_new(page)
            md = _named(snap, "Record Name").md
            await page.locator("x-modal").evaluate("(host) => host.shadowRoot.getElementById('cancel').click()")
            with pytest.raises(ElementGone):
                await resolve(page, snap, md)

    browser.run(scenario())


_EMPTY_MODAL = """<!doctype html><html><body>
<button>Reload</button><a href="#help">Help</a>
<div role="dialog" aria-modal="true" aria-label="Loading" style="position:fixed;top:40px;left:40px;width:300px;height:120px;background:#fff"><p>Please wait</p></div>
</body></html>"""


def test_a_modal_with_nothing_readable_falls_back_to_offering_everything(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page() as page:
            await page.set_content(_EMPTY_MODAL)
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())
    assert snap.modal == "Loading"
    assert {e.name for e in snap.offered} == {"Reload", "Help"}
    assert '# modal: "Loading" open, but no element in it could be read; every element is offered' in snap.observation_text()


_TOP_LAYER = """<!doctype html><html><body>
<main><button>Delete</button></main>
<div role="dialog" aria-modal="true" aria-label="Cookie banner" style="position:fixed;bottom:0;left:0;right:0;height:80px;background:#eee;z-index:5">
  <button>Accept</button>
</div>
<dialog id="confirm"><h2>Really delete?</h2><button>Yes</button><button>No</button></dialog>
<script>document.getElementById("confirm").showModal();</script>
</body></html>"""


def test_a_dialog_shown_as_modal_is_on_top_of_other_modal_layers(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page() as page:
            await page.set_content(_TOP_LAYER)
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())
    assert snap.modal == "Really delete?", "an unnamed dialog is known by its first heading"
    assert {e.name for e in snap.offered} == {"Yes", "No"}
    assert _named(snap, "Accept").blocked == "behind_modal"


_INERT = """<!doctype html><html><body>
<main inert><button>Buy</button></main>
<div aria-hidden="true"><a href="#x">Hidden link</a></div>
<button>Visible</button>
</body></html>"""


def test_inert_and_aria_hidden_subtrees_are_not_offered(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page() as page:
            await page.set_content(_INERT)
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())
    assert snap.modal is None
    assert [e.name for e in snap.offered] == ["Visible"]
    assert _named(snap, "Buy").blocked == "inert" and _named(snap, "Hidden link").blocked == "aria-hidden"
    assert "# hidden: 2 elements inert or aria-hidden, not offered" in snap.observation_text()


_FRAME_BEHIND = """<!doctype html><html><body>
<iframe srcdoc="<button>Inside frame</button>" title="widget"></iframe>
<div role="dialog" aria-modal="true" aria-label="Sign in" style="position:fixed;top:200px;left:40px;width:300px;background:#fff"><button>Continue</button></div>
</body></html>"""


def test_a_frame_behind_the_modal_is_behind_it_too(browser: BrowserHarness) -> None:
    async def scenario() -> Snapshot:
        async with browser.page() as page:
            await page.set_content(_FRAME_BEHIND)
            await page.wait_for_timeout(100)
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())
    assert _named(snap, "Inside frame").blocked == "behind_modal"
    assert [e.name for e in snap.offered] == ["Continue"]
