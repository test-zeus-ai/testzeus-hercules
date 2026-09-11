#!/usr/bin/env python3
"""Assemble a runnable project base for one target from the case library.

Cases are written once and filed under the version that introduced them:

    opt/cases/<type>/<module>/<version>.feature
    opt/cases/<type>/<module>/test_data/          (optional, copied verbatim)

``--version V`` assembles exactly that version's files (no accumulation:
cross-release coverage is carried by an explicit regression manifest).
``--regression`` assembles an explicit set described by a manifest of
``<type>/<module> :: <scenario-title-substring>`` entries.

Output matches the layout the runner's bulk loop iterates
(``tests/<folder>/input/<folder>.feature`` + ``tests/<folder>/test_data``):

    opt/.run/<target>/tests/<type>-<module>/input/<type>-<module>.feature
    opt/.run/<target>/tests/<type>-<module>/test_data/...
    opt/.run/<target>/blocked.json                  (always written)

Non-runnable cases are excluded at the source with a two-line marker placed
directly above the scenario (a Gherkin tag cannot carry the reason text):

    @blocked:<category>
    # blocked: <human-readable reason>
    Scenario: ...

Malformed lookalikes (``@block:x``, a blocked-lookalike among multiple tags on
one line, a ``@blocked:``/``# blocked:`` pair detached from its ``Scenario:`` by
a blank line, a reason without its tag, a legacy free-form "not executable"
comment) abort the assembly instead of being tolerated -- tolerating them is
what recreates always-red runs.

Stdlib only. Exit codes: 0 assembled (or dry-run), 2 assembly aborted.
"""

import argparse
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

LOGGER = logging.getLogger(__name__)

DEFAULT_LIBRARY = "opt/cases"
DEFAULT_RUN_ROOT = "opt/.run"
DEFAULT_TYPES = "api,web,ui"
DEFAULT_MANIFEST_NAME = "regression.txt"
REGRESSION_TARGET = "regression"
BLOCKED_SCHEMA = "case-library-blocked/v1"
BLOCKED_FILENAME = "blocked.json"

VERSION_FILE_RE = re.compile(r"^v?(\d+(?:\.\d+)*)\.feature$")
VERSION_TEXT_RE = re.compile(r"^\d+(?:\.\d+)*$")
SCENARIO_RE = re.compile(r"^\s*(?:Scenario|Scenario Outline):\s*(.*?)\s*$")
BLOCKED_TAG_RE = re.compile(r"^@blocked:([A-Za-z0-9_.-]+)$")
BLOCKED_REASON_RE = re.compile(r"^#\s*blocked:\s*(\S.*?)\s*$", re.IGNORECASE)
BLOCKED_COMMENT_RE = re.compile(r"^#\s*blocked\b", re.IGNORECASE)
LEGACY_MARKER_PHRASE = "not executable"
TYPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AssemblyError(RuntimeError):
    """Raised when the case library violates a convention; assembly aborts."""


@dataclass(frozen=True)
class BlockedMarker:
    """A parsed ``@blocked:<category>`` + ``# blocked: <reason>`` pair."""

    category: str
    reason: str
    line: int  # 1-based line number of the tag line


@dataclass
class ScenarioBlock:
    """One scenario plus the tag/comment run directly above it."""

    title: str
    lines: list[str]
    start_line: int  # 1-based, first line of the tag/comment run
    scenario_line: int  # 1-based, the ``Scenario:`` line itself
    blocked: BlockedMarker | None = None


@dataclass
class SuitePlan:
    case_type: str
    module: str
    text: str
    scenario_count: int
    test_data_src: Path | None
    feature_path: Path
    test_data_path: Path


@dataclass
class AssemblyResult:
    target: str
    run_dir: Path
    suites: list[SuitePlan]
    excluded: list[dict[str, object]]
    manifest_entries: int | None
    dry_run: bool


def parse_version(text: str) -> tuple[int, ...]:
    """Parse ``10.0`` / ``v10.0`` into an integer tuple so 10.0 > 9.0."""
    cleaned = text.strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    if not cleaned or not VERSION_TEXT_RE.match(cleaned):
        raise AssemblyError(f"invalid version {text!r}: expected dot-separated integers like '10.0'")
    return tuple(int(part) for part in cleaned.split("."))


