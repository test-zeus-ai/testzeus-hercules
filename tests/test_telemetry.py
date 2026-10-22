import os
import subprocess
import sys
from pathlib import Path


def test_telemetry_import_does_not_read_captured_stdin(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import testzeus_hercules.telemetry; print('imported')",
        ],
        cwd=tmp_path,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout
