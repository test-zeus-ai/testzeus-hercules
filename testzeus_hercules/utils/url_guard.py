"""SSRF-guarded HTTP fetching for model/client-supplied URLs.

Rules: http/https only; the host is resolved and every address must be public
(loopback, private, link-local incl. cloud metadata, multicast, reserved and
unspecified ranges are blocked) unless ``allow_private`` is set; every redirect hop is re-validated
(no blind ``follow_redirects``); the connection is pinned to the validated address
(defeats DNS rebinding between check and connect); response size and time are bounded.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

Resolver = Callable[[str, int], list[str]]


class UnsafeURLError(ValueError):
    """The URL (or a redirect target) is not allowed to be fetched."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status_code: int
    text: str


def _default_resolver(host: str, port: int) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def validate_url(url: str, resolver: Resolver | None = None, allow_private: bool = False) -> tuple[str, str, int, str]:
    """Validate ``url``; return ``(scheme, hostname, port, pinned_ip)``.

    Raises:
        UnsafeURLError: bad scheme, missing host, credentials in URL, unresolvable host or non-public address.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("only http/https URLs are allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError("URL has an invalid port") from exc
    try:
        addresses = [str(ipaddress.ip_address(host))]  # literal IP
    except ValueError:
        try:
            addresses = (resolver or _default_resolver)(host, port)
        except OSError as exc:
            raise UnsafeURLError(f"could not resolve host {host!r}") from exc
    if not addresses:
        raise UnsafeURLError(f"could not resolve host {host!r}")
    if not allow_private:
        for address in addresses:
            if not is_public_address(address):
                raise UnsafeURLError(f"host {host!r} resolves to a non-public address; fetching internal/loopback/link-local targets is blocked")
    return parts.scheme, host, port, addresses[0]


def fetch_text(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allow_private: bool = False,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FetchResult:
    """GET ``url`` safely and return its decoded text.

    Raises:
        UnsafeURLError: blocked URL/redirect, or response larger than ``max_bytes``.
        httpx.HTTPError: transport failure or non-2xx status.
    """
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport, trust_env=False) as client:
        for _hop in range(max_redirects + 1):
            scheme, host, port, pinned_ip = validate_url(current, resolver=resolver, allow_private=allow_private)
            parts = urlsplit(current)
            ip_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
            default_port = 443 if scheme == "https" else 80
            netloc = ip_host if port == default_port else f"{ip_host}:{port}"
            pinned_url = parts._replace(netloc=netloc).geturl()
            host_header = host if port == default_port else f"{host}:{port}"
            extensions = {"sni_hostname": host} if scheme == "https" else {}
            with client.stream("GET", pinned_url, headers={"Host": host_header}, extensions=extensions) as response:
                if response.status_code in _REDIRECT_STATUSES and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise UnsafeURLError(f"response larger than {max_bytes} bytes")
                encoding = response.encoding or "utf-8"
                return FetchResult(url=current, status_code=response.status_code, text=bytes(body).decode(encoding, errors="replace"))
    raise UnsafeURLError(f"too many redirects (>{max_redirects})")
