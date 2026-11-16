// A run's plan (`GET /api/runs/{id}/plan`, schema cases/schemas/plan.schema.json): its plan steps with success
// criteria (author or inferred), risks, coverage, lint, amendments and each criterion's result; and the edits a
// reviewer may make before the run starts (step text, criteria, order), applied without changing anything else.

export type CriterionOrigin = "author" | "inferred";
export type CriterionStatus = "passed" | "failed" | "unverified" | "not_run";

export interface Criterion {
  id: string;
  text: string;
  origin: CriterionOrigin;
  check: string;
  expected?: string | null;
  target?: string | null;
  value?: string | null;
  state?: string | null;
  count?: number | null;
  outcome?: boolean;
  [key: string]: unknown;
}

export interface PlanStep {
  id: string;
  step: number;
  item?: "action" | "expected";
  read?: "case" | "page" | "service";
  kind: string;
  text: string;
  target?: string | null;
  value?: string | null;
  url?: string | null;
  needs?: number[];
  risk?: { flags: string[]; reason?: string | null };
  criteria?: Criterion[];
  [key: string]: unknown;
}

export interface SourceStep {
  number: number;
  action: string;
  expected?: string | null;
  kind?: string;
  payload?: boolean;
}

export interface LintFinding {
  severity: "error" | "warning" | "info";
  code: string;
  step?: number | null;
  message: string;
  suggestion?: string | null;
  source: "model" | "fact";
}

export interface Amendment {
  step: number;
  plan_step?: string | null;
  kind: string;
  before?: string | null;
  after?: string | null;
  reason: string;
  source: "model" | "user" | "engine";
  at?: string;
}

export interface CriterionResult {
  criterion: string;
  plan_step: string;
  step: number;
  origin: CriterionOrigin;
  check: string;
  text: string;
  status: CriterionStatus;
  actual?: string | null;
  recovered?: boolean;
}

export interface Coverage {
  checked: boolean;
  engine?: string | null;
  steps: number;
  gaps: Array<{ step: number; probability: number; repaired: boolean }>;
  repaired: number[];
  note?: string | null;
}

export interface Plan {
  case_id: string;
  case_hash: string;
  prompt_version: string;
  model?: string | null;
  source: string;
  source_steps: SourceStep[];
  steps: PlanStep[];
  order?: number[];
  coverage?: Coverage;
  lint?: LintFinding[];
  review?: { state: string; by?: string | null; at?: string | null };
  timing?: { total_ms: number; cached?: boolean };
  amendments?: Amendment[];
  results?: CriterionResult[];
  [key: string]: unknown;
}

export interface PlanView {
  plan: Plan;
  lint_errors: number;
  lint_warnings: number;
  run_id?: string | null;
  approved: boolean;
  summary: string;
}

/** Per source step (in run order), its plan steps in plan order. */
export function planGroups(plan: Plan): Array<{ source: SourceStep; steps: PlanStep[] }> {
  const order = plan.order?.length ? plan.order : plan.source_steps.map((step) => step.number);
  const byNumber = new Map(plan.source_steps.map((step) => [step.number, step]));
  return order
    .map((number) => byNumber.get(number))
    .filter((source): source is SourceStep => source !== undefined)
    .map((source) => ({ source, steps: plan.steps.filter((step) => step.step === source.number) }));
}

export function resultOf(plan: Plan, criterionId: string): CriterionResult | undefined {
  return plan.results?.find((result) => result.criterion === criterionId);
}

/** A plan step's state after the run: failed when an author criterion failed, unmet when an inferred one did not hold, amended when the run changed it;
 * without a criterion result, its source step's status in the run (``stepStatus``: step number to status). */
