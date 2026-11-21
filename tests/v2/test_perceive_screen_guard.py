"""Secret values never appear in screenshots: fields, text and frames that show one are covered before the image leaves.

The pages paint every place a secret can show in a loud colour (magenta); a covered region has none of it left.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401
from testzeus_hercules.perceive import take_snapshot
from testzeus_hercules.perceive.screen_guard import COVER, capture, guard_secrets

MAGENTA = (255, 0, 255)
SECRET = "PANTRY10"

FIELD = (
    "<style>body{margin:0;font:28px sans-serif} input,p,span{background:#f0f;color:#f0f;display:block;margin:20px;width:300px;height:40px}</style>"
    "<input id=coupon aria-label=Coupon><input id=name aria-label=Name value=Ada><span id=plain>nothing here</span>"
)


def _magenta(jpeg: bytes, box: tuple[float, float, float, float]) -> int:
    """How many pixels inside ``box`` (CSS px) are close to magenta."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    x, y, w, h = (int(v) for v in box)
    crop = image.crop((x + 4, y + 4, x + w - 4, y + h - 4))
    return sum(1 for r, g, b in crop.get_flattened_data() if r > 200 and g < 80 and b > 200)


async def _box(page: Any, selector: str) -> tuple[float, float, float, float]:
    box = await page.locator(selector).bounding_box()
    assert box is not None
    return box["x"], box["y"], box["width"], box["height"]


def test_a_typed_secret_is_covered_in_the_snapshot_images(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[int, int, int, int]:
        async with browser.page() as page:
            await page.set_content(FIELD)
            await page.fill("#coupon", SECRET)
            guard_secrets(page, lambda: (SECRET,))
            snap = await take_snapshot(page, seq=1)
            coupon, name = await _box(page, "#coupon"), await _box(page, "#name")
            # plain.jpg is downscaled only past 1280 px wide, so CSS px and image px match here.
            return _magenta(snap.plain_jpeg, coupon), _magenta(snap.som_jpeg, coupon), _magenta(snap.plain_jpeg, name), len(snap.plain_jpeg)

    secret_plain, secret_som, other, size = browser.run(scenario())
    assert size > 0
    assert secret_plain == 0 and secret_som == 0, "the field holding the secret must be covered"
    assert other > 100, "fields without a secret stay as they are"


def test_without_the_guard_the_secret_field_is_visible(browser: BrowserHarness) -> None:
    async def scenario() -> int:
        async with browser.page() as page:
            await page.set_content(FIELD)
            await page.fill("#coupon", SECRET)
            snap = await take_snapshot(page, seq=1)
            return _magenta(snap.plain_jpeg, await _box(page, "#coupon"))

    assert browser.run(scenario()) > 100


def test_secret_text_on_the_page_is_covered(browser: BrowserHarness) -> None:
    page_html = FIELD.replace("<span id=plain>nothing here</span>", f"<span id=plain style='background:#fff;width:auto;height:auto;display:inline'>Code {SECRET} applied</span>")

    async def scenario() -> tuple[int, int]:
        async with browser.page() as page:
            await page.set_content(page_html)
            guard_secrets(page, lambda: (SECRET,))
            jpeg = await capture(page, type="jpeg", quality=80)
            return _magenta(jpeg, await _box(page, "#plain")), _magenta(jpeg, await _box(page, "#name"))

    text, other = browser.run(scenario())
    assert text < 50, "the text node holding the secret is covered"
    assert other > 100


def test_a_secret_inside_a_frame_is_covered(browser: BrowserHarness) -> None:
    inner = "<style>body{margin:0}input{background:#f0f;color:#f0f;width:300px;height:40px;margin:10px}</style><input id=inner>"
    outer = f"<style>body{{margin:0}}iframe{{margin:30px;width:400px;height:120px;border:0}}</style><iframe srcdoc='{inner}'></iframe>"

    async def scenario() -> int:
        async with browser.page() as page:
            await page.set_content(outer)
            frame = page.frame_locator("iframe")
            await frame.locator("#inner").fill(SECRET)
            guard_secrets(page, lambda: (SECRET,))
            jpeg = await capture(page, type="jpeg", quality=80)
            box = await frame.locator("#inner").bounding_box()
            assert box is not None
            return _magenta(jpeg, (box["x"], box["y"], box["width"], box["height"]))

    assert browser.run(scenario()) == 0


def test_a_short_secret_inside_a_longer_value_is_left_alone(browser: BrowserHarness) -> None:
    async def scenario() -> int:
        async with browser.page() as page:
            await page.set_content(FIELD)
            await page.fill("#coupon", "order 1230")
            guard_secrets(page, lambda: ("123",))
            jpeg = await capture(page, type="jpeg", quality=80)
            return _magenta(jpeg, await _box(page, "#coupon"))

    assert browser.run(scenario()) > 100


def test_a_secret_like_field_is_covered_without_a_declared_secret(browser: BrowserHarness) -> None:
    html = FIELD.replace("aria-label=Coupon", "aria-label='One-time code' autocomplete=one-time-code")

    async def scenario() -> int:
        async with browser.page() as page:
            await page.set_content(html)
            await page.fill("#coupon", "482913")
            guard_secrets(page, lambda: ())
            jpeg = await capture(page, type="jpeg", quality=80)
            return _magenta(jpeg, await _box(page, "#coupon"))

    assert browser.run(scenario()) == 0


def test_the_guard_covers_popups_of_the_same_context(browser: BrowserHarness) -> None:
    async def scenario() -> int:
        async with browser.page() as page:
            guard_secrets(page, lambda: (SECRET,))
            popup = await page.context.new_page()
            await popup.set_content(FIELD)
            await popup.fill("#coupon", SECRET)
            jpeg = await capture(popup, type="jpeg", quality=80)
            return _magenta(jpeg, await _box(popup, "#coupon"))

    assert browser.run(scenario()) == 0


def test_cover_colour_is_not_magenta() -> None:
    assert COVER != MAGENTA
