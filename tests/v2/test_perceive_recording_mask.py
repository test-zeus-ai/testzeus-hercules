"""Browser recordings hide secret values: a cover layer in the page, drawn before each frame, over the fields that hold one.

A recording is written by the browser, so these tests look at what the page renders (``page.screenshot`` with no
covering of its own): that is what each recorded frame shows. Secret fields are painted magenta; a covered field has
none of it left. The page itself never receives a secret value.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401
from testzeus_hercules.act.browser import fill
from testzeus_hercules.perceive.screen_guard import capture, guard_secrets, holds_secret, mask_recording, recording_exposed

SECRET = "PANTRY10"
SITE = "http://hercules.test/"
FORM = (
    "<style>body{margin:0;font:28px sans-serif} input,span{background:#f0f;color:#f0f;display:block;margin:20px;width:300px;height:40px}</style>"
    "<input id=coupon aria-label=Coupon><input id=name aria-label=Name><span id=echo></span>"
)


def _magenta(png: bytes, box: tuple[float, float, float, float]) -> int:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    x, y, w, h = (int(v) for v in box)
    crop = image.crop((x + 4, y + 4, x + w - 4, y + h - 4))
    return sum(1 for r, g, b in crop.get_flattened_data() if r > 200 and g < 80 and b > 200)


async def _box(page: Any, selector: str) -> tuple[float, float, float, float]:
    box = await page.locator(selector).bounding_box()
    assert box is not None
    return box["x"], box["y"], box["width"], box["height"]


async def _recording_page(harness: BrowserHarness, html: str, video_dir: Path | None = None) -> tuple[Any, Any]:
    options: dict[str, Any] = {"viewport": {"width": 1280, "height": 800}}
    if video_dir is not None:
        options["record_video_dir"] = str(video_dir)
    context = await harness.browser.new_context(**options)  # type: ignore[attr-defined]
    await context.route(f"{SITE}**", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page = await context.new_page()
    guard_secrets(page, lambda: (SECRET,))
    await mask_recording(page)
    await page.goto(SITE)
    return context, page


def test_a_field_hercules_types_a_secret_into_is_covered_in_what_the_page_renders(browser: BrowserHarness) -> None:
    async def scenario() -> tuple[int, int, bool]:
        context, page = await _recording_page(browser, FORM)
        try:
            result = await fill(page.locator("#coupon"), SECRET)
            await fill(page.locator("#name"), "Ada")
            assert result.ok, result.detail
            await page.wait_for_timeout(100)
            frame = await page.screenshot()  # no covering of its own: what a recorded frame shows
            covered, other = _magenta(frame, await _box(page, "#coupon")), _magenta(frame, await _box(page, "#name"))
            await capture(page, type="jpeg", quality=80)  # the screenshot check finds only covered fields
            return covered, other, recording_exposed(page)
        finally:
            await context.close()

    covered, other, exposed = browser.run(scenario())
    assert covered == 0, "the field holding the secret is under the cover layer"
    assert other > 100, "a field without a secret stays visible"
    assert exposed is False


def test_a_secret_like_field_is_covered_once_it_has_a_value(browser: BrowserHarness) -> None:
    html = FORM.replace("aria-label=Coupon", "aria-label='One-time code' autocomplete=one-time-code")

    async def scenario() -> tuple[int, int]:
        context, page = await _recording_page(browser, html)
        try:
            await page.wait_for_timeout(100)
            empty = _magenta(await page.screenshot(), await _box(page, "#coupon"))
            await page.locator("#coupon").fill("482913")
            await page.wait_for_timeout(100)
            return empty, _magenta(await page.screenshot(), await _box(page, "#coupon"))
        finally:
            await context.close()

    empty, filled = browser.run(scenario())
    assert empty > 100, "an empty field is not covered"
    assert filled == 0


def test_a_secret_the_page_prints_outside_a_covered_field_marks_the_recording_exposed(browser: BrowserHarness) -> None:
    async def scenario() -> bool:
        context, page = await _recording_page(browser, FORM)
        try:
            await fill(page.locator("#coupon"), SECRET)
            await page.evaluate(f"document.getElementById('echo').textContent = 'Coupon {SECRET} applied'")
            await capture(page, type="jpeg", quality=80)
            return recording_exposed(page)
        finally:
            await context.close()

    assert browser.run(scenario()) is True


def test_the_cover_layer_does_not_take_clicks(browser: BrowserHarness) -> None:
    html = FORM + "<script>document.getElementById('coupon').addEventListener('click', () => document.title = 'clicked')</script>"

    async def scenario() -> str:
        context, page = await _recording_page(browser, html)
        try:
            await fill(page.locator("#coupon"), SECRET)
            await page.wait_for_timeout(100)
            await page.locator("#coupon").click(timeout=2000)  # Playwright checks the field receives the click
            return await page.title()
        finally:
            await context.close()

    assert browser.run(scenario()) == "clicked"


def test_the_page_never_receives_a_secret_value(browser: BrowserHarness) -> None:
    spy = (
        "<script>window.seen = [];"
        "for (const name of ['includes', 'indexOf', 'startsWith']) { const real = String.prototype[name];"
        " String.prototype[name] = function (...args) { window.seen.push(String(args[0])); return real.apply(this, args); }; }"
        "const realTest = RegExp.prototype.test; RegExp.prototype.test = function (s) { window.seen.push(this.source); return realTest.call(this, s); };"
        "</script>"
    )

    async def scenario() -> list[str]:
        context, page = await _recording_page(browser, FORM + spy)
        try:
            await fill(page.locator("#coupon"), SECRET)
            await capture(page, type="jpeg", quality=80)
            return await page.evaluate("window.seen")
        finally:
            await context.close()

    seen = browser.run(scenario())
    assert not any(SECRET in item for item in seen)


def test_no_frame_of_the_recorded_video_shows_the_typed_secret(browser: BrowserHarness, tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is needed to read the recorded frames")

    async def scenario() -> tuple[list[Path], tuple[float, float, float, float]]:
        context, page = await _recording_page(browser, FORM, video_dir=tmp_path)
        await page.wait_for_timeout(300)
        await page.locator("#coupon").press_sequentially(SECRET[:3])  # a partial value, typed by hand: not a secret yet
        await page.locator("#coupon").fill("")
        await fill(page.locator("#coupon"), SECRET)
        await page.wait_for_timeout(500)
        box = await _box(page, "#coupon")
        await context.close()
        return sorted(tmp_path.glob("*.webm")), box

    videos, (x, y, w, h) = browser.run(scenario())
    assert videos
    frames = tmp_path / "frames"
    frames.mkdir()
    subprocess.run([ffmpeg, "-loglevel", "error", "-i", str(videos[0]), "-vf", "fps=25", str(frames / "f%04d.png")], check=True)
    shots = sorted(frames.glob("*.png"))
    assert shots
    width = Image.open(shots[0]).width
    scale = width / 1280  # Playwright scales the 1280x800 viewport down to fit 800x800
    # The coupon field holds the whole secret only after the last fill; from then on every frame must be covered.
    last = [_magenta(frame.read_bytes(), (x * scale, y * scale, w * scale, h * scale)) for frame in shots[-8:]]
    assert max(last) == 0, f"magenta pixels in the last frames: {last}"


def test_short_secrets_count_only_where_they_stand_alone() -> None:
    assert holds_secret("pin 1234 set", ("1234",))
    assert not holds_secret("order 12345", ("1234",))
    assert holds_secret("token=abcdefgh12", ("abcdefgh",))
    assert not holds_secret("", ("abcdefgh",))


# --- the run keeps a recording only when no secret showed outside the cover ---------------------------------------


class _Context:
    """A browser context stand-in that holds the guard's state."""


