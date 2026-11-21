"""``hercules doctor``: check settings, hardware tier, egress policy, the model server and who decides.

The ``decider`` check says which mode ``hercules run`` picks by default (hybrid when
an OpenRouter key is found and egress allows it, else pure) and where the key comes
from (never the key itself); the ``clef`` check asks a configured local Clef server
one tiny question. The ``appium`` check looks for the Appium server native app cases use
(``mobile.appium_url``), and the ``device_cloud`` check says whether the BrowserStack, Sauce
Labs and TestMu AI credentials are set, in the environment or saved in ``clouds.env`` (by variable name, never
the values); neither fails the report, since both are needed only for native app tests and cloud runs. The ``scanner`` check says whether the optional security scanner (nuclei)
is installed for ``hercules scan`` and security steps (``skipped`` when it is not: it is optional).

Exit codes: 0 when every check is ok or a warning, 1 when any check fails,
2 when the model server cannot be reached.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, TextIO

from pydantic import BaseModel, ConfigDict, Field

from testzeus_hercules.cli.render import render_text
from testzeus_hercules.cli.style import Style
from testzeus_hercules.cli.version import package_version
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.cli.keys import with_model_key
from testzeus_hercules.kernel.settings import Settings, SettingsError
from testzeus_hercules.kernel.telemetry import TelemetryClient, telemetry_enabled
from testzeus_hercules.llm.capabilities import (
    Capabilities,
    CapabilityCache,
    capability_url,
)
from testzeus_hercules.llm.checks import CheckResult, CheckStatus, overall_status
from testzeus_hercules.llm.providers import (
    model_key_variable,
    EgressBlocked,
    Endpoint,
    EndpointError,
    cache_mode,
    check_egress,
    is_local_url,
    redact_url,
    resolve_endpoint,
)
from testzeus_hercules.llm.tiers import (
    GIB,
    TierProfile,
    TierSelection,
    detect_memory_bytes,
    select_tier,
)

if TYPE_CHECKING:
    import httpx

    from testzeus_hercules.llm.preflight import ProbeReport

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNREACHABLE = 2


def _default_client(timeout_s: float) -> httpx.AsyncClient:
    from testzeus_hercules.llm.providers import http_client

    # Local servers are reached directly; an environment proxy applies to remote endpoints only.
    return http_client(timeout=timeout_s, follow_redirects=False)


def _default_telemetry(settings: Settings, paths: HerculesPaths) -> TelemetryClient:
    return TelemetryClient(settings, paths)


def appium_status(url: str) -> dict[str, Any] | None:
    """The Appium server's ``/status`` value at ``url``, or None when nothing answers (a short, direct request)."""
    import httpx

    try:
        response = httpx.get(f"{url.rstrip('/')}/status", timeout=3.0, trust_env=False)
        value = response.json().get("value") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return value if isinstance(value, dict) else None


def _default_scanner(env: Mapping[str, str], cache_dir: Path) -> tuple[Path, str | None]:
    """The security scanner's path and version; raises the scanner's ``ScannerMissing`` when it is not installed."""
    from testzeus_hercules.scan.install import find_nuclei
    from testzeus_hercules.scan.nuclei import scanner_version

    binary = find_nuclei(env, cache_dir)
    return binary, asyncio.run(scanner_version(binary))


@dataclass(frozen=True)
class DoctorDeps:
    """Seams for tests: memory detection, the HTTP client factory and telemetry."""

    detect_memory: Callable[[], int | None] = detect_memory_bytes
    make_client: Callable[[float], httpx.AsyncClient] = _default_client
    make_telemetry: Callable[[Settings, HerculesPaths], TelemetryClient] = _default_telemetry
    appium_status: Callable[[str], dict[str, Any] | None] = appium_status
    find_scanner: Callable[[Mapping[str, str], Path], tuple[Path, str | None]] = _default_scanner


@dataclass(frozen=True)
class DoctorOptions:
    project_dir: Path | None = None
    json_output: bool = False
    use_cache: bool = True


