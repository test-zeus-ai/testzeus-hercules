"""Local model and Clef traffic never goes through an environment proxy (HTTP_PROXY / ALL_PROXY); hosted traffic may."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from testzeus_hercules.cli import doctor, setup
from testzeus_hercules.decide.backends.clef import make_clef
from testzeus_hercules.decide.backends.jev import JevBackend
from testzeus_hercules.kernel.settings import LLMSettings
from testzeus_hercules.llm.client import ChatClient
from testzeus_hercules.llm.providers import http_client, resolve_endpoint

PROXY = "http://proxy.example.com:3128"
LOCAL_URLS = ("http://127.0.0.1:11434/v1/chat/completions", "http://localhost:8080/v1/models", "http://[::1]:30000/v1/systemone")
REMOTE_URL = "https://openrouter.ai/api/v1/chat/completions"


@pytest.fixture(autouse=True)
def proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, PROXY)


def _proxied(client: httpx.AsyncClient, url: str) -> bool:
    return client._transport_for_url(httpx.URL(url)) is not client._transport


def _close(client: httpx.AsyncClient) -> None:
    asyncio.run(client.aclose())


def test_the_environment_proxy_is_in_effect_for_a_plain_client() -> None:
    client = httpx.AsyncClient()
    try:
        assert _proxied(client, LOCAL_URLS[0]), "precondition: httpx would send loopback traffic to the proxy"
    finally:
        _close(client)


def test_http_client_reaches_loopback_directly_and_remote_hosts_through_the_proxy() -> None:
    client = http_client()
    try:
        assert not any(_proxied(client, url) for url in LOCAL_URLS)
        assert _proxied(client, REMOTE_URL)
    finally:
        _close(client)


def test_http_client_reaches_a_listed_local_host_directly() -> None:
    client = http_client(local_hosts=("10.0.0.5",))
    try:
        assert not _proxied(client, "http://10.0.0.5:11434/v1/models")
        assert _proxied(client, "http://10.0.0.6:11434/v1/models")
    finally:
        _close(client)


def test_http_client_for_a_local_url_ignores_the_environment() -> None:
    client = http_client("http://127.0.0.1:11434/v1")
    try:
        assert not _proxied(client, LOCAL_URLS[0])
        assert not client._trust_env
    finally:
        _close(client)


def test_the_chat_client_reaches_a_local_server_directly() -> None:
    chat = ChatClient(resolve_endpoint(LLMSettings(), env={}), model="m")
    try:
        assert not _proxied(chat._http, LOCAL_URLS[0])
    finally:
        asyncio.run(chat.aclose())


def test_the_chat_client_honours_the_proxy_for_a_hosted_endpoint() -> None:
    endpoint = resolve_endpoint(LLMSettings(provider="openrouter"), env={"OPENROUTER_API_KEY": "k"})
    chat = ChatClient(endpoint, model="m", egress_policy="hybrid")
    try:
        assert _proxied(chat._http, REMOTE_URL)
    finally:
        asyncio.run(chat.aclose())


def test_clef_reaches_its_local_server_directly_and_hosted_jev_uses_the_proxy() -> None:
    clef = make_clef("http://127.0.0.1:30000")
    jev = JevBackend(api_key_env="OPENROUTER_API_KEY", egress_policy="hybrid", env={})
    try:
        assert not _proxied(clef._client(), "http://127.0.0.1:30000/v1/systemone")
        assert _proxied(jev._client(), REMOTE_URL)
    finally:
        asyncio.run(clef.aclose())
        asyncio.run(jev.aclose())


@pytest.mark.parametrize("factory", [doctor._default_client, setup._default_client])
def test_doctor_and_setup_reach_local_servers_directly(factory) -> None:
    client = factory(2.0)
    try:
        assert not any(_proxied(client, url) for url in LOCAL_URLS)
        assert _proxied(client, REMOTE_URL)
    finally:
        _close(client)
