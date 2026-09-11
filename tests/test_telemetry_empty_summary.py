"""Tests for the telemetry empty-summary skip.

Processes that merely import the stack never record events; the shutdown hook
must not send an empty "Program execution summary" (and pay the Sentry flush)
for them. Sentry is monkeypatched — nothing ever hits the network.
"""

import asyncio

import testzeus_hercules.telemetry as telemetry


def _capture_sentry(monkeypatch) -> list:
    """Replace sentry_sdk.push_scope/capture_message with recording fakes."""
    sent: list = []

    class _FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def set_extra(self, key, value):
            sent.append(("extra", key))

    monkeypatch.setattr(telemetry.sentry_sdk, "push_scope", lambda: _FakeScope())
    monkeypatch.setattr(telemetry.sentry_sdk, "capture_message", lambda message: sent.append(("message", message)))
    return sent


def test_empty_summary_is_not_sent(monkeypatch) -> None:
    # Simulate a process that never recorded any event (import-only process).
    # setitem restores the shared collector afterwards, whatever earlier tests
    # in the same session put into it.
    monkeypatch.setitem(telemetry.event_collector, "buckets", {})
    sent = _capture_sentry(monkeypatch)

    asyncio.run(telemetry.send_message_to_sentry())

    assert sent == []  # skipped: no Sentry event, no flush cost


def test_summary_is_sent_once_events_were_recorded(monkeypatch) -> None:
    sent = _capture_sentry(monkeypatch)
    monkeypatch.setitem(telemetry.event_collector, "buckets", {"run": {"events": [], "event_count": 1}})

    asyncio.run(telemetry.send_message_to_sentry())

    assert ("message", "Program execution summary") in sent
