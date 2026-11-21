import { amendmentsOf, lintOf, planGroups, planStepState, resultOf, type Criterion, type LintFinding, type Plan, type PlanStep } from "../lib/plan";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

/** Edits a reviewer can make in the plan (see PlanReview); without them the plan is read-only. */
export interface PlanEditing {
  onStepText: (stepId: string, text: string) => void;
  onCriterionText: (criterionId: string, text: string) => void;
  onToggleOrigin: (criterionId: string) => void;
  onRemoveCriterion: (criterionId: string) => void;
  onMove: (number: number, delta: -1 | 1) => void;
}

const STATE_CHIP: Record<string, string> = { passed: "passed", failed: "failed", unmet: "unverified", amended: "recovering", not_run: "skipped", pending: "pending" };

function OriginBadge({ origin }: { origin: Criterion["origin"] }) {
  return (
    <span className={`badge badge-${origin}`} title={origin === "author" ? "From the case's own words: failing it fails the step" : "Inferred by the model: drives recovery, and is a warning unless the run is strict"}>
      {origin}
    </span>
  );
}

function CriterionRow({ plan, criterion, editing }: { plan: Plan; criterion: Criterion; editing?: PlanEditing }) {
  const result = resultOf(plan, criterion.id);
  return (
    <li className={`plan-criterion plan-criterion-${criterion.origin}`}>
      <OriginBadge origin={criterion.origin} />
      {editing ? (
        <input
          className="plan-input"
          aria-label={`Criterion ${criterion.id}`}
          value={criterion.text}
          onChange={(event) => editing.onCriterionText(criterion.id, event.target.value)}
        />
      ) : (
        <span className="plan-criterion-text">{criterion.text}</span>
      )}
      <span className="muted small mono">{criterion.check}</span>
      {result ? <StatusChip status={result.status === "not_run" ? "skipped" : result.status} compact /> : null}
      {result?.recovered ? (
        <span className="tag" title="It was unmet once and drove a recovery">
          recovered
        </span>
      ) : null}
      {result?.actual ? <span className="muted small plan-actual">{result.actual}</span> : null}
      {editing ? (
        <span className="plan-edit-buttons">
          <button type="button" className="link-button" onClick={() => editing.onToggleOrigin(criterion.id)}>
            Make {criterion.origin === "author" ? "inferred" : "author"}
          </button>
          <button type="button" className="link-button" onClick={() => editing.onRemoveCriterion(criterion.id)} aria-label={`Remove criterion ${criterion.id}`}>
            Remove
          </button>
        </span>
      ) : null}
    </li>
  );
}

