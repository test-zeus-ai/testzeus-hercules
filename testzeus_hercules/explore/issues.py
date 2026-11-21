"""A finding as an issue for a tracker: GitHub, Jira or Linear (Markdown), with everything a developer needs.

``hercules explore issue <finding> --tracker github`` prints it (or opens a prefilled GitHub issue URL); the tracker
key a person gets back is stored on the finding with ``hercules explore triage <finding> --tracker KEY``.
"""

from __future__ import annotations

from urllib.parse import urlencode

from testzeus_hercules.explore.models import Finding

__all__ = ["TRACKERS", "issue_text", "issue_title", "github_new_issue_url"]

TRACKERS = ("github", "jira", "linear", "markdown")


def issue_title(finding: Finding) -> str:
    return f"[{finding.effective_severity}] {finding.title}"[:250]


def issue_text(finding: Finding, tracker: str = "markdown") -> str:
    """The issue body. Jira gets its wiki markup; the others Markdown."""
    env = finding.environment
    severity = finding.severity or f"{finding.proposed_severity} (proposed by the model, not confirmed)"
    priority = finding.priority or f"{finding.proposed_priority} (proposed)"
    steps = finding.steps or ["(see the case)"]
    if tracker == "jira":
        lines = [
            f"*Severity:* {severity}   *Priority:* {priority}",
            "",
            "h3. Steps to reproduce",
            *[f"# {step}" for step in steps],
            "",
            f"*Expected:* {finding.expected}",
            f"*Actual:* {finding.actual}",
            "",
            "h3. Environment",
            f"||Build|{env.build}|",
            f"||Base URL|{env.base_url}|",
            f"||Browser|{env.browser} {env.browser_version}|",
            f"||Viewport / locale|{env.viewport} / {env.locale or 'default'}|",
            f"||Account|{env.account or 'the session’s'}|",
            f"||Found by|Hercules {env.hercules_version} explore session {env.session}, charter {env.charter}|",
            "",
            f"*Reproduced:* recorded {finding.reproduced_recorded}, interpreted {finding.reproduced_interpreted}; minimized from {finding.minimized_from} moves",
            f"*Failing case:* {{{{{finding.case_path or 'not written'}}}}} — run it with {{{{hercules run {finding.case_path}}}}}",
        ]
        if finding.evidence:
            lines += ["", "h3. Evidence", *[f"* {item}" for item in finding.evidence]]
        return "\n".join(lines) + "\n"
    lines = [
        f"**Severity:** {severity} · **Priority:** {priority} · **Oracle:** {finding.oracle} ({finding.tier})",
        "",
        "### Steps to reproduce",
        *[f"{index}. {step}" for index, step in enumerate(steps, start=1)],
        "",
        f"**Expected:** {finding.expected}",
        "",
        f"**Actual:** {finding.actual}",
        "",
        "### Environment",
        "",
        "| | |",
        "|---|---|",
        f"| Build | {env.build} |",
        f"| Base URL | {env.base_url} |",
        f"| Browser | {env.browser} {env.browser_version} |",
        f"| Viewport / locale | {env.viewport} / {env.locale or 'default'} |",
        f"| Account | {env.account or 'the session’s'} |",
        f"| Found by | Hercules {env.hercules_version}, explore session {env.session}, charter {env.charter} |",
        "",
        f"Reproduced: recorded replay {finding.reproduced_recorded}, interpreted replay {finding.reproduced_interpreted}; minimized from {finding.minimized_from} moves.",
    ]
    if finding.promise:
        lines.append(f"Broken promise: {finding.promise} ({finding.promise_source or 'learned'}){f', requirement {finding.requirement}' if finding.requirement else ''}.")
    if finding.case_path:
        lines += ["", f"Failing case: `{finding.case_path}` — `hercules run {finding.case_path}` (it passes once the bug is fixed)."]
    if finding.evidence:
        lines += ["", "### Evidence", *[f"- `{item}`" for item in finding.evidence]]
    lines += ["", f"<!-- hercules-finding: {finding.id} signature: {finding.signature} -->"]
    return "\n".join(lines) + "\n"


def github_new_issue_url(repository: str, finding: Finding) -> str:
    """A prefilled ``issues/new`` URL for ``owner/name``; the body is clipped to what a URL carries."""
    body = issue_text(finding, "github")[:6000]
    return f"https://github.com/{repository}/issues/new?" + urlencode({"title": issue_title(finding), "body": body, "labels": f"bug,{finding.effective_severity}"})
