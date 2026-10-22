"""Mobile device automation via Appium.

This subpackage is fully optional: nothing here is imported eagerly by the
rest of the framework, and the Appium client is imported lazily inside
functions so this package can be imported without the client installed.

Tool registration is double-gated (see :func:`mobile_enabled`): tools are
imported — and therefore registered into the tool registry under
``mobile_nav_agent`` — only when BOTH hold:

1. ``DEVICE_MANAGER`` is set to ``appium`` (device-settings module), and
2. the Appium client is importable (``importlib.util.find_spec("appium")``).

The gate is evaluated lazily, every time :func:`mobile_enabled` is called, so it
never depends on when ``.env``/config was loaded relative to this import; the
tools are (idempotently) registered the first time the gate is seen open.

When the gate is closed, importing this package has no side effects.
"""

import importlib.util

from testzeus_hercules.core.mobile.settings import get_device_manager
from testzeus_hercules.utils.logger import logger

__all__ = ["ensure_mobile_tools_registered", "mobile_enabled"]


def mobile_enabled() -> bool:
    """Return True when mobile device automation is enabled and importable.

    Evaluated on every call; when the gate is open the mobile tools are
    registered (once) as a side effect so callers that only check the gate
    still end up with a usable toolset.
    """
    try:
        if get_device_manager() != "appium":
            return False
    except Exception:  # pragma: no cover - defensive
        return False
    try:
        if importlib.util.find_spec("appium") is None:
            return False
    except Exception:  # pragma: no cover - defensive: broken installs
        return False
    ensure_mobile_tools_registered()
    return True


def ensure_mobile_tools_registered() -> None:
    """Import the mobile tools module (registering its ``@tool`` entries). Idempotent."""
    try:
        # Importing the tools module performs @tool registration. All Appium
        # imports inside it are lazy, so this cannot fail merely because the
        # client is absent.
        from testzeus_hercules.core.mobile import tools as _tools  # noqa: F401
    except Exception as e:  # pragma: no cover - defensive: never break callers
        logger.warning("Mobile tools could not be registered; mobile automation stays disabled: %s", e)


# Evaluate the gate once at import so the tools are registered for code that
# imports the package early; later calls re-evaluate lazily.
mobile_enabled()
