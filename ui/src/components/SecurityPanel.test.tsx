import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RUN, securityScan, securitySection } from "../test/securityFixtures";
import { SecurityPanel } from "./SecurityPanel";

describe("SecurityPanel", () => {
  it("shows the findings per severity over all scans", () => {
    render(<SecurityPanel runId={RUN} security={securitySection()} />);
    const counts = screen.getByLabelText("Findings by severity");
    expect(within(counts).getByText("High").nextElementSibling?.textContent).toBe("1");
    expect(within(counts).getByText("Info").nextElementSibling?.textContent).toBe("3");
    expect(within(counts).getByText("Critical").nextElementSibling?.textContent).toBe("0");
  });

  it("lists the failing findings of a scan with where they matched and what they extracted", () => {
    render(<SecurityPanel runId={RUN} security={securitySection()} />);
    expect(screen.getByText(/1 finding at medium or above fails this step/)).toBeTruthy();
    const card = screen.getByRole("article", { name: /High: exposed-env-file/ });
    expect(within(card).getByText("http://127.0.0.1:8000/.env")).toBeTruthy();
    expect(within(card).getByText("Extracted: DB_HOST")).toBeTruthy();
  });

  it("folds the findings below the gate into one line per template", () => {
    render(<SecurityPanel runId={RUN} security={securitySection()} />);
    expect(screen.getByText("1 other template matched below medium")).toBeTruthy();
    expect(screen.getByText(/x-frame-options, content-security-policy/)).toBeTruthy();
  });

  it("says when nothing reaches the gate, and when the scan did not finish", () => {
    const clean = securityScan({ findings: [] });
    const broken = securityScan({ step: 2, findings: [], error: "the security scanner (nuclei) is not installed" });
    render(<SecurityPanel runId={RUN} security={securitySection([clean, broken])} />);
    expect(screen.getByText(/No finding at medium or above/)).toBeTruthy();
    expect(screen.getByText(/The scan did not finish: the security scanner \(nuclei\) is not installed/)).toBeTruthy();
  });

  it("links the scan's evidence files", () => {
    render(<SecurityPanel runId={RUN} security={securitySection()} />);
    const link = screen.getByRole("link", { name: /summary\.txt/ });
    expect(link.getAttribute("href")).toContain("summary.txt");
  });
});
