"""Find the nuclei scanner, and install a pinned release when asked to.

Hercules never downloads the scanner on its own. :func:`find_nuclei` looks, in order, at
``HERCULES_NUCLEI`` (a path to the executable), ``nuclei`` on ``PATH``, and a copy
installed earlier into ``<hercules home>/cache/nuclei/<version>/``. :func:`install_nuclei`
(``hercules scan --install``) downloads the release pinned here for this machine's OS and
CPU from the scanner's GitHub releases, checks its SHA-256 against the digest recorded
below (copied from the release's checksums file), and unpacks only the executable.

Templates are nuclei's own: ``nuclei -update-templates`` downloads them into the user's
nuclei folder (``hercules scan --update-templates`` runs it).
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = [
    "NUCLEI_ENV",
    "NUCLEI_VERSION",
    "InstallError",
    "ReleaseAsset",
    "ScannerMissing",
    "find_nuclei",
    "install_dir",
    "install_nuclei",
    "missing_message",
    "release_asset",
]

NUCLEI_ENV = "HERCULES_NUCLEI"
NUCLEI_VERSION = "3.11.1"
_RELEASE_URL = "https://github.com/projectdiscovery/nuclei/releases/download/v{version}/{name}"
# SHA-256 of each release archive, from nuclei_3.11.1_checksums.txt of the v3.11.1 release.
_DIGESTS: dict[str, str] = {
    "nuclei_3.11.1_linux_amd64.zip": "ea63d4ae232808cd7c6bc00d0142428e231fab59dae01042246097d195835ab6",
    "nuclei_3.11.1_linux_arm64.zip": "8044e3d9768ba0a744b2872c1a87e813006f013da97ca9f50f7661a4203bec07",
    "nuclei_3.11.1_macOS_amd64.zip": "75c47ce11e9dbd4288a1c895ed85e3c4df9f4acb8237d6d178da6dccd1628f2a",
    "nuclei_3.11.1_macOS_arm64.zip": "7d7e291addd1fc29a9bf8d089afe878a9799b20229cbea2fb1693fc40fd4c5f0",
    "nuclei_3.11.1_windows_amd64.zip": "bb6cb9ff8939b753f6fcab06fd30a15f5bc61d8382a597871334c007a85ff9d2",
}
_SYSTEMS = {"darwin": "macOS", "linux": "linux", "windows": "windows"}
_ARCHES = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64", "armv8": "arm64"}
# The release archives are about 45 MB; anything much larger is not the file we pinned.
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_MAX_BINARY_BYTES = 400 * 1024 * 1024


class ScannerMissing(RuntimeError):
    """The scanner is not installed (or ``HERCULES_NUCLEI`` names nothing runnable); the message says how to install it."""


class InstallError(RuntimeError):
    """The pinned release could not be installed (no build for this machine, download failed, checksum mismatch)."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str
    binary: str


def release_asset(system: str | None = None, machine: str | None = None) -> ReleaseAsset:
    """The pinned release archive for an OS and CPU (default: this machine)."""
    os_name = (system or platform.system()).lower()
    arch = _ARCHES.get((machine or platform.machine()).lower())
    family = _SYSTEMS.get(os_name)
    name = f"nuclei_{NUCLEI_VERSION}_{family}_{arch}.zip"
    if family is None or arch is None or name not in _DIGESTS:
        raise InstallError(f"no pinned nuclei {NUCLEI_VERSION} build for {system or platform.system()} on {machine or platform.machine()}; install nuclei yourself and put it on PATH")
    return ReleaseAsset(name=name, url=_RELEASE_URL.format(version=NUCLEI_VERSION, name=name), sha256=_DIGESTS[name], binary="nuclei.exe" if family == "windows" else "nuclei")


def install_dir(cache_dir: Path) -> Path:
    return cache_dir / "nuclei" / NUCLEI_VERSION


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_nuclei(env: Mapping[str, str], cache_dir: Path | None = None) -> Path:
    """The scanner to run: ``HERCULES_NUCLEI``, else ``nuclei`` on ``PATH``, else the pinned copy in the cache.

    :class:`ScannerMissing` when none is found (or ``HERCULES_NUCLEI`` names nothing runnable).
    """
    configured = env.get(NUCLEI_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not _runnable(path):
            raise ScannerMissing(f"{NUCLEI_ENV} is set to {configured}, which is not a runnable file. Fix it, or unset it to use nuclei from PATH.")
        return path
    found = shutil.which("nuclei", path=env.get("PATH"))
    if found:
        return Path(found)
    if cache_dir is not None:
        for name in ("nuclei", "nuclei.exe"):
            cached = install_dir(cache_dir) / name
            if _runnable(cached):
                return cached
    raise ScannerMissing(missing_message())


def missing_message() -> str:
    return (
        "the security scanner (nuclei) is not installed. Install it with your package manager (for example `brew install nuclei`), "
        f"or run `hercules scan --install` to download the pinned release {NUCLEI_VERSION}; or set {NUCLEI_ENV} to its path. "
        "Then download its templates with `hercules scan --update-templates` (or `nuclei -update-templates`)."
    )


Fetch = Callable[[str, int], bytes]


def _fetch(url: str, limit: int) -> bytes:
    import httpx

    buffer = io.BytesIO()
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                buffer.write(chunk)
                if buffer.tell() > limit:
                    raise InstallError(f"the download from {url} is larger than expected; stopped")
    return buffer.getvalue()


def install_nuclei(cache_dir: Path, *, asset: ReleaseAsset | None = None, fetch: Fetch | None = None) -> Path:
    """Download the pinned release into ``<cache_dir>/nuclei/<version>/``, check its SHA-256 and unpack the executable.

    Returns the installed executable. An installed copy is reused. Raises :class:`InstallError`.
    """
    chosen = asset or release_asset()
    target_dir = install_dir(cache_dir)
    target = target_dir / chosen.binary
    if _runnable(target):
        return target
    try:
        data = (fetch or _fetch)(chosen.url, _MAX_DOWNLOAD_BYTES)
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(f"could not download {chosen.url}: {type(exc).__name__}: {exc}") from None
    digest = hashlib.sha256(data).hexdigest()
    if digest != chosen.sha256:
        raise InstallError(f"checksum mismatch for {chosen.name}: expected sha256 {chosen.sha256}, got {digest}; nothing was installed")
    binary = _extract(data, chosen.binary)
    target_dir.mkdir(parents=True, exist_ok=True)
    partial = target_dir / f".{chosen.binary}.partial"
    partial.write_bytes(binary)
    partial.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    partial.replace(target)
    return target


def _extract(archive: bytes, binary: str) -> bytes:
    """The executable from the release zip, read from its top level only (no path from the archive is used on disk)."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            member: Any = next((info for info in bundle.infolist() if info.filename == binary and not info.is_dir()), None)
            if member is None:
                raise InstallError(f"the archive does not hold {binary}")
            if member.file_size > _MAX_BINARY_BYTES:
                raise InstallError(f"{binary} in the archive is larger than expected")
            return bundle.read(member)
    except zipfile.BadZipFile:
        raise InstallError("the download is not a zip archive") from None
