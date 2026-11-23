"""Shared fixtures for the v2 tests."""

import os
import sys
from pathlib import Path

import pytest
from testzeus_hercules.kernel import telemetry


def _pin_playwright_browsers() -> None:
    """Point Playwright at the real browser cache before any test moves HOME to a temporary folder.

    Playwright finds its browsers under the user's cache folder, which it derives from HOME; many tests set HOME to
    a temporary folder, after which a browser launched by Hercules would look there and find nothing.
    """
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    home = Path.home()
    if sys.platform == "darwin":
        cache = home / "Library" / "Caches"
    elif sys.platform.startswith("win"):
        cache = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", str(home / ".cache")))
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "ms-playwright")


_pin_playwright_browsers()


@pytest.fixture(autouse=True)
def _no_real_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry is on by default; tests must never reach the real PostHog endpoint.

    Any client that would build its default transport gets a recording one instead.
    Tests that exercise ``PostHogTransport`` itself import the class directly and inject
    a capturing HTTP transport.
    """
    monkeypatch.setattr(telemetry, "PostHogTransport", lambda *args, **kwargs: telemetry.RecordingTransport())
