"""Dashboard web app (starlette).

A small self-contained web UI over the run-history database
(``reporting.run_db.RunHistory``): run list, per-run/per-case drill-down with
evidence (video, screenshots, network logs, planner thoughts), on-the-fly
failure classification and a pass-rate trend chart.

All pages are pure-string HTML with inline CSS — no template engine and no
external assets. Serve it locally with the ``hercules-dashboard`` console
script (uvicorn) or embed it via ``create_app``.
"""

import argparse
import glob
import html
import json
import math
import mimetypes
import os
import re
import secrets
import urllib.parse
from urllib.parse import urlparse

from sqlalchemy.exc import OperationalError
from starlette.applications import Starlette
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.routing import Route
from testzeus_hercules.healing.failure_classifier import classify_case
from testzeus_hercules.reporting.bundle import parse_junit_testcases
from testzeus_hercules.utils.http_security import (
    MIN_REMOTE_TOKEN_LENGTH,
    BearerTokenMiddleware,
    HostAllowlistMiddleware,
    InsecureBindError,
    build_host_allowlist,
    enforce_bind_policy,
    normalize_token,
    parse_csv,
)
from testzeus_hercules.utils.path_guard import is_sensitive_name, is_within
from testzeus_hercules.reporting.run_db import (
    RunHistory,
    get_default_history,
    record_run_summary,
)

FALLBACK_PROJECT_SOURCE_ROOT = "./opt"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001

RUNS_PAGE_LIMIT = 50
TREND_RUN_LIMIT = 30
FAILURE_MESSAGE_TRUNC = 120
NETWORK_URL_TRUNC = 80
NETWORK_ENTRY_LIMIT = 200
PLANNER_THOUGHTS_TRUNC = 4000

# Evidence property values may point at a directory; try these children first.
_VIDEO_FILENAMES = ("recording.webm", "video.webm")
_NETWORK_FILENAMES = ("network_logs.json", "network.json")
_PLANNER_FILENAMES = ("planner_thoughts.json", "agent_inner_thoughts.json")

_MEDIA_TYPES = {
    ".html": "text/html",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".md": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".zip": "application/zip",
    ".xml": "application/xml",
    ".txt": "text/plain",
}

