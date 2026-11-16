"""``hercules ui``: prints (and opens) a one-time sign-in link for the running daemon."""

from __future__ import annotations

import io
from pathlib import Path

from testzeus_hercules.cli.main import main
from testzeus_hercules.cli.ui import UiOptions, run_ui
from testzeus_hercules.daemon.discovery import DaemonInfo, write_daemon_file
from tests.v2.fixtures.daemon_support import TOKEN, daemon_thread, isolated_env, make_config


def test_ui_without_a_daemon_says_how_to_start_one(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main(["ui", "--print"], env=isolated_env(tmp_path), stdout=out, stderr=err)
    assert code == 1
    assert "hercules serve" in err.getvalue()
    assert out.getvalue() == ""


def test_ui_with_a_stale_daemon_file_reports_it(tmp_path: Path) -> None:
    env = isolated_env(tmp_path)
    write_daemon_file(Path(env["HERCULES_HOME"]) / "daemon.json", DaemonInfo(port=9, pid=999_999_999, token=TOKEN, started_at="2026-10-26T00:00:00+00:00"))
    err = io.StringIO()
    assert run_ui(UiOptions(print_only=True), env=env, out=io.StringIO(), err=err) == 1
    assert TOKEN not in err.getvalue()


def test_ui_prints_and_opens_a_one_time_link_that_signs_in(tmp_path: Path) -> None:
    import httpx

    env = isolated_env(tmp_path)
    config = make_config(tmp_path, env=env)
    opened: list[str] = []
    with daemon_thread(config) as info:
        out, err = io.StringIO(), io.StringIO()
        code = run_ui(UiOptions(), env=env, out=out, err=err, opener=lambda url: opened.append(url) or True)
        assert code == 0, err.getvalue()
        url = out.getvalue().strip().splitlines()[-1]
        assert url.startswith(f"{info.url}/login?code=")
        assert opened == [url]
        assert TOKEN not in out.getvalue()
        with httpx.Client(trust_env=False) as client:
            login = client.get(url, follow_redirects=False)
            assert login.status_code == 303
            assert "hercules_session=" in login.headers["set-cookie"]
            assert client.get(f"{info.url}/api/health").status_code == 200  # the cookie works
            client.cookies.clear()
            assert client.get(url, follow_redirects=False).status_code == 401  # one time only


def test_ui_print_does_not_open_a_browser(tmp_path: Path) -> None:
    env = isolated_env(tmp_path)
    config = make_config(tmp_path, env=env)
    opened: list[str] = []
    with daemon_thread(config):
        out = io.StringIO()
        assert run_ui(UiOptions(print_only=True), env=env, out=out, err=io.StringIO(), opener=lambda url: opened.append(url) or True) == 0
    assert opened == []
    assert "/login?code=" in out.getvalue()
