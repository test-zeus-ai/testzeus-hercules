import { describe, expect, it } from "vitest";
import { applyEvent, initialTimeline, mergeResult, orderedSteps, replay, stepLabel } from "./timeline";
import type { RunEvent, RunResult } from "./types";

const RUN = "r_20261026_101500_abc123";
const shot = (step: number, name: string) => `hercules://runs/${RUN}/steps/${step}/${name}`;

// What the daemon streams for a three-step run whose step 3 fails after one retry.
const EVENTS: RunEvent[] = [
  { seq: 1, kind: "status", data: { status: "queued" } },
  { seq: 2, kind: "status", data: { status: "running" } },
  { seq: 3, kind: "run_started", data: { run_id: RUN, case_id: "login", steps: 3 } },
  { seq: 4, kind: "step_started", data: { step: 1, action: 'I open "/login.html"' } },
  { seq: 5, kind: "step_finished", data: { step: 1, status: "passed", duration_ms: 900 } },
  { seq: 6, kind: "step_started", data: { step: 2, action: 'I click the "Sign in" button' } },
  { seq: 7, kind: "screenshot", data: { step: 2, evidence: [shot(2, "plain.jpg"), shot(2, "som.jpg")] } },
  { seq: 8, kind: "decision", data: { step: 2, purpose: "target", engine: "local-llm", choice: "md_4", confidence: 0.97 } },
  { seq: 9, kind: "action", data: { step: 2, action: "click", ok: true } },
  { seq: 10, kind: "step_finished", data: { step: 2, status: "passed", duration_ms: 3100 } },
  { seq: 11, kind: "step_started", data: { step: 3, action: 'I should see "Welcome"' } },
  { seq: 12, kind: "step_recovering", data: { step: 3, attempt: 1, reason: "TargetNotFound" } },
  { seq: 13, kind: "screenshot", data: { step: 3, evidence: [shot(3, "som.jpg")] } },
  { seq: 14, kind: "step_finished", data: { step: 3, status: "failed", duration_ms: 5200 } },
  { seq: 15, kind: "run_finished", data: { run_id: RUN, status: "failed" } },
  { seq: 16, kind: "status", data: { status: "failed" } },
];

