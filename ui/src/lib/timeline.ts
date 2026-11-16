// The live step timeline: a pure reducer over the run's server-sent events.
//
// Events carry a sequence number. Reconnecting EventSources replay events after the last
// id they saw, and a page may also seed the timeline from a stored event list, so the
// reducer ignores anything at or below the last applied sequence number: replaying the
// same events twice gives the same state.

import { FINAL_STATUSES, type RunEvent, type RunResult } from "./types";

export type StepPhase = "pending" | "running" | "recovering" | "passed" | "failed" | "error" | "skipped" | "blocked" | "unverified";

export interface TimelineStep {
  index: number;
  action: string;
  expected: string | null;
  actual: string | null;
  status: StepPhase;
  durationMs: number | null;
  decisions: number;
  actions: number;
  attempts: number;
  screenshots: string[];
  evidence: string[];
  /** When the step ran (1-based) when that differs from its index; null in the written order. */
  sequence: number | null;
  notes: string[];
}

export interface Screenshot {
  step: number;
  ref: string;
  seq: number;
}

export interface ApprovalRequest {
  id: string | null;
  kind: string;
  step: number | null;
  reason: string;
  action: string | null;
  target: string | null;
  screenshot: string | null;
  allowEdit: boolean;
  timeoutS: number | null;
}

export interface PausePoint {
  step: number | null;
  where: string | null;
  action: string | null;
}

/** Debug state of a run: where it is paused, the question it waits on, and the last control feedback. */
export interface ControlState {
  paused: PausePoint | null;
  approval: ApprovalRequest | null;
  lastInput: { ok: boolean; detail: string } | null;
  notice: string | null;
  resumedFrom: number | null;
}

export interface TimelineState {
  lastSeq: number;
  runStatus: string | null;
  reason: string | null;
  totalSteps: number | null;
  currentStep: number | null;
  steps: Readonly<Record<number, TimelineStep>>;
  latestScreenshot: Screenshot | null;
  finished: boolean;
  errors: string[];
  control: ControlState;
}

export interface PlannedStep {
  action: string;
  expected?: string | null;
}

const STEP_STATUSES: ReadonlySet<string> = new Set(["passed", "failed", "error", "skipped", "blocked", "unverified"]);

function blankStep(index: number, planned?: PlannedStep): TimelineStep {
  return {
    index,
    action: planned?.action ?? `Step ${index}`,
    expected: planned?.expected ?? null,
    actual: null,
    status: "pending",
    durationMs: null,
    decisions: 0,
    actions: 0,
    attempts: 0,
    screenshots: [],
    evidence: [],
    sequence: null,
    notes: [],
  };
}

export function initialTimeline(plan: readonly PlannedStep[] = []): TimelineState {
  const steps: Record<number, TimelineStep> = {};
  plan.forEach((planned, offset) => {
    steps[offset + 1] = blankStep(offset + 1, planned);
  });
  return {
    lastSeq: 0,
    runStatus: null,
    reason: null,
    totalSteps: plan.length || null,
    currentStep: null,
    steps,
    latestScreenshot: null,
    finished: false,
    errors: [],
    control: initialControl(),
  };
}

export function initialControl(): ControlState {
  return { paused: null, approval: null, lastInput: null, notice: null, resumedFrom: null };
}

const DECISION_NOTICES: Record<string, string> = { approve: "Approved", reject: "Rejected", edit: "Step edited; running it again" };

