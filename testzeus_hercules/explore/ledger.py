"""The project's finding ledger: every finding ever reported, by signature, with its triage history.

Bugs outlive sessions. A finding whose signature is already in the ledger **attaches** to it ("seen again on build X")
instead of being new; a signature someone dismissed (``by_design``, ``wont_fix``, ``known_issue``, ``duplicate``) stays
suppressed until reopened; a closed signature that appears again is a **regression**. Severity set by a person, the
tracker key and every state change are kept with an audit trail.

The ledger is ``.hercules/explore/ledger.json``, written atomically under a lock (the daemon and ``hercules explore
triage`` may both write it).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from testzeus_hercules.explore.cases import CASE_DECISIONS, sync_case
from testzeus_hercules.explore.models import OPEN, SEVERITIES, SUPPRESSED, Finding, TriageEvent, now_iso

__all__ = ["Ledger", "Recorded", "TRANSITIONS", "TriageError", "default_triager"]

LEDGER_FILE = "ledger.json"
# Who may go where: a person's triage moves.
TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"confirmed", "in_tracker", "duplicate", "by_design", "wont_fix", "cannot_reproduce", "known_issue"}),
    "confirmed": frozenset({"in_tracker", "duplicate", "by_design", "wont_fix", "known_issue", "verify_fixed"}),
    "in_tracker": frozenset({"verify_fixed", "wont_fix", "known_issue", "duplicate"}),
    "verify_fixed": frozenset({"closed", "confirmed"}),
    "closed": frozenset({"new"}),
    "duplicate": frozenset({"new"}),
    "by_design": frozenset({"new"}),
    "wont_fix": frozenset({"new"}),
    "known_issue": frozenset({"new", "confirmed"}),
    "cannot_reproduce": frozenset({"new"}),
}


# What a triage state does to the case when the person does not decide it in the same change.
_DISMISSED = frozenset({"by_design", "wont_fix", "duplicate", "cannot_reproduce"})
_ACCEPTED = frozenset({"confirmed", "in_tracker", "known_issue", "verify_fixed"})


def default_triager(project_dir: Path) -> str:
    """Who is triaging when nobody says: the project's git ``user.name``, else the login name, else ""."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        name = subprocess.run(["git", "config", "user.name"], cwd=project_dir, capture_output=True, text=True, timeout=3, check=False).stdout.strip()  # noqa: S603, S607
        if name:
            return name
    return os.environ.get("USER") or os.environ.get("USERNAME") or ""


def _case_for(state: str, case: str) -> str:
    """The case decision that follows a state change: dismissing rejects the case, accepting keeps a proposed one, a
    verified fix makes a kept case a regression test, and reopening a dismissed finding proposes its case again."""
    if state in _DISMISSED:
        return "rejected"
    if state in _ACCEPTED and case == "proposed":
        return "kept"
    if state == "closed" and case in ("proposed", "kept"):
        return "regression"
    if state == "new" and case == "rejected":
        return "proposed"
    return case


class TriageError(ValueError):
    """A triage change that is not allowed (an unknown finding, a move the workflow does not have)."""


@dataclass(frozen=True)
class Recorded:
    finding: Finding
    outcome: Literal["new", "seen_again", "regression", "suppressed"]