# Media types served inline (rendered by the browser); everything else —
# notably text/html, image/svg+xml and unknown extensions — is forced to
# download so hostile files cannot execute in the dashboard's origin.
_INLINE_MEDIA_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "video/webm",
    "video/mp4",
    "application/pdf",
}

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #0b1220; color: #d7e1ef; font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
a { color: #6cb2ff; text-decoration: none; }
a:hover { text-decoration: underline; }
nav { display: flex; gap: 16px; align-items: center; padding: 10px 20px; background: #101a2e; border-bottom: 1px solid #1e2c47; }
nav .brand { font-weight: 700; color: #eaf1fb; margin-right: 12px; }
main { padding: 20px; max-width: 1200px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 12px; }
h2 { font-size: 16px; margin: 24px 0 8px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #1c2a44; vertical-align: top; }
th { color: #8fa3c0; font-weight: 600; white-space: nowrap; }
pre { background: #0a0f1c; border: 1px solid #1c2a44; border-radius: 6px; padding: 10px 12px; overflow: auto; max-width: 100%; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
code { background: #0a0f1c; border-radius: 4px; padding: 1px 5px; font: 12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.badge-passed { background: rgba(47, 191, 113, 0.15); color: #2fbf71; }
.badge-failed { background: rgba(229, 83, 75, 0.15); color: #e5534b; }
.badge-error { background: rgba(240, 150, 50, 0.18); color: #f09632; }
.badge-running { background: rgba(76, 141, 255, 0.15); color: #4c8dff; }
.badge-other { background: rgba(140, 160, 190, 0.15); color: #8ca0be; }
.card { background: #101a2e; border: 1px solid #1c2a44; border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
.card h2 { margin-top: 0; }
.meta { color: #8fa3c0; }
.hint { color: #8fa3c0; background: #101a2e; border: 1px dashed #2a3c5f; border-radius: 8px; padding: 14px 16px; }
button { background: #1d4ed8; color: #ffffff; border: 0; border-radius: 6px; padding: 7px 14px; font: inherit; cursor: pointer; }
button:hover { background: #2563eb; }
.gallery { display: flex; flex-wrap: wrap; gap: 10px; }
.gallery img { border: 1px solid #1c2a44; border-radius: 4px; }
.muted { color: #8fa3c0; }
.trunc { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
svg.chart { background: #101a2e; border: 1px solid #1c2a44; border-radius: 8px; max-width: 100%; }
"""

_NAV = "<nav><span class='brand'>Hercules Dashboard</span>" "<a href='/'>Runs</a><a href='/trend'>Trend</a><a href='/live'>Live</a></nav>"

_STATUS_BADGE_CLASSES = {
    "passed": "badge-passed",
    "failed": "badge-failed",
    "error": "badge-error",
    "running": "badge-running",
}


# ---------------------------------------------------------------------------
# Small rendering helpers
# ---------------------------------------------------------------------------


def _esc(value) -> str:
    """HTML-escape any value for safe interpolation into a page."""
    return html.escape(str(value), quote=True)


def _badge(status) -> str:
    """Colored status badge: passed green, failed red, error orange, running blue."""
    css_class = _STATUS_BADGE_CLASSES.get(str(status), "badge-other")
    return f"<span class='badge {css_class}'>{_esc(status)}</span>"


def _fmt_duration(seconds) -> str:
    """Human-readable duration, or an em dash when unknown."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        return "—"
    if value < 60:
        return f"{value:.2f}s"
    return f"{int(value // 60)}m {value % 60:.0f}s"


def _fmt_cost(cost) -> str:
    """Human-readable cost, or an em dash when unknown."""
    try:
        return f"${float(cost):.4f}"
    except (TypeError, ValueError):
        return "—"


def _truncate(value, limit: int) -> str:
    """Single-line truncated text (newlines collapsed) with an ellipsis when cut."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _artifact_href(path: str) -> str:
    """Safely-encoded /artifact URL for an absolute evidence path."""
    return f"/artifact?{urllib.parse.urlencode({'path': path})}"


def _case_href(run_id: str, case_key: str) -> str:
    """Safely-encoded case-detail URL (case keys contain spaces/colons)."""
    return f"/case/{urllib.parse.quote(run_id, safe='')}/{urllib.parse.quote(case_key, safe='')}"


def _content_security_policy(script_nonce: str | None = None) -> str:
    """Restrictive CSP for dashboard pages: same-origin only, no framing, inline scripts only via a nonce.

    Pages use one inline ``<style>`` block plus a few ``style=`` attributes (hence style 'unsafe-inline')
    and a single inline script on /live (allowed through ``script_nonce``); everything else is blocked.
    """
    directives = [
        "default-src 'none'",
        f"script-src 'nonce-{script_nonce}'" if script_nonce else "script-src 'none'",
        "style-src 'unsafe-inline'",
        "img-src 'self' data:",
        "media-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(directives)


def _page(title: str, body: str, script_nonce: str | None = None) -> HTMLResponse:
    """Wrap a body fragment in the shared page shell (inline CSS, no assets) with anti-framing/CSP headers."""
    document = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)} · Hercules Dashboard</title>"
        f"<style>{_CSS}</style></head><body>{_NAV}<main>{body}</main></body></html>"
    )
    headers = {
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": _content_security_policy(script_nonce),
        "X-Content-Type-Options": "nosniff",
    }
    return HTMLResponse(document, headers=headers)


def _resolve_artifact_file(path_value: str | None, child_names: tuple[str, ...] = (), root: str | None = None) -> str | None:
    """Resolve an artifact DB path to an existing file, trying child names inside directories.

    Artifact paths come from JUnit XML properties (attacker/tool-controlled
    data), so when ``root`` is given a path that does not resolve (symlinks
    followed) inside it is ignored, never opened, and neither is anything the
    /artifact route would refuse to serve (``_is_sensitive_artifact``: env/key
    files, databases, session stores, dotfiles and dot-directories): the same
    containment and sensitive-file filter that route enforces.
    """
    if not path_value:
        return None
    if root is not None and not is_within(path_value, root):
        return None
    if os.path.isfile(path_value):
        return path_value if _artifact_readable(path_value, root) else None
    if os.path.isdir(path_value):
        for name in child_names:
            candidate = os.path.join(path_value, name)
            if os.path.isfile(candidate) and (root is None or is_within(candidate, root)) and _artifact_readable(candidate, root):
                return candidate
    return None


def _artifact_readable(path_value: str, root: str | None) -> bool:
    """True when ``path_value`` may be read for rendering (always, without a root; else not a sensitive artifact)."""
    if root is None:
        return True
    try:
        real_root = os.path.realpath(root)
        return not _is_sensitive_artifact(path_value, os.path.realpath(path_value), real_root)
    except (ValueError, OSError):
        return False


def _parse_network_entries(path_value: str | None, limit: int = NETWORK_ENTRY_LIMIT) -> list[dict]:
    """Parse the first ``limit`` entries of a JSON-lines network log; never raises."""
    if not path_value or not os.path.isfile(path_value):
        return []
    entries: list[dict] = []
    try:
        with open(path_value, "r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
                if len(entries) >= limit:
                    break
    except OSError:
        return []
    return entries


def _network_entry_field(entry: dict, *names: str):
    """First present value among candidate keys (network logs vary by producer)."""
    for name in names:
        value = entry.get(name)
        if value not in (None, ""):
            return value
    return None


# ---------------------------------------------------------------------------
# Page fragments
# ---------------------------------------------------------------------------


def _runs_table(runs: list[dict], history: RunHistory) -> str:
    """Runs list table; cost comes from list_runs' "total_cost" when the key is
    present (newer run_db), falling back to a per-run get_run lookup only for
    older run_db versions that do not carry it (keeps the page O(1) queries)."""
    header = "<tr><th>Run</th><th>Started (UTC)</th><th>Status</th><th>Passed</th><th>Failed</th><th>Errors</th><th>Duration</th><th>Cost</th></tr>"
    rows: list[str] = []
    for entry in runs:
        if "total_cost" in entry:
            cost = entry["total_cost"]
        else:
            try:
                cost = history.get_run(entry["run_id"]).get("total_cost")
            except (ValueError, KeyError):
                cost = None
        cells = (
            f"<td><a href='/run/{urllib.parse.quote(entry['run_id'], safe='')}'>{_esc(entry['run_id'])}</a></td>"
            f"<td class='meta'>{_esc(entry['started_at'] or '—')}</td>"
            f"<td>{_badge(entry['status'])}</td>"
            f"<td>{entry['case_passed']}</td>"
            f"<td>{entry['case_failed']}</td>"
            f"<td>{entry['case_errors']}</td>"
            f"<td>{_fmt_duration(entry['duration_s'])}</td>"
            f"<td>{_fmt_cost(cost)}</td>"
        )
        rows.append(f"<tr>{cells}</tr>")
    return f"<table>{header}{''.join(rows)}</table>"


def _trend_svg(runs: list[dict]) -> str:
    """Inline SVG bar chart: one stacked green/red bar per run, oldest on the left."""
    if not runs:
        return "<p class='muted'>No runs recorded yet.</p>"
    ordered = list(reversed(runs))  # oldest first so time flows left to right
    bar_width, gap, chart_height, top = 34, 10, 120, 8
    label_every = max(1, math.ceil(len(ordered) / 12))
    width = len(ordered) * (bar_width + gap) + gap
    height = chart_height + 2 * top + 26
    parts = [
        f"<svg class='chart' width='{width}' height='{height}' xmlns='http://www.w3.org/2000/svg' role='img' aria-label='Pass rate per run'>",
        f"<rect x='0' y='0' width='{width}' height='{height}' fill='#101a2e' rx='8'/>",
    ]
    for index, run in enumerate(ordered):
        passed = int(run.get("passed") or 0)
        failed = int(run.get("failed") or 0) + int(run.get("errors") or 0)
        total = passed + failed
        x = gap + index * (bar_width + gap)
        rate = passed / total if total else None
        if total == 0:
            parts.append(f"<rect x='{x}' y='{top}' width='{bar_width}' height='{chart_height}' fill='#33415c' rx='3'/>")
        else:
            green_height = chart_height * passed / total
            red_height = chart_height - green_height
            if red_height > 0:
                parts.append(f"<rect x='{x}' y='{top}' width='{bar_width}' height='{red_height:.1f}' fill='#e5534b' rx='3'/>")
            if green_height > 0:
                parts.append(f"<rect x='{x}' y='{top + red_height:.1f}' width='{bar_width}' height='{green_height:.1f}' fill='#2fbf71' rx='3'/>")
        rate_text = f"{round(rate * 100)}% passed" if rate is not None else "no cases"
        parts.append(f"<rect x='{x}' y='{top}' width='{bar_width}' height='{chart_height}' fill='transparent'><title>{_esc(run['run_id'])}: {rate_text}</title></rect>")
        if index % label_every == 0:
            center = x + bar_width / 2
            label = _truncate(run["run_id"], 12)
            parts.append(f"<text x='{center:.1f}' y='{top + chart_height + 16}' font-size='9' fill='#8fa3c0' text-anchor='middle' font-family='system-ui, sans-serif'>{_esc(label)}</text>")
            if rate is not None:
                parts.append(f"<text x='{center:.1f}' y='{top + chart_height + 26}' font-size='9' fill='#d7e1ef' text-anchor='middle' font-family='system-ui, sans-serif'>{round(rate * 100)}%</text>")
    parts.append("</svg>")
    return "".join(parts)


def _recorded_classification(events: list[dict]) -> tuple[str, float | None] | None:
    """Category/confidence recorded by a ``failure_classified`` healing event, if any.

    Events carry their classification in ``detail`` as ``"<category> (confidence=0.83)"``.
    """
    for event in events:
        if event.get("kind") != "failure_classified":
            continue
        detail = str(event.get("detail") or "")
        category = detail.split(" (confidence", 1)[0].strip()
        if not category:
            continue
        confidence_match = re.search(r"confidence=([0-9.]+)", detail)
        confidence = None
        if confidence_match:
            try:
                confidence = float(confidence_match.group(1))
            except ValueError:
                confidence = None
        return category, confidence
    return None


def _classification_card(case: dict, recorded: tuple[str, float | None] | None = None) -> str:
    """Failure classification card; a classification recorded at healing time wins.

    Recomputing heuristics on the fly can contradict the classification that was
    recorded when the failure actually happened, so the recorded one is shown
    verbatim when present.
    """
    if recorded is not None:
        category, confidence = recorded
        confidence_text = f"{round(confidence * 100)}%" if confidence is not None else "—"
        return (
            "<div class='card'><h2>Failure classification</h2>"
            f"<p>{_badge(category)} <strong>Confidence:</strong> {confidence_text}</p>"
            "<p class='meta'>Classification recorded at healing time.</p></div>"
        )
    classification = classify_case(case)
    signals = "".join(f"<li><code>{_esc(signal)}</code></li>" for signal in classification.signals) or "<li class='muted'>none</li>"
    confidence_pct = round(classification.confidence * 100)
    return (
        "<div class='card'><h2>Failure classification</h2>"
        f"<p>{_badge(classification.category)} <strong>Confidence:</strong> {confidence_pct}%</p>"
        f"<p>{_esc(classification.rationale)}</p>"
        f"<p><strong>Signals:</strong></p><ul>{signals}</ul></div>"
    )


def _healing_section(events: list[dict]) -> str:
    """Healing events recorded in run_db for this case (selector hits, proposals, classification)."""
    header = "<tr><th>When (UTC)</th><th>Kind</th><th>Element</th><th>Detail</th><th>Status</th></tr>"
    rows = "".join(
        "<tr>"
        f"<td>{_esc(event.get('occurred_at') or '—')}</td>"
        f"<td><code>{_esc(event.get('kind') or '')}</code></td>"
        f"<td>{_esc(event.get('canonical_name') or '—')}</td>"
        f"<td>{_esc(event.get('detail') or '—')}</td>"
        f"<td>{_esc(event.get('status') or '—')}</td>"
        "</tr>"
        for event in events
    )
    return f"<h2>Healing</h2><table>{header}{rows}</table>"


def _network_table(case: dict, root: str | None = None) -> str:
    """Network log viewer: JSON-lines parsed server-side, first 200 entries."""
    network_path = next(
        (_resolve_artifact_file(artifact["path"], _NETWORK_FILENAMES, root) for artifact in case["artifacts"] if artifact["kind"] == "network"),
        None,
    )
    entries = _parse_network_entries(network_path)
    if network_path is None:
        return "<p class='muted'>(no network log recorded for this case)</p>"
    if not entries:
        return "<p class='muted'>(network log could not be parsed)</p>"
    header = "<tr><th>Method</th><th>URL</th><th>Status</th><th>Duration (ms)</th></tr>"
    rows: list[str] = []
    for entry in entries:
        method = _network_entry_field(entry, "method")
        url = _network_entry_field(entry, "url", "uri", "path")
        status = _network_entry_field(entry, "status", "response_status")
        duration = _network_entry_field(entry, "duration", "duration_ms", "time_ms")
        rows.append(
            "<tr>"
            f"<td>{_esc(method or '—')}</td>"
            f"<td class='trunc' title='{_esc(url or '')}'>{_esc(_truncate(url, NETWORK_URL_TRUNC) or '—')}</td>"
            f"<td>{_esc(status if status is not None else '—')}</td>"
            f"<td>{_esc(duration if duration is not None else '—')}</td>"
            "</tr>"
        )
    shown = f"<p class='meta'>Showing first {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.</p>"
    return shown + f"<table>{header}{''.join(rows)}</table>"


def _video_player(case: dict, root: str | None = None) -> str:
    """Video player for the case recording, streamed through /artifact."""
    video_path = next(
        (_resolve_artifact_file(artifact["path"], _VIDEO_FILENAMES, root) for artifact in case["artifacts"] if artifact["kind"] == "video"),
        None,
    )
    if video_path is None:
        return "<p class='muted'>(no video recording for this case)</p>"
    return f"<video controls width='100%' src='{_artifact_href(video_path)}'>Video playback not supported by this browser.</video>"


def _screenshot_paths(case: dict, root: str | None = None) -> list[str]:
    """All screenshot files for a case (artifact values may be files or directories)."""
    paths: list[str] = []
    for artifact in case["artifacts"]:
        if artifact["kind"] != "screenshot":
            continue
        value = artifact["path"]
        if root is not None and value and not is_within(value, root):
            continue
        if value and os.path.isdir(value):
            paths.extend(path for path in sorted(glob.glob(os.path.join(value, "*.png"))) if root is None or is_within(path, root))
        else:
            resolved = _resolve_artifact_file(value, (), root)
            if resolved:
                paths.append(resolved)
    return paths


def _planner_text(case: dict, root: str | None = None) -> str | None:
    """Planner-thoughts content (truncated); prefers the planner-named log artifact."""
    planner_path = None
    for artifact in case["artifacts"]:
        if artifact["kind"] != "log":
            continue
        resolved = _resolve_artifact_file(artifact["path"], _PLANNER_FILENAMES, root)
        if resolved and (planner_path is None or "planner" in os.path.basename(resolved).lower()):
            planner_path = resolved
        if planner_path and "planner" in os.path.basename(planner_path).lower():
            break
    if planner_path is None:
        return None
    try:
        with open(planner_path, "r", encoding="utf-8", errors="replace") as thoughts_file:
            return thoughts_file.read(PLANNER_THOUGHTS_TRUNC)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Route endpoints
# ---------------------------------------------------------------------------


def _history(request) -> RunHistory:
    return request.app.state.history


def _project_root(request) -> str:
    return request.app.state.project_root


def runs_page(request):
    """GET / — runs list with import button; hints when the DB is empty."""
    history = _history(request)
    runs = history.list_runs(limit=RUNS_PAGE_LIMIT)
    import_form = "<form method='post' action='/import' style='display:inline'><button type='submit'>Import from output folders</button></form>"
    if not runs:
        body = (
            "<h1>Runs</h1>"
            f"<p>{import_form}</p>"
            "<div class='hint'>No runs recorded yet. Execute a Hercules run, then click "
            "<strong>Import from output folders</strong> to scan <code>&lt;project root&gt;/output/*/&lt;run&gt;_result.xml</code> "
            "into the history database.</div>"
        )
        return _page("Runs", body)
    body = f"<h1>Runs</h1><p>{import_form}</p>{_runs_table(runs, history)}"
    return _page("Runs", body)


def _summary_from_junit(junit_path: str) -> dict:
    """Build a record_run_summary dict from one merged JUnit XML.

    Raises:
        ValueError: The XML could not be parsed or contains no testcases; the
            caller must never record a run row from such a file (an empty
            all-passed run would poison history and block re-import).
    """
    try:
        cases = parse_junit_testcases(junit_path)
    except Exception as exc:
        raise ValueError(f"could not parse junit xml {junit_path}: {exc}") from exc
    if not cases:
        raise ValueError(f"junit xml {junit_path} contains 0 testcases; not recording a run")
    summary: dict = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "junit_xml": junit_path}
    durations: list[float] = []
    for case in cases:
        summary["total"] += 1
        status = case.get("status") or "passed"
        if status in ("passed", "failed", "error"):
            summary[status] += 1
        try:
            durations.append(float(case.get("time") or 0.0))
        except (TypeError, ValueError):
            continue
    summary["duration_s"] = round(sum(durations), 3) if durations else None
    return summary


# Error response bodies are plain text shown in a browser: absolute POSIX paths
# inside them (junit XML locations, sqlite file names) leak the server layout.
_ABSOLUTE_PATH_RE = re.compile(r"(?:/[\w.@+-]+)+/([\w.@+-]+)")


def _sanitize_import_error(exc: BaseException) -> str:
    """Single-line exception text safe for import response bodies.

    Raw exception strings can embed the server's absolute paths and (for
    SQLAlchemy errors) multi-line SQL with statements. Keep only the first
    line, with absolute paths collapsed to their basename.
    """
    text = str(exc).strip()
    first_line = text.splitlines()[0] if text else type(exc).__name__
    return _ABSOLUTE_PATH_RE.sub(r"\1", first_line)


def import_endpoint(request):
    """POST /import — scan <project_root>/output/*/ for *_result.xml, record new runs, redirect to /.

    Unparseable or testcase-free XML files are rejected with a 400 listing the
    errors (no run row is created for them); a locked database yields a 503
    retry hint.

    Cross-site form posts are rejected (Origin/Sec-Fetch-Site check) so a
    drive-by web page cannot mutate the operator's history DB.
    """
    fetch_site = request.headers.get("sec-fetch-site", "")
    host = request.headers.get("host", "")
    if fetch_site and fetch_site not in ("same-origin", "same-site", "none"):
        return PlainTextResponse("cross-site import rejected", status_code=403)
    if "origin" in request.headers:
        # A present Origin must name this host: "null" (sandboxed/redirected contexts), empty or
        # opaque origins have no netloc and are rejected rather than waved through.
        origin_host = urlparse(request.headers["origin"]).netloc
        if not origin_host or origin_host != host:
            return PlainTextResponse("cross-site import rejected", status_code=403)
    elif not fetch_site and not getattr(request.app.state, "auth_token", None):
        # Neither Origin nor Sec-Fetch-Site: not a browser form post we can vouch for. With a token
        # configured the auth middleware already authenticated the caller; without one, refuse.
        return PlainTextResponse("import rejected: send an Origin/Sec-Fetch-Site header or configure DASHBOARD_AUTH_TOKEN", status_code=403)
    history = _history(request)
    root = _project_root(request)
    existing = {entry["run_id"]: entry for entry in history.list_runs(limit=10000)}
    errors: list[str] = []
    for junit_path in sorted(glob.glob(os.path.join(root, "output", "*", "*_result.xml"))):
        run_dir = os.path.dirname(os.path.abspath(junit_path))
        run_id = os.path.basename(run_dir)
        if not run_id:
            continue
        prior = existing.get(run_id)
        if prior is not None and (prior.get("total") or prior.get("case_count")):
            continue  # already imported with content; import never overwrites real runs
        try:
            summary = _summary_from_junit(junit_path)
        except ValueError as exc:
            errors.append(_sanitize_import_error(exc))
            continue
        if prior is not None:
            # Drop an earlier poisoned empty run first: record_run_summary renames
            # on duplicate run_ids, so the row must be gone before re-recording.
            # RunHistory.delete_run also clears run-level artifacts and healing
            # events (test_case_id IS NULL) that an ORM session.delete would orphan.
            history.delete_run(run_id)
        try:
            record_run_summary(summary, run_id=run_id, db=history)
        except OperationalError as exc:
            return PlainTextResponse(f"History database is busy (locked); try the import again. ({_sanitize_import_error(exc)})", status_code=503)
    if errors:
        return PlainTextResponse("\n".join(errors), status_code=400)
    return RedirectResponse("/", status_code=303)


def run_page(request):
    """GET /run/{run_id} — run meta plus its cases table."""
    run_id = request.path_params["run_id"]
    history = _history(request)
    try:
        run = history.get_run(run_id)
    except ValueError:
        return PlainTextResponse(f"Run {run_id!r} not found", status_code=404)

    meta = (
        "<div class='card'>"
        f"<p><strong>Run:</strong> <code>{_esc(run['run_id'])}</code> {_badge(run['status'])}</p>"
        f"<p class='meta'>Started: {_esc(run['started_at'] or '—')} · Finished: {_esc(run['finished_at'] or '—')} · "
        f"Duration: {_fmt_duration(run['duration_s'])} · Cost: {_fmt_cost(run['total_cost'])}</p>"
        f"<p class='meta'>Total: {run['total']} · Passed: {run['passed']} · Failed: {run['failed']} · Errors: {run['errors']} · "
        f"<a href='/trend'>Trend</a></p>"
        "</div>"
    )
    header = "<tr><th>Status</th><th>Case</th><th>Feature</th><th>Duration</th><th>Failure message</th><th>Bundle</th></tr>"
    rows: list[str] = []
    for case in run["cases"]:
        case_href = _case_href(run_id, case["case_key"])
        bundle_cell = f"<a href='{_case_href(run_id, case['case_key'])}#artifacts'>bundle</a>" if case.get("bundle_dir") else "<span class='muted'>—</span>"
        rows.append(
            "<tr>"
            f"<td>{_badge(case['status'])}</td>"
            f"<td><a href='{case_href}'>{_esc(case['case_key'])}</a></td>"
            f"<td>{_esc(case['feature'] or '—')}</td>"
            f"<td>{_fmt_duration(case['duration_s'])}</td>"
            f"<td class='trunc' title='{_esc(case['failure_message'] or '')}'>{_esc(_truncate(case['failure_message'], FAILURE_MESSAGE_TRUNC) or '—')}</td>"
            f"<td>{bundle_cell}</td>"
            "</tr>"
        )
    cases_table = f"<table>{header}{''.join(rows)}</table>" if rows else "<p class='muted'>(no testcases recorded for this run)</p>"
    body = f"<h1>Run {_esc(run_id)}</h1>{meta}<h2>Cases</h2>{cases_table}"
    return _page(f"Run {run_id}", body)


def case_page(request):
    """GET /case/{run_id}/{case_key} — full case detail with evidence and classification."""
    run_id = request.path_params["run_id"]
    case_key = request.path_params["case_key"]
    history = _history(request)
    try:
        case = history.get_case_detail(run_id, case_key)
    except ValueError:
        return PlainTextResponse(f"Case {case_key!r} not found in run {run_id!r}", status_code=404)

    meta = (
        "<div class='card'>"
        f"<p><strong>Case:</strong> <code>{_esc(case['case_key'])}</code> {_badge(case['status'])}</p>"
        f"<p class='meta'>Run: <a href='/run/{urllib.parse.quote(run_id, safe='')}'>{_esc(run_id)}</a> · "
        f"Feature: {_esc(case['feature'] or '—')} · Duration: {_fmt_duration(case['duration_s'])} · Cost: {_fmt_cost(case['cost'])}</p>"
        "</div>"
    )

    healing_events = case.get("healing_events") or []
    sections = [meta]
    if case["status"] == "failed":
        sections.append(_classification_card(case, recorded=_recorded_classification(healing_events)))
    if healing_events:
        sections.append(_healing_section(healing_events))
    if case["failure_message"]:
        sections.append("<h2>Failure message</h2><pre>" + _esc(case["failure_message"]) + "</pre>")
    if case["final_response"]:
        sections.append("<h2>Final response</h2><pre>" + _esc(case["final_response"]) + "</pre>")

    if case["steps"]:
        steps_header = "<tr><th>#</th><th>Type</th><th>Description</th><th>Status</th><th>Duration</th><th>Error</th></tr>"
        step_rows = "".join(
            "<tr>"
            f"<td>{step['idx']}</td>"
            f"<td>{_esc(step['step_type'])}</td>"
            f"<td>{_esc(_truncate(step['description'], 120))}</td>"
            f"<td>{_badge(step['status']) if step['status'] else '—'}</td>"
            f"<td>{_fmt_duration(step['duration_s'])}</td>"
            f"<td class='trunc' title='{_esc(step['error'] or '')}'>{_esc(_truncate(step['error'], 120) or '—')}</td>"
            "</tr>"
            for step in case["steps"]
        )
        sections.append("<h2>Steps</h2><table>" + steps_header + step_rows + "</table>")

    sections.append("<h2 id='artifacts'>Artifacts</h2>")
    if case["artifacts"]:
        artifact_items = "".join(
            f"<li><code>{_esc(artifact['kind'])}</code> "
            + (f"<a href='{_artifact_href(artifact['path'])}'>{_esc(artifact['path'])}</a>" if os.path.isfile(artifact["path"]) else f"<span class='muted'>{_esc(artifact['path'])}</span>")
            + "</li>"
            for artifact in case["artifacts"]
        )
        sections.append(f"<ul>{artifact_items}</ul>")
    else:
        sections.append("<p class='muted'>(no artifacts recorded)</p>")

    sections.append("<h2>Video</h2>" + _video_player(case, _project_root(request)))

    screenshots = _screenshot_paths(case, _project_root(request))
    sections.append("<h2>Screenshots</h2>")
    if screenshots:
        gallery = "".join(f"<a href='{_artifact_href(path)}' target='_blank'><img src='{_artifact_href(path)}' width='300' alt='screenshot' loading='lazy'></a>" for path in screenshots)
        sections.append(f"<div class='gallery'>{gallery}</div>")
    else:
        sections.append("<p class='muted'>(no screenshots for this case)</p>")

    sections.append("<h2>Network log</h2>" + _network_table(case, _project_root(request)))

    planner = _planner_text(case, _project_root(request))
    sections.append("<h2>Planner thoughts</h2>")
    sections.append("<pre>" + _esc(planner) + "</pre>" if planner else "<p class='muted'>(no planner thoughts recorded)</p>")

    body = f"<h1>Case detail</h1>{''.join(sections)}"
    return _page(f"{case_key}", body)


def _safe_download_filename(path: str) -> str:
    """Filesystem/HTTP-safe filename for a Content-Disposition attachment."""
    basename = os.path.basename(path)
    cleaned = "".join(char if char.isalnum() or char in "._- " else "_" for char in basename).strip() or "artifact"
    return cleaned.replace('"', "_")


def _is_sensitive_artifact(raw_path: str, candidate: str, root: str) -> bool:
    """True for DBs, session/selector stores, env/key files and dotfiles (also behind symlinks or dot-directories).

    The project root usually holds hercules_history.db, selector_store.json and .hercules/sessions.json next to
    the evidence this route is meant to serve; none of that may be downloadable.
    """
    if is_sensitive_name(raw_path) or is_sensitive_name(candidate):
        return True
    relative = os.path.relpath(candidate, root)
    return any(part.startswith(".") and part not in (".", "..") for part in relative.split(os.sep))


def artifact_endpoint(request):
    """GET /artifact?path= — the only file-serving route; realpath-contained in the project root."""
    raw_path = request.query_params.get("path") or ""
    if not raw_path:
        return PlainTextResponse("Missing ?path= parameter", status_code=400)
    if "\x00" in raw_path:
        return PlainTextResponse("Invalid path", status_code=400)
    root = os.path.realpath(_project_root(request))
    try:
        candidate = os.path.realpath(raw_path)
    except (ValueError, OSError):
        return PlainTextResponse("Invalid path", status_code=400)
    if candidate != root and not candidate.startswith(root + os.sep):
        return PlainTextResponse("Path is outside the project root", status_code=403)
    if _is_sensitive_artifact(raw_path, candidate, root):
        return PlainTextResponse("Path is not a servable artifact", status_code=403)
    try:
        is_file = os.path.isfile(candidate)
    except (ValueError, OSError):  # e.g. ENAMETOOLONG
        is_file = False
    if not is_file:
        return PlainTextResponse("File not found", status_code=404)
    extension = os.path.splitext(candidate)[1].lower()
    media_type = _MEDIA_TYPES.get(extension) or mimetypes.guess_type(candidate)[0] or "application/octet-stream"
    # text/html, image/svg+xml and unknown types must never render inline
    # (stored XSS); nosniff guards against content-type sniffing on top.
    headers = {"X-Content-Type-Options": "nosniff"}
    if media_type not in _INLINE_MEDIA_TYPES:
        headers["Content-Disposition"] = f'attachment; filename="{_safe_download_filename(candidate)}"'
    return FileResponse(candidate, media_type=media_type, headers=headers)


def trend_page(request):
    """GET /trend — pass-rate history chart plus flakiest cases."""
    trend = _history(request).get_trend(limit=TREND_RUN_LIMIT)
    aggregate = trend["aggregate"]
    avg_rate = aggregate.get("avg_pass_rate")
    avg_text = f"{round(avg_rate * 100)}%" if avg_rate is not None else "—"

    body = f"<h1>Trend</h1><p class='meta'>Average pass rate over the last {TREND_RUN_LIMIT} runs: <strong>{avg_text}</strong></p>"
    body += "<h2>Pass rate per run</h2>" + _trend_svg(trend["runs"])

    flaky = aggregate.get("flakiest_cases") or []
    body += "<h2>Flakiest cases</h2>"
    if flaky:
        header = "<tr><th>Case</th><th>Runs</th><th>Passed</th><th>Failed</th><th>Errors</th></tr>"
        rows = "".join(f"<tr><td>{_esc(entry['case_key'])}</td><td>{entry['runs']}</td><td>{entry['passed']}</td><td>{entry['failed']}</td><td>{entry['errors']}</td></tr>" for entry in flaky)
        body += f"<table>{header}{rows}</table>"
    else:
        body += "<p class='muted'>(no flaky cases detected)</p>"
    return _page("Trend", body)


def api_runs(request):
    """GET /api/runs?limit=N — JSON runs list (for live polling)."""
    try:
        limit = int(request.query_params.get("limit", RUNS_PAGE_LIMIT))
    except (TypeError, ValueError, OverflowError):
        limit = RUNS_PAGE_LIMIT
    # Clamp both ends: huge ints overflow SQLite INTEGER binding.
    return JSONResponse(_history(request).list_runs(limit=max(1, min(limit, 1000))))


def api_runs_latest(request):
    """GET /api/runs/latest — JSON newest run summary, or {} when the DB is empty."""
    runs = _history(request).list_runs(limit=1)
    return JSONResponse(runs[0] if runs else {})


def live_page(request):
    """GET /live — minimal auto-refreshing status page (vanilla JS, 2s polling)."""
    nonce = secrets.token_urlsafe(16)
    body = (
        "<h1>Live status</h1>"
        "<div class='card'><div id='status' class='muted'>Loading…</div></div>"
        f"<script nonce='{nonce}'>"
        "async function refreshStatus() {"
        "  try {"
        "    const res = await fetch('/api/runs/latest');"
        "    const run = await res.json();"
        "    const el = document.getElementById('status');"
        "    if (!run || !run.run_id) { el.textContent = 'No runs recorded yet.'; return; }"
        "    el.className = '';"
        "    el.textContent = run.run_id + ' — ' + run.status + ' — ' + run.case_passed + ' passed / '"
        "      + run.case_failed + ' failed / ' + run.case_errors + ' errors';"
        "  } catch (err) {"
        "    const el = document.getElementById('status');"
        "    el.className = 'muted';"
        "    el.textContent = 'Fetch failed: ' + err;"
        "  }"
        "}"
        "setInterval(refreshStatus, 2000);"
        "refreshStatus();"
        "</script>"
    )
    return _page("Live", body, script_nonce=nonce)


# ---------------------------------------------------------------------------
# App factory + CLI entry point
# ---------------------------------------------------------------------------


def _default_project_root() -> str:
    """Project root from config (read lazily, like run_db), falling back to ./opt."""
    try:
        from testzeus_hercules.config import (
            get_global_conf,  # lazy: keep heavy config import out of module import
        )

        return get_global_conf().get_project_source_root()
    except Exception as exc:
        from testzeus_hercules.utils.logger import logger

        logger.warning("Could not read PROJECT_SOURCE_ROOT from config (%s); falling back to %s", exc, FALLBACK_PROJECT_SOURCE_ROOT)
        return FALLBACK_PROJECT_SOURCE_ROOT


def create_app(
    db_url: str | None = None,
    project_root: str | None = None,
    auth_token: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> Starlette:
    """Build the dashboard app.

    Args:
        db_url (str | None): Run-history database URL; None uses the default
            location (``<project root>/hercules_history.db``). Pass an explicit
            sqlite URL in tests to share one temp DB with the seeded fixture.
        project_root (str | None): Root containing ``output/`` and evidence
            paths; None resolves from config. Also bounds /artifact access.
        auth_token (str | None): When set, every request must carry it as
            ``Authorization: Bearer <token>`` (or HTTP Basic with the token as
            the password, so a browser can log in). Embedders keep the old
            unauthenticated behaviour by leaving this unset.
        allowed_hosts (set[str] | None): When set, requests whose ``Host`` header
            (port ignored) is not in the set are rejected (DNS-rebinding defence).

    Returns:
        Starlette: Configured app (state carries the RunHistory + root).
    """
    history = RunHistory(db_url) if db_url else get_default_history()
    if project_root is None:
        project_root = _default_project_root()

    app = Starlette(
        routes=[
            Route("/", runs_page, methods=["GET"]),
            Route("/import", import_endpoint, methods=["POST"]),
            Route("/run/{run_id}", run_page, methods=["GET"]),
            # case keys are legal JUnit names and may contain "/" once %2F
            # decodes, so the key must match across slashes (:path converter).
            Route("/case/{run_id}/{case_key:path}", case_page, methods=["GET"]),
            Route("/artifact", artifact_endpoint, methods=["GET"]),
            Route("/trend", trend_page, methods=["GET"]),
            Route("/api/runs", api_runs, methods=["GET"]),
            Route("/api/runs/latest", api_runs_latest, methods=["GET"]),
            Route("/live", live_page, methods=["GET"]),
        ]
    )
    if auth_token:
        app.add_middleware(BearerTokenMiddleware, token=auth_token, allow_basic=True, realm="hercules-dashboard")
    if allowed_hosts:
        # Added last so it is the outermost layer: a bad Host never reaches auth/routes.
        app.add_middleware(HostAllowlistMiddleware, allowed_hosts=allowed_hosts)
    app.state.history = history
    app.state.project_root = project_root
    app.state.auth_token = auth_token
    return app


def normalize_db_url(value: str | None) -> str | None:
    """Accept a full SQLAlchemy URL or a bare filesystem path (mainly for ``--db``).

    Bare paths are made absolute and prefixed with ``sqlite:///`` — the same
    normalization the ``HERCULES_HISTORY_DB`` env var gets — so a crash-prone
    ArgumentError from SQLAlchemy can never happen.
    """
    if not value:
        return None
    if "://" in value:
        return value
    return "sqlite:///" + os.path.abspath(value)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``hercules-dashboard``): serve the dashboard via uvicorn."""
    parser = argparse.ArgumentParser(prog="hercules-dashboard", description="Hercules run-history dashboard.")
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind host (default: {DEFAULT_HOST}). A non-loopback host requires DASHBOARD_AUTH_TOKEN (>= {MIN_REMOTE_TOKEN_LENGTH} chars).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT}).")
    parser.add_argument("--db", default=None, help="Run-history database URL (default: <PROJECT_SOURCE_ROOT>/hercules_history.db (opt/hercules_history.db)).")
    parser.add_argument("--project-root", default=None, help="Project root holding output/ and evidence paths (default: config value).")
    args = parser.parse_args(argv)

    token = normalize_token(os.getenv("DASHBOARD_AUTH_TOKEN"))
    try:
        enforce_bind_policy("the Hercules dashboard", args.host, token, "DASHBOARD_AUTH_TOKEN")
    except InsecureBindError as exc:
        raise SystemExit(str(exc)) from exc
    allowed_hosts = build_host_allowlist(args.host, parse_csv(os.getenv("DASHBOARD_ALLOWED_HOSTS")))

    try:
        import uvicorn
    except ImportError as exc:  # declared in pyproject dependencies; guard kept for odd installs
        raise SystemExit("uvicorn is required to serve the dashboard. Install it with: pip install uvicorn") from exc

    db_url = normalize_db_url(args.db)
    if db_url and db_url.startswith("sqlite:///"):
        parent = os.path.dirname(os.path.abspath(db_url.removeprefix("sqlite:///")))
        if parent:
            os.makedirs(parent, exist_ok=True)

    app = create_app(db_url=db_url, project_root=args.project_root, auth_token=token, allowed_hosts=allowed_hosts)
    print(f"Hercules dashboard listening on http://{args.host}:{args.port}")
    print("Auth: " + ("bearer/basic token required (DASHBOARD_AUTH_TOKEN)" if token else "none (loopback bind only)"))
    print(f"Run history DB: {app.state.history.db_url}")
    print("Press Ctrl+C to stop.")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
