import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "@yusufceng1z/ding";
import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import type { SessionView } from "../lib/explore";
import { NOTIFY_KEY, resetPrefsCache, type NotifyPrefs } from "../lib/notify/prefs";
import type { RunView } from "../lib/types";
import { run, session } from "../test/notifyFixtures";
import { Notifications } from "./Notifications";

const chime = vi.hoisted(() => vi.fn());
vi.mock("@yusufceng1z/ding", async (original) => ({ ...(await original<typeof import("@yusufceng1z/ding")>()), chime }));

let runs: RunView[] = [];
let sessions: SessionView[] = [];
const desktop = vi.fn();
let saved: Map<string, string>;

class FakeNotification {
  static permission: NotificationPermission = "granted";
  static requestPermission = vi.fn(() => Promise.resolve("granted"));
  onclick: (() => void) | null = null;
  close = vi.fn();
  constructor(title: string, options: NotificationOptions) {
    desktop(title, options, this);
  }
}

function prefs(patch: Partial<NotifyPrefs>): void {
  saved.set(NOTIFY_KEY, JSON.stringify(patch));
  resetPrefsCache();
}

beforeEach(() => {
  runs = [];
  sessions = [];
  desktop.mockReset();
  chime.mockReset();
  saved = new Map();
  resetPrefsCache();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => void saved.set(key, value),
    removeItem: (key: string) => void saved.delete(key),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const body = url.startsWith("/api/runs") ? { runs } : url.startsWith("/api/explore") ? { sessions } : {};
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
    }),
  );
  vi.stubGlobal("Notification", FakeNotification);
  vi.stubGlobal("isSecureContext", true);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  for (const kind of ["success", "error", "warning", "info", "dismiss"] as const) vi.spyOn(toast, kind).mockImplementation(() => "id" as never);
  document.title = "Hercules";
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function mount(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { hook, history } = memoryLocation({ path, record: true });
  render(
    <QueryClientProvider client={client}>
      <Router hook={hook}>
        <Notifications />
      </Router>
    </QueryClientProvider>,
  );
  // React Query tells the component about new data on the next tick: wait for it too.
  const poll = async () => {
    await act(() => client.refetchQueries());
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
  };
  return { client, poll, history };
}

async function firstPoll(client: QueryClient) {
  await waitFor(() => expect(client.getQueryState(["runs", { notify: true }])?.dataUpdateCount).toBeGreaterThan(0));
}

