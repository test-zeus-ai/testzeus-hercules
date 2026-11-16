"""Effective Hercules configuration.

``Settings.load`` assembles one immutable ``Settings`` object from, in order of
increasing precedence: model defaults, ``<home>/config.yaml``,
``<project>/.hercules/config.yaml``, ``HERCULES_*`` environment variables and
explicit overrides (CLI flags). Secrets are never stored here; configuration
only names the environment variable that holds a key (``api_key_env``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Provider = Literal["ollama", "mlx-vlm", "mlx", "lmstudio", "llamacpp", "vllm", "openai-compatible", "litellm", "openrouter", "openai", "anthropic", "azure"]
LLMCache = Literal["allow", "bypass"]
Tier = Literal["auto", "floor", "standard", "recommended", "comfortable"]
DeciderBackend = Literal["local", "jev", "clef"]
DeciderTransport = Literal["openrouter", "typesafe"]
EgressPolicy = Literal["local", "hybrid-text", "hybrid"]

CONFIG_FILENAME = "config.yaml"
PROJECT_DIRNAME = ".hercules"

_ENV_VAR_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
# Request fields Hercules sets itself: llm.request_params may not set them.
RESERVED_REQUEST_PARAMS = frozenset({"model", "messages", "stream", "response_format", "tools", "tool_choice"})
_JSON_FIELDS = frozenset({"request_params", "headers"})
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FALSE_FLAGS = frozenset({"0", "false", "no", "off"})
_BOOL_FIELDS = frozenset({"enabled", "zdr", "strict", "coverage"})
_SECRET_KEY = re.compile(r"(?:^|[_-])(?:api[_-]?key|apikey|token|secret|password|passwd|credentials?|private[_-]?key|access[_-]?key)(?:$|[_-])", re.IGNORECASE)

# Environment variable -> (section, field).
_ENV_VARS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "HERCULES_LLM_PROVIDER": ("llm", "provider"),
        "HERCULES_LLM_MODEL": ("llm", "model"),
        "HERCULES_LLM_BASE_URL": ("llm", "base_url"),
        "HERCULES_LLM_API_KEY_ENV": ("llm", "api_key_env"),
        "HERCULES_LLM_TIER": ("llm", "tier"),
        "HERCULES_LLM_TIMEOUT_S": ("llm", "request_timeout_s"),
        "HERCULES_LLM_REQUEST_PARAMS": ("llm", "request_params"),
        "HERCULES_LLM_HEADERS": ("llm", "headers"),
        "HERCULES_LLM_CACHE": ("llm", "cache"),
        "HERCULES_DECIDER_BACKEND": ("decider", "backend"),
        "HERCULES_DECIDER_TRANSPORT": ("decider", "jev_transport"),
        "HERCULES_DECIDER_ZDR": ("decider", "zdr"),
        "HERCULES_CLEF_URL": ("decider", "clef_url"),
        "HERCULES_CLEF_MODEL": ("decider", "clef_model"),
        "HERCULES_EGRESS": ("egress", "policy"),
        "HERCULES_TELEMETRY": ("telemetry", "enabled"),
        "HERCULES_UI_HOST": ("ui", "host"),
        "HERCULES_UI_PORT": ("ui", "port"),
        "HERCULES_PLAN_STRICT": ("plan", "strict"),
        "HERCULES_PLAN_REVIEW": ("plan", "review"),
        "HERCULES_PLAN_COVERAGE": ("plan", "coverage"),
        "HERCULES_APPIUM_URL": ("mobile", "appium_url"),
    }
)


class SettingsError(ValueError):
    """Raised when configuration cannot be loaded; the message names its source."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LLMSettings(_Section):
    provider: Provider = "ollama"
    model: str = "auto"
    base_url: str | None = None
    api_key_env: str | None = Field(default=None, pattern=_ENV_VAR_NAME)
    tier: Tier = "auto"
    request_timeout_s: float = Field(default=300.0, gt=0)
    max_retries: int = Field(default=1, ge=0)
    # Extra fields sent with every chat request to the model server (and doctor's probes), for servers that need
    # them, for example {"reasoning_effort": "none"} for a reasoning model that should answer directly. Hercules'
    # own fields (model, messages, stream, response_format, tools, ...) are not overridden; secrets are refused.
    request_params: dict[str, Any] = Field(default_factory=dict)
    # Extra request headers for proxies and gateways: header name -> the NAME of the environment variable that holds
    # its value (never the value), for example {"x-api-key": "GATEWAY_KEY"}.
    headers: dict[str, str] = Field(default_factory=dict)
    # A caching proxy's response cache: bypass (ask for fresh answers) or allow. Unset: bypass for litellm, else allow.
    cache: LLMCache | None = None

    @field_validator("headers")
    @classmethod
    def _headers_name_variables(cls, value: dict[str, str]) -> dict[str, str]:
        for name, variable in value.items():
            if not _HEADER_NAME.match(name):
                raise ValueError(f"'{name}' is not a valid header name")
            if not isinstance(variable, str) or not re.match(_ENV_VAR_NAME, variable):
                raise ValueError(f"header '{name}' must name the environment variable that holds its value, not the value")
        return value

    @field_validator("request_params")
    @classmethod
    def _no_reserved_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = sorted(set(value) & RESERVED_REQUEST_PARAMS)
        if reserved:
            raise ValueError(f"{', '.join(reserved)} cannot be set here: Hercules sets {'it' if len(reserved) == 1 else 'them'} on each request")
        return value