class SettingsSummary(BaseModel):
    """Effective settings without secrets (key values are never read into the report)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None
    tier: str
    egress_policy: str
    request_timeout_s: float
    # llm.request_params: extra fields sent with every chat request (never secrets: configuration refuses them).
    request_params: dict[str, Any] = Field(default_factory=dict)
    # llm.headers: header name -> the environment variable that holds its value (values are never read into the report).
    headers: dict[str, str] = Field(default_factory=dict)
    # A caching proxy's response cache: bypass or allow (llm.cache, or its default for the provider).
    cache: str = "allow"
    telemetry_enabled: bool
    project_dir: str


class EndpointSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    base_url: str
    is_local: bool


class DeciderSummary(BaseModel):
    """Who decides in a default (``--mode auto``) run, and why; never the key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str
    reason: str
    decider: str
    key_env: str
    key_source: str
    zdr: bool
    clef_url: str | None = None
    clef_reachable: bool | None = None


class DoctorReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hercules_version: str
    settings: SettingsSummary | None = None
    decider: DeciderSummary | None = None
    memory_bytes: int | None = None
    tier: TierProfile | None = None
    tier_auto: bool = False
    endpoint: EndpointSummary | None = None
    requested_model: str | None = None
    model_id: str | None = None
    server_version: str | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    capabilities: Capabilities | None = None
    from_cache: bool = False
    status: CheckStatus
    exit_code: int


def run_doctor(
    options: DoctorOptions,
    *,
    env: Mapping[str, str] | None,
    deps: DoctorDeps,
    out: TextIO,
) -> int:
    started = time.monotonic()
    environ: Mapping[str, str] = os.environ if env is None else env
    try:
        settings = Settings.load(options.project_dir, env=environ)
    except SettingsError as exc:
        check = CheckResult(
            name="settings",
            status="fail",
            detail=str(exc),
            fix="fix the configuration file or environment variable named above",
        )
        report = DoctorReport(
            hercules_version=package_version(),
            checks=[check],
            status="fail",
            exit_code=EXIT_FAILED,
        )
        _write(report, options, out, environ)
        return report.exit_code

    paths = HerculesPaths.from_settings(settings)
    environ = with_model_key(environ, settings.home_dir, model_key_variable(settings.llm))
    report = _diagnose(settings, paths, options, environ, deps)
    summary, decider_checks = _decider(settings, environ, deps)
    report = _with_decider(report, summary, decider_checks)
    report = _with_decider(report, report.decider, _mobile_checks(settings, environ, deps))
    report = _with_checks(report, [_scanner_check(environ, paths, deps)])
    _write(report, options, out, environ)
    _send_telemetry(settings, paths, deps, report, started)
    return report.exit_code


def _diagnose(
    settings: Settings,
    paths: HerculesPaths,
    options: DoctorOptions,
    env: Mapping[str, str],
    deps: DoctorDeps,
) -> DoctorReport:
    selection = select_tier(deps.detect_memory(), settings.llm.tier)
    checks = [_memory_check(selection)]
    fields = {
        "hercules_version": package_version(),
        "settings": _summary(settings),
        "memory_bytes": selection.memory_bytes,
        "tier": selection.profile,
        "tier_auto": selection.auto,
    }
    try:
        endpoint = resolve_endpoint(settings.llm, env, settings.egress.local_hosts)
    except EndpointError as exc:
        checks.append(
            CheckResult(
                name="endpoint",
                status="fail",
                detail=str(exc),
                fix="set the variable or llm.base_url, then run `hercules doctor` again",
            )
        )
        return _finish(fields, checks, probe=None)
    fields["endpoint"] = EndpointSummary(
        provider=endpoint.provider,
        base_url=redact_url(endpoint.base_url),
        is_local=endpoint.is_local,
    )
    egress = _egress_check(endpoint, settings)
    checks.append(egress)
    if egress.status == "fail":
        return _finish(fields, checks, probe=None)

    requested = selection.profile.model_alias if settings.llm.model == "auto" else settings.llm.model
    cache = CapabilityCache(paths.capabilities_file)
    params = settings.llm.request_params
    probe = asyncio.run(
        _probe(
            endpoint,
            requested,
            selection.profile,
            deps,
            settings.llm.request_timeout_s,
            cache if options.use_cache else None,
            params,
        )
    )
    if probe.capabilities is not None and not probe.from_cache:
        cache.put(capability_url(probe.base_url, params), probe.capabilities)
    return _finish(fields, checks + probe.checks, probe=probe)


