// The structured TestCase form: conversion to and from TestCase JSON, and the same checks
// the daemon applies (it validates again on save; these give instant, per-field feedback).

import type { TestCase } from "./types";

export const CASE_ID_PATTERN = /^[a-z0-9][a-z0-9._-]{0,99}$/;
export const SECRET_NAME_PATTERN = /^[A-Z_][A-Z0-9_]*$/;
export const DATA_KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const VIEWPORT_PATTERN = /^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$/;
export const BASE_URL_PATTERN = /^https?:\/\/[^\s/?#]+/;
export const BUILTIN_PLACEHOLDERS = ["run_id", "timestamp"];
export const PLACEHOLDER = /\{\{(.*?)\}\}/g;
export const MAX_STEPS = 200;

export interface FormStep {
  action: string;
  expected: string;
}

export interface FormDatum {
  key: string;
  value: string;
  secret: boolean;
}

export interface CaseFormValue {
  id: string;
  title: string;
  priority: "low" | "medium" | "high" | "critical";
  tags: string;
  environmentRef: string;
  baseUrl: string;
  viewport: string;
  status: "draft" | "ready";
  steps: FormStep[];
  testData: FormDatum[];
}

export type FormErrors = Record<string, string>;

export function emptyForm(): CaseFormValue {
  return { id: "", title: "", priority: "medium", tags: "", environmentRef: "", baseUrl: "", viewport: "", status: "ready", steps: [{ action: "", expected: "" }], testData: [] };
}

export function toForm(testCase: TestCase): CaseFormValue {
  const env = testCase.environment ?? {};
  return {
    id: testCase.id,
    title: testCase.title,
    priority: testCase.priority ?? "medium",
    tags: (testCase.tags ?? []).join(", "),
    environmentRef: env.ref ?? "",
    baseUrl: env.base_url ?? "",
    viewport: env.viewport ?? "",
    status: testCase.status ?? "ready",
    steps: testCase.steps.map((step) => ({ action: step.action, expected: step.expected ?? "" })),
    testData: Object.entries(testCase.test_data ?? {}).map(([key, value]) =>
      typeof value === "string" ? { key, value, secret: false } : { key, value: value.secret, secret: true },
    ),
  };
}

/** The TestCase for a form; `base` keeps fields the form does not edit (description, preconditions, timeouts). */
export function fromForm(value: CaseFormValue, base?: TestCase): TestCase {
  const environment: NonNullable<TestCase["environment"]> = { ...(base?.environment ?? {}) };
  const setOrDrop = (key: "ref" | "base_url" | "viewport", text: string) => {
    if (text.trim()) environment[key] = text.trim();
    else delete environment[key];
  };
  setOrDrop("ref", value.environmentRef);
  setOrDrop("base_url", value.baseUrl);
  setOrDrop("viewport", value.viewport);
  const testData = testDataOf(value.testData);
  const tags = value.tags
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
  const testCase: TestCase = {
    ...(base ?? {}),
    id: value.id.trim(),
    title: value.title.trim(),
    priority: value.priority,
    tags,
    environment,
    test_data: testData,
    status: value.status,
    steps: value.steps.map((step) => {
      const kept = matchingStep(base, step);
      const expected = step.expected.trim();
      return expected ? { ...kept, action: step.action.trim(), expected } : { ...kept, action: step.action.trim() };
    }),
  };
  return testCase;
}

export function testDataOf(rows: FormDatum[]): NonNullable<TestCase["test_data"]> {
  const testData: NonNullable<TestCase["test_data"]> = {};
  for (const datum of rows) {
    if (datum.key.trim()) testData[datum.key.trim()] = datum.secret ? { secret: datum.value.trim() } : datum.value;
  }
  return testData;
}

export function validateTestData(rows: FormDatum[]): FormErrors {
  const errors: FormErrors = {};
  const keys = new Set<string>();
  rows.forEach((datum, offset) => {
    const key = datum.key.trim();
    if (!DATA_KEY_PATTERN.test(key)) errors[`testData.${offset}.key`] = "Names start with a letter or '_' and use letters, digits or '_'.";
    else if (BUILTIN_PLACEHOLDERS.includes(key)) errors[`testData.${offset}.key`] = `'${key}' is a built-in placeholder.`;
    else if (keys.has(key)) errors[`testData.${offset}.key`] = "This name is used twice.";
    keys.add(key);
    if (datum.secret && !SECRET_NAME_PATTERN.test(datum.value.trim())) {
      errors[`testData.${offset}.value`] = "Secrets name an environment variable, e.g. SHOP_PASSWORD.";
    }
  });
  return errors;
}

export function validTestData(rows: FormDatum[]): FormDatum[] {
  const errors = validateTestData(rows);
  return rows.filter((_, offset) => !errors[`testData.${offset}.key`] && !errors[`testData.${offset}.value`]);
}

/** Options of the original step with the same action (semantic compare, timeout), kept through an edit. */
function matchingStep(base: TestCase | undefined, step: FormStep): { semantic?: boolean; timeout_s?: number } {
  const found = base?.steps.find((candidate) => candidate.action === step.action);
  const kept: { semantic?: boolean; timeout_s?: number } = {};
  if (found?.semantic) kept.semantic = true;
  if (found?.timeout_s) kept.timeout_s = found.timeout_s;
  return kept;
}

export function validateCaseForm(value: CaseFormValue, environmentNames: Iterable<string> = []): FormErrors {
  const errors: FormErrors = {};
  if (!CASE_ID_PATTERN.test(value.id.trim())) {
    errors.id = "Use lowercase letters, digits, '.', '_' or '-' (start with a letter or digit, at most 100).";
  }
  const title = value.title.trim();
  if (!title) errors.title = "A title is required.";
  else if (title.length > 300) errors.title = "Keep the title under 300 characters.";
  if (value.baseUrl.trim() && !BASE_URL_PATTERN.test(value.baseUrl.trim())) {
    errors.baseUrl = "Use an http(s) URL with a host, e.g. http://localhost:3000.";
  }
  if (value.viewport.trim() && !VIEWPORT_PATTERN.test(value.viewport.trim())) {
    errors.viewport = "Use WIDTHxHEIGHT, e.g. 1280x800.";
  }
  if (value.environmentRef.trim() && !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value.environmentRef.trim())) {
    errors.environmentRef = "Environment names use lowercase letters, digits, '_' or '-'.";
  }
  if (value.steps.length === 0) errors.steps = "Add at least one step.";
  if (value.steps.length > MAX_STEPS) errors.steps = `A case has at most ${MAX_STEPS} steps.`;
  value.steps.forEach((step, offset) => {
    if (!step.action.trim()) errors[`steps.${offset}.action`] = "Describe the action.";
    else if (step.action.length > 2000) errors[`steps.${offset}.action`] = "Keep the action under 2000 characters.";
  });
  Object.assign(errors, validateTestData(value.testData));
  const keys = value.testData.map((datum) => datum.key.trim());
  const known = new Set([...BUILTIN_PLACEHOLDERS, ...keys, ...environmentNames]);
  value.steps.forEach((step, offset) => {
    for (const text of [step.action, step.expected]) {
      for (const match of text.matchAll(PLACEHOLDER)) {
        const name = match[1].trim();
        if (!known.has(name)) {
          errors[`steps.${offset}.placeholders`] = `Unknown placeholder {{${name}}}: add it to the test data or to the environment's variables.`;
        }
      }
    }
  });
  return errors;
}

