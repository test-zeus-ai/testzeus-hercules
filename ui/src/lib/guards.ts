// Light runtime checks of the daemon's payloads at the api() boundary: the key fields the screens
// read exist with the right type. A daemon of another version (or a proxy's HTML error page)
// then fails with a clear message instead of a crash deep inside a screen.

import type { CaseListing, Cloud, CloudRun, CloudsReport, ModelsReport, RunResult, RunView, SavedCase, TestCase } from "./types";

export type Guard<T> = (value: unknown) => value is T;

type Obj = Record<string, unknown>;

const isObject = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const isString = (value: unknown): value is string => typeof value === "string";
const optional = (value: unknown, check: (inner: unknown) => boolean) => value === undefined || value === null || check(value);

export function isRunResult(value: unknown): value is RunResult {
  return isObject(value) && isString(value.status) && Array.isArray(value.steps) && value.steps.every((step) => isObject(step) && typeof step.index === "number");
}

export function isRunView(value: unknown): value is RunView {
  return isObject(value) && isString(value.run_id) && isString(value.status) && optional(value.result, isRunResult);
}

export function isRunList(value: unknown): value is { runs: RunView[] } {
  return isObject(value) && Array.isArray(value.runs) && value.runs.every(isRunView);
}

export function isTestCase(value: unknown): value is TestCase {
  return isObject(value) && Array.isArray(value.steps) && value.steps.every((step) => isObject(step) && isString(step.action));
}

export function isRunCase(value: unknown): value is { test_case: TestCase } {
  return isObject(value) && isTestCase(value.test_case);
}

export function isSavedCase(value: unknown): value is SavedCase {
  return isObject(value) && isString(value.name) && isString(value.kind) && optional(value.source, isString) && isTestCase(value.test_case);
}

export function isCaseList(value: unknown): value is CaseListing {
  return (
    isObject(value) &&
    Array.isArray(value.cases) &&
    value.cases.every((entry) => isObject(entry) && isString(entry.name) && isString(entry.path) && typeof entry.valid === "boolean") &&
    (value.folders === undefined || (Array.isArray(value.folders) && value.folders.every(isString)))
  );
}

export function isModelsReport(value: unknown): value is ModelsReport {
  return isObject(value) && isString(value.provider) && isString(value.model) && Array.isArray(value.capabilities) && isObject(value.modes) && isObject(value.modes.pure) && isObject(value.modes.hybrid);
}

export function isCloud(value: unknown): value is Cloud {
  return isObject(value) && isString(value.id) && isString(value.name) && typeof value.connected === "boolean" && isString(value.source);
}

export function isCloudsReport(value: unknown): value is CloudsReport {
  return isObject(value) && Array.isArray(value.clouds) && value.clouds.every(isCloud) && typeof value.egress_allows === "boolean" && Array.isArray(value.browsers) && Array.isArray(value.os_names);
}

export function isCloudRun(value: unknown): value is CloudRun {
  return isObject(value) && isString(value.id) && isString(value.cloud) && isString(value.status) && isObject(value.options);
}

export function isCloudRunList(value: unknown): value is { cloud_runs: CloudRun[] } {
  return isObject(value) && Array.isArray(value.cloud_runs) && value.cloud_runs.every(isCloudRun);
}
