import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CloudRun } from "../lib/types";
import { cloudRun } from "../test/cloudFixtures";
import { CloudRunsPanel } from "./CloudRunsPanel";

let fetchMock: ReturnType<typeof vi.fn>;
let states: CloudRun[];
let listCalls: number;

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  listCalls = 0;
  states = [
    cloudRun({ status: "starting", started_at: null }),
    cloudRun({ status: "running" }),
    cloudRun({ status: "passed", finished_at: "2026-11-12T10:00:42Z", session_url: "https://example.test/sessions/abc", message: "1 passed", exit_code: 0 }),
  ];
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/runs/r_parent/cloud-runs") {
      const state = states[Math.min(listCalls, states.length - 1)];
      listCalls += 1;
      return respond({ cloud_runs: [state] });
    }
    if (url === "/api/cloud-runs/cr_1") return respond({ ...states[Math.min(listCalls, states.length) - 1], log: "tests/test_login.py::test_login PASSED\n1 passed in 9.1s" });
    if (url === "/api/cloud-runs/cr_1/cancel" && init?.method === "POST") return respond(cloudRun({ status: "cancelled" }));
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function renderPanel(onRunOnCloud?: () => void) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CloudRunsPanel runId="r_parent" onRunOnCloud={onRunOnCloud} />
    </QueryClientProvider>,
  );
}

describe("CloudRunsPanel", () => {
  it("polls until the cloud run is final, then shows its links and stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPanel();
    await screen.findByText("Starting");
    expect(screen.getByRole("button", { name: /cancel the browserstack run/i })).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    await screen.findByText("Running");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    await screen.findByText("Passed");
    expect(screen.getByRole("status").textContent).toBe("BrowserStack run passed");
    const session = screen.getByRole("link", { name: /open the browserstack session/i });
    expect(session.getAttribute("href")).toBe("https://example.test/sessions/abc");
    expect(session.getAttribute("target")).toBe("_blank");
    expect(session.getAttribute("rel")).toContain("noopener");
    expect(screen.getByRole("link", { name: /open the browserstack dashboard/i }).getAttribute("href")).toBe("https://example.test/dashboard");
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
    expect(screen.getByText("1 passed")).toBeTruthy();
    expect(screen.getByText("40 s")).toBeTruthy();
    const calls = listCalls;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(listCalls).toBe(calls);
  });

  it("shows the output log when expanded", async () => {
    states = [states[2]];
    renderPanel();
    await screen.findByText("Passed");
    const details = screen.getByText("Output").closest("details") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect((await screen.findByLabelText(/output of the browserstack run/i)).textContent).toMatch(/1 passed in 9\.1s/);
  });

  it("cancels a running cloud run", async () => {
    states = [states[1]];
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /cancel the browserstack run/i }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/cloud-runs/cr_1/cancel")).toBe(true));
  });

  it("invites a first cloud run when there is none", async () => {
    states = [];
    fetchMock.mockImplementation(() => respond({ cloud_runs: [] }));
    const onRun = vi.fn();
    renderPanel(onRun);
    expect(await screen.findByText(/not run on a cloud yet/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /run on a cloud/i }));
    expect(onRun).toHaveBeenCalled();
  });

  it("renders nothing when the daemon has no cloud runs endpoint", async () => {
    fetchMock.mockImplementation(() => respond({ error: "not found" }, 404));
    renderPanel(() => undefined);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole("heading", { name: /cloud runs/i })).toBeNull());
  });
});
