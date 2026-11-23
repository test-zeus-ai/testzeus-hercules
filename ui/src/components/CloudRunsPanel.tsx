import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, post } from "../lib/api";
import { CLOUD_RUN_POLL_MS, cloudRunAnnouncement, cloudRunOptionsLabel, cloudRunSeconds, cloudRunsPollInterval, isCloudRunActive } from "../lib/clouds";
import { formatDuration, formatTime } from "../lib/format";
import { isCloudRun, isCloudRunList } from "../lib/guards";
import type { CloudRun, CloudRunDetail } from "../lib/types";
import { CloudMark } from "./CloudCard";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

/** The current time, ticking each second while `active` (for elapsed times). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function CloudRunLog({ run }: { run: CloudRun }) {
  const active = isCloudRunActive(run);
  const detail = useQuery({
    queryKey: ["cloud-run", run.id],
    queryFn: () => api<CloudRunDetail>(`/api/cloud-runs/${encodeURIComponent(run.id)}`),
    refetchInterval: active ? CLOUD_RUN_POLL_MS : false,
  });
  const ref = useRef<HTMLPreElement>(null);
  const log = detail.data?.log ?? "";
  useEffect(() => {
    // Follow the tail, like a terminal.
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [log]);
  if (detail.isError) return <p className="callout callout-fail small">{(detail.error as Error).message}</p>;
  if (!detail.data) return <p className="muted small">Loading the output…</p>;
  return (
    <pre ref={ref} className="cloud-log" tabIndex={0} aria-label={`Output of the ${run.cloud_name} run`}>
      {log || "No output yet."}
    </pre>
  );
}

function CloudRunItem({ run, highlighted }: { run: CloudRun; highlighted: boolean }) {
  const client = useQueryClient();
  const active = isCloudRunActive(run);
  const now = useNow(active);
  const [showLog, setShowLog] = useState(false);
  const ref = useRef<HTMLLIElement>(null);
  const cancel = useMutation({
    mutationFn: () => post<CloudRun>(`/api/cloud-runs/${encodeURIComponent(run.id)}/cancel`, undefined, isCloudRun),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["cloud-runs", run.run_id] }),
  });
  useEffect(() => {
    if (!highlighted || !ref.current) return;
    ref.current.focus({ preventScroll: true });
    if (typeof ref.current.scrollIntoView === "function") ref.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);
  const seconds = cloudRunSeconds(run, now);
  return (
    <li ref={ref} className={`cloud-run cloud-run-${run.status}${highlighted ? " cloud-run-new" : ""}`} tabIndex={-1} aria-label={`${run.cloud_name}, ${cloudRunOptionsLabel(run)}`}>
      <div className="cloud-run-head">
        <CloudMark cloud={run.cloud} name={run.cloud_name} small />
        <div className="cloud-run-what">
          <strong>{run.cloud_name}</strong>
          <span className="muted small">
            {cloudRunOptionsLabel(run)} · {formatTime(run.created_at)}
          </span>
        </div>
        <StatusChip status={run.status} />
        <span className="cloud-run-time small" title={active ? "Elapsed so far" : "Duration"}>
          {active ? <span className="live-dot" aria-hidden="true" /> : null}
          {formatDuration(seconds)}
        </span>
        <span className="cloud-run-links">
          {run.session_url ? (
            <a className="button button-small" href={run.session_url} target="_blank" rel="noopener noreferrer" aria-label={`Open the ${run.cloud_name} session (new tab)`}>
              Open session <Icon name="link" size={12} />
            </a>
          ) : null}
          <a className="button button-small button-ghost" href={run.dashboard_url} target="_blank" rel="noopener noreferrer" aria-label={`Open the ${run.cloud_name} dashboard (new tab)`}>
            Open dashboard <Icon name="link" size={12} />
          </a>
          {active ? (
            <button type="button" className="button button-small button-danger" onClick={() => cancel.mutate()} disabled={cancel.isPending} aria-label={`Cancel the ${run.cloud_name} run`}>
              <Icon name="stop" size={12} /> Cancel
            </button>
          ) : null}
        </span>
      </div>
      {run.message ? <p className={`cloud-run-message small${run.status === "error" || run.status === "failed" ? " text-fail" : " muted"}`}>{run.message}</p> : null}
      {cancel.error ? (
        <p className="callout callout-fail small" role="alert">
          {(cancel.error as Error).message}
        </p>
      ) : null}
      <details className="cloud-run-output" onToggle={(event) => setShowLog((event.currentTarget as HTMLDetailsElement).open)}>
        <summary>Output{run.exit_code !== null ? <span className="muted"> · exit code {run.exit_code}</span> : null}</summary>
        {showLog ? <CloudRunLog run={run} /> : null}
      </details>
    </li>
  );
}

/** Announces cloud runs whose status changed since the last poll (not the ones already there). */
function useAnnouncement(runs: readonly CloudRun[] | undefined): string {
  const seen = useRef<Map<string, string> | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!runs) return;
    const previous = seen.current;
    seen.current = new Map(runs.map((run) => [run.id, run.status]));
    if (!previous) return;
    const changed = runs.filter((run) => previous.get(run.id) !== run.status);
    if (changed.length) setMessage(changed.map(cloudRunAnnouncement).join(". "));
  }, [runs]);
  return message;
}

/** This run's cloud runs, newest first, polled while any is starting or running. */
export function CloudRunsPanel({ runId, highlight = null, onRunOnCloud }: { runId: string; highlight?: string | null; onRunOnCloud?: () => void }) {
  const list = useQuery({
    queryKey: ["cloud-runs", runId],
    queryFn: () => api<{ cloud_runs: CloudRun[] }>(`/api/runs/${encodeURIComponent(runId)}/cloud-runs`, { guard: isCloudRunList }),
    refetchInterval: (query) => cloudRunsPollInterval(query.state.data?.cloud_runs),
  });
  const runs = list.data?.cloud_runs;
  const announcement = useAnnouncement(runs);
  // A daemon without cloud support answers 404: show nothing rather than an error.
  if (list.error instanceof ApiError && list.error.status === 404) return null;
  if (!onRunOnCloud && !runs?.length) return null;
  return (
    <section className="panel panel-cloud-runs" aria-labelledby="cloud-runs-title">
      <div className="panel-head">
        <h2 id="cloud-runs-title">
          <Icon name="cloud" size={16} /> Cloud runs
        </h2>
        {onRunOnCloud ? (
          <button type="button" className="button button-small" onClick={onRunOnCloud}>
            <Icon name="play" size={12} /> Run on a cloud
          </button>
        ) : null}
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
      {list.isError ? <p className="callout callout-fail small">{(list.error as Error).message}</p> : null}
      {runs && runs.length === 0 ? <p className="empty-note small">Not run on a cloud yet. Run this test on BrowserStack, Sauce Labs or TestMu AI to see it on their browsers and devices.</p> : null}
      {runs && runs.length > 0 ? (
        <ul className="cloud-runs">
          {runs.map((run) => (
            <CloudRunItem key={run.id} run={run} highlighted={run.id === highlight} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