def _tag_run_start(lines: list[str], scenario_idx: int) -> int:
    """Index where the contiguous tag/comment run above a scenario starts."""
    start = scenario_idx
    while start > 0:
        previous = lines[start - 1].strip()
        if previous.startswith("@") or previous.startswith("#"):
            start -= 1
        else:
            break
    return start


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


def _check_detached_blocked_markers(lines: list[str], source: str) -> None:
    """Abort when a ``@blocked:``/``# blocked:`` line is not directly attached to a scenario.

    A marker pair separated from its ``Scenario:`` by a blank line lands in the header
    or in the previous block's tail, is emitted verbatim, and the supposedly blocked
    case runs with a zero ``blocked.json`` count -- exactly the always-red run the
    marker exists to prevent.
    """
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not (BLOCKED_TAG_RE.match(stripped) or BLOCKED_REASON_RE.match(stripped)):
            continue
        if _attached_to_scenario(lines, idx):
            continue  # attached: the per-scenario grammar checks in _analyze_markers apply
        what = "'@blocked:<category>' tag" if BLOCKED_TAG_RE.match(stripped) else "'# blocked:' reason"
        raise AssemblyError(
            f"{source}:{idx + 1}: {what} is detached from any Scenario line (a blank line between the marker pair and "
            "'Scenario:' makes the case run anyway); move the pair directly above the 'Scenario:' line"
        )


def _check_legacy_marker(lines: list[str], source: str) -> None:
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") and LEGACY_MARKER_PHRASE in stripped.lower():
            raise AssemblyError(f"{source}:{lineno}: legacy free-form marker {stripped!r}; " "replace it with '@blocked:<category>' plus '# blocked: <reason>' directly above the scenario")


def _analyze_markers(lines: list[str], run_start: int, scenario_idx: int, source: str) -> BlockedMarker | None:
    """Validate the tag/comment run above one scenario; return its blocked marker."""
    run = [lines[i].strip() for i in range(run_start, scenario_idx)]
    tag_idx: int | None = None
    reason_idx: int | None = None
    for offset, entry in enumerate(run):
        lineno = run_start + offset + 1
        if entry.startswith("@"):
            tokens = entry.split()
            if len(tokens) > 1:
                if any(token.lower().startswith("@block") for token in tokens):
                    raise AssemblyError(f"{source}:{lineno}: multiple tags on one line ({entry!r}); put each tag on its own line")
                continue  # ordinary multi-tag line (e.g. '@ui @desktop'): kept verbatim
            token = tokens[0]
            if token.lower().startswith("@block") and not BLOCKED_TAG_RE.match(token):
                raise AssemblyError(f"{source}:{lineno}: malformed blocked tag {token!r}; expected '@blocked:<category>'")
            if BLOCKED_TAG_RE.match(token):
                if tag_idx is not None:
                    raise AssemblyError(f"{source}:{lineno}: more than one '@blocked:' tag on one scenario")
                tag_idx = offset
        elif entry.startswith("#"):
            if LEGACY_MARKER_PHRASE in entry.lower():
                raise AssemblyError(f"{source}:{lineno}: legacy free-form marker {entry!r}; " "use '@blocked:<category>' plus '# blocked: <reason>' directly above the scenario")
            if BLOCKED_REASON_RE.match(entry):
                if reason_idx is not None:
                    raise AssemblyError(f"{source}:{lineno}: more than one '# blocked:' reason on one scenario")
                reason_idx = offset
            elif BLOCKED_COMMENT_RE.match(entry):
                raise AssemblyError(f"{source}:{lineno}: malformed blocked comment {entry!r}; expected '# blocked: <reason>'")
    if tag_idx is None:
        if reason_idx is not None:
            raise AssemblyError(f"{source}:{run_start + reason_idx + 1}: '# blocked:' reason without an '@blocked:<category>' tag directly above it")
        return None
    if reason_idx != tag_idx + 1 or tag_idx + 1 != len(run) - 1:
        raise AssemblyError(
            f"{source}:{run_start + tag_idx + 1}: '@blocked:<category>' must be immediately followed by a " "'# blocked: <reason>' line, and the pair must sit directly above the Scenario line"
        )
    tag = BLOCKED_TAG_RE.match(run[tag_idx])
    reason = BLOCKED_REASON_RE.match(run[reason_idx])
    assert tag is not None and reason is not None  # both matched above
    return BlockedMarker(category=tag.group(1), reason=reason.group(1), line=run_start + tag_idx + 1)


