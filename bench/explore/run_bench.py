"""Score ``hercules explore`` on Acme Pantry: recall of the seeded bugs, precision of the findings, and false findings
on the clean build.

A run serves the app (``--bugs all`` by default) on a free port, writes a throwaway project (a sign-in start case and
the app's promises), runs ``hercules explore start`` there (the project's daemon starts on demand, with the model
configured by your environment), then matches the session's findings to ``bugs.yaml``:

* a finding **matches** a bug when its route is the bug's route and its oracle is the bug's oracle (a judged or
  invariant finding may also match by the bug's ``signature_hint`` in its title or signature);
* **recall** = seeded bugs matched ÷ seeded bugs switched on; **precision** = findings that match a bug ÷ findings;
* with ``--clean`` the app runs with no bugs, and every finding is a false finding.

Usage::

    python bench/explore/run_bench.py --time 10m --workers 2
    python bench/explore/run_bench.py --clean --time 5m
    python bench/explore/run_bench.py --score REPORT.json --bugs all    # score an existing session report

It prints a JSON summary; ``--out FILE`` also writes it. Exit code 0 always (it measures; it does not gate).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import app as pantry  # noqa: E402

PROMISES = [
    {"id": "total-not-negative", "kind": "not_negative", "route": "/cart", "target": "#total", "text": "The cart total is never negative"},
    {"id": "badge-equals-count", "kind": "equal", "route": "/cart", "target": "#badge", "other": "#count", "text": "The cart badge equals the items in the cart"},
]


def load_manifest(path: Path = pantry.MANIFEST) -> list[dict[str, Any]]:
    return list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("bugs") or [])


def _route(finding: dict[str, Any]) -> str:
    signature = str(finding.get("signature") or "")
    parts = signature.split("|")
    return parts[1] if len(parts) >= 3 else ""


def matches(finding: dict[str, Any], bug: dict[str, Any]) -> bool:
    """Whether a session finding is this seeded bug."""
    if _route(finding) != bug["route"]:
        return False
    oracle = str(finding.get("oracle") or "")
    hint = str(bug.get("signature_hint") or "").lower()
    text = f"{finding.get('title', '')} {finding.get('signature', '')} {finding.get('promise') or ''}".lower()
    if bug["oracle"] in ("server_error", "page_error", "dead_action", "broken_link"):
        return oracle == bug["oracle"]
    # Promise, persistence and judged findings on a busy route need the bug's hint too (two promises on /cart are two bugs).
    return oracle in ("judged", "invariant", "persistence") and bool(hint) and hint in text


def score(report: dict[str, Any], bugs_on: list[str], manifest: list[dict[str, Any]]) -> dict[str, Any]:
    """Recall, precision and false findings of one session report against the seeded bugs that were on."""
    findings = list(report.get("findings") or [])
    seeded = [bug for bug in manifest if bug["id"] in bugs_on]
    found: dict[str, list[str]] = {}
    unmatched: list[dict[str, Any]] = []
    for finding in findings:
        hits = [bug["id"] for bug in seeded if matches(finding, bug)][:1]  # one finding is one bug
        for bug_id in hits:
            found.setdefault(bug_id, []).append(str(finding.get("id")))
        if not hits:
            unmatched.append({"id": finding.get("id"), "title": finding.get("title"), "oracle": finding.get("oracle"), "route": _route(finding)})
    matched_findings = len(findings) - len(unmatched)
    return {
        "session": report.get("id"),
        "status": report.get("status"),
        "elapsed_s": report.get("elapsed_s"),
        "workers": report.get("workers"),
        "reproducers": report.get("reproducers"),
        "cost_usd": report.get("cost_usd"),
        "bugs_on": bugs_on,
        "findings": len(findings),
        "recall": round(len(found) / len(seeded), 3) if seeded else None,
        "precision": round(matched_findings / len(findings), 3) if findings else None,
        "false_findings": len(unmatched),
        "found": {bug_id: ids for bug_id, ids in sorted(found.items())},
        # Matches made from a judged finding by the bug's hint alone: a person should confirm these.
        "judged_matches": sorted({bug_id for finding in findings if finding.get("oracle") == "judged" for bug_id in found if str(finding.get("id")) in found[bug_id]}),
        "missed": [bug["id"] for bug in seeded if bug["id"] not in found],
        "unmatched": unmatched,
        "clues": report.get("clues"),
        "not_reproduced": len(report.get("not_reproduced") or []),
        "flaky": len(report.get("flaky") or []),
    }


def _project(root: Path, url: str) -> Path:
    project = root / "project"
    (project / ".hercules" / "cases").mkdir(parents=True, exist_ok=True)
    (project / ".hercules" / "explore").mkdir(parents=True, exist_ok=True)
    start = {
        "id": "sign-in",
        "title": "Sign in to Acme Pantry",
        "environment": {"base_url": url},
        "steps": [{"action": 'Open "/login"'}, {"action": 'Fill "ana" in "Name" and click the "Sign in" button'}],
    }
    (project / ".hercules" / "cases" / "sign-in.json").write_text(json.dumps(start, indent=1), encoding="utf-8")
    (project / ".hercules" / "explore" / "promises.yaml").write_text(yaml.safe_dump(PROMISES, sort_keys=False), encoding="utf-8")
    return project


def run_session(project: Path, url: str, args: argparse.Namespace) -> dict[str, Any]:
    command = [
        *shlex.split(args.hercules),
        "explore",
        "start",
        "--from",
        ".hercules/cases/sign-in.json",
        "--base-url",
        url,
        "--time",
        args.time,
        "--workers",
        str(args.workers),
        "--reproducers",
        str(args.reproducers),
        "--mission",
        args.mission,
        "--isolation",
        "partition",
        "--yes",
        "--json",
    ]
    completed = subprocess.run(command, cwd=project, capture_output=True, text=True, check=False)
    sys.stderr.write(completed.stderr[-4000:])
    session_id = next((line.split()[1] for line in completed.stdout.splitlines() if line.startswith("Session x_")), None)
    if session_id is None:
        raise SystemExit(f"the session did not start (exit {completed.returncode}): {completed.stdout[-2000:]}")
    report = project / ".hercules" / "explore" / session_id / "report.json"
    if not report.is_file():
        raise SystemExit(f"no report for {session_id}: {completed.stdout[-2000:]}")
    return dict(json.loads(report.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score hercules explore on Acme Pantry's seeded bugs")
    parser.add_argument("--bugs", default="all", help="seeded bugs to switch on: all, or a list such as B1,B3 (ignored with --clean)")
    parser.add_argument("--clean", action="store_true", help="run on the clean build: every finding is a false finding")
    parser.add_argument("--time", default="10m", help="the session's time box (default 10m)")
    parser.add_argument("--workers", type=int, default=2, help="explorers at once (default 2)")
    parser.add_argument("--reproducers", type=int, default=1, help="reproducers at once (default 1)")
    parser.add_argument("--mission", default="p1", help="the mission (default p1)")
    parser.add_argument("--hercules", default="hercules", help='the hercules command, e.g. "uv run hercules" (default: hercules on PATH)')
    parser.add_argument("--score", type=Path, default=None, metavar="REPORT", help="only score an existing report.json")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway project (its path is printed)")
    parser.add_argument("--out", type=Path, default=None, help="also write the summary here")
    args = parser.parse_args(argv)
    manifest = load_manifest()
    bugs_on = [] if args.clean else list(pantry.parse_bugs(args.bugs))
    if args.score is not None:
        summary = score(json.loads(args.score.read_text(encoding="utf-8")), bugs_on, manifest)
    else:
        root = Path(tempfile.mkdtemp(prefix="hercules-explore-bench-"))
        with pantry.serve(tuple(bugs_on)) as url:
            project = _project(root, url)
            report = run_session(project, url, args)
        summary = score(report, bugs_on, manifest)
        summary["project"] = str(project) if args.keep else None
    text = json.dumps(summary, indent=1)
    print(text)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
