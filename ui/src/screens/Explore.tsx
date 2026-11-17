import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { Field } from "../components/Field";
import { Icon } from "../components/Icon";
import { Modal } from "../components/Modal";
import { api, post } from "../lib/api";
import {
  CASE_DECISIONS,
  CASE_HELP,
  CASE_LABELS,
  COST_ESTIMATE_NOTE,
  EMPTY_START,
  FINISHED,
  formFromSpec,
  isUnfilled,
  LEVERS,
  MISSIONS,
  NEEDS_REASON,
  STATE_LABELS,
  TRANSITIONS,
  findingSummary,
  isFindingList,
  isSessionList,
  isSessionView,
  quickDecisions,
  sessionCost,
  sessionTime,
  severityText,
  specFromForm,
  toReview,
  type CaseDecision,
  type Finding,
  type Precision,
  type Preflight,
  type QuickDecision,
  type SessionView,
  type StartForm,
} from "../lib/explore";
import { formatCost, formatDuration, formatTime } from "../lib/format";
import type { CaseEntry, Environment } from "../lib/types";
import { useDebounced } from "../lib/useDebounced";

export const SESSIONS_POLL_MS = 3000;

export interface FindingsData {
  findings: Finding[];
  precision: Precision;
  /** Who is triaging when nobody says: the project's git user.name. */
  you?: string;
}

/** The project's findings ledger, shared by the tab badge, the ledger and the triage form. */
export function useFindings() {
  return useQuery({ queryKey: ["explore-findings"], queryFn: () => api<FindingsData>("/api/explore/findings", { guard: isFindingList }) });
}

