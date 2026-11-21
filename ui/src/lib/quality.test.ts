import { describe, expect, it } from "vitest";
import { finding, fix, page, qualitySection } from "../test/qualityFixtures";
import { failingCount, formatValue, groupFindings, pageLabel, thresholdLabel } from "./quality";

describe("groupFindings", () => {
  it("orders groups by impact and merges a finding seen on several pages", () => {
    const groups = groupFindings(qualitySection().pages);
    expect(groups.map((group) => group.impact)).toEqual(["critical", "serious", "moderate"]);
    const contrast = groups[1].findings[0];
    expect(contrast.occurrences.map((occurrence) => [occurrence.page.step, occurrence.box])).toEqual([
      [2, 1],
      [4, null],
    ]);
    expect(contrast.fixes).toHaveLength(1);
  });

  it("keeps the most severe impact and lists verified fixes first", () => {
    const pages = [
      page({ findings: [finding({ impact: "minor" })], fixes: [fix({ verified: false, suggested: "#fefefe" })] }),
      page({ step: 3, findings: [finding({ impact: "critical" })], fixes: [fix()] }),
    ];
    const groups = groupFindings(pages);
    expect(groups).toHaveLength(1);
    expect(groups[0].impact).toBe("critical");
    expect(groups[0].findings[0].fixes.map((item) => item.verified)).toEqual([true, false]);
  });

  it("ignores fixes for findings on other pages", () => {
    const groups = groupFindings([page({ findings: [finding()] }), page({ step: 5, fixes: [fix()] })]);
    expect(groups[0].findings[0].fixes).toEqual([]);
  });
});

describe("quality helpers", () => {
  it("counts the findings that fail the run", () => {
    const counts = { critical: 1, serious: 2, moderate: 3, minor: 4, findings: 10, fixes: 0, verified_fixes: 0, pages: 1 };
    expect(failingCount(counts, "critical")).toBe(1);
    expect(failingCount(counts, "moderate")).toBe(6);
    expect(failingCount(counts, "minor")).toBe(10);
    expect(thresholdLabel("critical")).toBe("critical");
    expect(thresholdLabel("serious")).toBe("serious or worse");
  });

  it("labels pages and formats measured values", () => {
    expect(pageLabel(page({ step: 3 }))).toBe("Step 3");
    expect(pageLabel(page({ step: 5, source: "run" }))).toBe("End of run (after step 5)");
    expect(formatValue(4.5678)).toBe("4.57");
    expect(formatValue(3)).toBe("3");
    expect(formatValue(["a", 1])).toBe("a, 1");
    expect(formatValue({ w: 2 })).toBe('{"w":2}');
    expect(formatValue(null)).toBe("–");
  });
});
