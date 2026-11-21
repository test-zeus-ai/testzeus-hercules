import type { Plan, PlanView } from "../lib/plan";

/** A run's plan after it ran: an inferred criterion that drove a recovery, an author outcome, a lint warning, an amendment. */
export function samplePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    case_id: "records",
    case_hash: "a".repeat(64),
    prompt_version: "case-7",
    model: "local-model",
    source: "model",
    source_steps: [
      { number: 1, action: 'Click the "New" button' },
      { number: 2, action: 'Enter "Acme" in "Record Name"', expected: 'I should see "was created"' },
    ],
    steps: [
      {
        id: "1.1",
        step: 1,
        kind: "click",
        text: 'click "New" button',
        target: "New",
        risk: { flags: [], reason: null },
        criteria: [{ id: "1.1.c1", text: 'the "New Record" dialog is open', origin: "inferred", check: "element_state", target: "New Record", state: "open" }],
      },
      { id: "2.1", step: 2, kind: "fill", text: 'fill "Record Name" with "Acme"', target: "Record Name", value: "Acme", risk: { flags: ["irreversible"], reason: "saves the record" }, criteria: [] },
      { id: "2.2", step: 2, item: "expected", kind: "verify", text: 'check: I should see "was created"', criteria: [{ id: "2.2.c1", text: 'the page shows "was created"', origin: "author", check: "text_visible", expected: "was created", outcome: true }] },
    ],
    coverage: { checked: true, engine: "jev", steps: 2, gaps: [], repaired: [] },
    lint: [{ severity: "warning", code: "suspicious_value", step: 2, message: 'the value "Acme" looks cut off', suggestion: 'write "Acme Corp"', source: "model" }],
    review: { state: "generated" },
    timing: { total_ms: 2300 },
    amendments: [{ step: 1, plan_step: "1.1", kind: "recovery", before: null, after: 'click button "New"', reason: 'expected: the "New Record" dialog is open (not met: open dialog(s): none)', source: "model" }],
    results: [
      { criterion: "1.1.c1", plan_step: "1.1", step: 1, origin: "inferred", check: "element_state", text: 'the "New Record" dialog is open', status: "passed", actual: "open dialog(s): 'New Record'", recovered: true },
      { criterion: "2.2.c1", plan_step: "2.2", step: 2, origin: "author", check: "text_visible", text: 'the page shows "was created"', status: "failed", actual: "not found" },
    ],
    ...overrides,
  };
}

export function sampleView(plan: Plan = samplePlan({ results: [], amendments: [] })): PlanView {
  return { plan, lint_errors: 0, lint_warnings: 1, approved: false, summary: "" };
}