def find_scenarios(text: str, source: str) -> tuple[list[str], list[ScenarioBlock]]:
    """Split a feature file into its header and its scenario blocks.

    Each block includes the tag/comment run above the ``Scenario:`` line --
    dropping it would silently attach a ``@blocked`` marker to the neighbouring
    scenario. Raises :class:`AssemblyError` on convention violations.
    """
    lines = text.splitlines()
    _check_legacy_marker(lines, source)
    _check_detached_blocked_markers(lines, source)
    starts: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        match = SCENARIO_RE.match(line)
        if match:
            starts.append((idx, _tag_run_start(lines, idx), match.group(1)))

    first_run = starts[0][1] if starts else len(lines)
    header = lines[:first_run]
    while header and not header[-1].strip():
        header.pop()
    if not any(line.strip().startswith("Feature:") for line in header):
        raise AssemblyError(f"{source}: no 'Feature:' line found before the first scenario")

    blocks: list[ScenarioBlock] = []
    for pos, (idx, run_start, title) in enumerate(starts):
        # End the block at the NEXT block's tag-run start, never at the bare Scenario:
        # line -- otherwise the next scenario's markers leak into this block's tail.
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(lines)
        while end > idx + 1 and not lines[end - 1].strip():
            end -= 1
        blocks.append(
            ScenarioBlock(
                title=title,
                lines=lines[run_start:end],
                start_line=run_start + 1,
                scenario_line=idx + 1,
                blocked=_analyze_markers(lines, run_start, idx, source),
            )
        )
    return header, blocks


def _emit_block_lines(block: ScenarioBlock) -> list[str]:
    """Block lines with the blocked marker stripped (the runner's splitter cuts at ``Scenario:``)."""
    out = list(block.lines)
    if block.blocked is not None:
        first = block.blocked.line - block.start_line
        del out[first : first + 2]
    return out


@dataclass(frozen=True)
class _VersionFile:
    version: tuple[int, ...]
    path: Path


def _discover_modules(library: Path, types: Sequence[str]) -> dict[str, dict[str, list[_VersionFile]]]:
    """type -> module -> version-sorted list of version files (duplicates abort)."""
    discovered: dict[str, dict[str, list[_VersionFile]]] = {}
    for case_type in sorted(types):
        type_dir = library / case_type
        if not type_dir.is_dir():
            LOGGER.warning("case type directory %s does not exist; skipping", type_dir)
            continue
        modules: dict[str, list[_VersionFile]] = {}
        for module_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
            files: list[_VersionFile] = []
            for candidate in sorted(module_dir.iterdir()):
                match = VERSION_FILE_RE.match(candidate.name)
                if candidate.is_file() and match:
                    files.append(_VersionFile(version=tuple(int(part) for part in match.group(1).split(".")), path=candidate))
            files.sort(key=lambda item: (item.version, item.path.name))
            seen: dict[tuple[int, ...], str] = {}
            for item in files:
                if item.version in seen:
                    raise AssemblyError(f"{module_dir}: duplicate version {item.path.name} vs {seen[item.version]}; keep one file per version")
                seen[item.version] = item.path.name
            if files:
                modules[module_dir.name] = files
        discovered[case_type] = modules
    return discovered


@dataclass(frozen=True)
class _ManifestEntry:
    lineno: int
    raw: str
    case_type: str
    module: str
    needle: str


def _parse_manifest(manifest_path: Path) -> list[_ManifestEntry]:
    entries: list[_ManifestEntry] = []
    for lineno, raw in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "::" not in line:
            raise AssemblyError(f"{manifest_path}:{lineno}: expected '<type>/<module> :: <scenario-title-substring>', got {line!r}")
        ref, needle = line.split("::", 1)
        parts = ref.strip().split("/")
        if len(parts) != 2 or not parts[0] or not parts[1] or not needle.strip():
            raise AssemblyError(f"{manifest_path}:{lineno}: expected '<type>/<module> :: <scenario-title-substring>', got {line!r}")
        entries.append(_ManifestEntry(lineno=lineno, raw=line, case_type=parts[0], module=parts[1], needle=needle.strip()))
    return entries


