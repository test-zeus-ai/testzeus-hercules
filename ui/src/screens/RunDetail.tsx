import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState, type KeyboardEvent } from "react";
import { Link, useLocation } from "wouter";
import { ApprovalCard, type Decision } from "../components/ApprovalCard";
import { CloudRunsPanel } from "../components/CloudRunsPanel";
import { DebugToolbar } from "../components/DebugToolbar";
import { DecisionInspector } from "../components/DecisionInspector";
import { ExportDialog } from "../components/ExportDialog";
import { LiveView } from "../components/LiveView";
import { PlanPanel } from "../components/PlanPanel";
import { PlanReview, type PlanDecision } from "../components/PlanReview";
import { QualityPanel } from "../components/QualityPanel";
import { RunOnCloudDialog } from "../components/RunOnCloudDialog";
import { SecurityPanel } from "../components/SecurityPanel";
import { StartRunDialog } from "../components/StartRunDialog";
import { Icon } from "../components/Icon";
import { StatusChip } from "../components/StatusChip";
import { api, evidenceName, evidenceUrl, post, remove } from "../lib/api";
import { agentName, deleteRunPrompt, engineLabels, formatCost, formatDuration, formatMs, formatTime, runSeconds, shortPath } from "../lib/format";
import type { DebugAction } from "../lib/control";
import { mergeResult, orderedSteps, stepLabel, type ApprovalRequest, type PlannedStep, type TimelineStep } from "../lib/timeline";
import { FINAL_STATUSES, type ApprovalView, type CaseEntry, type CloudRun, type DecisionView, type RerunScope, type RunView, type TestCase } from "../lib/types";
import { useRunTimeline } from "../lib/useRunTimeline";
import { pathUrl } from "../lib/caseTree";
import { isCaseList, isRunCase, isRunView } from "../lib/guards";
import type { PlanView } from "../lib/plan";

type ShotKind = "som" | "plain";

const OPEN_STATUSES: ReadonlySet<string> = new Set(["running", "paused", "needs_approval"]);

function approvalFromView(view: ApprovalView | null | undefined): ApprovalRequest | null {
  if (!view) return null;
  return {
    id: view.id ?? null,
    kind: view.kind ?? "question",
    step: view.step ?? null,
    reason: view.reason ?? "",
    action: view.action ?? null,
    target: view.target ?? null,
    screenshot: view.screenshot ?? null,
    allowEdit: view.allow_edit !== false,
    timeoutS: view.timeout_s ?? null,
  };
}

/** "1st", "2nd", "3rd", "4th", "11th", "22nd" … */
export function ordinal(value: number): string {
  const tens = value % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[value % 10] ?? "th";
  return `${value}${suffix}`;
}

