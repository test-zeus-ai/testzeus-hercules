// Shapes of the daemon API (see /api/openapi.json). Only the fields the UI reads are typed.

export type RunStatus = "queued" | "running" | "paused" | "needs_approval" | "needs_plan_approval" | "passed" | "failed" | "error" | "cancelled";
export type StepStatus = "passed" | "failed" | "error" | "skipped" | "blocked" | "unverified";
export type RunMode = "auto" | "pure" | "hybrid";
export type Egress = "local" | "hybrid-text" | "hybrid";

export const FINAL_STATUSES: ReadonlySet<string> = new Set(["passed", "failed", "error", "cancelled"]);

/** GET /api/runs/batches/{batch}: a folder run counted by each case's latest attempt. */
export interface BatchSummary {
  batch: string;
  folder?: string | null;
  total: number;
  /** Latest attempts not finished yet. */
  open: number;
  counts: Partial<Record<RunStatus, number>>;
  /** When its last latest attempt ended, once none is open. */
  finished_at?: string | null;
}

export interface StepResult {
  index: number;
  action: string;
  status: StepStatus;
  expected?: string | null;
  actual?: string | null;
  duration_ms: number;
  decisions: number;
  evidence: string[];
  /** When the step ran (1-based), when that differs from its index: a field it depends on ran first. */
  sequence?: number | null;
  /** What the run changed about the step (its order, a value re-entered the way the field asks for it). */
  notes?: string[];
}

export interface RunResult {
  run_id: string;
  status: RunStatus;
  case_id: string;
  verdict_source: string;
  steps: StepResult[];
  assertions: { total: number; passed: number; failed: number; unverified: number };
  failure?: { category: string; confidence: number; signals: string[]; summary?: string | null } | null;
  hints: string[];
  evidence: Record<string, string | null>;
  engines: { llm?: string | null; decider?: string | null; egress: Egress; mode?: RunMode | null; decider_model?: string | null; jev_calls?: number; clef_calls?: number; fallbacks?: number };
  /** Local-model tokens (exact); decision_tokens_est: prompt tokens sent to JEV or Clef, estimated (they report cost only). */
  cost: { tokens_in: number; tokens_out: number; usd?: number | null; decision_tokens_est?: number | null };
  duration_s: number;
  ui_url?: string | null;
  quality?: QualitySection | null;
  /** The run's security scans (security steps) and their findings. */
  security?: SecuritySection | null;
  /** What the run did as framework-neutral automation; rendered as code by `GET /api/runs/{id}/export`. */
  automation?: Record<string, unknown> | null;
  /** The run's plan in numbers; the plan itself is `GET /api/runs/{id}/plan`. */
  plan?: PlanSummary | null;
}

/** `RunResult.plan`: criteria results, amendments and lint of the run's plan. */
export interface PlanSummary {
  steps: number;
  criteria: number;
  author: number;
  inferred: number;
  passed: number;
  failed: number;
  unverified: number;
  not_run: number;
  recoveries: number;
  amendments: number;
  lint_errors: number;
  lint_warnings: number;
  coverage_gaps: number;
  strict: boolean;
  source: string;
  review: string;
  planning_ms: number;
}

// Exporting a passed run as code (`GET /api/runs/{id}/export`).

export type ExportFormat = "playwright-ts" | "playwright-py" | "appium-py" | "spec";

export interface ExportView {
  format: ExportFormat;
  run_id: string;
  case_id: string;
  run_status: string;
  filename: string;
  code: string;
  spec: Record<string, unknown>;
  env: Array<{ name: string; required: boolean; purpose: string }>;
  notes: string[];
  steps_exported: number;
  steps_total: number;
  partial: boolean;
  guidance: string;
}

// Quality checks (accessibility, visual and layout), the `quality` section of a run result.

export type Impact = "critical" | "serious" | "moderate" | "minor";
export type QualityKind = "a11y" | "visual" | "layout";