async def _probe(
    endpoint: Endpoint,
    requested: str,
    tier: TierProfile,
    deps: DoctorDeps,
    timeout_s: float,
    cache: CapabilityCache | None,
    request_params: dict[str, Any] | None = None,
) -> ProbeReport:
    from testzeus_hercules.llm.preflight import probe_server

    async with deps.make_client(timeout_s) as client:
        return await probe_server(
            endpoint,
            requested,
            tier,
            client=client,
            timeout_s=timeout_s,
            cache=cache,
            request_params=request_params,
        )


def _finish(fields: dict[str, object], checks: list[CheckResult], probe: ProbeReport | None) -> DoctorReport:
    status = overall_status(checks)
    if probe is not None and not probe.reachable:
        exit_code = EXIT_UNREACHABLE
    else:
        exit_code = EXIT_FAILED if status == "fail" else EXIT_OK
    extra: dict[str, object] = {}
    if probe is not None:
        extra = {
            "requested_model": probe.requested_model,
            "model_id": probe.model_id,
            "server_version": probe.server_version,
            "capabilities": probe.capabilities,
            "from_cache": probe.from_cache,
        }
    return DoctorReport.model_validate({**fields, **extra, "checks": checks, "status": status, "exit_code": exit_code})


def _decider(settings: Settings, env: Mapping[str, str], deps: DoctorDeps) -> tuple[DeciderSummary | None, list[CheckResult]]:
    """The mode a default run picks and the checks behind it (key, Clef server)."""
    from testzeus_hercules.cli.deciders import clef_url, key_origin, prepare
    from testzeus_hercules.decide.backends.jev import DEFAULT_MODELS
    from testzeus_hercules.engine.factory import (
        EngineFactoryError,
        FactoryOptions,
        resolve_mode,
    )

    setup = prepare(settings, env)
    key = setup.key
    checks: list[CheckResult] = []
    if key.problem:
        checks.append(
            CheckResult(
                name="jev_key",
                status="warn",
                detail=key.problem,
                fix=f"chmod 600 {key.path}, or run `hercules setup`",
            )
        )
    try:
        mode, reason, _ = resolve_mode(setup.settings, setup.env, FactoryOptions(key_origin=key_origin(key)))
    except EngineFactoryError as exc:
        checks.append(
            CheckResult(
                name="decider",
                status="fail",
                detail=str(exc),
                fix="fix decider.* or egress.policy in .hercules/config.yaml, or run `hercules setup`",
            )
        )
        return None, checks
    decider_settings = settings.decider
    zdr = decider_settings.zdr and decider_settings.jev_transport == "openrouter"
    clef = clef_url(settings)
    clef_ok: bool | None = None
    if mode == "pure" and clef is not None:
        clef_ok = _clef_check(clef, settings, deps, checks)
    if mode == "hybrid":
        model = decider_settings.model or DEFAULT_MODELS[decider_settings.jev_transport]
        where = "OpenRouter" if decider_settings.jev_transport == "openrouter" else "the TypeSafe API"
        privacy = "zero data retention requested" if zdr else "zero data retention not requested (decider.zdr: false)"
        images = "page text and, when JEV is unsure, screenshots" if settings.egress.policy == "hybrid" else "page text only"
        decider = f"JEV {model} via {where} ({privacy}; sends {images})"
        checks.append(
            CheckResult(
                name="decider",
                status="ok",
                detail=f"hybrid: {decider} decides; key {key.describe()}",
            )
        )
    elif clef_ok:
        decider = f"Clef at {clef}"
        checks.append(
            CheckResult(
                name="decider",
                status="ok",
                detail=f"pure: the local Clef server decides; the local model interprets ({reason})",
            )
        )
    else:
        decider = "local model"
        if key.found:
            checks.append(
                CheckResult(
                    name="decider",
                    status="ok",
                    detail=f"pure: the local model decides ({reason})",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="decider",
                    status="warn",
                    detail=f"pure: the local model decides because no OpenRouter key is configured; key {key.describe()}",
                    fix="run `hercules setup` to add a key and use hybrid mode (JEV decides), or keep pure mode",
                )
            )
    summary = DeciderSummary(
        mode=mode,
        reason=reason,
        decider=decider,
        key_env=key.variable,
        key_source=key.source,
        zdr=zdr,
        clef_url=clef,
        clef_reachable=clef_ok,
    )
    return summary, checks


