import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RunView } from "../lib/types";
import { StartRunDialog } from "./StartRunDialog";

const GHERKIN = 'Feature: Sign up\n  Scenario: New user\n    Given I open "/signup"\n';

const MODELS = {
  provider: "local",
  model: "local-model",
  tier: "small",
  egress_policy: "local",
  parallel_runs: 1,
  endpoint: null,
  capabilities: [],
  modes: { pure: { available: true, reason: null }, hybrid: { available: false, key_env: "JEV_API_KEY", key_set: false, default_egress_allows: false, reason: null } },
};

let fetchMock: ReturnType<typeof vi.fn>;

function respond(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)) });
}

beforeEach(() => {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/runs" && init?.method === "POST") return respond({ run_id: "r_1", status: "queued", ui_url: "/runs/r_1" }, 201);
    if (url === "/api/runs/r_parent/rerun" && init?.method === "POST") return respond({ run_id: "r_2", status: "queued", ui_url: "/runs/r_2" }, 201);
    if (url === "/api/cases") return respond({ cases: [] });
    if (url === "/api/environments") return respond({ environments: [{ name: "staging", base_url: "http://127.0.0.1:8124" }] });
    if (url === "/api/models") return respond(MODELS);
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDialog() {
  const onStarted = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StartRunDialog onClose={() => undefined} onStarted={onStarted} initial={{ source: "gherkin", gherkin: GHERKIN }} />
    </QueryClientProvider>,
  );
  return onStarted;
}

function postedBody(): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(([url, init]) => url === "/api/runs" && (init as RequestInit | undefined)?.method === "POST");
  expect(call).toBeTruthy();
  return JSON.parse((call![1] as RequestInit).body as string) as Record<string, unknown>;
}

const startButton = () => screen.getByRole("button", { name: "Start run" });
const runPosted = () => fetchMock.mock.calls.some(([url, init]) => url === "/api/runs" && (init as RequestInit | undefined)?.method === "POST");

describe("StartRunDialog options", () => {
  it("sends none of the quality or human-in-the-loop fields by default", async () => {
    const onStarted = renderDialog();
    fireEvent.click(startButton());
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("r_1"));
    expect(postedBody()).toEqual({ mode: "auto", gherkin: GHERKIN });
  });

  it("disables fail-on until a quality check is on, and sends the checks with it", async () => {
    const onStarted = renderDialog();
    const failOn = screen.getByLabelText("Fail the run on") as HTMLSelectElement;
    expect(failOn.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Accessibility" }));
    expect(failOn.disabled).toBe(false);
    fireEvent.click(screen.getByRole("checkbox", { name: "Visual" }));
    fireEvent.change(failOn, { target: { value: "serious" } });
    fireEvent.click(startButton());
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(postedBody()).toEqual({ mode: "auto", gherkin: GHERKIN, a11y: true, visual: true, fail_on: "serious" });
  });

  it("drops fail-on again when the checks are turned off", async () => {
    const onStarted = renderDialog();
    const a11y = screen.getByRole("checkbox", { name: "Accessibility" });
    fireEvent.click(a11y);
    fireEvent.change(screen.getByLabelText("Fail the run on"), { target: { value: "critical" } });
    fireEvent.click(a11y);
    expect((screen.getByLabelText("Fail the run on") as HTMLSelectElement).disabled).toBe(true);
    fireEvent.click(startButton());
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(postedBody()).toEqual({ mode: "auto", gherkin: GHERKIN });
  });

  it("sends the human-in-the-loop options", async () => {
    const onStarted = renderDialog();
    const terms = screen.getByLabelText("Destructive terms") as HTMLInputElement;
    const timeout = screen.getByLabelText("Answer timeout (seconds)") as HTMLInputElement;
    expect(terms.disabled).toBe(true);
    expect(timeout.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm destructive clicks" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Ask when a step fails" }));
    fireEvent.change(terms, { target: { value: " delete , archive,, wipe account " } });
    fireEvent.change(timeout, { target: { value: "120" } });
    fireEvent.click(startButton());
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(postedBody()).toEqual({
      mode: "auto",
      gherkin: GHERKIN,
      confirm_destructive: true,
      destructive_terms: ["delete", "archive", "wipe account"],
      ask_on_failure: true,
      approval_timeout_s: 120,
    });
  });

  it("uses the built-in terms and default timeout when those fields are left empty", async () => {
    const onStarted = renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm destructive clicks" }));
    fireEvent.click(startButton());
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(postedBody()).toEqual({ mode: "auto", gherkin: GHERKIN, confirm_destructive: true });
  });

  it("blocks a destructive term that is too long", async () => {
    renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm destructive clicks" }));
    const terms = screen.getByLabelText("Destructive terms");
    fireEvent.change(terms, { target: { value: `delete, ${"x".repeat(61)}` } });
    expect(terms.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("alert").textContent).toMatch(/at most 60 characters/);
    fireEvent.click(startButton());
    await waitFor(() => expect(document.activeElement).toBe(terms));
    expect(runPosted()).toBe(false);
  });

  it("blocks a timeout above 24 hours", async () => {
    renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "Ask when a step fails" }));
    fireEvent.change(screen.getByLabelText("Answer timeout (seconds)"), { target: { value: "90000" } });
    expect(screen.getByRole("alert").textContent).toMatch(/at most 86400/);
    fireEvent.click(startButton());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Answer timeout (seconds)")));
    expect(runPosted()).toBe(false);
  });
});

