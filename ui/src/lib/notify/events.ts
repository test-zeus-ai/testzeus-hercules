// What changed between two polls of the runs list or the Explore sessions: the pure half of the notifier.
// A tracker remembers what was already announced; the first poll only records what is there, so a reload
// never replays runs that finished before the page opened, except what ended after `since`: the page was open
// then (it reloaded, or the browser put a background tab to sleep), so that is still news.

import { FINISHED, MISSIONS, type SessionView, type Severity } from "../explore";
import { agentName, formatCost, formatDuration, runSeconds } from "../format";
import { FINAL_STATUSES, type RunView } from "../types";
import type { NotifyPrefs } from "./prefs";

export type NotifyEvent =
  | { kind: "run"; key: string; run: RunView }
  | { kind: "batch"; key: string; batch: string; runs: RunView[] }
  /** quiet: it was already waiting when the page opened, so it shows without a sound or a system notification. */
  | { kind: "approval"; key: string; run: RunView; quiet: boolean }
  | { kind: "approval-cleared"; key: string }
  | { kind: "explore"; key: string; session: SessionView };

export interface SessionTracker {
  seeded: boolean;
  seen: Set<string>;
  /** On the first poll, what ended after this (ms, the daemon's clock) is announced; null announces nothing. */
  since: number | null;
  /** The latest end the tracker has accounted for: the next page load's `since`. */
  latest: number;
}

export interface RunTracker extends SessionTracker {
  /** run_id → the key of the approval it waits on */
  approvals: Map<string, string>;
}

export const newSessionTracker = (since: number | null = null): SessionTracker => ({ seeded: false, seen: new Set(), since, latest: since ?? 0 });
export const newRunTracker = (since: number | null = null): RunTracker => ({ ...newSessionTracker(since), approvals: new Map() });

function endedAt(at: string | null | undefined): number {
  const time = at ? Date.parse(at) : Number.NaN;
  return Number.isNaN(time) ? 0 : time;
}

/** Whether something that just ended is news: always after the first poll, and on it when it ended after `since`. */
function isNews(tracker: SessionTracker, at: string | null | undefined): boolean {
  const time = endedAt(at);
  tracker.latest = Math.max(tracker.latest, time);
  return tracker.seeded || (tracker.since !== null && time > tracker.since);
}

const WAITING = new Set(["needs_approval", "needs_plan_approval"]);
const FAILING = new Set(["failed", "error"]);

/** One key per question asked: a second approval in the same run is a new notification. */
export function approvalKey(run: RunView): string {
  const asked = run.approval;
  return `approval:${run.run_id}:${run.status}:${asked?.id ?? asked?.asked_at ?? asked?.step ?? "draft"}`;
}

export function runEvents(tracker: RunTracker, runs: readonly RunView[]): NotifyEvent[] {
  const events: NotifyEvent[] = [];
  // An Explore session's child runs are announced once, as the session.
  const own = runs.filter((run) => !run.explore_session);

  const waiting = new Map<string, string>();
  for (const run of own) if (WAITING.has(run.status)) waiting.set(run.run_id, approvalKey(run));
  for (const [runId, key] of tracker.approvals) if (waiting.get(runId) !== key) events.push({ kind: "approval-cleared", key });
  for (const run of own) {
    const key = waiting.get(run.run_id);
    if (key && tracker.approvals.get(run.run_id) !== key) events.push({ kind: "approval", key, run, quiet: !tracker.seeded });
  }
  tracker.approvals = waiting;

  const announceRun = (run: RunView) => {
    if (!FINAL_STATUSES.has(run.status)) return;
    const key = `run:${run.run_id}`;
    if (tracker.seen.has(key)) return;
    tracker.seen.add(key);
    if (isNews(tracker, run.finished_at)) events.push({ kind: "run", key, run });
  };
  const batches = new Map<string, RunView[]>();
  for (const run of own) {
    if (run.batch) batches.set(run.batch, [...(batches.get(run.batch) ?? []), run]);
    else announceRun(run);
  }
  // A folder run is one notification, once its last run ends.
  for (const [batch, members] of batches) {
    const key = `batch:${batch}`;
    // A rerun keeps its folder run's batch; once the folder run was announced, the rerun is news on its own.
    if (tracker.seen.has(key)) {
      members.forEach(announceRun);
      continue;
    }
    if (!members.every((run) => FINAL_STATUSES.has(run.status))) continue;
    tracker.seen.add(key);
    for (const run of members) tracker.seen.add(`run:${run.run_id}`);
    const last = members.map((run) => run.finished_at).reduce<string | null | undefined>((a, b) => (endedAt(b) > endedAt(a) ? b : a), null);
    if (isNews(tracker, last)) events.push({ kind: "batch", key, batch, runs: members });
  }
  tracker.seeded = true;
  return events;
}

export function exploreEvents(tracker: SessionTracker, sessions: readonly SessionView[]): NotifyEvent[] {
  const events: NotifyEvent[] = [];
  for (const session of sessions) {
    if (!FINISHED.has(session.status)) continue;
    const key = `explore:${session.id}`;
    if (tracker.seen.has(key)) continue;
    tracker.seen.add(key);
    if (isNews(tracker, session.finished_at)) events.push({ kind: "explore", key, session });
  }
  tracker.seeded = true;
  return events;
}

export function failing(event: NotifyEvent): boolean {
  if (event.kind === "run") return FAILING.has(event.run.status);
  if (event.kind === "batch") return event.runs.some((run) => FAILING.has(run.status));
  if (event.kind === "explore") return event.session.status === "failed";
  return false;
}

