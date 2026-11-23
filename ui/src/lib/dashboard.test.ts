import { describe, expect, it } from "vitest";
import { chartMax, dayLabel, dayTitle, durationDelta, greeting, passRateDelta, relativeTime, runsDelta } from "./dashboard";
import type { DashboardPeriod } from "./types";

function period(overrides: Partial<DashboardPeriod> = {}): DashboardPeriod {
  return { runs: 10, finished: 10, passed: 8, failed: 2, pass_rate: 0.8, median_duration_s: 100, cost_usd: 1, costed_runs: 10, ...overrides };
}

describe("dashboard helpers", () => {
  it("greets by the time of day", () => {
    expect(greeting(new Date(2026, 10, 18, 8))).toBe("Good morning");
    expect(greeting(new Date(2026, 10, 18, 14))).toBe("Good afternoon");
    expect(greeting(new Date(2026, 10, 18, 21))).toBe("Good evening");
    expect(greeting(new Date(2026, 10, 18, 2))).toBe("Good evening");
  });

  it("says how long ago a run started", () => {
    const now = Date.parse("2026-11-18T12:00:00Z");
    expect(relativeTime("2026-11-18T11:59:40Z", now)).toBe("now");
    expect(relativeTime("2026-11-18T11:56:00Z", now)).toBe("4 min ago");
    expect(relativeTime("2026-11-18T09:00:00Z", now)).toBe("3 h ago");
    expect(relativeTime("2026-11-17T06:00:00Z", now)).toBe("yesterday");
    expect(relativeTime(null, now)).toBe("–");
  });

  it("compares the run count with the period before", () => {
    expect(runsDelta(period({ runs: 12 }), period({ runs: 9 }), 7)).toEqual({ text: "▲ 3 vs the week before", tone: "up" });
    expect(runsDelta(period({ runs: 5 }), period({ runs: 9 }), 30)).toEqual({ text: "▼ 4 vs the 30 days before", tone: "down" });
    expect(runsDelta(period(), period(), 7).tone).toBe("flat");
    expect(runsDelta(period({ runs: 0 }), period({ runs: 0 }), 7).text).toBe("no runs yet");
    expect(runsDelta(period({ runs: 176 }), period({ runs: 0 }), 7)).toEqual({ text: "none the week before", tone: "flat" });
  });

  it("compares the pass rate in points", () => {
    expect(passRateDelta(period({ pass_rate: 0.87 }), period({ pass_rate: 0.83 }))).toEqual({ text: "▲ 4 pts", tone: "up" });
    expect(passRateDelta(period({ pass_rate: 0.7 }), period({ pass_rate: 0.8 }))).toEqual({ text: "▼ 10 pts", tone: "down" });
    expect(passRateDelta(period(), period({ pass_rate: 0.802 })).text).toBe("about the same");
    expect(passRateDelta(period(), period({ pass_rate: null })).text).toBe("8 of 10 passed");
    expect(passRateDelta(period({ pass_rate: null }), period()).text).toBe("no finished runs");
  });

  it("reads a shorter median duration as better", () => {
    expect(durationDelta(period({ median_duration_s: 75 }), period())).toEqual({ text: "▼ 25% faster", tone: "up" });
    expect(durationDelta(period({ median_duration_s: 150 }), period())).toEqual({ text: "▲ 50% slower", tone: "down" });
    expect(durationDelta(period({ median_duration_s: 105 }), period()).text).toBe("about the same");
    expect(durationDelta(period(), period({ median_duration_s: null })).text).toBe("over 10 finished runs");
  });

  it("scales and labels the chart days", () => {
    expect(chartMax([])).toBe(1);
    expect(chartMax([{ date: "2026-11-01", passed: 3, failed: 1, other: 1 }, { date: "2026-11-02", passed: 1, failed: 0, other: 0 }])).toBe(5);
    expect(dayLabel("2026-11-18")).toBe("18");
    expect(dayLabel("2026-12-01")).toMatch(/Dec/);
    expect(dayTitle({ date: "2026-11-18", passed: 2, failed: 1, other: 0 })).toMatch(/3 runs \(2 passed, 1 failed, 0 other\)/);
    expect(dayTitle({ date: "2026-11-18", passed: 0, failed: 0, other: 0 })).toMatch(/no runs$/);
  });
});