/** Most to least severe; findings at or above a run's `fail_on` fail it. */
export const IMPACTS: readonly Impact[] = ["critical", "serious", "moderate", "minor"];

export interface Finding {
  id: string;
  kind: QualityKind;
  rule: string;
  source: "axe" | "hercules";
  wcag: string | null;
  level: "A" | "AA" | "AAA" | "none";
  impact: Impact;
  element_md: number | null;
  selector: string;
  frame_path: string[];
  frame: string | null;
  element_desc: string;
  message: string;
  measured: Record<string, unknown> | null;
  evidence: string[];
  hints: string[];
  help_url: string | null;
}

export interface FixSuggestion {
  /** The `id` of the finding (on the same page) this fixes. */
  finding_ref: string;
  property_or_attr: string;
  current: string | null;
  suggested: string;
  changes: Record<string, string>;
  state: string | null;
  target_selector: string | null;
  after_fix: Record<string, unknown> | null;
  /** The value was applied in the live page and the re-check passed. */
  verified: boolean;
  method: "css_override" | "attribute" | "text";
  /** A CSS rule (css_override) or HTML attributes (attribute, text), ready to paste. */
  snippet: string | null;
  likely_file: string | null;
  rationale: string;
  confidence: number;
}

export interface StyleDelta {
  key: string;
  selector: string;
  property: string;
  before: string;
  after: string;
}

export interface DiffClassification {
  /** intended_change | layout_break | content_change */
  label: string;
  confidence: number;
  probabilities: Record<string, number>;
  engine: string;
}

export interface QualityVisual {
  baseline: string;
  created: boolean;
  diff_ratio: number;
  threshold: number;
  changed: boolean;
  regions: number[][];
  style_deltas: StyleDelta[];
  missing: string[];
  classification: DiffClassification | null;
  /** Why a difference was not classified (no decider that can see screenshots, no answer). */
  note?: string | null;
  /** before, after, diff: evidence URIs. */
  images: Record<string, string>;
}

export interface QualityPage {
  kind: QualityKind;
  source: "step" | "run";
  step: number;
  url: string;
  title: string | null;
  findings: Finding[];
  fixes: FixSuggestion[];
  not_scanned: string[];
  visual: QualityVisual | null;
  /** Evidence URIs: the findings JSON and an annotated screenshot (numbered boxes in findings order). */
  evidence: string[];
  error: string | null;
}

export interface QualityCounts {
  critical: number;
  serious: number;
  moderate: number;
  minor: number;
  findings: number;
  fixes: number;
  verified_fixes: number;
  pages: number;
}

export interface QualitySection {
  a11y: boolean;
  visual: boolean;
  fail_on: Impact | null;
  pages: QualityPage[];
  counts: QualityCounts;
}

/** The options a run was started with (`RunView.settings`); a re-run inherits them and takes changes. */
export interface RunSettings {
  environment?: string | null;
  base_url?: string | null;
  mode: RunMode;
  egress?: Egress | null;
  /** A model-free test double ("stub"); null for real runs. */
  decider?: string | null;
  headed: boolean;
  max_steps?: number | null;
  step_timeout_s?: number | null;
  confirm_destructive: boolean;
  destructive_terms?: string[] | null;
  ask_on_failure: boolean;
  approval_timeout_s?: number | null;
  a11y: boolean;
  visual: boolean;
  fail_on?: Impact | null;
  /** "ask": the run waits for its plan's approval before the browser starts. */
  plan_review?: "auto" | "ask" | null;
  strict?: boolean;
}

export type RerunScope = "whole" | "failed_only";

