import { useEffect, useReducer, useRef, useState } from "react";
import { applyEvent, initialTimeline, type PlannedStep, type TimelineState } from "./timeline";
import { FINAL_STATUSES } from "./types";

// Event kinds the daemon streams (worker events plus the daemon's own "status"). EventSource only
// delivers named events that have a listener, so a kind missing here is silently dropped; keep it
// in step with the `events.emit(...)` calls of testzeus_hercules/engine and the daemon.
export const EVENT_KINDS = [
  "status",
  "run_started",
  "plan_ready",
  "step_started",
  "step_recovering",
  "step_deferred",
  "decision",
  "action",
  "screenshot",
  "quality",
  "step_finished",
  "run_finished",
  "error",
  "paused",
  "resumed",
  "needs_approval",
  "approval_result",
  "input_result",
  "control_ignored",
  "run_resumed",
  "step_skipped",
] as const;

/** Failed reconnects in a row (without an event in between) before the stream is given up. */
export const MAX_STREAM_RETRIES = 5;
/** How long a stream may still deliver events after the run is known to be over. */
export const FINAL_GRACE_MS = 2000;

type Action = { type: "event"; seq: number; kind: string; data: Record<string, unknown> } | { type: "reset"; plan: readonly PlannedStep[] };

function reducer(state: TimelineState, action: Action): TimelineState {
  if (action.type === "reset") return initialTimeline(action.plan);
  return applyEvent(state, { seq: action.seq, kind: action.kind, data: action.data });
}

export interface RunTimeline {
  timeline: TimelineState;
  /** Why the live event stream stopped before the run ended, or null. */
  streamError: string | null;
}

/**
 * Follow a run's server-sent events. The browser resumes after the last event id on its own
 * when the connection drops. The stream is closed once the run reaches a final status, either
 * from its own `status` event or because `final` (the run query) says so; otherwise
 * EventSource would reconnect to a finished stream forever. A stream the daemon refuses
 * (401, 404) or that keeps failing is closed and reported in `streamError`. Pass `plan`
 * undefined until the case's steps are known.
 */
export function useRunTimeline(runId: string, plan: readonly PlannedStep[] | undefined, onFinished: () => void, final = false): RunTimeline {
  const [state, dispatch] = useReducer(reducer, plan ?? [], initialTimeline);
  const [streamError, setStreamError] = useState<string | null>(null);
  const planKey = plan ? JSON.stringify(plan) : null;
  const finalRef = useRef(final);
  finalRef.current = final;
  const sourceRef = useRef<EventSource | null>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    if (planKey === null) return undefined; // wait for the case's steps so the stream is read once
    dispatch({ type: "reset", plan: plan ?? [] });
    setStreamError(null);
    if (typeof EventSource === "undefined") return undefined;
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`, { withCredentials: true });
    sourceRef.current = source;
    let failures = 0;
    const stop = () => {
      source.close();
      if (sourceRef.current === source) sourceRef.current = null;
    };
    const listener = (event: MessageEvent<string>) => {
      failures = 0;
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
      dispatch({ type: "event", seq: Number(event.lastEventId), kind: event.type, data });
      if (event.type === "status" && typeof data.status === "string" && FINAL_STATUSES.has(data.status)) {
        stop();
        finishedRef.current();
      }
    };
    for (const kind of EVENT_KINDS) source.addEventListener(kind, listener as EventListener);
    source.onopen = () => setStreamError(null);
    source.onerror = () => {
      if (finalRef.current) {
        stop(); // the daemon ended a finished run's stream: nothing more will come
        return;
      }
      if (source.readyState === EventSource.CLOSED) {
        stop();
        setStreamError("The live event stream was refused (the run may be gone, or the session ended). Reload to try again.");
        return;
      }
      failures += 1;
      if (failures >= MAX_STREAM_RETRIES) {
        stop();
        setStreamError("Lost the live event stream. The page still refreshes the run's status; reload to follow it live again.");
      }
    };
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only for another run or plan
  }, [runId, planKey]);

  // The run query saw the run end: let the stream deliver its last events, then close it.
  useEffect(() => {
    if (!final) return undefined;
    const timer = window.setTimeout(() => {
      sourceRef.current?.close();
      sourceRef.current = null;
    }, FINAL_GRACE_MS);
    return () => window.clearTimeout(timer);
  }, [final]);

  return { timeline: state, streamError };
}
