#!/usr/bin/env bash
#
# Post or update a pull-request comment summarizing Hercules test results.
#
# Standalone-testable: the body is built purely from JUNIT_PATH before any GitHub
# API call is made. Required environment:
#   GITHUB_TOKEN        - token used for the API call (required to post)
#   GITHUB_REPOSITORY   - "owner/repo" (required to post)
#   GITHUB_PR_NUMBER    - PR number; defaults to the one from GITHUB_REF (refs/pull/N/merge)
#   JUNIT_PATH          - path to the JUnit XML to summarize (optional; missing file
#                         yields a "no results" comment)
#   RUN_URL             - link to the workflow run shown in the comment (optional)
#   INCLUDE_FAILURE_DETAILS - "true" to include redacted, truncated failure messages in the
#                         comment; any other value (default) lists only names/statuses/durations,
#                         because raw failure text can contain secrets and PR comments are public
#                         to everyone who can read the PR
#   HERCULES_PASSTHROUGH_VARS - space-separated env var NAMES whose values are masked in any text
#   HERCULES_PR_COMMENT_DRY_RUN - "1" prints the comment body and exits without calling the API
#
# The comment is idempotent: it is identified by the marker below and updated
# in place instead of being posted again.

set -euo pipefail

MARKER='<!-- hercules-results -->'
MAX_ROWS=50

# ---------------------------------------------------------------------------
# Resolve the PR number
# ---------------------------------------------------------------------------
if [ -z "${GITHUB_PR_NUMBER:-}" ]; then
  case "${GITHUB_REF:-}" in
    refs/pull/*/merge|pull/*/merge)
      GITHUB_PR_NUMBER="$(basename "$(dirname "${GITHUB_REF}")")"
      ;;
    *)
      echo "::warning::pr-comment: no GITHUB_PR_NUMBER and GITHUB_REF is not a PR ref (${GITHUB_REF:-unset}); skipping." >&2
      exit 0
      ;;
  esac
fi

# Defence in depth for values that end up in API paths.
case "$GITHUB_PR_NUMBER" in ''|*[!0-9]*)
  echo "::error::pr-comment: invalid PR number" >&2
  exit 1
  ;;