const PARENT: RunView = {
  run_id: "r_parent",
  status: "failed",
  finished: true,
  case_id: "demo-login",
  title: "Demo login",
  created_at: "2026-10-26T10:00:00+00:00",
  steps_done: 2,
  last_seq: 10,
  mode: "pure",
  egress: "local",
  run_dir: "/tmp/runs/r_parent",
  ui_url: "/runs/r_parent",
  summary: "FAILED",
  settings: {
    environment: "staging",
    base_url: "http://127.0.0.1:5555",
    mode: "pure",
    egress: "local",
    decider: null,
    headed: false,
    confirm_destructive: true,
    destructive_terms: ["wipe"],
    ask_on_failure: true,
    approval_timeout_s: 90,
    a11y: true,
    visual: false,
    fail_on: "serious",
  },
};

function renderRerun(scope: "whole" | "failed_only") {
  const onStarted = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StartRunDialog onClose={() => undefined} onStarted={onStarted} rerun={{ run: PARENT, scope }} />
    </QueryClientProvider>,
  );
  return onStarted;
}

function rerunBody(): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(([url, init]) => url === "/api/runs/r_parent/rerun" && (init as RequestInit | undefined)?.method === "POST");
  expect(call).toBeTruthy();
  return JSON.parse((call![1] as RequestInit).body as string) as Record<string, unknown>;
}

