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

# ---------------------------------------------------------------------------
# Build the comment body (pure python3 stdlib; no external deps)
# ---------------------------------------------------------------------------
export HERCULES_MARKER="$MARKER"
export HERCULES_MAX_ROWS="$MAX_ROWS"

if [ -n "${JUNIT_PATH:-}" ] && [ -f "${JUNIT_PATH}" ]; then
  BODY="$(JUNIT_PATH="$JUNIT_PATH" RUN_URL="${RUN_URL:-}" python3 <<'PY'
import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

junit_path = os.environ["JUNIT_PATH"]
run_url = os.environ.get("RUN_URL", "")
marker = os.environ["HERCULES_MARKER"]
max_rows = int(os.environ.get("HERCULES_MAX_ROWS", "50"))


def clean(text):
    """Make text safe for a markdown table cell."""
    if not text:
        return ""
    text = " ".join(str(text).split())  # collapse whitespace/newlines
    text = text.replace("|", "\\|")
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
    "| Test case | Status | Duration | Failure message |",
    "| --- | --- | --- | --- |",
]
for name, status, duration, message in cases[:max_rows]:
    icon = {
        "passed": ":white_check_mark:",
        "failed": ":x:",
        "error": ":rotating_light:",
        "skipped": ":fast_forward:",
    }[status]
    lines.append(f"| {clean(name)} | {icon} {status} | {clean(duration)}s | {truncate(message)} |")
if len(cases) > max_rows:
    lines.append(f"| ... and {len(cases) - max_rows} more cases (see artifacts) | | | |")

if bad > 0:
    lines += [
        "",
        "Failure evidence (screenshots, traces, bundles) is attached to the failing cases in the artifacts.",
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

# ---------------------------------------------------------------------------
# Post or update the PR comment via gh
# ---------------------------------------------------------------------------
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required to post the PR comment}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required to post the PR comment}"
export GITHUB_TOKEN

api="repos/${GITHUB_REPOSITORY}/issues/${GITHUB_PR_NUMBER}/comments"

comment_id="$(gh api "$api" --paginate --jq ".[] | select(.body | contains(\"$MARKER\")) | .id" 2>/dev/null | head -n1 || true)"

if [ -n "$comment_id" ]; then
  echo "Updating existing Hercules PR comment (id=$comment_id)"
  gh api -X PATCH "repos/${GITHUB_REPOSITORY}/issues/comments/${comment_id}" -f body="$BODY" >/dev/null
else
  echo "Creating new Hercules PR comment on PR #${GITHUB_PR_NUMBER}"
  gh api -X POST "$api" -f body="$BODY" >/dev/null
fi
