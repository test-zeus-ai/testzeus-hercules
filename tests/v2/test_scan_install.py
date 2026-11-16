"""Finding the scanner and installing the pinned release (scan.install), offline: downloads are faked."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from testzeus_hercules.scan import install
from testzeus_hercules.scan.install import NUCLEI_ENV, NUCLEI_VERSION, InstallError, ReleaseAsset, ScannerMissing, find_nuclei, install_dir, install_nuclei, release_asset


@pytest.mark.parametrize(
    "system, machine, name",
    [
        ("Darwin", "arm64", f"nuclei_{NUCLEI_VERSION}_macOS_arm64.zip"),
        ("Darwin", "x86_64", f"nuclei_{NUCLEI_VERSION}_macOS_amd64.zip"),
        ("Linux", "x86_64", f"nuclei_{NUCLEI_VERSION}_linux_amd64.zip"),
        ("Linux", "aarch64", f"nuclei_{NUCLEI_VERSION}_linux_arm64.zip"),
        ("Windows", "AMD64", f"nuclei_{NUCLEI_VERSION}_windows_amd64.zip"),
    ],
)
def test_the_pinned_archive_matches_the_os_and_cpu(system: str, machine: str, name: str) -> None:
    asset = release_asset(system, machine)
    assert asset.name == name
    assert asset.url == f"https://github.com/projectdiscovery/nuclei/releases/download/v{NUCLEI_VERSION}/{name}"
    assert len(asset.sha256) == 64
    assert asset.binary == ("nuclei.exe" if system == "Windows" else "nuclei")


def test_a_machine_without_a_pinned_build_is_told_to_install_it_itself() -> None:
    with pytest.raises(InstallError, match="install nuclei yourself"):
        release_asset("Linux", "riscv64")
    with pytest.raises(InstallError):
        release_asset("FreeBSD", "x86_64")


def _zip(binary: str = "nuclei", content: bytes = b"#!/bin/sh\necho nuclei\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("README.md", "readme")
        bundle.writestr(binary, content)
        bundle.writestr("../evil", "never written")
    return buffer.getvalue()


def _asset(data: bytes, sha256: str | None = None) -> ReleaseAsset:
    return ReleaseAsset(name="nuclei_test.zip", url="https://example.invalid/nuclei_test.zip", sha256=sha256 or hashlib.sha256(data).hexdigest(), binary="nuclei")


def test_install_checks_the_checksum_and_unpacks_only_the_executable(tmp_path: Path) -> None:
    data = _zip()
    fetched: list[str] = []
    binary = install_nuclei(tmp_path / "cache", asset=_asset(data), fetch=lambda url, limit: fetched.append(url) or data)
    assert binary == install_dir(tmp_path / "cache") / "nuclei"
    assert binary.read_bytes() == b"#!/bin/sh\necho nuclei\n"
    assert binary.stat().st_mode & 0o111
    assert sorted(path.name for path in binary.parent.iterdir()) == ["nuclei"]
    assert not (tmp_path / "evil").exists() and not (tmp_path / "cache" / "nuclei" / "evil").exists()
    # Installed once: reused without downloading again.
    install_nuclei(tmp_path / "cache", asset=_asset(data), fetch=lambda url, limit: pytest.fail("downloaded again"))
    assert fetched == ["https://example.invalid/nuclei_test.zip"]


def test_a_checksum_mismatch_installs_nothing(tmp_path: Path) -> None:
    data = _zip()
    with pytest.raises(InstallError, match="checksum mismatch"):
        install_nuclei(tmp_path / "cache", asset=_asset(data, sha256="0" * 64), fetch=lambda url, limit: data)
    assert not install_dir(tmp_path / "cache").exists()


def test_an_archive_without_the_executable_or_not_a_zip_is_refused(tmp_path: Path) -> None:
    other = _zip(binary="something-else")
    with pytest.raises(InstallError, match="does not hold nuclei"):
        install_nuclei(tmp_path / "cache", asset=_asset(other), fetch=lambda url, limit: other)
    garbage = b"not a zip"
    with pytest.raises(InstallError, match="not a zip"):
        install_nuclei(tmp_path / "cache", asset=_asset(garbage), fetch=lambda url, limit: garbage)


def test_a_failed_download_is_an_install_error(tmp_path: Path) -> None:
    def broken(url: str, limit: int) -> bytes:
        raise OSError("network is unreachable")

    with pytest.raises(InstallError, match="could not download"):
        install_nuclei(tmp_path / "cache", asset=_asset(b"x"), fetch=broken)


def _runnable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_find_prefers_the_variable_then_path_then_the_cache(tmp_path: Path) -> None:
    configured = _runnable(tmp_path / "custom" / "my-nuclei")
    on_path = _runnable(tmp_path / "bin" / "nuclei")
    cached = _runnable(install_dir(tmp_path / "cache") / "nuclei")
    assert find_nuclei({NUCLEI_ENV: str(configured), "PATH": str(on_path.parent)}, tmp_path / "cache") == configured
    assert find_nuclei({"PATH": str(on_path.parent)}, tmp_path / "cache") == on_path
    assert find_nuclei({"PATH": str(tmp_path / "empty")}, tmp_path / "cache") == cached


def test_a_missing_scanner_explains_how_to_install_it(tmp_path: Path) -> None:
    with pytest.raises(ScannerMissing) as missing:
        find_nuclei({"PATH": str(tmp_path)}, tmp_path / "cache")
    message = str(missing.value)
    assert "brew install nuclei" in message and "hercules scan --install" in message and NUCLEI_ENV in message
    with pytest.raises(ScannerMissing, match="not a runnable file"):
        find_nuclei({NUCLEI_ENV: str(tmp_path / "nope")}, None)


def test_every_pinned_digest_names_this_version() -> None:
    assert all(name.startswith(f"nuclei_{NUCLEI_VERSION}_") for name in install._DIGESTS)
