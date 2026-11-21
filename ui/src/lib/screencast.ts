// The live view's frames: a WebSocket of binary messages, each a 16-byte header (JPEG length,
// CSS width, CSS height as big-endian u32/u16/u16, capture time as f64) followed by the JPEG.
// Frames are drawn on a canvas from an ImageBitmap (no object URLs, so the page's
// same-origin image policy is untouched); a frame that arrives while one is still being
// drawn replaces the waiting one, so a slow screen skips frames instead of lagging.

import { useEffect, useRef, useState, type RefObject } from "react";

export const FRAME_HEADER_BYTES = 16;

export interface Frame {
  width: number;
  height: number;
  captured: number;
  jpeg: Uint8Array;
}

export function parseFrame(buffer: ArrayBuffer): Frame | null {
  if (buffer.byteLength < FRAME_HEADER_BYTES) return null;
  const view = new DataView(buffer);
  const length = view.getUint32(0);
  if (length !== buffer.byteLength - FRAME_HEADER_BYTES || length === 0) return null;
  return { width: view.getUint16(4), height: view.getUint16(6), captured: view.getFloat64(8), jpeg: new Uint8Array(buffer, FRAME_HEADER_BYTES, length) };
}

/** Frames per second over a sliding window of `windowMs`. */
export class FpsMeter {
  private readonly windowMs: number;
  private times: number[] = [];

  constructor(windowMs = 2000) {
    this.windowMs = windowMs;
  }

  tick(now: number): void {
    this.times = [...this.times.filter((time) => now - time <= this.windowMs), now];
  }

  fps(now: number): number {
    const recent = this.times.filter((time) => now - time <= this.windowMs);
    return recent.length <= 1 ? 0 : (recent.length - 1) / (this.windowMs / 1000);
  }
}

export function screencastUrl(runId: string, where: { protocol: string; host: string } = window.location): string {
  const scheme = where.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${where.host}/api/runs/${encodeURIComponent(runId)}/screencast`;
}

export type ScreencastState = "off" | "connecting" | "waiting" | "live" | "ended" | "unavailable";

export interface Screencast {
  state: ScreencastState;
  fps: number;
  frames: number;
  size: { width: number; height: number } | null;
}

const STATS_EVERY_MS = 500;
/** Reconnects in a row (without a frame in between) before the live view gives up. */
export const MAX_RECONNECTS = 5;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 8000;

/** Wait before reconnect number `attempt` (1-based): 1 s, 2 s, 4 s, 8 s, 8 s. */
export function reconnectDelay(attempt: number): number {
  return Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.max(0, attempt - 1));
}

/**
 * Stream the run's live view into `canvas` while `active`; returns the connection state and
 * rates. A dropped connection is reopened (a bounded number of times, with backoff) while the
 * run is live; the daemon's `end` message or `active` turning false stops for good.
 */
export function useScreencast(runId: string, active: boolean, canvas: RefObject<HTMLCanvasElement | null>): Screencast {
  const [status, setStatus] = useState<Screencast>({ state: "off", fps: 0, frames: 0, size: null });
  const meter = useRef(new FpsMeter());

  useEffect(() => {
    if (!active || typeof WebSocket === "undefined") {
      setStatus((current) => ({ ...current, state: active ? "unavailable" : current.state === "live" ? "ended" : current.state }));
      return undefined;
    }
    let closed = false;
    let ended = false;
    let drawing = false;
    let waiting: Frame | null = null;
    let frames = 0;
    let lastStats = 0;
    let size: Screencast["size"] = null;
    let current: ScreencastState = "connecting";
    let socket: WebSocket | null = null;
    let attempts = 0;
    let retryTimer: number | undefined;
    setStatus({ state: "connecting", fps: 0, frames: 0, size: null });

    const report = (state: ScreencastState, force = false) => {
      if (closed) return;
      current = state;
      const now = performance.now();
      if (!force && now - lastStats < STATS_EVERY_MS) return;
      lastStats = now;
      setStatus({ state, fps: meter.current.fps(now), frames, size });
    };

    const draw = async (frame: Frame) => {
      drawing = true;
      try {
        const bitmap = await createImageBitmap(new Blob([frame.jpeg.slice()], { type: "image/jpeg" }));
        const target = canvas.current;
        if (target && !closed) {
          if (target.width !== bitmap.width || target.height !== bitmap.height) {
            target.width = bitmap.width;
            target.height = bitmap.height;
          }
          target.getContext("2d")?.drawImage(bitmap, 0, 0);
        }
        bitmap.close();
      } catch {
        // a broken frame: wait for the next one
      } finally {
        drawing = false;
      }
      const next = waiting;
      waiting = null;
      if (next && !closed) void draw(next);
    };

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(screencastUrl(runId));
      socket = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => report("waiting", true);
      ws.onmessage = (message: MessageEvent<ArrayBuffer | string>) => {
        if (closed) return;
        if (typeof message.data === "string") {
          try {
            const payload = JSON.parse(message.data) as { type?: string };
            if (payload.type === "end") {
              ended = true;
              report("ended", true);
            }
          } catch {
            // not a control message
          }
          return;
        }
        const frame = parseFrame(message.data);
        if (!frame) return;
        attempts = 0;
        frames += 1;
        size = { width: frame.width, height: frame.height };
        meter.current.tick(performance.now());
        if (drawing) waiting = frame;
        else void draw(frame);
        report("live", frames === 1);
      };
      // An error is always followed by close, which decides whether to try again.
      ws.onerror = () => undefined;
      ws.onclose = () => {
        if (closed || socket !== ws) return;
        if (!ended && attempts < MAX_RECONNECTS) {
          attempts += 1;
          report("connecting", true);
          retryTimer = window.setTimeout(connect, reconnectDelay(attempts));
          return;
        }
        report(ended || frames > 0 ? "ended" : "unavailable", true);
      };
    };
    connect();
    // Keep the rate and counters fresh when frames stop coming (a still page sends none).
    const ticker = window.setInterval(() => report(current, true), 1000);
    return () => {
      closed = true;
      window.clearInterval(ticker);
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [runId, active, canvas]);

  return status;
}
