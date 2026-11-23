// Exploratory testing sessions (/api/explore): types, payload checks and the pure helpers the Explore screens use.

import type { Guard } from "./guards";
import { formatCost } from "./format";

export type Severity = "P0" | "P1" | "P2" | "P3";
export const SEVERITIES: readonly Severity[] = ["P0", "P1", "P2", "P3"];
export const MISSIONS = [
  { id: "p1", label: "P1 hunt", help: "Bugs at P1 or worse: lost or wrong data, money, another user's data, a blocked core flow." },
  { id: "forms", label: "Forms", help: "Every form with unusual, empty, long and malformed values." },
  { id: "changed", label: "What changed", help: "Only the routes or paths you list as changed." },
  { id: "smoke-plus", label: "Smoke plus", help: "A quick pass over every page with plain moves." },
  { id: "a11y", label: "Accessibility", help: "Keyboard-only and screen-reader use, and accessibility checks on every page." },
  { id: "conditions", label: "Bad conditions", help: "Narrow screens, offline moments, expired sessions." },
] as const;
export const LEVERS = ["inputs", "order", "conditions", "time", "people"] as const;
export const FINISHED = new Set(["finished", "failed", "stopped"]);

export interface Charter {
  id: string;
  title: string;
  kind: "setup" | "recon" | "explore" | "dig";
  area: string;
  levers: string[];
  persona?: string | null;
  timebox_s: number;
  priority: number;
  origin: "coordinator" | "person";
  pinned: boolean;
  status: "queued" | "running" | "done" | "vetoed" | "failed" | "stopped";
  run_id?: string | null;
  explorer?: number | null;
  notes: string[];
  dig_of?: string | null;
}

export interface TriageEvent {
  at: string;
  by: string;
  field: string;
  before?: string | null;
  after?: string | null;
  reason: string;
}

export interface Finding {
  id: string;
  title: string;
  signature: string;
  oracle: string;
  tier: string;
  proposed_severity: Severity;
  severity?: Severity | null;
  severity_reason?: string;
  category: string;
  steps: string[];
  expected: string;
  actual: string;
  reproduced_recorded: string;
  reproduced_interpreted: string;
  flaky: string;
  case_path?: string | null;
  /** What a person decided about the case: proposed (outside the suite), kept, regression or rejected. */
  case?: CaseDecision;
  run_ids: string[];
  evidence: string[];
  state: string;
  tracker?: string | null;
  duplicate_of?: string | null;
  audit: TriageEvent[];
  sessions: string[];
  regression: boolean;
  first_seen: string;
  last_seen: string;
}

export interface ExploreSpec {
  mission: string;
  prompt?: string | null;
  start_case?: string | null;
  environment?: string | null;
  base_url?: string | null;
  workers: number;
  reproducers: number;
  budget: { time_s: number; max_cost_usd?: number | null };
}

export interface ExploreEvent {
  seq: number;
  kind: string;
  data: Record<string, unknown>;
  at: number;
}

export interface SessionView {
  id: string;
  status: "starting" | "recon" | "exploring" | "reproducing" | "stopping" | "finished" | "failed" | "stopped";
  spec: ExploreSpec;
  phase_detail: string;
  workers: number;
  reproducers: number;
  size_changes: Array<{ at: string; by: string; workers?: number[]; reproducers?: number[] }>;
  started_at: string;
  finished_at?: string | null;
  elapsed_s: number;
  cost_usd: number;
  /** Part of cost_usd prices hosted model calls at explore.cost_per_decision_usd, since the provider billed no dollars. */
  cost_estimated?: boolean;
  charters: Charter[];
  clues: number;
  findings: Finding[];
  ux_notes: string[];
  coverage: { routes_seen?: string[]; states_seen?: number; moves_tried?: number; gaps?: Array<{ route: string; levers_not_used: string[] }>; charters_not_run?: string[] };
  children: string[];
  message: string;
  exit_code?: number | null;
  events?: ExploreEvent[];
  last_seq?: number;
}

export interface Preflight {
  problems: string[];
  /** The base URL does not look like a test environment: the tester confirms it is theirs. */
  host_problem?: string | null;
  estimate: { workers: number; reproducers: number; time_s: number; decisions: number; cost_usd: number; note: string };
}

export interface Precision {
  confirmed: number;
  dismissed: number;
  triaged: number;
  untriaged: number;
  precision: number | null;
  interval_90: [number, number] | null;
}

type Obj = Record<string, unknown>;
const isObject = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);

export const isSessionView: Guard<SessionView> = (value): value is SessionView =>
  isObject(value) && typeof value.id === "string" && typeof value.status === "string" && Array.isArray(value.charters) && Array.isArray(value.findings);

export const isSessionList: Guard<{ sessions: SessionView[] }> = (value): value is { sessions: SessionView[] } =>
  isObject(value) && Array.isArray(value.sessions) && value.sessions.every((item) => isObject(item) && typeof item.id === "string" && typeof item.status === "string");

export const isFindingList: Guard<{ findings: Finding[]; precision: Precision }> = (value): value is { findings: Finding[]; precision: Precision } =>
  isObject(value) && Array.isArray(value.findings) && value.findings.every((item) => isObject(item) && typeof item.id === "string" && typeof item.title === "string") && isObject(value.precision);

/** What the start form holds; the swarm size is the tester's and is sent as given. */
export interface StartForm {
  where: "base_url" | "environment" | "start_case";
  base_url: string;
  environment: string;
  start_case: string;
  mission: string;
  prompt: string;
  minutes: string;
  workers: string;
  reproducers: string;
  max_cost: string;
  levers: string[];
  deny_paths: string;
  changed: string;
  allow_more_workers: boolean;
  production_ok: boolean;
}

export const EMPTY_START: StartForm = {
  where: "base_url",
  base_url: "",
  environment: "",
  start_case: "",
  mission: "p1",
  prompt: "",
  minutes: "30",
  workers: "2",
  reproducers: "1",
  max_cost: "",
  levers: [...LEVERS],
  deny_paths: "",
  changed: "",
  allow_more_workers: false,
  production_ok: false,
};

/** The start form filled from an earlier session's spec, to run it again. */
export function formFromSpec(spec: Partial<ExploreSpec> & Record<string, unknown>): StartForm {
  const where: StartForm["where"] = spec.start_case ? "start_case" : spec.environment ? "environment" : "base_url";
  const scope = (spec.scope ?? {}) as { deny_paths?: string[] };
  const levers = Array.isArray(spec.levers) && spec.levers.length ? (spec.levers as string[]) : [...LEVERS];
  return {
    ...EMPTY_START,
    where,
    base_url: spec.base_url ?? "",
    environment: spec.environment ?? "",
    start_case: spec.start_case ?? "",
    mission: spec.mission ?? EMPTY_START.mission,
    prompt: spec.prompt ?? "",
    minutes: spec.budget?.time_s ? String(Math.round(spec.budget.time_s / 60)) : EMPTY_START.minutes,
    workers: spec.workers ? String(spec.workers) : EMPTY_START.workers,
    reproducers: spec.reproducers ? String(spec.reproducers) : EMPTY_START.reproducers,
    max_cost: spec.budget?.max_cost_usd ? String(spec.budget.max_cost_usd) : "",
    levers,
    deny_paths: (scope.deny_paths ?? []).join(", "),
    changed: Array.isArray(spec.changed_paths) ? (spec.changed_paths as string[]).join("\n") : "",
    production_ok: Boolean(spec.production_ok),
  };
}

/** True when the form's only problem is that nothing is filled in yet (shown as a hint, not an error). */
export function isUnfilled(form: StartForm): boolean {
  return !form[form.where].trim();
}

const list = (text: string) =>
  text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);

