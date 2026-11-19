import { FINAL_STATUSES, type RunResult, type RunView, type StepResult } from "./types";

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest.toString().padStart(2, "0")}s`;
}

export function formatMs(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "–" : formatDuration(ms / 1000);
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "–";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatPercent(probability: number | null | undefined): string {
  if (probability === null || probability === undefined) return "–";
  const percent = probability * 100;
  if (percent > 0 && percent < 1) return "<1%";
  if (percent < 100 && percent > 99) return ">99%";
  return `${Math.round(percent)}%`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return sameDay ? time : `${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}`;
}

/** A date and time in full, as "Nov 16, 2026, 9:22 AM" (local time). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  return date.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true });
}

/** The device's time zone as a short name ("IST", "PST", or "GMT+5:30" where no short name exists). */
export function localZoneName(at: Date = new Date()): string {
  return new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(at).find((part) => part.type === "timeZoneName")?.value ?? "local time";
}

/** Seconds a run took (result), or has been running so far. */
export function runSeconds(run: RunView, now: number = Date.now()): number | null {
  if (run.result) return run.result.duration_s;
  if (!run.started_at) return null;
  const start = Date.parse(run.started_at);
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, (end - start) / 1000) : null;
}

export function failingStep(run: RunView): StepResult | null {
  return run.result?.steps.find((step) => step.status === "failed" || step.status === "error" || step.status === "blocked") ?? null;
}

export function runMode(run: RunView): string {
  return run.result?.engines.mode ?? (run.mode && run.mode !== "auto" ? run.mode : run.mode ?? "–");
}

export interface EngineLabels {
  /** The mode the run used (or asked for while it has not finished). */
  mode: string;
  /** Set when the run asked for "auto" and the result says which mode it chose. */
  requested: string | null;
  /** The engine that decided, or why it is not known. */
  decider: string;
  deciderKnown: boolean;
}

/** Mode and decider of a run for its header: the result's engines once finished, else the options it started with. */
export function engineLabels(run: RunView): EngineLabels {
  const engines = run.result?.engines;
  const asked = run.settings?.mode ?? run.mode ?? "auto";
  const mode = engines?.mode ?? asked;
  const requested = engines?.mode && engines.mode !== asked ? asked : null;
  const stub = run.settings?.decider ?? null;
  const decider = engines?.decider ?? stub;
  if (decider) return { mode, requested, decider: engines?.decider_model ? `${decider} (${engines.decider_model})` : decider, deciderKnown: true };
  const finished = FINAL_STATUSES.has(run.status);
  return { mode, requested, decider: finished ? "none reported" : "reported when the run finishes", deciderKnown: false };
}

const AGENT_NAMES: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
  copilot: "GitHub Copilot",
  gemini: "Gemini CLI",
};

/** The display name of the coding agent that started a run. */
export function agentName(agent: string | null | undefined): string {
  return (agent && AGENT_NAMES[agent]) || "a coding agent";
}

/** A path as the UI shows it: inside a run folder from ``.hercules/`` on, under a home folder as ``~/...``, else as is.
 * The full path stays available (as a title) for copying. */
export function shortPath(path: string | null | undefined): string {
  if (!path) return "";
  if (path.startsWith("~")) return path;
  const unix = path.replace(/\\/g, "/");
  const project = unix.indexOf("/.hercules/");
  if (project >= 0 && !/^\/(Users|home)\/[^/]+\/\.hercules\//.test(unix) && !/^[A-Za-z]:\/Users\/[^/]+\/\.hercules\//.test(unix)) return unix.slice(project + 1);
  const home = unix.match(/^(?:\/Users\/[^/]+|\/home\/[^/]+|[A-Za-z]:\/Users\/[^/]+)(\/.*)?$/);
  return home ? `~${home[1] ?? ""}` : path;
}

export const deleteRunPrompt = (runId: string) => `Delete run ${runId} with its screenshots, logs and other evidence? This cannot be undone.`;

/** "1.2k" for 1,234 and "47k" for 47,463; plain below a thousand. */
export function compactCount(count: number): string {
  if (count < 1000) return String(count);
  const thousands = count / 1000;
  return `${thousands < 10 ? thousands.toFixed(1).replace(/\.0$/, "") : Math.round(thousands)}k`;
}

/**
 * The decision engine's line for a finished run: which engine (JEV, or Clef in pure mode), its calls and the
 * estimated prompt tokens sent to it (it reports cost, not token counts). Null when no decision engine was asked.
 */
export function decisionUsage(result: Pick<RunResult, "engines" | "cost">): { engine: string; text: string; calls: string; tokens: number | null } | null {
  const clef = result.engines.clef_calls ?? 0;
  const calls = clef || (result.engines.jev_calls ?? 0);
  if (!calls) return null;
  const engine = clef ? "Clef" : "JEV";
  const tokens = result.cost.decision_tokens_est ?? null;
  const callText = `${calls} ${calls === 1 ? "call" : "calls"}`;
  // Runs recorded before the estimate existed show their calls instead.
  return { engine, text: tokens === null ? callText : `~${compactCount(tokens)} tokens`, calls: callText, tokens };
}
