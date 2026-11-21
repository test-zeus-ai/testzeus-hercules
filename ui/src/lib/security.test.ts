import { describe, expect, it } from "vitest";
import { securityScan, secFinding } from "../test/securityFixtures";
import { atOrAbove, failingFindings, groupBelowGate } from "./security";

describe("security results", () => {
  it("compares severities, critical being the most severe", () => {
    expect(atOrAbove("critical", "medium")).toBe(true);
    expect(atOrAbove("medium", "medium")).toBe(true);
    expect(atOrAbove("low", "medium")).toBe(false);
    expect(atOrAbove("info", "info")).toBe(true);
  });

  it("lists the findings at or above the scan's fail_on", () => {
    const failing = failingFindings(securityScan());
    expect(failing.map((item) => item.template_id)).toEqual(["exposed-env-file"]);
  });

  it("groups the other findings by severity and template, with their matchers and places once each", () => {
    const scan = securityScan({ findings: [secFinding(), secFinding({ matcher: "csp" }), secFinding({ matched_at: "http://127.0.0.1:8000/a" }), secFinding({ template_id: "weak-cipher", severity: "low", matcher: null })] });
    const groups = groupBelowGate(scan);
    expect(groups.map((group) => [group.severity, group.templateId, group.count])).toEqual([
      ["low", "weak-cipher", 1],
      ["info", "http-missing-security-headers", 3],
    ]);
    expect(groups[1].matchers).toEqual(["x-frame-options", "csp"]);
    expect(groups[1].places).toEqual(["http://127.0.0.1:8000", "http://127.0.0.1:8000/a"]);
  });
});
