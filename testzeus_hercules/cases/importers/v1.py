"""Import a v1 project folder: ``opt/input/*.feature``, ``opt/test_data/*`` and ``.env``.

* Every scenario becomes a TestCase in ``.hercules/cases/<id>.json``; a Scenario
  Outline becomes one case per Examples row.
* v1 gave every scenario all of its test data. Flat key/value files (a JSON object
  of scalars, or ``KEY=VALUE`` lines) become ``test_data`` entries on every case;
  keys that name a credential become secret references by name (the value is never
  written). Other files are copied to ``.hercules/data/`` and attached as
  ``<name>_file`` entries to the cases whose steps mention them.
* A security feature (its name or tags say security, like v1's ``SecTesting``) becomes
  security scan steps: each step that names a topic (CVEs, exposure, XSS, SQL injection, ...)
  scans for that template tag, the ``Then`` after it is its expected outcome, and the base
  URL step is dropped (the environment holds the base URL). Plain-words scan steps
  (``When I run a security scan for "cve"``) are read in every feature.
* ``.env`` becomes ``.hercules/envs/<name>.json`` (the daemon's environment format):
  the base URL plus the names of the secrets the cases use. v1 engine settings
  (model, browser and logging switches) are not carried over, and no value of a
  secret is ever copied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from testzeus_hercules.cases.gherkin import GherkinError
from testzeus_hercules.cases.importers.common import (
    ImporterError,
    ImportReport,
    PlannedFile,
    case_file,
    data_key,
    looks_secret,
    relative_to,
    secret_name,
    sensitive_file,
    unique_case_id,
    write_plan,
)
from testzeus_hercules.cases.importers.gherkin_multi import FeatureSpec, ScenarioSpec, parse_feature
from testzeus_hercules.cases.models import BASE_URL_PATTERN, SECRET_NAME_PATTERN, EnvironmentRef, MobileApp, SecretRef, TestCase, TestDataValue
from testzeus_hercules.cases.scan_phrases import fold_scan_steps, is_security_scenario, v1_security_steps

IMPORT_TAG = "imported:v1"
DATA_DIRNAME = "data"
_MAX_FEATURE_BYTES = 2 * 1024 * 1024
_SNIFF_BYTES = 64 * 1024
_MAX_SCAN_NODES = 100_000
_MAX_DATA_BYTES = 20 * 1024 * 1024
_MAX_FILES = 500
_FLAT_SUFFIXES = frozenset({".json", ".txt", ".env", ".properties"})
_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)$")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_ENV_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_BASE_URL_KEYS = ("BASE_URL", "APP_URL", "APPLICATION_URL", "TARGET_URL", "SITE_URL", "URL")
# v1 engine configuration: replaced by v2 settings, never an app variable.
_ENGINE_PREFIXES = (
    "LLM_",
    "AGENTS_LLM_",
    "LANGSMITH_",
    "LANGCHAIN_",
    "PORTKEY_",
    "HF_",
    "TOKENIZERS_",
    "GEO_",
    "CDP_",
    "BROWSER_",
    "MOBILE_",
    "APPIUM_",
    "HERCULES_",
)
_ENGINE_KEYS = frozenset(
    {
        "MODE",
        "HEADLESS",
        "RECORD_VIDEO",
        "TAKE_SCREENSHOTS",
        "CAPTURE_NETWORK",
        "LOAD_EXTRA_TOOLS",
        "EXECUTE_BULK",
        "ENABLE_TELEMETRY",
        "RUN_DEVICE",
        "PROJECT_SOURCE_ROOT",
        "REUSE_VECTOR_DB",
        "TIMEOUT",
        "LOG_LEVEL",
        "ENABLE_PLAYWRIGHT_TRACING",
    }
)


class _EnvironmentFile(BaseModel):
    """Mirror of the daemon's environment file (``.hercules/envs/<name>.json``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(pattern=_ENV_NAME.pattern)]
    base_url: Annotated[str, StringConstraints(pattern=BASE_URL_PATTERN)] | None = None
    device: str | None = None
    mobile: MobileApp | None = None
    secret_names: list[Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN)]] = Field(default_factory=list, max_length=50)


