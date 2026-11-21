import type { Finding, SessionView } from "../lib/explore";
import type { RunView } from "../lib/types";

// Runs and Explore sessions for the notifier tests.

const FINISHED_STATES = new Set(["finished", "failed", "stopped"]);

export function run(id: string, status: RunView["status"], extra: Partial<RunView> = {}): RunView {
  return {
    run_id: id,
    status,
    finished: ["passed", "failed", "error", "cancelled"].includes(status),
    title: `Case ${id}`,
    created_at: "2026-11-20T10:00:00Z",
    started_at: "2026-11-20T10:00:00Z",
    finished_at: status === "running" ? null : "2026-11-20T10:01:42Z",
    total_steps: 7,
    steps_done: 3,
    last_seq: 0,
    run_dir: "",
    ui_url: "",
    summary: "",
    ...extra,
  };
}

export function session(id: string, status: SessionView["status"], findings: Array<Partial<Finding>> = []): SessionView {
  return {
    id,
    status,
    spec: { mission: "p1", workers: 2, reproducers: 1, budget: { time_s: 1800 } },
    phase_detail: "",
    workers: 2,
    reproducers: 1,
    size_changes: [],
    started_at: "2026-11-20T10:00:00Z",
    finished_at: FINISHED_STATES.has(status) ? "2026-11-20T10:23:00Z" : null,
    elapsed_s: 1380,
    cost_usd: 1.84,
    charters: [],
    clues: 0,
    findings: findings as Finding[],
    ux_notes: [],
    coverage: {},
    children: [],
    message: status === "failed" ? "the browser crashed" : "",
  } as SessionView;
}
