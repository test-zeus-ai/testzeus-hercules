import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { Field } from "../components/Field";
import { Icon } from "../components/Icon";
import { LiveView } from "../components/LiveView";
import { ApiError, api, patch, post } from "../lib/api";
import { COST_ESTIMATE_NOTE, FINISHED, LEVERS, MISSIONS, formFromSpec, STATE_LABELS, charterCounts, eventLine, findingSummary, isSessionView, sessionCost, sessionTime, timeShare, toReview, type Charter, type ExploreEvent, type SessionView } from "../lib/explore";
import { formatDuration } from "../lib/format";
import { SessionStatus, SeverityBadge, StartExploreDialog, useFindings } from "./Explore";

const FEED_KEPT = 300;

/** One exploration session, live: its explorers' browsers, charters (steerable), the clue and finding feed, coverage gaps. */
export function ExploreSessionScreen({ sessionId }: { sessionId: string }) {
  const client = useQueryClient();
  const since = useRef(0);
  const [feed, setFeed] = useState<ExploreEvent[]>([]);
  const session = useQuery({
    queryKey: ["explore", sessionId],
    queryFn: async ({ signal }) => {
      const view = await api<SessionView>(`/api/explore/${encodeURIComponent(sessionId)}?wait_s=10&since=${since.current}`, { guard: isSessionView, signal });
      const events = view.events ?? [];
      if (events.length) {
        since.current = view.last_seq ?? since.current;
        setFeed((previous) => [...previous, ...events].slice(-FEED_KEPT));
      }
      return view;
    },
    refetchInterval: (query) => (query.state.data && FINISHED.has(query.state.data.status) ? false : 300),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
  const view = session.data;
  const ledger = useFindings();
  const [, navigate] = useLocation();
  const [again, setAgain] = useState(false);
  useEffect(() => {
    if (view && FINISHED.has(view.status)) void client.invalidateQueries({ queryKey: ["explore"], exact: true });
  }, [view?.status, client, view]);

  if (session.isError && !view) {
    const message = (session.error as Error).message;
    return (
      <section className="page">
        <h1>Exploration {sessionId}</h1>
        <p className="callout callout-fail">{message}</p>
        <p>
          A session from an earlier daemon is read-only: <a href={`/api/explore/${encodeURIComponent(sessionId)}/report`}>open its report</a>. <Link href="/explore">Back to Explore</Link>
        </p>
      </section>
    );
  }
  if (!view) return <p className="muted page">Loading the session…</p>;
  const live = !FINISHED.has(view.status);
  const counts = charterCounts(view.charters);
  const running = view.charters.filter((charter) => charter.status === "running" && charter.run_id);
  const stateOf = new Map((ledger.data?.findings ?? []).map((finding) => [finding.id, finding.state]));
  const current = view.findings.map((finding) => ({ ...finding, state: stateOf.get(finding.id) ?? finding.state }));
  const waiting = toReview(current);
  const time = sessionTime(view, formatDuration);
  const missionLabel = MISSIONS.find((mission) => mission.id === view.spec.mission)?.label ?? view.spec.mission;

  return (
    <section className="page explore-session" aria-labelledby="session-title">
      <header className="page-head">
        <div>
          <p className="crumbs">
            <Link href="/explore">Explore</Link> / <span className="mono">{view.id}</span>
          </p>
          <h1 id="session-title">{view.spec.prompt || missionLabel}</h1>
          <p className="page-sub">
            <SessionStatus status={view.status} /> {view.phase_detail || view.message} · {view.spec.base_url || view.spec.environment || view.spec.start_case}
          </p>
        </div>
        <div className="head-actions">
          <a className="button" href={`/api/explore/${encodeURIComponent(view.id)}/report`} target="_blank" rel="noopener noreferrer" aria-disabled={live}>
            <Icon name="link" size={14} /> Report
          </a>
          {live ? (
            <StopButton sessionId={view.id} />
          ) : (
            <button type="button" className="button button-primary" onClick={() => setAgain(true)}>
              <Icon name="compass" size={14} /> Run again
            </button>
          )}
        </div>
      </header>
      {again ? (
        <StartExploreDialog
          initial={formFromSpec(view.spec as SessionView["spec"] & Record<string, unknown>)}
          onClose={() => setAgain(false)}
          onStarted={(id) => {
            setAgain(false);
            navigate(`/explore/${id}`);
          }}
        />
      ) : null}

      <div className="explore-stats" role="group" aria-label="Session at a glance">
        <div className="stat">
          <span className="stat-label">Time</span>
          <span className="stat-value" title={time.title}>
            {time.text}
          </span>
          {live ? (
            <span className="time-bar" aria-hidden="true">
              <span className="time-bar-fill" style={{ transform: `scaleX(${timeShare(view)})` }} />
            </span>
          ) : null}
        </div>
        <div className="stat">
          <span className="stat-label">Charters</span>
          <span className="stat-value">
            {counts.running} running · {counts.queued} queued · {counts.done} done
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Clues → findings</span>
          <span className="stat-value">
            {view.clues} → {view.findings.length}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Model cost</span>
          <span className="stat-value" title={view.cost_estimated ? COST_ESTIMATE_NOTE : undefined}>
            {sessionCost(view)}
          </span>
        </div>
      </div>

      {!live ? (
        <div className={`callout ${waiting ? "callout-warn" : "callout-note"} next-step`} role="status">
          {waiting ? (
            <>
              <span>
                {waiting} finding{waiting === 1 ? "" : "s"} wait for your decision: confirm the real bugs to keep their cases, dismiss the rest.
              </span>
              <Link href="/explore?tab=findings" className="button button-primary button-small">
                Review findings
              </Link>
            </>
          ) : view.findings.length ? (
            <span>Every finding from this session is decided. Kept cases run with your cases; `hercules explore reverify` checks the fixes.</span>
          ) : (
            <span>No findings. That is not a pass: read the coverage gaps below and explore what was not reached.</span>
          )}
        </div>
      ) : (
        <SwarmSize view={view} />
      )}

      {live && running.length ? (
        <section className="panel" aria-labelledby="explorers-title">
          <div className="panel-head">
            <h2 id="explorers-title">Explorers, live</h2>
          </div>
          <div className="explorer-grid">
            {running.map((charter) => (
              <figure key={charter.id} className="explorer-tile">
                <LiveView runId={charter.run_id as string} active paused={false} fallbackRef={null} />
                <figcaption>
                  <Link href={`/runs/${charter.run_id}`}>{charter.title}</Link>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      ) : null}

      <div className="explore-columns">
        <section className="panel" aria-labelledby="findings-title">
          <div className="panel-head">
            <h2 id="findings-title">Findings</h2>
          </div>
          {view.findings.length === 0 ? (
            <p className="empty-note">{live ? "None yet: clues are reproduced before they are reported." : "None. No findings is not a pass: read the coverage gaps below."}</p>
          ) : (
            <ul className="finding-list">
              {current.map((finding) => (
                <SessionFinding key={finding.id} finding={finding} />
              ))}
            </ul>
          )}
          {view.ux_notes.length ? (
            <>
              <h3>UX notes (preferences, not bugs)</h3>
              <ul>
                {view.ux_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
        <section className="panel" aria-labelledby="feed-title">
          <div className="panel-head">
            <h2 id="feed-title">Feed</h2>
          </div>
          {!live && feed.length === 0 ? <p className="empty-note">The live feed is kept only while the daemon that ran this session is up. The report has the whole story.</p> : null}
          <ol className="explore-feed" aria-live="polite">
            {feed
              .map((event) => ({ event, line: eventLine(event) }))
              .filter((item) => item.line !== null)
              .slice(-80)
              .reverse()
              .map(({ event, line }) => (
                <li key={event.seq} className={`feed-${line?.tone}`}>
                  {line?.text}
                </li>
              ))}
          </ol>
        </section>
      </div>

      <Charters view={view} />
      <Coverage view={view} />
    </section>
  );
}

function StopButton({ sessionId }: { sessionId: string }) {
  const client = useQueryClient();
  const stop = useMutation({ mutationFn: () => post(`/api/explore/${encodeURIComponent(sessionId)}/stop`), onSuccess: () => void client.invalidateQueries({ queryKey: ["explore", sessionId] }) });
  return (
    <button type="button" className="button button-danger" onClick={() => stop.mutate()} disabled={stop.isPending}>
      <Icon name="stop" size={14} /> {stop.isPending || stop.isSuccess ? "Stopping…" : "Stop"}
    </button>
  );
}

function SwarmSize({ view }: { view: SessionView }) {
  const client = useQueryClient();
  const [workers, setWorkers] = useState(String(view.workers));
  const [reproducers, setReproducers] = useState(String(view.reproducers));
  const change = useMutation({
    mutationFn: () => patch<SessionView>(`/api/explore/${encodeURIComponent(view.id)}`, { workers: Number(workers), reproducers: Number(reproducers), by: "the tester UI" }, isSessionView),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["explore", view.id] }),
  });
  const live = !FINISHED.has(view.status);
  const dirty = Number(workers) !== view.workers || Number(reproducers) !== view.reproducers;
  return (
    <section className="panel swarm" aria-labelledby="swarm-title">
      <div className="panel-head">
        <h2 id="swarm-title">Swarm</h2>
        <span className="muted">Hercules never changes the size; changes apply to the next charters and are logged.</span>
      </div>
      <form
        className="field-row swarm-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (dirty) change.mutate();
        }}
      >
        <Field id="swarm-workers" label="Explorers">
          <input type="number" min={1} max={64} value={workers} disabled={!live} onChange={(event) => setWorkers(event.target.value)} />
        </Field>
        <Field id="swarm-reproducers" label="Reproducers">
          <input type="number" min={1} max={16} value={reproducers} disabled={!live} onChange={(event) => setReproducers(event.target.value)} />
        </Field>
        <div className="form-actions">
          <button type="submit" className="button" disabled={!live || !dirty || change.isPending}>
            Apply
          </button>
        </div>
      </form>
      {change.isError ? <p className="callout callout-fail">{(change.error as Error).message}</p> : null}
      {view.size_changes.length ? (
        <ul className="audit">
          {view.size_changes.map((item, index) => (
            <li key={index}>
              {item.by}: {item.workers ? `explorers ${item.workers.join(" → ")}` : ""}
              {item.workers && item.reproducers ? ", " : ""}
              {item.reproducers ? `reproducers ${item.reproducers.join(" → ")}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function SessionFinding({ finding }: { finding: SessionView["findings"][number] }) {
  return (
    <li>
      <Link href={`/explore?tab=findings&finding=${encodeURIComponent(finding.id)}`} className="finding-row">
        <SeverityBadge finding={finding} />
        <span className="finding-title">{finding.title}</span>
        {findingSummary(finding) ? <span className="finding-summary">{findingSummary(finding)}</span> : null}
        <span className="finding-meta">
          <span className={`state-tag state-${finding.state}`}>{STATE_LABELS[finding.state] ?? finding.state}</span> · {finding.oracle.replace(/_/g, " ")} · reproduced {finding.reproduced_recorded}
        </span>
      </Link>
    </li>
  );
}

const CHARTER_ORDER: Record<Charter["status"], number> = { running: 0, queued: 1, done: 2, failed: 3, stopped: 4, vetoed: 5 };

function Charters({ view }: { view: SessionView }) {
  const client = useQueryClient();
  const live = !FINISHED.has(view.status);
  const [title, setTitle] = useState("");
  const [area, setArea] = useState("/");
  const [lever, setLever] = useState("inputs");
  const refresh = () => void client.invalidateQueries({ queryKey: ["explore", view.id] });
  const steer = useMutation({ mutationFn: ({ id, action }: { id: string; action: "pin" | "veto" }) => post(`/api/explore/${encodeURIComponent(view.id)}/charters/${encodeURIComponent(id)}`, { action, by: "the tester UI" }), onSuccess: refresh });
  const add = useMutation({
    mutationFn: () => post(`/api/explore/${encodeURIComponent(view.id)}/charters`, { title: title.trim(), area: area.trim() || "/", levers: [lever], pinned: true, by: "the tester UI" }),
    onSuccess: () => {
      setTitle("");
      refresh();
    },
  });
  const sorted = [...view.charters].sort((left, right) => CHARTER_ORDER[left.status] - CHARTER_ORDER[right.status] || right.priority - left.priority);
  return (
    <section className="panel" aria-labelledby="charters-title">
      <div className="panel-head">
        <h2 id="charters-title">Charters</h2>
      </div>
      {live ? (
        <form
          className="field-row charter-add"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim()) add.mutate();
          }}
        >
          <Field id="charter-title" label="Add a charter" help="One sentence: what to push on. It runs next.">
            <input type="text" value={title} placeholder="Try the cart with zero and negative quantities" onChange={(event) => setTitle(event.target.value)} />
          </Field>
          <Field id="charter-area" label="Area (route)">
            <input type="text" value={area} onChange={(event) => setArea(event.target.value)} />
          </Field>
          <Field id="charter-lever" label="Lever">
            <select value={lever} onChange={(event) => setLever(event.target.value)}>
              {LEVERS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </Field>
          <div className="form-actions">
            <button type="submit" className="button" disabled={!title.trim() || add.isPending}>
              <Icon name="plus" size={13} /> Add
            </button>
          </div>
        </form>
      ) : null}
      {add.isError || steer.isError ? <p className="callout callout-fail">{((add.error ?? steer.error) as Error).message}</p> : null}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Status</th>
              <th scope="col">Charter</th>
              <th scope="col">Area</th>
              <th scope="col" className="num">
                Priority
              </th>
              <th scope="col">
                <span className="visually-hidden">Steer</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((charter) => (
              <tr key={charter.id}>
                <td>
                  <span className={`tag tag-charter-${charter.status}`}>{charter.status}</span>
                </td>
                <td>
                  {charter.run_id ? <Link href={`/runs/${charter.run_id}`}>{charter.title}</Link> : charter.title}
                  <span className="run-meta">
                    {charter.kind}
                    {charter.levers.length ? ` · ${charter.levers.join(", ")}` : ""}
                    {charter.origin === "person" ? " · added by a person" : ""}
                    {charter.pinned ? " · pinned" : ""}
                  </span>
                </td>
                <td className="mono">{charter.area}</td>
                <td className="num">{charter.priority.toFixed(2)}</td>
                <td className="cell-actions">
                  {live && charter.status === "queued" ? (
                    <>
                      <button type="button" className="button button-small" disabled={charter.pinned} onClick={() => steer.mutate({ id: charter.id, action: "pin" })}>
                        Run next
                      </button>
                      <button type="button" className="button button-small button-ghost" onClick={() => steer.mutate({ id: charter.id, action: "veto" })}>
                        Never run
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Coverage({ view }: { view: SessionView }) {
  const coverage = view.coverage ?? {};
  return (
    <section className="panel" aria-labelledby="coverage-title">
      <div className="panel-head">
        <h2 id="coverage-title">Coverage gaps</h2>
        <span className="muted">What Hercules did, never a percentage of the app: parts recon never found are not listed.</span>
      </div>
      <p>
        Routes seen: {(coverage.routes_seen ?? []).join(", ") || "none yet"} · {coverage.states_seen ?? 0} page states · {coverage.moves_tried ?? 0} distinct moves.
      </p>
      {coverage.gaps?.length ? (
        <ul>
          {coverage.gaps.map((gap) => (
            <li key={gap.route}>
              <span className="mono">{gap.route}</span>: levers not used: {gap.levers_not_used.join(", ")}
            </li>
          ))}
        </ul>
      ) : null}
      {coverage.charters_not_run?.length ? (
        <>
          <h3>Next charters</h3>
          <ul>
            {coverage.charters_not_run.slice(0, 15).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
