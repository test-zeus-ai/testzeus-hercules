import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { DecisionView } from "../lib/types";
import { DecisionInspector, engineLabel } from "./DecisionInspector";

function decision(overrides: Partial<DecisionView> = {}): DecisionView {
  return {
    purpose: "target",
    attempt: 0,
    engine: "local-llm",
    model: "local-model-4b",
    latency_ms: 2977,
    wall_ms: 2980,
    cost_usd: 0,
    cached: false,
    choice: "md_11",
    confidence: 0.93,
    rows: 11,
    prompt_tokens_est: 585,
    fallback: null,
    answered_by: null,
    calls: [],
    page: { title: "Classic Tee", url: "http://127.0.0.1:8000/product.html" },
    step: { kind: "click", role: "button", target: "Add to cart", text: 'I click the "Add to cart" button' },
    questions: [
      {
        id: "target",
        type: "choice",
        instructions: "Which listed element should the test step act on?",
        options: [
          { id: "md_11", label: 'button "Add to cart"', probability: 0.93 },
          { id: "md_8", label: 'button "Open cart"', probability: 0.05 },
          { id: "no_match", label: "None of the listed elements", probability: 0.02 },
        ],
        answer: "md_11",
        confidence: 0.93,
      },
      {
        id: "target_absent",
        type: "noul",
        instructions: "The element is not among the listed elements.",
        options: [
          { id: "true", label: "No listed element fits the step.", probability: 0.03 },
          { id: "false", label: "One of the listed elements fits the step.", probability: 0.97 },
        ],
        answer: "false",
        confidence: 0.97,
      },
    ],
    ...overrides,
  };
}

describe("DecisionInspector", () => {
  it("shows every option with its probability and marks the chosen answer", () => {
    render(<DecisionInspector decisions={[decision()]} />);
    const chosen = screen.getByTestId("option-target-md_11");
    expect(chosen.className).toContain("option-chosen");
    expect(within(chosen).getByText("(chosen)")).toBeTruthy();
    expect(within(chosen).getByRole("meter").getAttribute("aria-valuenow")).toBe("93");
    expect(within(chosen).getByText("93%")).toBeTruthy();
    const other = screen.getByTestId("option-target-md_8");
    expect(other.className).not.toContain("option-chosen");
    expect(within(other).getByText("5%")).toBeTruthy();
    expect(screen.getByTestId("option-target_absent-false").className).toContain("option-chosen");
  });

  it("shows engine, model, latency and cost", () => {
    render(<DecisionInspector decisions={[decision()]} />);
    expect(screen.getByText("Local model")).toBeTruthy();
    expect(screen.getByText("local-model-4b")).toBeTruthy();
    expect(screen.getByText("3.0 s")).toBeTruthy();
    expect(screen.getByText("$0")).toBeTruthy();
    expect(screen.getByText("Which element to act on", { exact: false })).toBeTruthy();
  });

  it("shows the fallback reason and each call in hybrid mode", () => {
    const hybrid = decision({
      engine: "local-llm",
      answered_by: "local-llm",
      fallback: "DecisionError: JEV timed out",
      calls: [
        { engine: "jev", images: false, latency_ms: 30000, error: "DecisionError: JEV timed out" },
        { engine: "local-llm", images: true, latency_ms: 2100, confidence: 0.9 },
      ],
    });
    render(<DecisionInspector decisions={[hybrid]} />);
    expect(screen.getByRole("note").textContent).toContain("JEV timed out");
    const calls = screen.getByRole("list", { name: "Calls made for this decision" });
    expect(within(calls).getAllByRole("listitem")).toHaveLength(2);
    expect(within(calls).getByText(/with screenshot/)).toBeTruthy();
  });

  it("collapses long option lists and expands them on request", () => {
    const options = Array.from({ length: 9 }, (_, index) => ({ id: `md_${index + 1}`, label: `link ${index + 1}`, probability: index === 0 ? 0.9 : 0.0125 }));
    render(<DecisionInspector decisions={[decision({ questions: [{ id: "target", type: "choice", instructions: null, options, answer: "md_1", confidence: 0.9 }] })]} />);
    expect(screen.getAllByRole("meter")).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: "Show all 9 options" }));
    expect(screen.getAllByRole("meter")).toHaveLength(9);
  });

  it("explains steps without decisions", () => {
    render(<DecisionInspector decisions={[]} />);
    expect(screen.getByText(/No model decisions in this step/)).toBeTruthy();
  });

  it("labels engines for people", () => {
    expect(engineLabel("jev")).toBe("JEV");
    expect(engineLabel("jev+local-llm")).toBe("JEV → Local model");
    expect(engineLabel("clef")).toBe("Clef (local)");
    expect(engineLabel(null)).toBe("Unknown engine");
  });
});
