"""Helper processes (the browser, the security scanner, an export's check) get no keys or tokens."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from testzeus_hercules.kernel.child_env import BROWSER_KEEP, child_environment

SECRETS = {"HERCULES_DAEMON_TOKEN": "tok-7f3a", "OPENROUTER_API_KEY": "sk-9c1e", "BROWSERSTACK_ACCESS_KEY": "bs-1d2e"}


def test_only_what_programs_need_is_passed_on() -> None:
    environ = {"PATH": "/usr/bin", "HOME": "/home/h", "LC_ALL": "C.UTF-8", "https_proxy": "http://proxy:3128", "LD_LIBRARY_PATH": "/opt/lib", "PLAYWRIGHT_BROWSERS_PATH": "/ms", **SECRETS}
    child = child_environment(environ)
    assert child == {"PATH": "/usr/bin", "HOME": "/home/h", "LC_ALL": "C.UTF-8", "https_proxy": "http://proxy:3128"}
    browser = child_environment(environ, keep=BROWSER_KEEP, prefixes=("PLAYWRIGHT_",))
    assert browser["LD_LIBRARY_PATH"] == "/opt/lib" and browser["PLAYWRIGHT_BROWSERS_PATH"] == "/ms"
    assert not set(SECRETS) & set(browser)


def test_the_scanner_process_cannot_read_keys_or_the_daemon_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from testzeus_hercules.scan.nuclei import run_nuclei

    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    names = tmp_path / "names.txt"
    command = [sys.executable, "-c", "import os, sys; open(sys.argv[1], 'w').write('\\n'.join(os.environ))", str(names)]
    asyncio.run(run_nuclei(command, tmp_path / "findings.jsonl", 30, lambda text: text))
    seen = set(names.read_text().split("\n"))
    assert "PATH" in seen and not set(SECRETS) & seen