/** Gherkin line number named in a daemon error ("line 7: ..."), if any. */
export function errorLine(message: string | null | undefined): number | null {
  if (!message) return null;
  const match = /\bline (\d+)/i.exec(message) ?? /^[^:\s]*:(\d+):/.exec(message);
  return match ? Number(match[1]) : null;
}

const ENV_TAG = /@env:\S+/g;

export function environmentTag(gherkin: string): string | null {
  const found = [...gherkin.matchAll(ENV_TAG)].at(-1);
  return found ? found[0].slice("@env:".length) : null;
}

export function withEnvironmentTag(gherkin: string, name: string): string {
  const tag = `@env:${name}`;
  if (environmentTag(gherkin) !== null) {
    let first = true;
    return gherkin.replace(/([ \t]*)@env:\S+/g, (_match, space: string) => {
      const kept = first ? `${space}${tag}` : "";
      first = false;
      return kept;
    });
  }
  const lines = gherkin.split("\n");
  const feature = lines.findIndex((line) => /^\s*Feature:/.test(line));
  if (feature > 0 && lines[feature - 1].trim().startsWith("@")) {
    lines[feature - 1] = `${lines[feature - 1].trimEnd()} ${tag}`;
    return lines.join("\n");
  }
  const at = Math.max(feature, 0);
  lines.splice(at, 0, `${/^\s*/.exec(lines[at] ?? "")?.[0] ?? ""}${tag}`);
  return lines.join("\n");
}

