import { useState } from "react";
import { editProblems, moveSourceStep, removeCriterion, setCriterionText, setStepText, toggleOrigin, type Plan, type PlanView } from "../lib/plan";
import { Icon } from "./Icon";
import { PlanPanel, type PlanEditing } from "./PlanPanel";

export type PlanDecision = { decision: "approve" } | { decision: "reject" } | { decision: "edit"; plan: Plan };

/** The plan of a run waiting in needs_plan_approval: approve it, edit it (step texts, criteria, order) and approve, or reject it. */
export function PlanReview({ view, busy, error, onDecide }: { view: PlanView | undefined; busy: boolean; error: string | null; onDecide: (decision: PlanDecision) => void }) {
  const [draft, setDraft] = useState<Plan | null>(null);
  if (!view) {
    return (
      <section className="panel panel-approval" aria-labelledby="plan-review-title">
        <h2 id="plan-review-title">Review the plan</h2>
        <p className="muted">Loading the plan…</p>
      </section>
    );
  }
  const plan = draft ?? view.plan;
  const problems = draft ? editProblems(draft) : [];
  const editing: PlanEditing | undefined = draft
    ? {
        onStepText: (stepId, text) => setDraft((current) => (current ? setStepText(current, stepId, text) : current)),
        onCriterionText: (criterionId, text) => setDraft((current) => (current ? setCriterionText(current, criterionId, text) : current)),
        onToggleOrigin: (criterionId) => setDraft((current) => (current ? toggleOrigin(current, criterionId) : current)),
        onRemoveCriterion: (criterionId) => setDraft((current) => (current ? removeCriterion(current, criterionId) : current)),
        onMove: (number, delta) => setDraft((current) => (current ? moveSourceStep(current, number, delta) : current)),
      }
    : undefined;
  return (
    <section className="panel panel-approval plan-review" aria-labelledby="plan-review-title">
      <div className="panel-head">
        <h2 id="plan-review-title">
          <Icon name="alert" size={15} /> Review the plan before the run starts
        </h2>
        <span className="muted small">
          {view.lint_errors} lint error(s), {view.lint_warnings} warning(s)
        </span>
      </div>
      <p className="muted small">
        Author criteria come from the case's words and fail the step when they do not hold; inferred criteria are the model's expectations: they drive recovery and are warnings unless the run is strict.
      </p>
      <PlanPanel plan={plan} editing={editing} />
      {problems.length ? (
        <ul className="callout callout-fail" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p className="callout callout-fail" role="alert">
          {error}
        </p>
      ) : null}
      <div className="approval-actions">
        {draft ? (
          <>
            <button type="button" className="button button-primary" disabled={busy || problems.length > 0} onClick={() => onDecide({ decision: "edit", plan: draft })}>
              <Icon name="check" size={14} /> Save and approve
            </button>
            <button type="button" className="button button-ghost" disabled={busy} onClick={() => setDraft(null)}>
              Discard edits
            </button>
          </>
        ) : (
          <>
            <button type="button" className="button button-primary" disabled={busy} onClick={() => onDecide({ decision: "approve" })}>
              <Icon name="check" size={14} /> Approve and run
            </button>
            <button type="button" className="button" disabled={busy} onClick={() => setDraft(view.plan)}>
              Edit plan
            </button>
            <button type="button" className="button button-ghost" disabled={busy} onClick={() => onDecide({ decision: "reject" })}>
              Reject
            </button>
          </>
        )}
      </div>
    </section>
  );
}
