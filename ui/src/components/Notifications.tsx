import { useQuery } from "@tanstack/react-query";
import { Toaster, toast } from "@yusufceng1z/ding-react";
import { useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { api } from "../lib/api";
import { FINISHED, isSessionList, type SessionView } from "../lib/explore";
import { isRunList } from "../lib/guards";
import { away, clearTitleBadge, deliver, type Delivery } from "../lib/notify/deliver";
import { exploreEvents, newRunTracker, newSessionTracker, runEvents, wanted, type NotifyEvent, type RunTracker, type SessionTracker } from "../lib/notify/events";
import { useNotifyPrefs } from "../lib/notify/prefs";
import { FINAL_STATUSES, type RunView } from "../lib/types";

/** Runs the notifier looks at: the newest ones, which is where a run that just ended is. */
export const NOTIFY_RUN_LIMIT = 100;
export const ACTIVE_POLL_MS = 1500;
export const IDLE_POLL_MS = 10_000;
export const EXPLORE_ACTIVE_POLL_MS = 3000;
export const EXPLORE_IDLE_POLL_MS = 15_000;

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

  const watchRuns = prefs.runs !== "off" || prefs.approvals;
  const runs = useQuery({
    queryKey: ["runs", { notify: true }],
    queryFn: () => api<{ runs: RunView[] }>(`/api/runs?limit=${NOTIFY_RUN_LIMIT}`, { guard: isRunList }),
    enabled: watchRuns,
    refetchInterval: (query) => (query.state.data?.runs.some((run) => !FINAL_STATUSES.has(run.status)) ? ACTIVE_POLL_MS : IDLE_POLL_MS),
    refetchIntervalInBackground: true,
  });
  const sessions = useQuery({
    queryKey: ["explore"],
    queryFn: () => api<{ sessions: SessionView[] }>("/api/explore", { guard: isSessionList }),
    enabled: prefs.explore,
    refetchInterval: (query) => (query.state.data?.sessions.some((item) => !FINISHED.has(item.status)) ? EXPLORE_ACTIVE_POLL_MS : EXPLORE_IDLE_POLL_MS),
    refetchIntervalInBackground: true,
  });

  // Switched off and on again: start over, so what ended meanwhile is not announced late.
  const runTracker = useRef<RunTracker | null>(null);
  runTracker.current ??= newRunTracker(readMark(RUNS_MARK));
  const sessionTracker = useRef<SessionTracker | null>(null);
  sessionTracker.current ??= newSessionTracker(readMark(EXPLORE_MARK));
  useEffect(() => {
    if (!watchRuns) runTracker.current = newRunTracker();
  }, [watchRuns]);
  useEffect(() => {
    if (!prefs.explore) sessionTracker.current = newSessionTracker();
  }, [prefs.explore]);

  const announce = (events: NotifyEvent[]) => {
    for (const event of events) if (wanted(event, live.current.prefs)) deliver(event, live.current);
  };
  useEffect(() => {
    if (!runs.data || !watchRuns || !runTracker.current) return;
    announce(runEvents(runTracker.current, runs.data.runs));
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
