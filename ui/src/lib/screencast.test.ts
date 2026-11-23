import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MAX_RECONNECTS, reconnectDelay, useScreencast } from "./screencast";

class FakeSocket {
  static instances: FakeSocket[] = [];
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onmessage: ((message: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  drop() {
    this.onerror?.();
    this.onclose?.();
  }
}

const canvas = { current: null };

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useScreencast", () => {
  it("backs off between reconnects", () => {
    expect([1, 2, 3, 4, 5, 6].map(reconnectDelay)).toEqual([1000, 2000, 4000, 8000, 8000, 8000]);
  });

  it("reconnects a dropped live view a bounded number of times", () => {
    const { result } = renderHook(() => useScreencast("r_1", true, canvas));
    for (let attempt = 1; attempt <= MAX_RECONNECTS; attempt += 1) {
      act(() => FakeSocket.instances.at(-1)!.drop());
      expect(result.current.state).toBe("connecting");
      act(() => vi.advanceTimersByTime(reconnectDelay(attempt)));
      expect(FakeSocket.instances).toHaveLength(attempt + 1);
    }
    act(() => FakeSocket.instances.at(-1)!.drop());
    expect(result.current.state).toBe("unavailable");
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeSocket.instances).toHaveLength(MAX_RECONNECTS + 1);
  });

  it("does not reconnect after the daemon says the stream ended", () => {
    const { result } = renderHook(() => useScreencast("r_1", true, canvas));
    const socket = FakeSocket.instances[0];
    act(() => {
      socket.onmessage?.({ data: JSON.stringify({ type: "end" }) } as MessageEvent);
      socket.drop();
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current.state).toBe("ended");
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("ignores errors and closes after it was unmounted", () => {
    const { result, unmount } = renderHook(() => useScreencast("r_1", true, canvas));
    const socket = FakeSocket.instances[0];
    unmount();
    expect(socket.closed).toBe(true);
    act(() => {
      socket.drop();
      vi.advanceTimersByTime(10_000);
    });
    expect(FakeSocket.instances).toHaveLength(1);
    expect(result.current.state).toBe("connecting");
  });
});
