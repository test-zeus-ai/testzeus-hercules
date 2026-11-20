import { useQuery } from "@tanstack/react-query";
import { Toaster, toast } from "@yusufceng1z/ding-react";
import { useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { api, ApiError } from "../lib/api";
import { FINISHED, isSessionList, type SessionView } from "../lib/explore";
import { isRunList, isRunView } from "../lib/guards";
import { away, clearTitleBadge, deliver, type Delivery } from "../lib/notify/deliver";
import { exploreEvents, followUps, newRunTracker, newSessionTracker, runEvents, wanted, type NotifyEvent, type RunPoll, type RunTracker, type SessionTracker } from "../lib/notify/events";
import { useNotifyPrefs } from "../lib/notify/prefs";
import { FINAL_STATUSES } from "../lib/types";

/** Runs the notifier looks at: the newest ones, which is where a run that just ended is. */
export const NOTIFY_RUN_LIMIT = 100;
export const ACTIVE_POLL_MS = 1500;
export const IDLE_POLL_MS = 10_000;
export const EXPLORE_ACTIVE_POLL_MS = 5000;
export const EXPLORE_IDLE_POLL_MS = 15_000;
/** The daemon's cap on one runs listing: a folder run this large is checked for runs still open beyond it. */
const BATCH_LIMIT = 200;
const OPEN_STATUSES = ["queued", "running", "paused", "needs_approval", "needs_plan_approval"];

// What the notifier had accounted for, kept per tab: after a reload (or the browser waking a tab it put to sleep),
// what ended meanwhile is still announced.
const RUNS_MARK = "hercules.notify.runs-until";
const EXPLORE_MARK = "hercules.notify.explore-until";

function readMark(key: string): number | null {
  try {
    const value = Number(window.sessionStorage.getItem(key));
    return value > 0 ? value : null;
  } catch {
    return null;
  }
}

function writeMark(key: string, value: number): void {
  try {
    if (value > 0) window.sessionStorage.setItem(key, String(value));
  } catch {
    // no storage (a private window): a reload only skips what ended meanwhile
  }
}

const runsPath = (query: string) => `/api/runs?${query}`;

async function openBeyondTheList(batch: string): Promise<boolean> {
  const found = await Promise.all(OPEN_STATUSES.map((status) => api(runsPath(`batch=${encodeURIComponent(batch)}&status=${status}&limit=1`), { guard: isRunList })));
  return found.some((listing) => listing.runs.length > 0);
}

/** The newest runs, plus what they do not settle (followUps): runs that dropped out of the list, and whole folder runs. */
async function pollRuns(tracker: RunTracker): Promise<RunPoll> {
  const { runs } = await api(runsPath(`limit=${NOTIFY_RUN_LIMIT}`), { guard: isRunList });
  const asked = followUps(tracker, runs, Date.now());
  const merged = new Map(runs.map((run) => [run.run_id, run]));
  const gone: string[] = [];
  const complete: string[] = [];
  const lookUp = async (runId: string) => {
    try {
      merged.set(runId, await api(`/api/runs/${encodeURIComponent(runId)}`, { guard: isRunView }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) gone.push(runId);
    }
  };
  const fetchWhole = async (batch: string) => {
    tracker.checked.set(batch, Date.now());
    try {
      const { runs: members } = await api(runsPath(`batch=${encodeURIComponent(batch)}&limit=${BATCH_LIMIT}`), { guard: isRunList });
      for (const run of members) merged.set(run.run_id, run);
      if (members.length < BATCH_LIMIT || !(await openBeyondTheList(batch))) complete.push(batch);
    } catch {
      // checked again on a later poll
    }
  };
  await Promise.all([...asked.runIds.map(lookUp), ...asked.batches.map(fetchWhole)]);
  return { runs: [...merged.values()], gone, complete };
}

// The notifier plays its own chime (once per event, and not when a system notification already made a sound).
toast.configure({ sound: false });

/**
 * Toasts for runs that finish, runs that wait for an approval, and Explore sessions that end, wherever the
 * tester is in the UI; a system notification and a count in the tab title while the page is in the background.
 */
export function Notifications() {
  const [prefs] = useNotifyPrefs();
  const [path, navigate] = useLocation();
  const live = useRef<Delivery>({ prefs, path, navigate });
  live.current = { prefs, path, navigate };

  const runTracker = useRef<RunTracker | null>(null);
  runTracker.current ??= newRunTracker(readMark(RUNS_MARK));
  const sessionTracker = useRef<SessionTracker | null>(null);
  sessionTracker.current ??= newSessionTracker(readMark(EXPLORE_MARK));

  const watchRuns = prefs.runs !== "off" || prefs.approvals;
  const runs = useQuery({
    queryKey: ["runs", { notify: true }],
    queryFn: () => pollRuns(runTracker.current ?? newRunTracker()),
    enabled: watchRuns,
    refetchInterval: (query) => (query.state.data?.runs.some((run) => !FINAL_STATUSES.has(run.status)) ? ACTIVE_POLL_MS : IDLE_POLL_MS),
    refetchIntervalInBackground: true,
  });
  // Only this daemon's sessions: one an earlier daemon ran has ended and never changes.
  const sessions = useQuery({
    queryKey: ["explore", { notify: true }],
    queryFn: () => api<{ sessions: SessionView[] }>("/api/explore?live=true", { guard: isSessionList }),
    enabled: prefs.explore,
    refetchInterval: (query) => (query.state.data?.sessions.some((item) => !FINISHED.has(item.status)) ? EXPLORE_ACTIVE_POLL_MS : EXPLORE_IDLE_POLL_MS),
    refetchIntervalInBackground: true,
  });

  // Switched off and on again: start over, so what ended meanwhile is not announced late. Approval toasts close: with
  // nothing watching the runs, none of them would close when its run moves on.
  useEffect(() => {
    if (watchRuns) return;
    for (const key of runTracker.current?.approvals.values() ?? []) toast.dismiss(key);
    runTracker.current = newRunTracker();
  }, [watchRuns]);
  useEffect(() => {
    if (prefs.approvals) return;
    for (const key of runTracker.current?.approvals.values() ?? []) toast.dismiss(key);
  }, [prefs.approvals]);
  useEffect(() => {
    if (!prefs.explore) sessionTracker.current = newSessionTracker();
  }, [prefs.explore]);

  const announce = (events: NotifyEvent[]) => {
    for (const event of events) if (wanted(event, live.current.prefs)) deliver(event, live.current);
  };
  useEffect(() => {
    if (!runs.data || !watchRuns || !runTracker.current) return;
    announce(runEvents(runTracker.current, runs.data));
    writeMark(RUNS_MARK, runTracker.current.latest);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only new data is news
  }, [runs.data]);
  useEffect(() => {
    if (!sessions.data || !prefs.explore || !sessionTracker.current) return;
    announce(exploreEvents(sessionTracker.current, sessions.data.sessions));
    writeMark(EXPLORE_MARK, sessionTracker.current.latest);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only new data is news
  }, [sessions.data]);

  useEffect(() => {
    const back = () => {
      if (!away()) clearTitleBadge();
    };
    window.addEventListener("focus", back);
    document.addEventListener("visibilitychange", back);
    return () => {
      window.removeEventListener("focus", back);
      document.removeEventListener("visibilitychange", back);
    };
  }, []);

  return <Toaster position="top-center" labels={{ region: "Notifications" }} />;
}
