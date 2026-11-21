// Security scan results for display: severity order, the findings that fail a scan, and the rest grouped by template.

import { SEVERITIES, type SecurityFinding, type SecurityScan, type Severity } from "./types";

export const SEVERITY_LABELS: Record<Severity, string> = { critical: "Critical", high: "High", medium: "Medium", low: "Low", info: "Info" };

/** The quality panel's impact colours, reused: critical, then high, medium and low; info stays neutral. */
export const SEVERITY_CLASS: Record<Severity, string> = { critical: "impact-critical", high: "impact-serious", medium: "impact-moderate", low: "impact-minor", info: "" };

export function atOrAbove(severity: Severity, gate: Severity): boolean {
  return SEVERITIES.indexOf(severity) <= SEVERITIES.indexOf(gate);
}

export function failingFindings(scan: SecurityScan): SecurityFinding[] {
  return scan.findings.filter((finding) => atOrAbove(finding.severity, scan.fail_on));
}

export interface FindingGroup {
  key: string;
  severity: Severity;
  templateId: string;
  name: string;
  matchers: string[];
  places: string[];
  count: number;
}

/** Findings below the scan's gate, one group per severity and template (most severe first), with where they matched. */
export function groupBelowGate(scan: SecurityScan): FindingGroup[] {
  const groups = new Map<string, FindingGroup>();
  for (const finding of scan.findings) {
    if (atOrAbove(finding.severity, scan.fail_on)) continue;
    const key = `${finding.severity}|${finding.template_id}`;
    const group = groups.get(key) ?? { key, severity: finding.severity, templateId: finding.template_id, name: finding.name, matchers: [], places: [], count: 0 };
    if (finding.matcher && !group.matchers.includes(finding.matcher)) group.matchers.push(finding.matcher);
    if (!group.places.includes(finding.matched_at)) group.places.push(finding.matched_at);
    group.count += 1;
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) || a.templateId.localeCompare(b.templateId));
}
