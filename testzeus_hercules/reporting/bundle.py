"""Failure bundle creation for failed testcases.

Collects the evidence referenced by a merged JUnit XML (video, screenshots,
network/console logs, planner thoughts, Playwright traces) into self-contained
per-testcase folders, each with a readable ``bundle.md`` summary.
"""

import glob
import hashlib
import math
import os
import re
import shutil
import unicodedata
import zipfile

from junitparser import JUnitXml
from junitparser.junitparser import Properties
from testzeus_hercules.utils.logger import logger

# Exact property names written by JUnitXMLGenerator.add_test_case (junit_helper.py).
PROP_VIDEO = "Proofs Video"
PROP_SCREENSHOT = "Proofs Screenshot"
PROP_PROOFS_BASE = "Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs"
PROP_NETWORK_LOGS = "Network Logs"
PROP_INTERNAL_LOGS = "Agents Internal Logs"
PROP_PLANNER_THOUGHTS = "Planner Thoughts"

PROP_VALUE_TRUNCATE_LEN = 200
DOM_EXTRACT_LIMIT = 5
UNCLASSIFIED_TEXT = "Unclassified (auto-classification pending)"


def sanitize_name(name: str) -> str:
    """Sanitize a testcase name into a filesystem-safe directory name.

    Uses the same character replacements as stake_id derivation in __main__.py.
    """
    return name.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace(".", "_")


def _fold_dir_name(name: str) -> str:
    """Fold a candidate bundle dir name for uniqueness claims.

    macOS and Windows filesystems match paths case-insensitively and treat NFC
    and NFD spellings of the same text as one entry, so two names that differ
    only by case or Unicode normalization would be distinct here yet silently
    overwrite each other on disk. Claims are compared on this folded key while
    the returned dir names keep their original sanitized form.
    """
    return unicodedata.normalize("NFC", name).lower()


def resolve_bundle_dir_names(testcase_names: list[str]) -> dict[str, str]:
    """Build the per-run mapping of testcase names to unique bundle directory names.

    ``sanitize_name`` alone is not injective ("a/b" and "a_b" collapse to the
    same directory), so names whose sanitized form is already unique within the
    run keep their legacy name, while a name colliding with a DIFFERENT testcase
    gets a short hash of its raw name appended (extended further on the rare
    chance the hashed name is taken too). Uniqueness is judged on the
    filesystem-folded key (case- and normalization-insensitive) so macOS/Windows
    path merging cannot make one bundle overwrite another; the returned dir
    names themselves are never renamed beyond the hash suffix.
    """
    mapping: dict[str, str] = {}
    claimed: dict[str, str] = {}  # folded bundle dir name -> testcase name that owns it
    for raw_name in testcase_names:
        safe = sanitize_name(raw_name)
        folded = _fold_dir_name(safe)
        owner = claimed.get(folded)
        if owner is None:
            mapping[raw_name] = safe
            claimed[folded] = raw_name
            continue
        if owner == raw_name:
            # The same testcase name appears twice: one shared bundle dir.
            mapping[raw_name] = safe
            continue
        digest = hashlib.sha1(raw_name.encode("utf-8"), usedforsecurity=False).hexdigest()
        length = 8
        dir_name = f"{safe}_{digest[:length]}"
        while _fold_dir_name(dir_name) in claimed and claimed[_fold_dir_name(dir_name)] != raw_name:
            length += 1
            dir_name = f"{safe}_{digest[:length]}"
        mapping[raw_name] = dir_name
        claimed[_fold_dir_name(dir_name)] = raw_name
    return mapping


def _first_existing(*candidates: str) -> str | None:
    """Return the first candidate path that exists, or None."""
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _resolve_file(value: str, dir_children: tuple[str, ...] = ()) -> str | None:
    """Resolve a property value to an existing file.

    The value may point at the file itself or at a directory containing it;
    when it is a directory, the given child names (in order) are tried.
    """
    path = _first_existing(value) if value else None
    if path is None:
        return None
    if os.path.isdir(path):
        return _first_existing(*(os.path.join(path, child) for child in dir_children))
    return path


def _stays_within(path: str, base: str) -> bool:
    """True when the fully-resolved path remains inside base (symlinks cannot escape)."""
    real_base = os.path.realpath(base)
    return os.path.realpath(path).startswith(real_base + os.sep)


def _find_in_tree(base: str, filename: str, cache: dict | None = None) -> str | None:
    """Find a file by name directly under base, or anywhere beneath it (best effort).

    Symlinks are skipped so evidence stored outside the tree cannot leak into a bundle.
    ``cache`` (optional) memoizes results per (base, filename) across the many
    per-testcase calls of one bundle run, so a shared proofs tree is walked once.
    """
    if not base or not os.path.isdir(base):
        return None
    cache_key = (os.path.abspath(base), filename)
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    direct = os.path.join(base, filename)
    result: str | None = direct
    if not (os.path.isfile(direct) and not os.path.islink(direct)):
        result = next(
            (path for path in sorted(glob.glob(os.path.join(base, "**", filename), recursive=True)) if os.path.isfile(path) and not os.path.islink(path) and _stays_within(path, base)),
            None,
        )
    if cache is not None:
        cache[cache_key] = result
    return result


def parse_junit_testcases(junit_xml_path: str) -> list[dict]:
    """Parse a merged JUnit XML file into a list of testcase dicts.

    Args:
        junit_xml_path (str): Path to the merged JUnit XML file.

    Returns:
        list[dict]: One dict per testcase with keys: name, classname,
            status ("passed" | "failed" | "error"), failure_message,
            system_out, properties (dict[str, str]) and time (float).
    """
    parsed = JUnitXml.fromfile(junit_xml_path)
    # JUnitXml.fromfile returns a TestSuite when the root element is <testsuite>.
    suites = parsed if isinstance(parsed, JUnitXml) else [parsed]

    testcases: list[dict] = []
    for suite in suites:
        for test_case in suite:
            results = test_case.result or []
            if not isinstance(results, list):
                results = [results]

            status = "passed"
            messages: list[str] = []
            for result in results:
                tag = getattr(result, "_tag", "")
                if tag == "error":
                    status = "error"
                elif tag == "failure" and status != "error":
                    status = "failed"
                message = getattr(result, "message", None) or getattr(result, "text", None)
                if message:
                    messages.append(str(message))

            properties: dict[str, str] = {}
            try:
                props_elem = test_case.child(Properties)
                if props_elem is not None:
                    for prop in props_elem:
                        if prop.name is not None:
                            properties[prop.name] = prop.value or ""
            except Exception as exc:
                logger.warning("Could not read properties of testcase %s: %s", test_case.name, exc)

            system_out = _read_system_out(test_case)
            try:
                duration = float(test_case.time or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            if not math.isfinite(duration) or duration <= 0:
                # Negative, NaN or infinite junit time attributes are junk; report 0.
                duration = 0.0

            testcases.append(
                {
                    "name": test_case.name or "",
                    "classname": test_case.classname or "",
                    "status": status,
                    "failure_message": "\n".join(messages) if messages else None,
                    "system_out": system_out,
                    "properties": properties,
                    "time": duration,
                }
            )
    return testcases


def _read_system_out(test_case) -> str:
    """Read (and join) the system-out content of a testcase."""
    parts: list[str] = []
    try:
        # junit_helper.py appends multiple SystemOut elements per testcase;
        # the public getter only exposes the first one.
        elem = getattr(test_case, "_elem", None)
        if elem is not None:
            parts = [(child.text or "") for child in elem.findall("system-out")]
    except Exception as exc:
        logger.debug("Falling back to junitparser system_out getter: %s", exc)
    if not parts:
        out = test_case.system_out
        parts = [out] if out else []
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _copy_into_bundle(src: str, bundle_dir: str, rel_dest: str, artifacts: list[str]) -> None:
    """Copy a file into the bundle, never raising on failure."""
    if os.path.islink(src):
        # A symlink inside the evidence tree may point anywhere; never follow it.
        logger.warning("Skipping symlinked evidence %s for the failure bundle.", src)
        return
    try:
        dest = os.path.join(bundle_dir, rel_dest)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(src, dest)
        artifacts.append(rel_dest)
    except Exception as exc:
        logger.warning("Could not copy %s into failure bundle: %s", src, exc)


def _bundle_videos(props: dict[str, str], bundle_dir: str, artifacts: list[str]) -> None:
    video_path = _resolve_file(props.get(PROP_VIDEO, ""), ("recording.webm", "video.webm"))
    if video_path is None:
        return
    if os.path.isdir(video_path):
        videos = sorted(os.path.join(video_path, name) for name in os.listdir(video_path) if os.path.isfile(os.path.join(video_path, name)) and not os.path.islink(os.path.join(video_path, name)))
        if len(videos) == 1:
            _copy_into_bundle(videos[0], bundle_dir, "video.webm", artifacts)
        else:
            for video in videos:
                _copy_into_bundle(video, bundle_dir, os.path.basename(video), artifacts)
    else:
        _copy_into_bundle(video_path, bundle_dir, "video.webm", artifacts)


def _bundle_json_logs(props: dict[str, str], proofs_base: str | None, bundle_dir: str, artifacts: list[str], tree_cache: dict | None = None) -> None:
    network = _resolve_file(props.get(PROP_NETWORK_LOGS, ""), ("network_logs.json",))
    if network:
        _copy_into_bundle(network, bundle_dir, "network.json", artifacts)

    planner = _resolve_file(props.get(PROP_PLANNER_THOUGHTS, ""), ("planner_thoughts.json", "agent_inner_thoughts.json"))
    if planner:
        _copy_into_bundle(planner, bundle_dir, "planner_thoughts.json", artifacts)

    console = _find_in_tree(proofs_base or "", "console_logs.json", tree_cache)
    if console:
        _copy_into_bundle(console, bundle_dir, "console.json", artifacts)


def _resolve_screenshots_dir(props: dict[str, str], proofs_base: str | None) -> str | None:
    shot_value = props.get(PROP_SCREENSHOT, "")
    shot_path = _first_existing(shot_value) if shot_value else None
    if shot_path is not None:
        dir_path = shot_path if os.path.isdir(shot_path) else os.path.dirname(shot_path)
        if dir_path and not os.path.islink(dir_path):
            return dir_path
    if proofs_base and os.path.isdir(proofs_base) and not os.path.islink(proofs_base):
        candidate = os.path.join(proofs_base, "screenshots")
        if os.path.isdir(candidate) and not os.path.islink(candidate):
            return candidate
    return None


def _bundle_screenshots(props: dict[str, str], proofs_base: str | None, bundle_dir: str, max_screenshots: int, artifacts: list[str]) -> None:
    screenshots_dir = _resolve_screenshots_dir(props, proofs_base)
    if not screenshots_dir:
        return

    png_paths = [path for path in glob.glob(os.path.join(screenshots_dir, "*.png")) if not os.path.islink(path)]
    # Most recent first; ties fall back to the path for deterministic order.
    png_paths.sort(key=lambda path: (os.path.getmtime(path), path), reverse=True)

    selected = png_paths[:max_screenshots]
    # Renumber oldest-first so the narrative order matches chronology.
    selected.sort(key=lambda path: (os.path.getmtime(path), path))
    for index, src in enumerate(selected, start=1):
        _copy_into_bundle(src, bundle_dir, os.path.join("screenshots", f"step_{index:02d}.png"), artifacts)

    # Screenshots whose name mentions a failure are always included, even
    # outside the most-recent window.
    already_copied = {os.path.basename(src) for src in selected}
    for src in png_paths:
        if "fail" in os.path.basename(src).lower() and os.path.basename(src) not in already_copied:
            _copy_into_bundle(src, bundle_dir, os.path.join("screenshots", os.path.basename(src)), artifacts)


def _bundle_internal_logs(props: dict[str, str], bundle_dir: str, artifacts: list[str], tree_cache: dict | None = None) -> None:
    logs = _resolve_file(props.get(PROP_INTERNAL_LOGS, ""), ("agent_inner_thoughts.json",))
    if logs is None:
        return
    if os.path.isdir(logs):
        logs = _find_in_tree(logs, "agent_inner_thoughts.json", tree_cache)
    if logs:
        _copy_into_bundle(logs, bundle_dir, "agent_inner_thoughts.json", artifacts)


def _extract_trace_dom(zip_path: str, bundle_dir: str) -> list[str]:
    """Extract the largest .html entries of a Playwright trace zip into dom/.

    Returns the relative paths of extracted files; never raises.
    """
    extracted: list[str] = []
    dom_dir = os.path.join(bundle_dir, "dom")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            html_entries = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith(".html")]
            html_entries.sort(key=lambda info: info.file_size, reverse=True)
            if not html_entries:
                return extracted
            os.makedirs(dom_dir, exist_ok=True)
            for index, info in enumerate(html_entries[:DOM_EXTRACT_LIMIT], start=1):
                entry_name = os.path.basename(info.filename) or f"page_{index}.html"
                dest = os.path.join(dom_dir, entry_name)
                with archive.open(info) as src, open(dest, "wb") as out_file:
                    shutil.copyfileobj(src, out_file)
                extracted.append(os.path.join("dom", entry_name))
    except Exception as exc:
        logger.warning("Could not extract DOM snapshots from trace %s: %s", zip_path, exc)
        for rel_path in extracted:
            try:
                os.remove(os.path.join(bundle_dir, rel_path))
            except OSError:
                pass
        extracted = []
    return extracted


