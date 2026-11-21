"""Prepare a release: set the version, commit it and tag it. Pushing the tag starts the Release workflow.

    make release VERSION=2.1.0        # or 2.1.0rc1, 2.1.0b2, ...

It checks that the version is a valid final or pre-release version (PEP 440, no .dev), newer than the current one,
and that the working tree is clean. It then writes the version into pyproject.toml, the Dockerfile's VERSION
default and uv.lock, shows the change, commits ``release: <version>`` and creates the annotated tag ``<version>``
(no ``v``, as earlier releases). Nothing is pushed: the last line prints the push command to run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT_VERSION = re.compile(r'^(version = ")([^"]+)(")', re.MULTILINE)
_DOCKER_VERSION = re.compile(r"^(ARG VERSION=)(\S+)", re.MULTILINE)


def check_version(wanted: str, current: str) -> Version:
    """The new version, or SystemExit with the reason it cannot be released."""
    try:
        version = Version(wanted)
    except InvalidVersion as exc:
        raise SystemExit(f"{wanted!r} is not a PEP 440 version (for example 2.1.0 or 2.1.0rc1)") from exc
    if version.is_devrelease or version.local:
        raise SystemExit(f"{version} is a development or local version; release a final or pre-release version")
    if version <= Version(current):
        raise SystemExit(f"{version} is not newer than the current version {current}")
    return version


def set_version(root: Path, version: Version) -> None:
    """Write ``version`` into pyproject.toml and the Dockerfile's VERSION default."""
    for name, pattern in (("pyproject.toml", _PYPROJECT_VERSION), ("Dockerfile", _DOCKER_VERSION)):
        path = root / name
        text, count = pattern.subn(lambda m: f"{m.group(1)}{version}{m.group(3) if m.lastindex == 3 else ''}", path.read_text(encoding="utf-8"), count=1)
        if count != 1:
            raise SystemExit(f"no version line found in {name}")
        path.write_text(text, encoding="utf-8")


def current_version(root: Path) -> str:
    found = _PYPROJECT_VERSION.search((root / "pyproject.toml").read_text(encoding="utf-8"))
    if found is None:
        raise SystemExit("no version in pyproject.toml")
    return found.group(2)


def _git(*args: str, capture: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=capture, text=True).stdout.strip() if capture else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="the version to release, such as 2.1.0 or 2.1.0rc1")
    parser.add_argument("--yes", action="store_true", help="do not ask before committing and tagging")
    args = parser.parse_args(argv)
    version = check_version(args.version, current_version(ROOT))
    if _git("status", "--porcelain"):
        raise SystemExit("the working tree has changes; commit or set them aside first")
    if _git("tag", "--list", str(version)):
        raise SystemExit(f"the tag {version} already exists")
    set_version(ROOT, version)
    subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT, check=True)
    print(_git("diff", "--stat"))
    if not args.yes and input(f"Commit and tag {version}? [y/N] ").strip().lower() != "y":
        _git("checkout", "--", "pyproject.toml", "Dockerfile", "uv.lock")
        print("left unchanged")
        return 1
    _git("commit", "--quiet", "-am", f"release: {version}")
    _git("tag", "--annotate", str(version), "-m", f"Hercules {version}")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    print(f"Tagged {version}. Push it to publish (the Release workflow checks, builds, tests and publishes):")
    print(f"  git push origin {branch} {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