/** The start form as an ExploreSpec body, or the first problem with it (checked here so the tester sees it at once). */
export function specFromForm(form: StartForm): { spec: Record<string, unknown> } | { error: string } {
  const where = form[form.where].trim();
  if (!where) return { error: form.where === "base_url" ? "Give the app's base URL" : form.where === "environment" ? "Choose an environment" : "Choose a start case" };
  if (form.where === "base_url" && !/^https?:\/\/[^\s/?#]+/.test(where)) return { error: "The base URL starts with http:// or https://" };
  const minutes = Number(form.minutes);
  if (!Number.isFinite(minutes) || minutes < 1 || minutes > 1440) return { error: "Time is 1 to 1440 minutes" };
  const workers = Number(form.workers);
  const reproducers = Number(form.reproducers);
  if (!Number.isInteger(workers) || workers < 1 || workers > 64) return { error: "Explorers is a whole number from 1 to 64" };
  if (!Number.isInteger(reproducers) || reproducers < 1 || reproducers > 16) return { error: "Reproducers is a whole number from 1 to 16" };
  if (!form.levers.length) return { error: "Choose at least one lever" };
  const budget: Record<string, unknown> = { time_s: Math.round(minutes * 60) };
  if (form.max_cost.trim()) {
    const cost = Number(form.max_cost);
    if (!Number.isFinite(cost) || cost < 0) return { error: "The cost limit is a number of dollars" };
    budget.max_cost_usd = cost;
  }
  const spec: Record<string, unknown> = { [form.where]: where, mission: form.mission, workers, reproducers, budget, levers: form.levers };
  if (form.prompt.trim()) spec.prompt = form.prompt.trim();
  const deny = list(form.deny_paths);
  if (deny.some((path) => !path.startsWith("/"))) return { error: "Paths never to open start with /" };
  if (deny.length) spec.scope = { deny_paths: deny };
  const changed = list(form.changed);
  if (form.mission === "changed" && !changed.length) return { error: "List what changed (routes or paths) for this mission" };
  if (changed.length) spec.changed_paths = changed;
  if (form.allow_more_workers) spec.allow_more_workers = true;
  if (form.production_ok) spec.production_ok = true;
  return { spec };
}

/** Triage moves a person may make from each state (the daemon's ledger rules). */
export const TRANSITIONS: Record<string, readonly string[]> = {
  new: ["confirmed", "in_tracker", "duplicate", "by_design", "wont_fix", "cannot_reproduce", "known_issue"],
  confirmed: ["in_tracker", "duplicate", "by_design", "wont_fix", "known_issue", "verify_fixed"],
  in_tracker: ["verify_fixed", "wont_fix", "known_issue", "duplicate"],
  verify_fixed: ["closed", "confirmed"],
  closed: ["new"],
  duplicate: ["new"],
  by_design: ["new"],
  wont_fix: ["new"],
  known_issue: ["new", "confirmed"],
  cannot_reproduce: ["new"],
};
export const NEEDS_REASON = new Set(["by_design", "wont_fix", "duplicate", "cannot_reproduce"]);

export type CaseDecision = "proposed" | "kept" | "regression" | "rejected";
export const CASE_DECISIONS: readonly CaseDecision[] = ["proposed", "kept", "regression", "rejected"];
export const CASE_LABELS: Record<CaseDecision, string> = { proposed: "Proposed", kept: "Kept", regression: "Regression", rejected: "Rejected" };
/** What each case decision does, in the words the triage form shows. */
export const CASE_HELP: Record<CaseDecision, string> = {
  proposed: "Waits outside your cases until someone decides.",
  kept: "Added to your cases as a known bug: runs report it but never fail on it while the bug is open.",
  regression: "Added to your cases as a normal case: a run fails if the bug comes back.",
  rejected: "Never run and never proposed again; kept aside for the record.",
};
export const STATE_LABELS: Record<string, string> = {
  new: "New",
  confirmed: "Confirmed",
  in_tracker: "In tracker",
  verify_fixed: "Verify fix",
  closed: "Closed",
  duplicate: "Duplicate",
  by_design: "By design",
  wont_fix: "Won't fix",
  known_issue: "Known issue",
  cannot_reproduce: "Can't reproduce",
};

/** One decision a reviewer makes in a click: the triage it saves and what it does to the finding's case. */
export interface QuickDecision {
  id: string;
  label: string;
  state: string;
  /** What the reviewer is told the click does. */
  effect: string;
  needsReason: boolean;
  tone: "primary" | "plain";
}

/** The decisions a finding in this state is most likely to need, the first one being the usual next step. */
export function quickDecisions(state: string): QuickDecision[] {
  switch (state) {
    case "new":
      return [
        { id: "confirm", label: "Real bug", state: "confirmed", effect: "Confirms it and keeps its case in your cases as a known bug.", needsReason: false, tone: "primary" },
        { id: "by_design", label: "Not a bug", state: "by_design", effect: "Dismisses it as intended behaviour and rejects its case.", needsReason: true, tone: "plain" },
        { id: "cannot_reproduce", label: "Can't reproduce", state: "cannot_reproduce", effect: "Dismisses it and rejects its case.", needsReason: true, tone: "plain" },
        { id: "duplicate", label: "Duplicate", state: "duplicate", effect: "Marks it a duplicate of another finding and rejects its case.", needsReason: true, tone: "plain" },
      ];
    case "confirmed":
    case "in_tracker":
    case "known_issue":
      return [{ id: "fixed", label: "It's fixed", state: "verify_fixed", effect: "Asks for a check: `hercules explore reverify` runs the kept case.", needsReason: false, tone: "primary" }];
    case "verify_fixed":
      return [
        { id: "close", label: "Close as fixed", state: "closed", effect: "Closes it and turns its case into a regression test that fails if the bug returns.", needsReason: false, tone: "primary" },
        { id: "reopen", label: "Still broken", state: "confirmed", effect: "Moves it back to confirmed.", needsReason: false, tone: "plain" },
      ];
    default:
      return [{ id: "reopen", label: "Reopen", state: "new", effect: "Moves it back to new for another look.", needsReason: false, tone: "plain" }];
  }
}

/** The finding's observed problem in one short line, to tell findings with the same title apart. */
export function findingSummary(finding: Pick<Finding, "actual">, max = 140): string {
  const line = (finding.actual || "").split("\n").find((item) => item.trim()) ?? "";
  const text = line.trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Findings waiting for a person's first decision. */
export function toReview(findings: readonly Pick<Finding, "state">[]): number {
  return findings.filter((item) => item.state === "new").length;
}

/** A session's time as a person reads it: elapsed of the budget while it runs, the time it took once finished. */
export function sessionTime(view: Pick<SessionView, "elapsed_s" | "status" | "spec">, format: (seconds: number) => string): { text: string; title?: string } {
  const budget = view.spec?.budget?.time_s ?? 0;
  if (!FINISHED.has(view.status)) return { text: `${format(view.elapsed_s)} / ${format(budget)}` };
  const over = budget && view.elapsed_s > budget + 5;
  return { text: format(view.elapsed_s), title: over ? `Explored for ${format(budget)}; reproducing the last clues took the rest.` : `Time box ${format(budget)}.` };
}

/** A finding's severity as shown: a person's call, else the model's proposal (always marked as one). */
export function severityText(finding: Pick<Finding, "severity" | "proposed_severity">): { level: Severity; label: string; confirmed: boolean } {
  if (finding.severity) return { level: finding.severity, label: finding.severity, confirmed: true };
  return { level: finding.proposed_severity, label: `${finding.proposed_severity} proposed`, confirmed: false };
}

export function charterCounts(charters: readonly Charter[]): Record<Charter["status"], number> {
  const counts: Record<Charter["status"], number> = { queued: 0, running: 0, done: 0, vetoed: 0, failed: 0, stopped: 0 };
  for (const charter of charters) counts[charter.status] += 1;
  return counts;
}

/** The share of the time box used, 0..1. */
export function timeShare(view: Pick<SessionView, "elapsed_s" | "spec">): number {
  const total = view.spec.budget.time_s || 1;
  return Math.max(0, Math.min(1, view.elapsed_s / total));
}

/** One line for the live feed; null for events the feed does not show. */
export function eventLine(event: ExploreEvent): { tone: "clue" | "finding" | "info" | "warn"; text: string } | null {
  const data = event.data;
  const text = (key: string) => (typeof data[key] === "string" ? (data[key] as string) : "");
  switch (event.kind) {
    case "clue":
      return { tone: "clue", text: `Clue: ${text("title")}` };
    case "reproducing":
      return { tone: "info", text: `Reproducing: ${text("title")}` };
    case "finding":
      return { tone: "finding", text: `Finding (${text("outcome") || "new"}, ${text("severity")} proposed): ${text("title")}` };
    case "suppressed":
      return { tone: "info", text: `Already triaged as not a bug: ${text("title")}` };
    case "charter_started":
      return { tone: "info", text: `Started: ${text("title")}` };
    case "charter_finished":
      return { tone: "info", text: `Charter done: ${String(data.turns ?? 0)} moves, ${String(data.clues ?? 0)} clue(s)` };
    case "charter_added":
      return { tone: "info", text: `Charter added by ${text("by") || "a person"}` };
    case "planned":
      return { tone: "info", text: `Planned ${String(data.charters ?? 0)} charter(s)` };
    case "size":
      return { tone: "info", text: `Swarm size changed by ${text("by") || "a person"}` };
    case "finished":
      return { tone: text("status") === "failed" ? "warn" : "info", text: text("message") || "Finished" };
    default:
      return null;
  }
}

export const COST_ESTIMATE_NOTE = "Estimated: the hosted model reports tokens, not dollars, so each call is priced at explore.cost_per_decision_usd";

/** A session's model cost, with ~ when part of it is an estimate. */
export function sessionCost(view: Pick<SessionView, "cost_usd" | "cost_estimated">): string {
  return `${view.cost_estimated ? "~" : ""}${formatCost(view.cost_usd)}`;
}
