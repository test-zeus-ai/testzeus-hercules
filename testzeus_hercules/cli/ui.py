"""``hercules ui``: sign a browser into the running daemon's tester UI.

Finds the daemon through ``~/.hercules/daemon.json``, asks it for a one-time login
URL (``POST /api/login-url`` with the API token), prints the URL and opens it in the
default browser unless ``--print`` is given. The URL holds a single-use code that
expires after a few minutes, never the API token.

Exit codes: 0 when a URL was printed, 1 when no daemon is running or it did not answer.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from dataclasses import dataclass
from typing import Callable, Mapping, TextIO

EXIT_OK = 0
EXIT_NO_DAEMON = 1
_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class UiOptions:
    print_only: bool = False


def run_ui(
    options: UiOptions,
    *,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    opener: Callable[[str], bool] = webbrowser.open,
) -> int:
    import httpx

    from testzeus_hercules.daemon.discovery import daemon_file_path, pid_alive, read_daemon_file

    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    environ = dict(os.environ) if env is None else dict(env)
    info = read_daemon_file(daemon_file_path(environ))
    if info is None or not pid_alive(info.pid):
        stderr.write("hercules ui: no Hercules daemon is running; start one with `hercules serve` in your project\n")
        return EXIT_NO_DAEMON
    try:
        response = httpx.post(f"{info.url}/api/login-url", headers={"Authorization": f"Bearer {info.token}"}, timeout=_TIMEOUT_S, trust_env=False)
    except httpx.HTTPError as exc:
        stderr.write(f"hercules ui: the daemon at {info.url} did not answer ({type(exc).__name__}); is `hercules serve` still running?\n")
        return EXIT_NO_DAEMON
    url = response.json().get("url") if response.status_code == 200 else None
    if not isinstance(url, str) or not url.startswith(info.url):
        stderr.write(f"hercules ui: the daemon at {info.url} refused the request (HTTP {response.status_code})\n")
        return EXIT_NO_DAEMON
    stdout.write(f"Open the Hercules UI (one-time link, valid for 5 minutes):\n{url}\n")
    stdout.flush()
    if not options.print_only:
        try:
            opener(url)
        except Exception:  # no browser available: the printed link is enough
            pass
    return EXIT_OK
