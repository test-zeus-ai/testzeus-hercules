"""Off-page Set-of-Mark painter: legibility budget on synthetic layouts."""

from __future__ import annotations

import io
from itertools import combinations

from PIL import Image
from testzeus_hercules.perceive import BBox
from testzeus_hercules.perceive.painter import COVERAGE_LIMIT, PALETTE, Mark, PaintResult, colour_for, paint_marks


def _plain(width: int = 1280, height: int = 800) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _assert_no_badge_overlap(result: PaintResult) -> None:
    for (ma, a), (mb, b) in combinations(result.badges.items(), 2):
        assert not _overlap(a, b), f"badges {ma} and {mb} overlap: {a} {b}"


def _form_marks() -> list[Mark]:
    marks = [Mark(md=i + 1, bbox=BBox(100, 60 + i * 50, 260, 30), tier=0) for i in range(8)]
    marks += [Mark(md=20 + i, bbox=BBox(420 + i * 90, 60, 80, 30), tier=1) for i in range(5)]
    return marks


def _dense_grid(count: int = 60) -> list[Mark]:
    return [Mark(md=i + 1, bbox=BBox(8 + (i % 6) * 65, 60 + (i // 6) * 19, 64, 18), tier=1) for i in range(count)]


def test_output_is_jpeg_at_most_1024_wide_with_aspect_kept() -> None:
    result = paint_marks(_plain(1280, 800), _form_marks(), viewport=(1280, 800))
    image = Image.open(io.BytesIO(result.jpeg))
    assert image.format == "JPEG"
    assert image.size == (1024, 640)
    assert result.size == (1024, 640)


def test_small_screenshots_are_not_upscaled() -> None:
    result = paint_marks(_plain(800, 600), _form_marks()[:3], viewport=(800, 600))
    assert result.size == (800, 600)


def test_every_mark_painted_on_a_sparse_form() -> None:
    marks = _form_marks()
    result = paint_marks(_plain(), marks, viewport=(1280, 800))
    assert set(result.painted) == {m.md for m in marks}
    assert not result.dropped
    _assert_no_badge_overlap(result)
    assert 0 < result.coverage <= COVERAGE_LIMIT


def test_badges_stay_inside_the_image() -> None:
    marks = [Mark(md=1, bbox=BBox(0, 0, 40, 20), tier=1), Mark(md=2, bbox=BBox(1240, 780, 40, 20), tier=1)]
    result = paint_marks(_plain(), marks, viewport=(1280, 800))
    w, h = result.size
    for x0, y0, x1, y1 in result.badges.values():
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h


def test_dense_grid_stays_legible() -> None:
    sparse = paint_marks(_plain(), _form_marks(), viewport=(1280, 800))
    dense = paint_marks(_plain(), _dense_grid(), viewport=(1280, 800))
    _assert_no_badge_overlap(dense)
    assert dense.font_size < sparse.font_size
    assert dense.dropped, "a 60-link grid cannot carry 60 legible badges"
    assert set(dense.painted) | set(dense.dropped) == set(range(1, 61))
    assert len(dense.painted) >= 15
    assert dense.coverage <= COVERAGE_LIMIT


def test_fields_are_always_painted_even_when_crowded() -> None:
    marks = _dense_grid()
    marks += [Mark(md=100 + i, bbox=BBox(8 + i * 65, 80, 64, 18), tier=0) for i in range(4)]  # fields inside the crowd
    result = paint_marks(_plain(), marks, viewport=(1280, 800))
    assert {100, 101, 102, 103} <= set(result.painted)
    _assert_no_badge_overlap(result)


def test_focus_marks_are_always_painted() -> None:
    marks = _dense_grid()
    marks = [Mark(md=m.md, bbox=m.bbox, tier=m.tier, must=m.md in (33, 34)) for m in marks]
    result = paint_marks(_plain(), marks, viewport=(1280, 800))
    assert {33, 34} <= set(result.painted)


def test_coverage_budget_drops_optional_marks() -> None:
    # Large boxes: outlines are cheap but a huge number of badges is not.
    marks = [Mark(md=i + 1, bbox=BBox((i % 40) * 32, (i // 40) * 20, 30, 18), tier=3) for i in range(1600)]
    result = paint_marks(_plain(), marks, viewport=(1280, 800))
    assert result.coverage <= COVERAGE_LIMIT
    _assert_no_badge_overlap(result)


def test_colours_are_deterministic_by_md() -> None:
    assert colour_for(5) == colour_for(5) == PALETTE[5 % len(PALETTE)]
    first = paint_marks(_plain(), _form_marks(), viewport=(1280, 800))
    second = paint_marks(_plain(), _form_marks(), viewport=(1280, 800))
    assert first.badges == second.badges and first.painted == second.painted


def test_marks_outside_the_image_are_ignored() -> None:
    marks = [Mark(md=1, bbox=BBox(10, 2000, 50, 20), tier=0), Mark(md=2, bbox=BBox(10, 10, 50, 20), tier=1)]
    result = paint_marks(_plain(), marks, viewport=(1280, 800))
    assert result.painted == (2,)
    assert result.dropped == {1: "offscreen"}


def test_badges_do_not_cover_other_painted_boxes() -> None:
    result = paint_marks(_plain(), _form_marks(), viewport=(1280, 800))
    scale = result.size[0] / 1280
    boxes = {m.md: (m.bbox.x * scale, m.bbox.y * scale, (m.bbox.x + m.bbox.width) * scale, (m.bbox.y + m.bbox.height) * scale) for m in _form_marks()}
    for md, badge in result.badges.items():
        for other, box in boxes.items():
            if other != md:
                assert not _overlap(badge, tuple(int(v) for v in box)), (md, other)  # type: ignore[arg-type]
