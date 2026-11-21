"""Tests for the v2 kernel settings loader."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from testzeus_hercules.kernel.settings import LLMSettings, Settings, SettingsError


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def _env(home: Path, **extra: str) -> dict[str, str]:
    return {"HERCULES_HOME": str(home), **extra}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _home_config(home: Path, text: str) -> Path:
    return _write(home / "config.yaml", text)


def _project_config(project: Path, text: str) -> Path:
    return _write(project / ".hercules" / "config.yaml", text)


def test_defaults_without_any_config(home: Path, project: Path) -> None:
    settings = Settings.load(project, env=_env(home))

    assert settings.llm.provider == "ollama"
    assert settings.llm.model == "auto"
    assert settings.llm.base_url is None
    assert settings.llm.api_key_env is None
    assert settings.llm.tier == "auto"
    assert settings.llm.request_timeout_s == 300.0
    assert settings.llm.max_retries == 1
    assert settings.decider.backend is None  # chosen per run: pure or hybrid
    assert settings.decider.jev_transport == "openrouter"
    assert settings.egress.policy == "hybrid", "hybrid mode is the default: page content may go to JEV when a key is set"
    assert settings.telemetry.enabled is True
    assert settings.ui.host == "127.0.0.1"
    assert settings.ui.port == 7373
    assert settings.home_dir == home
    assert settings.project_dir == project


def test_home_defaults_to_dot_hercules_under_home(tmp_path: Path, project: Path) -> None:
    settings = Settings.load(project, env={"HOME": str(tmp_path)})

    assert settings.home_dir == tmp_path / ".hercules"


def test_project_dir_defaults_to_cwd(home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project)

    settings = Settings.load(env=_env(home))

    assert settings.project_dir == project


def test_injected_env_ignores_process_environment(home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERCULES_LLM_MODEL", "from-process")
    monkeypatch.setenv("HERCULES_TELEMETRY", "0")

    settings = Settings.load(project, env=_env(home))

    assert settings.llm.model == "auto"
    assert settings.telemetry.enabled is True


def test_process_environment_used_when_env_not_given(home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERCULES_HOME", str(home))
    monkeypatch.setenv("HERCULES_LLM_MODEL", "from-process")

    settings = Settings.load(project)

    assert settings.home_dir == home
    assert settings.llm.model == "from-process"


def test_layer_precedence(home: Path, project: Path) -> None:
    _home_config(
        home,
        "llm:\n  model: home-model\n  base_url: http://home:1\n  provider: lmstudio\n  max_retries: 3\nui:\n  port: 8001\n",
    )
    _project_config(project, "llm:\n  model: project-model\n  base_url: http://project:2\n  provider: vllm\nui:\n  port: 8002\n")
    env = _env(home, HERCULES_LLM_MODEL="env-model", HERCULES_LLM_BASE_URL="http://env:3")

    settings = Settings.load(project, env=env, overrides={"llm": {"model": "override-model"}})

    assert settings.llm.model == "override-model"
    assert settings.llm.base_url == "http://env:3"
    assert settings.llm.provider == "vllm"
    assert settings.llm.max_retries == 3
    assert settings.ui.port == 8002


def test_overrides_can_relocate_home_and_project(tmp_path: Path, home: Path, project: Path) -> None:
    other_home = tmp_path / "other-home"
    _home_config(other_home, "llm:\n  model: other\n")

    settings = Settings.load(project, env=_env(home), overrides={"home_dir": other_home})

    assert settings.home_dir == other_home
    assert settings.llm.model == "other"


@pytest.mark.parametrize(
    ("var", "value", "section", "field", "expected"),
    [
        ("HERCULES_LLM_PROVIDER", "openrouter", "llm", "provider", "openrouter"),
        ("HERCULES_LLM_MODEL", "gemma-4", "llm", "model", "gemma-4"),
        ("HERCULES_LLM_BASE_URL", "http://127.0.0.1:11434", "llm", "base_url", "http://127.0.0.1:11434"),
        ("HERCULES_LLM_API_KEY_ENV", "OPENROUTER_API_KEY", "llm", "api_key_env", "OPENROUTER_API_KEY"),
        ("HERCULES_LLM_TIER", "comfortable", "llm", "tier", "comfortable"),
        ("HERCULES_LLM_TIMEOUT_S", "42.5", "llm", "request_timeout_s", 42.5),
        ("HERCULES_DECIDER_BACKEND", "clef", "decider", "backend", "clef"),
        ("HERCULES_DECIDER_TRANSPORT", "typesafe", "decider", "jev_transport", "typesafe"),
        ("HERCULES_EGRESS", "hybrid-text", "egress", "policy", "hybrid-text"),
        ("HERCULES_UI_HOST", "localhost", "ui", "host", "localhost"),
        ("HERCULES_UI_PORT", "9000", "ui", "port", 9000),
    ],
)
def test_environment_variables(home: Path, project: Path, var: str, value: str, section: str, field: str, expected: Any) -> None:
    settings = Settings.load(project, env=_env(home, **{var: value}))

    assert getattr(getattr(settings, section), field) == expected


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "No", "off", " Off "])
def test_telemetry_env_opt_out(home: Path, project: Path, value: str) -> None:
    settings = Settings.load(project, env=_env(home, HERCULES_TELEMETRY=value))

    assert settings.telemetry.enabled is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_telemetry_env_opt_in(home: Path, project: Path, value: str) -> None:
    _home_config(home, "telemetry:\n  enabled: false\n")

    settings = Settings.load(project, env=_env(home, HERCULES_TELEMETRY=value))

    assert settings.telemetry.enabled is True


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_do_not_track_turns_telemetry_off(home: Path, project: Path, value: str) -> None:
    settings = Settings.load(project, env=_env(home, DO_NOT_TRACK=value))

    assert settings.telemetry.enabled is False


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_do_not_track_unset_or_false_leaves_telemetry_on(home: Path, project: Path, value: str) -> None:
    settings = Settings.load(project, env=_env(home, DO_NOT_TRACK=value))

    assert settings.telemetry.enabled is True


def test_hercules_telemetry_wins_over_do_not_track(home: Path, project: Path) -> None:
    settings = Settings.load(project, env=_env(home, DO_NOT_TRACK="1", HERCULES_TELEMETRY="1"))

    assert settings.telemetry.enabled is True


def test_telemetry_opt_out_in_yaml(home: Path, project: Path) -> None:
    _project_config(project, "telemetry: {enabled: false}\n")

    assert Settings.load(project, env=_env(home)).telemetry.enabled is False


def test_empty_env_values_are_ignored(home: Path, project: Path) -> None:
    settings = Settings.load(project, env=_env(home, HERCULES_LLM_MODEL="", HERCULES_UI_PORT=""))

    assert settings.llm.model == "auto"
    assert settings.ui.port == 7373


@pytest.mark.parametrize(
    ("var", "value"),
    [("HERCULES_UI_PORT", "not-a-port"), ("HERCULES_LLM_PROVIDER", "bogus"), ("HERCULES_LLM_TIMEOUT_S", "-1"), ("HERCULES_EGRESS", "anything")],
)
def test_invalid_env_value_names_the_variable(home: Path, project: Path, var: str, value: str) -> None:
    with pytest.raises(SettingsError, match=var):
        Settings.load(project, env=_env(home, **{var: value}))


def test_empty_and_missing_yaml_files_are_fine(home: Path, project: Path) -> None:
    _home_config(home, "")
    _project_config(project, "# only a comment\n")

    assert Settings.load(project, env=_env(home)).llm.model == "auto"


def test_malformed_yaml_names_the_file(home: Path, project: Path) -> None:
    path = _project_config(project, "llm:\n  model: [unclosed\n")

    with pytest.raises(SettingsError, match=str(path)):
        Settings.load(project, env=_env(home))


def test_non_mapping_yaml_is_rejected(home: Path, project: Path) -> None:
    path = _home_config(home, "- just\n- a list\n")

    with pytest.raises(SettingsError, match=str(path)):
        Settings.load(project, env=_env(home))


def test_unknown_nested_key_names_file_and_key(home: Path, project: Path) -> None:
    path = _project_config(project, "llm:\n  modle: typo\n")

    with pytest.raises(SettingsError) as info:
        Settings.load(project, env=_env(home))

    assert str(path) in str(info.value)
    assert "llm.modle" in str(info.value)


@pytest.mark.parametrize("text", ["browser:\n  headless: true\n", "home_dir: /tmp/elsewhere\n"])
def test_unknown_top_level_key_is_rejected(home: Path, project: Path, text: str) -> None:
    path = _home_config(home, text)

    with pytest.raises(SettingsError, match=str(path)):
        Settings.load(project, env=_env(home))


def test_invalid_yaml_value_names_file_and_key(home: Path, project: Path) -> None:
    path = _home_config(home, "ui:\n  port: 99999\n")

    with pytest.raises(SettingsError) as info:
        Settings.load(project, env=_env(home))

    assert str(path) in str(info.value)
    assert "ui.port" in str(info.value)


@pytest.mark.parametrize(
    "text",
    [
        "llm:\n  api_key: sk-super-secret-value\n",
        "llm:\n  openai_api_key: sk-super-secret-value\n",
        "decider:\n  token: sk-super-secret-value\n",
        "secret: sk-super-secret-value\n",
        "ui:\n  password: sk-super-secret-value\n",
        "llm:\n  headers:\n    auth_token: sk-super-secret-value\n",
    ],
)
def test_secrets_in_yaml_are_rejected_without_echoing_them(home: Path, project: Path, text: str) -> None:
    _project_config(project, text)

    with pytest.raises(SettingsError) as info:
        Settings.load(project, env=_env(home))

    message = str(info.value)
    # llm.headers values name environment variables: a literal value there is refused with that hint instead.
    assert ("never the value itself" if "headers" in text else "api_key_env") in message
    assert "sk-super-secret-value" not in message


def test_secrets_in_overrides_are_rejected(home: Path, project: Path) -> None:
    with pytest.raises(SettingsError, match="api_key_env"):
        Settings.load(project, env=_env(home), overrides={"llm": {"api_key": "sk-123"}})


def test_api_key_env_name_is_allowed_in_yaml(home: Path, project: Path) -> None:
    _home_config(home, "llm:\n  provider: openrouter\n  api_key_env: OPENROUTER_API_KEY\ndecider:\n  backend: jev\n  api_key_env: OPENROUTER_API_KEY\n")

    settings = Settings.load(project, env=_env(home))

    assert settings.llm.api_key_env == "OPENROUTER_API_KEY"
    assert settings.decider.api_key_env == "OPENROUTER_API_KEY"


def test_api_key_env_must_look_like_a_variable_name(home: Path, project: Path) -> None:
    _home_config(home, "llm:\n  api_key_env: sk-or-v1-abc123\n")

    with pytest.raises(SettingsError, match="llm.api_key_env"):
        Settings.load(project, env=_env(home))


def test_unknown_override_key_is_rejected(home: Path, project: Path) -> None:
    with pytest.raises(SettingsError, match="llm.nope"):
        Settings.load(project, env=_env(home), overrides={"llm": {"nope": 1}})


def test_settings_error_is_a_value_error() -> None:
    assert issubclass(SettingsError, ValueError)


def test_settings_are_frozen(home: Path, project: Path) -> None:
    settings = Settings.load(project, env=_env(home))

    with pytest.raises(ValidationError):
        settings.llm.model = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        settings.home_dir = Path("/elsewhere")  # type: ignore[misc]


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LLMSettings(api_key="sk-123")  # type: ignore[call-arg]


def test_llm_request_params_come_from_yaml_or_json_in_the_environment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("llm:\n  request_params:\n    reasoning_effort: low\n", encoding="utf-8")

    from_file = Settings.load(tmp_path, env={"HERCULES_HOME": str(home)})
    from_env = Settings.load(tmp_path, env={"HERCULES_HOME": str(home), "HERCULES_LLM_REQUEST_PARAMS": '{"reasoning_effort": "none", "top_k": 20}'})

    assert from_file.llm.request_params == {"reasoning_effort": "low"}
    assert from_env.llm.request_params == {"reasoning_effort": "none", "top_k": 20}
    assert Settings.load(tmp_path, env={"HERCULES_HOME": str(home)}).llm.request_params == {"reasoning_effort": "low"}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not json", "expected a JSON object"),
        ('["reasoning_effort"]', "expected a JSON object"),
        ('{"model": "other"}', "model cannot be set here"),
        ('{"api_key": "sk-123"}', "looks like a secret"),
    ],
)
def test_bad_request_params_are_refused_without_echoing_values(tmp_path: Path, value: str, message: str) -> None:
    with pytest.raises(SettingsError) as error:
        Settings.load(tmp_path, env={"HERCULES_HOME": str(tmp_path / "home"), "HERCULES_LLM_REQUEST_PARAMS": value})
    assert message in str(error.value)
    assert "sk-123" not in str(error.value)


def test_llm_headers_name_environment_variables_and_cache_is_allow_or_bypass(home: Path, project: Path) -> None:
    _project_config(project, "llm:\n  provider: litellm\n  headers:\n    x-api-key: GATEWAY_KEY\n  cache: allow\n")

    settings = Settings.load(project, env=_env(home))

    assert settings.llm.headers == {"x-api-key": "GATEWAY_KEY"}
    assert settings.llm.cache == "allow"
    from_env = Settings.load(project, env={**_env(home), "HERCULES_LLM_HEADERS": '{"x-gateway-token": "GW_TOKEN"}', "HERCULES_LLM_CACHE": "bypass"})
    assert from_env.llm.headers == {"x-api-key": "GATEWAY_KEY", "x-gateway-token": "GW_TOKEN"}, "layers merge, as for every mapping"
    assert from_env.llm.cache == "bypass"
    with pytest.raises(SettingsError, match="never the value itself"):
        Settings.load(project, env={**_env(home), "HERCULES_LLM_HEADERS": '{"x-api-key": "sk-live-abc"}'})
    with pytest.raises(SettingsError, match="llm.cache"):
        Settings.load(project, env={**_env(home), "HERCULES_LLM_CACHE": "sometimes"})
