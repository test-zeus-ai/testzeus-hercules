import type { DashboardDay, DashboardPeriod } from "./types";

export type Tone = "up" | "down" | "flat";

export interface Delta {
  text: string;
  tone: Tone;
}

/** "Good morning" before noon, "Good afternoon" before six, then "Good evening". */
export function greeting(at: Date = new Date()): string {
  const hour = at.getHours();
  return hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

/** "now", "4 min ago", "2 h ago", "yesterday", then a short date. */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "–";
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return "–";
  const minutes = Math.round((now - at) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  if (hours < 48) return "yesterday";
  return new Date(at).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const FLAT: Delta = { text: "about the same", tone: "flat" };

/** More runs than the period before reads as up; the same count as flat. */
export function runsDelta(current: DashboardPeriod, previous: DashboardPeriod, days: number): Delta {
  const change = current.runs - previous.runs;
  const before = `the ${days === 7 ? "week" : `${days} days`} before`;
  if (!previous.runs && !current.runs) return { text: "no runs yet", tone: "flat" };
  if (!previous.runs) return { text: `none ${before}`, tone: "flat" };
  if (!change) return { text: `same as ${before}`, tone: "flat" };
  return { text: `${change > 0 ? "▲" : "▼"} ${Math.abs(change)} vs ${before}`, tone: change > 0 ? "up" : "down" };
}

/** A pass rate change in percentage points; less than one point reads as about the same. */
export function passRateDelta(current: DashboardPeriod, previous: DashboardPeriod): Delta {
  if (current.pass_rate === null) return { text: "no finished runs", tone: "flat" };
  if (previous.pass_rate === null) return { text: `${current.passed} of ${current.passed + current.failed} passed`, tone: "flat" };
  const points = Math.round((current.pass_rate - previous.pass_rate) * 100);
  if (!points) return FLAT;
  return { text: `${points > 0 ? "▲" : "▼"} ${Math.abs(points)} pts`, tone: points > 0 ? "up" : "down" };
}

/** Faster is better: a median within 10% of the period before reads as about the same. */
export function durationDelta(current: DashboardPeriod, previous: DashboardPeriod): Delta {
  if (current.median_duration_s === null) return { text: "no finished runs", tone: "flat" };
  if (previous.median_duration_s === null || previous.median_duration_s === 0) return { text: `over ${current.finished} finished ${current.finished === 1 ? "run" : "runs"}`, tone: "flat" };
  const ratio = current.median_duration_s / previous.median_duration_s - 1;
  if (Math.abs(ratio) < 0.1) return FLAT;
  return { text: `${ratio < 0 ? "▼" : "▲"} ${Math.round(Math.abs(ratio) * 100)}% ${ratio < 0 ? "faster" : "slower"}`, tone: ratio < 0 ? "up" : "down" };
}

/** The tallest day's total, for scaling the bars (at least 1, so an empty chart has no zero division). */
export function chartMax(days: DashboardDay[]): number {
  return Math.max(1, ...days.map((day) => day.passed + day.failed + day.other));
}

/** "18" for a chart day; the first day of a month as "Nov 1". */
export function dayLabel(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const at = new Date(year, month - 1, day);
  return day === 1 ? at.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : String(day);
}

export function dayTitle(day: DashboardDay): string {
  const [year, month, date] = day.date.split("-").map(Number);
  const when = new Date(year, month - 1, date).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const total = day.passed + day.failed + day.other;
  if (!total) return `${when}: no runs`;
  return `${when}: ${total} ${total === 1 ? "run" : "runs"} (${day.passed} passed, ${day.failed} failed, ${day.other} other)`;
}
