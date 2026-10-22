#!/usr/bin/env python3
"""Offline Gherkin linter for the case library.

Syntax is checked with the ``gherkin-official`` parser when it happens to be
installed; otherwise a regex-based structural lint runs instead (a warning is
printed once). On top of syntax, quality rules are enforced per scenario block:

- a scenario line must carry a title (error);
- steps must not be empty (error);
- scenario titles must be unique within a file (error);
- a scenario with steps but no Given/When/Then is a WARNING only (heuristic);
- blocked-marker lookalikes and blocked markers detached from their scenario are
  reported (error), mirroring the assembler.

When a regression manifest is supplied, every ``<type>/<module> :: <needle>``
entry is cross-checked against the scanned scenario titles; unmatched entries
are warnings.

This tool is informational: it exits 0 unless ``--strict`` is given, in which
case any error or warning fails. Never wired as a hard build gate.

Stdlib only; ``gherkin-official`` is lazy-imported and purely optional.
"""

import argparse
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

LOGGER = logging.getLogger(__name__)

DEFAULT_SCAN_PATH = "opt/cases"
DEFAULT_MANIFEST = "opt/cases/regression.txt"

FEATURE_RE = re.compile(r"^\s*Feature:\s*(.*?)\s*$")
SCENARIO_RE = re.compile(r"^\s*(?:Scenario|Scenario Outline):\s*(.*?)\s*$")
STEP_RE = re.compile(r"^\s*(?:Given|When|Then|And|But|\*)\s+(.*)$")
STEP_KEYWORD_ONLY_RE = re.compile(r"^\s*(?:Given|When|Then|And|But|\*)\s*$")
GWT_KEYWORD_RE = re.compile(r"^\s*(?:Given|When|Then)\s")
BLOCKED_TAG_RE = re.compile(r"^@blocked:([A-Za-z0-9_.-]+)$")
BLOCKED_REASON_RE = re.compile(r"^#\s*blocked:\s*(\S.*?)\s*$", re.IGNORECASE)
BLOCKED_COMMENT_RE = re.compile(r"^#\s*blocked\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int | None
    severity: str  # "error" | "warning"
    message: str


@dataclass
class LintResult:
    files_checked: int
    findings: list[Finding] = field(default_factory=list)
    syntax_checker: str = "regex-fallback"
    manifest_entries: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


@dataclass
class _ScenarioSpan:
    title: str
    run_start: int  # 0-based, first line of the tag/comment run
    scenario_idx: int  # 0-based, the Scenario: line
    end: int  # 0-based exclusive


def _gherkin_syntax_errors(text: str) -> list[str] | None:
    """Parse with gherkin-official; return error strings, or None when the lib is absent."""
    try:
        from gherkin.parser import Parser  # type: ignore[import-not-found]
    except ImportError:
        return None
    parser = Parser()
    try:
        parser.parse(text)
    except Exception as exc:  # noqa: BLE001 - parser raises its own exception types
        return [str(exc)]
    return []


def _scenario_spans(lines: list[str]) -> list[_ScenarioSpan]:
    starts: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        match = SCENARIO_RE.match(line)
        if match:
            starts.append((idx, match.group(1)))
    spans: list[_ScenarioSpan] = []
    for pos, (idx, title) in enumerate(starts):
        run_start = idx
        while run_start > 0:
            previous = lines[run_start - 1].strip()
            if previous.startswith("@") or previous.startswith("#"):
                run_start -= 1
            else:
                break
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        spans.append(_ScenarioSpan(title=title, run_start=run_start, scenario_idx=idx, end=end))
    return spans


def _attached_to_scenario(lines: list[str], idx: int) -> bool:
    """True when the @/#-line at ``idx`` belongs to the tag run of a scenario."""
    walk = idx
    while walk < len(lines):
        stripped = lines[walk].strip()
        if stripped.startswith("@") or stripped.startswith("#"):
            walk += 1
        else:
            break
    return walk < len(lines) and SCENARIO_RE.match(lines[walk]) is not None


def _detached_marker_problems(lines: list[str]) -> list[tuple[int, str]]:
    """Blocked markers not directly attached to a scenario, mirroring the assembler's abort.

    A ``@blocked:``/``# blocked:`` pair separated from its ``Scenario:`` by a blank line
    is emitted verbatim by the assembler and the supposedly blocked case runs anyway.
    """
    problems: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not (BLOCKED_TAG_RE.match(stripped) or BLOCKED_REASON_RE.match(stripped)):
            continue
        if _attached_to_scenario(lines, idx):
            continue
        what = "'@blocked:<category>' tag" if BLOCKED_TAG_RE.match(stripped) else "'# blocked:' reason"
        problems.append((idx + 1, f"{what} is detached from any Scenario line; move the '@blocked:'/'# blocked:' pair directly above the 'Scenario:' line"))
    return problems


def _marker_problems(lines: list[str], span: _ScenarioSpan, path: Path) -> list[tuple[int, str]]:
    """Blocked-marker lookalikes, mirroring the assembler's conventions. Returns (lineno, message) pairs."""
    problems: list[tuple[int, str]] = []
    run = [(run_idx, lines[run_idx].strip()) for run_idx in range(span.run_start, span.scenario_idx)]
    tag_positions: list[int] = []
    reason_positions: list[int] = []
    for offset, (_, entry) in enumerate(run):
        lineno = span.run_start + offset + 1
        if entry.startswith("@"):
            tokens = entry.split()
            if len(tokens) > 1:
                if any(token.lower().startswith("@block") for token in tokens):
                    problems.append((lineno, f"multiple tags on one line ({entry!r}); put each tag on its own line"))
                continue  # ordinary multi-tag line (e.g. '@ui @desktop'): not a blocked-marker problem
            token = tokens[0]
            if token.lower().startswith("@block") and not BLOCKED_TAG_RE.match(token):
                problems.append((lineno, f"malformed blocked tag {token!r}; expected '@blocked:<category>'"))
            elif BLOCKED_TAG_RE.match(token):
                tag_positions.append(offset)
        elif entry.startswith("#"):
            if BLOCKED_REASON_RE.match(entry):
                reason_positions.append(offset)
            elif BLOCKED_COMMENT_RE.match(entry):
                problems.append((lineno, f"malformed blocked comment {entry!r}; expected '# blocked: <reason>'"))
    if len(tag_positions) > 1:
        problems.append((span.run_start + tag_positions[0] + 1, "more than one '@blocked:' tag on one scenario"))
    if len(reason_positions) > 1:
        problems.append((span.run_start + reason_positions[0] + 1, "more than one '# blocked:' reason on one scenario"))
    if tag_positions and not reason_positions:
        problems.append((span.run_start + tag_positions[0] + 1, "'@blocked:<category>' tag without a '# blocked: <reason>' line below it"))
    if reason_positions and not tag_positions:
        problems.append((span.run_start + reason_positions[0] + 1, "'# blocked:' reason without an '@blocked:<category>' tag above it"))
    if tag_positions and reason_positions:
        if reason_positions[0] != tag_positions[0] + 1 or tag_positions[0] + 1 != len(run) - 1:
            problems.append(
                (
                    span.run_start + tag_positions[0] + 1,
                    "'@blocked:<category>' and '# blocked: <reason>' must sit as a pair directly above the Scenario line",
                )
            )
    for lineno, problem in problems:
        LOGGER.error("%s:%s: blocked-marker problem: %s", path, lineno, problem)
    return problems


def _lint_feature_file(path: Path, text: str) -> tuple[list[Finding], list[str]]:
    """Structural/quality checks for one file. Returns findings and scenario titles."""
    findings: list[Finding] = []
    lines = text.splitlines()

    feature_matches = [idx for idx, line in enumerate(lines) if FEATURE_RE.match(line)]
    if not feature_matches:
        findings.append(Finding(path, None, "error", "no 'Feature:' line found"))

    findings.extend(Finding(path, lineno, "error", message) for lineno, message in _detached_marker_problems(lines))

    titles: list[str] = []
    title_seen: dict[str, int] = {}
    for span in _scenario_spans(lines):
        lineno = span.scenario_idx + 1
        if not span.title:
            findings.append(Finding(path, lineno, "error", "scenario has no title"))
        else:
            titles.append(span.title)
            title_seen[span.title] = title_seen.get(span.title, 0) + 1

        body = lines[span.scenario_idx + 1 : span.end]
        steps = [line for line in body if STEP_RE.match(line) or STEP_KEYWORD_ONLY_RE.match(line)]
        if not steps:
            findings.append(Finding(path, lineno, "error", "scenario has no steps"))
        else:
            findings.extend(_step_findings(path, body, span.scenario_idx + 2))
            if not any(GWT_KEYWORD_RE.match(line) for line in steps):
                findings.append(Finding(path, lineno, "warning", "no Given/When/Then step found (heuristic warning only)"))
        findings.extend(Finding(path, lineno, "error", problem) for lineno, problem in _marker_problems(lines, span, path))

    for title, count in sorted(title_seen.items()):
        if count > 1:
            findings.append(Finding(path, None, "error", f"duplicate scenario title ({count} occurrences): {title!r}"))
    return findings, titles


def _step_findings(path: Path, body: list[str], first_body_line: int) -> list[Finding]:
    findings: list[Finding] = []
    for offset, line in enumerate(body):
        keyword_only = STEP_KEYWORD_ONLY_RE.match(line)
        step_match = STEP_RE.match(line)
        if keyword_only or (step_match and not step_match.group(1).strip()):
            findings.append(Finding(path, first_body_line + offset, "error", f"empty step ({line.strip()!r} carries no text)"))
    return findings


def _parse_manifest_entries(manifest_path: Path) -> list[tuple[int, str]]:
    """(lineno, raw entry) pairs; malformed lines are reported by the caller as warnings."""
    entries: list[tuple[int, str]] = []
    for lineno, raw in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append((lineno, line))
    return entries


def lint_paths(paths: Sequence[Path], manifest: Path | None = None) -> LintResult:
    feature_files: list[Path] = []
    for entry in paths:
        if entry.is_dir():
            feature_files.extend(sorted(entry.rglob("*.feature")))
        elif entry.is_file() and entry.suffix == ".feature":
            feature_files.append(entry)
        else:
            LOGGER.warning("lint input %s is not a feature file or directory; skipping", entry)
    feature_files = sorted(set(feature_files))

    result = LintResult(files_checked=len(feature_files))
    all_titles: list[str] = []
    syntax_available: bool | None = None
    for path in feature_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.findings.append(Finding(path, None, "error", f"unreadable file: {exc}"))
            continue
        syntax_errors = _gherkin_syntax_errors(text)
        if syntax_available is None:
            syntax_available = syntax_errors is not None
            result.syntax_checker = "gherkin-official" if syntax_available else "regex-fallback"
        if syntax_errors:
            result.findings.extend(Finding(path, None, "error", f"syntax: {message}") for message in syntax_errors)
        file_findings, titles = _lint_feature_file(path, text)
        result.findings.extend(file_findings)
        all_titles.extend(titles)

    if manifest is not None and manifest.is_file():
        entries = _parse_manifest_entries(manifest)
        result.manifest_entries = len(entries)
        for lineno, raw in entries:
            if "::" not in raw:
                result.findings.append(Finding(manifest, lineno, "warning", f"expected '<type>/<module> :: <needle>', got {raw!r}"))
                continue
            needle = raw.split("::", 1)[1].strip()
            if not any(needle in title for title in all_titles):
                result.findings.append(Finding(manifest, lineno, "warning", f"regression entry {raw!r} matched no scanned scenario title"))
    elif manifest is not None:
        LOGGER.warning("regression manifest %s not found; cross-check skipped", manifest)
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline Gherkin linter for the case library (informational).")
    parser.add_argument("paths", nargs="*", default=[DEFAULT_SCAN_PATH], help=f"feature files or directories to scan (default: {DEFAULT_SCAN_PATH})")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, help=f"regression manifest to cross-check (default: {DEFAULT_MANIFEST})")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any error or warning is found")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not _syntax_available():
        print("lint-gherkin: gherkin-official not installed; running the regex-based structural lint only", file=sys.stderr)
    result = lint_paths([Path(p) for p in args.paths], Path(args.manifest) if args.manifest else None)
    for finding in result.findings:
        location = f"{finding.path}:{finding.line}" if finding.line is not None else str(finding.path)
        print(f"{location}: {finding.severity}: {finding.message}")
    manifest_note = f"; manifest entries cross-checked: {result.manifest_entries}" if result.manifest_entries else "; manifest: not checked"
    print(f"lint-gherkin: {result.files_checked} file(s) checked, {len(result.errors)} error(s), " f"{len(result.warnings)} warning(s); syntax checker: {result.syntax_checker}{manifest_note}")
    if args.strict and result.findings:
        print("lint-gherkin: --strict: findings above are fatal", file=sys.stderr)
        return 1
    return 0


def _syntax_available() -> bool:
    try:
        import gherkin.parser  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
