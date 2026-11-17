import { Field, RequiredNote } from "./Field";
import { SuggestInput, type Suggestion } from "./PlaceholderSuggest";
import { unquotedPlaceholders, type CaseFormValue, type FormDatum, type FormErrors, type FormStep } from "../lib/caseForm";

/** The structured TestCase form. Pure: the value and errors come from the parent. */
export function CaseForm({
  value,
  errors,
  onChange,
  showTestData = true,
  environments = [],
  suggestions = [],
}: {
  value: CaseFormValue;
  errors: FormErrors;
  onChange: (value: CaseFormValue) => void;
  showTestData?: boolean;
  environments?: string[];
  suggestions?: Suggestion[];
}) {
  const chosenEnvironment = value.environmentRef.trim();
  const set = <K extends keyof CaseFormValue>(key: K, next: CaseFormValue[K]) => onChange({ ...value, [key]: next });
  const setStep = (offset: number, change: Partial<FormStep>) => set("steps", value.steps.map((step, index) => (index === offset ? { ...step, ...change } : step)));
  const moveStep = (offset: number, by: number) => {
    const target = offset + by;
    if (target < 0 || target >= value.steps.length) return;
    const steps = [...value.steps];
    [steps[offset], steps[target]] = [steps[target], steps[offset]];
    set("steps", steps);
  };

  return (
    <div className="case-form">
      <RequiredNote />
      <div className="field-row">
        <Field id="case-id" label="Case id" error={errors.id} help="Lowercase; also the file name." required>
          <input className="mono" value={value.id} onChange={(event) => set("id", event.target.value)} />
        </Field>
        <Field id="case-priority" label="Priority">
          <select value={value.priority} onChange={(event) => set("priority", event.target.value as CaseFormValue["priority"])}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </Field>
        <Field id="case-status" label="Status">
          <select value={value.status} onChange={(event) => set("status", event.target.value as CaseFormValue["status"])}>
            <option value="ready">Ready</option>
            <option value="draft">Draft (needs approval to run)</option>
          </select>
        </Field>
      </div>
      <Field id="case-title" label="Title" error={errors.title} required>
        <input value={value.title} onChange={(event) => set("title", event.target.value)} />
      </Field>
      <div className="field-row">
        <Field id="case-env" label="Environment" error={errors.environmentRef} help="A saved environment (optional).">
          <select className="mono" value={chosenEnvironment} onChange={(event) => set("environmentRef", event.target.value)}>
            <option value="">None</option>
            {environments.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
            {chosenEnvironment && !environments.includes(chosenEnvironment) ? <option value={chosenEnvironment}>{chosenEnvironment} (not saved)</option> : null}
          </select>
        </Field>
        <Field id="case-base-url" label="Base URL" error={errors.baseUrl}>
          <input className="mono" value={value.baseUrl} onChange={(event) => set("baseUrl", event.target.value)} placeholder="http://localhost:3000" />
        </Field>
        <Field id="case-viewport" label="Viewport" error={errors.viewport}>
          <input className="mono" value={value.viewport} onChange={(event) => set("viewport", event.target.value)} placeholder="1280x800" />
        </Field>
      </div>
      <Field id="case-tags" label="Tags" help="Comma separated.">
        <input value={value.tags} onChange={(event) => set("tags", event.target.value)} />
      </Field>

      <fieldset className="steps-editor">
        <legend className="is-required">Steps</legend>
        {errors.steps ? <p className="field-error" role="alert">{errors.steps}</p> : null}
        <ol>
          {value.steps.map((step, offset) => {
            const actionError = errors[`steps.${offset}.action`];
            const placeholderError = errors[`steps.${offset}.placeholders`];
            const bare = [...unquotedPlaceholders(step.action), ...unquotedPlaceholders(step.expected)];
            return (
              <li key={offset} className="step-row">
                <span className="step-number" aria-hidden="true">
                  {offset + 1}
                </span>
                <div className="step-fields">
                  <label className="visually-hidden" htmlFor={`step-${offset}-action`}>
                    Step {offset + 1} action
                  </label>
                  <SuggestInput
                    id={`step-${offset}-action`}
                    suggestions={suggestions}
                    value={step.action}
                    placeholder="Action, e.g. Click the Sign in button"
                    onChange={(action) => setStep(offset, { action })}
                    aria-invalid={Boolean(actionError)}
                    aria-describedby={actionError ? `step-${offset}-error` : undefined}
                  />
                  <label className="visually-hidden" htmlFor={`step-${offset}-expected`}>
                    Step {offset + 1} expected outcome
                  </label>
                  <SuggestInput id={`step-${offset}-expected`} suggestions={suggestions} value={step.expected} placeholder="Expected outcome (optional)" onChange={(expected) => setStep(offset, { expected })} />
                  {actionError || placeholderError ? (
                    <span className="field-error" id={`step-${offset}-error`} role="alert">
                      {actionError ?? placeholderError}
                    </span>
                  ) : bare.length ? (
                    <span className="field-warn">Put placeholder values in quotes: {bare.map((token) => `${token} → "${token}"`).join(", ")}</span>
                  ) : null}
                </div>
                <div className="step-tools">
                  <button type="button" className="icon-button" onClick={() => moveStep(offset, -1)} disabled={offset === 0} aria-label={`Move step ${offset + 1} up`}>
                    ↑
                  </button>
                  <button type="button" className="icon-button" onClick={() => moveStep(offset, 1)} disabled={offset === value.steps.length - 1} aria-label={`Move step ${offset + 1} down`}>
                    ↓
                  </button>
                  <button type="button" className="icon-button" onClick={() => set("steps", value.steps.filter((_, index) => index !== offset))} disabled={value.steps.length === 1} aria-label={`Remove step ${offset + 1}`}>
                    ×
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
        <button type="button" className="button button-small" onClick={() => set("steps", [...value.steps, { action: "", expected: "" }])}>
          Add step
        </button>
      </fieldset>

      {showTestData ? <TestDataEditor rows={value.testData} errors={errors} onChange={(rows) => set("testData", rows)} /> : null}
    </div>
  );
}

export function TestDataEditor({ rows, errors, onChange }: { rows: FormDatum[]; errors: FormErrors; onChange: (rows: FormDatum[]) => void }) {
  const setDatum = (offset: number, change: Partial<FormDatum>) => onChange(rows.map((datum, index) => (index === offset ? { ...datum, ...change } : datum)));
  return (
    <fieldset className="data-editor">
      <legend>Test data</legend>
      <p className="field-help">Use values in steps as {"{{name}}"}. Secrets name an environment variable; the value is read at run time and never stored.</p>
      {rows.map((datum, offset) => {
        const keyError = errors[`testData.${offset}.key`];
        const valueError = errors[`testData.${offset}.value`];
        return (
          <div key={offset} className="data-row">
            <label className="visually-hidden" htmlFor={`data-${offset}-key`}>
              Name
            </label>
            <input id={`data-${offset}-key`} className="mono" value={datum.key} placeholder="name" onChange={(event) => setDatum(offset, { key: event.target.value })} aria-invalid={Boolean(keyError)} />
            <label className="visually-hidden" htmlFor={`data-${offset}-value`}>
              {datum.secret ? "Environment variable" : "Value"}
            </label>
            <input
              id={`data-${offset}-value`}
              className={datum.secret ? "mono" : undefined}
              value={datum.value}
              placeholder={datum.secret ? "ENV_VAR_NAME" : "value"}
              onChange={(event) => setDatum(offset, { value: event.target.value })}
              aria-invalid={Boolean(valueError)}
            />
            <label className="field-check">
              <input type="checkbox" checked={datum.secret} onChange={(event) => setDatum(offset, { secret: event.target.checked })} /> Secret
            </label>
            <button type="button" className="icon-button" onClick={() => onChange(rows.filter((_, index) => index !== offset))} aria-label={`Remove ${datum.key || "test data row"}`}>
              ×
            </button>
            {keyError || valueError ? (
              <span className="field-error data-error" role="alert">
                {keyError ?? valueError}
              </span>
            ) : null}
          </div>
        );
      })}
      <button type="button" className="button button-small" onClick={() => onChange([...rows, { key: "", value: "", secret: false }])}>
        Add value
      </button>
    </fieldset>
  );
}
