import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { orderedSteps } from "./timeline";
import { EVENT_KINDS, FINAL_GRACE_MS, MAX_STREAM_RETRIES, useRunTimeline } from "./useRunTimeline";

// Every kind the engine and the daemon stream to the UI (`events.emit("<kind>", …)` in
// testzeus_hercules/engine, plus the daemon's own "status"). Update it with the engine.
const ENGINE_KINDS = [
  "status",
  "run_started",
  "plan_ready",
  "step_started",
  "step_recovering",
  "step_deferred",
  "step_skipped",
  "step_finished",
  "decision",
  "action",
  "screenshot",
  "quality",
  "run_finished",
  "error",
  "paused",
  "resumed",
  "needs_approval",
  "approval_result",
  "input_result",
  "control_ignored",
  "run_resumed",
];

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(kind: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(kind, [...(this.listeners.get(kind) ?? []), listener]);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  send(kind: string, seq: number, data: Record<string, unknown>) {
    const event = { type: kind, lastEventId: String(seq), data: JSON.stringify(data) } as MessageEvent<string>;
    for (const listener of this.listeners.get(kind) ?? []) listener(event);
  }

  fail(state = FakeEventSource.CONNECTING) {
    this.readyState = state;
    this.onerror?.();
  }

  listens(kind: string) {
    return this.listeners.has(kind);
  }
}

const PLAN = [{ action: "Open /cart" }, { action: "Fill quantity" }];

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useRunTimeline", () => {
  it("listens to every event kind the engine emits", () => {
    expect([...EVENT_KINDS].sort()).toEqual([...ENGINE_KINDS].sort());
    renderHook(() => useRunTimeline("r_1", PLAN, () => undefined));
    const source = FakeEventSource.instances[0];
    for (const kind of ENGINE_KINDS) expect(source.listens(kind), kind).toBe(true);
  });

  it("applies a deferred step", () => {
    const { result } = renderHook(() => useRunTimeline("r_1", PLAN, () => undefined));
    const source = FakeEventSource.instances[0];
    act(() => {
      source.send("step_started", 1, { step: 2, action: "Fill quantity" });
      source.send("step_deferred", 2, { step: 2, after: 3, duration_ms: 10 });
    });
    expect(result.current.timeline.lastSeq).toBe(2);
    expect(orderedSteps(result.current.timeline)).toHaveLength(2);
  });

  it("closes on the final status and reports the end", () => {
    const onFinished = vi.fn();
    renderHook(() => useRunTimeline("r_1", PLAN, onFinished));
    const source = FakeEventSource.instances[0];
    act(() => source.send("status", 1, { status: "passed" }));
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    expect(onFinished).toHaveBeenCalledOnce();
  });

  it("surfaces a refused stream (401/404) instead of an empty timeline", () => {
    const { result } = renderHook(() => useRunTimeline("r_1", PLAN, () => undefined));
    act(() => FakeEventSource.instances[0].fail(FakeEventSource.CLOSED));
    expect(result.current.streamError).toMatch(/refused/i);
  });

  it("gives up after repeated failed reconnects", () => {
    const { result } = renderHook(() => useRunTimeline("r_1", PLAN, () => undefined));
    const source = FakeEventSource.instances[0];
    act(() => {
      for (let attempt = 1; attempt < MAX_STREAM_RETRIES; attempt += 1) source.fail();
    });
    expect(result.current.streamError).toBeNull();
    expect(source.readyState).toBe(FakeEventSource.CONNECTING);
    act(() => source.fail());
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    expect(result.current.streamError).toMatch(/lost/i);
  });

  it("does not reconnect to a finished run's stream", () => {
    const { result } = renderHook(() => useRunTimeline("r_1", PLAN, () => undefined, true));
    const source = FakeEventSource.instances[0];
    act(() => source.fail()); // the daemon ended the replay
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    expect(result.current.streamError).toBeNull();
  });

  it("closes the stream shortly after the run query says the run is over", () => {
    vi.useFakeTimers();
    const { rerender } = renderHook(({ final }) => useRunTimeline("r_1", PLAN, () => undefined, final), { initialProps: { final: false } });
    const source = FakeEventSource.instances[0];
    rerender({ final: true });
    expect(source.readyState).toBe(FakeEventSource.CONNECTING);
    act(() => vi.advanceTimersByTime(FINAL_GRACE_MS));
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });
});
