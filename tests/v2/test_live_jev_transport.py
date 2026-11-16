"""Opt-in canaries that pin how OpenRouter's Decisions endpoint treats our requests.

Skipped unless ``HERCULES_LIVE_JEV=1`` and the key variable holds a key (see
``test_live_jev``). About ten small calls in all (well under a tenth of a cent).

* **ZDR:** ``provider: {zdr: true, data_collection: deny}`` is accepted (HTTP 200).
* **Images:** an inline ``image_url`` part is seen; the TypeSafe ``assets`` map is
  ignored whatever the image size (the answer equals the answer without any image).
  Hercules therefore sends images inline on OpenRouter. If the ``assets`` canary
  starts failing, OpenRouter has begun reading assets: revisit the transport.
* **Long picks:** a pick over 32 KB of text is split into parts and still answered.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import random
from typing import Any

import httpx
import pytest

from testzeus_hercules.decide.backends.jev import OPENROUTER_URL, ZDR_PROVIDER, JevBackend
from testzeus_hercules.decide.contract import DecisionRequest, NoulQuestion
from testzeus_hercules.decide.questions import MAX_TEXT_BYTES, TargetSpec, build_step_request

from tests.v2.engine_fixtures.fakes import FakeBBox, FakeElement, FakeSnapshot

KEY_ENV = os.environ.get("HERCULES_JEV_KEY_ENV", "OPENROUTER_API_KEY")
MODEL = "typesafe/jev-1.14"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("HERCULES_LIVE_JEV") != "1" or not os.environ.get(KEY_ENV, "").strip(),
        reason=f"set HERCULES_LIVE_JEV=1 and {KEY_ENV} to call the real JEV endpoint",
    ),
]

QUESTIONS = {
    "red": {"type": "noul", "instructions": "Is the image's background mostly red?"},
    "word": {
        "type": "choice",
        "instructions": "Which word is written in the image?",
        "criteria": {"CAT": "The image shows the word CAT", "DOG": "The image shows the word DOG", "SUN": "The image shows the word SUN", "none": "No image is visible or no word can be read"},
    },
}


def image(size: int, colour: str, word: str, *, noisy: bool = False) -> bytes:
    from PIL import Image, ImageDraw

    picture = Image.new("RGB", (size, size), colour)
    if noisy:  # speckles keep the PNG large (~150 KB at 400 px)
        rng = random.Random(1)
        pixels = picture.load()
        for _ in range(size * size // 2):
            x, y = rng.randrange(size), rng.randrange(size)
            r, g, b = pixels[x, y]
            pixels[x, y] = (min(255, r + rng.randrange(40)), min(255, g + rng.randrange(40)), min(255, b + rng.randrange(40)))
    ImageDraw.Draw(picture).text((size * 0.1, size * 0.4), word, fill="white", font_size=max(10, size // 4))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return buffer.getvalue()


def post(body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    key = os.environ[KEY_ENV].strip()
    response = httpx.post(OPENROUTER_URL, json={"model": MODEL, "provider": dict(ZDR_PROVIDER), **body}, headers={"Authorization": f"Bearer {key}", **(headers or {})}, timeout=60)
    assert response.status_code == 200, f"HTTP {response.status_code}"
    payload = response.json()
    answers = payload["answers"]
    result = {"red": answers["red"]["noul"], "word": answers["word"]["choice"], "input_tokens": payload.get("usage", {}).get("input_tokens")}
    print(f"\n[live jev transport] {result}")
    return result


def inline(png: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def with_assets(png: bytes) -> dict[str, Any]:
    return {"state": {"image": "img1"}, "questions": QUESTIONS, "assets": {"img1": {"type": "image", "format": "png", "data_base64": base64.b64encode(png).decode()}}}


def test_zdr_provider_preferences_are_accepted() -> None:
    async def run() -> Any:
        engine = JevBackend(api_key_env=KEY_ENV, egress_policy="hybrid-text", timeout_s=30.0)
        try:
            return await engine.decide(DecisionRequest(state={"text": "The sky is blue."}, questions={"q": NoulQuestion(instructions="Does the text say the sky is blue?")}), purpose="live-zdr")
        finally:
            await engine.aclose()

    decision = asyncio.run(run())

    assert decision.noul("q").noul > 0.8


@pytest.mark.parametrize(("colour", "word", "red"), [("red", "DOG", True), ("blue", "SUN", False)])
def test_inline_image_parts_are_seen(colour: str, word: str, red: bool) -> None:
    result = post({"state": {"image": inline(image(320, colour, word))}, "questions": QUESTIONS})

    assert (result["red"] > 0.8) is red
    assert result["word"] == word


def test_the_assets_map_is_ignored_on_openrouter_whatever_the_image_size() -> None:
    blind = post({"state": {"image": "img1"}, "questions": QUESTIONS})
    for size, noisy in ((64, False), (320, False), (400, True)):
        seen = post(with_assets(image(size, "red", "DOG", noisy=noisy)), {"TypeSafe-Danger-Zone-Images": "true"})
        assert seen["word"] == "none" and seen["red"] < 0.5, f"assets map read at {size} px: switch images to the assets transport?"
        assert seen["input_tokens"] == blind["input_tokens"], "the image added no input tokens: it was dropped"


def test_a_pick_over_32_kb_is_split_and_answered() -> None:
    filler = "a long descriptive product title that pads the row " * 4
    elements = tuple(FakeElement(md=md, role="link", name=("Order history " if md == 140 else "") + f"{filler}{md}", bbox=FakeBBox(x=20, y=30 * md)) for md in range(1, 161))
    request = build_step_request(TargetSpec(kind="click", target="Order history"), FakeSnapshot(seq=1, elements=elements)).request
    assert request.text_size_bytes() > MAX_TEXT_BYTES

    async def run() -> Any:
        engine = JevBackend(api_key_env=KEY_ENV, egress_policy="hybrid-text", timeout_s=30.0)
        try:
            return await engine.decide(request, purpose="live-long")
        finally:
            await engine.aclose()

    decision = asyncio.run(run())
    print(f"\n[live jev transport] long pick: {decision.choice('target').choice} conf={decision.choice('target').confidence} cost={decision.cost_usd} latency_ms={decision.latency_ms}")

    assert decision.choice("target").choice == "md_140"
