import { useEffect, useRef, useState } from "react";
import { evidenceUrl } from "../lib/api";
import type { ApprovalRequest } from "../lib/timeline";
import { Icon } from "./Icon";

export type Decision = "approve" | "reject" | "edit";

const TITLES: Record<string, string> = {
  destructive: "Confirm a destructive action",
  recovery_exhausted: "A step could not be done",
  draft: "Approve the drafted case",
};

const HELP: Record<string, string> = {
  destructive: "Approve to perform the action, reject to block the step, or edit the step to do something else.",
  recovery_exhausted: "Approve to retry the step, edit it and run it again, or reject to stop with the failure.",
  draft: "Approve to run the drafted steps, or reject to cancel the run.",
};

interface Props {
  runId: string;
  approval: ApprovalRequest;
  /** The step's current text, the starting point of an edit. */
  stepText: string;
  onDecide: (decision: Decision, stepText?: string) => void;
  busy?: boolean;
  error?: string | null;
}

/** The question a running run waits on, with its screenshot and the three answers. */
/** Identifies one question: its id, or (from an older daemon without ids) what it asks about. */
export function approvalKey(approval: ApprovalRequest): string {
  return approval.id ?? `${approval.kind}:${approval.step ?? ""}:${approval.reason}`;
}

export function ApprovalCard({ runId, approval, stepText, onDecide, busy = false, error = null }: Props) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(stepText);
  const [announcement, setAnnouncement] = useState("");
  const key = approvalKey(approval);
  const title = TITLES[approval.kind] ?? "The run needs your decision";
  const stepName = approval.step ? `step ${approval.step}` : "the run";

  // A new question starts a fresh edit; a refreshed step text of the same question does not
  // wipe what the tester is typing (the text is read once, when the question arrives).
  const latestText = useRef(stepText);
  latestText.current = stepText;
  useEffect(() => {
    setEditing(false);
    setText(latestText.current);
    // Set after mount, so the (already present, empty) live region announces it.
    setAnnouncement(`The run is waiting for your decision at ${stepName}: ${title}.`);
  }, [key, stepName, title]);

  // Until the tester starts editing, follow the latest step text.
  useEffect(() => {
    if (!editing) setText(stepText);
  }, [stepText, editing]);

  return (
    <section className="approval-card" aria-labelledby="approval-title">
      <p className="visually-hidden" aria-live="assertive" aria-atomic="true">
        {announcement}
      </p>
      <div className="approval-body">
        <p className="approval-eyebrow">
          <Icon name="alert" size={14} /> Waiting for you · {stepName}
        </p>
        <h2 id="approval-title">{title}</h2>
        <p className="approval-reason">{approval.reason}</p>
        {approval.action ? <p className="approval-action mono">{approval.action}</p> : null}
        <p className="muted small">
          {HELP[approval.kind] ?? "Approve, reject or edit the step."}
          {approval.timeoutS ? ` Without an answer within ${Math.round(approval.timeoutS / 60) || 1} min the step is not performed.` : ""}
        </p>
        {editing ? (
          <div className="approval-edit">
            <label htmlFor="approval-step-text" className="small">
              New text for step {approval.step}
            </label>
            <textarea id="approval-step-text" rows={2} value={text} onChange={(event) => setText(event.target.value)} autoFocus />
            <div className="approval-buttons">
              <button type="button" className="button button-primary" disabled={busy || !text.trim()} onClick={() => onDecide("edit", text.trim())}>
                Run edited step
              </button>
              <button type="button" className="button button-ghost" disabled={busy} onClick={() => setEditing(false)}>
                Back
              </button>
            </div>
          </div>
        ) : (
          <div className="approval-buttons">
            <button type="button" className="button button-primary" disabled={busy} onClick={() => onDecide("approve")}>
              <Icon name="check" size={14} /> Approve
            </button>
            <button type="button" className="button button-danger" disabled={busy} onClick={() => onDecide("reject")}>
              <Icon name="cross" size={14} /> Reject
            </button>
            {approval.allowEdit ? (
              <button type="button" className="button" disabled={busy} onClick={() => setEditing(true)}>
                Edit step…
              </button>
            ) : null}
          </div>
        )}
        {error ? (
          <p className="callout callout-fail" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      {approval.screenshot ? (
        <a className="approval-shot" href={evidenceUrl(runId, approval.screenshot)} target="_blank" rel="noopener" title="Open full size">
          <img src={evidenceUrl(runId, approval.screenshot)} alt={`The page at step ${approval.step ?? ""} when the run stopped`} />
        </a>
      ) : null}
    </section>
  );
}