class DeciderSettings(_Section):
    # local: the local model decides (pure mode); jev: JEV decides, the local model does the rest
    # (hybrid mode); clef: a local Clef server decides, the local model does the rest (pure mode);
    # unset: chosen per run (hybrid when the JEV key is set and egress allows it, else pure).
    backend: DeciderBackend | None = None
    jev_transport: DeciderTransport = "openrouter"
    model: str | None = None
    api_key_env: str | None = Field(default=None, pattern=_ENV_VAR_NAME)
    # OpenRouter only: ask for providers that keep no data and do not train on it
    # (``provider: {zdr: true, data_collection: deny}`` on every JEV request).
    zdr: bool = True
    # No longer used, accepted so existing configs still load: every page element is offered to the
    # decider in page order (nothing is trimmed by matching words); a pick larger than one request
    # is split into parts by the decider, and a page with more than 254 elements is asked in groups.
    max_candidates: int = Field(default=254, ge=1, le=254)
    # A local Clef or Clef-flash server (a JEV-compatible decision API), for example
    # http://127.0.0.1:30000. In pure mode it makes the decisions when it is reachable.
    clef_url: str | None = None
    clef_model: str | None = None
    # Send screenshots to the Clef server (``assets`` map, as SystemOne takes them). Off until verified.
    clef_images: bool = False


class EgressSettings(_Section):
    # hybrid: page text, and screenshots when JEV is unsure, may go to JEV (hosted);
    # hybrid-text: text only; local: nothing leaves the machine.
    policy: EgressPolicy = "hybrid"
    # Other machines (host names or IP addresses, no scheme or port) whose model or Clef server counts as local, for
    # example a GPU box on the LAN. Only this machine (loopback) counts as local unless a host is listed here.
    local_hosts: tuple[str, ...] = ()

    @field_validator("local_hosts")
    @classmethod
    def _plain_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        hosts = []
        for host in value:
            name = host.strip().lower().rstrip(".")
            if name.startswith("[") and name.endswith("]"):
                name = name[1:-1]
            if not name or not re.fullmatch(r"[a-z0-9._:-]+", name) or "://" in host or (":" in name and name.count(":") == 1):
                raise ValueError(f"egress.local_hosts: {host!r} must be a host name or IP address, without scheme, port or path")
            hosts.append(name)
        return tuple(hosts)


class TelemetrySettings(_Section):
    enabled: bool = True


class UISettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=7373, ge=1, le=65535)


class PlanSettings(_Section):
    """The plan phase (see docs/guide/plans.md): strict mode (lint errors stop a run before the browser starts; an inferred
    criterion still unmet after recovery makes its step unverified), the default review of daemon runs (``ask`` waits for
    the plan's approval; ``errors`` waits only for a plan with lint errors) and the coverage audit."""

    strict: bool = False
    review: Literal["auto", "ask", "errors"] = "errors"
    coverage: bool = True


class ExploreSettings(_Section):
    """Exploration sessions (``hercules explore``): the swarm size used when the user gives none, the machine's guard
    rail (a larger size needs --allow-more-workers), the default time, and the price assumed per hosted decision for the
    pre-flight estimate."""

    workers: int = Field(default=2, ge=1, le=64)
    reproducers: int = Field(default=1, ge=1, le=16)
    max_workers: int = Field(default=8, ge=1, le=64)
    default_time_s: int = Field(default=1800, ge=60, le=86_400)
    cost_per_decision_usd: float = Field(default=0.0005, ge=0)


