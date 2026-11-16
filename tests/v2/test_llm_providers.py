"""Tests for named LLM providers, endpoint resolution and the egress check."""

import pytest

from testzeus_hercules.kernel.settings import LLMSettings
from testzeus_hercules.llm.providers import (
    KEYLESS_PLACEHOLDER,
    PROVIDERS,
    EgressBlocked,
    Endpoint,
    EndpointError,
    check_egress,
    is_local_url,
    native_root,
    resolve_endpoint,
)


@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("ollama", "http://127.0.0.1:11434/v1"),
        ("mlx-vlm", "http://127.0.0.1:8080/v1"),
        ("mlx", "http://127.0.0.1:8080/v1"),
        ("lmstudio", "http://127.0.0.1:1234/v1"),
        ("llamacpp", "http://127.0.0.1:8080/v1"),
        ("vllm", "http://127.0.0.1:8000/v1"),
    ],
)
def test_local_providers_default_to_loopback_and_need_no_key(provider: str, base_url: str) -> None:
    endpoint = resolve_endpoint(LLMSettings(provider=provider), env={})

    assert endpoint == Endpoint(provider=provider, base_url=base_url, api_key=KEYLESS_PLACEHOLDER, is_local=True)
    assert PROVIDERS[provider].local and PROVIDERS[provider].keyless


def test_every_settings_provider_has_a_spec() -> None:
    from typing import get_args

    from testzeus_hercules.kernel.settings import Provider

    assert set(get_args(Provider)) == set(PROVIDERS)


@pytest.mark.parametrize(
    ("provider", "base_url"),
    [("openrouter", "https://openrouter.ai/api/v1"), ("openai", "https://api.openai.com/v1"), ("anthropic", "https://api.anthropic.com/v1/")],
)
def test_hosted_provider_reads_key_from_named_env_var(provider: str, base_url: str) -> None:
    endpoint = resolve_endpoint(LLMSettings(provider=provider, api_key_env="MY_KEY"), env={"MY_KEY": "k-123"})

    assert endpoint.base_url == base_url
    assert endpoint.api_key == "k-123"
    assert endpoint.is_local is False


def test_hosted_provider_uses_conventional_env_var_by_default() -> None:
    endpoint = resolve_endpoint(LLMSettings(provider="openrouter"), env={"OPENROUTER_API_KEY": "k-1"})

    assert endpoint.api_key == "k-1"


def test_hosted_provider_without_key_is_an_error_naming_the_variable() -> None:
    with pytest.raises(EndpointError, match="MY_KEY"):
        resolve_endpoint(LLMSettings(provider="openai", api_key_env="MY_KEY"), env={})


@pytest.mark.parametrize("provider", ["openai-compatible", "azure"])
def test_providers_without_a_default_url_require_base_url(provider: str) -> None:
    with pytest.raises(EndpointError, match="base_url"):
        resolve_endpoint(LLMSettings(provider=provider, api_key_env="K"), env={"K": "x"})


def test_explicit_base_url_overrides_default_and_decides_locality() -> None:
    endpoint = resolve_endpoint(LLMSettings(provider="ollama", base_url="http://gpu-box.example.com:11434/v1"), env={})

    assert endpoint.base_url == "http://gpu-box.example.com:11434/v1"
    assert endpoint.is_local is False


def test_keyless_provider_uses_named_key_when_given() -> None:
    endpoint = resolve_endpoint(LLMSettings(provider="vllm", api_key_env="VLLM_KEY"), env={"VLLM_KEY": "secret"})

    assert endpoint.api_key == "secret"


def test_keyless_provider_with_missing_named_key_is_an_error() -> None:
    with pytest.raises(EndpointError, match="VLLM_KEY"):
        resolve_endpoint(LLMSettings(provider="vllm", api_key_env="VLLM_KEY"), env={})


