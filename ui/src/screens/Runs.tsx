import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { Bugsy } from "../components/Brand";
import { Icon } from "../components/Icon";
import { StartRunDialog, type RerunOf } from "../components/StartRunDialog";
import { StatusChip } from "../components/StatusChip";
import { api, remove } from "../lib/api";
import { isRunList } from "../lib/guards";
import { stepLabel } from "../lib/timeline";
import { agentName, deleteRunPrompt, failingStep, formatCost, formatDateTime, formatDuration, localZoneName, runMode, runSeconds } from "../lib/format";
import { FINAL_STATUSES, type RunView } from "../lib/types";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "failed", label: "Failed" },
  { id: "passed", label: "Passed" },
  { id: "error", label: "Error" },
  { id: "cancelled", label: "Cancelled" },
] as const;
type FilterId = (typeof FILTERS)[number]["id"];

/** Newest runs the list shows; older ones stay in .hercules/runs and open by their URL. */
export const RUN_LIMIT = 200;
/** Poll fast only while a run is queued or running; a finished list changes only when a run starts. */
export const ACTIVE_POLL_MS = 1500;
export const IDLE_POLL_MS = 10_000;

export function runsPollInterval(runs: readonly RunView[] | undefined): number {
  return runs?.some((run) => !FINAL_STATUSES.has(run.status)) ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

function matchesFilter(run: RunView, filter: FilterId): boolean {
  if (filter === "all") return true;
  if (filter === "active") return !FINAL_STATUSES.has(run.status);
  return run.status === filter;
}

function useFilters() {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const status = (FILTERS.some((item) => item.id === params.get("status")) ? params.get("status") : "all") as FilterId;
  const query = params.get("q") ?? "";
  const mode = params.get("mode") ?? "any";
  const batch = params.get("batch") ?? "";
  const set = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(search);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    const text = next.toString();
    navigate(`${location}${text ? `?${text}` : ""}`, { replace: true });
  };
  return { status, query, mode, batch, clearBatch: () => set("batch", "", ""), setStatus: (value: FilterId) => set("status", value, "all"), setQuery: (value: string) => set("q", value, ""), setMode: (value: string) => set("mode", value, "any") };
}