/** Whether the tester asked to hear about this kind of event. */
export function wanted(event: NotifyEvent, prefs: NotifyPrefs): boolean {
  switch (event.kind) {
    case "run":
    case "batch":
      return prefs.runs === "all" || (prefs.runs === "failures" && failing(event));
    case "approval":
      return prefs.approvals;
    case "explore":
      return prefs.explore;
    case "approval-cleared":
      return true;
  }
}

export type MessageType = "success" | "error" | "warning" | "info";

export interface Message {
  type: MessageType;
  title: string;
  body: string;
  /** Where the toast's button and the system notification's click go. */
  href: string;
  action: string;
  /** Stays until dismissed (failures, approvals, serious findings). The toast library replaces a toast that closes on
   * its own with the next one, so only these stack and none of them is lost to a later toast. */
  sticky: boolean;
}

type Draft = Omit<Message, "sticky">;

export function runName(run: RunView): string {
  return run.title || run.case_id || run.run_id;
}

function byAgent(run: RunView): string | null {
  return run.started_by === "agent" ? `started by ${agentName(run.agent)}` : null;
}

function join(...parts: Array<string | null | undefined | false>): string {
  return parts.filter(Boolean).join(" · ");
}

function stepsDone(run: RunView): string | null {
  return run.total_steps ? `${run.steps_done} of ${run.total_steps} steps done` : null;
}

function runMessage(run: RunView): Draft {
  const name = runName(run);
  const base = { href: `/runs/${run.run_id}`, action: "View run" };
  const seconds = runSeconds(run);
  const took = seconds === null ? null : formatDuration(seconds);
  switch (run.status) {
    case "passed":
      return { ...base, type: "success", title: `${name} passed`, body: join(took, byAgent(run)) };
    case "failed":
      return { ...base, type: "error", title: `${name} failed`, body: join(run.reason || stepsDone(run), byAgent(run)) };
    case "cancelled":
      return { ...base, type: "info", title: `${name} was cancelled`, body: join(stepsDone(run), byAgent(run)) };
    default:
      return { ...base, type: "error", title: `${name} stopped with an error`, body: join(run.reason, byAgent(run)) };
  }
}

function batchMessage(batch: string, runs: readonly RunView[]): Draft {
  const count = (statuses: Set<string>) => runs.filter((run) => statuses.has(run.status)).length;
  const passed = count(new Set(["passed"]));
  const failed = count(FAILING);
  const cancelled = count(new Set(["cancelled"]));
  const folder = runs.find((run) => run.folder)?.folder;
  return {
    type: failed ? "error" : "success",
    title: `${folder || "Folder run"}: ${join(`${passed} passed`, failed > 0 && `${failed} failed`, cancelled > 0 && `${cancelled} cancelled`)}`,
    body: `${runs.length} ${runs.length === 1 ? "run" : "runs"} finished`,
    href: `/runs?batch=${encodeURIComponent(batch)}`,
    action: "View runs",
  };
}

function approvalMessage(run: RunView): Draft {
  const name = runName(run);
  const base = { type: "warning" as const, href: `/runs/${run.run_id}`, action: "Review" };
  if (run.status === "needs_plan_approval") return { ...base, title: `Review the plan for ${name}`, body: join("The run waits for your go-ahead", byAgent(run)) };
  const asked = run.approval;
  if (!asked) return { ...base, title: `Approve the steps for ${name}`, body: join("Drafted from a description; it starts once you approve", byAgent(run)) };
  const deadline = asked.asked_at && asked.timeout_s ? new Date(Date.parse(asked.asked_at) + asked.timeout_s * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : null;
  const step = asked.step ? `Step ${asked.step}` : null;
  const what = asked.action || asked.reason;
  return { ...base, title: `${name} needs your approval`, body: join(step && what ? `${step}: ${what}` : step || what, deadline && `answer by ${deadline}`) };
}

const SEVERITY_ORDER: readonly Severity[] = ["P0", "P1", "P2", "P3"];

function exploreMessage(session: SessionView): Draft {
  const mission = MISSIONS.find((item) => item.id === session.spec.mission)?.label ?? "Explore";
  const base = { href: `/explore/${session.id}`, action: "Review findings" };
  const findings = session.findings.length;
  const counted = `${findings} ${findings === 1 ? "finding" : "findings"}`;
  const bySeverity = SEVERITY_ORDER.map((severity) => [severity, session.findings.filter((item) => (item.severity ?? item.proposed_severity) === severity).length] as const);
  const serious = bySeverity.filter(([severity, n]) => n > 0 && (severity === "P0" || severity === "P1")).map(([severity, n]) => `${n} ${severity}`);
  const summary = join(formatDuration(session.elapsed_s), formatCost(session.cost_usd));
  switch (session.status) {
    case "failed":
      return { ...base, type: "error", title: `${mission} session failed`, body: join(session.message, findings > 0 && `${counted} so far`), action: "Open session" };
    case "stopped":
      return { ...base, type: "info", title: `${mission} session stopped: ${counted} so far`, body: summary };
    default:
      return { ...base, type: serious.length ? "warning" : "success", title: `${mission} session finished: ${counted}${serious.length ? ` (${serious.join(", ")})` : ""}`, body: summary };
  }
}

export function message(event: Exclude<NotifyEvent, { kind: "approval-cleared" }>): Message {
  const draft = draftOf(event);
  return { ...draft, sticky: draft.type === "error" || draft.type === "warning" };
}

function draftOf(event: Exclude<NotifyEvent, { kind: "approval-cleared" }>): Draft {
  switch (event.kind) {
    case "run":
      return runMessage(event.run);
    case "batch":
      return batchMessage(event.batch, event.runs);
    case "approval":
      return approvalMessage(event.run);
    case "explore":
      return exploreMessage(event.session);
  }
}
