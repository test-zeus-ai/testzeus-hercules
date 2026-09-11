"""Small boolean feature flags read from the process environment."""

import os


def should_skip_browser() -> bool:
    """Return True when ``SKIP_BROWSER`` opts the run out of any browser launch.

    Accepts ``1``/``true``/``yes`` case-insensitively; anything else (including
    an unset variable) keeps the browser enabled. Pure MCP/API scenarios do not
    need a browser: skipping the launch avoids paying a chromium startup per
    scenario and avoids empty screenshots/videos folders in the proofs tree.
    """
    return os.getenv("SKIP_BROWSER", "").strip().lower() in ("1", "true", "yes")