/** Control events (pause, approvals, take-over input) change the debug state, not the steps. */
function applyControlEvent(state: TimelineState, event: RunEvent): TimelineState {
  const { kind, data } = event;
  const control = state.control;
  switch (kind) {
    case "paused":
      return { ...state, control: { ...control, paused: { step: asNumber(data.step), where: asString(data.where), action: asString(data.action) } } };
    case "resumed":
      return { ...state, control: { ...control, paused: null } };
    case "needs_approval": {
      const approval: ApprovalRequest = {
        id: asString(data.id),
        kind: asString(data.kind) ?? "question",
        step: asNumber(data.step),
        reason: asString(data.reason) ?? "",
        action: asString(data.action),
        target: asString(data.target),
        screenshot: asString(data.screenshot),
        allowEdit: data.allow_edit !== false,
        timeoutS: asNumber(data.timeout_s),
      };
      const shot = approval.screenshot && approval.step ? { step: approval.step, ref: approval.screenshot, seq: event.seq } : state.latestScreenshot;
      return { ...state, latestScreenshot: shot, control: { ...control, approval } };
    }
    case "approval_result": {
      const decision = asString(data.decision) ?? "";
      const notice = data.timed_out === true ? "No answer in time; the action was not performed" : (DECISION_NOTICES[decision] ?? null);
      return { ...state, control: { ...control, approval: null, paused: data.state === "paused" ? control.paused : null, notice } };
    }
    case "input_result":
      return { ...state, control: { ...control, lastInput: { ok: data.ok === true, detail: asString(data.detail) ?? "" } } };
    case "control_ignored":
      return { ...state, control: { ...control, notice: asString(data.reason) } };
    case "run_resumed":
      return { ...state, control: { ...control, resumedFrom: asNumber(data.from_step) } };
    default:
      return state;
  }
}

const CONTROL_KINDS: ReadonlySet<string> = new Set(["paused", "resumed", "needs_approval", "approval_result", "input_result", "control_ignored", "run_resumed"]);

const asNumber = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const asString = (value: unknown): string | null => (typeof value === "string" ? value : null);
const asStrings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);

function withStep(state: TimelineState, index: number, change: (step: TimelineStep) => TimelineStep): TimelineState {
  const current = state.steps[index] ?? blankStep(index);
  return { ...state, steps: { ...state.steps, [index]: change(current) } };
}

function applyStepEvent(state: TimelineState, event: RunEvent, index: number): TimelineState {
  const { kind, data } = event;
  switch (kind) {
    case "step_started": {
      const action = asString(data.action);
      const retry = data.retry === true || state.steps[index]?.status === "failed" || state.steps[index]?.status === "error";
      const sequence = asNumber(data.sequence);
      const next = withStep(state, index, (step) => ({
        ...step,
        action: action ?? step.action,
        status: "running",
        attempts: retry ? step.attempts + 1 : step.attempts,
        sequence: sequence !== null && sequence !== index ? sequence : step.sequence,
      }));
      return { ...next, currentStep: index };
    }
    case "step_deferred": {
      const after = asNumber(data.after);
      const note = after !== null ? `runs again after step ${after}, whose field it depends on` : null;
      return withStep(state, index, (step) => ({ ...step, status: "pending", notes: note && !step.notes.includes(note) ? [...step.notes, note] : step.notes }));
    }
    case "step_recovering":
      return withStep(state, index, (step) => ({ ...step, status: "recovering", attempts: Math.max(step.attempts, asNumber(data.attempt) ?? step.attempts + 1) }));
    case "decision":
      return withStep(state, index, (step) => ({ ...step, decisions: step.decisions + 1 }));
    case "action":
      return withStep(state, index, (step) => ({ ...step, actions: step.actions + 1 }));
    case "security_scan": {
      // A security step's scan finished: its counts as a note on the step while the run goes on.
      const findings = asNumber(data.findings) ?? 0;
      const failing = asNumber(data.failing) ?? 0;
      const gate = asString(data.fail_on) ?? "medium";
      const note = `security scan: ${findings} finding${findings === 1 ? "" : "s"}, ${failing} at ${gate} or above`;
      return withStep(state, index, (step) => ({ ...step, notes: step.notes.includes(note) ? step.notes : [...step.notes, note] }));
    }
    case "screenshot": {
      const images = asStrings(data.evidence);
      if (images.length === 0) return state;
      const next = withStep(state, index, (step) => ({ ...step, screenshots: images }));
      const preferred = images.find((ref) => ref.endsWith("som.jpg")) ?? images[0];
      return { ...next, latestScreenshot: { step: index, ref: preferred, seq: event.seq } };
    }
    case "step_finished": {
      const status = asString(data.status);
      const phase: StepPhase = status && STEP_STATUSES.has(status) ? (status as StepPhase) : "error";
      return withStep(state, index, (step) => ({ ...step, status: phase, durationMs: asNumber(data.duration_ms) ?? step.durationMs }));
    }
    default:
      return state;
  }
}

