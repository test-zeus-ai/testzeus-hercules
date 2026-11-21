"""Local decision backend: a Clef or Clef-flash server on this machine (or the local network).

Clef and Clef-flash are open-weight decision models (Apache-2.0) that answer the same
request shape as JEV's SystemOne API at ``POST <server>/v1/systemone``: a ``state``, a
flat map of typed questions and, for images, an ``assets`` map. This backend is the
JEV client (:class:`~testzeus_hercules.decide.backends.jev.JevBackend`) in its TypeSafe
shape, pointed at the local server: no key, nothing leaves the machine, so the egress
policy does not restrict it. A Clef URL that is not on this machine (loopback) or a host
listed in ``egress.local_hosts`` is refused; hosted decision models go through JEV and
the egress policy.

Images are off by default: how a Clef server reads them over HTTP is not yet verified,
so the step's screenshot stays with the local vision model unless ``images`` is on.
See the user guide (modes and privacy) for how to serve Clef-flash locally.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

import httpx

from testzeus_hercules.decide.backends.jev import JevBackend
from testzeus_hercules.decide.contract import DecisionRequest, NoulQuestion
from testzeus_hercules.decide.errors import DecisionError
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.llm.providers import is_local_url, redact_url

__all__ = ["CLEF_ENGINE", "CLEF_PATH", "CLEF_TIMEOUT_S", "DEFAULT_CLEF_MODEL", "DEFAULT_CLEF_URL", "ClefProbe", "clef_endpoint", "clef_url", "make_clef", "probe_clef"]

CLEF_ENGINE = "clef"
# Where a local Clef server listens unless decider.clef_url says otherwise (SGLang's default port).
DEFAULT_CLEF_URL = "http://127.0.0.1:30000"
CLEF_PATH = "/v1/systemone"
DEFAULT_CLEF_MODEL = "Cloudflare/clef-flash"
CLEF_TIMEOUT_S = 20.0
PROBE_TIMEOUT_S = 10.0


def clef_endpoint(base_url: str, local_hosts: Iterable[str] = ()) -> str:
    """The decisions URL of a Clef server given its root (``http://127.0.0.1:30000``) or the full URL.

    The server must be on this machine, or on a host listed in ``local_hosts`` (``egress.local_hosts``)."""
    url = base_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Clef URL must be an http(s) URL such as http://127.0.0.1:30000, got {redact_url(url)!r}")
    if not is_local_url(url, local_hosts):
        raise ValueError(f"Clef URL {redact_url(url)} is not on this machine; only a local Clef server is supported (list another machine in egress.local_hosts to trust it)")
    if url.endswith(CLEF_PATH):
        return url
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url + CLEF_PATH


def make_clef(
    base_url: str,
    *,
    model: str | None = None,
    http: httpx.AsyncClient | None = None,
    timeout_s: float = CLEF_TIMEOUT_S,
    max_retries: int = 1,
    env: Mapping[str, str] | None = None,
    local_hosts: Iterable[str] = (),
) -> JevBackend:
    """A decision engine that asks the local Clef server at ``base_url``; ValueError for a non-local URL."""
    hosts = tuple(local_hosts)
    return JevBackend(
        transport="typesafe",
        model=model or DEFAULT_CLEF_MODEL,
        api_key_env=None,
        egress_policy="local",
        env=env or {},
        http=http,
        timeout_s=timeout_s,
        max_retries=max_retries,
        url=clef_endpoint(base_url, hosts),
        local_hosts=hosts,
        engine_name=CLEF_ENGINE,
        zdr=False,
        allow_alias=True,
    )


def clef_url(settings: Settings) -> str | None:
    """The Clef server to try in pure mode: ``decider.clef_url``, or the default one when ``decider.backend`` is clef."""
    if settings.decider.clef_url:
        return settings.decider.clef_url
    return DEFAULT_CLEF_URL if settings.decider.backend == "clef" else None


def clef_from_settings(settings: Settings, *, http: httpx.AsyncClient | None = None) -> JevBackend | None:
    """The Clef engine ``decider.clef_url`` names, or None when no Clef server is configured."""
    if not settings.decider.clef_url:
        return None
    return make_clef(settings.decider.clef_url, model=settings.decider.clef_model, http=http, local_hosts=settings.egress.local_hosts)


class ClefProbe:
    """Result of one tiny decision against a Clef server."""

    def __init__(self, url: str, ok: bool, detail: str, latency_ms: int | None = None, model: str | None = None) -> None:
        self.url = url
        self.ok = ok
        self.detail = detail
        self.latency_ms = latency_ms
        self.model = model


_PROBE = DecisionRequest(state={"note": "Hercules health check."}, questions={"ok": NoulQuestion(instructions="Is this a health check?")})


async def probe_clef(base_url: str, *, model: str | None = None, http: httpx.AsyncClient | None = None, timeout_s: float = PROBE_TIMEOUT_S, local_hosts: Iterable[str] = ()) -> ClefProbe:
    """Ask the Clef server one yes/no question; never raises."""
    try:
        engine = make_clef(base_url, model=model, http=http, timeout_s=timeout_s, max_retries=0, local_hosts=local_hosts)
    except ValueError as exc:
        return ClefProbe(redact_url(base_url), False, str(exc))
    started = time.perf_counter()
    try:
        decision = await engine.decide(_PROBE, purpose="health")
    except DecisionError as exc:
        return ClefProbe(engine.url, False, str(exc))
    finally:
        await engine.aclose()
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ClefProbe(engine.url, True, f"answered a test decision in {latency_ms} ms (model {decision.model_version})", latency_ms, decision.model_version)
