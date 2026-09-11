"""Mobile device automation via Appium.

This subpackage is fully optional: nothing here is imported eagerly by the
rest of the framework, and the Appium client is imported lazily inside
functions so this package can be imported without the client installed.

Tool registration is double-gated (see :func:`mobile_enabled`): tools are
imported — and therefore registered into the tool registry under
``mobile_nav_agent`` — only when BOTH hold:

1. ``DEVICE_MANAGER`` is set to ``appium`` (device-settings module), and
2. the Appium client is importable (``importlib.util.find_spec("appium")``).

When the gate is closed, importing this package has no side effects.
"""

import importlib.util

from testzeus_hercules.core.mobile.settings import get_device_manager
from testzeus_hercules.utils.logger import logger

__all__ = ["mobile_enabled"]


def mobile_enabled() -> bool:
    """Return True when mobile device automation is enabled and importable."""
    try:
        if get_device_manager() != "appium":
            return False
    except Exception:  # pragma: no cover - defensive
        return False
    try:
        return importlib.util.find_spec("appium") is not None
    except Exception:  # pragma: no cover - defensive: broken installs
        return False


if mobile_enabled():
    try:
        # Importing the tools module performs @tool registration. All Appium
        # imports inside it are lazy, so this cannot fail merely because the
        # client is absent — the find_spec gate above already ensured it.
        from testzeus_hercules.core.mobile import tools as _tools  # noqa: F401
    except Exception as e:  # pragma: no cover - defensive: never break imports
        logger.warning("Mobile tools could not be registered; mobile automation stays disabled: %s", e)