esac
if [ -n "${GITHUB_REPOSITORY:-}" ] && ! [[ "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "::error::pr-comment: invalid repository name" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Build the comment body (pure python3 stdlib; no external deps)
# ---------------------------------------------------------------------------
export HERCULES_MARKER="$MARKER"
export HERCULES_MAX_ROWS="$MAX_ROWS"

if [ -n "${JUNIT_PATH:-}" ] && [ -f "${JUNIT_PATH}" ]; then
  BODY="$(JUNIT_PATH="$JUNIT_PATH" RUN_URL="${RUN_URL:-}" INCLUDE_FAILURE_DETAILS="${INCLUDE_FAILURE_DETAILS:-false}" python3 <<'PY'
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

junit_path = os.environ["JUNIT_PATH"]
run_url = os.environ.get("RUN_URL", "")
marker = os.environ["HERCULES_MARKER"]
max_rows = int(os.environ.get("HERCULES_MAX_ROWS", "50"))


include_details = os.environ.get("INCLUDE_FAILURE_DETAILS", "").strip().lower() == "true"

_SECRET_PATTERNS = [
    re.compile(r"(?i)(://)[^/\s:@]+:[^@\s]+@"),  # URL userinfo
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(authorization|password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),  # JWT
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
]
_SENSITIVE_NAME = re.compile(r"(?i)(key|token|secret|password|passwd|credential)")


def _known_secret_values():
    passthrough = set(os.environ.get("HERCULES_PASSTHROUGH_VARS", "").split())
    values = {os.environ[name] for name in passthrough if name in os.environ and len(os.environ[name]) >= 4}
    values.update(value for name, value in os.environ.items() if _SENSITIVE_NAME.search(name) and len(value) >= 8)
    return sorted(values, key=len, reverse=True)


_SECRET_VALUES = _known_secret_values()


def redact(text):
    """Mask credentials/tokens (patterns plus the literal values of secret-like env vars)."""
    text = str(text)
    for value in _SECRET_VALUES:
        text = text.replace(value, "***")
    text = _SECRET_PATTERNS[0].sub(r"\1***@", text)
    text = _SECRET_PATTERNS[1].sub("Bearer ***", text)
    text = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    for pattern in _SECRET_PATTERNS[3:]:
        text = pattern.sub("***", text)
    return text


def clean(text):
    """Make text safe for a markdown table cell: redacted, no @mentions, no table/HTML breakage."""
    if not text:
        return ""
    text = " ".join(redact(text).split())  # collapse whitespace/newlines
    text = text.replace("|", "\\|").replace("@", "@\u200b")  # zero-width space defuses @mentions
    return escape(text)


def truncate(text, limit=300):
    text = clean(text)
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


tree = ET.parse(junit_path)
root = tree.getroot()

# JUnit XML may be a bare <testsuite> or wrapped in <testsuites>.
suites = []
if root.tag == "testsuites":
    suites = list(root.iter("testsuite"))
elif root.tag == "testsuite":
    suites = [root]

totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
cases = []  # (name, status, duration, message)
for suite in suites:
    for key in ("tests", "failures", "errors", "skipped"):
        totals[key] += int(suite.get(key, "0") or 0)
    totals["time"] += float(suite.get("time", "0") or 0)
    for case in suite.iter("testcase"):
        name = case.get("name", "unknown")
        classname = case.get("classname", "")
        display = f"{classname}.{name}" if classname else name
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if error is not None:
            status, message = "error", (error.text or error.get("message", ""))
        elif failure is not None:
            status, message = "failed", (failure.text or failure.get("message", ""))
        elif skipped is not None:
            status, message = "skipped", (skipped.text or skipped.get("message", ""))
        else:
            status, message = "passed", ""
        cases.append((display, status, case.get("time", "0"), message))

bad = totals["failures"] + totals["errors"]
status_word = "PASSED" if (bad == 0 and totals["tests"] > 0) else "FAILED"
status_icon = "white_check_mark" if status_word == "PASSED" else "x"

lines = [
    marker,
    f"## :{status_icon}: Hercules Tests: {status_word}",
    "",
    f"**Totals:** {totals['tests']} tests | "
    f":white_check_mark: {totals['tests'] - bad - totals['skipped']} passed | "
    f":x: {totals['failures']} failed | "
    f":rotating_light: {totals['errors']} errors | "
    f":fast_forward: {totals['skipped']} skipped | "
    f":stopwatch: {totals['time']:.1f}s",
    "",
    ("| Test case | Status | Duration | Failure message |" if include_details else "| Test case | Status | Duration |"),
    ("| --- | --- | --- | --- |" if include_details else "| --- | --- | --- |"),
]
for name, status, duration, message in cases[:max_rows]:
    icon = {
        "passed": ":white_check_mark:",
        "failed": ":x:",
        "error": ":rotating_light:",
        "skipped": ":fast_forward:",
    }[status]
    row = f"| {clean(name)} | {icon} {status} | {clean(duration)}s |"
    lines.append(row + (f" {truncate(message)} |" if include_details else ""))
if len(cases) > max_rows:
    lines.append(f"| ... and {len(cases) - max_rows} more cases (see artifacts) | |" + (" | |" if include_details else " |"))

if bad > 0:
    lines += [
        "",
        "Failure details (messages, screenshots, traces, bundles) are in the run artifacts; they are not posted here to avoid leaking sensitive data.",
    ]
if run_url:
    lines += [
        "",
        f"[View full workflow run]({run_url}) - HTML report, JUnit XML, failure bundles and proofs are in the run artifacts.",
    ]
lines += [
    "",
    "<sub>Posted by the TestZeus Hercules GitHub Action. Updates in place on re-run.</sub>",
]

print("\n".join(lines))
PY
)"
else
  BODY="${MARKER}
## :warning: Hercules Tests: no results

Hercules produced no results (no JUnit XML was found for this run).
Check the workflow logs and the uploaded artifact for details."
  if [ -n "${RUN_URL:-}" ]; then
    BODY="$BODY
[View workflow run](${RUN_URL})"
  fi
fi

if [ "${HERCULES_PR_COMMENT_DRY_RUN:-}" = "1" ]; then
  printf '%s\n' "$BODY"
  exit 0
fi

# ---------------------------------------------------------------------------
# Post or update the PR comment via gh
# ---------------------------------------------------------------------------
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required to post the PR comment}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required to post the PR comment}"
export GITHUB_TOKEN

api="repos/${GITHUB_REPOSITORY}/issues/${GITHUB_PR_NUMBER}/comments"

# Only adopt (and later PATCH) a marker comment authored by the Actions bot: anyone who can comment on
# the PR could otherwise plant the marker to make this script overwrite or hijack their comment.
comment_author='github-actions[bot]'
comment_id="$(gh api "$api" --paginate --jq ".[] | select(.user.login == \"$comment_author\" and (.body | contains(\"$MARKER\"))) | .id" 2>/dev/null | head -n1 || true)"

if [ -n "$comment_id" ]; then
  echo "Updating existing Hercules PR comment (id=$comment_id)"
  gh api -X PATCH "repos/${GITHUB_REPOSITORY}/issues/comments/${comment_id}" -f body="$BODY" >/dev/null
else
  echo "Creating new Hercules PR comment on PR #${GITHUB_PR_NUMBER}"
  gh api -X POST "$api" -f body="$BODY" >/dev/null
fi