function StepTimeline({ steps, selected, current, onSelect }: { steps: TimelineStep[]; selected: number | null; current: number | null; onSelect: (index: number) => void }) {
  const onKey = (event: KeyboardEvent<HTMLOListElement>) => {
    if (selected === null || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
    event.preventDefault();
    const next = Math.min(steps.length, Math.max(1, selected + (event.key === "ArrowDown" ? 1 : -1)));
    onSelect(next);
    (event.currentTarget.querySelector(`[data-step="${next}"]`) as HTMLElement | null)?.focus();
  };
  return (
    <ol className="timeline" aria-label="Steps" onKeyDown={onKey}>
      {steps.map((step) => (
        <li key={step.index} className={`tl-item tl-${step.status}${step.index === current ? " tl-current" : ""}${step.index === selected ? " tl-selected" : ""}`}>
          <button type="button" className="tl-button" data-step={step.index} aria-pressed={step.index === selected} aria-current={step.index === current ? "step" : undefined} onClick={() => onSelect(step.index)}>
            <span className="tl-marker" aria-hidden="true">
              {step.status === "passed" ? <Icon name="check" size={12} /> : step.status === "failed" || step.status === "error" ? <Icon name="cross" size={12} /> : step.index}
            </span>
            <span className="tl-body">
              <span className="tl-action">{stepLabel(step)}</span>
              <span className="tl-meta">
                <StatusChip status={step.status} />
                {step.durationMs !== null ? <span>{formatMs(step.durationMs)}</span> : null}
                {step.decisions > 0 ? <span>{step.decisions} decision{step.decisions === 1 ? "" : "s"}</span> : null}
                {step.attempts > 0 ? <span>{step.attempts} retr{step.attempts === 1 ? "y" : "ies"}</span> : null}
                {step.sequence !== null ? <span title="A field this step depends on was set first">ran {ordinal(step.sequence)}</span> : null}
              </span>
              {step.notes.length ? (
                <span className="tl-notes">
                  {step.notes.map((note) => (
                    <span key={note} className="muted">
                      {note}
                    </span>
                  ))}
                </span>
              ) : null}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function Screenshot({ runId, step, live, liveSeq }: { runId: string; step: TimelineStep | null; live: boolean; liveSeq: number | null }) {
  const [kind, setKind] = useState<ShotKind>("som");
  const shots = step?.screenshots ?? [];
  const som = shots.find((ref) => ref.endsWith("som.jpg"));
  const plain = shots.find((ref) => ref.endsWith("plain.jpg"));
  const ref = kind === "som" ? (som ?? plain) : (plain ?? som);
  const url = ref ? `${evidenceUrl(runId, ref)}${liveSeq !== null ? `?v=${liveSeq}` : ""}` : null;
  return (
    <figure className="shot">
      <figcaption className="shot-head">
        <span>
          {live ? <span className="live-dot" aria-hidden="true" /> : null}
          {live ? "Latest screenshot" : "Screenshot"}
          {step ? <span className="muted"> · step {step.index}</span> : null}
        </span>
        <span className="segmented segmented-small" role="group" aria-label="Screenshot kind">
          <button type="button" className={`segment${kind === "som" ? " segment-on" : ""}`} aria-pressed={kind === "som"} onClick={() => setKind("som")} disabled={!som}>
            Marked
          </button>
          <button type="button" className={`segment${kind === "plain" ? " segment-on" : ""}`} aria-pressed={kind === "plain"} onClick={() => setKind("plain")} disabled={!plain}>
            Plain
          </button>
        </span>
      </figcaption>
      {url ? (
        <a href={url} target="_blank" rel="noopener" className="shot-frame" title="Open full size">
          <img src={url} alt={`${kind === "som" ? "Screenshot with numbered element marks" : "Screenshot"} of step ${step?.index ?? ""}`} />
        </a>
      ) : (
        <div className="shot-empty">{step?.status === "pending" ? "This step has not run yet." : step?.status === "skipped" ? "This step did not run." : "No screenshot for this step."}</div>
      )}
    </figure>
  );
}

function EvidenceLinks({ run }: { run: RunView }) {
  const result = run.result;
  const named: Array<[string, string]> = [["run.json", "run.json"]];
  if (result) {
    for (const [label, ref] of Object.entries(result.evidence)) {
      // Only files inside the run directory are served; anything else is not linkable.
      if (ref && (ref.startsWith(`hercules://runs/${run.run_id}/`) || !/^(\/|[a-z]+:)/i.test(ref))) named.push([label.replace("_md", "").replace("_", " "), ref]);
    }
  }
  named.push(["case.json", "case.json"], ["worker.log", "worker.log"]);
  return (
    <ul className="evidence-links">
      {named.map(([label, ref]) => (
        <li key={label}>
          <a href={evidenceUrl(run.run_id, ref)} target="_blank" rel="noopener">
            <Icon name="link" size={13} /> {label}
          </a>
        </li>
      ))}
    </ul>
  );
}

export function RunDetailScreen({ runId }: { runId: string }) {
  const client = useQueryClient();
  const [, navigate] = useLocation();
  const [pinned, setPinned] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [cloudDialog, setCloudDialog] = useState(false);
  const [newCloudRun, setNewCloudRun] = useState<string | null>(null);
  const [rerunScope, setRerunScope] = useState<RerunScope | null>(null);
  const [tab, setTab] = useState<"run" | "plan">("run");
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<RunView>(`/api/runs/${encodeURIComponent(runId)}`, { guard: isRunView }),
    refetchInterval: (query) => (query.state.data && FINAL_STATUSES.has(query.state.data.status) ? false : 2500),
  });
  const caseQuery = useQuery({
    queryKey: ["run-case", runId],
    queryFn: () => api<{ test_case: TestCase; source?: { kind?: string; path?: string } | null }>(`/api/runs/${encodeURIComponent(runId)}/case`, { guard: isRunCase }),
    staleTime: Infinity,
  });
  const savedCases = useQuery({ queryKey: ["cases"], queryFn: () => api<{ cases: CaseEntry[] }>("/api/cases", { guard: isCaseList }) });
  const sourcePath = caseQuery.data?.source?.kind === "case_path" ? caseQuery.data.source.path : undefined;
  const sameId = (savedCases.data?.cases ?? []).filter((entry) => entry.valid && entry.id === caseQuery.data?.test_case.id);
  const savedCase = sourcePath ? savedCases.data?.cases.find((entry) => entry.path === sourcePath) : sameId.length === 1 ? sameId[0] : undefined;
  const plan: PlannedStep[] | undefined = caseQuery.isPending ? undefined : (caseQuery.data?.test_case.steps ?? []);
  const onFinished = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["run", runId] });
    void client.invalidateQueries({ queryKey: ["runs"] });
  }, [client, runId]);
  const finished = run.data ? FINAL_STATUSES.has(run.data.status) : false;
  const { timeline: live, streamError } = useRunTimeline(runId, plan, onFinished, finished);
  const timeline = useMemo(() => (run.data?.result ? mergeResult(live, run.data.result) : live), [live, run.data?.result]);
  const steps = useMemo(() => orderedSteps(timeline), [timeline]);

  const following = pinned === null;
  const failedStep = steps.find((step) => step.status === "failed" || step.status === "error");
  const selectedIndex = pinned ?? timeline.currentStep ?? timeline.latestScreenshot?.step ?? (finished ? (failedStep?.index ?? steps.at(-1)?.index) : null) ?? (steps.length ? 1 : null);
  const selected = steps.find((step) => step.index === selectedIndex) ?? null;
  const decisionCount = selected?.decisions ?? 0;
  const decisions = useQuery({
    queryKey: ["decisions", runId, selectedIndex, decisionCount],
    queryFn: () => api<{ decisions: DecisionView[] }>(`/api/runs/${encodeURIComponent(runId)}/steps/${selectedIndex}/decisions`),
    enabled: selectedIndex !== null && selected?.status !== "pending",
    placeholderData: (previous) => previous,
  });

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post<RunView>(`/api/runs/${encodeURIComponent(runId)}/${path}`, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["run", runId] });
      void client.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const drop = useMutation({
    mutationFn: () => remove<{ run_id: string; removed: boolean }>(`/api/runs/${encodeURIComponent(runId)}`),
    onSuccess: () => {
      client.removeQueries({ queryKey: ["run", runId] });
      void client.invalidateQueries({ queryKey: ["runs"] });
      navigate("/runs");
    },
  });
  const debug = useMutation({
    mutationFn: (action: DebugAction) => post<RunView>(`/api/runs/${encodeURIComponent(runId)}/control`, { action }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const decide = useMutation({
    mutationFn: ({ decision, stepText, approvalId }: { decision: Decision; stepText?: string; approvalId?: string | null }) =>
      post<RunView>(`/api/runs/${encodeURIComponent(runId)}/approve`, {
        decision,
        ...(decision === "edit" ? { step_text: stepText } : {}),
        // Names the question being answered, so a stale answer cannot settle a newer one.
        ...(approvalId ? { approval_id: approvalId } : {}),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const runStatus = run.data?.status;
  const planQuery = useQuery({
    queryKey: ["plan", runId, runStatus],
    queryFn: () => api<PlanView>(`/api/runs/${encodeURIComponent(runId)}/plan`),
    enabled: tab === "plan" || runStatus === "needs_plan_approval",
    retry: false,
  });
  const decidePlan = useMutation({
    mutationFn: (decision: PlanDecision) => post<RunView>(`/api/runs/${encodeURIComponent(runId)}/approve`, decision),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["run", runId] });
      void client.invalidateQueries({ queryKey: ["plan", runId] });
    },
  });
  const debugMutate = debug.mutate;
  const onDebug = useCallback((action: DebugAction) => debugMutate(action), [debugMutate]);

  if (run.isError) {
    return (
      <section className="page">
        <p className="crumbs">
          <Link href="/runs">Runs</Link>
        </p>
        <p className="callout callout-fail">Could not load run {runId}: {(run.error as Error).message}</p>
      </section>
    );
  }
  const view = run.data;
  const result = view?.result;
  const liveShot = following && !finished && timeline.latestScreenshot ? timeline.latestScreenshot : null;
  const shotStep = liveShot ? (steps.find((step) => step.index === liveShot.step) ?? selected) : selected;
  const status = view ? (timeline.runStatus && !finished ? timeline.runStatus : view.status) : null;
  const isDraft = view?.status === "needs_approval" && Boolean(view.draft_steps);
  const open = status !== null && OPEN_STATUSES.has(status) && !isDraft;
  const approval = status === "needs_approval" && !isDraft ? (timeline.control.approval ?? approvalFromView(view?.approval)) : null;
  const pausedAt = status === "paused" ? (timeline.control.paused ?? (view?.paused ? { step: view.paused.step ?? null, where: view.paused.where ?? null, action: view.paused.action ?? null } : null)) : null;
  const canRunOnCloud = finished && view?.status === "passed" && Boolean(result?.automation);
  const native = Boolean(result?.automation?.app);
  const onCloudStarted = (started: CloudRun) => {
    setCloudDialog(false);
    setNewCloudRun(started.id);
    client.setQueryData<{ cloud_runs: CloudRun[] }>(["cloud-runs", runId], (old) => ({ cloud_runs: [started, ...(old?.cloud_runs ?? []).filter((item) => item.id !== started.id)] }));
    void client.invalidateQueries({ queryKey: ["cloud-runs", runId] });
  };
  const approvalStep = approval?.step ? steps.find((step) => step.index === approval.step) : undefined;
  const engines = view ? engineLabels(view) : null;

  return (
    <section className="page page-run" aria-labelledby="run-title">
      <p className="crumbs">
        <Link href="/runs">Runs</Link> <Icon name="chevron" size={12} /> <span className="mono">{runId}</span>
      </p>
      <header className="run-head">
        <div className="run-head-main">
          <h1 id="run-title">{view?.title ?? view?.case_id ?? runId}</h1>
          <div className="run-chips">
            {status ? <StatusChip status={status} /> : null}
            {engines ? (
              <span className={`tag tag-labelled tag-${engines.mode}`} title={engines.requested ? `Asked for ${engines.requested}; the run chose ${engines.mode}` : undefined}>
                <span className="tag-key">Mode</span> {engines.mode}
                {engines.requested ? <span className="muted"> (from {engines.requested})</span> : null}
              </span>
            ) : null}
            {engines ? (
              <span className={`tag tag-labelled${engines.deciderKnown ? "" : " tag-pending"}`}>
                <span className="tag-key">Decider</span> {engines.decider}
              </span>
            ) : null}
            <span className="tag">egress {result?.engines.egress ?? view?.egress ?? "default"}</span>
            {result?.engines.llm ? <span className="tag mono">{result.engines.llm}</span> : null}
          </div>
        </div>
        <div className="run-actions">
          {savedCase ? (
            <Link href={`/cases/${pathUrl(savedCase.name)}`} className="button" title={`Edit ${savedCase.name}`}>
              <Icon name="cases" size={13} /> Edit case
            </Link>
          ) : null}
          {isDraft ? (
            <>
              <button type="button" className="button button-primary" onClick={() => act.mutate({ path: "approve" })} disabled={act.isPending}>
                <Icon name="check" size={14} /> Approve and run
              </button>
              <button type="button" className="button button-ghost" onClick={() => act.mutate({ path: "approve", body: { decision: "reject" } })} disabled={act.isPending}>
                Reject
              </button>
            </>
          ) : null}
          {view?.resumable ? (
            <button type="button" className="button button-primary" onClick={() => act.mutate({ path: "resume" })} disabled={act.isPending} title="Continue from the last completed step">
              <Icon name="play" size={13} /> Resume run
            </button>
          ) : null}
          {view && !finished ? (
            <button type="button" className="button button-danger" onClick={() => act.mutate({ path: "cancel" })} disabled={act.isPending}>
              <Icon name="stop" size={13} /> Cancel run
            </button>
          ) : null}
          {finished && view?.status === "passed" && result?.automation ? (
            <button type="button" className="button" onClick={() => setExporting(true)} title="Turn this run into a Playwright or pytest test">
              <Icon name="copy" size={13} /> Export as code
            </button>
          ) : null}
          {canRunOnCloud ? (
            <button type="button" className="button" onClick={() => setCloudDialog(true)} title="Run the exported test on BrowserStack, Sauce Labs or TestMu AI">
              <Icon name="cloud" size={14} /> Run on a cloud
            </button>
          ) : null}
          {finished ? (
            <>
              <button type="button" className="button" onClick={() => setRerunScope("whole")} disabled={act.isPending} title="Run the case again; change its options first">
                <Icon name="rerun" size={14} /> Re-run
              </button>
              <button type="button" className="button button-ghost" onClick={() => setRerunScope("failed_only")} disabled={act.isPending || view?.status === "passed"} title="Re-run only if this run did not pass">
                Re-run if failed
              </button>
              <button
                type="button"
                className="button button-danger"
                onClick={() => {
                  if (window.confirm(deleteRunPrompt(runId))) drop.mutate();
                }}
                disabled={drop.isPending}
              >
                Delete run
              </button>
            </>
          ) : null}
        </div>
      </header>

      {view ? (
        <dl className="run-facts">
          <div>
            <dt>Started</dt>
            <dd>{formatTime(view.started_at ?? view.created_at)}</dd>
          </div>
          <div>
            <dt>Duration</dt>
            <dd>{formatDuration(runSeconds(view))}</dd>
          </div>
          <div>
            <dt>Steps</dt>
            <dd>
              {steps.filter((step) => step.status === "passed").length}/{timeline.totalSteps ?? view.total_steps ?? "?"} passed
            </dd>
          </div>
          <div>
            <dt>Assertions</dt>
            <dd>{result ? `${result.assertions.passed}/${result.assertions.total} passed` : "–"}</dd>
          </div>
          <div>
            <dt>Cost</dt>
            <dd>{formatCost(result?.cost.usd)}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>{result ? `${result.cost.tokens_in.toLocaleString()} in / ${result.cost.tokens_out.toLocaleString()} out` : "–"}</dd>
          </div>
          <div>
            <dt>Case</dt>
            <dd className="mono">{view.case_id ?? "–"}</dd>
          </div>
          {view.started_by === "agent" ? (
            <div>
              <dt>Started by</dt>
              <dd>{agentName(view.agent)}</dd>
            </div>
          ) : null}
          {view.parent_run_id ? (
            <div>
              <dt>Re-run of</dt>
              <dd>
                <Link href={`/runs/${view.parent_run_id}`} className="mono">
                  {view.parent_run_id}
                </Link>
              </dd>
            </div>
          ) : null}
        </dl>
      ) : (
        <p className="muted">Loading run…</p>
      )}

      {exporting ? <ExportDialog runId={runId} native={native} onClose={() => setExporting(false)} /> : null}
      {cloudDialog ? <RunOnCloudDialog runId={runId} native={native} onClose={() => setCloudDialog(false)} onStarted={onCloudStarted} /> : null}
      {rerunScope && view ? (
        <StartRunDialog
          rerun={{ run: view, scope: rerunScope }}
          onClose={() => setRerunScope(null)}
          onStarted={(newRunId) => {
            setRerunScope(null);
            void client.invalidateQueries({ queryKey: ["runs"] });
            navigate(`/runs/${newRunId}`);
          }}
        />
      ) : null}
      {act.error ? <p className="callout callout-fail" role="alert">{(act.error as Error).message}</p> : null}
      {drop.error ? <p className="callout callout-fail" role="alert">{(drop.error as Error).message}</p> : null}
      {view?.reason && view.status !== "passed" ? (
        <p className={`callout ${view.status === "cancelled" ? "callout-note" : "callout-fail"}`}>
          <Icon name="alert" size={14} /> {view.reason}
        </p>
      ) : null}
      {timeline.errors.length > 0 && !view?.reason ? <p className="callout callout-fail">{timeline.errors.at(-1)}</p> : null}
      {result?.failure ? (
        <p className="callout callout-note">
          Classified as <strong>{result.failure.category.replace("_", " ")}</strong> ({Math.round(result.failure.confidence * 100)}%){result.failure.signals.length ? `: ${result.failure.signals.join(", ")}` : ""}
        </p>
      ) : null}
      {result?.hints.length ? (
        <ul className="hints">
          {result.hints.map((hint) => (
            <li key={hint}>{hint}</li>
          ))}
        </ul>
      ) : null}

      {status === "needs_plan_approval" ? (
        <PlanReview view={planQuery.data} busy={decidePlan.isPending} error={decidePlan.error ? (decidePlan.error as Error).message : null} onDecide={(decision) => decidePlan.mutate(decision)} />
      ) : null}

      {approval ? (
        <ApprovalCard
          runId={runId}
          approval={approval}
          stepText={approvalStep?.action ?? approval.action ?? ""}
          busy={decide.isPending}
          error={decide.error ? (decide.error as Error).message : null}
          onDecide={(decision, stepText) => decide.mutate({ decision, stepText, approvalId: approval.id })}
        />
      ) : null}

      {open && status ? (
        <section className="panel panel-live" aria-labelledby="live-title">
          <div className="panel-head">
            <h2 id="live-title">Live run</h2>
            {timeline.control.notice ? <span className="muted small">{timeline.control.notice}</span> : null}
          </div>
          <DebugToolbar status={status} pausedAt={pausedAt} busy={debug.isPending} onAction={onDebug} />
          {streamError ? (
            <p className="callout callout-fail" role="alert">
              {streamError}
            </p>
          ) : null}
          {debug.error ? (
            <p className="callout callout-fail" role="alert">
              {(debug.error as Error).message}
            </p>
          ) : null}
          <LiveView runId={runId} active={open} paused={status === "paused"} fallbackRef={timeline.latestScreenshot?.ref ?? null} lastInput={timeline.control.lastInput} />
        </section>
      ) : null}

      {isDraft && view?.draft_steps ? (
        <div className="draft">
          <h2>Drafted steps</h2>
          <ol>
            {view.draft_steps.map((step, offset) => (
              <li key={offset}>{step}</li>
            ))}
          </ol>
        </div>
      ) : null}

      {finished ? <CloudRunsPanel runId={runId} highlight={newCloudRun} onRunOnCloud={canRunOnCloud ? () => setCloudDialog(true) : undefined} /> : null}

      <div className="segmented run-tabs" role="tablist" aria-label="Run views">
        <button type="button" role="tab" id="tab-run" aria-selected={tab === "run"} aria-controls="panel-run" className={`segment${tab === "run" ? " segment-on" : ""}`} onClick={() => setTab("run")}>
          Steps
        </button>
        <button type="button" role="tab" id="tab-plan" aria-selected={tab === "plan"} aria-controls="panel-plan" className={`segment${tab === "plan" ? " segment-on" : ""}`} onClick={() => setTab("plan")}>
          Plan
          {result?.plan ? (
            <span className="muted small">
              {" "}
              {result.plan.passed}/{result.plan.criteria} criteria
              {result.plan.amendments ? ` · ${result.plan.amendments} amended` : ""}
              {result.plan.lint_errors + result.plan.lint_warnings ? ` · ${result.plan.lint_errors + result.plan.lint_warnings} lint` : ""}
            </span>
          ) : null}
        </button>
      </div>

      {tab === "plan" ? (
        <section className="panel panel-plan" id="panel-plan" role="tabpanel" aria-labelledby="tab-plan">
          {planQuery.data ? (
            <PlanPanel plan={planQuery.data.plan} stepStatus={Object.fromEntries(steps.map((step) => [step.index, step.status]))} />
          ) : planQuery.isError ? (
            <p className="muted">{(planQuery.error as Error).message}</p>
          ) : (
            <p className="muted">Loading the plan…</p>
          )}
        </section>
      ) : null}

      <div className="run-grid" id="panel-run" role="tabpanel" aria-labelledby="tab-run" hidden={tab !== "run"}>
        <div className="panel panel-timeline">
          <div className="panel-head">
            <h2>Steps</h2>
            {!following ? (
              <button type="button" className="link-button" onClick={() => setPinned(null)}>
                {!finished ? "Follow live" : failedStep ? "Back to the failing step" : "Back to the last step"}
              </button>
            ) : !finished ? (
              <span className="muted small">
                <span className="live-dot" aria-hidden="true" /> following
              </span>
            ) : null}
          </div>
          {steps.length ? <StepTimeline steps={steps} selected={selectedIndex} current={timeline.currentStep} onSelect={setPinned} /> : <p className="muted">Waiting for the run to start…</p>}
        </div>

        <div className="panel panel-step">
          {selected ? (
            <>
              <div className="panel-head">
                <h2>
                  Step {selected.index} <StatusChip status={selected.status} />
                </h2>
                {selected.durationMs !== null ? <span className="muted">{formatMs(selected.durationMs)}</span> : null}
              </div>
              <p className="step-action">{stepLabel(selected)}</p>
              <div className="compare">
                <div className="compare-col">
                  <h3>Expected</h3>
                  <p>{selected.expected ?? <span className="muted">No expectation on this step; it passes when the action succeeds.</span>}</p>
                </div>
                <div className={`compare-col${selected.status === "failed" ? " compare-bad" : ""}`}>
                  <h3>Actual</h3>
                  <p>{selected.actual ?? <span className="muted">{selected.status === "passed" ? "As expected." : selected.status === "pending" ? "Not run yet." : selected.status === "skipped" ? "Did not run." : "–"}</span>}</p>
                </div>
              </div>
              <Screenshot runId={runId} step={shotStep} live={liveShot !== null} liveSeq={liveShot?.seq ?? null} />
              {selected.evidence.length ? (
                <ul className="evidence-links evidence-step">
                  {selected.evidence.map((ref) => (
                    <li key={ref}>
                      <a href={evidenceUrl(runId, ref)} target="_blank" rel="noopener">
                        <Icon name={/\.(jpe?g|png)$/.test(ref) ? "image" : "link"} size={13} /> {evidenceName(ref)}
                      </a>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : (
            <p className="muted">Select a step.</p>
          )}
        </div>

        <div className="panel panel-decisions">
          <div className="panel-head">
            <h2>Decision inspector</h2>
            {selected ? <span className="muted">step {selected.index}</span> : null}
          </div>
          <DecisionInspector decisions={decisions.data?.decisions} loading={decisions.isFetching} error={decisions.isError ? (decisions.error as Error).message : null} />
        </div>
      </div>

      {result?.quality && result.quality.pages.length > 0 ? <QualityPanel runId={runId} quality={result.quality} /> : null}

      {result?.security && result.security.scans.length > 0 ? <SecurityPanel runId={runId} security={result.security} /> : null}

      {view ? (
        <section className="panel panel-evidence" aria-labelledby="evidence-title">
          <h2 id="evidence-title">Evidence</h2>
          <EvidenceLinks run={view} />
          <p className="muted small mono" title={view.run_dir}>{shortPath(view.run_dir)}</p>
        </section>
      ) : null}
    </section>
  );
}
