"""Small boolean feature flags read from the process environment."""

import os

_TRUE_VALUES = ("1", "true", "yes", "on")


def env_flag(name: str, default: bool = False) -> bool:
    """Return the boolean value of environment variable ``name``.

    Accepts ``1``/``true``/``yes``/``on`` (case-insensitive) as true. An unset or
    empty variable returns ``default``; any other value is false.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE_VALUES


def should_skip_browser() -> bool:
    """Return True when ``SKIP_BROWSER`` opts the run out of any browser launch.

    Accepts ``1``/``true``/``yes`` case-insensitively; anything else (including
    an unset variable) keeps the browser enabled. Pure MCP/API scenarios do not
    need a browser: skipping the launch avoids paying a chromium startup per
    scenario and avoids empty screenshots/videos folders in the proofs tree.
    """
    return os.getenv("SKIP_BROWSER", "").strip().lower() in ("1", "true", "yes")


# --- Opt-in hardening / reporting features -------------------------------------
# Everything below defaults to the legacy behaviour (off). Each flag is read at
# call time so tests and long-lived processes can toggle it.


def run_reporting_enabled() -> bool:
    """``ENABLE_RUN_REPORTING``: write the markdown report (Hercules_Test_Report.md) next to the junit output."""
    return env_flag("ENABLE_RUN_REPORTING")


def failure_bundles_enabled() -> bool:
    """``ENABLE_FAILURE_BUNDLES``: copy per-failure evidence (videos, network logs) into ``bundles/``."""
    return env_flag("ENABLE_FAILURE_BUNDLES")


def run_history_enabled() -> bool:
    """``ENABLE_RUN_HISTORY``: record runs/testcases in the ``hercules_history.db`` run-history database."""
    return env_flag("ENABLE_RUN_HISTORY")


def failure_classification_enabled() -> bool:
    """``ENABLE_FAILURE_CLASSIFICATION``: classify failed testcases and annotate bundles/history."""
    return env_flag("ENABLE_FAILURE_CLASSIFICATION")


def observer_invariants_enabled() -> bool:
    """``ENABLE_OBSERVER_INVARIANTS``: fail a claimed pass on observed 5xx responses / uncaught console errors."""
    return env_flag("ENABLE_OBSERVER_INVARIANTS")


def strict_verdict_enabled() -> bool:
    """``STRICT_VERDICT``: a scenario with no explicit assertion no longer counts as passed."""
    return env_flag("STRICT_VERDICT")


def strict_exit_code_enabled() -> bool:
    """``STRICT_EXIT_CODE``: exit the process with status 1 when any scenario failed or errored.

    ``EXIT_CODE_COMPAT=1`` (the old escape hatch) still forces the legacy exit code.
    """
    if os.getenv("EXIT_CODE_COMPAT", "").strip() == "1":
        return False
    return env_flag("STRICT_EXIT_CODE")