describe("timeline reducer", () => {
  it("replays a run into per-step statuses, counts and the latest screenshot", () => {
    const state = replay(EVENTS);
    const steps = orderedSteps(state);
    expect(steps.map((step) => step.status)).toEqual(["passed", "passed", "failed"]);
    expect(steps[1]).toMatchObject({ action: 'I click the "Sign in" button', decisions: 1, actions: 1, durationMs: 3100 });
    expect(steps[2].attempts).toBe(1);
    expect(state.latestScreenshot).toEqual({ step: 3, ref: shot(3, "som.jpg"), seq: 13 });
    expect(state).toMatchObject({ lastSeq: 16, runStatus: "failed", finished: true, totalSteps: 3, currentStep: null });
  });

  it("highlights the running step while the run is in progress", () => {
    const state = replay(EVENTS.slice(0, 7));
    expect(state.currentStep).toBe(2);
    expect(state.steps[2].status).toBe("running");
    expect(state.latestScreenshot?.ref).toBe(shot(2, "som.jpg"));
    expect(state.finished).toBe(false);
    expect(orderedSteps(state).map((step) => step.status)).toEqual(["passed", "running", "pending"]);
  });

  it("notes a security step's scan counts on the step", () => {
    const state = replay([
      { seq: 1, kind: "step_started", data: { step: 1, action: "Scan the app" } },
      { seq: 2, kind: "security_scan", data: { step: 1, findings: 12, failing: 1, fail_on: "high" } },
      { seq: 3, kind: "security_scan", data: { step: 1, findings: 12, failing: 1, fail_on: "high" } },
    ]);
    expect(state.steps[1].notes).toEqual(["security scan: 12 findings, 1 at high or above"]);
  });

  it("ignores events replayed after a reconnect (same result as one pass)", () => {
    const once = replay(EVENTS);
    // EventSource resumes after Last-Event-ID, but a page may also seed from the stored list.
    const twice = replay([...EVENTS.slice(0, 10), ...EVENTS.slice(5, 10), ...EVENTS.slice(10)]);
    expect(twice).toEqual(once);
    expect(twice.steps[2].decisions).toBe(1);
  });

  it("drops out-of-order older events and keeps going with newer ones", () => {
    let state = replay(EVENTS.slice(0, 10));
    state = applyEvent(state, { seq: 8, kind: "decision", data: { step: 2 } });
    expect(state.steps[2].decisions).toBe(1);
    state = applyEvent(state, EVENTS[10]);
    expect(state.currentStep).toBe(3);
  });

  it("creates a step it has not seen start (joined mid-run)", () => {
    const state = applyEvent(initialTimeline(), { seq: 40, kind: "step_finished", data: { step: 4, status: "passed", duration_ms: 12 } });
    expect(orderedSteps(state)).toHaveLength(4);
    expect(state.steps[4]).toMatchObject({ status: "passed", action: "Step 4" });
    expect(orderedSteps(state)[0].status).toBe("pending");
  });

  it("uses planned steps from the case before they start", () => {
    const state = initialTimeline([{ action: "Open the shop" }, { action: "Check the title", expected: "Demo Shop" }]);
    expect(orderedSteps(state).map((step) => [step.action, step.expected, step.status])).toEqual([
      ["Open the shop", null, "pending"],
      ["Check the title", "Demo Shop", "pending"],
    ]);
  });

  it("marks a step interrupted when the run ends while it runs (cancel)", () => {
    const state = replay([...EVENTS.slice(0, 11), { seq: 12, kind: "status", data: { status: "cancelled", reason: "cancelled by request" } }]);
    expect(state.steps[3].status).toBe("error");
    expect(orderedSteps(replay([{ seq: 1, kind: "status", data: { status: "cancelled" } }], [{ action: "a" }, { action: "b" }])).map((step) => step.status)).toEqual(["skipped", "skipped"]);
    expect(state).toMatchObject({ runStatus: "cancelled", reason: "cancelled by request", finished: true, currentStep: null });
  });

  it("names verification steps by what they check", () => {
    expect(stepLabel({ action: "verify", expected: 'I should see "Welcome"' })).toBe('I should see "Welcome"');
    expect(stepLabel({ action: "Click Save", expected: "Saved" })).toBe("Click Save");
    expect(stepLabel({ action: "verify", expected: null })).toBe("verify");
  });

  it("keeps worker errors and ignores unknown event kinds", () => {
    const state = replay([
      { seq: 1, kind: "error", data: { message: "the model server is not reachable" } },
      { seq: 2, kind: "something_new", data: { value: 1 } },
    ]);
    expect(state.errors).toEqual(["the model server is not reachable"]);
    expect(state.lastSeq).toBe(2);
  });

  it("takes expected and actual from the final RunResult", () => {
    const result = {
      run_id: RUN,
      status: "failed",
      case_id: "login",
      steps: [
        { index: 1, action: 'I open "/login.html"', status: "passed", duration_ms: 900, decisions: 0, evidence: [] },
        { index: 2, action: 'I click the "Sign in" button', status: "passed", duration_ms: 3100, decisions: 1, evidence: [shot(2, "som.jpg"), shot(2, "decisions.jsonl")] },
        { index: 3, action: 'I should see "Welcome"', status: "failed", expected: 'I should see "Welcome"', actual: "text not found", duration_ms: 5200, decisions: 0, evidence: [shot(3, "plain.jpg")] },
      ],
    } as unknown as RunResult;
    const state = mergeResult(replay(EVENTS.slice(0, 12)), result);
    expect(state.steps[3]).toMatchObject({ status: "failed", actual: "text not found", expected: 'I should see "Welcome"' });
    expect(state.steps[2].screenshots).toEqual([shot(2, "som.jpg")]);
    expect(state.finished).toBe(true);
  });

  it("shows when a step ran out of the written order, and why", () => {
    const state = replay([
      { seq: 1, kind: "run_started", data: { steps: 3 } },
      { seq: 2, kind: "step_started", data: { step: 1, action: "open the form" } },
      { seq: 3, kind: "step_finished", data: { step: 1, status: "passed", duration_ms: 10 } },
      { seq: 4, kind: "step_started", data: { step: 2, action: 'I select "Ontario" from "Region"' } },
      { seq: 5, kind: "step_deferred", data: { step: 2, after: 3 } },
      { seq: 6, kind: "step_started", data: { step: 3, action: 'I select "Canada" from "Country"', sequence: 2 } },
      { seq: 7, kind: "step_finished", data: { step: 3, status: "passed", duration_ms: 10 } },
      { seq: 8, kind: "step_started", data: { step: 2, action: 'I select "Ontario" from "Region"', sequence: 3 } },
    ]);
    expect(state.steps[2]).toMatchObject({ status: "running", sequence: 3, notes: ["runs again after step 3, whose field it depends on"] });
    expect(state.steps[3]).toMatchObject({ status: "passed", sequence: 2 });
    expect(state.steps[1].sequence).toBeNull();
    const result = {
      run_id: RUN,
      status: "passed",
      case_id: "form",
      steps: [{ index: 2, action: "region", status: "passed", duration_ms: 1, decisions: 0, evidence: [], sequence: 3, notes: ["run after step 3"] }],
    } as unknown as RunResult;
    expect(mergeResult(state, result).steps[2]).toMatchObject({ sequence: 3, notes: ["run after step 3"] });
  });
});
