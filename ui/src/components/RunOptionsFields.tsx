import { useState, type ReactNode } from "react";
import { DEFAULT_APPROVAL_TIMEOUT_S, MAX_APPROVAL_TIMEOUT_S, MAX_TERM_LENGTH, MAX_TERMS, type OptionErrors, type RunOptions } from "../lib/runOptions";
import type { Impact } from "../lib/types";
import { Field } from "./Field";

const FAIL_ON: Array<[Impact | "", string]> = [
  ["", "Report only"],
  ["critical", "Critical findings"],
  ["serious", "Serious or worse"],
  ["moderate", "Moderate or worse"],
  ["minor", "Any finding (minor or worse)"],
];

function CheckOption({ id, label, help, checked, onChange }: { id: string; label: string; help: ReactNode; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <div className="check-option">
      <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} aria-describedby={`${id}-help`} />
      <label htmlFor={id}>{label}</label>
      <span className="field-help" id={`${id}-help`}>
        {help}
      </span>
    </div>
  );
}

/** The "Quality checks" and "Human in the loop" groups of the start-run dialog. */
export function RunOptionsFields({ value, errors, onChange }: { value: RunOptions; errors: OptionErrors; onChange: (next: RunOptions) => void }) {
  const set = <K extends keyof RunOptions>(key: K, next: RunOptions[K]) => onChange({ ...value, [key]: next });
  const checking = value.a11y || value.visual;
  const asking = value.confirmDestructive || value.askOnFailure;
  return (
    <>
      <OptionGroup title="Quality checks" summary={qualitySummary(value)}>
        <div className="check-options">
          <CheckOption id="start-a11y" label="Accessibility" help="Audit every page the run reaches (WCAG) and suggest fixes." checked={value.a11y} onChange={(checked) => set("a11y", checked)} />
          <CheckOption id="start-visual" label="Visual" help="Compare each page with its baseline screenshot and check the layout." checked={value.visual} onChange={(checked) => set("visual", checked)} />
        </div>
        <Field id="start-fail-on" label="Fail the run on" help={checking ? "Findings at or above this impact fail the run." : "Turn on a check to choose."}>
          <select value={value.failOn} onChange={(event) => set("failOn", event.target.value as Impact | "")} disabled={!checking}>
            {FAIL_ON.map(([id, label]) => (
              <option key={id || "report"} value={id}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </OptionGroup>

      <OptionGroup title="Plan" summary={planSummary(value)}>
        <Field id="start-plan-review" label="Review the plan" help="Ask: the run waits for you to approve, edit or reject its plan before the browser starts. Default: ask for drafted cases and for plans with lint errors.">
          <select value={value.planReview} onChange={(event) => set("planReview", event.target.value as RunOptions["planReview"])}>
            <option value="">Default</option>
            <option value="auto">Run the plan as made</option>
            <option value="ask">Ask before the run starts</option>
          </select>
        </Field>
        <div className="check-options">
          <CheckOption
            id="start-strict"
            label="Strict"
            help="Lint errors stop the run before it starts, and inferred criteria still unmet after recovery fail their step."
            checked={value.strict}
            onChange={(checked) => set("strict", checked)}
          />
        </div>
      </OptionGroup>

      <OptionGroup title="Human in the loop" summary={askingSummary(value)}>
        <div className="check-options">
          <CheckOption
            id="start-confirm-destructive"
            label="Confirm destructive clicks"
            help="Ask before clicking elements whose names suggest delete, pay or transfer."
            checked={value.confirmDestructive}
            onChange={(checked) => set("confirmDestructive", checked)}
          />
          <CheckOption id="start-ask-on-failure" label="Ask when a step fails" help="Ask whether to retry once a step's recovery is exhausted." checked={value.askOnFailure} onChange={(checked) => set("askOnFailure", checked)} />
        </div>
        <div className="field-row">
          <Field
            id="start-destructive-terms"
            label="Destructive terms"
            error={errors.destructiveTerms ?? undefined}
            help={value.confirmDestructive ? `Comma-separated, up to ${MAX_TERMS} terms of at most ${MAX_TERM_LENGTH} characters. Empty uses the built-in list.` : "Turn on confirming destructive clicks to edit."}
          >
            <input type="text" value={value.destructiveTerms} onChange={(event) => set("destructiveTerms", event.target.value)} placeholder="delete, remove, pay, transfer" disabled={!value.confirmDestructive} autoComplete="off" spellCheck={false} />
          </Field>
          <Field
            id="start-approval-timeout"
            label="Answer timeout (seconds)"
            error={errors.approvalTimeout ?? undefined}
            help={asking ? "An unanswered question fails the step after this long." : "Turn on a question above to set."}
          >
            <input type="number" inputMode="numeric" min={1} max={MAX_APPROVAL_TIMEOUT_S} step="any" value={value.approvalTimeout} onChange={(event) => set("approvalTimeout", event.target.value)} placeholder={String(DEFAULT_APPROVAL_TIMEOUT_S)} disabled={!asking} />
          </Field>
        </div>
      </OptionGroup>
    </>
  );
}

/** A collapsible group of options: closed unless it already holds a choice, with what is set beside its title. */
export function OptionGroup({ title, summary, children }: { title: string; summary: string | null; children: ReactNode }) {
  const [open, setOpen] = useState(summary !== null);
  return (
    <details className="field option-group option-details" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="option-group-title">{title}</span>
        {!open && summary ? <span className="option-group-summary">{summary}</span> : null}
      </summary>
      <fieldset className="option-group-body">
        <legend className="visually-hidden">{title}</legend>
        {children}
      </fieldset>
    </details>
  );
}

function qualitySummary(value: RunOptions): string | null {
  const on = [value.a11y ? "Accessibility" : "", value.visual ? "Visual" : ""].filter(Boolean);
  if (!on.length) return null;
  return on.join(", ") + (value.failOn ? ` · fail on ${value.failOn}` : "");
}

function planSummary(value: RunOptions): string | null {
  const on = [value.planReview === "ask" ? "ask before the run" : value.planReview === "auto" ? "run as made" : "", value.strict ? "strict" : ""].filter(Boolean);
  return on.length ? on.join(", ") : null;
}

function askingSummary(value: RunOptions): string | null {
  const on = [value.confirmDestructive ? "confirm destructive clicks" : "", value.askOnFailure ? "ask when a step fails" : ""].filter(Boolean);
  return on.length ? on.join(", ") : null;
}
