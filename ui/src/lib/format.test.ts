import { describe, expect, it } from "vitest";
import { formatDateTime, localZoneName } from "./format";

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