@dataclass(frozen=True)
class V1ImportOptions:
    source_dir: Path
    project_dir: Path
    dry_run: bool = False
    force: bool = False
    base_url: str | None = None
    env_name: str = "default"
    env_file: Path | None = None


@dataclass
class _Layout:
    root: Path
    input_dir: Path
    data_dir: Path | None
    env_files: list[Path]


@dataclass
class _DataFiles:
    shared: dict[str, TestDataValue] = field(default_factory=dict)
    copied: dict[str, str] = field(default_factory=dict)  # file name -> project-relative path
    planned: list[PlannedFile] = field(default_factory=list)


def import_v1(options: V1ImportOptions) -> ImportReport:
    """Convert the v1 project at ``options.source_dir``; raises :class:`ImporterError` when it is not one."""
    if not _ENV_NAME.fullmatch(options.env_name):
        raise ImporterError(f"invalid environment name {options.env_name!r}: use lowercase letters, digits, '_' or '-'")
    layout = _find_layout(options.source_dir, options.env_file)
    report = ImportReport(title=f"Import of the v1 project {layout.root}", dry_run=options.dry_run)
    data = _read_test_data(layout, report)
    features = _read_features(layout, report)
    env_vars = _read_env_files(layout, report)
    base_url = _base_url(options.base_url, env_vars, features, report)
    env_secrets = _env_secrets(env_vars, report)
    for name in data.shared.values():
        if isinstance(name, SecretRef):
            report.add_secret(name.secret)
    target = _device_target(env_vars, report)
    native = "mobile" in target
    env_ref = options.env_name if base_url is not None or native else None
    cases = _build_cases(layout, features, data, env_ref, report)
    planned = [*data.planned, *cases]
    if base_url is not None or native:
        secrets = sorted({*env_secrets, *(value.secret for value in data.shared.values() if isinstance(value, SecretRef))})
        planned.append(_environment_file(options.env_name, base_url, secrets, layout, target))
    else:
        report.skip(".env", "no environment written: no base URL found (pass --base-url, or set BASE_URL in .env)")
    for name in env_secrets:
        report.add_secret(name)
    write_plan(options.project_dir, planned, report, dry_run=options.dry_run, force=options.force)
    return report


# --------------------------------------------------------------------------- layout


def _find_layout(source_dir: Path, env_file: Path | None) -> _Layout:
    root = source_dir.expanduser().resolve()
    if not root.is_dir():
        raise ImporterError(f"{source_dir} is not a directory")
    for base in (root / "opt", root):
        if (base / "input").is_dir():
            data_dir = base / "test_data"
            env_files = [env_file] if env_file is not None else [path for path in dict.fromkeys((root / ".env", base / ".env")) if path.is_file()]
            return _Layout(root=root, input_dir=base / "input", data_dir=data_dir if data_dir.is_dir() else None, env_files=env_files)
    raise ImporterError(f"no v1 input folder in {source_dir}: expected opt/input/*.feature (or input/*.feature)")


