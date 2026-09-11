"""Observer invariants: global red/green evidence judged from proof artifacts.

The browser layer continuously writes two JSONL artifacts under the proof
folder — ``console_logs.json`` (browser console) and ``network_logs.json``
(request/response capture) — but nothing ever judged them. This module is
that consumer. It incrementally reads both files (read positions are
persisted per test id across polls in this module; a half-written trailing
line stays pending until it is complete) and classifies every new entry:

- BINDING — fails the scenario (recorded in the assertion ledger with
  verdict ``FAILED``): HTTP 5xx responses; uncaught frontend exceptions
  (console entries matching ``Uncaught`` / ``TypeError`` / ``Cannot read``).
- NOTICE — recorded in the ledger with verdict ``notice``, never fails
  anything: other console errors and HTTP 4xx (too noisy to fail on).

Design rule: "rather miss a red than raise a false red" — when an entry
cannot be judged confidently (missing/odd status, corrupt line, unknown
shape) it is skipped or downgraded to NOTICE, never escalated.

Classification lives in exactly this one module and the file is the
contract: any producer, any process, as long as the JSONL shape holds.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import (
    assertion_logger,
    assertion_subject,
)
from testzeus_hercules.utils.logger import logger

SEVERITY_BINDING = "binding"
SEVERITY_NOTICE = "notice"

# Console text markers of an uncaught frontend exception (matched
# case-insensitively): uncaught errors, type errors and null/undefined
# dereferences. Everything else an app logs at error level is NOTICE.
_UNCAUGHT_EXCEPTION_MARKERS = ("uncaught", "typeerror", "cannot read")

# Expected outcome per finding kind, surfaced in the ledger line.
_EXPECTED_BY_KIND = {
    "http_5xx": "no HTTP 5xx responses",
    "http_4xx": "no HTTP 4xx responses",
    "uncaught_exception": "no uncaught frontend exceptions",
    "console_error": "no console errors",
}

# Per-test-id read positions (byte offsets) for each observed file; the key
# is "<test_id>:<filename>" so concurrent scenarios in one process do not
# share state.
_OFFSETS: Dict[str, int] = {}
# Per-test-id file identities (st_ino, st_mtime_ns) seen at the last poll; a
# changed identity means the file was recreated and is re-read from the start
# even when its size matches the stored offset.
_FILE_IDENTITIES: Dict[str, Tuple[int, int]] = {}


def _state_key(test_id: str, filename: str) -> str:
    return f"{test_id}:{filename}"


def reset_invariant_offsets(test_id: Optional[str] = None) -> None:
    """Forget stored read positions (used between runs and by tests)."""
    if test_id is None:
        _OFFSETS.clear()
        _FILE_IDENTITIES.clear()
        return
    prefix = f"{test_id}:"
    for key in [key for key in _OFFSETS if key.startswith(prefix)]:
        _OFFSETS.pop(key, None)
    for key in [key for key in _FILE_IDENTITIES if key.startswith(prefix)]:
        _FILE_IDENTITIES.pop(key, None)


def _classify_network_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Classify one network_logs.json entry (response lines carry a status)."""
    if str(entry.get("type") or "").lower() != "response":
        return None
    status = entry.get("status")
    # A missing/non-numeric status cannot be judged — never a false red.
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    url = str(entry.get("url") or "")
    if status >= 500:
        return {"severity": SEVERITY_BINDING, "kind": "http_5xx", "detail": f"HTTP {status} response from {url}", "status": status, "url": url}
    if 400 <= status < 500:
        # 4xx is usually an intentional part of a scenario (bad-login checks,
        # validation probes) — recorded, never binding.
        return {"severity": SEVERITY_NOTICE, "kind": "http_4xx", "detail": f"HTTP {status} response from {url}", "status": status, "url": url}
    return None