function PlanStepRow({ plan, step, editing, stepStatus }: { plan: Plan; step: PlanStep; editing?: PlanEditing; stepStatus?: Record<number, string> }) {
  const state = planStepState(plan, step, stepStatus);
  const flags = step.risk?.flags ?? [];
  return (
    <li className={`plan-step plan-step-${state}`} data-plan-step={step.id}>
      <div className="plan-step-head">
        <span className="mono plan-step-id">{step.id}</span>
        <span className="tag mono">{step.kind}</span>
        {editing && step.kind !== "verify" ? (
          <input className="plan-input" aria-label={`Plan step ${step.id}`} value={step.text} onChange={(event) => editing.onStepText(step.id, event.target.value)} />
        ) : (
          <span className="plan-step-text">{step.text}</span>
        )}
        {step.read === "page" ? <span className="muted small">read on the page when it runs</span> : null}
        {step.needs?.length ? <span className="muted small">after step {step.needs.join(", ")}</span> : null}
        {flags.map((flag) => (
          <span key={flag} className="badge badge-risk" title={step.risk?.reason ?? undefined}>
            <Icon name="alert" size={11} /> {flag.replace(/_/g, " ")}
          </span>
        ))}
        {plan.results?.length || stepStatus ? <StatusChip status={STATE_CHIP[state] ?? state} compact /> : null}
      </div>
      {step.criteria?.length ? (
        <ul className="plan-criteria" aria-label={`Success criteria of ${step.id}`}>
          {step.criteria.map((criterion) => (
            <CriterionRow key={criterion.id} plan={plan} criterion={criterion} editing={editing} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function LintList({ findings }: { findings: LintFinding[] }) {
  if (!findings.length) return null;
  return (
    <ul className="plan-lint">
      {findings.map((finding, offset) => (
        <li key={offset} className={`plan-lint-${finding.severity}`}>
          <strong>{finding.severity}</strong> <span className="mono small">{finding.code}</span> {finding.message}
          {finding.suggestion ? <span className="muted"> → {finding.suggestion}</span> : null}
          <span className="muted small"> ({finding.source === "model" ? "model" : "fact"})</span>
        </li>
      ))}
    </ul>
  );
}

/** The plan: per source step its plan steps, criteria (with results after the run), risks, lint and amendments as before → after.
 * ``stepStatus`` (step number to the run's status of that step) gives a plan step without a criterion result its step's outcome. */
export function PlanPanel({ plan, editing, stepStatus }: { plan: Plan; editing?: PlanEditing; stepStatus?: Record<number, string> }) {
  const groups = planGroups(plan);
  const general = lintOf(plan, null);
  const coverage = plan.coverage;
  return (
    <div className="plan">
      <p className="muted small">
        {plan.steps.length} plan steps · {plan.source} · review {plan.review?.state ?? "generated"} · model <span className="mono">{plan.model ?? "none"}</span>
        {coverage ? <> · coverage {coverage.checked ? `${coverage.gaps.filter((gap) => !gap.repaired).length ? "gaps open" : "covered"}${coverage.engine ? ` (${coverage.engine})` : ""}` : (coverage.note ?? "not audited")}</> : null}
        {plan.timing ? <> · planned in {(plan.timing.total_ms / 1000).toFixed(1)} s{plan.timing.cached ? " (stored)" : ""}</> : null}
      </p>
      <LintList findings={general} />
      <ol className="plan-sources">
        {groups.map(({ source, steps }, position) => {
          const amendments = amendmentsOf(plan, source.number);
          return (
            <li key={source.number} className="plan-source">
              <div className="plan-source-head">
                <span className="plan-source-number">{source.number}</span>
                <span className="plan-source-text">
                  {source.action}
                  {source.expected ? <span className="muted"> → {source.expected}</span> : null}
                </span>
                {editing ? (
                  <span className="plan-edit-buttons">
                    <button type="button" className="link-button" disabled={position === 0} onClick={() => editing.onMove(source.number, -1)} aria-label={`Move step ${source.number} up`}>
                      Up
                    </button>
                    <button type="button" className="link-button" disabled={position === groups.length - 1} onClick={() => editing.onMove(source.number, 1)} aria-label={`Move step ${source.number} down`}>
                      Down
                    </button>
                  </span>
                ) : null}
              </div>
              <LintList findings={lintOf(plan, source.number)} />
              <ul className="plan-steps">
                {steps.map((step) => (
                  <PlanStepRow key={step.id} plan={plan} step={step} editing={editing} stepStatus={stepStatus} />
                ))}
              </ul>
              {amendments.length ? (
                <ul className="plan-amendments" aria-label={`Amendments of step ${source.number}`}>
                  {amendments.map((amendment, offset) => (
                    <li key={offset}>
                      <span className={`badge badge-${amendment.source}`}>{amendment.source}</span> <strong>{amendment.kind.replace("_", " ")}</strong>
                      {amendment.plan_step ? <span className="mono small"> {amendment.plan_step}</span> : null}
                      <div className="plan-diff">
                        {amendment.before ? <del>{amendment.before}</del> : null}
                        {amendment.after ? <ins>{amendment.after}</ins> : null}
                      </div>
                      <span className="muted small">{amendment.reason}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
