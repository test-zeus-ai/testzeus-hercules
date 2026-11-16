import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { EMPTY_START, TRANSITIONS, charterCounts, eventLine, findingSummary, formFromSpec, quickDecisions, sessionTime, severityText, specFromForm, timeShare, type Finding, type SessionView } from "../lib/explore";
import { ExploreScreen } from "./Explore";
import { ExploreSessionScreen } from "./ExploreSession";

const FINDING: Finding = {
  id: "f0123456789",
  title: "Saving a 41-character name returns a server error",
  signature: "server_error|/profile|5xx",
  oracle: "server_error",
  tier: "code",
  proposed_severity: "P2",
  severity: null,
  category: "product_bug",
  steps: ['Open "/profile"', 'Fill "xxx" in "Display name" and click the "Save profile" button'],
  expected: "The name is saved or refused with a message",
  actual: "HTTP 500",
  reproduced_recorded: "3/3",
  reproduced_interpreted: "1/1",
  flaky: "none",
  case_path: ".hercules/cases/explore/x_1/f0123456789.json",
  run_ids: ["r_1"],
  evidence: [],
  state: "new",
  audit: [],
  sessions: ["x_1"],
  regression: false,
  first_seen: "2026-11-13T10:00:00Z",
  last_seen: "2026-11-13T10:00:00Z",
};