export function planStepState(plan: Plan, step: PlanStep, stepStatus?: Record<number, string>): "passed" | "failed" | "unmet" | "amended" | "not_run" | "pending" {
  const results = (step.criteria ?? []).map((criterion) => resultOf(plan, criterion.id)).filter((result): result is CriterionResult => result !== undefined);
  if (results.some((result) => result.origin === "author" && result.status === "failed")) return "failed";
  if (results.some((result) => result.status === "failed" || result.status === "unverified")) return "unmet";
  if (results.some((result) => result.status === "passed")) return "passed";
  if ((plan.amendments ?? []).some((amendment) => amendment.plan_step === step.id)) return "amended";
  if (results.length && results.every((result) => result.status === "not_run")) return "not_run";
  const ran = stepStatus?.[step.step];
  if (ran === "passed") return "passed";
  if (ran === "failed" || ran === "error" || ran === "blocked") return "failed";
  if (ran === "skipped") return "not_run";
  if (ran === "unverified") return "unmet";
  return "pending";
}

export function amendmentsOf(plan: Plan, number: number): Amendment[] {
  return (plan.amendments ?? []).filter((amendment) => amendment.step === number);
}

export function lintOf(plan: Plan, number: number | null): LintFinding[] {
  return (plan.lint ?? []).filter((finding) => (finding.step ?? null) === number);
}

// --------------------------------------------------------------------------- reviewer edits (pure)

function withSteps(plan: Plan, steps: PlanStep[]): Plan {
  return { ...plan, steps };
}

/** The plan with a plan step's text replaced (the daemon has the model read it again when only the text changed). */
export function setStepText(plan: Plan, stepId: string, text: string): Plan {
  return withSteps(
    plan,
    plan.steps.map((step) => (step.id === stepId ? { ...step, text } : step)),
  );
}

/** A criterion in the reviewer's words: it is then judged by its meaning on the page (a `judged` check of that text). */
export function setCriterionText(plan: Plan, criterionId: string, text: string): Plan {
  return withSteps(
    plan,
    plan.steps.map((step) => ({
      ...step,
      criteria: (step.criteria ?? []).map((criterion) =>
        criterion.id === criterionId ? { ...criterion, text, check: "judged", expected: text, target: null, value: null, state: null, count: null } : criterion,
      ),
    })),
  );
}

/** Author criteria fail the step; inferred ones drive recovery and are warnings. A reviewer may promote or demote one. */
export function toggleOrigin(plan: Plan, criterionId: string): Plan {
  return withSteps(
    plan,
    plan.steps.map((step) => ({
      ...step,
      criteria: (step.criteria ?? []).map((criterion) => (criterion.id === criterionId ? { ...criterion, origin: criterion.origin === "author" ? "inferred" : "author" } : criterion)),
    })),
  );
}

export function removeCriterion(plan: Plan, criterionId: string): Plan {
  return withSteps(
    plan,
    plan.steps.map((step) => ({ ...step, criteria: (step.criteria ?? []).filter((criterion) => criterion.id !== criterionId) })),
  );
}

/** Move a source step up or down in the run order (its plan steps move with it). */
export function moveSourceStep(plan: Plan, number: number, delta: -1 | 1): Plan {
  const order = plan.order?.length ? [...plan.order] : plan.source_steps.map((step) => step.number);
  const at = order.indexOf(number);
  const to = at + delta;
  if (at < 0 || to < 0 || to >= order.length) return plan;
  [order[at], order[to]] = [order[to], order[at]];
  return { ...plan, order };
}

/** Problems the daemon would refuse an edited plan for (it checks the plan again). */
export function editProblems(plan: Plan): string[] {
  const problems: string[] = [];
  for (const step of plan.steps) {
    if (!step.text.trim()) problems.push(`Plan step ${step.id} needs a text.`);
    for (const criterion of step.criteria ?? []) {
      if (!criterion.text.trim()) problems.push(`Criterion ${criterion.id} needs a text.`);
    }
  }
  const covered = new Set(plan.steps.map((step) => step.step));
  for (const source of plan.source_steps) {
    if (!covered.has(source.number)) problems.push(`Step ${source.number} has no plan step.`);
  }
  return problems;
}