class DeviceCloudSettings(_Section):
    """One device cloud: the environment variables that hold its user name and access key (never the values), and,
    to override the defaults, its WebDriver hub and REST API (app upload, session status) and data center."""

    username_env: str = Field(default="BROWSERSTACK_USERNAME", pattern=_ENV_VAR_NAME)
    access_key_env: str = Field(default="BROWSERSTACK_ACCESS_KEY", pattern=_ENV_VAR_NAME)
    hub_url: str | None = Field(default=None, pattern=r"^https?://[^\s@]+$")
    api_url: str | None = Field(default=None, pattern=r"^https?://[^\s@]+$")
    region: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")


class SauceLabsSettings(DeviceCloudSettings):
    username_env: str = Field(default="SAUCE_USERNAME", pattern=_ENV_VAR_NAME)
    access_key_env: str = Field(default="SAUCE_ACCESS_KEY", pattern=_ENV_VAR_NAME)
    region: str | None = Field(default="us-west-1", pattern=r"^[a-z0-9-]{1,40}$")


class TestMuSettings(DeviceCloudSettings):
    """TestMu AI (formerly LambdaTest)."""

    __test__ = False  # not a pytest test class
    username_env: str = Field(default="LT_USERNAME", pattern=_ENV_VAR_NAME)
    access_key_env: str = Field(default="LT_ACCESS_KEY", pattern=_ENV_VAR_NAME)


class MobileSettings(_Section):
    """Native app tests (see docs/guide/mobile-testing.md): the local Appium server cases use unless they name another
    (``appium_url``) or a device cloud, how long a device session may take to start, and the device clouds. The device
    cloud entries (credential variable names, hubs, region) are also used for web tests run on a cloud (docs/guide/clouds.md)."""

    appium_url: str = Field(default="http://127.0.0.1:4723", pattern=r"^https?://[^\s@]+$")
    session_timeout_s: float = Field(default=300.0, gt=0)
    command_timeout_s: float = Field(default=120.0, gt=0)
    browserstack: DeviceCloudSettings = Field(default_factory=DeviceCloudSettings)
    saucelabs: SauceLabsSettings = Field(default_factory=SauceLabsSettings)
    testmu: TestMuSettings = Field(default_factory=TestMuSettings)


_SECTIONS: Mapping[str, type[_Section]] = MappingProxyType(
    {
        "llm": LLMSettings,
        "decider": DeciderSettings,
        "egress": EgressSettings,
        "telemetry": TelemetrySettings,
        "ui": UISettings,
        "plan": PlanSettings,
        "mobile": MobileSettings,
        "explore": ExploreSettings,
    }
)