class _Page:
    def __init__(self) -> None:
        self.context = _Context()


def _run(tmp_path: Path, page: Any, *, mask_failed: bool = False, secrets: tuple[str, ...] = (SECRET,)) -> Any:
    from types import SimpleNamespace

    from testzeus_hercules.engine.run_graph import _Run

    run = object.__new__(_Run)
    run.evidence = SimpleNamespace(run_dir=tmp_path)
    run.page = page
    run.mask_failed = mask_failed
    run.data = SimpleNamespace(secret_values=lambda: secrets)
    (tmp_path / "video").mkdir(parents=True, exist_ok=True)
    (tmp_path / "video" / "page@1.webm").write_bytes(b"webm")
    return run


def test_a_run_drops_its_recording_when_a_secret_showed_outside_the_cover(tmp_path: Path) -> None:
    from testzeus_hercules.perceive import screen_guard

    page = _Page()
    run = _run(tmp_path, page)
    assert run._drop_exposed_recording() is None, "nothing exposed: the recording is kept"
    assert (tmp_path / "video" / "page@1.webm").exists()
    screen_guard._expose(page)
    note = run._drop_exposed_recording()
    assert note is not None and "recording not kept" in note
    assert not (tmp_path / "video" / "page@1.webm").exists(), "the evidence then lists no video (it reads the folder)"


def test_a_run_without_its_cover_layer_drops_the_recording_only_when_secrets_are_set(tmp_path: Path) -> None:
    assert _run(tmp_path / "a", _Page(), mask_failed=True, secrets=())._drop_exposed_recording() is None
    assert _run(tmp_path / "b", _Page(), mask_failed=True)._drop_exposed_recording() is not None
