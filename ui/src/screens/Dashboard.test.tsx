import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { App } from "../App";
import type { DashboardReport, RunView } from "../lib/types";
import { cloudsReport } from "../test/cloudFixtures";
import { DashboardScreen } from "./Dashboard";

function run(id: string, status: RunView["status"], extra: Partial<RunView> = {}): RunView {
  return { run_id: id, status, finished: !["running", "queued"].includes(status), created_at: "2026-11-18T10:00:00Z", steps_done: 1, last_seq: 1, run_dir: "", ui_url: "", summary: "", title: `Case ${id}`, ...extra };
}

function report(overrides: Partial<DashboardReport> = {}): DashboardReport {
  const daily = Array.from({ length: 14 }, (_, index) => ({ date: `2026-11-${String(index + 5).padStart(2, "0")}`, passed: index % 3, failed: index % 4 === 0 ? 1 : 0, other: 0 }));
  return {
    days: 7,
    time_zone: "UTC",
    generated_at: "2026-11-18T12:00:00+00:00",
    current: { runs: 48, finished: 46, passed: 40, failed: 6, pass_rate: 0.87, median_duration_s: 102, cost_usd: 3.18, costed_runs: 46 },
    previous: { runs: 36, finished: 36, passed: 30, failed: 6, pass_rate: 0.83, median_duration_s: 100, cost_usd: 2.5, costed_runs: 36 },
    daily,
    ...overrides,
  };
}

let routes: Record<string, unknown>;
let urls: string[];

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  urls = [];
  routes = {
    "/api/health": { status: "ok", version: "2.0.0", project_dir: "/work/shop-tests", parallel_runs: 1, queue: {}, user: "sam", egress_policy: "hybrid" },
    "/api/runs?limit=50": { runs: [run("r_live", "running", { current_step: 3, total_steps: 8, current_action: "Click Add to cart" }), run("r_q", "queued"), run("r_ok", "passed", { environment: "lumen_beach" }), run("r_bad", "failed")] },
    "/api/runs?status=needs_approval&limit=20": { runs: [run("r_ask", "needs_approval", { approval: { step: 5, reason: "Step 5 wants to submit a payment form" } })] },
    "/api/runs?status=needs_plan_approval&limit=20": { runs: [] },
    "/api/cases": { cases: [{ name: "a.feature", path: "a.feature", kind: "feature", valid: true, status: "ready" }, { name: "b.feature", path: "b.feature", kind: "feature", valid: true, status: "draft" }], folders: ["Checkout"] },
    "/api/environments": { environments: [{ name: "lumen_beach", base_url: "https://x.test", secret_names: [] }, { name: "phone", secret_names: [], mobile: { platform: "android" } }] },
    "/api/models": { provider: "local", model: "qwen3-vl-8b", tier: "standard", egress_policy: "hybrid", parallel_runs: 1, endpoint: null, capabilities: [], modes: { pure: { available: true, reason: null }, hybrid: { available: true, key_env: "JEV", key_set: true, default_egress_allows: true, reason: null } } },
    "/api/clouds": cloudsReport(),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      urls.push(url);
      if (url.startsWith("/api/dashboard")) return respond(routes["/api/dashboard"] ?? report());
      return url in routes ? respond(routes[url]) : respond({ error: "not found" }, 404);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

function renderAt(path: string, node = <DashboardScreen />) {
  const location = memoryLocation({ path, record: true });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Router hook={location.hook} searchHook={location.searchHook}>
        {node}
      </Router>
    </QueryClientProvider>,
  );
  return location;
}

