import { describe, expect, it } from "vitest";
import { run, session } from "../../test/notifyFixtures";
import type { RunView } from "../types";
import type { BatchSummary } from "../types";
import { BATCH_RECHECK_MS, exploreEvents, followUps, MAX_LOOKUPS, message, newRunTracker, newSessionTracker, runEvents, wanted, type NotifyEvent, type RunTracker } from "./events";
import { DEFAULT_PREFS } from "./prefs";

const kinds = (events: NotifyEvent[]) => events.map((event) => `${event.kind}:${event.key}`);
/** One poll of `runs`, with the folder run summaries fetched this time and the runs the daemon no longer has. */
const step = (tracker: RunTracker, runs: RunView[], summaries: BatchSummary[] = [], gone: string[] = []) =>
  runEvents(tracker, { runs, gone, summaries: Object.fromEntries(summaries.map((item) => [item.batch, item])) });
/** A folder run's daemon summary: `counts` by status; open counts the unfinished ones. */
const summary = (batch: string, counts: BatchSummary["counts"], extra: Partial<BatchSummary> = {}): BatchSummary => {
  const total = Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0);
  const open = (counts.queued ?? 0) + (counts.running ?? 0);
  return { batch, total, open, counts, finished_at: open ? null : "2026-11-20T10:03:00Z", ...extra };
};