def _regular_files(directory: Path, report: ImportReport, root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(directory.iterdir()):
        shown = relative_to(path, root)
        if path.name.startswith("."):
            continue
        if path.is_symlink():
            report.skip(shown, "symbolic link; not followed")
        elif path.is_dir():
            report.skip(shown, "sub-folder; only top-level files are imported")
        elif len(found) >= _MAX_FILES:
            report.skip(shown, f"more than {_MAX_FILES} files in {directory.name}/")
        else:
            found.append(path)
    return found


# --------------------------------------------------------------------------- features


def _read_features(layout: _Layout, report: ImportReport) -> list[tuple[Path, FeatureSpec]]:
    features: list[tuple[Path, FeatureSpec]] = []
    for path in _regular_files(layout.input_dir, report, layout.root):
        shown = relative_to(path, layout.root)
        if path.suffix.lower() != ".feature":
            report.skip(shown, "not a .feature file")
            continue
        if path.stat().st_size > _MAX_FEATURE_BYTES:
            report.skip(shown, f"larger than {_MAX_FEATURE_BYTES // (1024 * 1024)} MB")
            continue
        try:
            features.append((path, parse_feature(path.read_text(encoding="utf-8"), source_name=shown)))
        except UnicodeDecodeError:
            report.skip(shown, "not UTF-8 text")
        except (GherkinError, ValidationError) as exc:
            report.skip(shown, _problem(exc))
    return features


def _build_cases(layout: _Layout, features: list[tuple[Path, FeatureSpec]], data: _DataFiles, env_ref: str | None, report: ImportReport) -> list[PlannedFile]:
    taken: set[str] = set()
    planned: list[PlannedFile] = []
    for path, feature in features:
        shown = relative_to(path, layout.root)
        for scenario in feature.scenarios:
            source = f"{shown}:{scenario.line}"
            if not scenario.steps:
                report.skip(source, f"scenario {scenario.name!r} has no steps")
                continue
            try:
                case = _case(feature, scenario, source, data, env_ref, taken)
            except ValidationError as exc:
                report.skip(source, f"scenario {scenario.name!r} is not a valid test case: {_problem(exc)}")
                continue
            planned.append(case_file(case, source))
    return planned


def _case(feature: FeatureSpec, scenario: ScenarioSpec, source: str, data: _DataFiles, env_ref: str | None, taken: set[str]) -> TestCase:
    base_id = scenario.name or "scenario"
    title = scenario.name or "Scenario"
    if scenario.outline:
        base_id = f"{base_id}-{scenario.example_index}"
        if scenario.example:
            title = f"{title} ({', '.join(f'{key}={value}' for key, value in scenario.example.items())})"
    texts = " ".join(step.expected or step.action for step in scenario.steps)
    test_data: dict[str, TestDataValue] = dict(data.shared)
    for name, relative in data.copied.items():
        if name in texts:
            key = data_key(f"{Path(name).stem}_file")
            if key is not None and key not in test_data:
                test_data[key] = relative
    description = " ".join(part for part in (f"Imported from v1 {source}.", feature.name, feature.description) if part)
    tags = list(dict.fromkeys([*scenario.tags, IMPORT_TAG]))
    steps = fold_scan_steps(scenario.steps)
    if is_security_scenario((feature.name, scenario.name), [*feature.tags, *scenario.tags]):
        steps = v1_security_steps(steps, context=scenario.name) or steps
    return TestCase(
        id=unique_case_id(base_id, taken),
        title=title[:300],
        description=description,
        tags=tags,
        environment=EnvironmentRef(ref=env_ref),
        test_data=test_data,
        steps=steps,
    )


# --------------------------------------------------------------------------- test data


def _read_test_data(layout: _Layout, report: ImportReport) -> _DataFiles:
    data = _DataFiles()
    if layout.data_dir is None:
        return data
    for path in _regular_files(layout.data_dir, report, layout.root):
        shown = relative_to(path, layout.root)
        size = path.stat().st_size
        if size > _MAX_DATA_BYTES:
            report.skip(shown, f"larger than {_MAX_DATA_BYTES // (1024 * 1024)} MB; copy it by hand if a case needs it")
            continue
        pairs = _flat_pairs(path) if path.suffix.lower() in _FLAT_SUFFIXES else None
        if pairs is not None:
            _merge_pairs(pairs, shown, data, report)
            continue
        content = path.read_bytes()
        if sensitive_file(path.name, content[:_SNIFF_BYTES]) or _holds_secret_keys(path, content):
            report.skip(shown, "looks like a credential file (a key, a certificate store, a service account, secrets); not copied: give the cases what they need as secrets")
            continue
        relative = f".hercules/{DATA_DIRNAME}/{path.name}"
        data.copied[path.name] = relative
        data.planned.append(PlannedFile(kind="data", relative=f"{DATA_DIRNAME}/{path.name}", content=content, source=shown))
    return data


def _holds_secret_keys(path: Path, content: bytes) -> bool:
    """True when a structured (JSON) data file holds a credential under any key, at any depth."""
    if path.suffix.lower() != ".json":
        return False
    try:
        parsed = json.loads(content)
    except (ValueError, RecursionError):
        return False
    pending, seen = [parsed], 0
    while pending and seen < _MAX_SCAN_NODES:
        node = pending.pop()
        seen += 1
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (str, int, float)) and not isinstance(value, bool) and looks_secret(str(key), str(value)):
                    return True
                pending.append(value)
        elif isinstance(node, list):
            pending.extend(node)
    return False


