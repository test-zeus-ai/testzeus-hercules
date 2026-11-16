"""A fake nuclei executable for offline tests of the security scan.

The fake is a small Python script. It records its argv (one JSON line per call) in
``calls.jsonl`` next to it, answers ``-version``, writes the findings of ``findings.jsonl``
(next to it) to the ``-o`` file, echoes each ``-H`` header to stdout the way a verbose
scanner echoes requests, and exits with the code in ``exit_code`` (default 0) after
printing ``stderr.txt`` to stderr. ``sleep`` (seconds) makes it hang.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT = """
import json, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
argv = sys.argv[1:]
with (here / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps(argv) + "\\n")
if "-version" in argv:
    sys.stderr.write("[INF] Nuclei Engine Version: v3.11.1\\n")
    sys.exit(0)
if (here / "sleep").exists():
    time.sleep(float((here / "sleep").read_text()))
for index, arg in enumerate(argv):
    if arg == "-H" and index + 1 < len(argv):
        print("> " + argv[index + 1])
if "-o" in argv and (here / "findings.jsonl").exists():
    Path(argv[argv.index("-o") + 1]).write_text((here / "findings.jsonl").read_text())
if (here / "stderr.txt").exists():
    sys.stderr.write((here / "stderr.txt").read_text())
sys.exit(int((here / "exit_code").read_text()) if (here / "exit_code").exists() else 0)
"""


class FakeNuclei:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.dir = directory
        self.path = directory / "nuclei"
        self.path.write_text(f"#!{sys.executable}\n{SCRIPT}", encoding="utf-8")
        self.path.chmod(0o755)

    def findings(self, *records: dict[str, Any]) -> FakeNuclei:
        (self.dir / "findings.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return self

    def fails(self, code: int, stderr: str) -> FakeNuclei:
        (self.dir / "exit_code").write_text(str(code), encoding="utf-8")
        (self.dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        return self

    def hangs(self, seconds: float) -> FakeNuclei:
        (self.dir / "sleep").write_text(str(seconds), encoding="utf-8")
        return self

    def calls(self) -> list[list[str]]:
        path = self.dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def scans(self) -> list[list[str]]:
        """The calls that ran a scan (not ``-version``)."""
        return [call for call in self.calls() if "-version" not in call]


def record(template: str, severity: str, matched_at: str, *, matcher: str | None = None, name: str = "", extracted: list[str] | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    """One nuclei JSON line."""
    line: dict[str, Any] = {"template-id": template, "info": {"name": name or template, "severity": severity, "tags": tags or ["misconfig"]}, "matched-at": matched_at, "type": "http"}
    if matcher:
        line["matcher-name"] = matcher
    if extracted:
        line["extracted-results"] = extracted
    return line
