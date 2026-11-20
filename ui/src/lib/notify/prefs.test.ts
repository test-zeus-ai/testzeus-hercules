import { describe, expect, it } from "vitest";
import { DEFAULT_PREFS, notifyPrefs } from "./prefs";

describe("notifyPrefs", () => {
  it("leaves on whatever the config does not turn off", () => {
    expect(notifyPrefs(undefined)).toEqual(DEFAULT_PREFS);
    expect(DEFAULT_PREFS).toEqual({ runs: "all", approvals: true, explore: true, sound: true, desktop: true });
    expect(notifyPrefs({ runs: "failures", sound: false })).toEqual({ ...DEFAULT_PREFS, runs: "failures", sound: false });
    expect(notifyPrefs({ runs: "sometimes", desktop: false, approvals: false })).toEqual({ ...DEFAULT_PREFS, desktop: false, approvals: false });
  });
});