def _classify_console_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Classify one console_logs.json entry (level 'error' lines only)."""
    if str(entry.get("level") or "").lower() != "error":
        return None
    text = str(entry.get("text") or "")
    lowered = text.lower()
    marker_positions = [position for position in (lowered.find(marker) for marker in _UNCAUGHT_EXCEPTION_MARKERS) if position != -1]
    if marker_positions:
        # Markers are matched against the FULL text so an exception past the
        # head of a long line is still binding; only the stored detail is
        # truncated, keeping ~200 chars around the first marker.
        first_marker = min(marker_positions)
        start = max(0, first_marker - 50)
        snippet = text[start : start + 200]
        return {"severity": SEVERITY_BINDING, "kind": "uncaught_exception", "detail": snippet, "text": snippet}
    snippet = text[:200]
    return {"severity": SEVERITY_NOTICE, "kind": "console_error", "detail": snippet, "text": snippet}


# (filename, classifier) pairs — the observed proof artifacts.
_SOURCES: Tuple[Tuple[str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]], ...] = (
    ("network_logs.json", _classify_network_entry),
    ("console_logs.json", _classify_console_entry),
)


def _read_new_lines(path: str, state_key: str) -> List[Tuple[Dict[str, Any], int]]:
    """Read complete JSONL lines appended since the last poll.

    Returns (entry, byte_offset_of_line_start) pairs. A half-written
    trailing line is left for the next poll; a file whose identity changed
    (recreated — even with exactly the previously-read size — or truncated)
    is re-read from the start so recreated content is never mistaken for
    already-consumed data. A genuine append-after-truncate that keeps inode
    and grows beyond the stored offset stays best-effort: it is
    indistinguishable from an ordinary append without buffering past bytes.
    Corrupt lines are skipped — evidence is judged, never trusted blindly.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return []
    size = stat.st_size
    identity = (stat.st_ino, stat.st_mtime_ns)
    offset = _OFFSETS.get(state_key, 0)
    last_identity = _FILE_IDENTITIES.get(state_key)
    if last_identity is not None:
        if stat.st_ino != last_identity[0]:
            # The file was recreated (new inode): read from the start, even
            # when the new size matches the stored offset.
            offset = 0
        elif size < offset:
            # Same file truncated in place: start over.
            offset = 0
        elif size == offset and stat.st_mtime_ns != last_identity[1]:
            # Rewritten to exactly the stored length: an append always grows
            # the file, so a same-size change is a same-size recreation.
            offset = 0
    _FILE_IDENTITIES[state_key] = identity
    _OFFSETS[state_key] = offset
    if size == offset:
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError as e:
        logger.debug("[INVARIANTS] unable to read %s: %s", path, e)
        return []
    end = data.rfind(b"\n")
    if end == -1:
        # Only an incomplete trailing line so far — nothing to judge yet.
        return []
    _OFFSETS[state_key] = offset + end + 1

    entries: List[Tuple[Dict[str, Any], int]] = []
    line_start = offset
    for raw_line in data[: end + 1].splitlines(keepends=True):
        start = line_start
        line_start += len(raw_line)
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            entry = json.loads(text)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        entries.append((entry, start))
    return entries


def poll_invariants(test_id: str, proof_path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Return new invariant findings since the last poll for ``test_id``.

    Shape: ``{"binding": [...], "notice": [...]}`` where each finding
    carries ``kind``, ``detail`` (status code / console text snippet) and
    source offset info (``source`` file name plus ``offset`` byte position).
    """
    findings: Dict[str, List[Dict[str, Any]]] = {SEVERITY_BINDING: [], SEVERITY_NOTICE: []}
    if not test_id:
        return findings
    base = proof_path if proof_path is not None else get_global_conf().get_proof_path(test_id)
    for filename, classifier in _SOURCES:
        for entry, offset in _read_new_lines(os.path.join(base, filename), _state_key(test_id, filename)):
            finding = classifier(entry)
            if finding is None:
                continue
            severity = finding.pop("severity")
            finding["source"] = filename
            finding["offset"] = offset
            findings[severity].append(finding)
    return findings


def record_invariant_findings(test_id: str, findings: Dict[str, List[Dict[str, Any]]]) -> None:
    """Append findings to the assertion ledger (binding => FAILED, notice => notice).

    Uses the same ledger line format as the deterministic assertion tools so
    the report side consumes one uniform stream. A ledger write problem is
    logged, never raised: the observer must not break the run it watches.
    """
    for severity, verdict in ((SEVERITY_BINDING, "FAILED"), (SEVERITY_NOTICE, "notice")):
        for finding in (findings or {}).get(severity, []):
            _record_invariant_finding(test_id, finding, verdict)


def _record_invariant_finding(test_id: str, finding: Dict[str, Any], verdict: str) -> None:
    source = str(finding.get("source") or "proof artifact")
    entry = {
        "source": "invariant",
        "test_id": test_id,
        "subject": assertion_subject(str(finding.get("detail", ""))),
        "path": str(finding.get("url") or source),
        "operator": str(finding.get("kind") or "invariant"),
        "expected": _EXPECTED_BY_KIND.get(str(finding.get("kind")), "no invariant violations"),
        "actual": finding.get("detail", ""),
        "verdict": verdict,
        "reason": f"{source} @ byte offset {finding.get('offset', '?')}",
    }
    try:
        assertion_logger(json.dumps(entry, separators=(",", ":"), default=str))
    except Exception as e:
        # The ledger must never turn an observation into a crash.
        logger.warning(f"Failed to append invariant finding to the assertion ledger: {e}")
