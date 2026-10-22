"""Tests for the Hurl suite runner (core.tools.hurl_calls).

The no-binary ERROR branch always runs. The orchestration logic (JUnit
parsing, variable passing, report copy, log file, timeouts) is exercised
against a fake `hurl` executable, so no real binary or network is needed;
the real-binary tests skip unless hurl is installed.
"""

import asyncio
import json
import os
import shutil
import stat
import textwrap
import time

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import hurl_calls
from testzeus_hercules.core.tools.hurl_calls import run_hurl_suite

HAS_HURL_BINARY = shutil.which(hurl_calls.HURL_BINARY) is not None

requires_hurl = pytest.mark.skipif(not HAS_HURL_BINARY, reason="the hurl binary is not installed")

# hurl's argv layout is fixed: $1=--test $2=--report-junit $3=<report path>
# $4..=--variable pairs, then the suite files.
WRITE_REPORT_TEMPLATE = "cat > \"$3\" <<'XML'\n{report}\nXML\n"


@pytest.fixture
def proof_env(tmp_path):
    """Point the proof folder at tmp_path; restore the config afterwards."""
    conf = get_global_conf()
    original_config = dict(conf.get_config())
    original_test_id = conf.get_default_test_id()
    conf.get_config()["PROJECT_SOURCE_ROOT"] = str(tmp_path)
    conf.set_default_test_id("hurl-tests")
    yield conf
    conf.get_config().clear()
    conf.get_config().update(original_config)
    conf.set_default_test_id(original_test_id)


def _write_hurl(tmp_path, name="suite.hurl", content="GET http://127.0.0.1:1/echo\nHTTP 200\n"):
    hurl_file = tmp_path / name
    hurl_file.write_text(content, encoding="utf-8")
    return str(hurl_file)


@pytest.fixture
def fake_hurl(monkeypatch, tmp_path):
    """Install a fake hurl executable whose behavior is a scripted bash body."""

    def install(script_body, exit_code=0):
        script = tmp_path / "fake-hurl-bin"
        script.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(script_body) + f"\nexit {exit_code}\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setattr(hurl_calls.shutil, "which", lambda name: str(script))
        return script

    return install


PASSING_REPORT = """
<testsuites>
  <testsuite name="s" tests="2" failures="0" errors="0">
    <testcase name="first" time="0.01"/>
    <testcase name="second" time="0.02"/>
  </testsuite>
</testsuites>
"""

FAILING_REPORT = """
<testsuites>
  <testsuite name="s" tests="2" failures="1" errors="0">
    <testcase name="first" time="0.01"/>
    <testcase name="second" time="0.02">
      <failure message="Assert status code failed">expected 200, actual 500</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

GARBAGE_ATTRS_REPORT = """
<testsuites>
  <testsuite name="s" tests="garbage" failures="NaN" errors="oops">
    <testcase name="first" time="0.01"/>
  </testsuite>
