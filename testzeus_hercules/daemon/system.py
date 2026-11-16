"""``GET /api/models``: model server, cached capabilities and which run modes are available.

Key values are never read into the report: only whether the named variable is set.
"""

from __future__ import annotations

import io
import json
from typing import Any

from testzeus_hercules.cli.keys import find_key, with_model_key
from testzeus_hercules.daemon.config import DaemonConfig
from testzeus_hercules.llm.providers import model_key_variable, EgressBlocked, EndpointError, check_egress, redact_url, resolve_endpoint

# Same default as the engine factory (``engine.factory.DEFAULT_JEV_KEY_ENV``); kept here so the
# daemon does not import the engine.
DEFAULT_JEV_KEY_ENV = "OPENROUTER_API_KEY"
_HOSTED_EGRESS = ("hybrid-text", "hybrid")


def jev_key_env(config: DaemonConfig) -> str:
    return config.settings.decider.api_key_env or DEFAULT_JEV_KEY_ENV


def _capabilities(config: DaemonConfig) -> list[dict[str, Any]]:
    try:
        data = json.loads(config.paths.capabilities_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return []
    found = []
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        base_url = str(key).split("|", 1)[0]
        found.append(
            {
                "base_url": redact_url(base_url),
                **{name: entry.get(name) for name in ("model_id", "server_version", "context_tokens", "supports_tools", "supports_tool_choice", "supports_json_schema", "image_mode")},
            }
        )
    return found


def models_report(config: DaemonConfig, probe: bool = False) -> dict[str, Any]:
    settings = config.settings
    policy = settings.egress.policy
    report: dict[str, Any] = {
        "provider": settings.llm.provider,
        "model": settings.llm.model,
        "tier": settings.llm.tier,
        "egress_policy": policy,
        "parallel_runs": config.parallel_runs,
        "capabilities": _capabilities(config),
        "endpoint": None,
    }
    pure: dict[str, Any] = {"available": False, "reason": None}
    try:
        endpoint = resolve_endpoint(settings.llm, with_model_key(config.env, config.paths.home_dir, model_key_variable(settings.llm)), settings.egress.local_hosts)
    except EndpointError as exc:
        pure["reason"] = str(exc)
    else:
        report["endpoint"] = {"provider": endpoint.provider, "base_url": redact_url(endpoint.base_url), "is_local": endpoint.is_local}
        try:
            check_egress(endpoint, policy)
        except EgressBlocked:
            pure["reason"] = f"egress policy '{policy}' forbids the remote model endpoint"
        else:
            pure["available"] = True
            pure["reason"] = "the local model server is not probed here; run `hercules doctor` or pass ?probe=true" if not probe else None
    key_env = jev_key_env(config)
    # The same lookup the worker uses: the daemon's environment, else the key file in the Hercules home.
    _key, key_status = find_key(config.env, config.paths.home_dir, key_env)
    key_set = key_status.found
    hybrid: dict[str, Any] = {
        "available": key_set and pure["available"],
        "key_env": key_env,
        "key_set": key_set,
        "key_source": key_status.source,
        "default_egress_allows": policy in _HOSTED_EGRESS,
        "reason": None,
    }
    if not key_set:
        hybrid["reason"] = f"set {key_env} in the daemon's environment or run `hercules setup` to save it in {key_status.path} (it is never returned)"
        if key_status.problem:
            hybrid["reason"] += f"; {key_status.problem}"
    elif policy not in _HOSTED_EGRESS:
        hybrid["reason"] = "the default egress policy is 'local': pass egress 'hybrid-text' or 'hybrid' with the run"
    report["modes"] = {"pure": pure, "hybrid": hybrid}
    if probe:
        report["doctor"] = _doctor(config)
    return report


def _doctor(config: DaemonConfig) -> Any:
    from testzeus_hercules.cli.doctor import DoctorDeps, DoctorOptions, run_doctor

    out = io.StringIO()
    run_doctor(DoctorOptions(project_dir=config.settings.project_dir, json_output=True), env=config.env, deps=DoctorDeps(), out=out)
    try:
        return json.loads(out.getvalue())
    except ValueError:
        return {"status": "fail", "detail": "doctor produced no report"}