describe("Notifications", () => {
  it("announces a run that ends while the page is open, not one that ended before", async () => {
    runs = [run("old", "passed"), run("a", "running")];
    const { client, poll } = mount();
    await firstPoll(client);
    expect(toast.success).not.toHaveBeenCalled();

    runs = [run("old", "passed"), run("a", "failed", { reason: "The cart total was $0" })];
    await poll();

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith("Case a failed", expect.objectContaining({ id: "run:a", description: "The cart total was $0", duration: 0, action: expect.objectContaining({ label: "View run" }) }));
    expect(toast.success).not.toHaveBeenCalled();
    expect(desktop).not.toHaveBeenCalled(); // the page is in front
    expect(chime).not.toHaveBeenCalled(); // sound is off by default
    await poll();
    expect(toast.error).toHaveBeenCalledTimes(1);
  });

  it("announces after a reload what ended while the page was reloading", async () => {
    runs = [run("a", "running")];
    const first = mount();
    await firstPoll(first.client);
    expect(window.sessionStorage.getItem("hercules.notify.runs-until")).toBeNull(); // nothing has ended yet
    runs = [run("a", "passed", { finished_at: "2026-11-20T10:00:30Z" }), run("b", "running")];
    await first.poll();
    expect(toast.success).toHaveBeenCalledTimes(1);

    runs = [run("a", "passed", { finished_at: "2026-11-20T10:00:30Z" }), run("b", "failed", { finished_at: "2026-11-20T10:59:00Z" })];
    const second = mount(); // the browser woke a tab it had put to sleep
    await firstPoll(second.client);
    expect(toast.success).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith("Case b failed", expect.objectContaining({ id: "run:b" }));
  });

  it("opens the run from the toast with the page dissolve", async () => {
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    const main = document.body.appendChild(Object.assign(document.createElement("main"), { id: "main" }));
    main.getBoundingClientRect = () => ({ left: 208, top: 0, right: 1208, bottom: 700, width: 1000, height: 700, x: 208, y: 0, toJSON: () => ({}) });
    runs = [run("a", "running")];
    const { client, poll, history } = mount();
    await firstPoll(client);
    runs = [run("a", "passed")];
    await poll();

    const [, options] = vi.mocked(toast.success).mock.calls[0];
    act(() => void options?.action?.onClick());
    expect(document.querySelector(".page-dissolve")).toBeTruthy();
    expect(history.at(-1)).toBe("/runs/a");
    document.querySelectorAll(".page-dissolve").forEach((layer) => layer.remove());
    main.remove();
  });

  it("in the background: a desktop notification and a count in the tab title, cleared on return", async () => {
    prefs({ desktop: true, sound: true });
    runs = [run("a", "running")];
    const { client, poll, history } = mount();
    await firstPoll(client);
    vi.mocked(document.hasFocus).mockReturnValue(false);
    const focus = vi.spyOn(window, "focus").mockImplementation(() => undefined);

    runs = [run("a", "passed")];
    await poll();

    // It stays until dismissed: one that closed on its own would be gone before the tester is back.
    expect(toast.success).toHaveBeenCalledWith("Case a passed", expect.objectContaining({ duration: 0 }));
    expect(desktop).toHaveBeenCalledWith("Case a passed", expect.objectContaining({ body: "1m 42s", tag: "run:a", requireInteraction: false }), expect.anything());
    expect(chime).not.toHaveBeenCalled(); // the system notification makes the sound
    expect(document.title).toBe("(1) Hercules");

    const shown = desktop.mock.calls[0][2] as FakeNotification;
    act(() => shown.onclick?.());
    expect(focus).toHaveBeenCalled();
    expect(history.at(-1)).toBe("/runs/a");
    expect(shown.close).toHaveBeenCalled();

    vi.mocked(document.hasFocus).mockReturnValue(true);
    act(() => void fireEvent.focus(window));
    expect(document.title).toBe("Hercules");
  });

  it("chimes in front of the page when sound is on, and not for runs the tester does not want", async () => {
    prefs({ sound: true, runs: "failures" });
    runs = [run("a", "running"), run("b", "running")];
    const { client, poll } = mount();
    await firstPoll(client);

    runs = [run("a", "passed"), run("b", "error", { reason: "worker exited" })];
    await poll();

    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Case b stopped with an error", expect.anything());
    expect(chime).toHaveBeenCalledTimes(1);
    expect(chime).toHaveBeenCalledWith("error", expect.any(Number));
  });

  it("keeps an approval up until the run moves on", async () => {
    runs = [run("a", "running")];
    const { client, poll } = mount();
    await firstPoll(client);

    runs = [run("a", "needs_approval", { approval: { id: "q1", step: 4, action: "Delete the account" } })];
    await poll();
    expect(toast.warning).toHaveBeenCalledWith("Case a needs your approval", expect.objectContaining({ id: "approval:a:needs_approval:q1", duration: 0, description: "Step 4: Delete the account" }));

    runs = [run("a", "running")];
    await poll();
    expect(toast.dismiss).toHaveBeenCalledWith("approval:a:needs_approval:q1");
  });

  it("shows no toast for a run on its own page", async () => {
    runs = [run("a", "running")];
    const { client, poll } = mount("/runs/a");
    await firstPoll(client);
    runs = [run("a", "passed")];
    await poll();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("announces an Explore session that ends, and none of its child runs", async () => {
    runs = [run("child", "running", { explore_session: "s" })];
    sessions = [session("s", "exploring")];
    const { client, poll } = mount();
    await firstPoll(client);
    await waitFor(() => expect(client.getQueryState(["explore"])?.dataUpdateCount).toBeGreaterThan(0));

    runs = [run("child", "passed", { explore_session: "s" })];
    sessions = [session("s", "finished", [{ severity: "P2" }])];
    await poll();

    expect(toast.success).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith("P1 hunt session finished: 1 finding", expect.objectContaining({ action: expect.objectContaining({ label: "Review findings" }) }));
  });

  it("offers desktop notifications once, on the first toast", async () => {
    FakeNotification.permission = "default";
    try {
      runs = [run("a", "running"), run("b", "running")];
      const { client, poll } = mount();
      await firstPoll(client);
      runs = [run("a", "passed"), run("b", "running")];
      await poll();
      const offered = vi.mocked(toast.success).mock.calls[0][1]?.secondary;
      expect(offered?.label).toBe("Desktop alerts");
      runs = [run("a", "passed"), run("b", "passed")];
      await poll();
      expect(vi.mocked(toast.success).mock.calls[1][1]?.secondary).toBeUndefined();

      await act(async () => void (await offered?.onClick()));
      expect(FakeNotification.requestPermission).toHaveBeenCalled();
      expect(JSON.parse(saved.get(NOTIFY_KEY) ?? "{}")).toMatchObject({ desktop: true, offered: true });
    } finally {
      FakeNotification.permission = "granted";
    }
  });
});
