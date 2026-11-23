"""The installed Hercules version."""

from __future__ import annotations

from importlib import metadata

DISTRIBUTION = "testzeus-hercules"


def package_version() -> str:
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "unknown"