function session(overrides: Partial<SessionView> = {}): SessionView {
  return {
    id: "x_20261113_100000_abcdef",
    status: "exploring",
    spec: { mission: "p1", base_url: "http://localhost:3000", workers: 2, reproducers: 1, budget: { time_s: 600 } },
    phase_detail: "2 explorers running",
    workers: 2,
    reproducers: 1,
    size_changes: [],
    started_at: "2026-11-13T10:00:00Z",
    elapsed_s: 150,
    cost_usd: 0.02,
    charters: [
      { id: "ch-a", title: "Explore /cart with unusual inputs", kind: "explore", area: "/cart", levers: ["inputs"], timebox_s: 300, priority: 0.7, origin: "coordinator", pinned: false, status: "queued", notes: [] },
      { id: "ch-b", title: "Recon", kind: "recon", area: "/", levers: [], timebox_s: 60, priority: 1, origin: "coordinator", pinned: false, status: "done", notes: [] },
    ],
    clues: 3,
    findings: [FINDING],
    ux_notes: [],
    coverage: { routes_seen: ["/", "/cart"], states_seen: 4, moves_tried: 9, gaps: [{ route: "/cart", levers_not_used: ["order"] }], charters_not_run: [] },
    children: [],
    message: "",
    events: [
      { seq: 1, kind: "clue", data: { title: "POST /profile returned 500" }, at: 1 },
      { seq: 2, kind: "finding", data: { title: FINDING.title, severity: "P2", outcome: "new" }, at: 2 },
    ],
    last_seq: 2,
    ...overrides,
  };
}

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function wrap(node: ReactNode, path = "/explore") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Router hook={memoryLocation({ path }).hook}>{node}</Router>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("explore helpers", () => {
  it("fills the start form from an earlier session so it can run again", () => {
    const form = formFromSpec({ mission: "forms", base_url: "https://shop.test", workers: 5, reproducers: 2, budget: { time_s: 600, max_cost_usd: 2 }, scope: { deny_paths: ["/logout", "/admin"] }, levers: ["inputs"] });
    expect(form).toMatchObject({ where: "base_url", base_url: "https://shop.test", mission: "forms", minutes: "10", workers: "5", reproducers: "2", max_cost: "2", deny_paths: "/logout, /admin", levers: ["inputs"] });
    const built = specFromForm(form);
    expect("spec" in built && built.spec).toMatchObject({ base_url: "https://shop.test", workers: 5, budget: { time_s: 600, max_cost_usd: 2 }, scope: { deny_paths: ["/logout", "/admin"] } });
  });

  it("shows a finished session's real duration, not the clock since it started", () => {
    const format = (seconds: number) => `${Math.round(seconds)}s`;
    expect(sessionTime({ status: "exploring", elapsed_s: 30, spec: { budget: { time_s: 300 } } as SessionView["spec"] }, format).text).toBe("30s / 300s");
    const done = sessionTime({ status: "finished", elapsed_s: 600, spec: { budget: { time_s: 300 } } as SessionView["spec"] }, format);
    expect(done.text).toBe("600s");
    expect(done.title).toMatch(/reproducing/);
  });

  it("offers the usual next decision first and summarises the observed problem", () => {
    expect(quickDecisions("new")[0]).toMatchObject({ label: "Real bug", state: "confirmed" });
    expect(quickDecisions("new").find((item) => item.label === "Not a bug")?.needsReason).toBe(true);
    expect(quickDecisions("verify_fixed")[0]).toMatchObject({ state: "closed" });
    expect(findingSummary({ actual: "\n  Warning: first line\nsecond" })).toBe("Warning: first line");
  });

  it("builds a spec from the start form and keeps the swarm size as given", () => {
    const built = specFromForm({ ...EMPTY_START, base_url: "http://localhost:3000", workers: "12", reproducers: "3", minutes: "45", deny_paths: "/admin, /billing", max_cost: "2" });
    expect(built).toEqual({
      spec: {
        base_url: "http://localhost:3000",
        mission: "p1",
        workers: 12,
        reproducers: 3,
        budget: { time_s: 2700, max_cost_usd: 2 },
        levers: ["inputs", "order", "conditions", "time", "people"],
        scope: { deny_paths: ["/admin", "/billing"] },
      },
    });
  });

  it("says what is wrong with the form", () => {
    expect(specFromForm(EMPTY_START)).toEqual({ error: "Give the app's base URL" });
    expect(specFromForm({ ...EMPTY_START, base_url: "localhost" })).toEqual({ error: "The base URL starts with http:// or https://" });
    expect(specFromForm({ ...EMPTY_START, base_url: "http://x.test", workers: "0" })).toMatchObject({ error: expect.stringContaining("Explorers") });
    expect(specFromForm({ ...EMPTY_START, base_url: "http://x.test", workers: "65" })).toMatchObject({ error: expect.stringContaining("1 to 64") });
    expect(specFromForm({ ...EMPTY_START, base_url: "http://x.test", mission: "changed" })).toMatchObject({ error: expect.stringContaining("what changed") });
    expect(specFromForm({ ...EMPTY_START, base_url: "http://x.test", deny_paths: "admin" })).toMatchObject({ error: expect.stringContaining("start with /") });
    expect(specFromForm({ ...EMPTY_START, base_url: "http://x.test", levers: [] })).toEqual({ error: "Choose at least one lever" });
  });

  it("marks proposed severities and counts charters", () => {
    expect(severityText(FINDING)).toEqual({ level: "P2", label: "P2 proposed", confirmed: false });
    expect(severityText({ ...FINDING, severity: "P0" })).toEqual({ level: "P0", label: "P0", confirmed: true });
    expect(charterCounts(session().charters)).toMatchObject({ queued: 1, done: 1, running: 0 });
    expect(timeShare(session())).toBe(0.25);
    expect(timeShare(session({ elapsed_s: 9999 }))).toBe(1);
    expect(TRANSITIONS.new).toContain("in_tracker");
  });

  it("turns events into feed lines and skips the rest", () => {
    expect(eventLine({ seq: 1, kind: "clue", data: { title: "boom" }, at: 0 })).toEqual({ tone: "clue", text: "Clue: boom" });
    expect(eventLine({ seq: 2, kind: "finding", data: { title: "boom", severity: "P1", outcome: "regression" }, at: 0 })?.text).toBe("Finding (regression, P1 proposed): boom");
    expect(eventLine({ seq: 3, kind: "internal", data: {}, at: 0 })).toBeNull();
  });
});

