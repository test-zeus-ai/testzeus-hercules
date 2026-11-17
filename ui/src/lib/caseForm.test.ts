import { describe, expect, it } from "vitest";
import { emptyForm, errorLine, fromForm, savedNamesIn, toForm, validateCaseForm, environmentTag, gherkinUnquoted, quoteGherkinPlaceholders, quotePlaceholders, unquotedPlaceholders, withEnvironmentTag, type CaseFormValue } from "./caseForm";
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

describe("withEnvironmentTag", () => {
  it("adds the tag to the tag line above Feature, or above Feature itself", () => {
    expect(withEnvironmentTag('@smoke\nFeature: Sign in\n  Scenario: x\n', "shop")).toBe('@smoke @env:shop\nFeature: Sign in\n  Scenario: x\n');
    expect(withEnvironmentTag("# notes\nFeature: Sign in\n", "shop")).toBe("# notes\n@env:shop\nFeature: Sign in\n");
    expect(environmentTag("@smoke @env:shop\nFeature: x")).toBe("shop");
    expect(environmentTag("Feature: x")).toBeNull();
  });

  it("replaces an existing tag and drops repeats", () => {
    expect(withEnvironmentTag("@env:old @smoke @env:older\nFeature: x\n", "shop")).toBe("@env:shop @smoke\nFeature: x\n");
  });
});

describe("unquoted placeholders", () => {
  it("finds a placeholder outside quotes, with the rest of its word", () => {
    expect(unquotedPlaceholders("I open {{url}}")).toEqual(["{{url}}"]);
    expect(unquotedPlaceholders('I fill "{{name}}" in "Name"')).toEqual([]);
    expect(unquotedPlaceholders("I open https://shop.example/{{path}}/cart")).toEqual(["https://shop.example/{{path}}/cart"]);
    expect(unquotedPlaceholders('I see "Hello, {{name}}!" and {{run_id}}')).toEqual(["{{run_id}}"]);
  });

  it("quotes them", () => {
    expect(quotePlaceholders('I fill {{name}} in "Email"')).toBe('I fill "{{name}}" in "Email"');
    expect(quotePlaceholders("I open {{url}} then {{next}}")).toBe('I open "{{url}}" then "{{next}}"');
  });

  it("checks Gherkin step lines only, not tags, titles or doc strings", () => {
    const gherkin = ['@env:{{x}}', "Feature: {{title}}", "  Scenario: s", "    Given I open {{url}}", '    When I send a POST request to "/pets" with the JSON body:', '      """', '      {"name": {{pet}}}', '      """', '    Then I should see "{{name}}"'].join("\n");
    expect(gherkinUnquoted(gherkin)).toEqual([{ line: 4, tokens: ["{{url}}"] }]);
    expect(quoteGherkinPlaceholders(gherkin).split("\n")[3]).toBe('    Given I open "{{url}}"');
    expect(quoteGherkinPlaceholders(gherkin).split("\n")[6]).toBe('      {"name": {{pet}}}');
  });
});

describe("saved values", () => {
  it("knows the names a step saves, for the steps after it only", () => {
    expect(savedNamesIn('When I save the response JSON "$.code" as "order_code"')).toEqual(["order_code"]);
    expect(savedNamesIn("store the header ETag as {{etag}}")).toEqual(["etag"]);
    expect(savedNamesIn('Click "Save as draft"')).toEqual([]);
    const steps = [
      { action: 'I send a GET request to "/api/orders/{{order_code}}"', expected: "" },
      { action: 'I save the response JSON "$.code" as "order_code"', expected: "" },
      { action: 'I send a GET request to "/api/orders/{{order_code}}"', expected: "" },
    ];
    const errors = validateCaseForm({ ...valid(), steps });
    expect(errors["steps.0.placeholders"]).toMatch(/Unknown placeholder \{\{order_code\}\}/);
    expect(errors["steps.2.placeholders"]).toBeUndefined();
  });
});
