import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Health } from "../types";

/**
 * What the UI notifies about: `ui.notifications` in the project's (or the user's) config.yaml, served by the daemon in
 * /api/health. Everything is on unless the config turns it off; there is no switch in the UI.
 */
export interface NotifyPrefs {
  /** Finished runs: every one, only failed and errored ones, or none. */
  runs: "all" | "failures" | "off";
  /** Runs that stop and wait for a person (a step or a plan to approve). */
  approvals: boolean;
  /** Explore sessions that end. */
  explore: boolean;
  /** A short chime with each notification. */
  sound: boolean;
  /** System notifications while the page is in the background (the browser asks for permission once). */
  desktop: boolean;
}

export const DEFAULT_PREFS: NotifyPrefs = { runs: "all", approvals: true, explore: true, sound: true, desktop: true };

/** The config's switches, each one that is missing or not understood left on. */
export function notifyPrefs(served: Health["notifications"]): NotifyPrefs {
  const value = served ?? {};
  return {
    runs: value.runs === "failures" || value.runs === "off" ? value.runs : "all",
    approvals: value.approvals !== false,
    explore: value.explore !== false,
    sound: value.sound !== false,
    desktop: value.desktop !== false,
  };
}

/** The config's switches; null until the daemon has answered, so nothing is announced against a switch not read yet. */
export function useNotifyPrefs(): NotifyPrefs | null {
  // The same query as the app shell's health check (it polls it): an edit to config.yaml applies within a poll.
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health") });
  return health.data ? notifyPrefs(health.data.notifications) : null;
}

const OFFERED_KEY = "hercules.notify.offered";

/** Whether this browser was already offered desktop notifications (the offer rides on one toast, once). */
export function desktopOffered(): boolean {
  try {
    return window.localStorage.getItem(OFFERED_KEY) === "1";
  } catch {
    return false;
  }
}

export function markDesktopOffered(): void {
  try {
    window.localStorage.setItem(OFFERED_KEY, "1");
  } catch {
    /* storage unavailable: the offer may come once more */
  }
}

/** Desktop notifications need a secure page (127.0.0.1 and localhost count) and a browser that has them. */
export function desktopSupported(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && "Notification" in window;
}

export function desktopPermission(): NotificationPermission | "unsupported" {
  return desktopSupported() ? window.Notification.permission : "unsupported";
}
