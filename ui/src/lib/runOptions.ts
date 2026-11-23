// Quality-check and human-in-the-loop options of the start-run dialog, validated like the
// daemon's RunCreate body and sent only when they differ from the defaults.

import type { Impact, RunSettings } from "./types";

export const MAX_TERMS = 50;
export const MAX_TERM_LENGTH = 60;
export const MAX_APPROVAL_TIMEOUT_S = 86_400;
export const DEFAULT_APPROVAL_TIMEOUT_S = 600;

export interface RunOptions {
  a11y: boolean;
  visual: boolean;
  /** "" reports findings without failing the run. */
  failOn: Impact | "";
  confirmDestructive: boolean;
  /** Comma-separated; empty uses the built-in list. */
  destructiveTerms: string;
  askOnFailure: boolean;
  /** Seconds as typed; empty uses the default. */
  approvalTimeout: string;
  /** "" uses the default (ask for drafted cases, else the project's plan.review); "ask" waits for the plan's approval. */
  planReview: "" | "auto" | "ask";
  /** Lint errors stop the run; inferred criteria still unmet fail their step. */
  strict: boolean;
}

export const DEFAULT_RUN_OPTIONS: RunOptions = {
  a11y: false,
  visual: false,
  failOn: "",
  confirmDestructive: false,
  destructiveTerms: "",
  askOnFailure: false,
  approvalTimeout: "",
  planReview: "",
  strict: false,
};

export interface ParsedTerms {
  terms: string[];
  error: string | null;
}

export function parseDestructiveTerms(text: string): ParsedTerms {
  const terms = text
    .split(/[,\n\r]/)
    .map((term) => term.trim())
    .filter(Boolean);
  if (terms.length > MAX_TERMS) return { terms, error: `Use at most ${MAX_TERMS} terms (got ${terms.length}).` };
  const long = terms.find((term) => term.length > MAX_TERM_LENGTH);
  if (long) return { terms, error: `Each term must be at most ${MAX_TERM_LENGTH} characters: "${long.slice(0, 24)}…" is ${long.length}.` };
  return { terms, error: null };
}

export interface ParsedTimeout {
  seconds: number | null;
  error: string | null;
}

export function parseApprovalTimeout(text: string): ParsedTimeout {
  const trimmed = text.trim();
  if (!trimmed) return { seconds: null, error: null };
  const seconds = Number(trimmed);
  if (!Number.isFinite(seconds) || seconds <= 0 || seconds > MAX_APPROVAL_TIMEOUT_S) {
    return { seconds: null, error: `Enter a number of seconds above 0 and at most ${MAX_APPROVAL_TIMEOUT_S} (24 hours).` };
  }
  return { seconds, error: null };
}

export interface OptionErrors {
  destructiveTerms: string | null;
  approvalTimeout: string | null;
}

/** Errors only for the fields that are in use (a disabled field is never sent). */
export function validateRunOptions(options: RunOptions): OptionErrors {
  const asking = options.confirmDestructive || options.askOnFailure;
  return {
    destructiveTerms: options.confirmDestructive ? parseDestructiveTerms(options.destructiveTerms).error : null,
    approvalTimeout: asking ? parseApprovalTimeout(options.approvalTimeout).error : null,
  };
}

/** The request-body fields for these options: only what differs from the daemon's defaults. */
export function runOptionsBody(options: RunOptions): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (options.a11y) body.a11y = true;
  if (options.visual) body.visual = true;
  if ((options.a11y || options.visual) && options.failOn) body.fail_on = options.failOn;
  if (options.confirmDestructive) {
    body.confirm_destructive = true;
    const { terms, error } = parseDestructiveTerms(options.destructiveTerms);
    if (!error && terms.length) body.destructive_terms = terms;
  }
  if (options.askOnFailure) body.ask_on_failure = true;
  if (options.confirmDestructive || options.askOnFailure) {
    const { seconds } = parseApprovalTimeout(options.approvalTimeout);
    if (seconds !== null && seconds !== DEFAULT_APPROVAL_TIMEOUT_S) body.approval_timeout_s = seconds;
  }
  if (options.planReview) body.plan_review = options.planReview;
  if (options.strict) body.strict = true;
  return body;
}

/** The start-run dialog's fields other than the case: where and how the run goes. */
export interface RunForm {
  environment: string;
  baseUrl: string;
  mode: string;
  /** "" uses the project's policy. */
  egress: string;
  headed: boolean;
  options: RunOptions;
}

export const DEFAULT_RUN_FORM: RunForm = { environment: "", baseUrl: "", mode: "auto", egress: "", headed: false, options: DEFAULT_RUN_OPTIONS };

/** The dialog's fields filled from a run's settings (to re-run it with changes). */
export function formFromSettings(settings: RunSettings | null | undefined): RunForm {
  if (!settings) return DEFAULT_RUN_FORM;
  return {
    environment: settings.environment ?? "",
    baseUrl: settings.base_url ?? "",
    mode: settings.mode ?? "auto",
    egress: settings.egress ?? "",
    headed: Boolean(settings.headed),
    options: {
      a11y: Boolean(settings.a11y),
      visual: Boolean(settings.visual),
      failOn: settings.fail_on ?? "",
      confirmDestructive: Boolean(settings.confirm_destructive),
      destructiveTerms: (settings.destructive_terms ?? []).join(", "),
      askOnFailure: Boolean(settings.ask_on_failure),
      approvalTimeout: settings.approval_timeout_s != null ? String(settings.approval_timeout_s) : "",
      planReview: settings.plan_review ?? "",
      strict: Boolean(settings.strict),
    },
  };
}

/**
 * A re-run's options: every field of the dialog, explicitly (false and null included), so the new
 * run gets exactly what the dialog shows; options the dialog does not show are inherited.
 */
export function rerunOptionsBody(form: RunForm): Record<string, unknown> {
  const { options } = form;
  const checking = options.a11y || options.visual;
  const asking = options.confirmDestructive || options.askOnFailure;
  const terms = parseDestructiveTerms(options.destructiveTerms);
  const timeout = parseApprovalTimeout(options.approvalTimeout);
  return {
    environment: form.environment || null,
    base_url: form.baseUrl.trim() || null,
    mode: form.mode,
    egress: form.egress || null,
    headed: form.headed,
    a11y: options.a11y,
    visual: options.visual,
    fail_on: checking && options.failOn ? options.failOn : null,
    confirm_destructive: options.confirmDestructive,
    destructive_terms: options.confirmDestructive && !terms.error && terms.terms.length ? terms.terms : null,
    ask_on_failure: options.askOnFailure,
    approval_timeout_s: asking ? timeout.seconds : null,
    plan_review: options.planReview || null,
    strict: options.strict,
  };
}