def _clef_check(url: str, settings: Settings, deps: DoctorDeps, checks: list[CheckResult]) -> bool:
    from testzeus_hercules.decide.backends.clef import probe_clef

    async def probe() -> object:
        async with deps.make_client(10.0) as client:
            return await probe_clef(
                url,
                model=settings.decider.clef_model,
                http=client,
                local_hosts=settings.egress.local_hosts,
            )

    result = asyncio.run(probe())
    ok = bool(getattr(result, "ok", False))
    detail = str(getattr(result, "detail", ""))
    if ok:
        checks.append(
            CheckResult(
                name="clef",
                status="ok",
                detail=f"{getattr(result, 'url', url)}: {detail}",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="clef",
                status="warn",
                detail=f"Clef server not usable: {detail}; the local model decides",
                fix="start the Clef server (see the guide: modes and privacy) or unset decider.clef_url",
            )
        )
    return ok


def _mobile_checks(settings: Settings, env: Mapping[str, str], deps: DoctorDeps) -> list[CheckResult]:
    """The Appium server and the device cloud credentials (warnings at most: only native app cases need them)."""
    url = settings.mobile.appium_url.rstrip("/")
    is_local = is_local_url(url, settings.egress.local_hosts)
    # A remote Appium server is not contacted under the local egress policy.
    value = deps.appium_status(url) if is_local or settings.egress.policy != "local" else None
    if isinstance(value, dict):
        version = (value.get("build") or {}).get("version") if isinstance(value.get("build"), dict) else None
        checks = [
            CheckResult(
                name="appium",
                status="ok",
                detail=f"Appium server{f' {version}' if version else ''} at {redact_url(url)} (native app cases)",
            )
        ]
    else:
        checks = [
            CheckResult(
                name="appium",
                status="skipped",
                detail=f"no Appium server answers at {redact_url(url)}; needed only for native app cases",
                fix="start it with `appium --address 127.0.0.1 --port 4723` (see docs/guide/mobile-testing.md)",
            )
        ]
    found = []
    placeholders = []
    from testzeus_hercules.clouds.credentials import find_credentials, placeholder_problem
    from testzeus_hercules.clouds.registry import CLOUD_IDS

    for provider in CLOUD_IDS:
        credentials = find_credentials(provider, settings, env)
        if placeholder_problem(credentials):
            placeholders.append(provider)
        missing = [name for name in (credentials.username_env, credentials.access_key_env) if not env.get(name, "").strip()]
        if credentials.source == "file":
            found.append(f"{provider}: credentials set (saved in clouds.env)")
        else:
            found.append(f"{provider}: {'credentials set' if not missing else 'not set (' + ', '.join(missing) + ')'}")
    found.append("check them with `hercules cloud check <cloud>`")
    remote = "" if settings.egress.policy != "local" else "; egress policy 'local' forbids device clouds"
    checks.append(
        CheckResult(
            name="device_cloud",
            status="skipped" if remote else ("warn" if placeholders else "ok"),
            detail="; ".join(found) + remote,
            fix=(
                f"the user name for {', '.join(placeholders)} looks like a placeholder; connect again with the real one " "(Integrations in `hercules ui`, or `hercules cloud connect`)"
                if placeholders and not remote
                else None
            ),
        )
    )
    return checks


def _with_checks(report: DoctorReport, checks: list[CheckResult]) -> DoctorReport:
    merged = report.checks + checks
    status = overall_status(merged)
    exit_code = report.exit_code if report.exit_code == EXIT_UNREACHABLE else (EXIT_FAILED if status == "fail" else report.exit_code)
    return report.model_copy(update={"checks": merged, "status": status, "exit_code": exit_code})