def _read_module_scenarios(files: Sequence[_VersionFile]) -> tuple[list[str] | None, list[tuple[_VersionFile, ScenarioBlock]]]:
    """Header of the first file that has one, plus every scenario of every file."""
    header: list[str] | None = None
    scenarios: list[tuple[_VersionFile, ScenarioBlock]] = []
    for item in files:
        file_header, blocks = find_scenarios(item.path.read_text(encoding="utf-8"), str(item.path))
        if header is None and file_header:
            header = file_header
        scenarios.extend((item, block) for block in blocks)
    return header, scenarios


def _assemble_version_mode(
    library: Path,
    discovered: dict[str, dict[str, list[_VersionFile]]],
    version: tuple[int, ...],
    include_blocked: bool,
) -> tuple[list[tuple[str, str, list[str] | None, list[ScenarioBlock]]], list[dict[str, object]]]:
    """Return per-suite (type, module, header, included blocks) plus excluded records."""
    wanted_name = ".".join(str(part) for part in version)
    selected: list[tuple[str, str, list[_VersionFile]]] = []
    found_any = False
    for case_type in sorted(discovered):
        for module in sorted(discovered[case_type]):
            exact = [item for item in discovered[case_type][module] if item.version == version]
            if exact:
                selected.append((case_type, module, exact))
                found_any = True
    if not found_any:
        raise AssemblyError(f"no case file for version {wanted_name} under {library} " f"(expected e.g. {library}/<type>/<module>/{wanted_name}.feature)")

    suites: list[tuple[str, str, list[str] | None, list[ScenarioBlock]]] = []
    excluded: list[dict[str, object]] = []
    for case_type, module, files in selected:
        header, scenarios = _read_module_scenarios(files)
        included: list[ScenarioBlock] = []
        for _, block in scenarios:
            marker = block.blocked
            if marker is not None and not include_blocked:
                excluded.append(
                    {
                        "type": case_type,
                        "module": module,
                        "scenario": block.title,
                        "category": marker.category,
                        "reason": marker.reason,
                        "source_file": str(files[0].path),
                        "source_line": marker.line,
                    }
                )
                continue
            included.append(block)
        if included:
            suites.append((case_type, module, header, included))
    return suites, excluded


