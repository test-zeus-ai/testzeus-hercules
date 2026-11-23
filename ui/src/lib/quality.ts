// Shaping the `quality` section of a run result for display: findings deduplicated across
// pages and grouped by impact, each with the fixes suggested for it.

import { IMPACTS, type Finding, type FixSuggestion, type Impact, type QualityCounts, type QualityKind, type QualityPage } from "./types";

/** Where a finding was seen: the page (step) and its box number on the annotated screenshot. */
export interface Occurrence {
  page: QualityPage;
  pageIndex: number;
  /** 1-based box on the page's annotated screenshot (boxes follow the findings order), when there is one. */
  box: number | null;
}

export interface GroupedFinding {
  finding: Finding;
  occurrences: Occurrence[];
  /** Verified fixes first; the same fix suggested on several pages appears once. */
  fixes: FixSuggestion[];
}

export interface ImpactGroup {
  impact: Impact;
  findings: GroupedFinding[];
}

export const IMPACT_LABELS: Record<Impact, string> = { critical: "Critical", serious: "Serious", moderate: "Moderate", minor: "Minor" };

export const KIND_LABELS: Record<QualityKind, string> = { a11y: "Accessibility", visual: "Visual", layout: "Layout" };

const CLASSIFICATION_LABELS: Record<string, string> = {
  intended_change: "Intended change",
  layout_break: "Layout break",
  content_change: "Content change",
};

const METHOD_LABELS: Record<FixSuggestion["method"], string> = { css_override: "CSS override", attribute: "Attribute", text: "Text" };

export function annotatedScreenshot(page: QualityPage): string | null {
  return page.evidence.find((ref) => /annotated\.png$/i.test(ref)) ?? null;
}

function fixKey(fix: FixSuggestion): string {
  return [fix.method, fix.property_or_attr, fix.state ?? "", fix.target_selector ?? "", fix.suggested, fix.snippet ?? ""].join("\u0000");
}

/** Findings by impact (most severe first, empty groups left out), deduplicated by id across pages. */
export function groupFindings(pages: QualityPage[]): ImpactGroup[] {
  const byId = new Map<string, GroupedFinding>();
  const fixKeys = new Map<string, Set<string>>();
  pages.forEach((page, pageIndex) => {
    const annotated = annotatedScreenshot(page) !== null;
    page.findings.forEach((finding, offset) => {
      let entry = byId.get(finding.id);
      if (!entry) {
        entry = { finding, occurrences: [], fixes: [] };
        byId.set(finding.id, entry);
        fixKeys.set(finding.id, new Set());
      } else if (IMPACTS.indexOf(finding.impact) < IMPACTS.indexOf(entry.finding.impact)) {
        entry = { ...entry, finding };
        byId.set(finding.id, entry);
      }
      entry.occurrences.push({ page, pageIndex, box: annotated ? offset + 1 : null });
      const seen = fixKeys.get(finding.id)!;
      for (const fix of page.fixes) {
        if (fix.finding_ref !== finding.id || seen.has(fixKey(fix))) continue;
        seen.add(fixKey(fix));
        entry.fixes.push(fix);
      }
    });
  });
  const all = [...byId.values()].map((entry) => ({ ...entry, fixes: [...entry.fixes].sort((a, b) => Number(b.verified) - Number(a.verified)) }));
  return IMPACTS.map((impact) => ({ impact, findings: all.filter((entry) => entry.finding.impact === impact) })).filter((group) => group.findings.length > 0);
}

/** Findings at or above `failOn` (the ones that fail the run). */
export function failingCount(counts: QualityCounts, failOn: Impact): number {
  return IMPACTS.slice(0, IMPACTS.indexOf(failOn) + 1).reduce((sum, impact) => sum + (counts[impact] ?? 0), 0);
}

/** "serious" → "serious or worse"; "critical" → "critical". */
export function thresholdLabel(failOn: Impact): string {
  return failOn === "critical" ? "critical" : `${failOn} or worse`;
}

export function pageLabel(page: QualityPage): string {
  return page.source === "run" ? `End of run (after step ${page.step})` : `Step ${page.step}`;
}

export function classificationLabel(label: string): string {
  return CLASSIFICATION_LABELS[label] ?? label.replace(/_/g, " ");
}

export function methodLabel(method: FixSuggestion["method"]): string {
  return METHOD_LABELS[method] ?? method;
}

/** contrast_ratio → "contrast ratio". */
export function humanKey(key: string): string {
  return key.replace(/_/g, " ");
}

/** A measured value for display: numbers to at most two decimals, lists joined, objects as JSON. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  return JSON.stringify(value);
}

/** A 0–1 ratio as a percentage with two decimals (pixel diffs are small). */
export function formatRatio(ratio: number): string {
  return `${(ratio * 100).toFixed(2)}%`;
}

/** Only http(s) links from findings are rendered as links. */
export function safeHref(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}
