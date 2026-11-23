import { useState } from "react";
import { formatCost, formatMs, formatPercent } from "../lib/format";
import type { DecisionView, OptionView, QuestionView } from "../lib/types";
import { Icon } from "./Icon";

const COLLAPSED_OPTIONS = 6;

/** "jev" → JEV, "local-llm" → Local model, "clef" → Clef (local); the stub test double is labelled as such. */
export function engineLabel(engine: string | null | undefined): string {
  if (!engine) return "Unknown engine";
  const names: Record<string, string> = { jev: "JEV", "jev-openrouter": "JEV", "jev-typesafe": "JEV", "local-llm": "Local model", clef: "Clef (local)", stub: "Stub (test)" };
  return engine
    .split("+")
    .map((part) => names[part] ?? part)
    .join(" → ");
}

const PURPOSES: Record<string, string> = {
  target: "Which element to act on",
  verify: "Does the page meet the expectation",
  semantic: "Does the page show the expected outcome (by meaning)",
  visual: "Does the page look as expected",
};

function OptionRow({ option, chosen, questionId }: { option: OptionView; chosen: boolean; questionId: string }) {
  const value = option.probability ?? 0;
  return (
    <li className={`option${chosen ? " option-chosen" : ""}`} data-testid={`option-${questionId}-${option.id}`}>
      <span className="option-mark" aria-hidden="true">
        {chosen ? <Icon name="check" size={13} /> : null}
      </span>
      <span className="option-label">
        <code className="option-id">{option.id}</code>
        {option.label ? <span className="option-text">{option.label}</span> : null}
        {chosen ? <span className="visually-hidden"> (chosen)</span> : null}
      </span>
      <span
        className="meter"
        role="meter"
        aria-label={`Probability of ${option.id}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)}
        aria-valuetext={formatPercent(option.probability)}
      >
        <span className="meter-fill" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </span>
      <span className="option-pct">{formatPercent(option.probability)}</span>
    </li>
  );
}

function Question({ question }: { question: QuestionView }) {
  const [expanded, setExpanded] = useState(false);
  const options = expanded ? question.options : question.options.slice(0, COLLAPSED_OPTIONS);
  const hidden = question.options.length - options.length;
  return (
    <section className="question" aria-label={`Question ${question.id}`}>
      <header className="question-head">
        <code>{question.id}</code>
        <span className="question-type">{question.type}</span>
        <span className="question-answer">
          Answer <strong>{question.answer ?? "none"}</strong>
          {question.confidence !== null ? <span className="muted"> at {formatPercent(question.confidence)}</span> : null}
        </span>
      </header>
      {question.instructions ? <p className="question-text">{question.instructions}</p> : null}
      {question.options.length > 0 ? (
        <ul className="options">
          {options.map((option) => (
            <OptionRow key={option.id} option={option} chosen={option.id === question.answer} questionId={question.id} />
          ))}
        </ul>
      ) : null}
      {hidden > 0 || expanded ? (
        <button type="button" className="link-button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
          {expanded ? "Show fewer options" : `Show all ${question.options.length} options`}
        </button>
      ) : null}
    </section>
  );
}

function Decision({ decision, number }: { decision: DecisionView; number: number }) {
  return (
    <article className="decision" aria-label={`Decision ${number}`}>
      <header className="decision-head">
        <span className="decision-title">
          {number}. {PURPOSES[decision.purpose ?? ""] ?? decision.purpose ?? "Decision"}
          {decision.attempt > 0 ? <span className="muted"> · retry {decision.attempt}</span> : null}
        </span>
        <span className={`engine engine-${(decision.engine ?? "unknown").split("+")[0]}`}>{engineLabel(decision.answered_by ?? decision.engine)}</span>
      </header>
      <dl className="facts">
        <div>
          <dt>Model</dt>
          <dd className="mono">{decision.model ?? "–"}</dd>
        </div>
        <div>
          <dt>Latency</dt>
          <dd>{formatMs(decision.latency_ms)}</dd>
        </div>
        <div>
          <dt>Cost</dt>
          <dd>{formatCost(decision.cost_usd)}</dd>
        </div>
        <div>
          <dt>Elements shown</dt>
          <dd>{decision.rows ?? "–"}</dd>
        </div>
        {decision.cached ? (
          <div>
            <dt>Cache</dt>
            <dd>hit</dd>
          </div>
        ) : null}
      </dl>
      {decision.step?.text ? (
        <p className="decision-step">
          <span className="muted">Step read as</span> <strong>{decision.step.kind ?? "?"}</strong>
          {decision.step.target ? (
            <>
              {" "}
              on <q>{decision.step.target}</q>
            </>
          ) : null}
        </p>
      ) : null}
      {decision.fallback ? (
        <p className="callout callout-warn" role="note">
          <Icon name="alert" size={14} /> Fallback: {decision.fallback}
        </p>
      ) : null}
      {decision.calls.length > 1 ? (
        <ol className="calls" aria-label="Calls made for this decision">
          {decision.calls.map((call, offset) => (
            <li key={offset}>
              {engineLabel(call.engine)}
              {call.images ? " with screenshot" : ""} · {formatMs(call.latency_ms)}
              {call.confidence !== undefined && call.confidence !== null ? ` · ${formatPercent(call.confidence)}` : ""}
              {call.error ? <span className="text-fail"> · {call.error}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {decision.questions.map((question) => (
        <Question key={question.id} question={question} />
      ))}
    </article>
  );
}

export function DecisionInspector({ decisions, loading = false, error = null }: { decisions: DecisionView[] | undefined; loading?: boolean; error?: string | null }) {
  if (error) return <p className="callout callout-fail">Could not load decisions: {error}</p>;
  if (loading && !decisions) return <p className="muted">Loading decisions…</p>;
  if (!decisions || decisions.length === 0) {
    return <p className="empty-note">No model decisions in this step. Navigation, waits and text checks run without asking the decider.</p>;
  }
  return (
    <div className="decisions">
      {decisions.map((decision, offset) => (
        <Decision key={offset} decision={decision} number={offset + 1} />
      ))}
    </div>
  );
}
