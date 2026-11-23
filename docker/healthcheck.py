#!/opt/hercules/bin/python
"""Health check of the Hercules image: exit 0 when the daemon in this container answers.

Reads the port and API token from ``$HERCULES_HOME/daemon.json`` (written by ``hercules serve``)
and calls ``GET /api/health`` on the loopback address. Exits 1 when no daemon runs, so a
container that runs a single command instead of the daemon reports unhealthy; that is expected.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    home = Path(os.environ.get("HERCULES_HOME") or Path.home() / ".hercules")
    try:
        info = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
        port, token = int(info["port"]), str(info["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return 1
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"Authorization": f"Bearer {token}"})
    # No proxy: the token must never leave the container, even when http_proxy is set for the runs.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=4) as response:  # noqa: S310 - fixed loopback URL
            return 0 if response.status == 200 else 1
    except (OSError, http.client.HTTPException):
        return 1


if __name__ == "__main__":
    sys.exit(main())
