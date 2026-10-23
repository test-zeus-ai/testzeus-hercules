"""Regression tests for the TES-1939 fixes to the API verification tools (PR #159).

Offline only: HTTP goes through an httpx MockTransport, hurl and nuclei are
faked, nothing touches the network.
"""

import asyncio
import importlib.util
import json
import stat
import urllib.parse

import httpx
import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import api_calls, api_sec_calls, hurl_calls
from testzeus_hercules.core.tools.api_assertions import assert_json_path
from testzeus_hercules.core.tools.api_calls import (
    _VARIABLES,
    extract_from_response,
    generic_http_api,
    set_variable_value,
    validate_response_schema,
)

HAS_JSONPATH_NG = importlib.util.find_spec("jsonpath_ng") is not None
requires_jsonpath_ng = pytest.mark.skipif(not HAS_JSONPATH_NG, reason="jsonpath_ng (api extra) is not installed")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def proof_env(tmp_path):
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id("landed-fixes-api")
    _VARIABLES.pop("landed-fixes-api", None)
    yield conf
    _VARIABLES.pop("landed-fixes-api", None)
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


@pytest.fixture
def wire(monkeypatch, proof_env):
    """Capture the requests generic_http_api sends (MockTransport, no network)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(api_calls.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(api_calls, "file_logger", lambda _line: None)
    return seen


# ── 1. auth_value interpolation and validation ──────────────────────────────


@pytest.mark.parametrize(
    "auth_type,header,prefix",
    [("bearer", "authorization", "Bearer "), ("jwt", "authorization", "JWT "), ("api_key", "x-api-key", ""), ("form_login", "x-form-login", "")],
)
def test_auth_value_placeholder_is_interpolated(wire, auth_type, header, prefix) -> None:
    set_variable_value("tok", "abc123")
    _run(generic_http_api("GET", "https://api.test/x", auth_type=auth_type, auth_value="${tok}"))
    assert wire[0].headers[header] == f"{prefix}abc123"


def test_basic_auth_value_placeholder_is_interpolated(wire) -> None:
    set_variable_value("pw", "s3cret")
    _run(generic_http_api("GET", "https://api.test/x", auth_type="basic", auth_value=["bob", "${pw}"]))
    import base64

    assert wire[0].headers["authorization"] == "Basic " + base64.b64encode(b"bob:s3cret").decode()


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        ({"auth_type": "oauth2", "auth_value": "x"}, "Unknown auth_type"),
        ({"auth_type": "bearer", "auth_value": None}, "auth_value"),
        ({"auth_type": "basic", "auth_value": "bob:pw"}, "[username, password]"),
        ({"auth_type": "basic", "auth_value": ["only-one"]}, "[username, password]"),
    ],
)
def test_bad_auth_is_a_clear_error_and_nothing_is_sent(wire, kwargs, fragment) -> None:
    result = json.loads(_run(generic_http_api("GET", "https://api.test/x", **kwargs)))
    assert fragment in result["error"]
    assert result["status_type"] == "failure"
    assert wire == []


# ── 2. body without / with unknown body_mode ────────────────────────────────


def test_dict_body_without_mode_defaults_to_json(wire) -> None:
    _run(generic_http_api("POST", "https://api.test/x", body={"a": 1}))
    assert json.loads(wire[0].content) == {"a": 1}
    assert wire[0].headers["content-type"] == "application/json"


def test_str_body_without_mode_defaults_to_raw(wire) -> None:
    _run(generic_http_api("POST", "https://api.test/x", body="plain text", headers={"Content-Type": "text/plain"}))
    assert wire[0].content == b"plain text"


def test_body_mode_names_are_case_insensitive(wire) -> None:
    _run(generic_http_api("POST", "https://api.test/x", body={"a": 1}, body_mode=" JSON "))
    assert json.loads(wire[0].content) == {"a": 1}


def test_unknown_body_mode_is_an_error_listing_valid_modes(wire) -> None:
    result = json.loads(_run(generic_http_api("POST", "https://api.test/x", body="<a/>", body_mode="xml")))
    assert "xml" in result["error"]
    for mode in ("multipart", "urlencoded", "raw", "binary", "json"):
        assert mode in result["error"]
    assert wire == []


def test_no_body_means_no_body_regardless_of_mode(wire) -> None:
    _run(generic_http_api("GET", "https://api.test/x", body_mode="none"))
    assert wire[0].content == b""


# ── 4. list-valued query params ─────────────────────────────────────────────


def test_list_query_param_is_sent_as_repeated_keys(wire) -> None:
    _run(generic_http_api("GET", "https://api.test/x", query_params={"k": ["a", "b"], "n": 1}))
    pairs = urllib.parse.parse_qsl(wire[0].url.query.decode())
    assert pairs == [("k", "a"), ("k", "b"), ("n", "1")]


# ── 3. literal: suppresses interpolation ────────────────────────────────────


@requires_jsonpath_ng
def test_literal_prefix_suppresses_interpolation(proof_env) -> None:
    set_variable_value("x", 5)
    doc = {"body": {"tpl": "${x}"}}
    verdict = json.loads(_run(assert_json_path(doc, "$.body.tpl", "eq", "literal:${x}")))
    assert verdict["assertion"] == "PASSED", verdict
    # without the prefix the placeholder is still resolved
    verdict = json.loads(_run(assert_json_path({"body": {"tpl": 5}}, "$.body.tpl", "eq", "${x}")))
    assert verdict["assertion"] == "PASSED", verdict


# ── 6. truncated bodies ─────────────────────────────────────────────────────


def _truncated_result() -> str:
    return api_calls._bounded_payload({"status_code": 200, "status_type": "success", "body": {"items": ["x" * 50] * 5000}}, 0.1)


@requires_jsonpath_ng
def test_assertion_on_truncated_result_is_error_not_failed(proof_env) -> None:
    truncated = _truncated_result()
    assert json.loads(truncated)["body_truncated"] is True
    verdict = json.loads(_run(assert_json_path(truncated, "$.body.items[0]", "eq", "x")))
    assert verdict["assertion"] == "ERROR"
    assert "truncated" in verdict["reason"].lower()
    verdict = json.loads(_run(assert_json_path(truncated, "$.body.items", "not_exists")))
    assert verdict["assertion"] == "ERROR"


@requires_jsonpath_ng
def test_assertion_on_bare_truncated_body_text_is_error(proof_env) -> None:
    body_text = json.loads(_truncated_result())["body"]
    verdict = json.loads(_run(assert_json_path(body_text, "$.items", "exists")))
    assert verdict["assertion"] == "ERROR"
    assert "truncated" in verdict["reason"].lower()


@requires_jsonpath_ng
def test_extract_and_schema_report_truncation_as_error(proof_env) -> None:
    truncated = _truncated_result()
    extracted = json.loads(_run(extract_from_response(truncated, "$.body.items[0]", "first")))
    assert extracted["assertion"] == "ERROR" and "truncated" in extracted["reason"].lower()
    validated = json.loads(_run(validate_response_schema(truncated, {"type": "object"})))
    assert validated["assertion"] == "ERROR" and "truncated" in validated["reason"].lower()


def _truncated_error_result() -> str:
    return api_calls._bounded_payload({"status_code": 404, "status_type": "failure", "headers": {"x-id": "7"}, "error_detail": "x" * 200_000}, 0.1)


@requires_jsonpath_ng
def test_assertions_on_intact_fields_of_a_truncated_result_still_evaluate(proof_env) -> None:
    truncated = _truncated_error_result()
    assert json.loads(truncated)["error_detail_truncated"] is True
    assert json.loads(_run(assert_json_path(truncated, "$.status_code", "eq", 404)))["assertion"] == "PASSED"
    assert json.loads(_run(assert_json_path(truncated, "$.status_code", "eq", 200)))["assertion"] == "FAILED"
    assert json.loads(_run(assert_json_path(truncated, "$.headers.x-id", "exists")))["assertion"] == "PASSED"
    captured = json.loads(_run(extract_from_response(truncated, "$.status_code", "code")))
    assert captured["assertion"] == "PASSED" and captured["value"] == 404
    validated = json.loads(_run(validate_response_schema(truncated, {"type": "integer"}, path="$.status_code")))
    assert validated["assertion"] == "PASSED"


@requires_jsonpath_ng
def test_truncated_result_still_errors_when_the_verdict_depends_on_the_cut_part(proof_env) -> None:
    truncated = _truncated_error_result()
    for path in ("$.error_detail", "$.error_detail.code", "$.missing"):
        verdict = json.loads(_run(assert_json_path(truncated, path, "exists")))
        assert verdict["assertion"] == "ERROR" and "truncated" in verdict["reason"].lower(), path
    assert json.loads(_run(assert_json_path(truncated, "$.nothing", "not_exists")))["assertion"] == "ERROR"
    assert json.loads(_run(extract_from_response(truncated, "$.error_detail.id", "x")))["assertion"] == "ERROR"
    whole = json.loads(_run(validate_response_schema(truncated, {"type": "object"})))
    assert whole["assertion"] == "ERROR" and "truncated" in whole["reason"].lower()


@pytest.mark.parametrize("auth_type", ["none", "None", "NONE", "", None, "  "])
def test_none_like_auth_type_is_no_auth_not_an_error(wire, auth_type) -> None:
    result = json.loads(_run(generic_http_api("GET", "https://api.test/x", auth_type=auth_type, auth_value=None)))
    assert "error" not in result
    assert "authorization" not in wire[0].headers and "x-api-key" not in wire[0].headers


# ── 7. extract_from_response as_name validation ─────────────────────────────


@requires_jsonpath_ng
@pytest.mark.parametrize("bad_name", ["", "   ", " padded ", "a{b}", "x}y"])
def test_extract_rejects_invalid_as_name(proof_env, bad_name) -> None:
    result = json.loads(_run(extract_from_response({"id": 1}, "$.id", bad_name)))
    assert result["assertion"] == "ERROR"
    assert "as_name" in result["reason"]
    assert api_calls._var_store() == {}


@requires_jsonpath_ng
def test_extract_still_accepts_normal_names(proof_env) -> None:
    result = json.loads(_run(extract_from_response({"id": 1}, "$.id", "order_id")))
    assert result["assertion"] == "PASSED"


# ── 5. hurl output redaction anywhere on the line ───────────────────────────

SECRET = "hunter2-secret"


@pytest.mark.parametrize(
    "line",
    [
        f"> Authorization: Bearer {SECRET}",
        f"* Cookie: session={SECRET}; other=1",
        f"< Set-Cookie: sid={SECRET}; Path=/; HttpOnly",
        f"  --header 'Authorization: Bearer {SECRET}' --url http://x",
        f'curl --header "Proxy-Authorization: Basic {SECRET}" http://x',
        f"Assert failed: header Authorization: Bearer {SECRET} did not match",
        f"> X-Api-Key: {SECRET}",
        f"> X-Auth-Token: {SECRET}",
        f"> x-session-token: {SECRET}",
        f"* Request headers: authorization: Bearer {SECRET}",
    ],
)
def test_hurl_redaction_covers_verbose_and_mid_line_headers(line) -> None:
    redacted = hurl_calls._redact_output(line, [])
    assert SECRET not in redacted, redacted
    assert "[REDACTED]" in redacted
    # the XML variant (report copy) must keep markup intact and hide the value too
    xml = hurl_calls._redact_output(f'<failure message="{line.replace(chr(34), chr(39))}">{line}</failure>', [], xml=True)
    assert SECRET not in xml, xml
    assert xml.endswith("</failure>")


@pytest.mark.parametrize(
    "payload",
    ["token" * 200_000, "a-" * 500_000, "x: " + " " * 1_000_000, "token:[REDACTED]" * 60_000, ("Authorization: a" + "b" * 60 + "\n") * 15_000],
    ids=["unbroken-token", "dashes", "whitespace", "redacted-colons", "many-headers"],
)
def test_redaction_is_linear_on_pathological_input(payload) -> None:
    import time

    for xml in (False, True):
        started = time.monotonic()
        api_calls.redact_tool_output(payload, xml=xml)
        assert time.monotonic() - started < 3.0, "redaction must not be quadratic"


@pytest.mark.parametrize("name", ["X-Auth-Tok\u200ben", "Author\u200dization", "Coo\x00kie", "X-Api-\u00adKey", "Authori\ufeffzation"])
def test_invisible_characters_inside_a_header_name_do_not_evade_redaction(name) -> None:
    redacted = api_calls.redact_tool_output(f"> {name}: {SECRET}")
    assert SECRET not in redacted


def test_hurl_redaction_keeps_closing_quote_of_quoted_header() -> None:
    redacted = hurl_calls._redact_output(f"run --header 'Authorization: Bearer {SECRET}' --silent", [])
    assert "--silent" in redacted and redacted.count("'") == 2


def test_hurl_redaction_leaves_harmless_lines_alone() -> None:
    text = "* Connected to localhost\n> GET /items HTTP/1.1\n> Accept: application/json\n< HTTP/1.1 200 OK"
    assert hurl_calls._redact_output(text, []) == text


def test_verbose_hurl_output_never_reaches_log_excerpt_or_failures(proof_env, tmp_path, monkeypatch) -> None:
    report = f"""<testsuites><testsuite name="s" tests="1" failures="1" errors="0">
      <testcase name="c"><failure message="bad header Authorization: Bearer {SECRET} seen">detail</failure></testcase>
    </testsuite></testsuites>"""
    script = tmp_path / "fake-hurl"
    lines = [
        "#!/usr/bin/env bash",
        "cat > \"$3\" <<'XML'",
        report,
        "XML",
        f"echo '> Authorization: Bearer {SECRET}'",
        f"echo '* Cookie: sid={SECRET}'",
        f"echo \"run --header 'Authorization: Bearer {SECRET}'\"",
        "exit 4",
    ]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(hurl_calls.shutil, "which", lambda name: str(script))
    hurl_file = tmp_path / "s.hurl"
    hurl_file.write_text("GET http://127.0.0.1:1/x\nHTTP 200\n", encoding="utf-8")
    verdict = _run(hurl_calls.run_hurl_suite(str(hurl_file)))
    assert SECRET not in verdict
    with open(f"{proof_env.get_proof_path()}/hurl_logs.log", encoding="utf-8") as log_file:
        assert SECRET not in log_file.read()


# ── 7. nuclei output redaction ──────────────────────────────────────────────


class _FakeProcess:
    returncode = 1

    async def communicate(self):
        return (f"> Authorization: Bearer {SECRET}\nraw token {SECRET}".encode(), f"failed: sent Authorization: Bearer {SECRET} to target".encode())


def test_nuclei_stdout_and_stderr_are_redacted_before_logging_and_returning(proof_env, monkeypatch, tmp_path) -> None:
    logged = []
    monkeypatch.setattr(api_sec_calls, "file_logger", logged.append)

    async def fake_installed():
        return None

    async def fake_exec(*args, **kwargs):
        return _FakeProcess()

    monkeypatch.setattr(api_sec_calls, "ensure_nuclei_installed", fake_installed)
    monkeypatch.setattr(api_sec_calls.asyncio, "create_subprocess_exec", fake_exec)
    stdout, stderr, code = _run(
        api_sec_calls.run_nuclei_command(False, None, "https://t.test", "cve", tmp_path / "o.txt", headers=[("Authorization", f"Bearer {SECRET}")]),
    )
    assert code == 1
    for text in (stdout, stderr, "\n".join(logged)):
        assert SECRET not in text


# ── 7. OpenAPI status code given as a string ────────────────────────────────


def test_openapi_string_status_matches_int_key() -> None:
    from testzeus_hercules.core.tools.openapi_validation import _declared_response

    assert _declared_response({200: {"description": "ok"}}, "200") == {"description": "ok"}
    assert _declared_response({"200": {"description": "ok"}}, 200) == {"description": "ok"}