def _flat_pairs(path: Path) -> list[tuple[str, str]] | None:
    """Key/value pairs of a flat data file, or None when the file is anything else."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            return None
        if not isinstance(parsed, dict) or not all(isinstance(value, (str, int, float, bool)) or value is None for value in parsed.values()):
            return None
        return [(str(key), _scalar(value)) for key, value in parsed.items() if value is not None]
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    matches = [_ENV_LINE.match(line) for line in lines]
    if not lines or not all(matches):
        return None
    return [(match.group(1), _unquote(match.group(2))) for match in matches if match is not None]


def _merge_pairs(pairs: list[tuple[str, str]], shown: str, data: _DataFiles, report: ImportReport) -> None:
    for raw_key, value in pairs:
        key = data_key(raw_key)
        if key is None:
            report.skip(f"{shown}: {raw_key}", "key is empty or reserved for a built-in placeholder")
            continue
        if key in data.shared:
            report.skip(f"{shown}: {raw_key}", f"duplicate test data key {key!r}; the first value was kept")
            continue
        data.shared[key] = SecretRef(secret=secret_name(raw_key)) if looks_secret(raw_key, value) else _plain(value)


def _scalar(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _plain(value: str) -> str:
    # ``{{`` would read as a placeholder in v2; v1 data never meant that.
    return value.replace("{{", "{ {").replace("}}", "} }")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


# --------------------------------------------------------------------------- environment


def _read_env_files(layout: _Layout, report: ImportReport) -> dict[str, str]:
    variables: dict[str, str] = {}
    for path in layout.env_files:
        shown = relative_to(path, layout.root)
        if not path.is_file():
            raise ImporterError(f"environment file {path} not found")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            report.skip(shown, f"cannot read ({type(exc).__name__})")
            continue
        for line in lines:
            match = _ENV_LINE.match(line.strip())
            if match is not None and not line.strip().startswith("#"):
                variables.setdefault(match.group(1), _unquote(match.group(2)))
    return variables


def _base_url(explicit: str | None, env_vars: dict[str, str], features: list[tuple[Path, FeatureSpec]], report: ImportReport) -> str | None:
    if explicit is not None:
        if not _usable_url(explicit):
            raise ImporterError("--base-url must be an http(s) URL with a host and no user name or password")
        return explicit
    for key in _BASE_URL_KEYS:
        value = env_vars.get(key)
        if value:
            if _usable_url(value):
                return value
            report.skip(f".env: {key}", "not an http(s) URL without credentials; not used as the base URL")
    for _, feature in features:
        for scenario in feature.scenarios:
            for step in scenario.steps:
                match = _URL.search(step.expected or step.action)
                if match is not None and _usable_url(match.group(0)):
                    parts = urlsplit(match.group(0))
                    origin = f"{parts.scheme}://{parts.netloc}"
                    report.notes.append(f"base URL {origin} taken from the first URL in the feature files")
                    return origin
    return None


def _usable_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and parts.username is None and parts.password is None and re.match(BASE_URL_PATTERN, value) is not None


def _env_secrets(env_vars: dict[str, str], report: ImportReport) -> list[str]:
    engine = sorted(key for key in env_vars if _is_engine_setting(key))
    if engine:
        report.skip(".env", f"{len(engine)} v1 engine setting(s) not carried over (configure v2 in .hercules/config.yaml and check with `hercules doctor`): {', '.join(engine)}")
    secrets: list[str] = []
    plain: list[str] = []
    for key in env_vars:
        if _is_engine_setting(key) or key in _BASE_URL_KEYS:
            continue
        if looks_secret(key, env_vars[key]):
            secrets.append(secret_name(key))
        else:
            plain.append(key)
    if plain:
        report.skip(".env", f"plain variable(s) not carried over (add them to a case's test_data if a step needs them): {', '.join(sorted(plain))}")
    return sorted(set(secrets))


def _is_engine_setting(key: str) -> bool:
    upper = key.upper()
    return upper in _ENGINE_KEYS or upper.startswith(_ENGINE_PREFIXES)


def _environment_file(name: str, base_url: str | None, secrets: list[str], layout: _Layout, target: dict[str, Any] | None = None) -> PlannedFile:
    env = _EnvironmentFile(name=name, base_url=base_url, secret_names=secrets[:50], **(target or {}))
    source = ", ".join(relative_to(path, layout.root) for path in layout.env_files) or "feature files"
    return PlannedFile(kind="env", relative=f"envs/{name}.json", content=env.model_dump_json(indent=2, exclude_none=True) + "\n", source=source)


def _device_target(env_vars: dict[str, str], report: ImportReport) -> dict[str, Any]:
    """v1's device settings as the environment's ``device`` (RUN_DEVICE, mobile web emulation) or ``mobile`` (DEVICE_MANAGER=appium)."""
    if env_vars.get("DEVICE_MANAGER", "").strip().lower() == "appium":
        return _mobile_app(env_vars, report)
    run_device = env_vars.get("RUN_DEVICE", "").strip()
    if not run_device or run_device.lower() == "desktop":
        return {}
    try:
        return {"device": EnvironmentRef(device=run_device).device}
    except ValidationError:
        report.skip(".env: RUN_DEVICE", f"{run_device!r} is not a Playwright device profile; set environment.device by hand")
        return {}


def _mobile_app(env_vars: dict[str, str], report: ImportReport) -> dict[str, Any]:
    platform = "ios" if env_vars.get("DEVICE_OS", "android").strip().lower().startswith("ios") else "android"
    try:
        extra = json.loads(env_vars.get("APPIUM_CAPABILITIES", "") or "{}")
    except ValueError:
        extra = {}
    extra = extra if isinstance(extra, dict) else {}

    def cap(name: str) -> str | None:
        value = extra.get(f"appium:{name}", extra.get(name))
        return str(value) if value else None

    fields: dict[str, Any] = {
        "platform": platform,
        "package": cap("appPackage") if platform == "android" else None,
        "activity": cap("appActivity") if platform == "android" else None,
        "bundle_id": cap("bundleId") if platform == "ios" else None,
        "device": env_vars.get("DEVICE_NAME") or None,
        "udid": env_vars.get("APPIUM_DEVICE_UUID") or None,
        "platform_version": env_vars.get("APPIUM_PLATFORM_VERSION") or None,
        "appium_url": env_vars.get("APPIUM_SERVER_URL") or None,
    }
    app_path = env_vars.get("APP_PATH") or env_vars.get("APPIUM_APK_PATH")
    if app_path:
        report.skip(".env: APP_PATH", f"the app file {Path(app_path).name!r} was not copied: put it inside the project and set mobile.app in the environment to its path")
    try:
        app = MobileApp.model_validate({key: value for key, value in fields.items() if value is not None})
    except ValidationError as exc:
        report.skip(".env: DEVICE_MANAGER", f"the v1 Appium settings do not describe an app v2 can start ({_problem(exc)}); add mobile to the environment by hand")
        return {}
    return {"mobile": app}


def _problem(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}" for error in exc.errors()[:5])
    return str(exc)
