"""The Clef backend: a JEV-compatible client for a local Clef server (no key, never leaves the machine)."""

from __future__ import annotations

import asyncio

import pytest

from testzeus_hercules.decide.backends.clef import CLEF_ENGINE, DEFAULT_CLEF_MODEL, clef_endpoint, make_clef, probe_clef
from testzeus_hercules.decide.questions import TargetSpec, build_step_request

from tests.v2.clef_fakes import FakeClef
from tests.v2.engine_fixtures.fakes import FakeSnapshot, login_elements


@pytest.mark.parametrize(
    ("given", "url"),
    [
        ("http://127.0.0.1:30000", "http://127.0.0.1:30000/v1/systemone"),
        ("http://127.0.0.1:30000/", "http://127.0.0.1:30000/v1/systemone"),
        ("http://localhost:30000/v1", "http://localhost:30000/v1/systemone"),
    ],
)
def test_clef_endpoint_is_the_systemone_path_of_a_local_server(given: str, url: str) -> None:
    assert clef_endpoint(given) == url


@pytest.mark.parametrize("url", ["https://clef.example.com", "ftp://127.0.0.1", "127.0.0.1:30000", "http://10.0.0.5:30000", "http://clef-box.local:30000"])
def test_a_remote_or_malformed_clef_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        clef_endpoint(url)


def test_a_clef_server_on_another_machine_needs_listing_in_local_hosts() -> None:
    assert clef_endpoint("http://10.0.0.5:30000/v1/systemone", ("10.0.0.5",)) == "http://10.0.0.5:30000/v1/systemone"
    assert make_clef("http://10.0.0.5:30000", local_hosts=("10.0.0.5",)).url == "http://10.0.0.5:30000/v1/systemone"


def test_clef_answers_a_step_pick_without_any_key_even_under_local_egress() -> None:
    server = FakeClef()
    clef = make_clef("http://127.0.0.1:30000", http=server.client())
    request = build_step_request(TargetSpec(kind="click", target="Sign in"), FakeSnapshot(seq=1, elements=login_elements())).request

    decision = asyncio.run(clef.decide(request, purpose="target"))

    assert decision.engine == CLEF_ENGINE == "clef"
    assert decision.choice("target").choice == "md_6"
    (sent,) = server.requests
    assert str(sent.url) == "http://127.0.0.1:30000/v1/systemone"
    assert "authorization" not in sent.headers
    body = server.bodies()[0]
    assert body["model"] == DEFAULT_CLEF_MODEL
    assert "provider" not in body, "no OpenRouter provider preferences go to a local server"
    assert set(body) == {"model", "state", "questions"}


def test_probe_reports_a_working_server() -> None:
    probe = asyncio.run(probe_clef("http://127.0.0.1:30000", http=FakeClef().client()))

    assert probe.ok and probe.model == DEFAULT_CLEF_MODEL
    assert "answered a test decision" in probe.detail


def test_probe_reports_an_unreachable_server_without_raising() -> None:
    server = FakeClef(reachable=False)
    probe = asyncio.run(probe_clef("http://127.0.0.1:30000", http=server.client()))

    assert not probe.ok
    assert "Clef unavailable" in probe.detail and "ConnectError" in probe.detail
    assert len(server.requests) == 1, "the probe does not retry"


def test_probe_refuses_a_remote_url_without_contacting_it() -> None:
    server = FakeClef()
    probe = asyncio.run(probe_clef("https://clef.example.com", http=server.client()))

    assert not probe.ok and "not on this machine" in probe.detail
    assert server.requests == []
