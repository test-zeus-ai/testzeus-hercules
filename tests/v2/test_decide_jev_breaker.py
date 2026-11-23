"""The JEV circuit breaker never stays half-open when its trial call is cancelled or fails unexpectedly."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from testzeus_hercules.decide.backends.jev import JevBackend
from testzeus_hercules.decide.errors import DecisionUnavailable

from tests.v2.test_decide_jev import FAKE_SECRET, KEY_ENV, FakeClock, error, text_ok, text_request


class Switch:
    """A transport handler whose behaviour the test changes between calls."""

    def __init__(self) -> None:
        self.mode = "down"
        self.requests = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.mode == "down":
            return error(503, "down")
        if self.mode == "hang":
            await asyncio.Event().wait()
        if self.mode == "boom":
            raise RuntimeError("unexpected transport bug")
        return text_ok()


async def _no_sleep(_seconds: float) -> None:
    return None


def _open_breaker() -> tuple[JevBackend, Switch, FakeClock]:
    clock, switch = FakeClock(), Switch()
    engine = JevBackend(
        api_key_env=KEY_ENV, egress_policy="hybrid", env={KEY_ENV: FAKE_SECRET}, http=httpx.AsyncClient(transport=httpx.MockTransport(switch)), clock=clock, sleep=_no_sleep, max_retries=0
    )
    for _ in range(3):
        with pytest.raises(DecisionUnavailable):
            asyncio.run(engine.decide(text_request(), purpose="target"))
    clock.now += 301
    return engine, switch, clock


def test_a_cancelled_trial_lets_the_next_call_be_the_trial() -> None:
    engine, switch, _clock = _open_breaker()
    switch.mode = "hang"

    async def cancelled_trial() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(engine.decide(text_request(), purpose="target"), timeout=0.05)

    asyncio.run(cancelled_trial())
    switch.mode = "ok"

    assert asyncio.run(engine.decide(text_request(), purpose="target")).choice("target").choice == "12"


def test_an_unexpected_trial_error_counts_as_a_failure_and_reopens_the_breaker() -> None:
    engine, switch, clock = _open_breaker()
    switch.mode = "boom"

    with pytest.raises(RuntimeError):
        asyncio.run(engine.decide(text_request(), purpose="target"))
    with pytest.raises(DecisionUnavailable, match="retrying in"):
        asyncio.run(engine.decide(text_request(), purpose="target"))

    clock.now += 301
    switch.mode = "ok"
    assert asyncio.run(engine.decide(text_request(), purpose="target")).choice("target").choice == "12"