describe("runEvents", () => {
  it("records what is there on the first poll and announces each run that ends afterwards, once", () => {
    const tracker = newRunTracker();
    expect(step(tracker, [run("old", "passed"), run("a", "running")])).toEqual([]);
    expect(kinds(step(tracker, [run("old", "passed"), run("a", "failed")]))).toEqual(["run:run:a"]);
    expect(step(tracker, [run("old", "passed"), run("a", "failed")])).toEqual([]);
  });

  it("announces a run that started and ended between two polls", () => {
    const tracker = newRunTracker();
    step(tracker, [run("a", "passed")]);
    expect(kinds(step(tracker, [run("quick", "passed"), run("a", "passed")]))).toEqual(["run:run:quick"]);
  });

  it("leaves an Explore session's child runs to the session", () => {
    const tracker = newRunTracker();
    step(tracker, [run("child", "running", { explore_session: "s1" })]);
    expect(step(tracker, [run("child", "passed", { explore_session: "s1" }), run("asks", "needs_approval", { explore_session: "s1" })])).toEqual([]);
  });

  it("announces a folder run once, when its summary says none of its cases is still open", () => {
    const tracker = newRunTracker();
    step(tracker, [run("a", "running", { batch: "b_1" }), run("b", "queued", { batch: "b_1" })]);
    expect(step(tracker, [run("a", "passed", { batch: "b_1" }), run("b", "running", { batch: "b_1" })])).toEqual([]);
    const ended = [run("a", "passed", { batch: "b_1" }), run("b", "failed", { batch: "b_1" })];
    const events = step(tracker, ended, [summary("b_1", { passed: 1, failed: 1 }, { folder: "Checkout" })]);
    expect(kinds(events)).toEqual(["batch:batch:b_1"]);
    expect(message(events[0] as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ type: "error", title: "Checkout: 1 passed · 1 failed", body: "2 runs finished", href: "/runs?batch=b_1" });
    expect(step(tracker, ended, [summary("b_1", { passed: 1, failed: 1 })])).toEqual([]);
  });

  it("announces a rerun on its own once its folder run was announced", () => {
    const tracker = newRunTracker();
    step(tracker, [run("a", "passed", { batch: "b_1" }), run("b", "passed", { batch: "b_1" })], [summary("b_1", { passed: 2 })]);
    expect(step(tracker, [run("again", "running", { batch: "b_1" }), run("a", "passed", { batch: "b_1" }), run("b", "passed", { batch: "b_1" })])).toEqual([]);
    expect(kinds(step(tracker, [run("again", "passed", { batch: "b_1" }), run("a", "passed", { batch: "b_1" }), run("b", "passed", { batch: "b_1" })]))).toEqual(["run:run:again"]);
  });

  it("on the first poll after a reload, announces what ended since the page last looked", () => {
    const since = Date.parse("2026-11-20T10:01:00Z");
    const tracker = newRunTracker(since);
    const events = step(
      tracker,
      [
        run("before", "passed", { finished_at: "2026-11-20T10:00:30Z" }),
        run("meanwhile", "failed", { finished_at: "2026-11-20T10:05:00+00:00" }),
        run("f1", "passed", { batch: "b_1", finished_at: "2026-11-20T10:00:10Z" }),
        run("f2", "passed", { batch: "b_1", finished_at: "2026-11-20T10:03:00Z" }),
        run("still", "running"),
      ],
      [summary("b_1", { passed: 2 })],
    );
    expect(kinds(events)).toEqual(["run:run:meanwhile", "batch:batch:b_1"]);
    expect(tracker.latest).toBe(Date.parse("2026-11-20T10:05:00Z"));
    expect(kinds(step(newRunTracker(), [run("meanwhile", "failed")]))).toEqual([]);
  });

  it("waits for a folder run's runs outside the newest runs, by its summary", () => {
    const tracker = newRunTracker();
    step(tracker, [run("a", "running", { batch: "b_1" })]);
    // The newest runs show only "a", ended; the folder run's other runs are outside the list.
    const visible = [run("a", "passed", { batch: "b_1" })];
    expect(followUps(tracker, visible, 0).batches).toEqual(["b_1"]);
    expect(step(tracker, visible)).toEqual([]);
    tracker.checked.set("b_1", 0); // the component fetched its summary
    expect(step(tracker, visible, [summary("b_1", { passed: 1, running: 249 })])).toEqual([]);
    expect(tracker.pending.has("b_1")).toBe(true);
    expect(followUps(tracker, visible, 1).batches).toEqual([]); // summarised a moment ago
    expect(followUps(tracker, visible, BATCH_RECHECK_MS + 1).batches).toEqual(["b_1"]);
    // 250 cases, one failed early on: the summary counts them all, not the newest 200.
    const [event] = step(tracker, visible, [summary("b_1", { passed: 249, failed: 1 })]);
    expect(message(event as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ type: "error", title: "Folder run: 249 passed · 1 failed", body: "250 runs finished" });
    expect(tracker.pending.size).toBe(0);
  });

  it("stops waiting for a folder run whose runs were deleted", () => {
    const tracker = newRunTracker();
    step(tracker, [run("a", "running", { batch: "b_1" })]);
    expect(step(tracker, [])).toEqual([]);
    expect(followUps(tracker, [], 0).batches).toEqual(["b_1"]);
    runEvents(tracker, { runs: [], goneBatches: ["b_1"] });
    expect(tracker.pending.size).toBe(0);
    expect(followUps(tracker, [], BATCH_RECHECK_MS * 10).batches).toEqual([]);
  });

  it("records the folder runs that had ended when the page opened, and never announces them later", () => {
    const tracker = newRunTracker();
    const old = Array.from({ length: 8 }, (_, index) => run(`r${index}`, "failed", { batch: `b_${index}` }));
    expect(followUps(tracker, old, 0).batches).toHaveLength(8); // the first poll summarises every one of them
    // Five summaries arrive; three could not be fetched: all eight are taken as already there.
    const fetched = old.slice(0, 5).map((item) => summary(item.batch as string, { failed: 1 }));
    expect(step(tracker, old, fetched)).toEqual([]);
    expect(followUps(tracker, old, BATCH_RECHECK_MS * 10).batches).toEqual([]);
    expect(step(tracker, old, old.map((item) => summary(item.batch as string, { failed: 1 })))).toEqual([]);
  });

  it("looks up a followed run that dropped out of the list, and keeps its approval meanwhile", () => {
    const tracker = newRunTracker();
    step(tracker, [run("a", "running")]);
    const asks = run("w", "needs_approval", { approval: { id: "q1", step: 2 } });
    step(tracker, [asks, run("a", "running")]);
    expect(followUps(tracker, [], 0).runIds.sort()).toEqual(["a", "w"]);
    // Neither is in this poll and neither was looked up: nothing is cleared or announced.
    expect(step(tracker, [])).toEqual([]);
    expect(kinds(step(tracker, [run("a", "passed")]))).toEqual(["run:run:a"]);
    expect(step(tracker, [], [], ["w"]).map((event) => event.kind)).toEqual(["approval-cleared"]);
    expect(followUps(tracker, [], 0).runIds).toEqual([]);
  });

  it("looks up a different set of followed runs each poll when more than fit in one", () => {
    const tracker = newRunTracker();
    const many = Array.from({ length: MAX_LOOKUPS + 5 }, (_, index) => `r${index}`);
    step(tracker, many.map((id) => run(id, "running")));
    const first = followUps(tracker, [], 0).runIds;
    const second = followUps(tracker, [], 0).runIds;
    expect(first).toHaveLength(MAX_LOOKUPS);
    expect(second.slice(0, 5)).toEqual(many.slice(MAX_LOOKUPS));
    expect(new Set([...first, ...second])).toEqual(new Set(many));
  });

  it("shows the runs still waiting again, without a sound, when approvals are switched back on", () => {
    const tracker = newRunTracker();
    step(tracker, []);
    const asks = run("w", "needs_approval", { approval: { id: "q1", step: 2 } });
    expect(step(tracker, [asks])).toMatchObject([{ kind: "approval", quiet: false }]);
    tracker.approvals = new Map();
    tracker.requiet = true;
    expect(step(tracker, [asks])).toMatchObject([{ kind: "approval", quiet: true }]);
    expect(step(tracker, [asks])).toEqual([]);
  });

  it("announces each approval a run waits on and clears it when the run moves on", () => {
    const tracker = newRunTracker();
    const waiting = (id: string, at: number) => run("a", "needs_approval", { approval: { id, step: at, action: "Delete the account", asked_at: "2026-11-20T10:00:00Z", timeout_s: 300 } });
    const startup = step(tracker, [waiting("q1", 4)]);
    expect(startup).toMatchObject([{ kind: "approval", quiet: true }]);
    expect(step(tracker, [waiting("q1", 4)])).toEqual([]);
    const next = step(tracker, [waiting("q2", 5)]);
    expect(next.map((event) => event.kind)).toEqual(["approval-cleared", "approval"]);
    expect(next[1]).toMatchObject({ quiet: false });
    expect(message(next[1] as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ type: "warning", sticky: true, title: "Case a needs your approval", href: "/runs/a" });
    expect((message(next[1] as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).body).toMatch(/^Step 5: Delete the account · answer by /);
    const done = step(tracker, [run("a", "passed")]);
    expect(done.map((event) => event.kind)).toEqual(["approval-cleared", "run"]);
  });

  it("asks for a plan review", () => {
    const tracker = newRunTracker();
    step(tracker, []);
    const [event] = step(tracker, [run("p", "needs_plan_approval", { started_by: "agent", agent: "claude-code" })]);
    expect(message(event as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ title: "Review the plan for Case p", body: "The run waits for your go-ahead · started by Claude Code" });
  });
});

describe("exploreEvents", () => {
  it("announces a session once when it ends, with its serious findings counted", () => {
    const tracker = newSessionTracker();
    expect(exploreEvents(tracker, [session("old", "finished"), session("s", "exploring")])).toEqual([]);
    const events = exploreEvents(tracker, [session("old", "finished"), session("s", "finished", [{ severity: "P1" }, { proposed_severity: "P1" }, { proposed_severity: "P3" }, { severity: "P2", proposed_severity: "P0" }])]);
    expect(kinds(events)).toEqual(["explore:explore:s"]);
    expect(message(events[0] as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ type: "warning", title: "P1 hunt session finished: 4 findings (2 P1)", body: "23m 00s · $1.84", href: "/explore/s" });
    expect(exploreEvents(tracker, [session("s", "finished")])).toEqual([]);
  });

  it("on the first poll after a reload, announces a session that ended since the page last looked", () => {
    const tracker = newSessionTracker(Date.parse("2026-11-20T10:20:00Z"));
    expect(kinds(exploreEvents(tracker, [session("s", "finished")]))).toEqual(["explore:explore:s"]);
    expect(exploreEvents(newSessionTracker(Date.parse("2026-11-20T10:30:00Z")), [session("s", "finished")])).toEqual([]);
  });

  it("says why a session failed and what a stopped one found", () => {
    const tracker = newSessionTracker();
    exploreEvents(tracker, []);
    const [failed, stopped] = exploreEvents(tracker, [session("f", "failed"), session("x", "stopped", [{ severity: "P3" }])]);
    expect(message(failed as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ type: "error", title: "P1 hunt session failed", body: "the browser crashed" });
    expect(message(stopped as Exclude<NotifyEvent, { kind: "approval-cleared" }>)).toMatchObject({ type: "info", title: "P1 hunt session stopped: 1 finding so far" });
  });
});

describe("messages and choices", () => {
  it("words each way a run ends", () => {
    const say = (value: RunView) => message({ kind: "run", key: "k", run: value });
    expect(say(run("a", "passed"))).toMatchObject({ type: "success", title: "Case a passed", body: "1m 42s", href: "/runs/a", sticky: false });
    expect(say(run("a", "failed", { reason: "The cart total was $0" }))).toMatchObject({ type: "error", title: "Case a failed", body: "The cart total was $0", sticky: true });
    expect(say(run("a", "failed"))).toMatchObject({ body: "3 of 7 steps done" });
    expect(say(run("a", "error", { reason: "worker exited" }))).toMatchObject({ type: "error", title: "Case a stopped with an error", body: "worker exited" });
    expect(say(run("a", "cancelled"))).toMatchObject({ type: "info", title: "Case a was cancelled", sticky: false });
  });

  it("follows the tester's choices", () => {
    const passed: NotifyEvent = { kind: "run", key: "k", run: run("a", "passed") };
    const failed: NotifyEvent = { kind: "run", key: "k", run: run("a", "failed") };
    expect(wanted(passed, DEFAULT_PREFS) && wanted(failed, DEFAULT_PREFS)).toBe(true);
    expect([wanted(passed, { ...DEFAULT_PREFS, runs: "failures" }), wanted(failed, { ...DEFAULT_PREFS, runs: "failures" })]).toEqual([false, true]);
    expect(wanted(failed, { ...DEFAULT_PREFS, runs: "off" })).toBe(false);
    expect(wanted({ kind: "approval", key: "k", run: run("a", "needs_approval"), quiet: false }, { ...DEFAULT_PREFS, approvals: false })).toBe(false);
    expect(wanted({ kind: "explore", key: "k", session: session("s", "finished") }, { ...DEFAULT_PREFS, explore: false })).toBe(false);
    expect(wanted({ kind: "approval-cleared", key: "k" }, { ...DEFAULT_PREFS, approvals: false })).toBe(true);
  });
});
