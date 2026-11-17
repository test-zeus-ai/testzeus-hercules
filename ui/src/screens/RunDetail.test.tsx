import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RunResult, RunView } from "../lib/types";
import { cloudRun, cloudsReport } from "../test/cloudFixtures";
import { samplePlan, sampleView } from "../test/planFixtures";
import { RunDetailScreen, ordinal } from "./RunDetail";

const MODELS = {
  provider: "local",
  model: "local-model",
  tier: "small",
  egress_policy: "local",
  parallel_runs: 1,
  endpoint: null,
  capabilities: [],
  modes: { pure: { available: true, reason: null }, hybrid: { available: true, key_env: "JEV_API_KEY", key_set: true, default_egress_allows: true, reason: null } },
};

const RESULT: RunResult = {
  run_id: "r_parent",
  status: "failed",
  case_id: "demo-login",
  verdict_source: "decider",
  steps: [{ index: 1, action: "Open /login", status: "failed", duration_ms: 1200, decisions: 0, evidence: [] }],
  assertions: { total: 1, passed: 0, failed: 1, unverified: 0 },
  hints: [],
  evidence: {},
  engines: { llm: "local-model", decider: "jev", decider_model: "jev-1", egress: "hybrid-text", mode: "hybrid" },
  cost: { tokens_in: 10, tokens_out: 5, usd: 0 },
  duration_s: 3,
};

function parentRun(overrides: Partial<RunView> = {}): RunView {
  return {
    run_id: "r_parent",
    status: "failed",
    finished: true,
    case_id: "demo-login",
    title: "Demo login",
    created_at: "2026-10-26T10:00:00+00:00",
    steps_done: 1,
    total_steps: 1,
    last_seq: 4,
    mode: "auto",
    egress: "hybrid-text",
    run_dir: "/tmp/runs/r_parent",
    ui_url: "/runs/r_parent",
    summary: "FAILED",
    settings: {
      environment: null,
      base_url: "http://127.0.0.1:5555",
      mode: "auto",
      egress: "hybrid-text",
      decider: null,
      headed: false,
      confirm_destructive: false,
      destructive_terms: null,
      ask_on_failure: false,
      approval_timeout_s: null,
      a11y: false,
      visual: true,
      fail_on: null,
    },
    result: RESULT,
    ...overrides,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;
let view: RunView;

function respond(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)) });
}

beforeEach(() => {
  view = parentRun();
  window.history.replaceState(null, "", "/runs/r_parent");
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/runs/r_parent/rerun" && init?.method === "POST") return respond({ run_id: "r_child", status: "queued", ui_url: "/runs/r_child" }, 201);
    if (url === "/api/runs/r_parent") return respond(view);
    if (url === "/api/runs/r_parent/plan") return respond(sampleView(samplePlan()));
    if (url === "/api/runs/r_parent/approve" && init?.method === "POST") return respond({ ...view, status: "running", finished: false });
    if (url === "/api/runs/r_parent/case") return respond({ test_case: { id: "demo-login", title: "Demo login", steps: [{ action: "Open /login" }] } });
    if (url.startsWith("/api/runs/r_parent/steps/")) return respond({ decisions: [] });
    if (url === "/api/environments") return respond({ environments: [] });
    if (url === "/api/models") return respond(MODELS);
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderScreen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunDetailScreen runId="r_parent" />
    </QueryClientProvider>,
  );
}

const header = () => within(document.querySelector(".run-head") as HTMLElement);

describe("RunDetail header", () => {
  it("shows the mode the run used, the mode it asked for and the decider", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Demo login" });
    const head = header();
    const mode = head.getByText("Mode").parentElement as HTMLElement;
    expect(mode.textContent).toBe("Mode hybrid (from auto)");
    expect(head.getByText("Decider").parentElement?.textContent).toBe("Decider jev (jev-1)");
  });

  it("shows the asked-for mode and says when the decider will be known while the run is going", async () => {
    view = parentRun({ status: "running", finished: false, result: null, settings: { ...parentRun().settings!, mode: "pure" } });
    renderScreen();
    await screen.findByRole("heading", { name: "Demo login" });
    const head = header();
    expect(head.getByText("Mode").parentElement?.textContent).toBe("Mode pure");
    expect(head.getByText("Decider").parentElement?.textContent).toBe("Decider reported when the run finishes");
  });
});