describe("ExploreScreen", () => {
  it("lists sessions with their swarm and findings", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond({ sessions: [session()] })));
    wrap(<ExploreScreen />);
    const link = await screen.findByRole("link", { name: "P1 hunt" });
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("2 explorers, 1 reproducer")).toBeTruthy();
    expect(within(row).getByText("Exploring")).toBeTruthy();
  });

  it("opens a session from anywhere on its row", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond({ sessions: [session()] })));
    const memory = memoryLocation({ path: "/explore", record: true });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Router hook={memory.hook}>
          <ExploreScreen />
        </Router>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText("2 explorers, 1 reproducer"));
    expect(memory.history.at(-1)).toBe(`/explore/${session().id}`);
  });

  it("shows the preflight estimate and refuses a size over the guard rail until allowed", async () => {
    const posted: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url === "/api/explore") return init?.method === "POST" ? (posted.push(JSON.parse(String(init.body))), respond(session(), 202)) : respond({ sessions: [] });
        if (url === "/api/explore/preflight") {
          const body = JSON.parse(String(init?.body));
          return respond({ problems: body.workers > 8 ? ["12 explorers is more than explore.max_workers (8)"] : [], estimate: { workers: body.workers, reproducers: 1, time_s: 1800, decisions: 2700, cost_usd: 1.35, note: "" } });
        }
        return respond({ environments: [], cases: [] });
      }),
    );
    wrap(<ExploreScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Start the first session" }));
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:3000" } });
    fireEvent.change(screen.getByLabelText("Explorers"), { target: { value: "12" } });
    expect(await screen.findByText("12 explorers is more than explore.max_workers (8)", undefined, { timeout: 3000 })).toBeTruthy();
    const startButton = screen.getByRole("button", { name: "Start" }) as HTMLButtonElement;
    expect(startButton.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Start anyway at this size"));
    await waitFor(() => expect(startButton.disabled).toBe(false));
    fireEvent.click(startButton);
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ workers: 12, allow_more_workers: true, base_url: "http://localhost:3000", by: "the tester UI" });
  });

  it("triages a finding with a reason for dismissals", async () => {
    const triaged: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.startsWith("/api/explore/findings/f0123456789/triage")) {
          triaged.push(JSON.parse(String(init?.body)));
          return respond({ ...FINDING, state: "wont_fix" });
        }
        if (url.startsWith("/api/explore/findings")) return respond({ findings: [FINDING], precision: { precision: null, confirmed: 0, dismissed: 0, untriaged: 1, triaged: 0, interval_90: null } });
        return respond({ sessions: [] });
      }),
    );
    wrap(<ExploreScreen />, "/explore?tab=findings");
    fireEvent.click(await screen.findByRole("button", { name: /Saving a 41-character name/ }));
    expect(screen.getByText("P2 proposed")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("State"), { target: { value: "wont_fix" } });
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "ana" } });
    expect(screen.getByRole("alert").textContent).toMatch(/Say why/);
    const save = screen.getByRole("button", { name: "Save triage" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "legacy limit, documented" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(triaged).toEqual([{ by: "ana", state: "wont_fix", severity: null, tracker: null, duplicate_of: null, case: null, reason: "legacy limit, documented" }]));
  });
});

