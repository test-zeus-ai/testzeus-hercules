"""The session's findings report: ``report.md`` for people (scannable in two minutes) and ``report.json`` for tools.

It opens with what to read first: no findings is not a pass. Then the findings by severity (new, seen again,
regression; proposed severity until a person confirms one), one debrief per charter in SBTM shape, known noise from the
happy path, what did not reproduce, UX notes, the coverage gaps (never a percentage), the charters still to run, the
actions the scope guard blocked, and the severity rubric the calls were made against.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testzeus_hercules.explore.missions import load_rubric, rank
from testzeus_hercules.explore.models import Finding

if TYPE_CHECKING:
    from testzeus_hercules.explore.coordinator import Session

__all__ = ["render_markdown", "write_report"]


def write_report(session: Session) -> None:
    view = session.snapshot()
    data = json.loads(view.model_dump_json())
    data.update(
        {
            "baseline": [{"signature": signature, "title": title} for signature, title in session.baseline.items()],
            "flaky": session.flaky,
            "not_reproduced": session.not_reproduced,
            "debriefs": session.debriefs,
            "finding_outcomes": session.finding_outcomes,
            "blocked": session.blocked[:200],
            "promises": [promise.model_dump() for promise in session.promises],
            "environment": session.environment.model_dump(),
            "rubric": load_rubric(session.project_dir),
        }
    )
    session.dir.mkdir(parents=True, exist_ok=True)
    (session.dir / "report.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (session.dir / "report.md").write_text(render_markdown(data), encoding="utf-8")


def _finding_line(finding: Finding, outcome: str) -> str:
    severity = f"**{finding.severity}** (confirmed)" if finding.severity else f"{finding.proposed_severity} (proposed)"
    mark = {"regression": " — **regression**", "seen_again": " — seen before"}.get(outcome, "")
    return f"- {severity} {finding.title}{mark}. Reproduced {finding.reproduced_recorded} recorded, {finding.reproduced_interpreted} interpreted. Case ({finding.case}): `{finding.case_path or '—'}` (`{finding.id}`)"


def render_markdown(data: dict[str, Any]) -> str:
    spec = data["spec"]
    findings = [Finding.model_validate(item) for item in data.get("findings", [])]
    outcomes = data.get("finding_outcomes", {})
    lines = [f"# Exploration {data['id']}", ""]
    if not findings:
        lines += ["> **No findings is not a pass.** It means nothing reproducible was found in this time, with the coverage below.", ""]
    else:
        new = sum(outcomes.get(item.id) in ("new", "regression") for item in findings)
        lines += [f"> **{len(findings)} finding(s)**, {new} new or regressed. Severities are the model's proposals until a person confirms them (`hercules explore triage`).", ""]
    sizes = f"{data['workers']} explorer(s), {data['reproducers']} reproducer(s)"
    if data.get("size_changes"):
        sizes += f" ({len(data['size_changes'])} change(s) during the session)"
    lines += [
        f"- **Mission:** {spec.get('prompt') or spec['mission']}",
        f"- **Ended:** {data.get('message') or data['status']} (exit {data.get('exit_code')})",
        f"- **Time:** {data.get('elapsed_s', 0):.0f} s of {spec['budget']['time_s']} s · **Swarm:** {sizes} · **Model cost:** "
        + (f"~${data.get('cost_usd', 0):.2f} (hosted model calls estimated at explore.cost_per_decision_usd each)" if data.get("cost_estimated") else f"${data.get('cost_usd', 0):.2f}"),
        f"- **Environment:** {data['environment'].get('base_url')} · build {data['environment'].get('build')} · {data['environment'].get('browser')}",
        "",
        "## Findings",
        "",
    ]
    if findings:
        for item in sorted(findings, key=lambda found: rank(found.effective_severity)):
            lines.append(_finding_line(item, outcomes.get(item.id, "new")))
            for step in item.steps[:6]:
                lines.append(f"  - {step}")
            lines.append(f"  - Expected: {item.expected} · Actual: {item.actual}")
    else:
        lines.append("None.")
    lines += ["", "## Charters", ""]
    for debrief in data.get("debriefs", []):
        tried = ", ".join(f"{lever} {count}" for lever, count in (debrief.get("by_lever") or {}).items()) or "no moves"
        split = debrief.get("time_split") or {}
        lines += [
            f"### {debrief.get('charter')}",
            f"- Tried: {debrief.get('turns', 0)} moves ({tried}) over {debrief.get('states', 0)} page state(s); ended: {debrief.get('ended')}.",
            f"- Saw: {'; '.join(debrief.get('clues') or []) or 'nothing wrong'}.",
            f"- Did not get to: {', '.join(debrief.get('not_reached') or []) or 'nothing listed'}.",
            f"- Time: setup {split.get('setup_s', 0):.0%}, testing {split.get('testing_s', 0):.0%}, investigating {split.get('investigating_s', 0):.0%}.",
            f"- Confidence: {debrief.get('confidence')} ({debrief.get('confidence_reason')}).",
            "",
        ]
    if data.get("baseline"):
        lines += ["## Already there on the happy path (known noise, not reported again)", "", *[f"- {item['title']}" for item in data["baseline"]], ""]
    if data.get("flaky") or data.get("not_reproduced"):
        lines += ["## Clues that did not become findings", ""]
        for item in data.get("flaky", []):
            lines.append(f"- Flaky ({item.get('flaky')}): {item['title']} — recorded {item.get('recorded')}, interpreted {item.get('interpreted')}")
        for item in data.get("not_reproduced", []):
            lines.append(f"- Not reproduced: {item['title']} — {'; '.join(item.get('notes') or [])}")
        lines.append("")
    if data.get("ux_notes"):
        lines += ["## UX notes (design preferences, not bugs)", "", *[f"- {note}" for note in data["ux_notes"]], ""]
    learned = [promise for promise in data.get("promises", []) if promise.get("source") == "learned"]
    if data.get("promises"):
        lines += ["## Promises checked", ""]
        lines += [f"- {promise['text']} ({promise['source']}{', ' + promise['requirement'] if promise.get('requirement') else ''})" for promise in data["promises"]]
        if learned:
            lines.append("- Learned promises above can encode a bug as correct: review them, and promote the right ones to .hercules/explore/promises.yaml.")
        lines.append("")
    coverage = data.get("coverage") or {}
    lines += [
        "## Coverage gaps",
        "",
        f"Routes seen: {', '.join(coverage.get('routes_seen') or []) or 'none'} ({coverage.get('states_seen', 0)} page states, {coverage.get('moves_tried', 0)} distinct moves).",
    ]
    for gap in coverage.get("gaps") or []:
        lines.append(f"- {gap['route']}: levers not used: {', '.join(gap['levers_not_used'])}")
    lines.append("Coverage measures what Hercules did, not what the app contains: parts recon never found are not listed.")
    lines += ["", "## Next charters", ""]
    lines += [f"- {title}" for title in (coverage.get("charters_not_run") or [])[:15]] or ["- None queued."]
    if data.get("blocked"):
        lines += ["", "## Blocked by the scope guard", "", *[f"- {item['what']}: {item['reason']}" for item in data["blocked"][:20]]]
    lines += ["", "## Severity rubric", "", *[f"- **{level}**: {text}" for level, text in data.get("rubric", {}).items()], ""]
    return "\n".join(lines)