def _bundle_trace(props: dict[str, str], proofs_base: str | None, bundle_dir: str, artifacts: list[str]) -> None:
    bases = [base for base in (proofs_base, _first_existing(props.get(PROP_VIDEO, "")), _first_existing(props.get(PROP_SCREENSHOT, ""))) if base]
    trace_zip: str | None = None
    for base in bases:
        base_dir = base if os.path.isdir(base) else os.path.dirname(base)
        if not base_dir:
            continue
        trace_zip = next((path for path in sorted(glob.glob(os.path.join(base_dir, "*.zip"))) if os.path.isfile(path)), None)
        if trace_zip:
            break
    if not trace_zip:
        return
    _copy_into_bundle(trace_zip, bundle_dir, "trace.zip", artifacts)
    for rel_path in _extract_trace_dom(trace_zip, bundle_dir):
        artifacts.append(rel_path)


def _bundle_evidence(props: dict[str, str], bundle_dir: str, max_screenshots: int, tree_cache: dict | None = None) -> list[str]:
    """Copy all referenced evidence into bundle_dir; returns relative artifact paths."""
    artifacts: list[str] = []
    proofs_base = _first_existing(props.get(PROP_PROOFS_BASE, ""))
    if proofs_base and os.path.isfile(proofs_base):
        proofs_base = os.path.dirname(proofs_base)

    _bundle_videos(props, bundle_dir, artifacts)
    _bundle_json_logs(props, proofs_base, bundle_dir, artifacts, tree_cache)
    _bundle_screenshots(props, proofs_base, bundle_dir, max_screenshots, artifacts)
    _bundle_internal_logs(props, bundle_dir, artifacts, tree_cache)
    _bundle_trace(props, proofs_base, bundle_dir, artifacts)
    return artifacts


