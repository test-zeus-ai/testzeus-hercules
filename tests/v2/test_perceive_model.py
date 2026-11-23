"""Snapshot model and the observation text sent to models (no browser needed)."""

from __future__ import annotations

import dataclasses

import pytest
from testzeus_hercules.perceive import BBox, Element, ElementGone, Snapshot


def _el(md: int, role: str = "button", name: str = "", *, tier: int = 1, y: float = 0.0, x: float = 0.0, in_viewport: bool = True, **kw: object) -> Element:
    base = dict(
        md=md,
        role=role,
        name=name or f"item {md}",
        tag="button",
        attrs={},
        bbox=BBox(x, y, 80, 20),
        frame=None,
        in_viewport=in_viewport,
        tier=tier,
        value=None,
        checked=None,
        disabled=False,
    )
    base.update(kw)
    return Element(**base)  # type: ignore[arg-type]


def _snap(elements: tuple[Element, ...], **kw: object) -> Snapshot:
    base = dict(
        seq=14,
        url="https://shop.example/checkout?step=2#top",
        title="Checkout",
        viewport=(1280, 800),
        elements=elements,
        plain_jpeg=b"",
        som_jpeg=b"",
        above=(),
        below=(),
    )
    base.update(kw)
    return Snapshot(**base)  # type: ignore[arg-type]


