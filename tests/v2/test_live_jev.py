"""Opt-in: the JEV backend against the real OpenRouter Decisions endpoint.

Skipped unless ``HERCULES_LIVE_JEV=1`` and the key variable (``OPENROUTER_API_KEY``,
or the variable named by ``HERCULES_JEV_KEY_ENV``) holds a key. Four calls in all.
Latency, model id and answers are printed; use ``-s`` to see them. The key itself is
only read by the backend and never printed.
"""

from __future__ import annotations

import asyncio
import os
import struct
import zlib
from collections.abc import Callable

import pytest
from testzeus_hercules.decide.backends.jev import JevBackend
from testzeus_hercules.decide.contract import BoxQuestion, ChoiceQuestion, Decision, DecisionRequest, ImagePart, NoulCriteria, NoulQuestion

KEY_ENV = os.environ.get("HERCULES_JEV_KEY_ENV", "OPENROUTER_API_KEY")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("HERCULES_LIVE_JEV") != "1" or not os.environ.get(KEY_ENV, "").strip(),
        reason=f"set HERCULES_LIVE_JEV=1 and {KEY_ENV} to call the real JEV endpoint",
    ),
]

Pixel = Callable[[int, int], tuple[int, int, int]]


def png(width: int, height: int, pixel: Pixel) -> bytes:
    """An RGB PNG built with zlib only; ``pixel(x, y)`` gives each pixel's colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def ask(request: DecisionRequest, label: str) -> Decision:
    async def run() -> Decision:
        engine = JevBackend(api_key_env=KEY_ENV, egress_policy="hybrid", timeout_s=30.0)
        try:
            return await engine.decide(request, purpose="live-test")
        finally:
            await engine.aclose()

    decision = asyncio.run(run())
    answers = {qid: answer.model_dump(exclude={"type"}) for qid, answer in decision.answers.items()}
    print(f"\n[live jev] {label}: latency_ms={decision.latency_ms} model={decision.model_version} cost_usd={decision.cost_usd} answers={answers}")
    return decision


def test_text_choice_picks_the_sign_in_button() -> None:
    rows = [
        {"md": "3", "role": "link", "name": "Forgot password?"},
        {"md": "7", "role": "textbox", "name": "Email"},
        {"md": "12", "role": "button", "name": "Sign in"},
    ]
    request = DecisionRequest(
        state={"step": "Click Sign in", "tree": rows},
        questions={
            "target": ChoiceQuestion(
                instructions="Which row of `tree` does `step` act on?",
                criteria={**{row["md"]: f"{row['role']} {row['name']!r}" for row in rows}, "none": "No row matches the step's target."},
            ),
            "target_absent": NoulQuestion(
                instructions="Is the element that `step` names missing from `tree`?",
                criteria=NoulCriteria(true="No row matches the step's target.", false="A matching row is present."),
            ),
        },
    )
    decision = ask(request, "text choice")
    target = decision.choice("target")
    assert target.choice == "12"
    assert target.confidence >= 0.5


@pytest.mark.parametrize(("colour", "rgb", "red_expected"), [("red", (220, 20, 20), True), ("blue", (20, 40, 220), False)])
def test_colour_flip_canary(colour: str, rgb: tuple[int, int, int], red_expected: bool) -> None:
    image = ImagePart.from_bytes(png(300, 300, lambda x, y: rgb), "image/png")
    request = DecisionRequest(state={"image": image}, questions={"mostly_red": NoulQuestion(instructions="Is `image` mostly red?")})
    noul = ask(request, f"canary {colour}").noul("mostly_red").noul
    assert noul > 0.8 if red_expected else noul < 0.2


def test_box_overlaps_the_red_square() -> None:
    left, top, right, bottom = 220, 60, 340, 180  # pixels in a 400x400 image

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        return (220, 20, 20) if left <= x < right and top <= y < bottom else (255, 255, 255)

    request = DecisionRequest(
        state={"image": ImagePart.from_bytes(png(400, 400, pixel), "image/png")},
        questions={"square": BoxQuestion(instructions="The red square in `image`")},
    )
    box = ask(request, "box").box("square").box
    truth = (top / 400, bottom / 400, left / 400, right / 400)
    overlap_h = max(0.0, min(box.bottom, truth[1]) - max(box.top, truth[0]))
    overlap_w = max(0.0, min(box.right, truth[3]) - max(box.left, truth[2]))
    intersection = overlap_h * overlap_w
    union = (box.bottom - box.top) * (box.right - box.left) + (truth[1] - truth[0]) * (truth[3] - truth[2]) - intersection
    print(f"[live jev] box truth top/bottom/left/right={truth} iou={intersection / union:.2f}")
    assert intersection > 0
