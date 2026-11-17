import { describe, expect, it } from "vitest";
import { gherkinTokens } from "./gherkinTokens";

const KINDS = new Map([
  ["user", "data"],
  ["password", "env"],
  ["run_id", "builtin"],
] as const);

describe("gherkinTokens", () => {
  it("marks environment, test data, built-in and unknown placeholders", () => {
    const tokens = gherkinTokens('When I fill "{{user}}" and "{{password}}" for {{run_id}} {{nope}}', KINDS);
    expect(tokens.filter((token) => token.kind && token.kind !== "keyword").map((token) => [token.text, token.kind])).toEqual([
      ["{{user}}", "data"],
      ["{{password}}", "env"],
      ["{{run_id}}", "builtin"],
      ["{{nope}}", "unknown"],
    ]);
    expect(tokens.map((token) => token.text).join("")).toBe('When I fill "{{user}}" and "{{password}}" for {{run_id}} {{nope}}');
  });

  it("colours @env tags like inputs and TestZeus references by their kind", () => {
    const tokens = gherkinTokens("@smoke @env:shop\n[$.environment.current.bundle_id][$.input.a] as [$.output.b] [$.testdata.acct.name]", KINDS);
    expect(tokens.filter((token) => token.kind).map((token) => token.kind)).toEqual(["input", "env", "input", "output", "data"]);
  });

  it("colours Gherkin keywords at the start of a line, not inside text or doc strings", () => {
    const text = 'Feature: Sign in\n  Background:\n    Given I open "/login"\n  Scenario Outline: x\n    When I see "Given"\n    * I wait\n    And I send:\n      """\n      Then not a keyword\n      """\n    Thenceforth x';
    const keywords = gherkinTokens(text, KINDS).filter((token) => token.kind === "keyword").map((token) => token.text);
    expect(keywords).toEqual(["Feature:", "Background:", "Given", "Scenario Outline:", "When", "*", "And"]);
    expect(gherkinTokens(text, KINDS).map((token) => token.text).join("")).toBe(text);
  });
});