describe("DashboardScreen", () => {
  it("greets the user and shows the week's numbers against the week before", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1, name: /, sam$/ })).toBeTruthy();
    expect(screen.getByText(/shop-tests/)).toBeTruthy();
    const stats = within(await screen.findByRole("region", { name: "Last 7 days" }));
    expect(stats.getByText("48")).toBeTruthy();
    expect(stats.getByText("▲ 12 vs the week before")).toBeTruthy();
    expect(stats.getByText("87%")).toBeTruthy();
    expect(stats.getByText("▲ 4 pts")).toBeTruthy();
    expect(stats.getByText("1m 42s")).toBeTruthy();
    expect(stats.getByText("$3.18")).toBeTruthy();
    expect(urls.some((url) => /^\/api\/dashboard\?days=7&tz=/.test(url))).toBe(true);
  });

  it("lists the runs waiting for an answer, and hides the section when none are", async () => {
    renderAt("/");
    const waiting = within(await screen.findByRole("region", { name: /Waiting for you/ }));
    expect(waiting.getByText("Case r_ask")).toBeTruthy();
    expect(waiting.getByText("Step 5 wants to submit a payment form")).toBeTruthy();
    expect(waiting.getByRole("link", { name: "Review" }).getAttribute("href")).toBe("/runs/r_ask");
  });

  it("hides the waiting section when nothing waits", async () => {
    routes["/api/runs?status=needs_approval&limit=20"] = { runs: [] };
    renderAt("/");
    await screen.findByRole("region", { name: "Recent runs" });
    expect(screen.queryByRole("region", { name: /Waiting for you/ })).toBeNull();
  });

  it("shows what runs now, the queue and the recent runs", async () => {
    renderAt("/");
    const now = within(await screen.findByRole("region", { name: "Running now" }));
    expect(await now.findByText("Case r_live")).toBeTruthy();
    expect(now.getByText("3/8")).toBeTruthy();
    expect(now.getByText("Click Add to cart")).toBeTruthy();
    expect(now.getByText("Queued · 1")).toBeTruthy();
    const recent = within(screen.getByRole("region", { name: "Recent runs" }));
    expect(recent.getAllByRole("row")).toHaveLength(5);
    expect(recent.getByText("lumen_beach")).toBeTruthy();
    expect(recent.getByRole("link", { name: "All runs →" }).getAttribute("href")).toBe("/runs");
  });

  it("draws a bar per day with a readable label", async () => {
    renderAt("/");
    const bars = await screen.findByRole("list", { name: "Runs per day, oldest first" });
    expect(within(bars).getAllByRole("listitem")).toHaveLength(14);
    expect(within(bars).getAllByRole("listitem")[0].getAttribute("aria-label")).toMatch(/1 run \(0 passed, 1 failed, 0 other\)/);
  });

  it("sums up the setup with links to each page", async () => {
    renderAt("/");
    const setup = within(await screen.findByRole("region", { name: "Your setup" }));
    await waitFor(() => expect(setup.getByText("2 cases")).toBeTruthy());
    expect(setup.getByText("1 draft · 1 folder")).toBeTruthy();
    expect(setup.getByText("1 web · 1 native app")).toBeTruthy();
    expect(setup.getByText("qwen3-vl-8b")).toBeTruthy();
    expect(setup.getByText("hybrid mode ready")).toBeTruthy();
    expect(setup.getByText("2 clouds")).toBeTruthy();
    expect(setup.getByRole("link", { name: /Integrations/ }).getAttribute("href")).toBe("/integrations");
  });

  it("offers first steps instead of empty charts on a new project", async () => {
    routes["/api/runs?limit=50"] = { runs: [] };
    routes["/api/runs?status=needs_approval&limit=20"] = { runs: [] };
    renderAt("/");
    const start = within(await screen.findByRole("region", { name: "Get started" }));
    expect(start.getByRole("link", { name: /Write a test case/ }).getAttribute("href")).toBe("/cases");
    expect(start.getByRole("button", { name: /Start your first run/ })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Recent runs" })).toBeNull();
  });
});

describe("the home route", () => {
  it("is the dashboard, with Dashboard above Runs in the menu", async () => {
    renderAt("/", <App />);
    expect(await screen.findByRole("heading", { level: 1, name: /, sam$/ })).toBeTruthy();
    const links = within(screen.getByRole("navigation", { name: "Main" })).getAllByRole("link").map((link) => link.textContent);
    expect(links.slice(0, 3)).toEqual(["Dashboard", "Cases", "Runs"]);
    expect(screen.getByRole("link", { name: "Dashboard" }).getAttribute("aria-current")).toBe("page");
  });

  it("dissolves the page when a sidebar tab leads to another page, and not for the current one", async () => {
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
    routes["/api/runs?limit=200&with_result=true"] = { runs: [] };
    renderAt("/", <App />);
    await screen.findByRole("heading", { level: 1, name: /, sam$/ });
    const main = document.getElementById("main") as HTMLElement;
    main.getBoundingClientRect = () => ({ left: 208, top: 0, right: 1208, bottom: 700, width: 1000, height: 700, x: 208, y: 0, toJSON: () => ({}) });
    fireEvent.click(screen.getByRole("link", { name: "Dashboard" }), { detail: 1, clientX: 100, clientY: 120 });
    expect(document.querySelector(".page-dissolve")).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: /^Runs/ }), { detail: 1, clientX: 100, clientY: 160 });
    expect(document.querySelector(".page-dissolve")).toBeTruthy();
    expect(document.querySelector(".page-dissolve h1")?.textContent).toMatch(/, sam$/);
    document.querySelectorAll(".page-dissolve").forEach((layer) => layer.remove());
  });

  it("sends an old link to the runs list on with its filters", async () => {
    routes["/api/runs?limit=200&with_result=true"] = { runs: [] };
    const location = renderAt("/?status=failed", <App />);
    await waitFor(() => expect(location.history?.at(-1)).toBe("/runs?status=failed"));
    expect(screen.getByRole("link", { name: /^Runs/ }).getAttribute("aria-current")).toBe("page");
  });
});
