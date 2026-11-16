import { describe, expect, it } from "vitest";
import { emptyForm, errorLine, fromForm, toForm, validateCaseForm, type CaseFormValue } from "./caseForm";
import type { TestCase } from "./types";

const valid = (): CaseFormValue => ({
  ...emptyForm(),
  id: "login-ok",
  title: "Sign in works",
  baseUrl: "http://127.0.0.1:8000",
  steps: [
    { action: 'Open "/login.html"', expected: "" },
    { action: 'Fill "{{password}}" in "Password"', expected: "" },
  ],
  testData: [{ key: "password", value: "SHOP_PASSWORD", secret: true }],
});

describe("case form validation", () => {
  it("accepts a complete case", () => {
    expect(validateCaseForm(valid())).toEqual({});
  });

  it("names each invalid field", () => {
    const errors = validateCaseForm({ ...valid(), id: "Login OK", title: " ", baseUrl: "localhost:3000", viewport: "1280*800", steps: [{ action: "", expected: "" }] });
    expect(Object.keys(errors).sort()).toEqual(["baseUrl", "id", "steps.0.action", "title", "viewport"]);
  });

  it("requires at least one step", () => {
    expect(validateCaseForm({ ...valid(), steps: [] }).steps).toMatch(/at least one step/);
  });

  it("checks test data names, duplicates, reserved names and secret references", () => {
    const errors = validateCaseForm({
      ...valid(),
      testData: [
        { key: "password", value: "lower_case", secret: true },
        { key: "password", value: "x", secret: false },
        { key: "run_id", value: "x", secret: false },
        { key: "1bad", value: "x", secret: false },
      ],
    });
    expect(errors["testData.0.value"]).toMatch(/environment variable/);
    expect(errors["testData.1.key"]).toMatch(/twice/);
    expect(errors["testData.2.key"]).toMatch(/built-in/);
    expect(errors["testData.3.key"]).toBeDefined();
  });

  it("flags placeholders that no test data defines", () => {
    const errors = validateCaseForm({ ...valid(), testData: [] });
    expect(errors["steps.1.placeholders"]).toMatch(/\{\{password\}\}/);
    expect(validateCaseForm({ ...valid(), steps: [{ action: "Search {{run_id}}", expected: "" }], testData: [] })).toEqual({});
  });
});

describe("case form conversion", () => {
  it("round-trips a TestCase and keeps fields the form does not edit", () => {
    const testCase: TestCase = {
      id: "checkout",
      title: "Checkout",
      description: "Guest checkout",
      priority: "high",
      tags: ["smoke", "cart"],
      environment: { ref: "staging", viewport: "1280x800" },
      test_data: { email: "a@example.com", card: { secret: "CARD_NUMBER" } },
      steps: [{ action: "Pay", expected: "Order placed", semantic: true }, { action: "Done" }],
      timeout_s: 600,
      status: "ready",
    };
    const back = fromForm(toForm(testCase), testCase);
    expect(back).toMatchObject({ ...testCase, environment: { ref: "staging", viewport: "1280x800" } });
    expect(back.steps[0]).toEqual({ action: "Pay", expected: "Order placed", semantic: true });
    expect(back.steps[1]).toEqual({ action: "Done" });
  });

  it("trims values and drops empty optional fields", () => {
    const form = { ...valid(), tags: " a, ,b ", viewport: "  ", steps: [{ action: "  Go  ", expected: "  " }], testData: [] };
    const testCase = fromForm(form);
    expect(testCase.tags).toEqual(["a", "b"]);
    expect(testCase.environment).toEqual({ base_url: "http://127.0.0.1:8000" });
    expect(testCase.steps).toEqual([{ action: "Go" }]);
  });

  it("reads the line number from a daemon error", () => {
    expect(errorLine("<gherkin>, line 7: unknown step keyword")).toBe(7);
    expect(errorLine("<gherkin>:4: expected a step (Given/When/Then/And/But/*), got 'Clicks'")).toBe(4);
    expect(errorLine("steps.1.action: field required")).toBeNull();
    expect(errorLine(null)).toBeNull();
  });
});