describe("FindingDetail quick decisions", () => {
  const ledger = (you?: string) => ({ findings: [FINDING], precision: { precision: null, confirmed: 0, dismissed: 0, untriaged: 1, triaged: 0, interval_90: null }, you });

  it("confirms a real bug in one click with the git user's name", async () => {
    const triaged: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.startsWith("/api/explore/findings/f0123456789/triage")) {
          triaged.push(JSON.parse(String(init?.body)));
          return respond({ ...FINDING, state: "confirmed", case: "kept" });
        }
        if (url.startsWith("/api/explore/findings")) return respond(ledger("Ana Tester"));
        return respond({ sessions: [] });
      }),
    );
    wrap(<ExploreScreen />, "/explore?tab=findings&finding=f0123456789");
    fireEvent.click(await screen.findByRole("button", { name: "Real bug" }));
    await waitFor(() => expect(triaged).toEqual([{ by: "Ana Tester", state: "confirmed", severity: null, tracker: null, duplicate_of: null, case: null, reason: "" }]));
    expect(await screen.findByText(/Saved: Confirmed, case kept/)).toBeTruthy();
  });

  it("asks why before dismissing a finding as not a bug", async () => {
    const triaged: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.startsWith("/api/explore/findings/f0123456789/triage")) {
          triaged.push(JSON.parse(String(init?.body)));
          return respond({ ...FINDING, state: "by_design", case: "rejected" });
        }
        if (url.startsWith("/api/explore/findings")) return respond(ledger("Ana Tester"));
        return respond({ sessions: [] });
      }),
    );
    wrap(<ExploreScreen />, "/explore?tab=findings&finding=f0123456789");
    fireEvent.click(await screen.findByRole("button", { name: "Not a bug" }));
    expect(screen.getByText(/rejects its case/)).toBeTruthy();
    const confirm = screen.getAllByRole("button", { name: "Not a bug" }).at(-1) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Why"), { target: { value: "the limit is documented" } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(triaged).toEqual([{ by: "Ana Tester", state: "by_design", severity: null, tracker: null, duplicate_of: null, case: null, reason: "the limit is documented" }]));
  });
});

describe("FindingDetail case decisions", () => {
  it("rejects a useless case with a reason and keeps another as a regression test", async () => {
    const triaged: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.startsWith("/api/explore/findings/f0123456789/triage")) {
          triaged.push(JSON.parse(String(init?.body)));
          return respond({ ...FINDING, case: "rejected" });
        }
        if (url.startsWith("/api/explore/findings")) return respond({ findings: [FINDING], precision: { precision: null, confirmed: 0, dismissed: 0, untriaged: 1, triaged: 0, interval_90: null } });
        return respond({ sessions: [] });
      }),
    );
    wrap(<ExploreScreen />, "/explore?tab=findings");
    fireEvent.click(await screen.findByRole("button", { name: /Saving a 41-character name/ }));
    expect(screen.getByText(/Waits outside your cases/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Case"), { target: { value: "rejected" } });
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "ana" } });
    expect(screen.getByRole("alert").textContent).toMatch(/never proposed again/);
    const save = screen.getByRole("button", { name: "Save triage" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a third-party script" } });
    fireEvent.click(save);
    await waitFor(() => expect(triaged).toEqual([{ by: "ana", state: null, severity: null, tracker: null, duplicate_of: null, case: "rejected", reason: "a third-party script" }]));
  });
});

describe("ExploreSessionScreen", () => {
  it("shows the live session, its feed, coverage gaps and steers charters", async () => {
    const calls: Array<[string, string | undefined, unknown]> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push([url, init?.method, init?.body ? JSON.parse(String(init.body)) : null]);
        if (url.includes("/charters/")) return respond({ id: "ch-a" });
        if (init?.method === "PATCH") return respond(session({ workers: 3 }));
        return respond(session());
      }),
    );
    wrap(<ExploreSessionScreen sessionId="x_20261113_100000_abcdef" />, "/explore/x_20261113_100000_abcdef");
    expect(await screen.findByText("Clue: POST /profile returned 500")).toBeTruthy();
    expect(screen.getByText("Finding (new, P2 proposed): Saving a 41-character name returns a server error")).toBeTruthy();
    expect(screen.getByText(/levers not used: order/)).toBeTruthy();
    expect(screen.getByText("3 → 1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Run next" }));
    await waitFor(() => expect(calls.some(([url, method, body]) => url.endsWith("/charters/ch-a") && method === "POST" && (body as { action: string }).action === "pin")).toBe(true));
    fireEvent.change(screen.getByLabelText("Explorers"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(calls.some(([, method, body]) => method === "PATCH" && (body as { workers: number }).workers === 3)).toBe(true));
  });

  it("says a past session is read-only when the daemon does not know it", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond({ error: "no such session (sessions from an earlier daemon are read-only: see their report)" }, 404)));
    wrap(<ExploreSessionScreen sessionId="x_20260101_000000_abcdef" />, "/explore/x_20260101_000000_abcdef");
    expect(await screen.findByRole("link", { name: "open its report" })).toBeTruthy();
  });
});
