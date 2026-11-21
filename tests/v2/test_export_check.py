"""``hercules export --check``: the exported pytest runs once in a child process, with the variables it needs, a
trace, a recording from the trace's frames and a time limit; nothing it shows holds a secret.

A stand-in interpreter plays pytest: it writes a trace like Playwright's and prints what a run would.
"""

from __future__ import annotations

import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from testzeus_hercules.export.check import CheckError, check_environment, redacted, run_check
from testzeus_hercules.export.common import EnvVar, ExportResult
from testzeus_hercules.export.recording import trace_frames, write_recording

SECRET = "00Dsession!AQ.secretvalue"
FAKE_KEYS = ("FAKE_FRAME", "FAKE_EXIT", "FAKE_SLEEP", "FAKE_PLAYWRIGHT")


def _jpeg(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), color).save(buffer, "JPEG")
    return buffer.getvalue()


def _trace(path: Path, frames: list[tuple[float, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        events = [{"type": "before", "method": "goto"}] + [{"type": "screencast-frame", "sha1": f"frame-{index}.jpeg", "timestamp": stamp} for index, (stamp, _) in enumerate(frames)]
        archive.writestr("trace.trace", "\n".join(json.dumps(event) for event in events))
        for index, (_, color) in enumerate(frames):
            archive.writestr(f"resources/frame-{index}.jpeg", _jpeg(color))
    return path


FAKE_PYTHON = r"""#!{python}
import json, os, sys, time, zipfile, io
if sys.argv[1:2] == ["-c"]:
    sys.exit(0 if os.environ.get("FAKE_PLAYWRIGHT", "1") == "1" else 1)
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
out = sys.argv[sys.argv.index("--output") + 1]
folder = os.path.join(out, "test-chromium")
os.makedirs(folder, exist_ok=True)
with zipfile.ZipFile(os.path.join(folder, "trace.zip"), "w") as archive:
    archive.writestr("trace.trace", json.dumps({{"type": "screencast-frame", "sha1": "a.jpeg", "timestamp": 1}}))
    archive.writestr("resources/a.jpeg", open(os.environ["FAKE_FRAME"], "rb").read())
print("opened https://app.test/login?sid=" + os.environ.get("URL_CREDENTIAL_1", "") + " with " + os.environ.get("APP_PASSWORD", ""))
print("args", " ".join(sys.argv[1:]))
code = int(os.environ.get("FAKE_EXIT", "0"))
print("1 passed" if code == 0 else "1 failed")
sys.exit(code)
"""


@pytest.fixture
def fake_python(tmp_path: Path) -> str:
    script = tmp_path / "bin" / "python"
    script.parent.mkdir()
    script.write_text(FAKE_PYTHON.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    frame = tmp_path / "frame.jpeg"
    frame.write_bytes(_jpeg("navy"))
    return str(script)


def _exported() -> ExportResult:
    env = (EnvVar("URL_CREDENTIAL_1", True, "secret {{url_credential_1}}"), EnvVar("APP_PASSWORD", True, "secret {{password}}"), EnvVar("BASE_URL", False, "base"))
    return ExportResult(format="playwright-py", run_id="r_1", case_id="signin", run_status="passed", filename="tests/test_signin.py", code="def test_x():\n    pass\n", spec={}, env=env)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "APP_PASSWORD": "hunter2-password", "FAKE_FRAME": str(tmp_path / "frame.jpeg"), **extra}


def test_a_passing_export_is_reported_with_its_trace_and_a_recording_and_no_secret(tmp_path: Path, fake_python: str) -> None:
    checked = run_check(_exported(), tmp_path / "check", env=_env(tmp_path), extra={"URL_CREDENTIAL_1": SECRET}, python=fake_python, keep=FAKE_KEYS)

    assert checked.passed and checked.exit_code == 0 and not checked.timed_out
    assert checked.test_file == tmp_path / "check" / "test_signin.py" and checked.test_file.read_text() == "def test_x():\n    pass\n"
    assert checked.trace is not None and checked.trace.name == "trace.zip"
    assert checked.recording is not None and Image.open(checked.recording).format == "GIF"
    assert "--tracing on" in checked.output and "--video" not in checked.output, "video needs ffmpeg; the trace does not"
    assert SECRET not in checked.output and "hunter2-password" not in checked.output


def test_a_failing_export_is_a_failed_check_with_its_output(tmp_path: Path, fake_python: str) -> None:
    checked = run_check(_exported(), tmp_path / "check", env=_env(tmp_path, FAKE_EXIT="1"), extra={"URL_CREDENTIAL_1": SECRET}, python=fake_python, keep=FAKE_KEYS)

    assert not checked.passed and checked.exit_code == 1
    assert "1 failed" in checked.output and SECRET not in checked.output


def test_a_check_that_runs_too_long_is_stopped(tmp_path: Path, fake_python: str) -> None:
    checked = run_check(_exported(), tmp_path / "check", env=_env(tmp_path, FAKE_SLEEP="30"), extra={"URL_CREDENTIAL_1": SECRET}, python=fake_python, timeout_s=1.0, keep=FAKE_KEYS)

    assert checked.timed_out and not checked.passed and checked.exit_code is None
    assert checked.duration_s < 15


def test_a_variable_the_export_needs_is_named_never_shown(tmp_path: Path) -> None:
    with pytest.raises(CheckError, match="needs URL_CREDENTIAL_1; set it in the environment"):
        check_environment(_exported(), {"APP_PASSWORD": "x"}, {})
    assert check_environment(_exported(), {"APP_PASSWORD": "x"}, {"URL_CREDENTIAL_1": SECRET})["URL_CREDENTIAL_1"] == SECRET


def test_the_check_gets_only_the_environment_it_needs_not_the_callers_whole_one() -> None:
    caller = {"PATH": "/usr/bin", "HOME": "/home/me", "LC_ALL": "C.UTF-8", "PLAYWRIGHT_BROWSERS_PATH": "/pw", "HERCULES_CHROMIUM": "/cft", "TMPDIR": "/tmp/x"}
    caller |= {"APP_PASSWORD": "x", "BASE_URL": "https://app.test", "AWS_SECRET_ACCESS_KEY": "aws-secret", "OPENAI_API_KEY": "sk-unrelated", "GITHUB_TOKEN": "ghp_x"}
    child = check_environment(_exported(), caller, {"URL_CREDENTIAL_1": SECRET})

    assert set(child) == {"PATH", "HOME", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH", "HERCULES_CHROMIUM", "TMPDIR", "APP_PASSWORD", "BASE_URL", "URL_CREDENTIAL_1"}
    assert "aws-secret" not in child.values() and "sk-unrelated" not in child.values()


def test_only_the_python_export_with_pytest_playwright_can_be_checked(tmp_path: Path, fake_python: str) -> None:
    typescript = ExportResult(format="playwright-ts", run_id="r_1", case_id="c", run_status="passed", filename="t.spec.ts", code="", spec={})
    with pytest.raises(CheckError, match="only the pytest-playwright export"):
        run_check(typescript, tmp_path / "ts", env={})
    with pytest.raises(CheckError, match="pytest-playwright is not installed"):
        run_check(_exported(), tmp_path / "check", env=_env(tmp_path, FAKE_PLAYWRIGHT="0"), extra={"URL_CREDENTIAL_1": SECRET}, python=fake_python, keep=FAKE_KEYS)


def test_output_is_shown_without_secrets_or_url_credentials() -> None:
    text = f"GET https://app.test/signin?sid={SECRET}&next=home and token {SECRET}; other https://x.test/?session=abc123def"
    shown = redacted(text, [SECRET])
    assert SECRET not in shown and "abc123def" not in shown


def test_the_recording_shows_each_frame_for_as_long_as_it_was_on_screen(tmp_path: Path) -> None:
    trace = _trace(tmp_path / "trace.zip", [(1000.0, "red"), (1400.0, "green"), (1420.0, "blue"), (9000.0, "white")])

    assert [stamp for stamp, _ in trace_frames(trace)] == [1000.0, 1400.0, 1420.0, 9000.0]
    gif = write_recording(trace, tmp_path / "out" / "recording.gif")

    assert gif is not None
    image = Image.open(gif)
    durations = []
    for index in range(image.n_frames):
        image.seek(index)
        durations.append(image.info["duration"])
    assert durations == [400, 60, 1500, 1500], "at least 60 ms, at most 1.5 s a frame"
    assert write_recording(_trace(tmp_path / "empty.zip", []), tmp_path / "none.gif") is None


def test_the_cli_takes_credentials_from_the_runs_case_file_by_the_name_the_export_reads(tmp_path: Path) -> None:
    from testzeus_hercules.cli.export_cmd import _url_credentials
    from testzeus_hercules.export import ExportError

    cases = tmp_path / ".hercules" / "cases"
    cases.mkdir(parents=True)
    step = {"action": f'I open url "https://app.test/signin?sid={SECRET}&next=home"'}
    (cases / "signin.json").write_text(json.dumps({"id": "signin", "title": "Sign in", "steps": [step, {"action": 'click "Go"'}]}), encoding="utf-8")
    (cases / "other.json").write_text(json.dumps({"id": "other", "title": "Other", "steps": [{"action": "open /"}]}), encoding="utf-8")

    assert _url_credentials("signin", None, tmp_path, {}) == {"URL_CREDENTIAL_1": SECRET}
    assert _url_credentials("ignored", cases / "signin.json", tmp_path, {}) == {"URL_CREDENTIAL_1": SECRET}
    with pytest.raises(ExportError, match="pass its file with --case"):
        _url_credentials("missing", None, tmp_path, {})
