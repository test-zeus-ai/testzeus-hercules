"""Offline tests for the telemetry shutdown signal handler.

``_handle_shutdown_signal`` must never raise (it runs from signal handlers),
must keep a strong reference to any task it schedules, and must fall back to
``asyncio.run`` when no loop is running in this thread. ``send_message_to_sentry``
is monkeypatched, so nothing ever hits the network.
"""

import asyncio

import testzeus_hercules.telemetry as telemetry


def test_shutdown_signal_without_running_loop_sends_message(monkeypatch) -> None:
    sent: list[bool] = []

    async def fake_send() -> None:
        sent.append(True)

    monkeypatch.setattr(telemetry, "send_message_to_sentry", fake_send)

    telemetry._handle_shutdown_signal()  # no running loop: asyncio.run fallback

    assert sent == [True]


def test_shutdown_signal_with_running_loop_schedules_tracked_task(monkeypatch) -> None:
    sent: list[bool] = []

    async def fake_send() -> None:
        await asyncio.sleep(0)
        sent.append(True)

    monkeypatch.setattr(telemetry, "send_message_to_sentry", fake_send)

    async def _drive() -> None:
        telemetry._handle_shutdown_signal()
        assert len(telemetry._pending_shutdown_tasks) == 1  # strong reference held while in flight
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
        assert sent == [True]

    asyncio.run(_drive())
    assert telemetry._pending_shutdown_tasks == set()  # done-callback discarded the finished task


def test_shutdown_signal_never_raises_when_loop_is_unusable(monkeypatch, capsys) -> None:
    async def fake_send() -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr(telemetry, "send_message_to_sentry", fake_send)

    def _raise_runtime_error(coro):
        coro.close()
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    monkeypatch.setattr(telemetry.asyncio, "run", _raise_runtime_error)

    telemetry._handle_shutdown_signal()  # must not raise out of a signal handler

    assert "Could not send telemetry on shutdown" in capsys.readouterr().out
