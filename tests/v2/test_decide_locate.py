"""Location text for decision candidates: screen region, position, heading, rank among look-alikes, frame and shadow DOM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from testzeus_hercules.decide.locate import describe_locations, region_of

from tests.v2.engine_fixtures.fakes import FakeBBox, FakeElement, FakeSnapshot


@dataclass(frozen=True)
class Path_:
    segments: tuple[str, ...]


@dataclass(frozen=True)
class Snap(FakeSnapshot):
    headings: tuple[tuple[str, float], ...] = ()
    paths: Mapping[int, Any] = field(default_factory=dict)


def box(x: float, y: float, w: float = 100, h: float = 30) -> FakeBBox:
    return FakeBBox(x=x, y=y, width=w, height=h)


@pytest.mark.parametrize(
    ("x", "y", "region"),
    [
        (10, 10, "top-left"),
        (640, 10, "top"),
        (1270, 10, "top-right"),
        (10, 400, "left"),
        (640, 400, "center"),
        (1270, 400, "right"),
        (10, 790, "bottom-left"),
        (640, 790, "bottom"),
        (1270, 790, "bottom-right"),
        (640, -20, "above the visible area"),
        (640, 1500, "below the visible area"),
        (-5, 400, "outside the visible area"),
    ],
)
def test_region_of_names_the_ninth_of_the_viewport(x: float, y: float, region: str) -> None:
    assert region_of(x, y, 1280, 800) == region


def test_region_without_a_viewport_is_unknown() -> None:
    assert region_of(1, 1, 0, 0) == "position unknown"


def test_look_alikes_in_a_grid_get_rank_row_and_column() -> None:
    cards = [FakeElement(md=10 + i, role="button", name="Add to cart", bbox=box(100 + 400 * (i % 3), 200 + 300 * (i // 3))) for i in range(6)]
    found = describe_locations(Snap(seq=1, elements=tuple(cards)))

    assert found[10].endswith("#1 of 6 alike (row 1, col 1)")
    assert found[12].endswith("#3 of 6 alike (row 1, col 3)")
    assert found[14].endswith("#5 of 6 alike (row 2, col 2)")


def test_a_single_column_of_look_alikes_has_rank_only() -> None:
    rows = [FakeElement(md=i, role="button", name="Remove", bbox=box(900, 100 + 60 * i)) for i in range(1, 4)]
    found = describe_locations(Snap(seq=1, elements=tuple(rows)))

    assert found[2].endswith("#2 of 3 alike")
    assert "row" not in found[2]


def test_heading_above_frame_and_shadow_dom() -> None:
    elements = (
        FakeElement(md=1, role="textbox", name="Card number", bbox=box(200, 520), frame="payment"),
        FakeElement(md=2, role="button", name="Apply", bbox=box(200, 300)),
        FakeElement(md=3, role="heading", name="Coupon", bbox=box(200, 240)),
    )
    snapshot = Snap(seq=1, elements=elements, headings=(("Your cart", 50.0), ("Coupon", 240.0), ("Payment", 480.0)), paths={2: Path_(segments=("coupon-box", "button"))})
    found = describe_locations(snapshot)

    assert found[1] == 'bottom-left (x 250, y 540), under "Payment", in frame "payment"'
    assert found[2] == 'left (x 250, y 320), under "Coupon", in shadow DOM'
    assert "under" not in found[3], "a heading is not located under itself"


def test_elements_without_a_box_still_get_frame_context() -> None:
    found = describe_locations(Snap(seq=1, elements=(FakeElement(md=5, role="link", name="Help", frame="docs"),)))

    assert found == {5: 'in frame "docs"'}


def test_off_screen_elements_say_where_they_are() -> None:
    found = describe_locations(Snap(seq=1, elements=(FakeElement(md=1, role="button", name="Load more", bbox=box(600, 2400), in_viewport=False),)))

    assert found[1] == "below the visible area (x 650, y 2420)"


@dataclass(frozen=True)
class InDialog(FakeElement):
    dialog: str | None = None
    section: str = ""
    scroll: str = ""
    scroll_in_dialog: bool = False


def test_a_dialog_field_is_described_by_its_dialog_and_section_not_the_heading_drawn_above_it() -> None:
    field_ = InDialog(md=2, role="textbox", name="Record Name", bbox=box(400, 300), dialog="New Record", section="Details")
    row = InDialog(md=1, role="checkbox", name="Select Item 1", bbox=box(40, 500), section="")
    found = describe_locations(Snap(seq=1, elements=(row, field_), headings=(("Records", 10.0), ("Address", 450.0))))

    assert found[2] == 'center (x 450, y 320), in dialog "New Record", under "Details"'
    assert found[1] == "left (x 90, y 520)", "a perceived element without a section heading gets none from the screen"


def test_an_element_scrolled_out_of_its_scroll_area_says_where_it_is_instead_of_a_page_position() -> None:
    lower = InDialog(md=3, role="textbox", name="Description", bbox=box(400, 1500), dialog="New Record", section="More", scroll="below", scroll_in_dialog=True)
    upper = InDialog(md=4, role="textbox", name="Notes", bbox=box(400, -200), scroll="above")
    found = describe_locations(Snap(seq=1, elements=(lower, upper)))

    assert found[3] == 'in dialog "New Record", under "More", in the dialog\'s scroll area, below the visible part'
    assert found[4] == "in a scroll area, above the visible part"


def test_an_unnamed_dialog_is_still_named_a_dialog() -> None:
    found = describe_locations(Snap(seq=1, elements=(InDialog(md=5, role="button", name="OK", bbox=box(600, 400), dialog=""),)))
    assert found[5] == "center (x 650, y 420), in a dialog"