export interface RunView {
  run_id: string;
  status: RunStatus;
  finished: boolean;
  case_id?: string | null;
  title?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  total_steps?: number | null;
  steps_done: number;
  current_step?: number | null;
  current_action?: string | null;
  reason?: string | null;
  parent_run_id?: string | null;
  started_by?: "daemon" | "agent" | null;
  agent?: string | null;
  batch?: string | null;
  folder?: string | null;
  /** The Explore session that started this run as one of its children. */
  explore_session?: string | null;
  worker_pid?: number | null;
  last_seq: number;
  mode?: string | null;
  egress?: string | null;
  settings?: RunSettings | null;
  /** The saved environment the run uses: its case's, or the one it was started with. */
  environment?: string | null;
  run_dir: string;
  ui_url: string;
  summary: string;
  draft_steps?: string[] | null;
  approval?: ApprovalView | null;
  paused?: PauseView | null;
  resumable?: boolean;
  result?: RunResult | null;
}

/** The question a running run waits on (status needs_approval with a live worker). */
export interface ApprovalView {
  id?: string | null;
  kind?: string | null;
  step?: number | null;
  reason?: string | null;
  action?: string | null;
  target?: string | null;
  screenshot?: string | null;
  timeout_s?: number | null;
  allow_edit?: boolean;
  asked_at?: string | null;
}

export interface PauseView {
  step?: number | null;
  where?: string | null;
  action?: string | null;
  since?: string | null;
}

export interface CaseStep {
  action: string;
  expected?: string | null;
  semantic?: boolean;
  timeout_s?: number | null;
}

export interface SecretRef {
  secret: string;
}

export interface TestCase {
  id: string;
  title: string;
  description?: string | null;
  priority?: "low" | "medium" | "high" | "critical";
  tags?: string[];
  environment?: { ref?: string | null; base_url?: string | null; viewport?: string | null; browser?: string | null; device?: string | null; mobile?: MobileApp | null };
  preconditions?: string[];
  test_data?: Record<string, string | SecretRef>;
  steps: CaseStep[];
  timeout_s?: number;
  status?: "draft" | "ready";
}

export interface CaseEntry {
  name: string;
  folder?: string;
  path: string;
  kind: "json" | "feature";
  valid: boolean;
  error?: string;
  id?: string;
  title?: string;
  status?: string;
  steps?: number;
  environment?: string | null;
}

export interface CaseListing {
  cases: CaseEntry[];
  folders?: string[];
}

export interface SavedCase {
  name: string;
  folder?: string;
  kind: "json" | "feature";
  source: string | null;
  test_case: TestCase;
}

export interface CaseCheck {
  valid: boolean;
  error: string | null;
  test_case: TestCase | null;
}

/** A native app on a device (Appium): see docs/guide/mobile-testing.md. */
export interface MobileApp {
  platform: "android" | "ios";
  app?: string | null;
  package?: string | null;
  activity?: string | null;
  bundle_id?: string | null;
  device?: string | null;
  udid?: string | null;
  platform_version?: string | null;
  appium_url?: string | null;
  cloud?: "browserstack" | "saucelabs" | null;
  no_reset?: boolean;
  capabilities?: Record<string, unknown>;
}

export interface Environment {
  name: string;
  base_url?: string | null;
  viewport?: string | null;
  browser?: string | null;
  /** Playwright launch and context options by their Python names (locale, timezone_id, storage_state, ...). */
  browser_options?: Record<string, unknown> | null;
  /** Mobile web emulation: a Playwright device profile such as "iPhone 15". */
  device?: string | null;
  mobile?: MobileApp | null;
  secret_names: string[];
  variables?: Record<string, string | { secret: string }>;
}

export type SecretSource = "env" | "file" | "none";

export interface SecretStatus {
  name: string;
  source: SecretSource;
}

export interface OptionView {
  id: string;
  label: string | null;
  probability: number | null;
}

export interface QuestionView {
  id: string;
  type: string;
  instructions: string | null;
  options: OptionView[];
  answer: string | null;
  confidence: number | null;
}

export interface DecisionCall {
  engine?: string;
  images?: boolean;
  model?: string;
  latency_ms?: number;
  cost_usd?: number | null;
  confidence?: number | null;
  error?: string;
}