def _escape_inline(value: str) -> str:
    """Make arbitrary text safe inline in markdown (collapse newlines, escape pipes)."""
    return str(value or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _code_fence(content: str) -> str:
    """Pick a backtick fence that cannot be broken by the fenced content.

    One longer than the longest backtick run inside the content (minimum 3).
    """
    longest_run = max((len(run) for run in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest_run + 1)


def _truncate(value: str, limit: int = PROP_VALUE_TRUNCATE_LEN) -> str:
    value = _escape_inline(value)
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _list_bundled_files(bundle_dir: str) -> list[str]:
    """List every file in the bundle (relative paths, sorted), excluding bundle.md itself."""
    bundled: list[str] = []
    for root, _dirs, files in os.walk(bundle_dir):
        for file_name in files:
            if file_name == "bundle.md" and os.path.abspath(root) == os.path.abspath(bundle_dir):
                continue
            full = os.path.join(root, file_name)
            bundled.append(os.path.relpath(full, bundle_dir))
    return sorted(bundled)


def _write_bundle_md(bundle_dir: str, testcase: dict) -> None:
    """Write the bundle.md summary describing the failure and its artifacts."""
    name = testcase["name"] or "unknown_testcase"
    status = testcase["status"]
    lines: list[str] = [
        f"# Failure bundle: {_escape_inline(name)}",
        "",
        f"- **Status:** {status}",
        f"- **Feature:** {_escape_inline(testcase['classname'] or 'unknown')}",
        f"- **Duration:** {testcase['time']:.2f}s",
        "",
        "## Failure message",
        "",
    ]
    if testcase["failure_message"]:
        fence = _code_fence(testcase["failure_message"])
        lines += [fence, testcase["failure_message"], fence, ""]
    else:
        lines += ["(no failure message recorded)", ""]
    if testcase["system_out"]:
        fence = _code_fence(testcase["system_out"])
        lines += ["## System output", "", fence, testcase["system_out"], fence, ""]

    lines += ["## Properties", "", "| Name | Value |", "| --- | --- |"]
    if testcase["properties"]:
        for prop_name, prop_value in testcase["properties"].items():
            lines.append(f"| {_truncate(prop_name)} | {_truncate(prop_value)} |")
    else:
        lines.append("| (none recorded) | |")
    lines += ["", "## Classification", "", UNCLASSIFIED_TEXT, ""]

    artifacts = _list_bundled_files(bundle_dir)
    lines += ["## Artifacts", ""]
    if artifacts:
        lines += [f"- [{path}]({path})" for path in artifacts]
    else:
        lines.append("(no artifacts could be collected for this failure)")
    lines += [
        "",
        "## Suggested next steps",
        "",
        "- Inspect the most recent screenshots around the moment of failure.",
        "- Check `network.json` for failing HTTP calls (4xx/5xx responses or timeouts).",
        "- Review `planner_thoughts.json` for element-identification issues (selectors that no longer match).",
        "- Re-run the scenario once to check whether the failure is flaky.",
        "",
    ]

    bundle_md = os.path.join(bundle_dir, "bundle.md")
    with open(bundle_md, "w", encoding="utf-8") as md_file:
        md_file.write("\n".join(lines))


async def create_failure_bundles(junit_xml_path: str, output_dir: str | None = None, max_screenshots: int = 3) -> list[str]:
    """Create an evidence bundle for every failed/errored testcase in a merged JUnit XML.

    Args:
        junit_xml_path (str): Path to the merged JUnit XML file.
        output_dir (str | None): Directory that will contain the per-testcase
            bundle folders. Defaults to ``<junit xml dir>/bundles/``.
        max_screenshots (int): Maximum number of most-recent screenshots to bundle.

    Returns:
        list[str]: Paths of the created bundle directories.
    """
    junit_xml_path = os.path.abspath(junit_xml_path)
    base_dir = os.path.dirname(junit_xml_path)
    bundles_root = output_dir or os.path.join(base_dir, "bundles")
    bundles: list[str] = []
    # Shared across all per-testcase evidence lookups so a proofs tree shared by
    # many failing cases is walked once instead of once per case.
    tree_cache: dict = {}

    failing = [case for case in parse_junit_testcases(junit_xml_path) if case["status"] in ("failed", "error")]
    dir_names = resolve_bundle_dir_names([case["name"] or "unknown_testcase" for case in failing])
    written: set[str] = set()

    for testcase in failing:
        testcase_name = testcase["name"] or "unknown_testcase"
        bundle_dir = os.path.join(bundles_root, dir_names[testcase_name])
        if bundle_dir in written:
            # Duplicate testcase name: keep one bundle dir per testcase name.
            continue
        try:
            os.makedirs(bundle_dir, exist_ok=True)
        except OSError as exc:
            logger.error("Could not create bundle directory %s: %s", bundle_dir, exc)
            continue

        _bundle_evidence(testcase["properties"], bundle_dir, max_screenshots, tree_cache)
        _write_bundle_md(bundle_dir, testcase)
        written.add(bundle_dir)
        bundles.append(bundle_dir)
        logger.info("Created failure bundle for testcase %s at %s", testcase_name, bundle_dir)

    return bundles


async def create_run_bundles(project_source_root: str = "./opt") -> list[str]:
    """Create failure bundles for the newest run output under ``<root>/output``.

    Finds the newest ``output/*/`` directory that contains a ``*_result.xml``
    merged JUnit file and bundles the failed testcases from it.

    Args:
        project_source_root (str): Project source root; defaults to "./opt".

    Returns:
        list[str]: Paths of the created bundle directories (empty if no run output found).
    """
    output_root = os.path.join(project_source_root, "output")
    if not os.path.isdir(output_root):
        logger.warning("No output directory found under %s; skipping failure bundles.", os.path.abspath(project_source_root))
        return []

    # Newest run first; the path breaks mtime ties deterministically so
    # run_YYYYMMDD_HHMMSS names resolve chronologically.
    run_dirs = sorted(
        (entry for entry in (os.path.join(output_root, name) for name in os.listdir(output_root)) if os.path.isdir(entry)),
        key=lambda path: (os.path.getmtime(path), path),
        reverse=True,
    )
    for run_dir in run_dirs:
        junit_files = sorted(glob.glob(os.path.join(run_dir, "*_result.xml")), key=lambda path: (os.path.getmtime(path), path), reverse=True)
        if not junit_files:
            continue
        logger.info("Creating failure bundles for run output: %s", run_dir)
        for junit_file in junit_files:
            try:
                return await create_failure_bundles(junit_file)
            except Exception as exc:
                # A malformed/corrupt *_result.xml must not abort bundle creation
                # while other readable results exist in the same run dir.
                logger.warning("Skipping unreadable junit result %s: %s", junit_file, exc)
        logger.warning("No readable *_result.xml files in %s; trying older runs.", run_dir)

    logger.warning("No readable *_result.xml files found under %s; skipping failure bundles.", output_root)
    return []
