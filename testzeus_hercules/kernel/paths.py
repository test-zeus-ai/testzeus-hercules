"""On-disk layout of the Hercules home directory and a project's ``.hercules`` folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from testzeus_hercules.kernel.settings import CONFIG_FILENAME, PROJECT_DIRNAME, Settings

_RUN_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_PRIVATE_DIR_MODE = 0o700


def validate_run_id(run_id: str) -> str:
    """Return ``run_id`` unchanged if it is safe to use as a single path segment."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or ".." in run_id or run_id == ".":
        raise ValueError(f"invalid run id {run_id!r}: use 1-128 characters from [A-Za-z0-9_.-] and no '..'")
    return run_id


@dataclass(frozen=True)
class HerculesPaths:
    """Every file and directory Hercules reads or writes, derived from ``Settings``."""

    home_dir: Path
    config_file: Path
    daemon_file: Path
    capabilities_file: Path
    cache_dir: Path
    installation_id_file: Path
    project_dir: Path
    project_hercules_dir: Path
    project_config_file: Path
    cases_dir: Path
    envs_dir: Path
    runs_dir: Path
    history_db: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> HerculesPaths:
        home = settings.home_dir
        project_hercules = settings.project_dir / PROJECT_DIRNAME
        return cls(
            home_dir=home,
            config_file=home / CONFIG_FILENAME,
            daemon_file=home / "daemon.json",
            capabilities_file=home / "capabilities.json",
            cache_dir=home / "cache",
            installation_id_file=home / "installation_id",
            project_dir=settings.project_dir,
            project_hercules_dir=project_hercules,
            project_config_file=project_hercules / CONFIG_FILENAME,
            cases_dir=project_hercules / "cases",
            envs_dir=project_hercules / "envs",
            runs_dir=project_hercules / "runs",
            history_db=project_hercules / "history.db",
        )

    def ensure_dirs(self) -> None:
        """Create the home and project directories; safe to call repeatedly.

        A newly created home directory is private to the current user. The project's ``.hercules`` folder is made
        private every time (it holds the run history with real values, run evidence and case inputs).
        """
        try:
            self.home_dir.mkdir(parents=True, mode=_PRIVATE_DIR_MODE)
        except FileExistsError:
            pass
        else:
            self.home_dir.chmod(_PRIVATE_DIR_MODE)  # mkdir's mode is subject to the umask
        self.project_hercules_dir.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        if not self.project_hercules_dir.is_symlink():
            self.project_hercules_dir.chmod(_PRIVATE_DIR_MODE)
        for directory in (self.cache_dir, self.cases_dir, self.envs_dir, self.runs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        """Directory holding the artifacts of one run (not created here)."""
        return self.runs_dir / validate_run_id(run_id)