def test_endpoint_repr_hides_the_key() -> None:
    endpoint = resolve_endpoint(LLMSettings(provider="openai"), env={"OPENAI_API_KEY": "sk-very-secret"})

    assert "sk-very-secret" not in repr(endpoint)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://127.8.9.10:11434/v1",
        "http://localhost:8080/v1",
        "http://LOCALHOST./v1",
        "http://[::1]:8080/v1",
        "http://[::ffff:127.0.0.1]/v1",
    ],
)
def test_local_urls(url: str) -> None:
    assert is_local_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://openrouter.ai/api/v1",
        "https://api.openai.com/v1",
        "http://8.8.8.8/v1",
        "http://172.32.0.1/v1",
        "http://100.64.0.1/v1",
        "http://[2001:db8::1]/v1",
        "http://localhost.example.com/v1",
        "http://notlocal/v1",
        "not a url",
        "",
        # Other machines on the network are not "this machine" unless listed in egress.local_hosts.
        "http://0.0.0.0:8000",
        "http://10.1.2.3/v1",
        "http://172.16.0.1/v1",
        "http://10.0.0.5:1234/v1",
        "http://169.254.10.10/v1",
        "http://[fd12:3456::1]/v1",
        "http://[fe80::1]/v1",
        "http://[::ffff:10.0.0.5]/v1",
        "http://studio.local:8080/v1",
        "http://app.localhost/v1",
        "http://host.docker.internal:11434/v1",
        "http://host.containers.internal:11434/v1",
        "http://127.0.0.1@evil.example.com/v1",
    ],
)
def test_remote_or_invalid_urls(url: str) -> None:
    assert is_local_url(url) is False


@pytest.mark.parametrize(
    ("url", "listed"),
    [
        ("http://10.0.0.5:1234/v1", ("10.0.0.5",)),
        ("http://[::ffff:10.0.0.5]/v1", ("10.0.0.5",)),
        ("http://GPU-Box.local:11434/v1", ("gpu-box.local",)),
        ("http://host.docker.internal:11434/v1", ("host.docker.internal",)),
        ("http://[fd12:3456::1]/v1", ("[fd12:3456::1]",)),
    ],
)
def test_a_listed_local_host_counts_as_local(url: str, listed: tuple[str, ...]) -> None:
    assert is_local_url(url, listed) is True
    assert is_local_url("http://10.0.0.6/v1", listed) is False, "only the listed host, never its neighbours"


def test_resolve_endpoint_uses_the_listed_local_hosts() -> None:
    llm = LLMSettings(provider="ollama", base_url="http://10.0.0.5:11434/v1")

    assert resolve_endpoint(llm, env={}).is_local is False
    assert resolve_endpoint(llm, env={}, local_hosts=("10.0.0.5",)).is_local is True


def test_egress_policy_local_blocks_remote_endpoints() -> None:
    remote = Endpoint(provider="openrouter", base_url="https://openrouter.ai/api/v1", api_key="k", is_local=False)

    with pytest.raises(EgressBlocked, match="egress"):
        check_egress(remote, "local")
    check_egress(remote, "hybrid-text")
    check_egress(remote, "hybrid")


def test_egress_policy_local_allows_local_endpoints() -> None:
    check_egress(resolve_endpoint(LLMSettings(), env={}), "local")


@pytest.mark.parametrize(
    ("base_url", "root"),
    [("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434"), ("http://127.0.0.1:11434/v1/", "http://127.0.0.1:11434"), ("http://h:8080", "http://h:8080")],
)
def test_native_root_strips_the_openai_suffix(base_url: str, root: str) -> None:
    assert native_root(base_url) == root


def test_capability_hints_for_known_servers() -> None:
    assert PROVIDERS["ollama"].hints.tool_choice is False
    assert PROVIDERS["ollama"].hints.image_urls == "data_url"
    assert PROVIDERS["mlx"].hints.json_schema is False
    assert PROVIDERS["mlx"].hints.vision is False
    assert PROVIDERS["vllm"].hints.tool_choice is True


def test_egress_local_hosts_are_plain_hosts() -> None:
    from testzeus_hercules.kernel.settings import EgressSettings

    assert EgressSettings(local_hosts=("10.0.0.5", "GPU-Box.local.", "[fd12::1]")).local_hosts == ("10.0.0.5", "gpu-box.local", "fd12::1")
    for bad in ("http://10.0.0.5", "10.0.0.5:11434", "gpu box", "", "10.0.0.5/v1"):
        with pytest.raises(ValueError):
            EgressSettings(local_hosts=(bad,))