describe("RunDetail re-run", () => {
  it("opens the start dialog prefilled from the run, then re-runs with the changes and follows the new run", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /^Re-run$/ }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("heading", { name: "Re-run" })).toBeTruthy();
    expect((dialog.getByLabelText("Mode") as HTMLSelectElement).value).toBe("auto");
    expect((dialog.getByLabelText("Egress") as HTMLSelectElement).value).toBe("hybrid-text");
    expect((dialog.getByLabelText("Base URL") as HTMLInputElement).value).toBe("http://127.0.0.1:5555");
    expect((dialog.getByRole("checkbox", { name: "Visual" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.change(dialog.getByLabelText("Mode"), { target: { value: "pure" } });
    fireEvent.change(dialog.getByLabelText("Egress"), { target: { value: "local" } });
    fireEvent.click(dialog.getByRole("button", { name: "Start re-run" }));

    await waitFor(() => expect(window.location.pathname).toBe("/runs/r_child"));
    const call = fetchMock.mock.calls.find(([url, init]) => url === "/api/runs/r_parent/rerun" && (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse((call![1] as RequestInit).body as string)).toMatchObject({ scope: "whole", mode: "pure", egress: "local", base_url: "http://127.0.0.1:5555", visual: true, a11y: false });
  });

  it("re-runs if failed with the failed-only scope, and offers it only for a run that did not pass", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Re-run if failed" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("heading", { name: "Re-run if failed" })).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "Start re-run" }));
    await waitFor(() => expect(window.location.pathname).toBe("/runs/r_child"));
    const call = fetchMock.mock.calls.find(([url, init]) => url === "/api/runs/r_parent/rerun" && (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse((call![1] as RequestInit).body as string)).toMatchObject({ scope: "failed_only", mode: "auto" });
  });

  it("disables re-run if failed for a passed run", async () => {
    view = parentRun({ status: "passed", result: { ...RESULT, status: "passed" } });
    renderScreen();
    const button = (await screen.findByRole("button", { name: "Re-run if failed" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("RunDetail plan", () => {
  it("shows the plan to review while the run waits for it, and approves it", async () => {
    view = parentRun({ status: "needs_plan_approval", finished: false, result: null, approval: { id: "q1", kind: "plan", step: 0, reason: "review the run's plan", allow_edit: true } });
    renderScreen();
    expect(await screen.findByRole("heading", { name: /Review the plan before the run starts/ })).toBeTruthy();
    expect(await screen.findByText('the "New Record" dialog is open')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/runs/r_parent/approve" && JSON.parse((init as RequestInit).body as string).decision === "approve")).toBe(true));
  });

  it("has a Plan tab with criteria results and amendments", async () => {
    view = parentRun({ result: { ...RESULT, plan: { steps: 3, criteria: 2, author: 1, inferred: 1, passed: 1, failed: 1, unverified: 0, not_run: 0, recoveries: 1, amendments: 1, lint_errors: 0, lint_warnings: 1, coverage_gaps: 0, strict: false, source: "model", review: "generated", planning_ms: 2300 } } });
    renderScreen();
    fireEvent.click(await screen.findByRole("tab", { name: /Plan/ }));
    expect(screen.getByRole("tab", { name: /Plan/ }).textContent).toContain("1/2 criteria · 1 amended · 1 lint");
    expect(await screen.findByRole("list", { name: "Amendments of step 1" })).toBeTruthy();
    expect(document.getElementById("panel-run")?.hidden).toBe(true);
  });
});

describe("RunDetail cloud runs", () => {
  it("runs a passed run on a cloud and shows the new cloud run on the page", async () => {
    view = parentRun({ status: "passed", result: { ...RESULT, status: "passed", automation: { steps: [] } } });
    let cloudRuns: unknown[] = [];
    const base = fetchMock.getMockImplementation() as (url: string, init?: RequestInit) => Promise<unknown>;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds") return respond(cloudsReport());
      if (url === "/api/runs/r_parent/cloud-runs" && init?.method === "POST") {
        cloudRuns = [cloudRun({ id: "cr_9", status: "starting", started_at: null })];
        return respond(cloudRuns[0], 202);
      }
      if (url === "/api/runs/r_parent/cloud-runs") return respond({ cloud_runs: cloudRuns });
      return base(url, init);
    });
    renderScreen();
    expect(await screen.findByText(/not run on a cloud yet/i)).toBeTruthy();
    fireEvent.click(header().getByRole("button", { name: /run on a cloud/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Run on BrowserStack" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const item = await screen.findByRole("listitem", { name: /BrowserStack, Chrome · Windows/ });
    expect(within(item).getByText("Starting")).toBeTruthy();
  });

  it("offers no cloud run for a failed run", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Demo login" });
    expect(header().queryByRole("button", { name: /run on a cloud/i })).toBeNull();
  });
});

describe("ordinal", () => {
  it("names when a step ran", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 103].map(ordinal)).toEqual(["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "103rd"]);
  });
});

describe("RunDetail edit case", () => {
  function withCases(source: unknown, cases: unknown[]) {
    const base = fetchMock.getMockImplementation() as (url: string, init?: RequestInit) => ReturnType<typeof respond>;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/runs/r_parent/case") return respond({ source, test_case: { id: "demo-login", title: "Demo login", steps: [{ action: "Open /login" }] } });
      if (url === "/api/cases") return respond({ cases });
      return base(url, init);
    });
  }
  const entry = (name: string) => ({ name, path: `.hercules/cases/${name}`, kind: "feature", valid: true, id: "demo-login", title: "Demo login", steps: 1 });

  it("links to the saved case the run used", async () => {
    withCases({ kind: "case_path", path: ".hercules/cases/demo-login.feature" }, [entry("demo-login.feature")]);
    renderScreen();
    const link = await screen.findByRole("link", { name: /edit case/i });
    expect(link.getAttribute("href")).toBe("/cases/demo-login.feature");
  });

  it("finds the case by id when the run came from unsaved Gherkin", async () => {
    withCases({ kind: "gherkin" }, [entry("demo-login.feature")]);
    renderScreen();
    expect((await screen.findByRole("link", { name: /edit case/i })).getAttribute("href")).toBe("/cases/demo-login.feature");
  });

  it("shows no edit link for a case that is not saved", async () => {
    withCases({ kind: "test_case" }, []);
    renderScreen();
    await screen.findByRole("heading", { name: "Demo login" });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/cases")).toBe(true));
    expect(screen.queryByRole("link", { name: /edit case/i })).toBeNull();
  });
});