const PLACEHOLDER_TOKEN = /\S*\{\{[^{}]*\}\}\S*/g;
const STEP_LINE = /^(\s*(?:Given|When|Then|And|But|\*)\s+)(.+)$/;
const DOC_FENCE = /^\s*("""|```)/;

function quotedAt(text: string): boolean[] {
  const inside: boolean[] = [];
  let open = false;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '"') {
      inside.push(true);
      open = !open;
    } else inside.push(open);
  }
  return inside;
}

function unquotedTokens(text: string): { start: number; token: string }[] {
  const inside = quotedAt(text);
  return [...text.matchAll(PLACEHOLDER_TOKEN)].filter((match) => !inside[match.index] && !match[0].includes('"')).map((match) => ({ start: match.index, token: match[0] }));
}

export function unquotedPlaceholders(text: string): string[] {
  return unquotedTokens(text).map((found) => found.token);
}

export function quotePlaceholders(text: string): string {
  let result = text;
  for (const found of unquotedTokens(text).reverse()) {
    result = `${result.slice(0, found.start)}"${found.token}"${result.slice(found.start + found.token.length)}`;
  }
  return result;
}

export interface UnquotedLine {
  line: number;
  tokens: string[];
}

function eachStepLine(gherkin: string, visit: (prefix: string, text: string, line: number) => string): string {
  let fenced = false;
  return gherkin
    .split("\n")
    .map((raw, offset) => {
      if (DOC_FENCE.test(raw)) {
        fenced = !fenced;
        return raw;
      }
      const step = fenced ? null : STEP_LINE.exec(raw);
      return step ? visit(step[1], step[2], offset + 1) : raw;
    })
    .join("\n");
}

export function gherkinUnquoted(gherkin: string): UnquotedLine[] {
  const found: UnquotedLine[] = [];
  eachStepLine(gherkin, (prefix, text, line) => {
    const tokens = unquotedPlaceholders(text);
    if (tokens.length) found.push({ line, tokens });
    return prefix + text;
  });
  return found;
}

export function quoteGherkinPlaceholders(gherkin: string): string {
  return eachStepLine(gherkin, (prefix, text) => prefix + quotePlaceholders(text));
}

export function formUnquoted(value: CaseFormValue): UnquotedLine[] {
  return value.steps.flatMap((step, offset) => {
    const tokens = [...unquotedPlaceholders(step.action), ...unquotedPlaceholders(step.expected)];
    return tokens.length ? [{ line: offset + 1, tokens }] : [];
  });
}

export function quoteFormPlaceholders(value: CaseFormValue): CaseFormValue {
  return { ...value, steps: value.steps.map((step) => ({ action: quotePlaceholders(step.action), expected: quotePlaceholders(step.expected) })) };
}