describe("StartRunDialog re-run", () => {
  it("opens prefilled with the parent run's options and without a case picker", async () => {
    renderRerun("whole");
    expect(screen.getByRole("heading", { name: "Re-run" })).toBeTruthy();
    expect(screen.queryByRole("radiogroup", { name: "Source" })).toBeNull();
    expect(screen.getByText("r_parent")).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText("Environment") as HTMLSelectElement).value).toBe("staging"));
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe("http://127.0.0.1:5555");
    expect((screen.getByLabelText("Mode") as HTMLSelectElement).value).toBe("pure");
    expect((screen.getByLabelText("Egress") as HTMLSelectElement).value).toBe("local");
    expect((screen.getByRole("checkbox", { name: "Accessibility" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Visual" }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText("Fail the run on") as HTMLSelectElement).value).toBe("serious");
    expect((screen.getByRole("checkbox", { name: "Confirm destructive clicks" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Ask when a step fails" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Destructive terms") as HTMLInputElement).value).toBe("wipe");
    expect((screen.getByLabelText("Answer timeout (seconds)") as HTMLInputElement).value).toBe("90");
    expect((screen.getByRole("checkbox", { name: "Show the browser window" }) as HTMLInputElement).checked).toBe(false);
  });

  it("sends the unchanged options to the re-run endpoint", async () => {
    const onStarted = renderRerun("whole");
    fireEvent.click(screen.getByRole("button", { name: "Start re-run" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("r_2"));
    expect(rerunBody()).toEqual({
      scope: "whole",
      environment: "staging",
      base_url: "http://127.0.0.1:5555",
      mode: "pure",
      egress: "local",
      headed: false,
      a11y: true,
      visual: false,
      fail_on: "serious",
      confirm_destructive: true,
      destructive_terms: ["wipe"],
      ask_on_failure: true,
      approval_timeout_s: 90,
      plan_review: null,
      strict: false,
    });
    expect(runPosted()).toBe(false);
  });

  it("sends the changed options and keeps the failed-only scope", async () => {
    const onStarted = renderRerun("failed_only");
    expect(screen.getByRole("heading", { name: "Re-run if failed" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "auto" } });
    fireEvent.change(screen.getByLabelText("Egress"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Accessibility" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm destructive clicks" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Show the browser window" }));
    fireEvent.click(screen.getByRole("button", { name: "Start re-run" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("r_2"));
    expect(rerunBody()).toEqual({
      scope: "failed_only",
      environment: "staging",
      base_url: null,
      mode: "auto",
      egress: null,
      headed: true,
      a11y: false,
      visual: false,
      fail_on: null,
      confirm_destructive: false,
      destructive_terms: null,
      ask_on_failure: true,
      approval_timeout_s: 90,
      plan_review: null,
      strict: false,
    });
  });

  it("shows the daemon's answer when it refuses the options", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/runs/r_parent/rerun" && init?.method === "POST") return respond({ error: "unknown environment 'staging'" }, 422);
      if (url === "/api/environments") return respond({ environments: [] });
      if (url === "/api/models") return respond(MODELS);
      return respond({ error: "not found" }, 404);
    });
    const onStarted = renderRerun("whole");
    await waitFor(() => expect(screen.getByRole("option", { name: "staging (not saved)" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Start re-run" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/unknown environment/));
    expect(onStarted).not.toHaveBeenCalled();
  });
});

describe("StartRunDialog environment", () => {
  function renderSaved() {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/runs" && init?.method === "POST") return respond({ run_id: "r_1", status: "queued", ui_url: "/runs/r_1" }, 201);
      if (url === "/api/cases")
        return respond({
          cases: [
            { name: "a.feature", path: ".hercules/cases/a.feature", kind: "feature", valid: true, id: "a", title: "A", steps: 1, environment: "staging" },
            { name: "b.feature", path: ".hercules/cases/b.feature", kind: "feature", valid: true, id: "b", title: "B", steps: 1, environment: null },
          ],
        });
      if (url === "/api/environments") return respond({ environments: [{ name: "staging", base_url: "http://127.0.0.1:8124" }, { name: "prod", base_url: "https://shop.example" }] });
      if (url === "/api/models") return respond(MODELS);
      return respond({ error: "not found" }, 404);
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <StartRunDialog onClose={() => undefined} onStarted={() => undefined} />
      </QueryClientProvider>,
    );
  }
  const select = () => screen.getByLabelText("Environment") as HTMLSelectElement;

  it("starts on the case's own environment, with None unavailable, and sends nothing extra", async () => {
    renderSaved();
    await waitFor(() => expect(select().value).toBe("staging"));
    expect(screen.queryByText("From the case")).toBeNull();
    expect((screen.getByRole("option", { name: "None" }) as HTMLOptionElement).disabled).toBe(true);
    expect(screen.getByText(/The case runs in staging/)).toBeTruthy();
    fireEvent.click(startButton());
    await waitFor(() => expect(runPosted()).toBe(true));
    expect(postedBody()).not.toHaveProperty("environment");
  });

  it("shows None for a case without one and sends a picked environment", async () => {
    renderSaved();
    await waitFor(() => expect(select().value).toBe("staging"));
    fireEvent.change(screen.getByLabelText(/^Case/), { target: { value: ".hercules/cases/b.feature" } });
    expect(select().value).toBe("");
    expect((screen.getByRole("option", { name: "None" }) as HTMLOptionElement).disabled).toBe(false);
    fireEvent.change(select(), { target: { value: "prod" } });
    fireEvent.click(startButton());
    await waitFor(() => expect(runPosted()).toBe(true));
    expect(postedBody().environment).toBe("prod");
  });

  it("reads the environment from pasted Gherkin", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("Gherkin"), { target: { value: `@env:staging\n${GHERKIN}` } });
    await waitFor(() => expect(select().value).toBe("staging"));
  });
});

describe("StartRunDialog option groups", () => {
  const group = (title: string) => screen.getByText(title, { selector: ".option-group-title" }).closest("details") as HTMLDetailsElement;

  it("starts with quality checks, plan, human in the loop and advanced collapsed", () => {
    renderDialog();
    for (const title of ["Quality checks", "Plan", "Human in the loop", "Advanced"]) expect(group(title).open).toBe(false);
    fireEvent.click(screen.getByText("Quality checks", { selector: ".option-group-title" }));
    expect(group("Quality checks").open).toBe(true);
  });

  it("opens a group that already holds a choice and summarises the closed ones", () => {
    renderRerun("whole");
    expect(group("Quality checks").open).toBe(true);
    expect(group("Human in the loop").open).toBe(true);
    expect(group("Plan").open).toBe(false);
  });
});