export interface DecisionView {
  purpose: string | null;
  attempt: number;
  engine: string | null;
  model: string | null;
  latency_ms: number | null;
  wall_ms: number | null;
  cost_usd: number | null;
  cached: boolean;
  choice: string | null;
  confidence: number | null;
  rows: number | null;
  prompt_tokens_est: number | null;
  fallback: string | null;
  answered_by: string | null;
  calls: DecisionCall[];
  page: { title: string | null; url: string | null } | null;
  step: { kind?: string; role?: string; target?: string; text?: string } | null;
  questions: QuestionView[];
}

export interface CheckResult {
  name: string;
  status: "ok" | "warn" | "fail" | "skip" | string;
  detail: string;
  fix?: string | null;
}

export interface ModelsReport {
  provider: string;
  model: string;
  tier: string;
  egress_policy: Egress;
  parallel_runs: number;
  endpoint: { provider: string; base_url: string; is_local: boolean } | null;
  capabilities: Array<{
    base_url: string;
    model_id?: string | null;
    server_version?: string | null;
    context_tokens?: number | null;
    supports_tools?: boolean | null;
    supports_tool_choice?: boolean | null;
    supports_json_schema?: boolean | null;
    image_mode?: string | null;
  }>;
  modes: {
    pure: { available: boolean; reason: string | null };
    hybrid: { available: boolean; key_env: string; key_set: boolean; default_egress_allows: boolean; reason: string | null };
  };
  doctor?: {
    status: string;
    model_id?: string | null;
    server_version?: string | null;
    tier?: { name: string; context_tokens: number; model_alias: string; parallel_runs: number } | null;
    memory_bytes?: number | null;
    checks?: CheckResult[];
    capabilities?: { context_tokens: number | null; supports_tools: boolean; supports_json_schema: boolean; image_mode: string } | null;
    detail?: string;
  };
}

export interface Health {
  status: string;
  version: string;
  project_dir: string;
  parallel_runs: number;
  queue: Record<string, number>;
  pid?: number;
  started_at?: string;
  ui_url?: string;
  /** The account the daemon runs as, the computer's name, and the Hercules home folder. */
  user?: string | null;
  host?: string | null;
  home_dir?: string;
  egress_policy?: string;
}

/** One period of the home page's numbers (`/api/dashboard`). */
export interface DashboardPeriod {
  runs: number;
  finished: number;
  passed: number;
  failed: number;
  /** Passed among finished runs that were not cancelled; null when there are none. */
  pass_rate: number | null;
  median_duration_s: number | null;
  /** Model cost of the finished runs whose result names one; null when none does. */
  cost_usd: number | null;
  costed_runs: number;
}

export interface DashboardDay {
  date: string;
  passed: number;
  failed: number;
  other: number;
}

export interface DashboardReport {
  days: number;
  time_zone: string;
  generated_at: string;
  current: DashboardPeriod;
  previous: DashboardPeriod;
  /** The last 14 calendar days, oldest first. */
  daily: DashboardDay[];
}

export interface RunEvent {
  seq: number;
  kind: string;
  data: Record<string, unknown>;
}

// Security scans (security steps run the nuclei scanner), the `security` section of a run result.

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export type Severity = (typeof SEVERITIES)[number];

export interface SecurityFinding {
  template_id: string;
  name: string;
  severity: Severity;
  matched_at: string;
  matcher?: string | null;
  tags: string[];
  extracted: string[];
  description?: string | null;
}

export type SecurityCounts = Record<Severity, number> & { findings: number };

export interface SecurityScan {
  step: number | null;
  target: string;
  input: "url" | "openapi";
  tags: string[];
  exclude_tags: string[];
  fail_on: Severity;
  scanner?: string | null;
  duration_ms: number;
  findings: SecurityFinding[];
  counts: SecurityCounts;
  error?: string | null;
  evidence: string[];
}