/** The timeline after one event; events at or below `lastSeq` are ignored (replays). */
export function applyEvent(state: TimelineState, event: RunEvent): TimelineState {
  if (!Number.isFinite(event.seq) || event.seq <= state.lastSeq) return state;
  const data = event.data ?? {};
  let next: TimelineState = state;
  const step = asNumber(data.step);
  if (CONTROL_KINDS.has(event.kind)) {
    next = applyControlEvent(state, { ...event, data });
  } else if (step !== null && step >= 1) {
    next = applyStepEvent(state, { ...event, data }, step);
  } else if (event.kind === "run_started") {
    next = { ...state, totalSteps: asNumber(data.steps) ?? state.totalSteps };
  } else if (event.kind === "status") {
    const status = asString(data.status);
    const finished = status !== null && FINAL_STATUSES.has(status);
    next = { ...state, runStatus: status ?? state.runStatus, reason: asString(data.reason) ?? (finished ? state.reason : null), finished: state.finished || finished };
    if (status === "running") {
      next = { ...next, control: { ...next.control, paused: null, approval: null } };
    }
    if (finished) {
      next = { ...next, currentStep: null, steps: settleRunning(next.steps), control: { ...next.control, paused: null, approval: null } };
    }
  } else if (event.kind === "error") {
    const message = asString(data.message);
    next = message ? { ...state, errors: [...state.errors, message] } : state;
  }
  return { ...next, lastSeq: event.seq };
}

/** When the run ends, a step still running was interrupted and a step never started did not run. */
function settleRunning(steps: Readonly<Record<number, TimelineStep>>): Record<number, TimelineStep> {
  const settled: Record<number, TimelineStep> = {};
  for (const [key, step] of Object.entries(steps)) {
    const status: StepPhase = step.status === "running" || step.status === "recovering" ? "error" : step.status === "pending" ? "skipped" : step.status;
    settled[Number(key)] = status === step.status ? step : { ...step, status };
  }
  return settled;
}

export function replay(events: readonly RunEvent[], plan: readonly PlannedStep[] = []): TimelineState {
  return events.reduce(applyEvent, initialTimeline(plan));
}

/** Fold the final RunResult (run.json) in: it is the source of truth for statuses, expected and actual. */
export function mergeResult(state: TimelineState, result: RunResult): TimelineState {
  let next = state;
  for (const step of result.steps) {
    next = withStep(next, step.index, (current) => ({
      ...current,
      action: step.action || current.action,
      expected: step.expected ?? current.expected,
      actual: step.actual ?? null,
      status: step.status,
      durationMs: step.duration_ms,
      decisions: Math.max(current.decisions, step.decisions),
      evidence: step.evidence,
      screenshots: step.evidence.filter((ref) => /\.(jpe?g|png|webp)$/i.test(ref)),
      sequence: step.sequence ?? null,
      notes: step.notes ?? [],
    }));
  }
  return { ...next, totalSteps: Math.max(next.totalSteps ?? 0, result.steps.length) || null, finished: true, runStatus: result.status, currentStep: null };
}

/** What to call a step in lists: verification steps are named by what they check. */
export function stepLabel(step: { action: string; expected?: string | null }): string {
  return step.action.trim().toLowerCase() === "verify" && step.expected ? step.expected : step.action;
}

/** Steps 1..n in order, including planned steps that have not started yet. */
export function orderedSteps(state: TimelineState): TimelineStep[] {
  const known = Object.keys(state.steps).map(Number);
  const last = Math.max(state.totalSteps ?? 0, ...known, 0);
  const list: TimelineStep[] = [];
  for (let index = 1; index <= last; index += 1) {
    list.push(state.steps[index] ?? blankStep(index));
  }
  return list;
}