/** The Explore page: sessions (live and past) and the project's findings ledger. */
export function ExploreScreen() {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const tab = new URLSearchParams(search).get("tab") === "findings" ? "findings" : "sessions";
  const [starting, setStarting] = useState(false);
  const findings = useFindings();
  const waiting = findings.data ? toReview(findings.data.findings) : 0;
  return (
    <section className="page" aria-labelledby="explore-title">
      <header className="page-head">
        <div>
          <h1 id="explore-title">
            Explore <span className="tag tag-beta">Beta</span>
          </h1>
          <p className="page-sub">Time-boxed exploratory testing: explorers push on the app, and every clue is reproduced from a fresh start before it becomes a finding.</p>
        </div>
        <button type="button" className="button button-primary" onClick={() => setStarting(true)}>
          <Icon name="compass" size={14} /> Start exploring
        </button>
      </header>
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Show">
          {(["sessions", "findings"] as const).map((item) => (
            <button key={item} type="button" className={`segment${tab === item ? " segment-on" : ""}`} aria-pressed={tab === item} onClick={() => navigate(`${location}${item === "findings" ? "?tab=findings" : ""}`, { replace: true })}>
              {item === "sessions" ? "Sessions" : "Findings"}
              {item === "findings" && waiting ? (
                <span className="count-badge" title={`${waiting} finding(s) wait for your decision`}>
                  {waiting} to review
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>
      {tab === "sessions" ? <SessionList onStart={() => setStarting(true)} /> : <FindingsLedger />}
      {starting ? <StartExploreDialog onClose={() => setStarting(false)} onStarted={(id) => navigate(`/explore/${id}`)} /> : null}
    </section>
  );
}

function SessionList({ onStart }: { onStart: () => void }) {
  const [, navigate] = useLocation();
  const sessions = useQuery({
    queryKey: ["explore"],
    queryFn: () => api<{ sessions: SessionView[] }>("/api/explore", { guard: isSessionList }),
    refetchInterval: (query) => (query.state.data?.sessions.some((item) => !FINISHED.has(item.status)) ? SESSIONS_POLL_MS : 15_000),
  });
  const ledger = useFindings();
  const stateOf = new Map((ledger.data?.findings ?? []).map((finding) => [finding.id, finding.state]));
  const waitingIn = (item: SessionView) => toReview((item.findings ?? []).map((finding) => ({ state: stateOf.get(finding.id) ?? finding.state })));
  if (sessions.isError) return <p className="callout callout-fail">Could not load sessions: {(sessions.error as Error).message}</p>;
  if (sessions.isPending) return <p className="muted">Loading sessions…</p>;
  const list = sessions.data.sessions;
  if (!list.length) {
    return (
      <div className="empty">
        <div className="empty-hero">
          <p className="empty-title">No exploration yet.</p>
          <p>Point Hercules at a test environment, choose how long and how many explorers, and read the reproduced findings when it ends.</p>
          <button type="button" className="button" onClick={onStart}>
            Start the first session
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table className="table runs-table">
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">Session</th>
            <th scope="col">Swarm</th>
            <th scope="col">Started</th>
            <th scope="col" className="num">
              Time
            </th>
            <th scope="col" className="num">
              Findings
            </th>
            <th scope="col" className="num">
              Cost
            </th>
          </tr>
        </thead>
        <tbody>
          {list.map((item) => (
            <tr
              key={item.id}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("a,button")) return;
                navigate(`/explore/${item.id}`);
              }}
            >
              <td>
                <SessionStatus status={item.status} />
              </td>
              <td className="cell-case">
                <Link href={`/explore/${item.id}`} className="run-title">
                  {item.spec?.prompt || MISSIONS.find((mission) => mission.id === item.spec?.mission)?.label || item.spec?.mission}
                </Link>
                <span className="run-meta mono">
                  {item.id} · {item.spec?.base_url || item.spec?.environment || item.spec?.start_case}
                </span>
              </td>
              <td className="nowrap">
                {item.workers} explorer{item.workers === 1 ? "" : "s"}, {item.reproducers} reproducer{item.reproducers === 1 ? "" : "s"}
              </td>
              <td className="nowrap">{formatTime(item.started_at)}</td>
              <td className="num" title={sessionTime(item, formatDuration).title}>
                {sessionTime(item, formatDuration).text}
              </td>
              <td className="num">
                {item.findings?.length ?? 0}
                {waitingIn(item) ? <span className="muted"> · {waitingIn(item)} to review</span> : null}
              </td>
              <td className="num" title={item.cost_estimated ? COST_ESTIMATE_NOTE : undefined}>
                {sessionCost(item)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SESSION_LABEL: Record<string, string> = {
  starting: "Starting",
  recon: "Recon",
  exploring: "Exploring",
  reproducing: "Reproducing",
  stopping: "Stopping",
  finished: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};
const SESSION_CHIP: Record<string, string> = { starting: "queued", recon: "running", exploring: "running", reproducing: "running", stopping: "paused", finished: "passed", failed: "error", stopped: "cancelled" };

export function SessionStatus({ status }: { status: string }) {
  return (
    <span className={`chip chip-${SESSION_CHIP[status] ?? status}`}>
      <span className="chip-dot" aria-hidden="true" />
      {SESSION_LABEL[status] ?? status}
    </span>
  );
}

// --------------------------------------------------------------------------- starting a session

export function StartExploreDialog({ onClose, onStarted, initial }: { onClose: () => void; onStarted: (id: string) => void; initial?: StartForm }) {
  const client = useQueryClient();
  const recent = useQuery({ queryKey: ["explore"], queryFn: () => api<{ sessions: SessionView[] }>("/api/explore", { guard: isSessionList }), enabled: !initial });
  const [form, setForm] = useState<StartForm>(initial ?? EMPTY_START);
  const [prefilled, setPrefilled] = useState(Boolean(initial));
  const [filledFrom, setFilledFrom] = useState<string | null>(null);
  useEffect(() => {
    const last = recent.data?.sessions[0];
    if (prefilled || !last?.spec) return;
    setPrefilled(true);
    setFilledFrom(last.id);
    setForm(formFromSpec(last.spec as SessionView["spec"] & Record<string, unknown>));
  }, [recent.data, prefilled]);
  const set = <K extends keyof StartForm>(key: K, value: StartForm[K]) => setForm((previous) => ({ ...previous, [key]: value }));
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => api<{ environments: Environment[] }>("/api/environments") });
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api<{ cases: CaseEntry[] }>("/api/cases") });
  const built = specFromForm(form);
  const spec = "spec" in built ? built.spec : null;
  const debounced = useDebounced(spec ? JSON.stringify(spec) : "", 400);
  const preflight = useQuery({
    queryKey: ["explore-preflight", debounced],
    queryFn: () => post<Preflight>("/api/explore/preflight", JSON.parse(debounced)),
    enabled: Boolean(debounced),
    retry: false,
  });
  const start = useMutation({
    mutationFn: () => post<SessionView>("/api/explore", { ...spec, by: "the tester UI" }, isSessionView),
    onSuccess: (view) => {
      void client.invalidateQueries({ queryKey: ["explore"] });
      onStarted(view.id);
    },
  });
  const problems = preflight.data?.problems ?? [];
  const hostProblem = preflight.data?.host_problem ?? null;
  const blocked = !spec || (problems.length > 0 && !form.allow_more_workers) || Boolean(hostProblem && !form.production_ok);
  const estimate = preflight.data?.estimate;
  const mission = MISSIONS.find((item) => item.id === form.mission);

  return (
    <Modal title="Start exploring" onClose={onClose} wide>
      <form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) start.mutate();
        }}
      >
        {filledFrom ? (
          <p className="muted prefill-note">
            Filled in from your last session (<span className="mono">{filledFrom}</span>).{" "}
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setFilledFrom(null);
                setForm(EMPTY_START);
              }}
            >
              Start blank
            </button>
          </p>
        ) : null}
        <fieldset className="field">
          <legend>Where</legend>
          <div className="segmented" role="group" aria-label="Where to explore">
            {(
              [
                ["base_url", "Base URL"],
                ["environment", "Environment"],
                ["start_case", "Start case"],
              ] as const
            ).map(([id, label]) => (
              <button key={id} type="button" className={`segment${form.where === id ? " segment-on" : ""}`} aria-pressed={form.where === id} onClick={() => set("where", id)}>
                {label}
              </button>
            ))}
          </div>
        </fieldset>
        {form.where === "base_url" ? (
          <Field id="explore-base-url" label="Base URL" help="A test environment you own: explorers fill forms and click buttons.">
            <input type="url" value={form.base_url} placeholder="http://localhost:3000" onChange={(event) => set("base_url", event.target.value)} />
          </Field>
        ) : form.where === "environment" ? (
          <Field id="explore-env" label="Environment">
            <select value={form.environment} onChange={(event) => set("environment", event.target.value)}>
              <option value="">Choose…</option>
              {(envs.data?.environments ?? []).map((env) => (
                <option key={env.name} value={env.name}>
                  {env.name}
                  {env.base_url ? ` (${env.base_url})` : ""}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field id="explore-case" label="Start case" help="A case that reaches the starting state, such as signing in.">
            <select value={form.start_case} onChange={(event) => set("start_case", event.target.value)}>
              <option value="">Choose…</option>
              {(cases.data?.cases ?? [])
                .filter((item) => item.valid)
                .map((item) => (
                  <option key={item.path} value={item.path}>
                    {item.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
        <div className="field-row">
          <Field id="explore-mission" label="Mission" help={mission?.help}>
            <select value={form.mission} onChange={(event) => set("mission", event.target.value)}>
              {MISSIONS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field id="explore-minutes" label="Time (minutes)">
            <input type="number" min={1} max={1440} value={form.minutes} onChange={(event) => set("minutes", event.target.value)} />
          </Field>
          <Field id="explore-workers" label="Explorers" help="At once. Yours to choose; Hercules never changes it.">
            <input type="number" min={1} max={64} value={form.workers} onChange={(event) => set("workers", event.target.value)} />
          </Field>
          <Field id="explore-reproducers" label="Reproducers" help="Confirm clues at once.">
            <input type="number" min={1} max={16} value={form.reproducers} onChange={(event) => set("reproducers", event.target.value)} />
          </Field>
        </div>
        <Field id="explore-prompt" label="Your own target (optional)" help="Replaces the mission's target, e.g. find ways the cart total can be wrong.">
          <textarea rows={2} value={form.prompt} onChange={(event) => set("prompt", event.target.value)} />
        </Field>
        {form.mission === "changed" ? (
          <Field id="explore-changed" label="What changed" help="Routes or paths, one per line or comma-separated.">
            <textarea rows={2} value={form.changed} onChange={(event) => set("changed", event.target.value)} />
          </Field>
        ) : null}
        <details className="advanced">
          <summary>Levers, limits and scope</summary>
          <fieldset className="field">
            <legend>Levers</legend>
            <div className="field-row">
              {LEVERS.map((lever) => (
                <label key={lever} className="field-check">
                  <input
                    type="checkbox"
                    checked={form.levers.includes(lever)}
                    onChange={(event) => set("levers", event.target.checked ? [...form.levers, lever] : form.levers.filter((item) => item !== lever))}
                  />
                  {lever}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="field-row">
            <Field id="explore-cost" label="Stop at model cost (USD)">
              <input type="number" min={0} step="0.5" value={form.max_cost} onChange={(event) => set("max_cost", event.target.value)} />
            </Field>
            <Field id="explore-deny" label="Never open these paths" help="e.g. /admin, /billing">
              <input type="text" value={form.deny_paths} onChange={(event) => set("deny_paths", event.target.value)} />
            </Field>
          </div>
          <label className="field-check">
            <input type="checkbox" checked={form.production_ok} onChange={(event) => set("production_ok", event.target.checked)} />I own this environment (explore a host that does not look like a test one)
          </label>
        </details>

        <div className="explore-estimate" role="status" aria-live="polite">
          {"error" in built ? (
            <span className={isUnfilled(form) ? "muted" : "field-error"}>{built.error} to see the estimate.</span>
          ) : estimate ? (
            <span>
              {estimate.workers} explorer(s) and {estimate.reproducers} reproducer(s) for {formatDuration(estimate.time_s)}: about {estimate.decisions} model decisions
              {estimate.cost_usd ? `, ~${formatCost(estimate.cost_usd)}` : ""}.{estimate.note ? ` ${estimate.note}.` : ""}
            </span>
          ) : preflight.isError ? (
            <span className="field-error">{(preflight.error as Error).message}</span>
          ) : (
            <span className="muted">Estimating…</span>
          )}
        </div>
        {problems.length ? (
          <div className="callout callout-warn">
            <p>This size is over this machine's guard rail:</p>
            <ul>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
            <label className="field-check">
              <input type="checkbox" checked={form.allow_more_workers} onChange={(event) => set("allow_more_workers", event.target.checked)} />
              Start anyway at this size
            </label>
          </div>
        ) : null}
        {hostProblem ? (
          <div className="callout callout-warn">
            <p>{hostProblem}</p>
            <label className="field-check">
              <input type="checkbox" checked={form.production_ok} onChange={(event) => set("production_ok", event.target.checked)} />
              This host is mine to test: explorers may fill and submit its forms
            </label>
          </div>
        ) : null}
        {start.isError ? <p className="callout callout-fail">{(start.error as Error).message}</p> : null}
        <div className="form-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button-primary" disabled={blocked || start.isPending}>
            <Icon name="play" size={14} /> {start.isPending ? "Starting…" : "Start"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// --------------------------------------------------------------------------- the findings ledger

const OPEN_STATES = new Set(["new", "confirmed", "in_tracker", "verify_fixed"]);
const SEVERITY_RANK: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3 };

/** Findings waiting for a decision first, then the other open ones, then the decided; worst severity first within each. */
function reviewOrder(finding: Finding): number {
  const group = finding.state === "new" ? 0 : OPEN_STATES.has(finding.state) ? 1 : 2;
  return group * 10 + (SEVERITY_RANK[finding.severity ?? finding.proposed_severity] ?? 4);
}

export function FindingsLedger() {
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const selected = params.get("finding");
  const [state, setState] = useState(selected ? "all" : "open");
  const [caseFilter, setCaseFilter] = useState("");
  const select = (id: string | null) => {
    const next = new URLSearchParams(search);
    if (id) next.set("finding", id);
    else next.delete("finding");
    navigate(`${location}?${next.toString()}`, { replace: true });
  };
  const findings = useFindings();
  const scrolled = useRef<string | null>(null);
  const scrollTo = (node: HTMLElement | null) => {
    if (node && selected && scrolled.current !== selected) {
      scrolled.current = selected;
      node.scrollIntoView?.({ block: "nearest" });
    }
  };
  if (findings.isError) return <p className="callout callout-fail">Could not load findings: {(findings.error as Error).message}</p>;
  if (findings.isPending) return <p className="muted">Loading findings…</p>;
  const all = findings.data.findings;
  const open = OPEN_STATES;
  const visible = all
    .filter((item) => (state === "open" ? open.has(item.state) : state === "review" ? item.state === "new" : state === "all" ? true : item.state === state))
    .filter((item) => !caseFilter || (item.case ?? "proposed") === caseFilter)
    .sort((a, b) => reviewOrder(a) - reviewOrder(b));
  const precision = findings.data.precision;
  const chosen = all.find((item) => item.id === selected) ?? null;
  const decided = precision.confirmed + precision.dismissed;
  return (
    <div className="explore-findings">
      <div className="toolbar">
        <div className="field-inline">
          <select aria-label="Filter findings by state" value={state} onChange={(event) => setState(event.target.value)}>
            <option value="open">Open</option>
            <option value="review">To review</option>
            <option value="all">All</option>
            {Object.entries(STATE_LABELS).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
          <select aria-label="Filter findings by case" value={caseFilter} onChange={(event) => setCaseFilter(event.target.value)}>
            <option value="">Any case</option>
            {CASE_DECISIONS.map((decision) => (
              <option key={decision} value={decision}>
                {CASE_LABELS[decision]} cases
              </option>
            ))}
          </select>
        </div>
        <p className="muted" title="Of the findings people decided on, the share that were real bugs. It tells you how much to trust the next batch.">
          {decided ? `${Math.round((precision.precision ?? 0) * 100)}% real bugs (${precision.confirmed} of ${decided} decided)` : "Nothing decided yet"} · {precision.untriaged} to review
        </p>
      </div>
      {visible.length === 0 ? (
        <p className="empty-note">
          {state === "review" ? "Nothing waits for review." : `No findings${state === "open" ? " open" : " here"}.`} No findings is not a pass: check each session's coverage gaps.
        </p>
      ) : (
        <ul className="finding-list">
          {visible.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                id={`finding-${item.id}`}
                ref={selected === item.id ? scrollTo : undefined}
                className={`finding-row${selected === item.id ? " finding-row-on" : ""}${OPEN_STATES.has(item.state) ? "" : " finding-row-closed"}`} onClick={() => select(item.id === selected ? null : item.id)} aria-expanded={selected === item.id}>
                <SeverityBadge finding={item} />
                <span className="finding-title">{item.title}</span>
                {findingSummary(item) ? <span className="finding-summary">{findingSummary(item)}</span> : null}
                <span className="finding-meta">
                  <span className={`state-tag state-${item.state}`}>{STATE_LABELS[item.state] ?? item.state}</span>
                  {item.regression ? " · came back" : ""} · case {(CASE_LABELS[item.case ?? "proposed"] ?? "Proposed").toLowerCase()} · {item.oracle.replace(/_/g, " ")} · seen in {item.sessions.length} session
                  {item.sessions.length === 1 ? "" : "s"} · <span className="mono">{item.id}</span>
                </span>
              </button>
              {selected === item.id && chosen ? <FindingDetail finding={chosen} you={findings.data.you} /> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SeverityBadge({ finding }: { finding: Pick<Finding, "severity" | "proposed_severity"> }) {
  const shown = severityText(finding);
  return (
    <span className={`severity severity-${shown.level.toLowerCase()}${shown.confirmed ? "" : " severity-proposed"}`} title={shown.confirmed ? "Severity confirmed by a person" : "The model's proposal: a person confirms it"}>
      {shown.label}
    </span>
  );
}

const NAME_KEY = "hercules.triage.by";

function savedName(): string {
  try {
    return window.localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberName(name: string) {
  try {
    window.localStorage.setItem(NAME_KEY, name);
  } catch {
    // a private window: the name is asked again next time
  }
}

interface TriageBody {
  state?: string | null;
  severity?: string | null;
  tracker?: string | null;
  duplicate_of?: string | null;
  case?: CaseDecision | null;
  reason: string;
}

export function FindingDetail({ finding, you }: { finding: Finding; you?: string }) {
  const client = useQueryClient();
  const [by, setBy] = useState(() => savedName() || you || "");
  const [pending, setPending] = useState<QuickDecision | null>(null);
  const [quickReason, setQuickReason] = useState("");
  const [quickDuplicate, setQuickDuplicate] = useState("");
  const [nextState, setNextState] = useState("");
  const [severity, setSeverity] = useState("");
  const [reason, setReason] = useState("");
  const [tracker, setTracker] = useState("");
  const [duplicateOf, setDuplicateOf] = useState("");
  const [caseDecision, setCaseDecision] = useState<CaseDecision | "">("");
  const [copied, setCopied] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const ledger = useFindings();
  const summary = findingSummary(finding);
  const others = (ledger.data?.findings ?? [])
    .filter((item) => item.id !== finding.id && item.state !== "duplicate")
    .map((item) => ({ item, same: findingSummary(item) === summary ? 0 : item.oracle === finding.oracle ? 1 : 2 }))
    .sort((a, b) => a.same - b.same)
    .slice(0, 40);
  useEffect(() => {
    setCopied(null);
    setSaved(null);
    setPending(null);
  }, [finding.id]);
  useEffect(() => {
    if (!by && you) setBy(you);
  }, [you, by]);
  const triage = useMutation({
    mutationFn: (body: TriageBody) => {
      rememberName(by.trim());
      return post<Finding>(`/api/explore/findings/${encodeURIComponent(finding.id)}/triage`, { by: by.trim(), state: null, severity: null, tracker: null, duplicate_of: null, case: null, ...body });
    },
    onSuccess: (result) => {
      setNextState("");
      setSeverity("");
      setReason("");
      setTracker("");
      setDuplicateOf("");
      setCaseDecision("");
      setPending(null);
      setQuickReason("");
      setQuickDuplicate("");
      setSaved(`Saved: ${STATE_LABELS[result?.state ?? ""] ?? result?.state ?? "triaged"}, case ${(CASE_LABELS[result?.case ?? "proposed"] ?? "").toLowerCase()}.`);
      void client.invalidateQueries({ queryKey: ["explore-findings"] });
      void client.invalidateQueries({ queryKey: ["explore"] });
    },
  });
  const copyIssue = async (kind: string) => {
    const issue = await api<{ title: string; body: string }>(`/api/explore/findings/${encodeURIComponent(finding.id)}/issue?tracker=${kind}`);
    try {
      await navigator.clipboard.writeText(`${issue.title}\n\n${issue.body}`);
      setCopied(kind);
    } catch {
      setCopied(null);
    }
  };
  const decide = (choice: QuickDecision) => {
    if (choice.needsReason || !by.trim()) {
      setPending(choice);
      return;
    }
    triage.mutate({ state: choice.state, reason: "" });
  };
  const moves = TRANSITIONS[finding.state] ?? [];
  const needsReason = (NEEDS_REASON.has(nextState) || caseDecision === "rejected") && !reason.trim();
  const nothing = !nextState && !severity && !tracker.trim() && !caseDecision;
  const decision = finding.case ?? "proposed";
  const quickBlocked = !by.trim() || (pending?.needsReason && !quickReason.trim()) || (pending?.id === "duplicate" && !quickDuplicate.trim());
  return (
    <div className="finding-detail panel">
      <div className="decide" role="group" aria-label="Decide this finding">
        <span className="decide-label">Decide</span>
        {quickDecisions(finding.state).map((choice) => (
          <button
            key={choice.id}
            type="button"
            className={`button button-small${choice.tone === "primary" ? " button-primary" : ""}${pending?.id === choice.id ? " button-on" : ""}`}
            title={choice.effect}
            disabled={triage.isPending}
            onClick={() => decide(choice)}
          >
            {choice.label}
          </button>
        ))}
        {saved ? (
          <span className="decide-saved" role="status">
            {saved}
          </span>
        ) : null}
      </div>
      {pending ? (
        <form
          className="form decide-confirm"
          onSubmit={(event) => {
            event.preventDefault();
            if (!quickBlocked) triage.mutate({ state: pending.state, reason: quickReason.trim(), duplicate_of: pending.id === "duplicate" ? quickDuplicate.trim() : null });
          }}
        >
          <p className="muted">{pending.effect}</p>
          <div className="field-row">
            {pending.id === "duplicate" ? (
              <Field id={`quick-dup-${finding.id}`} label="Duplicate of" required help="Findings with the same message come first.">
                <select value={quickDuplicate} onChange={(event) => setQuickDuplicate(event.target.value)} autoFocus>
                  <option value="">Choose the original…</option>
                  {others.map(({ item, same }) => (
                    <option key={item.id} value={item.id}>
                      {same === 0 ? "Same message: " : ""}
                      {item.title} ({STATE_LABELS[item.state] ?? item.state}, {item.id})
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
            {pending.needsReason ? (
              <Field id={`quick-reason-${finding.id}`} label="Why" required help="Kept in the finding's history; a rejected case is never proposed again.">
                <input type="text" value={quickReason} onChange={(event) => setQuickReason(event.target.value)} autoFocus={pending.id !== "duplicate"} />
              </Field>
            ) : null}
            <Field id={`quick-by-${finding.id}`} label="Your name" required help={you && by === you ? "From git user.name." : undefined}>
              <input type="text" value={by} onChange={(event) => setBy(event.target.value)} />
            </Field>
          </div>
          <div className="form-actions">
            <button type="button" className="button button-ghost button-small" onClick={() => setPending(null)}>
              Cancel
            </button>
            <button type="submit" className="button button-primary button-small" disabled={Boolean(quickBlocked) || triage.isPending}>
              {pending.label}
            </button>
          </div>
        </form>
      ) : null}
      {triage.isError ? <p className="callout callout-fail">{(triage.error as Error).message}</p> : null}
      <dl className="kv">
        <dt>Expected</dt>
        <dd>{finding.expected || "–"}</dd>
        <dt>Actual</dt>
        <dd>{finding.actual || "–"}</dd>
        <dt>Reproduced</dt>
        <dd>
          {finding.reproduced_recorded || "–"} replays of the recording, {finding.reproduced_interpreted || "–"} from the steps
          {finding.flaky !== "none" ? ` (flaky: ${finding.flaky})` : ""}
        </dd>
        <dt>Case</dt>
        <dd>
          {CASE_LABELS[decision]}: <span className="mono">{finding.case_path ?? "not written"}</span>
          <span className="muted"> · {CASE_HELP[decision]}</span>
        </dd>
        {finding.severity_reason ? (
          <>
            <dt>Why this severity</dt>
            <dd>{finding.severity_reason}</dd>
          </>
        ) : null}
        {finding.tracker ? (
          <>
            <dt>Tracker</dt>
            <dd className="mono">{finding.tracker}</dd>
          </>
        ) : null}
        {finding.run_ids.length ? (
          <>
            <dt>Evidence</dt>
            <dd className="finding-runs">
              {finding.run_ids.slice(-3).map((runId) => (
                <Link key={runId} href={`/runs/${runId}`} className="mono finding-run">
                  {runId}
                </Link>
              ))}
            </dd>
          </>
        ) : null}
      </dl>
      <h3>Steps to reproduce</h3>
      <ol className="finding-steps">
        {finding.steps.map((step, index) => (
          <li key={index}>{step}</li>
        ))}
      </ol>
      <div className="form-actions form-actions-start">
        {(["github", "jira", "linear", "markdown"] as const).map((kind) => (
          <button key={kind} type="button" className="button button-small button-ghost" onClick={() => void copyIssue(kind)}>
            <Icon name="copy" size={13} /> {copied === kind ? "Copied" : `Copy as ${kind === "markdown" ? "Markdown" : kind[0].toUpperCase() + kind.slice(1)} issue`}
          </button>
        ))}
      </div>
      <details className="advanced">
        <summary>More options: state, severity, tracker key, case</summary>
        <form
          className="form triage-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (by.trim() && !needsReason && !nothing) triage.mutate({ state: nextState || null, severity: severity || null, tracker: tracker.trim() || null, duplicate_of: duplicateOf.trim() || null, case: caseDecision || null, reason: reason.trim() });
          }}
        >
          <div className="field-row">
            <Field id={`triage-state-${finding.id}`} label="State">
              <select value={nextState} onChange={(event) => setNextState(event.target.value)}>
                <option value="">No change ({STATE_LABELS[finding.state] ?? finding.state})</option>
                {moves.map((item) => (
                  <option key={item} value={item}>
                    {STATE_LABELS[item] ?? item}
                  </option>
                ))}
              </select>
            </Field>
            <Field id={`triage-severity-${finding.id}`} label="Severity" help={finding.severity ? undefined : `The model proposed ${finding.proposed_severity}; confirm or change it.`}>
              <select value={severity} onChange={(event) => setSeverity(event.target.value)}>
                <option value="">No change ({finding.severity ?? `${finding.proposed_severity} proposed`})</option>
                {["P0", "P1", "P2", "P3"].map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </Field>
            <Field id={`triage-tracker-${finding.id}`} label="Tracker key" help="Filing it moves it to In tracker.">
              <input type="text" value={tracker} placeholder="ACME-123" onChange={(event) => setTracker(event.target.value)} />
            </Field>
            <Field id={`triage-case-${finding.id}`} label="Case" help={caseDecision ? CASE_HELP[caseDecision] : "Set by the state unless you choose: confirming keeps it, dismissing rejects it, closing a fix makes it a regression test."}>
              <select value={caseDecision} onChange={(event) => setCaseDecision(event.target.value as CaseDecision | "")}>
                <option value="">No change ({CASE_LABELS[decision].toLowerCase()})</option>
                {CASE_DECISIONS.filter((item) => item !== decision).map((item) => (
                  <option key={item} value={item}>
                    {CASE_LABELS[item]}
                  </option>
                ))}
              </select>
            </Field>
            <Field id={`triage-by-${finding.id}`} label="Your name" required>
              <input type="text" value={by} onChange={(event) => setBy(event.target.value)} />
            </Field>
          </div>
          {nextState === "duplicate" ? (
            <Field id={`triage-dup-${finding.id}`} label="Duplicate of (finding id)">
              <input type="text" value={duplicateOf} onChange={(event) => setDuplicateOf(event.target.value)} />
            </Field>
          ) : null}
          <Field id={`triage-reason-${finding.id}`} label="Reason" required={NEEDS_REASON.has(nextState) || caseDecision === "rejected"} error={needsReason ? (caseDecision === "rejected" && !NEEDS_REASON.has(nextState) ? "Say why: a rejected case is never proposed again" : "Say why: dismissals feed the real-bug rate") : undefined}>
            <input type="text" value={reason} onChange={(event) => setReason(event.target.value)} />
          </Field>
          <div className="form-actions">
            <button type="submit" className="button button-primary button-small" disabled={!by.trim() || needsReason || nothing || triage.isPending}>
              Save triage
            </button>
          </div>
        </form>
      </details>
      {finding.audit.length ? (
        <details className="advanced">
          <summary>History ({finding.audit.length})</summary>
          <ul className="audit">
            {finding.audit.map((event, index) => (
              <li key={index}>
                {formatTime(event.at)} · {event.by}: {event.field} {event.before ?? "–"} → {event.after ?? "–"}
                {event.reason ? ` (${event.reason})` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