export function RunsScreen() {
  const [, navigate] = useLocation();
  const client = useQueryClient();
  const filters = useFilters();
  const [starting, setStarting] = useState(false);
  const [rerunOf, setRerunOf] = useState<RerunOf | null>(null);
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => api<{ runs: RunView[] }>(`/api/runs?limit=${RUN_LIMIT}&with_result=true`, { guard: isRunList }),
    refetchInterval: (query) => runsPollInterval(query.state.data?.runs),
  });

  const drop = useMutation({
    mutationFn: (runId: string) => remove<{ run_id: string; removed: boolean }>(`/api/runs/${encodeURIComponent(runId)}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["runs"] }),
  });

  const loaded = runs.data?.runs ?? [];
  const all = useMemo(() => (filters.batch ? loaded.filter((run) => run.batch === filters.batch) : loaded), [loaded, filters.batch]);
  const batchFolder = filters.batch ? (all.find((run) => run.folder !== undefined && run.folder !== null)?.folder ?? null) : null;
  const visible = useMemo(() => {
    const needle = filters.query.trim().toLowerCase();
    return all.filter((run) => {
      if (!matchesFilter(run, filters.status)) return false;
      if (filters.mode !== "any" && runMode(run) !== filters.mode) return false;
      if (!needle) return true;
      return [run.run_id, run.case_id ?? "", run.title ?? ""].some((text) => text.toLowerCase().includes(needle));
    });
  }, [all, filters.status, filters.mode, filters.query]);
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((item) => [item.id, all.filter((run) => matchesFilter(run, item.id)).length])), [all]);

  return (
    <section className="page" aria-labelledby="runs-title">
      <header className="page-head">
        <div>
          <h1 id="runs-title">Runs</h1>
          <p className="page-sub">Queued, running and finished runs of this project, newest first.</p>
        </div>
        <button type="button" className="button button-primary" onClick={() => setStarting(true)}>
          <Icon name="play" size={14} /> Start a run
        </button>
      </header>

      <div className="toolbar" role="search">
        <div className="segmented" role="group" aria-label="Filter by status">
          {FILTERS.map((item) => (
            <button key={item.id} type="button" className={`segment${filters.status === item.id ? " segment-on" : ""}`} aria-pressed={filters.status === item.id} onClick={() => filters.setStatus(item.id)}>
              {item.label}
              <span className="segment-count">{counts[item.id] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="field-inline">
          <select aria-label="Filter by mode" value={filters.mode} onChange={(event) => filters.setMode(event.target.value)}>
            <option value="any">Any mode</option>
            <option value="pure">Pure</option>
            <option value="hybrid">Hybrid</option>
            <option value="test">Test decider</option>
          </select>
        </div>
        <label className="search">
          <Icon name="search" size={14} />
          <span className="visually-hidden">Search runs</span>
          <input type="search" placeholder="Case, title or run id" value={filters.query} onChange={(event) => filters.setQuery(event.target.value)} />
        </label>
      </div>

      {filters.batch ? (
        <p className="callout callout-note batch-note" role="note">
          <span>
            Showing the {all.length} {all.length === 1 ? "run" : "runs"} of one folder run{batchFolder !== null ? <> of <strong>{batchFolder || "all cases"}</strong></> : null}.
          </span>
          <button type="button" className="link-button" onClick={filters.clearBatch}>
            Show all runs
          </button>
        </p>
      ) : null}

      {loaded.length >= RUN_LIMIT ? (
        <p className="callout callout-note" role="note">
          Showing the newest {RUN_LIMIT} runs; filters and counts cover these only. Older runs stay in <code>.hercules/runs</code> and open at <code>/runs/&lt;run id&gt;</code>.
        </p>
      ) : null}

      {runs.isError ? <p className="callout callout-fail">Could not load runs: {(runs.error as Error).message}</p> : null}
      {drop.error ? (
        <p className="callout callout-fail" role="alert">
          {(drop.error as Error).message}
        </p>
      ) : null}
      {runs.isPending ? <p className="muted">Loading runs…</p> : null}

      {runs.data && visible.length === 0 ? (
        <div className="empty">
          {all.length === 0 ? (
            <div className="empty-hero">
              <Bugsy />
              <p className="empty-title">No runs yet.</p>
              <p>Start a run from one of your cases and watch it live here.</p>
              <button type="button" className="button" onClick={() => setStarting(true)}>
                Start the first run
              </button>
            </div>
          ) : (
            <p>No runs match these filters.</p>
          )}
        </div>
      ) : null}

      {visible.length > 0 ? (
        <div className="table-wrap">
          <table className="table runs-table">
            <thead>
              <tr>
                <th scope="col">Status</th>
                <th scope="col">Case</th>
                <th scope="col">Mode</th>
                <th scope="col">Started ({localZoneName()})</th>
                <th scope="col" className="num">
                  Duration
                </th>
                <th scope="col" className="num">
                  Cost
                </th>
                <th scope="col">Failing step</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((run) => {
                const failed = failingStep(run);
                const final = FINAL_STATUSES.has(run.status);
                return (
                  <tr key={run.run_id} className={`row-${run.status}`} onClick={(event) => {
                    if ((event.target as HTMLElement).closest("a,button")) return;
                    navigate(`/runs/${run.run_id}`);
                  }}>
                    <td>
                      <StatusChip status={run.status} />
                    </td>
                    <td className="cell-case">
                      <Link href={`/runs/${run.run_id}`} className="run-title">
                        {run.title ?? run.case_id ?? run.run_id}
                      </Link>
                      <span className="run-meta mono">
                        {run.run_id}
                        {run.parent_run_id ? " · re-run" : ""}
                        {run.started_by === "agent" ? ` · via ${agentName(run.agent)}` : ""}
                      </span>
                      {run.status === "running" && run.current_step ? (
                        <span className="run-progress">
                          Step {run.current_step}/{run.total_steps ?? "?"}: {run.current_action}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <span className={`tag tag-${runMode(run)}`}>{runMode(run)}</span>
                    </td>
                    <td className="nowrap">{formatDateTime(run.started_at ?? run.created_at)}</td>
                    <td className="num">{formatDuration(runSeconds(run))}</td>
                    <td className="num">{formatCost(run.result?.cost.usd)}</td>
                    <td className="cell-failing">
                      {failed ? (
                        <span title={failed.actual ?? undefined}>
                          <span className="mono">#{failed.index}</span> {stepLabel(failed)}
                        </span>
                      ) : run.reason && run.status !== "passed" ? (
                        <span className="muted" title={run.reason}>
                          {run.reason}
                        </span>
                      ) : (
                        <span className="muted">–</span>
                      )}
                    </td>
                    <td className="cell-actions">
                      <button type="button" className="button button-small" disabled={!final} onClick={() => setRerunOf({ run, scope: "whole" })} title="Run the case again; change its options first">
                        <Icon name="rerun" size={13} /> Re-run
                      </button>
                      <button
                        type="button"
                        className="button button-small button-ghost button-danger-text"
                        disabled={!final || drop.isPending}
                        onClick={() => {
                          if (window.confirm(deleteRunPrompt(run.run_id))) drop.mutate(run.run_id);
                        }}
                        aria-label={`Delete run ${run.run_id}`}
                        title={final ? "Delete this run and its evidence" : "Cancel the run before deleting it"}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {starting || rerunOf ? (
        <StartRunDialog
          key={rerunOf ? `${rerunOf.run.run_id}-${rerunOf.scope}` : "new"}
          rerun={rerunOf ?? undefined}
          onClose={() => {
            setStarting(false);
            setRerunOf(null);
          }}
          onStarted={(runId, batch) => {
            setStarting(false);
            setRerunOf(null);
            void client.invalidateQueries({ queryKey: ["runs"] });
            navigate(batch ? `/?batch=${encodeURIComponent(batch)}` : `/runs/${runId}`);
          }}
        />
      ) : null}
    </section>
  );
}
