import { FINAL_STATUSES, type RunView, type StepResult } from "./types";

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