</testsuites>
"""


def _run(*args, **kwargs):
    verdict = asyncio.run(run_hurl_suite(*args, **kwargs))
    assert verdict.startswith("{")
    return json.loads(verdict)


# ── no-binary branch (always) ────────────────────────────────────────────────


def test_missing_binary_returns_structured_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(hurl_calls.shutil, "which", lambda name: None)
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "ERROR"
    assert "hurl" in verdict["reason"]
    assert "not installed" in verdict["reason"]


# ── orchestration against the fake binary ────────────────────────────────────


def test_passing_suite(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    hurl_file = _write_hurl(tmp_path)
    verdict = _run(hurl_file)
    assert verdict["assertion"] == "PASSED"
    assert verdict["executed"] == 2
    assert verdict["succeeded"] == 2
    assert verdict["failed"] == 0
    assert verdict["failures"] == []
    # A copy of the JUnit report is saved under the proof folder.
    assert verdict["junit_report"] and os.path.isfile(verdict["junit_report"])
    assert f"{proof_env.get_proof_path()}/hurl_results" in verdict["junit_report"]


def test_report_copy_names_do_not_collide(proof_env, tmp_path, monkeypatch) -> None:
    # Two saves within the same millisecond must not overwrite each other.
    monkeypatch.setattr(hurl_calls.time, "time", lambda: 1700000000.123)  # frozen clock: same ms for both saves
    junit = tmp_path / "report.xml"
    junit.write_text(PASSING_REPORT, encoding="utf-8")
    first_path = hurl_calls._save_report_copy(str(junit), "suite")
    second_path = hurl_calls._save_report_copy(str(junit), "suite")
    assert first_path and second_path
    assert first_path != second_path
    for saved in (first_path, second_path):
        with open(saved, "r", encoding="utf-8") as saved_file:
            assert "testsuites" in saved_file.read()


def test_failing_suite_reports_case_failures(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=FAILING_REPORT))
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "FAILED"
    assert verdict["executed"] == 2
    assert verdict["succeeded"] == 1
    assert verdict["failed"] == 1
    assert verdict["failures"][0]["name"] == "second"
    assert "status code" in verdict["failures"][0]["message"]


def test_parse_junit_report_treats_non_numeric_counts_as_zero(tmp_path) -> None:
    # A non-numeric junit attribute (tests="garbage") must not crash the parse;
    # unparseable counts read as 0.
    report = tmp_path / "report.xml"
    report.write_text(GARBAGE_ATTRS_REPORT, encoding="utf-8")
    parsed = hurl_calls._parse_junit_report(str(report))
    assert parsed == {"executed": 0, "failed": 0, "failures": []}


def test_non_numeric_junit_attributes_do_not_crash_the_run(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=GARBAGE_ATTRS_REPORT))
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "FAILED"  # zero executed tests can never count as PASSED
    assert verdict["executed"] == 0
    assert verdict["failed"] == 0


def test_temp_report_dir_is_removed_even_on_error_paths(fake_hurl, proof_env, tmp_path, monkeypatch) -> None:
    real_mkdtemp = hurl_calls.tempfile.mkdtemp

    def tracked_mkdtemp(**kwargs):
        # Redirect the report directory into tmp_path so it can be checked.
        return real_mkdtemp(prefix=str(tmp_path / "hurl-junit-"))

    monkeypatch.setattr(hurl_calls.tempfile, "mkdtemp", tracked_mkdtemp)
    fake_hurl('echo "crashed"\n', exit_code=3)  # exits without a report -> early ERROR return
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "ERROR"
    assert list(tmp_path.glob("hurl-junit-*")) == []


def test_variables_are_passed_and_bridged(fake_hurl, proof_env, tmp_path) -> None:
    from testzeus_hercules.core.tools.api_calls import set_variable_value

    set_variable_value("token", "secret-42")
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT) + 'echo "ARGS:$@"\necho "ENV:$HURL_VARIABLE_env/$HURL_VARIABLE_token"\n')
    verdict = _run(_write_hurl(tmp_path), variables={"env": "stage", "token": "${token}"})
    assert verdict["assertion"] == "PASSED"
    # The ${name} placeholder is resolved from the variable store, and each
    # variable travels in a HURL_VARIABLE_<name> environment entry; it never
    # appears on the command line (argv is visible to other local users).
    excerpt = verdict.get("output_excerpt", "")
    assert "--variable" not in excerpt
    assert "stage" not in excerpt.split("ARGS:")[1].splitlines()[0]
    assert "ENV:stage/" in excerpt
    # A secret-named variable's value is scrubbed from the returned output.
    assert "secret-42" not in excerpt
    assert "[REDACTED]" in excerpt


def test_secrets_never_reach_the_proof_log_or_failure_messages(fake_hurl, proof_env, tmp_path) -> None:
    report = FAILING_REPORT.replace("expected 200, actual 500", "got token=hunter2-secret in body")
    fake_hurl(
        WRITE_REPORT_TEMPLATE.format(report=report)
        + 'echo "Authorization: Bearer hunter2-secret"\necho "GET https://user:pw123@api.test/x?api_key=hunter2-secret"\necho "value=$HURL_VARIABLE_api_token"\n'
    )
    verdict = _run(_write_hurl(tmp_path), variables={"api_token": "hunter2-secret"})
    blob = json.dumps(verdict)
    assert "hunter2-secret" not in blob
    assert "pw123" not in blob
    with open(f"{proof_env.get_proof_path()}/hurl_logs.log", "r", encoding="utf-8") as log_file:
        log_text = log_file.read()
    assert "hunter2-secret" not in log_text
    assert "pw123" not in log_text


def test_invalid_variable_name_is_a_structured_error(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    verdict = _run(_write_hurl(tmp_path), variables={"bad=name": "x"})
    assert verdict["assertion"] == "ERROR"
    assert "bad=name" in verdict["reason"]


def test_nonzero_exit_code_never_reports_passed(fake_hurl, proof_env, tmp_path) -> None:
    # JUnit says every case passed, but hurl itself exited non-zero.
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT), exit_code=4)
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "FAILED"
    assert verdict["exit_code"] == 4
    assert "exited with code 4" in verdict["reason"]


def test_hurl_path_outside_allowed_roots_is_refused(fake_hurl, proof_env, tmp_path) -> None:
    import tempfile

    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    with tempfile.TemporaryDirectory() as elsewhere:
        outside = os.path.join(elsewhere, "evil.hurl")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("GET http://127.0.0.1:1/x\nHTTP 200\n")
        verdict = _run(outside)
    assert verdict["assertion"] == "ERROR"
    assert "Access denied" in verdict["reason"]


def test_symlinked_hurl_file_escaping_the_roots_is_refused(fake_hurl, proof_env, tmp_path) -> None:
    import tempfile

    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    with tempfile.TemporaryDirectory() as elsewhere:
        target = os.path.join(elsewhere, "evil.hurl")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("GET http://127.0.0.1:1/x\nHTTP 200\n")
        (suite_dir / "link.hurl").symlink_to(target)
        verdict = _run(str(suite_dir))
    assert verdict["assertion"] == "ERROR"
    assert "Access denied" in verdict["reason"]


def test_dash_prefixed_path_is_passed_as_an_absolute_path(fake_hurl, proof_env, tmp_path, monkeypatch) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT) + 'echo "ARGS:$@"\n')
    (tmp_path / "-oops.hurl").write_text("GET http://127.0.0.1:1/x\nHTTP 200\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    verdict = _run("-oops.hurl")
    args = verdict["output_excerpt"].split("ARGS:")[1].split()
    assert os.path.isabs(args[-1]) and args[-1].endswith("-oops.hurl")


def test_nul_byte_variable_returns_structured_error(fake_hurl, proof_env, tmp_path) -> None:
    # A variable containing a NUL byte must yield a structured ERROR naming
    # the offending variable, not a raised ValueError from the exec call.
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    verdict = _run(_write_hurl(tmp_path), variables={"good": "fine", "k": "a\x00b"})
    assert verdict["assertion"] == "ERROR"
    assert "k" in verdict["reason"]


def test_nul_byte_variable_name_is_named_in_structured_error(fake_hurl, proof_env, tmp_path) -> None:
    # A NUL byte in a variable NAME breaks the exec call too; the structured
    # error must name the offending variable, not stay silent about it.
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    verdict = _run(_write_hurl(tmp_path), variables={"na\x00me": "ok"})
    assert verdict["assertion"] == "ERROR"
    assert "na\x00me" in verdict["reason"]


def test_raw_output_is_appended_to_hurl_log(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl('echo "some hurl progress output"\n')
    _run(_write_hurl(tmp_path))
    hurl_log_path = f"{proof_env.get_proof_path()}/hurl_logs.log"
    with open(hurl_log_path, "r", encoding="utf-8") as log_file:
        log_text = log_file.read()
    assert "some hurl progress output" in log_text


def test_adversarial_output_is_capped_in_proof_log(fake_hurl, proof_env, tmp_path) -> None:
    # A huge stdout must not balloon the proof log; it is capped with a
    # truncation marker while the verdict itself stays well-formed.
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT) + "head -c 5242880 /dev/zero | tr '\\0' 'x'\n")
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "PASSED"  # the run itself is unaffected
    hurl_log_path = f"{proof_env.get_proof_path()}/hurl_logs.log"
    assert os.path.getsize(hurl_log_path) <= int(1.1 * 1024 * 1024)
    with open(hurl_log_path, "r", encoding="utf-8") as log_file:
        log_text = log_file.read()
    assert "[truncated 5242880 bytes total]" in log_text


def test_directory_input_runs_sorted_hurl_files(fake_hurl, proof_env, tmp_path) -> None:
    (tmp_path / "suites").mkdir()
    (tmp_path / "suites" / "b-second.hurl").write_text("GET http://127.0.0.1:1/b\nHTTP 200\n", encoding="utf-8")
    (tmp_path / "suites" / "a-first.hurl").write_text("GET http://127.0.0.1:1/a\nHTTP 200\n", encoding="utf-8")
    (tmp_path / "suites" / "notes.txt").write_text("ignored", encoding="utf-8")
    fake_hurl('echo "ARGS:$@"\n')
    verdict = _run(str(tmp_path / "suites"))
    excerpt = verdict.get("output_excerpt", "")
    a_index = excerpt.index("a-first.hurl")
    b_index = excerpt.index("b-second.hurl")
    assert a_index < b_index  # deterministic, sorted order
    assert "notes.txt" not in excerpt


def test_timeout_kills_the_suite(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl("sleep 30\n")
    verdict = _run(_write_hurl(tmp_path), timeout_seconds=1)
    assert verdict["assertion"] == "ERROR"
    assert "timed out after 1s" in verdict["reason"]


def _pid_alive(pid: int) -> bool:
    """True while the process still exists and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat_file:
            state = stat_file.read().rsplit(b")", 1)[1].split()[0]
        return state != b"Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def test_timeout_kills_grandchildren_and_returns_promptly(fake_hurl, proof_env, tmp_path, monkeypatch) -> None:
    # The fake hurl leaves a grandchild behind that inherits (and holds) the
    # stdout pipe: a bare kill() of the direct child would hang the drain on
    # the grandchild and orphan it.
    pid_file = tmp_path / "grandchild.pid"
    # The path is embedded in the script: the subprocess gets a minimal environment.
    fake_hurl(f'sleep 10 &\necho $! > "{pid_file}"\nsleep 300\n')
    started = time.monotonic()
    verdict = _run(_write_hurl(tmp_path), timeout_seconds=2)
    elapsed = time.monotonic() - started
    assert verdict["assertion"] == "ERROR"
    assert "timed out after 2s" in verdict["reason"]
    assert elapsed < 6  # must return near the 2s timeout, not when the grandchild releases the pipe
    grandchild_pid = int(pid_file.read_text().strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _pid_alive(grandchild_pid):
        time.sleep(0.1)
    assert not _pid_alive(grandchild_pid)


def test_timeout_keeps_partial_output(fake_hurl, proof_env, tmp_path) -> None:
    # Output emitted before the kill must still reach the verdict excerpt.
    fake_hurl('echo "progress-early"\nsleep 30\n')
    verdict = _run(_write_hurl(tmp_path), timeout_seconds=1)
    assert verdict["assertion"] == "ERROR"
    assert "timed out after 1s" in verdict["reason"]
    assert "progress-early" in verdict["output_excerpt"]


def test_exit_without_report_is_error(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl('echo "crashed"\n', exit_code=3)
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "ERROR"
    assert "without producing a JUnit report" in verdict["reason"]
    assert "crashed" in verdict["output_excerpt"]


def test_malformed_report_is_error(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl('echo "<not-xml" > "$3"\n')
    verdict = _run(_write_hurl(tmp_path))
    assert verdict["assertion"] == "ERROR"
    assert "parse" in verdict["reason"].lower()


def test_missing_input_is_error(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(":\n")  # binary present, input missing -> input branch is what fails
    verdict = _run(str(tmp_path / "absent.hurl"))
    assert verdict["assertion"] == "ERROR"
    assert "No .hurl input" in verdict["reason"]


def test_directory_without_hurl_files_is_error(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(":\n")
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    verdict = _run(str(empty_dir))
    assert verdict["assertion"] == "ERROR"
    assert "No .hurl input" in verdict["reason"]


# ── real binary (skipped unless hurl is installed) ───────────────────────────


@requires_hurl
def test_real_hurl_suite_passes(proof_env, tmp_path) -> None:
    hurl_file = tmp_path / "real.hurl"
    hurl_file.write_text("GET http://127.0.0.1:1/anything\nHTTP 200\n", encoding="utf-8")
    verdict = _run(str(hurl_file))
    assert verdict["assertion"] == "PASSED"


def test_hurl_environment_is_minimal_and_keeps_proxy_ca_and_variables(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith(("LC_", "HURL_VARIABLE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/ca.pem")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("HURL_VARIABLE_ambient", "from-ci")
    monkeypatch.setenv("HURL_VARIABLE_token", "parent-value")
    env = hurl_calls._hurl_environment({"token": "t-1"})
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u" and env["LC_ALL"] == "C"
    assert env["HTTPS_PROXY"] == "http://proxy:3128" and env["SSL_CERT_FILE"] == "/etc/ssl/ca.pem"
    assert env["HURL_VARIABLE_token"] == "t-1"
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    # The parent's own HURL_VARIABLE_* entries pass through; tool-supplied values win.
    assert env["HURL_VARIABLE_ambient"] == "from-ci"


def test_parent_secrets_do_not_reach_the_hurl_subprocess(fake_hurl, proof_env, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-secret")
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT) + 'echo "LEAK:[${OPENAI_API_KEY}]"\necho "VAR:[$HURL_VARIABLE_env]"\n')
    verdict = _run(_write_hurl(tmp_path), variables={"env": "stage"})
    assert verdict["assertion"] == "PASSED"
    excerpt = verdict["output_excerpt"]
    assert "LEAK:[]" in excerpt
    assert "sk-parent-secret" not in excerpt
    assert "VAR:[stage]" in excerpt


def test_captured_secret_under_a_harmless_hurl_name_is_scrubbed(fake_hurl, proof_env, tmp_path) -> None:
    from testzeus_hercules.core.tools.api_calls import set_variable_value

    set_variable_value("access_token", "captured-tok-777")
    report = FAILING_REPORT.replace("expected 200, actual 500", "body echoed captured-tok-777")
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=report) + 'echo "value=$HURL_VARIABLE_h"\n')
    verdict = _run(_write_hurl(tmp_path), variables={"h": "${access_token}"})
    assert "captured-tok-777" not in json.dumps(verdict)
    with open(f"{proof_env.get_proof_path()}/hurl_logs.log", "r", encoding="utf-8") as log_file:
        assert "captured-tok-777" not in log_file.read()


def test_saved_junit_report_copy_is_redacted_and_still_valid_xml(fake_hurl, proof_env, tmp_path) -> None:
    import xml.etree.ElementTree as ElementTree

    report = FAILING_REPORT.replace("expected 200, actual 500", "Authorization: Bearer hunter2-secret value=hunter2-secret https://u:pw123@api.test/x?token=t0k")
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=report))
    verdict = _run(_write_hurl(tmp_path), variables={"api_token": "hunter2-secret"})
    with open(verdict["junit_report"], "r", encoding="utf-8") as copy_file:
        saved = copy_file.read()
    assert "hunter2-secret" not in saved
    assert "pw123" not in saved and "t0k" not in saved
    ElementTree.fromstring(saved)


@pytest.mark.parametrize("name", ["notes.txt", "suite.hurl.bak"])
def test_non_hurl_file_input_is_refused(fake_hurl, proof_env, tmp_path, name) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    verdict = _run(_write_hurl(tmp_path, name=name))
    assert verdict["assertion"] == "ERROR"
    assert ".hurl" in verdict["reason"]


def test_sensitive_file_input_is_refused(fake_hurl, proof_env, tmp_path) -> None:
    fake_hurl(WRITE_REPORT_TEMPLATE.format(report=PASSING_REPORT))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-1", encoding="utf-8")
    verdict = _run(".env")
    assert verdict["assertion"] == "ERROR"
    assert "sk-1" not in json.dumps(verdict)
