import { describe, expect, it } from "vitest";
import { DEFAULT_RUN_FORM, DEFAULT_RUN_OPTIONS, formFromSettings, parseApprovalTimeout, parseDestructiveTerms, rerunOptionsBody, runOptionsBody } from "./runOptions";

describe("run options", () => {
  it("parses destructive terms", () => {
    expect(parseDestructiveTerms(" delete, ,pay ")).toEqual({ terms: ["delete", "pay"], error: null });
    expect(parseDestructiveTerms(Array.from({ length: 51 }, (_, i) => `t${i}`).join(",")).error).toMatch(/at most 50/);
    expect(parseDestructiveTerms("x".repeat(61)).error).toMatch(/at most 60/);
  });

  it("parses the answer timeout", () => {
    expect(parseApprovalTimeout("")).toEqual({ seconds: null, error: null });
    expect(parseApprovalTimeout("30.5").seconds).toBe(30.5);
    expect(parseApprovalTimeout("0").error).toBeTruthy();
    expect(parseApprovalTimeout("86401").error).toBeTruthy();
  });

  it("sends nothing for defaults and ignores options of disabled checks", () => {
    expect(runOptionsBody(DEFAULT_RUN_OPTIONS)).toEqual({});
    expect(runOptionsBody({ ...DEFAULT_RUN_OPTIONS, failOn: "minor", destructiveTerms: "pay", approvalTimeout: "60" })).toEqual({});
    expect(runOptionsBody({ ...DEFAULT_RUN_OPTIONS, askOnFailure: true, approvalTimeout: "600" })).toEqual({ ask_on_failure: true });
  });

  it("fills the form from a run's settings and sends every field back for a re-run", () => {
    const form = formFromSettings({
      environment: "staging",
      base_url: null,
      mode: "pure",
      egress: "local",
      headed: true,
      confirm_destructive: true,
      destructive_terms: ["wipe", "pay"],
      ask_on_failure: false,
      approval_timeout_s: 45,
      plan_review: null,
      strict: false,
      a11y: true,
      visual: false,
      fail_on: "serious",
    });
    expect(form).toEqual({
      environment: "staging",
      baseUrl: "",
      mode: "pure",
      egress: "local",
      headed: true,
      options: { a11y: true, visual: false, failOn: "serious", confirmDestructive: true, destructiveTerms: "wipe, pay", askOnFailure: false, approvalTimeout: "45", planReview: "", strict: false },
    });
    expect(rerunOptionsBody(form)).toEqual({
      environment: "staging",
      base_url: null,
      mode: "pure",
      egress: "local",
      headed: true,
      a11y: true,
      visual: false,
      fail_on: "serious",
      confirm_destructive: true,
      destructive_terms: ["wipe", "pay"],
      ask_on_failure: false,
      approval_timeout_s: 45,
      plan_review: null,
      strict: false,
    });
  });

  it("clears the options of checks that are off in a re-run", () => {
    expect(formFromSettings(null)).toEqual(DEFAULT_RUN_FORM);
    const off = { ...DEFAULT_RUN_FORM, baseUrl: " http://127.0.0.1:3000 ", options: { ...DEFAULT_RUN_OPTIONS, failOn: "minor" as const, destructiveTerms: "pay", approvalTimeout: "60" } };
    expect(rerunOptionsBody(off)).toMatchObject({ base_url: "http://127.0.0.1:3000", egress: null, environment: null, fail_on: null, destructive_terms: null, approval_timeout_s: null, a11y: false });
  });
});