export interface SecuritySection {
  scans: SecurityScan[];
  counts: SecurityCounts;
}

// Test clouds (`/api/clouds`, `/api/runs/{id}/cloud-runs`, `/api/cloud-runs/{id}`).

export type CloudId = "browserstack" | "saucelabs" | "testmu";

export interface Cloud {
  id: CloudId;
  name: string;
  connected: boolean;
  /** Where the credentials come from: environment variables (read-only here), the keys file, or nowhere. */
  source: "env" | "file" | "none";
  username: string | null;
  username_env: string;
  access_key_env: string;
  region: string | null;
  /** Where the region comes from: its environment variable (read-only here), the keys file, the config, or the default. */
  region_source: "env" | "file" | "config" | "default" | null;
  /** The environment variable that sets the region (SAUCE_REGION, LT_REGION); null for a cloud without one. */
  region_env: string | null;
  /** The regions this cloud can be set to; empty when it has none to choose. */
  regions: string[];
  problem: string | null;
  web_mode: "connect" | "saucectl";
  /** null: web tests can run on this cloud from here; otherwise why they cannot. */
  web_ready: string | null;
  /** null: native app tests can run on this cloud from here; otherwise why they cannot. */
  mobile_ready: string | null;
  website_url: string;
  dashboard_url: string;
  keys_url: string;
  signup_url: string;
  ci_runner: string;
  ci_config: string;
}

export interface CloudsReport {
  clouds: Cloud[];
  keys_file: string;
  /** false when the egress policy is local: nothing may reach a cloud. */
  egress_allows: boolean;
  browsers: string[];
  os_names: string[];
}

export interface CloudCheck {
  ok: boolean;
  message: string;
}

export type CloudSaved = Cloud & { check: CloudCheck; note: string | null };
export type CloudRegionSaved = Cloud & { check: CloudCheck | null; note: string | null };
export type CloudRemoved = Cloud & { removed: boolean; note: string | null };

/** A phone or tablet a cloud offers the account (`/api/clouds/{cloud}/devices`). */
export interface CloudMobileDevice {
  name: string;
  platform: "android" | "ios";
  os_version: string;
  kind: "phone" | "tablet";
  /** A real device; false for an emulator or simulator. */
  real: boolean;
  tier: string | null;
  /** Availability when the cloud reports it (TestMu AI: Available, Busy). */
  status: string | null;
}

/** A desktop browser on one OS version, with the browser versions offered, newest first. */
export interface CloudDesktopBrowser {
  os: string;
  os_version: string;
  browser: string;
  versions: string[];
}

export interface CloudDevices {
  cloud: CloudId;
  username: string;
  region: string | null;
  fetched_at: string;
  mobile: CloudMobileDevice[];
  desktop: CloudDesktopBrowser[];
  /** Parts that could not be listed, and what a cloud does not list. */
  notes: string[];
  /** Fetched for another user name or region than the current ones. */
  stale: boolean;
}

export type CloudRunStatus = "starting" | "running" | "passed" | "failed" | "error" | "cancelled";

export interface CloudRunOptions {
  browser?: string | null;
  os?: string | null;
  device?: string | null;
  platform_version?: string | null;
}

export interface CloudRun {
  id: string;
  run_id: string;
  case_id: string;
  cloud: CloudId;
  cloud_name: string;
  kind: "web" | "mobile";
  mode: "connect" | "saucectl" | "appium";
  options: CloudRunOptions;
  status: CloudRunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  dashboard_url: string;
  session_url: string | null;
  message: string | null;
  exit_code: number | null;
}

export type CloudRunDetail = CloudRun & { log: string };

export interface StepFile {
  name: string;
  size: number;
}

export interface StepFiles {
  environment?: string;
  case?: string;
  files: StepFile[];
  max_files: number;
  max_bytes: number;
}
