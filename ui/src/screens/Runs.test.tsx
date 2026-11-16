import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import type { RunView } from "../lib/types";
import { ACTIVE_POLL_MS, IDLE_POLL_MS, RUN_LIMIT, RunsScreen, runsPollInterval } from "./Runs";

function run(index: number, status: RunView["status"] = "passed"): RunView {
  return { run_id: `r_${index}`, status, finished: status !== "running", created_at: "2026-11-01T10:00:00Z", steps_done: 1, last_seq: 1, run_dir: "", ui_url: "", summary: "" };
}

afterEach(() => vi.unstubAllGlobals());

describe("RunsScreen", () => {
  it("polls fast only while a run is active", () => {
    expect(runsPollInterval(undefined)).toBe(IDLE_POLL_MS);
    expect(runsPollInterval([run(1), run(2, "failed")])).toBe(IDLE_POLL_MS);
    expect(runsPollInterval([run(1), run(2, "running")])).toBe(ACTIVE_POLL_MS);
    expect(IDLE_POLL_MS).toBeGreaterThanOrEqual(10_000);
  });

  it("says so when the list is cut at the limit", async () => {
    const runs = Array.from({ length: RUN_LIMIT }, (_, index) => run(index));
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ runs }), { status: 200 }))),
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Router hook={memoryLocation({ path: "/" }).hook}>
          <RunsScreen />
        </Router>
      </QueryClientProvider>,
    );
    expect((await screen.findByRole("note")).textContent).toMatch(new RegExp(`newest ${RUN_LIMIT} runs`));
  });
});