def _assemble_regression_mode(
    manifest_path: Path,
    discovered: dict[str, dict[str, list[_VersionFile]]],
    include_blocked: bool,
) -> tuple[list[tuple[str, str, list[str] | None, list[ScenarioBlock]]], list[dict[str, object]], int, int]:
    """Return per-suite (type, module, header, included blocks), excluded records, the
    manifest entry count, and how many duplicate titles the merge dropped."""
    entries = _parse_manifest(manifest_path)
    available = sorted(f"{case_type}/{module}" for case_type, modules in discovered.items() for module in modules)
    for entry in entries:
        if f"{entry.case_type}/{entry.module}" not in available:
            raise AssemblyError(f"{manifest_path}:{entry.lineno}: unknown module '{entry.case_type}/{entry.module}'. " f"Existing modules: {', '.join(available) if available else '(none)'}")

    scenarios_by_ref: dict[tuple[str, str], tuple[list[_VersionFile], list[tuple[_VersionFile, ScenarioBlock]]]] = {}
    for case_type, modules in discovered.items():
        for module, files in modules.items():
            if f"{case_type}/{module}" in {f"{e.case_type}/{e.module}" for e in entries}:
                header, scenarios = _read_module_scenarios(files)
                scenarios_by_ref[(case_type, module)] = (files, scenarios)

    suites: list[tuple[str, str, list[str] | None, list[ScenarioBlock]]] = []
    excluded: list[dict[str, object]] = []
    selected: dict[tuple[str, str], list[tuple[_VersionFile, ScenarioBlock]]] = {}
    seen_ids: set[tuple[Path, int]] = set()
    for entry in entries:
        files, scenarios = scenarios_by_ref[(entry.case_type, entry.module)]
        matched = [(item, block) for item, block in scenarios if entry.needle in block.title]
        if not matched:
            titles = [block.title for _, block in scenarios if block.title]
            hint = "; ".join(repr(t) for t in titles[:10]) or "(no scenarios found)"
            raise AssemblyError(f"{manifest_path}:{entry.lineno}: entry {entry.raw!r} matched no scenario in " f"{entry.case_type}/{entry.module}. Scenario titles available: {hint}")
        if all(block.blocked is not None for _, block in matched):
            raise AssemblyError(
                f"{manifest_path}:{entry.lineno}: entry {entry.raw!r} only matches blocked case(s); " "remove the entry or unblock the case -- regression coverage must not point at excluded cases"
            )
        for item, block in matched:
            if block.blocked is not None and not include_blocked:
                excluded.append(
                    {
                        "type": entry.case_type,
                        "module": entry.module,
                        "scenario": block.title,
                        "category": block.blocked.category,
                        "reason": block.blocked.reason,
                        "source_file": str(item.path),
                        "source_line": block.blocked.line,
                    }
                )
                continue
            identity = (item.path, block.start_line)
            if identity in seen_ids:
                continue
            seen_ids.add(identity)
            selected.setdefault((entry.case_type, entry.module), []).append((item, block))

    dropped_duplicates = 0
    for (case_type, module), picked in sorted(selected.items()):
        header: list[str] | None = None
        for item, _ in picked:  # header comes from the first file that actually contributes
            file_header, _ = find_scenarios(item.path.read_text(encoding="utf-8"), str(item.path))
            if file_header:
                header = file_header
                break
        # Merging versions can pull the same title in twice; the project's own
        # linter rejects duplicate titles, so keep the first occurrence.
        deduped: list[ScenarioBlock] = []
        seen_titles: set[str] = set()
        for _, block in picked:
            if block.title in seen_titles:
                dropped_duplicates += 1
                continue
            seen_titles.add(block.title)
            deduped.append(block)
        suites.append((case_type, module, header, deduped))
    return suites, excluded, len(entries), dropped_duplicates


def _materialize(
    suites: list[tuple[str, str, list[str] | None, list[ScenarioBlock]]],
    library: Path,
    run_dir: Path,
    dry_run: bool,
) -> list[SuitePlan]:
    plans: list[SuitePlan] = []
    for case_type, module, header, blocks in suites:
        assert header is not None
        suite_name = f"{case_type}-{module}"
        suite_dir = run_dir / "tests" / suite_name
        feature_path = suite_dir / "input" / f"{suite_name}.feature"
        parts = ["\n".join(header)] + ["\n".join(_emit_block_lines(block)) for block in blocks]
        text = "\n\n".join(parts) + "\n"
        test_data_src = library / case_type / module / "test_data"
        plans.append(
            SuitePlan(
                case_type=case_type,
                module=module,
                text=text,
                scenario_count=len(blocks),
                test_data_src=test_data_src if test_data_src.is_dir() else None,
                feature_path=feature_path,
                test_data_path=suite_dir / "test_data",
            )
        )
    if dry_run:
        return plans
    # Containment guard behind the --types validation: no materialized suite path
    # may escape <run_dir>/tests.
    tests_root = (run_dir / "tests").resolve()
    for plan in plans:
        for path in (plan.feature_path, plan.test_data_path):
            if not path.resolve().is_relative_to(tests_root):
                raise AssemblyError(f"materialized path {path} escapes {tests_root}; aborting assembly")
    for plan in plans:
        plan.feature_path.parent.mkdir(parents=True, exist_ok=True)
        plan.feature_path.write_text(plan.text, encoding="utf-8")
        if plan.test_data_src is not None:
            shutil.copytree(plan.test_data_src, plan.test_data_path, dirs_exist_ok=True)
    return plans