def test_models_are_frozen() -> None:
    el = _el(1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        el.md = 2  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _snap((el,)).seq = 3  # type: ignore[misc]


def test_element_lookup_and_unknown_md() -> None:
    snap = _snap((_el(3), _el(7)))
    assert snap.element(7).md == 7
    with pytest.raises(KeyError):
        snap.element(99)


def test_element_gone_is_an_exception() -> None:
    assert issubclass(ElementGone, Exception)


def test_header_and_sections() -> None:
    snap = _snap((_el(1, name="Pay"),), above=("Header", "Cart summary"), below=("Payment options", "Footer"))
    lines = snap.observation_text().splitlines()
    assert lines[0] == "# page: Checkout · /checkout · viewport 1280x800 · snapshot 14"
    assert lines[1] == "# above: Header, Cart summary"
    assert lines[2] == "# below: Payment options, Footer"
    assert lines[3] == '[1] button "Pay"'


def test_no_section_lines_when_nothing_is_outside_the_viewport() -> None:
    text = _snap((_el(1),)).observation_text()
    assert "# above" not in text and "# below" not in text


def test_rows_ordered_by_tier_then_reading_order_and_offscreen_hidden() -> None:
    elements = (
        _el(1, "link", "Help", tier=1, y=10, x=500),
        _el(2, "textbox", "Email", tier=0, y=300, x=10, tag="input", value=""),
        _el(3, "button", "Go", tier=1, y=10, x=20),
        _el(4, "textbox", "Name", tier=0, y=100, x=10, tag="input", value="Ada"),
        _el(5, "button", "Below", tier=1, y=2000, in_viewport=False),
    )
    rows = [line for line in _snap(elements).observation_text().splitlines() if line.startswith("[")]
    assert [row.split("]")[0] + "]" for row in rows] == ["[4]", "[2]", "[3]", "[1]"]


def test_row_attributes_are_compact() -> None:
    elements = (
        _el(3, "textbox", "Email", tier=0, tag="input", value="", attrs={"type": "email", "aria-required": "true"}),
        _el(4, "checkbox", "Save address", tier=0, y=30, tag="input", checked=False),
        _el(5, "button", "Delete", tier=1, y=60, disabled=True),
        _el(6, "link", "Docs", tier=1, y=90, tag="a", attrs={"href": "/docs"}),
        _el(7, "button", "Pay now", tier=1, y=120, frame="Payment frame"),
    )
    rows = {line.split("]")[0][1:]: line for line in _snap(elements).observation_text().splitlines() if line.startswith("[")}
    assert rows["3"] == '[3] textbox "Email" | val="" type=email req'
    assert rows["4"] == '[4] checkbox "Save address" | checked=false'
    assert rows["5"] == '[5] button "Delete" | disabled'
    assert rows["6"] == '[6] link "Docs" | href=/docs'
    assert rows["7"] == '[7] button "Pay now" | frame="Payment frame"'


def test_quotes_in_names_and_values_are_escaped() -> None:
    row = _snap((_el(1, "textbox", 'Say "hi"', tier=0, value='a"b'),)).observation_text().splitlines()[1]
    assert row == '[1] textbox "Say \\"hi\\"" | val="a\\"b"'


def test_max_elements_truncates_with_a_footer() -> None:
    elements = tuple(_el(i, y=float(i)) for i in range(1, 31))
    text = _snap(elements).observation_text(max_elements=10)
    rows = [line for line in text.splitlines() if line.startswith("[")]
    assert len(rows) == 10
    assert text.splitlines()[-1] == "# … 20 more elements (scroll or page=2)"


def test_token_budget_truncates() -> None:
    elements = tuple(_el(i, name="x" * 60, y=float(i)) for i in range(1, 200))
    text = _snap(elements).observation_text(max_tokens=500)
    assert len(text) / 4 <= 500 + 30  # footer may exceed by one line
    assert "more elements (scroll or page=2)" in text


def test_page_two_continues_where_page_one_stopped() -> None:
    elements = tuple(_el(i, y=float(i)) for i in range(1, 26))
    snap = _snap(elements)
    first = [line for line in snap.observation_text(max_elements=10).splitlines() if line.startswith("[")]
    second = [line for line in snap.observation_text(max_elements=10, page=2).splitlines() if line.startswith("[")]
    assert first[-1].startswith("[10]") and second[0].startswith("[11]")
    assert not set(first) & set(second)


def test_no_footer_when_everything_fits() -> None:
    text = _snap(tuple(_el(i, y=float(i)) for i in range(1, 5))).observation_text()
    assert "more elements" not in text


def test_focus_terms_are_never_truncated() -> None:
    elements = tuple(_el(i, y=float(i)) for i in range(1, 50)) + (_el(99, name="Place order", y=5000.0, tier=3),)
    snap = _snap(elements, focus_terms=("place ORDER",))
    text = snap.observation_text(max_elements=5)
    assert '[99] button "Place order"' in text
    assert "# … 45 more elements" in text


def test_walker_script_ships_as_package_data() -> None:
    import tomllib
    from importlib import resources
    from pathlib import Path

    assert "(first, second) =>" in resources.files("testzeus_hercules.perceive").joinpath("walker.js").read_text(encoding="utf-8")
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "*.js" in pyproject["tool"]["setuptools"]["package-data"]["testzeus_hercules.perceive"]


def test_elements_behind_an_open_modal_are_not_offered_nor_listed() -> None:
    snap = _snap(
        (_el(1, name="New", blocked="behind_modal"), _el(2, name="Row action", blocked="behind_modal"), _el(3, name="Save", dialog="Edit"), _el(4, name="Skip", blocked="inert")),
        modal="Edit",
    )

    assert [e.md for e in snap.offered] == [3] and snap.not_offered == 3
    text = snap.observation_text()
    assert '# modal: "Edit" open; 2 elements behind it not offered' in text
    assert "# hidden: 1 element inert or aria-hidden, not offered" in text
    assert '"Save"' in text and '"New"' not in text and '"Skip"' not in text


def test_a_modal_whose_content_cannot_be_read_offers_every_element() -> None:
    snap = _snap((_el(1, name="New", blocked="behind_modal"), _el(2, name="Help", blocked="behind_modal")), modal="")

    assert [e.md for e in snap.offered] == [1, 2] and snap.not_offered == 0
    assert '# modal: "a dialog" open' not in snap.observation_text()
    assert "# modal: a dialog open, but no element in it could be read; every element is offered" in snap.observation_text()


def test_a_page_without_a_modal_has_no_modal_line() -> None:
    snap = _snap((_el(1, name="New"),))
    assert snap.modal is None and snap.offered == snap.elements
    assert "# modal" not in snap.observation_text() and "# hidden" not in snap.observation_text()
