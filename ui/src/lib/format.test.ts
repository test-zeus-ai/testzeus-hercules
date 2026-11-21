import { describe, expect, it } from "vitest";
import { compactCount, decisionUsage, formatDateTime, localZoneName } from "./format";

describe("formatDateTime", () => {
  it("shows the month, day, year and time with AM or PM", () => {
    const shown = formatDateTime("2026-11-16T09:22:05Z");
    expect(shown).toMatch(/^[A-Z][a-z]{2} \d{1,2}, 2026, \d{1,2}:\d{2}\s?[AP]M$/);
    expect(formatDateTime(null)).toBe("–");
    expect(formatDateTime("not a date")).toBe("–");
  });
});

describe("localZoneName", () => {
  it("names the device's time zone", () => {
    expect(localZoneName().length).toBeGreaterThan(0);
  });
});

describe("decision engine usage", () => {
  const result = (engines: object, cost: object) => ({ engines: { egress: "hybrid", ...engines }, cost: { tokens_in: 0, tokens_out: 0, ...cost } }) as Parameters<typeof decisionUsage>[0];

  it("names JEV or Clef, its calls and the estimated prompt tokens", () => {
    expect(decisionUsage(result({ jev_calls: 10 }, { decision_tokens_est: 47463 }))).toEqual({ engine: "JEV", text: "~47k tokens", calls: "10 calls", tokens: 47463 });
    expect(decisionUsage(result({ clef_calls: 1 }, { decision_tokens_est: 812 }))?.text).toBe("~812 tokens");
    expect(decisionUsage(result({ clef_calls: 3 }, {}))?.engine).toBe("Clef");
  });

  it("leaves the tokens out for runs recorded before the estimate, and the line out when nothing was asked", () => {
    expect(decisionUsage(result({ jev_calls: 4 }, {}))?.text).toBe("4 calls");
    expect(decisionUsage(result({}, {}))).toBeNull();
    expect(decisionUsage(result({ jev_calls: 0 }, { decision_tokens_est: 5 }))).toBeNull();
  });

  it("shortens large counts", () => {
    expect([compactCount(999), compactCount(1234), compactCount(1000), compactCount(47463)]).toEqual(["999", "1.2k", "1k", "47k"]);
  });
});