class Settings(BaseModel):
    """Immutable effective configuration for one Hercules process or run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    llm: LLMSettings = Field(default_factory=LLMSettings)
    decider: DeciderSettings = Field(default_factory=DeciderSettings)
    egress: EgressSettings = Field(default_factory=EgressSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    ui: UISettings = Field(default_factory=UISettings)
    plan: PlanSettings = Field(default_factory=PlanSettings)
    mobile: MobileSettings = Field(default_factory=MobileSettings)
    explore: ExploreSettings = Field(default_factory=ExploreSettings)
    home_dir: Path
    project_dir: Path

    @classmethod
    def load(
        cls,
        project_dir: Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> Settings:
        """Load settings from config files, the environment and overrides.

        ``env`` defaults to the process environment; when given, the process
        environment is not consulted for configuration.
        """
        environ: Mapping[str, str] = os.environ if env is None else env
        remaining = dict(overrides or {})
        home_override = remaining.pop("home_dir", None)
        project_override = remaining.pop("project_dir", None)

        home = _absolute(home_override or environ.get("HERCULES_HOME") or Path(environ.get("HOME") or Path.home()) / ".hercules", environ)
        project = _absolute(project_override or project_dir or Path.cwd(), environ)

        layers = [
            _file_layer(home / CONFIG_FILENAME),
            _file_layer(project / PROJECT_DIRNAME / CONFIG_FILENAME),
            _env_layer(environ),
            _mapping_layer(remaining, "overrides"),
        ]
        merged: dict[str, Any] = {}
        for layer in layers:
            merged = _deep_merge(merged, layer)
        try:
            return cls(**merged, home_dir=home, project_dir=project)
        except ValidationError as exc:
            raise SettingsError(f"invalid settings: {_describe(exc, prefix='')}") from None


def _absolute(value: str | os.PathLike[str], environ: Mapping[str, str]) -> Path:
    text = os.fspath(value)
    if text == "~" or text.startswith("~/"):
        home = environ.get("HOME")
        text = os.path.join(home, text[2:]) if home else os.path.expanduser(text)
    return Path(os.path.normpath(os.path.abspath(text)))


def _file_layer(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"{path}: cannot read config file ({exc.__class__.__name__})") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SettingsError(f"{path}: malformed YAML{_yaml_location(exc)}") from None
    return _mapping_layer({} if data is None else data, str(path))


def _yaml_location(exc: yaml.YAMLError) -> str:
    # Only report the position: PyYAML's full message quotes file content.
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None)
    where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
    return f"{where}: {problem}" if problem else where


def _mapping_layer(data: Any, source: str) -> dict[str, Any]:
    """Validate one partial configuration layer and return it as plain dicts."""
    if not isinstance(data, Mapping):
        raise SettingsError(f"{source}: expected a mapping of settings sections, got {type(data).__name__}")
    _reject_secrets(data, source, prefix="")
    layer: dict[str, Any] = {}
    for key, value in data.items():
        section = _SECTIONS.get(str(key))
        if section is None:
            raise SettingsError(f"{source}: unknown key '{key}' (expected one of: {', '.join(_SECTIONS)})")
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise SettingsError(f"{source}: '{key}' must be a mapping, got {type(value).__name__}")
        try:
            section.model_validate(value)
        except ValidationError as exc:
            raise SettingsError(f"{source}: {_describe(exc, prefix=str(key))}") from None
        layer[str(key)] = dict(value)
    return layer


def _reject_secrets(data: Any, source: str, prefix: str) -> None:
    if prefix in _VARIABLE_MAPS and isinstance(data, Mapping):
        _check_variable_names(data, source, prefix)
        return
    if isinstance(data, Mapping):
        for key, value in data.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if _looks_like_secret(str(key)) and value not in (None, ""):
                raise SettingsError(
                    f"{source}: '{dotted}' looks like a secret. Hercules never stores secrets in configuration; "
                    "keep the secret in an environment variable and set 'api_key_env' to that variable's name "
                    "(for example llm.api_key_env: OPENROUTER_API_KEY)."
                )
            _reject_secrets(value, source, dotted)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _reject_secrets(item, source, f"{prefix}[{index}]")


# Settings whose values name environment variables (llm.headers: header -> variable): checked for values, not key names.
_VARIABLE_MAPS = frozenset({"llm.headers", "headers"})


def _check_variable_names(data: Mapping[Any, Any], source: str, prefix: str) -> None:
    for key, value in data.items():
        if not isinstance(value, str) or not re.match(_ENV_VAR_NAME, value):
            raise SettingsError(
                f"{source}: '{prefix}.{key}' must be the name of an environment variable that holds the header's value, never the value itself " "(for example llm.headers: {x-api-key: GATEWAY_KEY})"
            )


def _looks_like_secret(key: str) -> bool:
    return not key.lower().endswith("_env") and _SECRET_KEY.search(key) is not None


def _env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    layer: dict[str, dict[str, Any]] = {}
    for var, (section, field) in _ENV_VARS.items():
        raw = environ.get(var, "").strip()
        if not raw:
            continue
        value: Any = raw.lower() not in _FALSE_FLAGS if field in _BOOL_FIELDS else raw
        if field in _JSON_FIELDS:
            try:
                value = json.loads(raw)
            except ValueError:
                raise SettingsError(f'environment variable {var}: expected a JSON object, for example {{"reasoning_effort": "none"}}') from None
            if not isinstance(value, dict):
                raise SettingsError(f'environment variable {var}: expected a JSON object, for example {{"reasoning_effort": "none"}}')
            _reject_secrets(value, f"environment variable {var}", prefix=field if field == "headers" else "")
        try:
            _SECTIONS[section].model_validate({field: value})
        except ValidationError as exc:
            raise SettingsError(f"environment variable {var}: {_describe(exc, prefix=section)}") from None
        layer.setdefault(section, {})[field] = value
    if "enabled" not in layer.get("telemetry", {}) and _do_not_track(environ):
        layer.setdefault("telemetry", {})["enabled"] = False
    return layer


def _do_not_track(environ: Mapping[str, str]) -> bool:
    """The cross-tool ``DO_NOT_TRACK`` convention: any value other than empty or a false flag turns telemetry off.

    ``HERCULES_TELEMETRY`` wins when both are set.
    """
    raw = environ.get("DO_NOT_TRACK", "").strip().lower()
    return bool(raw) and raw not in _FALSE_FLAGS


def _describe(exc: ValidationError, prefix: str) -> str:
    """Summarise a validation error by key without echoing input values."""
    problems = []
    for error in exc.errors():
        key = ".".join(str(part) for part in ((prefix,) if prefix else ()) + tuple(error["loc"]))
        if error["type"] == "extra_forbidden":
            problems.append(f"unknown key '{key}'")
        else:
            problems.append(f"invalid value for '{key}': {error['msg']}")
    return "; ".join(problems)


def _deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