class Ledger:
    def __init__(self, explore_dir: Path) -> None:
        self.dir = explore_dir
        self.path = explore_dir / LEDGER_FILE
        # explore_dir is <project>/.hercules/explore: the cases move within the project.
        self.project_dir = explore_dir.parent.parent

    def _place_case(self, finding: Finding) -> Finding:
        """Move the finding's case to where its decision says, and point case_path at it."""
        placed = sync_case(self.project_dir, finding)
        return finding.model_copy(update={"case_path": placed}) if placed is not None else finding

    # ------------------------------------------------------------------ storage

    @contextlib.contextmanager
    def _locked(self) -> Iterator[dict[str, Finding]]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / ".ledger.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                findings = self._read()
                yield findings
                self._write(findings)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Finding]:
        """The findings; entries this version cannot read are kept as they are (written back untouched), and a file that
        is not JSON is copied aside (``ledger.json.corrupt-<time>``) before the ledger starts again, never lost."""
        self._unreadable: list[Any] = []
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("findings", []) if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ValueError("no findings list")
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                shutil.copy2(self.path, self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}"))
            return {}
        found: dict[str, Finding] = {}
        for item in items:
            try:
                finding = Finding.model_validate(item)
            except ValueError:
                self._unreadable.append(item)
                continue
            found[finding.id] = finding
        return found

    def _write(self, findings: dict[str, Finding]) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        kept = [json.loads(item.model_dump_json()) for item in sorted(findings.values(), key=lambda item: item.first_seen)]
        payload = {"version": 1, "findings": [*kept, *getattr(self, "_unreadable", [])]}
        temporary.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def all(self) -> list[Finding]:
        return sorted(self._read().values(), key=lambda item: (SEVERITIES.index(item.effective_severity), item.first_seen))

    def get(self, finding_id: str) -> Finding | None:
        return self._read().get(finding_id)

    def by_signature(self, signature: str) -> Finding | None:
        return next((item for item in self._read().values() if item.signature == signature), None)

    def suppressed(self, signature: str) -> bool:
        found = self.by_signature(signature)
        return found is not None and found.state in SUPPRESSED

    # ------------------------------------------------------------------ sessions report

    def record(self, finding: Finding) -> Recorded:
        """Add a session's finding: new, seen again (attached), a regression (it was closed), or suppressed."""
        with self._locked() as findings:
            existing = next((item for item in findings.values() if item.signature == finding.signature), None)
            if existing is None:
                findings[finding.id] = self._place_case(finding)
                return Recorded(findings[finding.id], "new")
            sessions = list(dict.fromkeys([*existing.sessions, *finding.sessions]))
            builds = list(dict.fromkeys([*existing.builds, *finding.builds]))
            update: dict[str, object] = {
                "sessions": sessions,
                "builds": builds,
                "last_seen": now_iso(),
                "run_ids": list(dict.fromkeys([*existing.run_ids, *finding.run_ids]))[-20:],
                # The fresh copy this session wrote is only a candidate: sync_case keeps the copy at the decision's place.
                "case_path": finding.case_path or existing.case_path,
            }
            if existing.state in SUPPRESSED:
                findings[existing.id] = self._place_case(existing.model_copy(update=update))
                return Recorded(findings[existing.id], "suppressed")
            if existing.state == "closed":
                event = TriageEvent(by="hercules", field="state", before="closed", after="new", reason=f"seen again in session {finding.sessions[-1] if finding.sessions else '?'}: a regression")
                update.update(state="new", regression=True, audit=[*existing.audit, event], proposed_severity=finding.proposed_severity, steps=finding.steps, environment=finding.environment)
                findings[existing.id] = self._place_case(existing.model_copy(update=update))
                return Recorded(findings[existing.id], "regression")
            findings[existing.id] = self._place_case(existing.model_copy(update=update))
            return Recorded(findings[existing.id], "seen_again")

    # ------------------------------------------------------------------ people triage

    def triage(
        self,
        finding_id: str,
        *,
        by: str,
        state: str | None = None,
        severity: str | None = None,
        priority: str | None = None,
        tracker: str | None = None,
        duplicate_of: str | None = None,
        case: str | None = None,
        reason: str = "",
    ) -> Finding:
        """A person's change to a finding; every change is audited. Raises :class:`TriageError`."""
        with self._locked() as findings:
            finding = findings.get(finding_id)
            if finding is None:
                raise TriageError(f"no finding {finding_id!r} in the ledger")
            events: list[TriageEvent] = []
            update: dict[str, object] = {}
            if severity is not None:
                if severity not in SEVERITIES:
                    raise TriageError(f"severity must be one of {', '.join(SEVERITIES)}")
                events.append(TriageEvent(by=by, field="severity", before=finding.severity or f"(proposed {finding.proposed_severity})", after=severity, reason=reason))
                update["severity"] = severity
            if priority is not None:
                if priority not in SEVERITIES:
                    raise TriageError(f"priority must be one of {', '.join(SEVERITIES)}")
                events.append(TriageEvent(by=by, field="priority", before=finding.priority or f"(proposed {finding.proposed_priority})", after=priority, reason=reason))
                update["priority"] = priority
            if tracker is not None:
                events.append(TriageEvent(by=by, field="tracker", before=finding.tracker, after=tracker, reason=reason))
                update["tracker"] = tracker
                if state is None and finding.state in ("new", "confirmed"):
                    state = "in_tracker"
            if state is not None and state != finding.state:
                allowed = TRANSITIONS.get(finding.state, frozenset())
                if state not in allowed:
                    raise TriageError(f"a {finding.state} finding cannot move to {state} (it can move to: {', '.join(sorted(allowed)) or 'nothing'})")
                if state == "duplicate":
                    if not duplicate_of or duplicate_of not in findings or duplicate_of == finding_id:
                        raise TriageError("a duplicate names the finding it duplicates (duplicate_of)")
                    update["duplicate_of"] = duplicate_of
                if state in ("by_design", "wont_fix", "duplicate", "cannot_reproduce") and not reason.strip():
                    raise TriageError(f"say why it is {state} (reason): dismissals feed the precision measure")
                events.append(TriageEvent(by=by, field="state", before=finding.state, after=state, reason=reason))
                update["state"] = state
            if case is not None and case not in CASE_DECISIONS:
                raise TriageError(f"case must be one of {', '.join(CASE_DECISIONS)}")
            final_state = str(update.get("state", finding.state))
            if case == "regression" and case != finding.case and final_state not in ("verify_fixed", "closed"):
                raise TriageError(
                    f"a regression case must pass, and this {final_state} bug is not fixed yet: keep its case (case kept) until the fix is verified, "
                    "then close it (state closed), which makes the case a regression test"
                )
            decided = case if case is not None else _case_for(str(update.get("state", finding.state)), finding.case) if "state" in update else finding.case
            if decided != finding.case:
                if decided == "rejected" and case is not None and not reason.strip():
                    raise TriageError("say why the case is rejected (reason): a rejected case is never proposed again")
                events.append(TriageEvent(by=by, field="case", before=finding.case, after=decided, reason=reason))
                update["case"] = decided
            if not events:
                raise TriageError("nothing to change")
            update["audit"] = [*finding.audit, *events]
            findings[finding_id] = self._place_case(finding.model_copy(update=update))
            return findings[finding_id]

    def verified(self, finding_id: str, *, passed: bool, build: str, by: str = "hercules") -> Finding:
        """``reverify``: the finding's case ran on ``build``. A pass moves it to verify_fixed (then a person closes it)."""
        with self._locked() as findings:
            finding = findings.get(finding_id)
            if finding is None:
                raise TriageError(f"no finding {finding_id!r} in the ledger")
            if passed and finding.state in OPEN and finding.state != "verify_fixed":
                event = TriageEvent(by=by, field="state", before=finding.state, after="verify_fixed", reason=f"its case passed on build {build}")
                findings[finding_id] = finding.model_copy(update={"state": "verify_fixed", "audit": [*finding.audit, event]})
            elif not passed and finding.state == "verify_fixed":
                event = TriageEvent(by=by, field="state", before="verify_fixed", after="confirmed", reason=f"its case still fails on build {build}")
                findings[finding_id] = finding.model_copy(update={"state": "confirmed", "audit": [*finding.audit, event]})
            return findings[finding_id]

    # ------------------------------------------------------------------ measures

    def precision(self) -> dict[str, object]:
        """Confirmed ÷ triaged (untriaged findings are not counted as right), with a Wilson 90% interval."""
        findings = self._read().values()
        good = sum(item.state in ("confirmed", "in_tracker", "verify_fixed", "closed") for item in findings)
        bad = sum(item.state in ("by_design", "wont_fix", "cannot_reproduce") for item in findings)
        triaged = good + bad
        low, high = _wilson(good, triaged)
        return {
            "confirmed": good,
            "dismissed": bad,
            "triaged": triaged,
            "untriaged": sum(item.state == "new" for item in findings),
            "precision": round(good / triaged, 3) if triaged else None,
            "interval_90": [low, high] if triaged else None,
        }


def _wilson(hits: int, total: int, z: float = 1.645) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    phat = hits / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = z * ((phat * (1 - phat) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return (round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3))