def assemble(
    library: Path,
    run_root: Path,
    version: str | None = None,
    manifest: Path | None = None,
    types: Sequence[str] = tuple(DEFAULT_TYPES.split(",")),
    include_blocked: bool = False,
    dry_run: bool = False,
) -> AssemblyResult:
    """Assemble suites for one version or a regression manifest into ``run_root``."""
    library = library.resolve()
    if not library.is_dir():
        raise AssemblyError(f"case library not found at {library}")
    normalized_types = [t.strip() for t in types if t.strip()]
    if not normalized_types:
        raise AssemblyError("--types resolved to an empty set")
    for case_type in normalized_types:
        if not TYPE_NAME_RE.match(case_type):
            raise AssemblyError(f"invalid case type {case_type!r}: expected a simple name matching {TYPE_NAME_RE.pattern} " "(no path separators, no leading dot)")
    discovered = _discover_modules(library, normalized_types)

    if version is not None:
        target = ".".join(str(part) for part in parse_version(version))
        suites, excluded = _assemble_version_mode(library, discovered, parse_version(version), include_blocked)
        manifest_entries: int | None = None
        dropped_duplicates = 0
    else:
        assert manifest is not None
        target = REGRESSION_TARGET
        suites, excluded, manifest_entries, dropped_duplicates = _assemble_regression_mode(manifest.resolve(), discovered, include_blocked)

    if not suites:
        raise AssemblyError(f"assembly produced zero suites for target {target!r}; nothing to run")

    run_dir = run_root.resolve() / target
    plans = _materialize(suites, library, run_dir, dry_run)

    if not dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": BLOCKED_SCHEMA,
            "target": target,
            "include_blocked": include_blocked,
            "count": len(excluded),
            "excluded": excluded,
        }
        (run_dir / BLOCKED_FILENAME).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    prefix = "(dry-run) " if dry_run else ""
    print(f"{prefix}assembled target '{target}' -> {run_dir}")
    for plan in plans:
        data_note = " + test_data" if plan.test_data_src is not None else ""
        print(f"{prefix}  suite {plan.case_type}-{plan.module}: {plan.scenario_count} scenario(s) -> {plan.feature_path}{data_note}")
    if excluded:
        print(f"{prefix}excluded (blocked) cases:")
        for record in excluded:
            print(f"{prefix}  {record['type']}/{record['module']} :: {record['scenario']} " f"[{record['category']}] {record['reason']} ({record['source_file']}:{record['source_line']})")
    elif include_blocked:
        print(f"{prefix}blocked cases included by --include-blocked; nothing excluded")
    if dropped_duplicates:
        print(f"{prefix}dropped {dropped_duplicates} duplicate scenario(s) with the same title during the regression merge (kept the first occurrence of each)")
    manifest_note = f", {manifest_entries} manifest entr{'y' if manifest_entries == 1 else 'ies'}" if manifest_entries is not None else ""
    print(f"{prefix}summary: {len(plans)} suite(s), {sum(p.scenario_count for p in plans)} scenario(s), {len(excluded)} excluded{manifest_note}")
    if dry_run:
        print(f"{prefix}dry-run: no files written")
    return AssemblyResult(target=target, run_dir=run_dir, suites=plans, excluded=excluded, manifest_entries=manifest_entries, dry_run=dry_run)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble a runnable project base from the case library.")
    parser.add_argument("--library", default=DEFAULT_LIBRARY, help=f"case library root (default: {DEFAULT_LIBRARY})")
    parser.add_argument("--run-root", default=DEFAULT_RUN_ROOT, help=f"assembly output root (default: {DEFAULT_RUN_ROOT})")
    parser.add_argument("--types", default=DEFAULT_TYPES, help=f"comma-separated case types, simple names only (default: {DEFAULT_TYPES})")
    parser.add_argument("--include-blocked", action="store_true", help="assemble blocked cases anyway")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing anything")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--version", help="assemble exactly this version's files, e.g. 10.0 (leading 'v' tolerated)")
    group.add_argument(
        "--regression",
        nargs="?",
        const=DEFAULT_MANIFEST_NAME,
        default=None,
        metavar="MANIFEST",
        help="assemble the set listed in a regression manifest (default path: <library>/regression.txt)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    library = Path(args.library)
    run_root = Path(args.run_root)
    manifest: Path | None = None
    if args.regression is not None:
        manifest = library / DEFAULT_MANIFEST_NAME if args.regression == DEFAULT_MANIFEST_NAME else Path(args.regression)
    try:
        assemble(
            library=library,
            run_root=run_root,
            version=args.version,
            manifest=manifest,
            types=tuple(args.types.split(",")),
            include_blocked=args.include_blocked,
            dry_run=args.dry_run,
        )
    except AssemblyError as exc:
        print(f"assembly aborted: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
