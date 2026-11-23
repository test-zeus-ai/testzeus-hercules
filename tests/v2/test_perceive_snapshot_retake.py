"""A snapshot whose page read had to wait and retry takes its screenshot again, so marks match what is shown."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from PIL import Image

from testzeus_hercules.perceive import snapshot as snap


def _jpeg(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakePage:
    url = "http://127.0.0.1:9/app"

    def __init__(self) -> None:
        self.shots: list[bytes] = [_jpeg((255, 0, 0)), _jpeg((0, 0, 255))]
        self.taken = 0

    async def screenshot(self, **_: Any) -> bytes:
        shot = self.shots[min(self.taken, len(self.shots) - 1)]
        self.taken += 1
        return shot

    async def title(self) -> str:
        return "App"


def _blue(jpeg: bytes) -> bool:
    red, _green, blue = Image.open(io.BytesIO(jpeg)).convert("RGB").getpixel((5, 5))
    return blue > 200 and red < 60


@pytest.mark.parametrize("retried", [False, True])
def test_the_screenshot_is_retaken_only_after_a_retried_read(monkeypatch: pytest.MonkeyPatch, retried: bool) -> None:
    async def rendered(_page: Any) -> None:
        return None

    async def collect(_page: Any) -> Any:
        await asyncio.sleep(0)
        return snap._Collected(viewport=(64, 48), retried=retried)

    monkeypatch.setattr(snap, "wait_until_rendered", rendered)
    monkeypatch.setattr(snap, "_collect", collect)
    page = FakePage()

    taken = asyncio.run(snap.take_snapshot(page, seq=1))  # type: ignore[arg-type]

    assert page.taken == (2 if retried else 1)
    assert _blue(taken.plain_jpeg) is retried
