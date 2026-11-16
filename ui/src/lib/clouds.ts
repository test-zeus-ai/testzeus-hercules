// Pure helpers for the test clouds: status text, what blocks a cloud run, the request body and
// bundle link for one, labels and polling for the cloud runs of a run.

import type { Cloud, CloudId, CloudRun, CloudRunOptions, CloudRunStatus } from "./types";

export type CloudKind = "web" | "mobile";

/** Cloud run statuses that will not change again. */
export const CLOUD_RUN_FINAL: ReadonlySet<CloudRunStatus> = new Set(["passed", "failed", "error", "cancelled"]);

/** Poll cadence while a cloud run is starting or running (as for live local runs). */
export const CLOUD_RUN_POLL_MS = 1500;

export const isCloudRunActive = (run: Pick<CloudRun, "status">): boolean => !CLOUD_RUN_FINAL.has(run.status);

/** How often to refresh a list of cloud runs: while any is active, else not at all. */
export function cloudRunsPollInterval(runs: ReadonlyArray<Pick<CloudRun, "status">> | undefined): number | false {
  return runs?.some(isCloudRunActive) ? CLOUD_RUN_POLL_MS : false;
}

/** A short monogram for the text logo of a cloud ("BrowserStack" → "BS", "TestMu AI" → "TM"). */
export function cloudMonogram(name: string): string {
  const capitals = name.replace(/\s+AI$/i, "").match(/[A-Z]/g) ?? [];
  const letters = capitals.length >= 2 ? capitals.slice(0, 2).join("") : name.replace(/[^A-Za-z]/g, "").slice(0, 2);
  return letters.toUpperCase();
}

/** "Connected as alice · from environment", "Connected as alice · saved in Hercules", "Not connected". */
export function connectionLabel(cloud: Pick<Cloud, "connected" | "source" | "username">): string {
  if (!cloud.connected) return "Not connected";
  const who = cloud.username ? `Connected as ${cloud.username}` : "Connected";
  if (cloud.source === "env") return `${who} · from environment`;
  if (cloud.source === "file") return `${who} · saved in Hercules`;
  return who;
}

/** Why a test of this kind cannot run on the cloud from here, or null when it can. */
export function cloudRunBlocker(cloud: Cloud, kind: CloudKind, egressAllows: boolean): string | null {
  if (!egressAllows) return "The egress policy is local, so nothing may leave this machine.";
  if (!cloud.connected) return cloud.problem ?? "Not connected yet.";
  return kind === "mobile" ? cloud.mobile_ready : cloud.web_ready;
}

/** The first cloud a test of this kind can run on, else the first cloud. */
export function defaultCloud(clouds: readonly Cloud[], kind: CloudKind, egressAllows: boolean): CloudId | null {
  return (clouds.find((cloud) => cloudRunBlocker(cloud, kind, egressAllows) === null) ?? clouds[0])?.id ?? null;
}

export interface CloudRunForm {
  browser: string;
  os: string;
  device: string;
  platformVersion: string;
}

/** browser "": the run's own browser (Chrome for a Chromium run), which the daemon fills in. */
export const EMPTY_CLOUD_RUN_FORM: CloudRunForm = { browser: "", os: "windows", device: "", platformVersion: "" };

// The daemon's own limits for these fields: checked here so a typo is caught before the request.
const DEVICE = /^[A-Za-z0-9 ._()+-]{1,80}$/;
const VERSION = /^[A-Za-z0-9._-]{1,20}$/;

export function validateCloudRunForm(form: CloudRunForm, kind: CloudKind): Partial<Record<keyof CloudRunForm, string>> {
  const errors: Partial<Record<keyof CloudRunForm, string>> = {};
  if (kind !== "mobile") return errors;
  const device = form.device.trim();
  const version = form.platformVersion.trim();
  if (device && !DEVICE.test(device)) errors.device = "Letters, digits, spaces and . _ ( ) + - only (up to 80).";
  if (version && !VERSION.test(version)) errors.platformVersion = "Like 14 or 17.5 (letters, digits, . _ - only).";
  return errors;
}

/** The options sent for a cloud run: browser and OS for web; device and version (when set) for an app. */
export function cloudRunOptions(form: CloudRunForm, kind: CloudKind): CloudRunOptions {
  if (kind === "web") return form.browser ? { browser: form.browser, os: form.os } : { os: form.os };
  const options: CloudRunOptions = {};
  if (form.device.trim()) options.device = form.device.trim();
  if (form.platformVersion.trim()) options.platform_version = form.platformVersion.trim();
  return options;
}

/** The body of `POST /api/runs/{id}/cloud-runs`. */
export function cloudRunBody(cloud: CloudId, form: CloudRunForm, kind: CloudKind): { cloud: CloudId } & CloudRunOptions {
  return { cloud, ...cloudRunOptions(form, kind) };
}

/** `GET /api/runs/{id}/cloud-bundle?…`: the exported test as a ready-to-run project for the cloud's CI. */
export function cloudBundleUrl(runId: string, cloud: CloudId, form: CloudRunForm, kind: CloudKind): string {
  const params = new URLSearchParams({ cloud });
  for (const [key, value] of Object.entries(cloudRunOptions(form, kind))) {
    if (value) params.set(key, value);
  }
  return `/api/runs/${encodeURIComponent(runId)}/cloud-bundle?${params.toString()}`;
}

const BROWSER_LABELS: Record<string, string> = { chrome: "Chrome", edge: "Edge", chromium: "Chromium", firefox: "Firefox", webkit: "WebKit", safari: "Safari" };
const OS_LABELS: Record<string, string> = { windows: "Windows", macos: "macOS", linux: "Linux" };

export const browserLabel = (id: string): string => BROWSER_LABELS[id] ?? id;
export const osLabel = (id: string): string => OS_LABELS[id] ?? id;

/** "Chrome · Windows" for a web run; "Pixel 8 · 14" (or "the run's device") for an app run. */
export function cloudRunOptionsLabel(run: Pick<CloudRun, "kind" | "options">): string {
  const { options } = run;
  if (run.kind === "web") return [options.browser ? browserLabel(options.browser) : null, options.os ? osLabel(options.os) : null].filter(Boolean).join(" · ") || "Default browser";
  const parts = [options.device ?? "The run's device", options.platform_version ? `version ${options.platform_version}` : null];
  return parts.filter(Boolean).join(" · ");
}

/** Seconds a cloud run has taken (so far, while it is active). */
export function cloudRunSeconds(run: Pick<CloudRun, "created_at" | "started_at" | "finished_at">, now: number = Date.now()): number | null {
  const start = Date.parse(run.started_at ?? run.created_at);
  if (!Number.isFinite(start)) return null;
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  return Number.isFinite(end) ? Math.max(0, (end - start) / 1000) : null;
}

const STATUS_SENTENCE: Record<CloudRunStatus, string> = {
  starting: "is starting",
  running: "is running",
  passed: "passed",
  failed: "failed",
  error: "could not run",
  cancelled: "was cancelled",
};

/** What a screen reader hears when a cloud run changes: "BrowserStack run passed". */
export function cloudRunAnnouncement(run: Pick<CloudRun, "cloud_name" | "status">): string {
  return `${run.cloud_name} run ${STATUS_SENTENCE[run.status] ?? run.status}`;
}
