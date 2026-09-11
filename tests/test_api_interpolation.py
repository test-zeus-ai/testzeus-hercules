"""Tests for the per-scenario variable store, ${name} interpolation, the
API request timeout config, and cross-step capture flows."""

import asyncio
import importlib.util
import json

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    _VARIABLES,
    extract_from_response,
    interpolate_value,
    set_variable_value,
    stringify_interpolated_value,
)
from testzeus_hercules.core.tools.tool_registry import tool_registry

HAS_JSONPATH_NG = importlib.util.find_spec("jsonpath_ng") is not None

requires_jsonpath_ng = pytest.mark.skipif(not HAS_JSONPATH_NG, reason="jsonpath_ng (api extra) is not installed")


@pytest.fixture
def var_env():
    conf = get_global_conf()
    original_test_id = conf.get_default_test_id()
    conf.set_default_test_id("interpolation-tests")
    _VARIABLES.pop("interpolation-tests", None)
    yield conf
    _VARIABLES.pop("interpolation-tests", None)
    conf.set_default_test_id(original_test_id)


# ── stringify ────────────────────────────────────────────────────────────────


def test_stringify_keeps_strings_and_json_literals() -> None:
    assert stringify_interpolated_value("abc") == "abc"
    assert stringify_interpolated_value(3) == "3"
    assert stringify_interpolated_value(None) == "null"  # str(None) would be "None"
    assert stringify_interpolated_value(True) == "true"
    assert stringify_interpolated_value({"a": 1}) == '{"a":1}'
    assert stringify_interpolated_value([1, 2]) == "[1,2]"


# ── interpolation ────────────────────────────────────────────────────────────


def test_exact_placeholder_keeps_the_captured_type(var_env) -> None:
    set_variable_value("count", 3)
    set_variable_value("flag", True)
    set_variable_value("payload", {"id": 7})
    assert interpolate_value("${count}") == 3
    assert interpolate_value("  ${count}  ") == 3
    assert interpolate_value("${flag}") is True
    assert interpolate_value("${payload}") == {"id": 7}


def test_embedded_placeholders_substitute_as_text(var_env) -> None:
    set_variable_value("token", "abc")
    set_variable_value("count", 3)
    assert interpolate_value("Bearer ${token}") == "Bearer abc"
    assert interpolate_value("page=${count}&size=10") == "page=3&size=10"


def test_unknown_names_are_left_as_is(var_env) -> None:
    assert interpolate_value("${never_captured}") == "${never_captured}"
    assert interpolate_value("Bearer ${never_captured}") == "Bearer ${never_captured}"


def test_non_string_values_pass_through_untouched(var_env) -> None:
    assert interpolate_value(42) == 42
    assert interpolate_value(None) is None
    assert interpolate_value({"a": "${b}"}) == {"a": "${b}"}


def test_variables_are_scoped_per_test_id(var_env) -> None:
    set_variable_value("only_here", 1)
    # A different test id sees its own (empty) store.
    get_global_conf().set_default_test_id("another-scenario")
    try:
        assert _VARIABLES["another-scenario"] == {}
        assert interpolate_value("${only_here}") == "${only_here}"
    finally:
        get_global_conf().set_default_test_id("interpolation-tests")


# ── extraction tool ──────────────────────────────────────────────────────────


@requires_jsonpath_ng
def test_extract_stores_value_for_cross_step_checks(var_env) -> None:
    response = {"body": {"session": {"id": "S-99"}}}
    captured = json_loads_tool_result(asyncio.run(extract_from_response(response, "$.body.session.id", "session_id")))
    assert captured["assertion"] == "PASSED"
    assert captured["reference"] == "${session_id}"
    # The captured value is usable in later assertions by name, with its type.
    assert interpolate_value("${session_id}") == "S-99"


@requires_jsonpath_ng
def test_extract_no_match_is_failed_with_hints(var_env) -> None:
    response = {"body": {"session": {"id": "S-99"}}}
    captured = json_loads_tool_result(asyncio.run(extract_from_response(response, "$.body.sesion.id", "x")))
    assert captured["assertion"] == "FAILED"
    assert "session" in captured["available_at"]["keys"]


def test_extract_and_assert_tools_are_registered_for_the_api_agent() -> None:
    registered = {entry["name"] for entry in tool_registry["api_nav_agent"]}
    assert {
        "assert_json_path",
        "extract_from_response",
        "validate_response_schema",
        "validate_against_openapi_spec",
        "run_hurl_suite",
        "generic_http_api",
    } <= registered


def json_loads_tool_result(result: str) -> dict:
    return json.loads(result)


# ── API_REQUEST_TIMEOUT config ───────────────────────────────────────────────


@pytest.fixture
def timeout_conf(monkeypatch):
    conf = get_global_conf()
    original = conf.get_config().get("API_REQUEST_TIMEOUT")
    conf.get_config().pop("API_REQUEST_TIMEOUT", None)
    yield conf.get_config()
    if original is None:
        conf.get_config().pop("API_REQUEST_TIMEOUT", None)
    else:
        conf.get_config()["API_REQUEST_TIMEOUT"] = original


def test_timeout_default_is_30(timeout_conf) -> None:
    assert get_global_conf().get_api_request_timeout() == 30.0


def test_timeout_reads_env_value(timeout_conf) -> None:
    timeout_conf["API_REQUEST_TIMEOUT"] = "7.5"
    assert get_global_conf().get_api_request_timeout() == 7.5


def test_timeout_invalid_value_warns_and_falls_back(timeout_conf, caplog) -> None:
    timeout_conf["API_REQUEST_TIMEOUT"] = "soon"
    assert get_global_conf().get_api_request_timeout() == 30.0


def test_timeout_non_positive_value_falls_back(timeout_conf) -> None:
    timeout_conf["API_REQUEST_TIMEOUT"] = "-2"
    assert get_global_conf().get_api_request_timeout() == 30.0
    timeout_conf["API_REQUEST_TIMEOUT"] = "0"
    assert get_global_conf().get_api_request_timeout() == 30.0
