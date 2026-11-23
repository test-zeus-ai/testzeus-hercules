import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { finding, fix, page, qualitySection, RUN } from "../test/qualityFixtures";
import { QualityPanel } from "./QualityPanel";

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

afterEach(() => {
  setClipboard(undefined);
  vi.useRealTimers();
});

describe("QualityPanel", () => {
  it("shows the counts and whether the findings fail the run", () => {
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const counts = screen.getByText("Critical", { selector: "dt" }).closest("dl") as HTMLElement;
    expect(within(counts).getByText("Serious").nextElementSibling?.textContent).toBe("1");
    expect(within(counts).getByText("Minor").nextElementSibling?.textContent).toBe("0");
    expect(screen.getByText(/2 findings at serious or worse fail this run/)).toBeTruthy();
  });

  it("says report only without fail_on", () => {
    render(<QualityPanel runId={RUN} quality={qualitySection({ fail_on: null })} />);
    expect(screen.getByText(/Report only/)).toBeTruthy();
  });

  it("groups findings by impact, most severe first, once per id with the steps they were seen on", () => {
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const headings = screen.getAllByRole("heading", { level: 4 }).map((heading) => heading.textContent);
    expect(headings.slice(0, 3)).toEqual(["Critical · 1", "Serious · 1", "Moderate · 1"]);
    const serious = screen.getByRole("region", { name: /^serious/i });
    const cards = within(serious).getAllByRole("article");
    expect(cards).toHaveLength(1);
    const card = cards[0];
    expect(within(card).getByText("color-contrast")).toBeTruthy();
    expect(within(card).getByText(/WCAG 1\.4\.3 · AA/)).toBeTruthy();
    expect(within(card).getByText("#subscribe", { selector: "p.finding-element code" })).toBeTruthy();
    expect(within(card).getByText("Text contrast 2.9:1 is below 4.5:1")).toBeTruthy();
    // Box numbers come from the page with an annotated screenshot only.
    expect(card.querySelector(".finding-where")?.textContent).toMatch(/step 2 \(box 1\), step 4/);
    // The same fix suggested on both pages appears once.
    expect(within(card).getAllByText("Suggested fix")).toHaveLength(1);
    expect(within(card).getByText("2.91")).toBeTruthy();
  });

  it("marks verified and unverified fixes and shows what the fix measured", () => {
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const serious = screen.getByRole("region", { name: /^serious/i });
    expect(within(serious).getByText("Verified")).toBeTruthy();
    expect(within(serious).getByText("After the fix")).toBeTruthy();
    expect(within(serious).getByText("5.31")).toBeTruthy();
    expect(within(serious).getByText("src/styles/buttons.css")).toBeTruthy();
    expect(within(serious).getByText("#9a9a9a")).toBeTruthy();
    expect(within(serious).getByText("background-color")).toBeTruthy();
    const critical = screen.getByRole("region", { name: /^critical/i });
    expect(within(critical).getByText("Not verified")).toBeTruthy();
    expect(within(critical).getByText(/review this suggestion before applying it/)).toBeTruthy();
    expect(within(critical).queryByText("Verified")).toBeNull();
  });

  it("copies a fix snippet to the clipboard and says so", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const serious = screen.getByRole("region", { name: /^serious/i });
    await act(async () => {
      fireEvent.click(within(serious).getByRole("button", { name: /copy fix for color-contrast/i }));
    });
    expect(writeText).toHaveBeenCalledWith("#subscribe { color: #ffffff; background-color: #2f6db3; }");
    expect(within(serious).getByRole("button", { name: /copied/i })).toBeTruthy();
    expect(within(serious).getByRole("status").textContent).toMatch(/copied to the clipboard/);
  });

  it("does not crash without a clipboard and selects the snippet instead", async () => {
    setClipboard(undefined);
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const critical = screen.getByRole("region", { name: /^critical/i });
    await act(async () => {
      fireEvent.click(within(critical).getByRole("button", { name: /copy fix for image-alt/i }));
    });
    expect(within(critical).getByRole("button", { name: /press ctrl\+c/i })).toBeTruthy();
    expect(window.getSelection()?.toString()).toBe('alt="Team photo"');
  });

  it("reports a rejected clipboard write as a failure", async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("denied")) });
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const critical = screen.getByRole("region", { name: /^critical/i });
    await act(async () => {
      fireEvent.click(within(critical).getByRole("button", { name: /copy fix for image-alt/i }));
    });
    expect(within(critical).getByRole("status").textContent).toMatch(/clipboard is unavailable/);
  });

  it("shows the visual comparison: verdict, classification, style deltas, missing elements and images", () => {
    render(<QualityPanel runId={RUN} quality={qualitySection()} />);
    const visual = screen.getByRole("article", { name: /baseline welcome-desktop/i });
    expect(within(visual).getByText("2.31%")).toBeTruthy();
    expect(within(visual).getByText(/threshold 1\.00%/)).toBeTruthy();
    expect(within(visual).getByText("Changed")).toBeTruthy();
    expect(within(visual).getByText("Layout break")).toBeTruthy();
    expect(within(visual).getByText(/87% · local-llm/)).toBeTruthy();
    const row = within(visual).getByRole("row", { name: /margin-left/ });
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["header .logo", "margin-left", "16px", "48px"]);
    expect(within(visual).getByText("nav.primary")).toBeTruthy();
    const images = within(visual).getAllByRole("img") as HTMLImageElement[];
    expect(images.map((image) => image.getAttribute("src"))).toEqual([
      `/api/runs/${RUN}/evidence/quality/step-4/before.png?max_side=900`,
      `/api/runs/${RUN}/evidence/quality/step-4/after.png?max_side=900`,
      `/api/runs/${RUN}/evidence/quality/step-4/diff.png?max_side=900`,
    ]);
    expect(images.every((image) => image.getAttribute("loading") === "lazy" && image.alt.length > 0)).toBe(true);
    expect(screen.queryByText(/Baseline created/)).toBeNull();
  });

  it("notes a newly created baseline", () => {
    const section = qualitySection();
    const visualPage = section.pages[2];
    const created = { ...section, pages: [page({ ...visualPage, visual: { ...visualPage.visual!, created: true, changed: false, diff_ratio: 0, style_deltas: [], missing: [], classification: null } })] };
    render(<QualityPanel runId={RUN} quality={created} />);
    expect(screen.getByRole("note").textContent).toMatch(/Baseline created/);
    expect(screen.getByText("New baseline")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says why a difference was not classified", () => {
    const section = qualitySection();
    const visualPage = section.pages[2];
    const unclassified = { ...section, pages: [page({ ...visualPage, visual: { ...visualPage.visual!, classification: null, note: "the decider cannot see screenshots" } })] };
    render(<QualityPanel runId={RUN} quality={unclassified} />);
    expect(screen.getByText("Not classified")).toBeTruthy();
    expect(screen.getByText("the decider cannot see screenshots")).toBeTruthy();
  });

  it("shows the annotated screenshot, page errors and frames that were not scanned", () => {
    const section = qualitySection();
    const withError = { ...section, pages: [...section.pages, page({ step: 6, error: "axe timed out", url: "http://localhost:3000/cart" })] };
    render(<QualityPanel runId={RUN} quality={withError} />);
    const shot = screen.getByRole("img", { name: /annotated screenshot, step 2/i });
    expect(shot.getAttribute("src")).toBe(`/api/runs/${RUN}/evidence/quality/step-2/annotated.png?max_side=720`);
    expect(screen.getByRole("link", { name: /findings\.json/ }).getAttribute("href")).toBe(`/api/runs/${RUN}/evidence/quality/step-2/findings.json`);
    expect(screen.getByText(/did not complete: axe timed out/)).toBeTruthy();
    expect(screen.getByText("iframe#chat")).toBeTruthy();
  });

  it("puts long lists behind a disclosure", () => {
    const many = Array.from({ length: 7 }, (_, offset) => finding({ id: `f-${offset}`, impact: "minor", rule: `rule-${offset}` }));
    const section = qualitySection({ pages: [page({ findings: many, fixes: [] })], counts: { critical: 0, serious: 0, moderate: 0, minor: 7, findings: 7, fixes: 0, verified_fixes: 0, pages: 1 } });
    render(<QualityPanel runId={RUN} quality={section} />);
    const more = screen.getByText("Show 2 more minor findings");
    expect(more.tagName).toBe("SUMMARY");
    expect(more.closest("details")?.querySelectorAll(".finding")).toHaveLength(2);
  });

  it("does not link a rule reference that is not http(s)", () => {
    const section = qualitySection({ pages: [page({ findings: [finding({ help_url: "javascript:alert(1)" })], fixes: [fix()] })] });
    render(<QualityPanel runId={RUN} quality={section} />);
    expect(screen.queryByRole("link", { name: /rule reference/i })).toBeNull();
  });
});
