import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation } from "wouter";
import { Icon, type IconName } from "../components/Icon";
import { StartRunDialog } from "../components/StartRunDialog";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { chartMax, dayLabel, dayTitle, durationDelta, greeting, passRateDelta, relativeTime, runsDelta, type Delta } from "../lib/dashboard";
import { formatCost, formatDuration, formatPercent, runMode, runSeconds } from "../lib/format";
import { isCaseList, isCloudsReport, isDashboardReport, isModelsReport, isRunList } from "../lib/guards";
import { FINAL_STATUSES, type Environment, type Health, type RunView } from "../lib/types";
import { ACTIVE_POLL_MS, IDLE_POLL_MS } from "./Runs";

const RECENT = 6;

function timeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const runList = (query: string) => () => api<{ runs: RunView[] }>(`/api/runs?${query}`, { guard: isRunList });

function Stat({ label, value, delta }: { label: string; value: string; delta: Delta }) {
  return (
    <div className="panel dash-stat">
      <span className="dash-label">{label}</span>
      <span className="dash-stat-value">{value}</span>
      <span className={`dash-delta dash-delta-${delta.tone}`}>{delta.text}</span>
    </div>
  );
}

function SetupCard({ href, icon, label, value, detail }: { href: string; icon: IconName; label: string; value: string; detail: string }) {
  return (
    <Link href={href} className="panel dash-setup">
      <span className="dash-setup-label">
        <Icon name={icon} size={15} /> {label}
      </span>
      <span className="dash-setup-value">{value}</span>
      <span className="dash-setup-detail">{detail}</span>
    </Link>
  );
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** The home page: what needs the tester, the last week at a glance, what runs now, recent runs and the setup. */
export function DashboardScreen() {
  const [, navigate] = useLocation();
  const client = useQueryClient();
  const [starting, setStarting] = useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health"), refetchInterval: 15000 });
  const recent = useQuery({
    queryKey: ["runs", "dashboard"],
    queryFn: runList("limit=50"),
    refetchInterval: (query) => (query.state.data?.runs.some((run) => !FINAL_STATUSES.has(run.status)) ? ACTIVE_POLL_MS : IDLE_POLL_MS),
  });
  const approvals = useQuery({ queryKey: ["runs", "needs_approval"], queryFn: runList("status=needs_approval&limit=20"), refetchInterval: IDLE_POLL_MS });
  const plans = useQuery({ queryKey: ["runs", "needs_plan_approval"], queryFn: runList("status=needs_plan_approval&limit=20"), refetchInterval: IDLE_POLL_MS });
  const report = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api(`/api/dashboard?days=7&tz=${encodeURIComponent(timeZone())}`, { guard: isDashboardReport }),
    refetchInterval: IDLE_POLL_MS * 3,
  });
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api("/api/cases", { guard: isCaseList }) });
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => api<{ environments: Environment[] }>("/api/environments") });
  const models = useQuery({ queryKey: ["models", false], queryFn: () => api("/api/models", { guard: isModelsReport }) });
  const clouds = useQuery({ queryKey: ["clouds"], queryFn: () => api("/api/clouds", { guard: isCloudsReport }) });

  const runs = recent.data?.runs ?? [];
  const waiting = [...(approvals.data?.runs ?? []), ...(plans.data?.runs ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const running = runs.filter((run) => run.status === "running" || run.status === "paused");
  const queued = runs.filter((run) => run.status === "queued").reverse();
  const latest = runs.slice(0, RECENT);
  const data = report.data;
  const fresh = recent.isSuccess && !runs.length;
  const user = health.data?.user;
  const project = health.data?.project_dir.split("/").pop();

  const caseList = cases.data?.cases ?? [];
  const drafts = caseList.filter((entry) => entry.status === "draft").length;
  const envList = envs.data?.environments ?? [];
  const native = envList.filter((env) => env.mobile).length;
  const connected = (clouds.data?.clouds ?? []).filter((cloud) => cloud.connected);
  const hybrid = models.data?.modes.hybrid;

  return (
    <section className="page dash" aria-labelledby="dash-title">
      <header className="page-head dash-head">
        <div>
          <h1 id="dash-title">
            {greeting()}
            {user ? `, ${user}` : ""}
          </h1>
          <p className="page-sub">
            {project ?? "This project"}
            {health.data ? (
              <>
                <span className="dash-dot"> · </span>
                {plural(health.data.parallel_runs, "run")} at a time
                {health.data.egress_policy ? (
                  <>
                    <span className="dash-dot"> · </span>egress {health.data.egress_policy}
                  </>
                ) : null}
              </>
            ) : null}
          </p>
        </div>
        <div className="dash-actions">
          <Link href="/explore" className="button">
            <Icon name="compass" size={14} /> Explore an app
          </Link>
          <button type="button" className="button button-primary" onClick={() => setStarting(true)}>
            <Icon name="play" size={13} /> Start a run
          </button>
        </div>
      </header>

      {waiting.length ? (
        <section className="panel dash-waiting" aria-labelledby="dash-waiting-title">
          <div className="dash-waiting-head">
            <h2 id="dash-waiting-title">
              <span className="dash-count">{waiting.length}</span> Waiting for you
            </h2>
            <span className="small muted">These runs are paused until you answer</span>
          </div>
          <ul className="dash-waiting-list">
            {waiting.map((run) => (
              <li key={run.run_id}>
                <span className="dash-kind">{run.status === "needs_plan_approval" ? "plan" : "approval"}</span>
                <Link href={`/runs/${run.run_id}`} className="dash-waiting-title">
                  {run.title ?? run.case_id ?? run.run_id}
                </Link>
                <span className="dash-waiting-detail">
                  {run.status === "needs_plan_approval"
                    ? "A draft plan is ready to review"
                    : run.approval?.reason ?? run.approval?.action ?? (run.approval?.step ? `Step ${run.approval.step} asks before it acts` : "A step asks before it acts")}
                </span>
                <span className="small muted">{relativeTime(run.created_at)}</span>
                <Link href={`/runs/${run.run_id}`} className="button button-small">
                  Review
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {fresh ? (
        <section className="panel dash-start" aria-labelledby="dash-start-title">
          <h2 id="dash-start-title">Get started</h2>
          <ol className="dash-steps">
            <li>
              <Link href="/cases">
                <Icon name="cases" size={16} /> Write a test case
              </Link>
              <span className="small muted">in Gherkin, or import one</span>
            </li>
            <li>
              <Link href="/environments">
                <Icon name="envs" size={16} /> Add an environment
              </Link>
              <span className="small muted">the URL or app to test</span>
            </li>
            <li>
              <button type="button" className="link-button" onClick={() => setStarting(true)}>
                <Icon name="play" size={14} /> Start your first run
              </button>
              <span className="small muted">and watch it live</span>
            </li>
          </ol>
        </section>
      ) : (
        <>
          <section className="dash-stats" aria-label={`Last ${data?.days ?? 7} days`}>
            {data ? (
              <>
                <Stat label={`Runs · ${data.days} days`} value={String(data.current.runs)} delta={runsDelta(data.current, data.previous, data.days)} />
                <Stat label="Pass rate" value={formatPercent(data.current.pass_rate)} delta={passRateDelta(data.current, data.previous)} />
                <Stat label="Median duration" value={formatDuration(data.current.median_duration_s)} delta={durationDelta(data.current, data.previous)} />
                <Stat
                  label="Model cost"
                  value={formatCost(data.current.cost_usd)}
                  delta={{ text: data.current.costed_runs ? `${formatCost((data.current.cost_usd ?? 0) / data.current.costed_runs)} a run` : "no model costs yet", tone: "flat" }}
                />
              </>
            ) : report.isError ? (
              <p className="callout callout-fail">Could not read the numbers: {(report.error as Error).message}</p>
            ) : (
              <p className="muted">Loading…</p>
            )}
          </section>

          <div className="dash-middle">
            <section className="panel dash-chart" aria-labelledby="dash-chart-title">
              <div className="dash-section-head">
                <h2 id="dash-chart-title">
                  Runs per day <span className="muted">· last 14 days</span>
                </h2>
                <ul className="dash-legend" aria-hidden="true">
                  <li>
                    <span className="dash-swatch dash-pass" /> Passed
                  </li>
                  <li>
                    <span className="dash-swatch dash-fail" /> Failed
                  </li>
                  <li>
                    <span className="dash-swatch dash-other" /> Other
                  </li>
                </ul>
              </div>
              {data ? (
                <>
                  <ol className="dash-bars" aria-label="Runs per day, oldest first">
                    {data.daily.map((day) => {
                      const max = chartMax(data.daily);
                      return (
                        <li key={day.date} title={dayTitle(day)} aria-label={dayTitle(day)}>
                          <span className="dash-bar dash-other" style={{ height: `${(day.other / max) * 100}%` }} />
                          <span className="dash-bar dash-fail" style={{ height: `${(day.failed / max) * 100}%` }} />
                          <span className="dash-bar dash-pass" style={{ height: `${(day.passed / max) * 100}%` }} />
                        </li>
                      );
                    })}
                  </ol>
                  <ol className="dash-days" aria-hidden="true">
                    {data.daily.map((day) => (
                      <li key={day.date}>{dayLabel(day.date)}</li>
                    ))}
                  </ol>
                </>
              ) : null}
            </section>

            <section className="panel dash-now" aria-labelledby="dash-now-title">
              <h2 id="dash-now-title">Running now</h2>
              {running.length ? (
                running.map((run) => (
                  <Link key={run.run_id} href={`/runs/${run.run_id}`} className="dash-live">
                    <span className="dash-live-head">
                      <span className={`dash-live-dot${run.status === "paused" ? " dash-live-paused" : ""}`} aria-hidden="true" />
                      <span className="dash-live-title" title={run.title ?? run.case_id ?? run.run_id}>
                        {run.title ?? run.case_id ?? run.run_id}
                      </span>
                      <span className="mono small">
                        {run.current_step ?? run.steps_done}/{run.total_steps ?? "?"}
                      </span>
                    </span>
                    <span className="dash-progress" aria-hidden="true">
                      <span style={{ width: `${run.total_steps ? Math.min(100, ((run.current_step ?? run.steps_done) / run.total_steps) * 100) : 0}%` }} />
                    </span>
                    <span className="small muted">{run.status === "paused" ? "Paused" : (run.current_action ?? "Starting…")}</span>
                  </Link>
                ))
              ) : (
                <p className="muted dash-idle">Nothing is running.</p>
              )}
              {queued.length ? (
                <div className="dash-queue">
                  <span className="dash-label">Queued · {queued.length}</span>
                  <ul>
                    {queued.slice(0, 4).map((run, index) => (
                      <li key={run.run_id}>
                        <Link href={`/runs/${run.run_id}`}>{run.title ?? run.case_id ?? run.run_id}</Link>
                        <span className="small muted">{index === 0 ? "next" : `#${index + 1}`}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <Link href="/runs?status=active" className="dash-more small">
                All active runs →
              </Link>
            </section>
          </div>

          <section className="panel dash-recent" aria-labelledby="dash-recent-title">
            <div className="dash-section-head">
              <h2 id="dash-recent-title">Recent runs</h2>
              <Link href="/runs" className="small">
                All runs →
              </Link>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Status</th>
                    <th scope="col">Case</th>
                    <th scope="col">Mode</th>
                    <th scope="col">Environment</th>
                    <th scope="col" className="num">
                      Duration
                    </th>
                    <th scope="col" className="num">
                      Started
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {latest.map((run) => (
                    <tr key={run.run_id} className="row-clickable" onClick={(event) => {
                      if (!(event.target as HTMLElement).closest("a,button")) navigate(`/runs/${run.run_id}`);
                    }}>
                      <td>
                        <StatusChip status={run.status} />
                      </td>
                      <td>
                        <Link href={`/runs/${run.run_id}`} className="run-title">
                          {run.title ?? run.case_id ?? run.run_id}
                        </Link>
                      </td>
                      <td>
                        <span className={`tag tag-${runMode(run)}`}>{runMode(run)}</span>
                      </td>
                      <td className="mono small">{run.environment ?? run.settings?.environment ?? <span className="muted">–</span>}</td>
                      <td className="num">{formatDuration(runSeconds(run))}</td>
                      <td className="num nowrap muted">{relativeTime(run.started_at ?? run.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section className="dash-setups" aria-label="Your setup">
        <SetupCard href="/cases" icon="cases" label="Cases" value={cases.data ? plural(caseList.length, "case") : "–"} detail={cases.data ? `${plural(drafts, "draft")} · ${plural(cases.data.folders?.length ?? 0, "folder")}` : ""} />
        <SetupCard href="/environments" icon="envs" label="Environments" value={envs.data ? plural(envList.length, "environment") : "–"} detail={envs.data ? `${envList.length - native} web · ${native} native app` : ""} />
        <SetupCard
          href="/models"
          icon="models"
          label="Model"
          value={models.data ? (models.data.model === "auto" ? models.data.provider : models.data.model) : "–"}
          detail={models.data ? `${models.data.model === "auto" ? "Model chosen automatically · " : ""}${hybrid?.available ? "hybrid mode ready" : models.data.modes.pure.available ? "pure mode only" : "not ready"}` : ""}
        />
        <SetupCard
          href="/integrations"
          icon="cloud"
          label="Integrations"
          value={clouds.data ? (connected.length ? plural(connected.length, "cloud") : "No cloud") : "–"}
          detail={clouds.data ? (connected.length ? connected.map((cloud) => cloud.name).join(" · ") : "Connect one to run on real devices") : ""}
        />
      </section>

      {starting ? (
        <StartRunDialog
          onClose={() => setStarting(false)}
          onStarted={(runId, batch) => {
            setStarting(false);
            void client.invalidateQueries({ queryKey: ["runs"] });
            void client.invalidateQueries({ queryKey: ["dashboard"] });
            navigate(batch ? `/runs?batch=${encodeURIComponent(batch)}` : `/runs/${runId}`);
          }}
        />
      ) : null}
    </section>
  );
}
