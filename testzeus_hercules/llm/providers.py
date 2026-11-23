"""Named LLM providers, endpoint resolution and the egress check.

Every provider speaks the OpenAI chat-completions API at its base URL. Local
servers need no key; Hercules sends a fixed placeholder so client libraries
that insist on a key are satisfied. Hosted providers read their key from the
environment variable named by ``llm.api_key_env`` (never from configuration).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import urlsplit

import httpx

from testzeus_hercules.kernel.settings import EgressPolicy, LLMCache, LLMSettings, Provider

KEYLESS_PLACEHOLDER = "hercules-local"

ImageUrlMode = Literal["data_url", "any", "none"]


@dataclass(frozen=True)
class CapabilityHints:
    """What a server is documented to support; ``None`` means unknown. Preflight measures the truth."""

    tool_choice: bool | None = None
    json_schema: bool | None = None
    vision: bool | None = None
    image_urls: ImageUrlMode | None = None


@dataclass(frozen=True)
class ProviderSpec:
    name: Provider
    default_base_url: str | None
    local: bool
    keyless: bool
    default_api_key_env: str | None = None
    hints: CapabilityHints = field(default_factory=CapabilityHints)

    @property
    def requires_base_url(self) -> bool:
        return self.default_base_url is None


PROVIDERS: Mapping[str, ProviderSpec] = MappingProxyType(
    {
        "ollama": ProviderSpec("ollama", "http://127.0.0.1:11434/v1", local=True, keyless=True, hints=CapabilityHints(tool_choice=False, json_schema=True, vision=True, image_urls="data_url")),
        "mlx-vlm": ProviderSpec("mlx-vlm", "http://127.0.0.1:8080/v1", local=True, keyless=True, hints=CapabilityHints(tool_choice=True, json_schema=True, vision=True)),
        "mlx": ProviderSpec("mlx", "http://127.0.0.1:8080/v1", local=True, keyless=True, hints=CapabilityHints(json_schema=False, vision=False, image_urls="none")),
        "lmstudio": ProviderSpec("lmstudio", "http://127.0.0.1:1234/v1", local=True, keyless=True, hints=CapabilityHints(json_schema=True, vision=True)),
        "llamacpp": ProviderSpec("llamacpp", "http://127.0.0.1:8080/v1", local=True, keyless=True, hints=CapabilityHints(tool_choice=True, json_schema=True)),
        "vllm": ProviderSpec("vllm", "http://127.0.0.1:8000/v1", local=True, keyless=True, hints=CapabilityHints(tool_choice=True, json_schema=True, vision=True)),
        "openai-compatible": ProviderSpec("openai-compatible", None, local=False, keyless=True),
        # A LiteLLM proxy (any model behind one OpenAI-compatible URL). Its key is optional; nothing is assumed about
        # the models behind it: doctor's probes measure what they support.
        "litellm": ProviderSpec("litellm", "http://127.0.0.1:4000/v1", local=False, keyless=True, default_api_key_env="LITELLM_API_KEY"),
        "openrouter": ProviderSpec("openrouter", "https://openrouter.ai/api/v1", local=False, keyless=False, default_api_key_env="OPENROUTER_API_KEY"),
        "openai": ProviderSpec("openai", "https://api.openai.com/v1", local=False, keyless=False, default_api_key_env="OPENAI_API_KEY"),
        "anthropic": ProviderSpec("anthropic", "https://api.anthropic.com/v1/", local=False, keyless=False, default_api_key_env="ANTHROPIC_API_KEY"),
        "azure": ProviderSpec("azure", None, local=False, keyless=False, default_api_key_env="AZURE_OPENAI_API_KEY"),
    }
)


class EndpointError(ValueError):
    """The configured endpoint cannot be used (missing base URL or key). Messages never contain secrets."""


class EgressBlocked(RuntimeError):
    """The egress policy forbids contacting this endpoint."""


@dataclass(frozen=True)
class Endpoint:
    provider: Provider
    base_url: str
    api_key: str = field(repr=False)
    is_local: bool
    # Extra request headers (llm.headers, values read from the environment); never printed.
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    # Ask a caching proxy for a fresh answer on every request (llm.cache: bypass, a LiteLLM proxy).
    no_cache: bool = False
    cache: LLMCache = "allow"


# Providers whose model names are passed through as written (a proxy's or hosted endpoint's own names, such as
# gemini/gemini-2.5-flash): never matched by prefix against the canonical model names.
VERBATIM_MODEL_PROVIDERS = frozenset({"openai-compatible", "litellm"})


def model_key_variable(llm: LLMSettings) -> str | None:
    """The environment variable that holds the model endpoint's key (``llm.api_key_env`` or the provider's), if any."""
    return llm.api_key_env or PROVIDERS[llm.provider].default_api_key_env


def cache_mode(llm: LLMSettings) -> LLMCache:
    """``llm.cache``, or its default: bypass for a LiteLLM proxy, allow otherwise."""
    return llm.cache or ("bypass" if llm.provider == "litellm" else "allow")


def resolve_endpoint(llm: LLMSettings, env: Mapping[str, str], local_hosts: Iterable[str] = ()) -> Endpoint:
    """Return the base URL, key and locality for the configured provider (``local_hosts``: ``egress.local_hosts``)."""
    spec = PROVIDERS[llm.provider]
    base_url = llm.base_url or spec.default_base_url
    if not base_url:
        raise EndpointError(f"provider '{llm.provider}' needs llm.base_url (or HERCULES_LLM_BASE_URL), for example http://127.0.0.1:8000/v1")
    cache = cache_mode(llm)
    return Endpoint(
        provider=llm.provider,
        base_url=base_url,
        api_key=_api_key(spec, llm.api_key_env, env),
        is_local=is_local_url(base_url, local_hosts),
        headers=MappingProxyType(_headers(llm.headers, env)),
        no_cache=cache == "bypass" and llm.provider == "litellm",
        cache=cache,
    )


def _headers(names: Mapping[str, str], env: Mapping[str, str]) -> dict[str, str]:
    """Header values from the environment variables ``llm.headers`` names; a missing variable is an error (never echoed)."""
    values: dict[str, str] = {}
    for header, variable in names.items():
        value = env.get(variable, "").strip()
        if not value:
            raise EndpointError(f"llm.headers: header '{header}' needs the environment variable {variable}, which is not set")
        values[header] = value
    return values


def _api_key(spec: ProviderSpec, api_key_env: str | None, env: Mapping[str, str]) -> str:
    variable = api_key_env or spec.default_api_key_env
    if variable is None:
        return KEYLESS_PLACEHOLDER
    value = env.get(variable, "").strip()
    if value:
        return value
    if spec.keyless and api_key_env is None:
        return KEYLESS_PLACEHOLDER
    raise EndpointError(f"provider '{spec.name}' needs an API key: set the environment variable {variable}")


def check_egress(endpoint: Endpoint, policy: EgressPolicy) -> None:
    """Raise ``EgressBlocked`` when the policy keeps traffic local and the endpoint is remote."""
    if policy == "local" and not endpoint.is_local:
        raise EgressBlocked(
            f"egress policy 'local' forbids the remote endpoint of provider '{endpoint.provider}'. Use a local provider, "
            "or allow hosted models with egress.policy: hybrid-text (or hybrid) in .hercules/config.yaml, or HERCULES_EGRESS=hybrid-text"
        )


# Only this machine is local by default: a LAN address or a ``.local`` / ``host.docker.internal`` name is another
# machine, so it counts as local only when listed in ``egress.local_hosts``.
_LOOPBACK_NAMES = frozenset({"localhost"})
# Hosts requests always reach directly (never through HTTP(S)_PROXY / ALL_PROXY), as httpx mount patterns.
_DIRECT_MOUNTS = ("localhost", "127.0.0.1", "[::1]")


def _host(url: str) -> str | None:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.rstrip(".").lower() if host else None


def _canonical(host: str) -> str:
    """A host name lower-cased, or an IP address in its canonical form (IPv4-mapped IPv6 as IPv4)."""
    name = host.strip().lower().rstrip(".").removeprefix("[").removesuffix("]")
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return name
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def is_local_url(url: str, local_hosts: Iterable[str] = ()) -> bool:
    """True when the URL's host is this machine (loopback: 127.0.0.0/8, ::1, ``localhost``) or a host listed in
    ``local_hosts`` (``egress.local_hosts``). No DNS lookups; no other private-network address or name is trusted."""
    host = _host(url)
    if not host:
        return False
    host = _canonical(host)
    if host in _LOOPBACK_NAMES or host in {_canonical(listed) for listed in local_hosts}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def http_client(url: str | None = None, *, local_hosts: Iterable[str] = (), **kwargs: Any) -> httpx.AsyncClient:
    """An httpx client that never sends local traffic through an environment proxy.

    With ``url`` on this machine (or a listed local host) the client ignores the environment (``trust_env=False``), so
    HTTP_PROXY / ALL_PROXY never see prompts, page text, screenshots or keys meant for a local server. Otherwise the
    environment's proxy settings apply to remote hosts only: loopback and listed hosts are always reached directly.
    """
    hosts = tuple(local_hosts)
    if url is not None and is_local_url(url, hosts):
        return httpx.AsyncClient(trust_env=False, **kwargs)
    direct = {f"all://{_mount_host(host)}": None for host in (*_DIRECT_MOUNTS, *hosts)}
    return httpx.AsyncClient(mounts=direct, **kwargs)


def _mount_host(host: str) -> str:
    name = _canonical(host)
    return f"[{name}]" if ":" in name and not name.startswith("[") else name


def native_root(base_url: str) -> str:
    """The server root for native (non-OpenAI) endpoints such as Ollama's ``/api`` or llama.cpp's ``/props``."""
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def redact_url(url: str) -> str:
    """Drop credentials, query and fragment so a URL is safe to print."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "<invalid url>"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{netloc}{parts.path}" if parts.scheme else netloc + parts.path