def _with_decider(report: DoctorReport, summary: DeciderSummary | None, checks: list[CheckResult]) -> DoctorReport:
    if summary is None and not checks:
        return report
    merged = report.checks + checks
    status = overall_status(merged)
    exit_code = report.exit_code if report.exit_code == EXIT_UNREACHABLE else (EXIT_FAILED if status == "fail" else report.exit_code)
    return report.model_copy(
        update={
            "decider": summary,
            "checks": merged,
            "status": status,
            "exit_code": exit_code,
        }
    )


def _scanner_check(env: Mapping[str, str], paths: HerculesPaths, deps: DoctorDeps) -> CheckResult:
    """The optional security scanner: ok with its path and version, skipped when it is missing (only scans need it)."""
    from testzeus_hercules.scan.install import ScannerMissing

    try:
        binary, version = deps.find_scanner(env, paths.cache_dir)
    except ScannerMissing:
        return CheckResult(
            name="scanner",
            status="skipped",
            detail="the security scanner (nuclei) is not installed; only `hercules scan` and security steps need it",
            fix="brew install nuclei (or `hercules scan --install`), then `hercules scan --update-templates`",
        )
    return CheckResult(
        name="scanner",
        status="ok",
        detail=f"nuclei {version or '(version unknown)'} at {binary}",
    )


def _memory_check(selection: TierSelection) -> CheckResult:
    memory = "unknown memory" if selection.memory_bytes is None else f"{selection.memory_bytes / GIB:.1f} GiB"
    how = "auto" if selection.auto else "set by llm.tier"
    detail = f"{memory} -> tier {selection.profile.name} ({how})"
    if selection.warnings:
        return CheckResult(
            name="memory",
            status="warn",
            detail=f"{detail}; {' '.join(selection.warnings)}",
        )
    return CheckResult(name="memory", status="ok", detail=detail)


def _egress_check(endpoint: Endpoint, settings: Settings) -> CheckResult:
    policy = settings.egress.policy
    try:
        check_egress(endpoint, policy)
    except EgressBlocked:
        return CheckResult(
            name="egress",
            status="fail",
            detail=f"egress policy '{policy}' forbids contacting the remote endpoint {redact_url(endpoint.base_url)}; it was not contacted",
            fix="use a local provider, or allow hosted models with `egress: {policy: hybrid-text}` (or hybrid) in .hercules/config.yaml or HERCULES_EGRESS=hybrid-text",
        )
    where = "local" if endpoint.is_local else "remote"
    return CheckResult(
        name="egress",
        status="ok",
        detail=f"policy '{policy}' allows this {where} endpoint",
    )


def _summary(settings: Settings) -> SettingsSummary:
    llm = settings.llm
    return SettingsSummary(
        provider=llm.provider,
        model=llm.model,
        base_url=redact_url(llm.base_url) if llm.base_url else None,
        api_key_env=llm.api_key_env,
        tier=llm.tier,
        egress_policy=settings.egress.policy,
        request_timeout_s=llm.request_timeout_s,
        request_params=dict(llm.request_params),
        headers=dict(llm.headers),
        cache=cache_mode(llm),
        telemetry_enabled=telemetry_enabled(settings),
        project_dir=str(settings.project_dir),
    )


def _write(report: DoctorReport, options: DoctorOptions, out: TextIO, environ: Mapping[str, str]) -> None:
    out.write(report.model_dump_json(indent=2) + "\n" if options.json_output else render_text(report, Style.for_stream(out, environ)))
    out.flush()


def _send_telemetry(
    settings: Settings,
    paths: HerculesPaths,
    deps: DoctorDeps,
    report: DoctorReport,
    started: float,
) -> None:
    telemetry = deps.make_telemetry(settings, paths)
    props: dict[str, object] = {"duration_ms": int((time.monotonic() - started) * 1000)}
    if report.tier is not None and report.tier.name != "unsupported":
        props["model_tier"] = report.tier.name
    failed = next((check.name for check in report.checks if check.status == "fail"), None)
    if failed is not None:
        props["error_class"] = "".join(part.title() for part in failed.split("_")) + "Failed"
    telemetry.emit("doctor_completed", **props)
    telemetry.close()
