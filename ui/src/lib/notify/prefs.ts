import { useCallback, useSyncExternalStore } from "react";

/** What the tester wants to hear about; remembered per browser, shared by every tab. */
export interface NotifyPrefs {
  /** Finished runs: every one, only failed and errored ones, or none. */
  runs: "all" | "failures" | "off";
  /** Runs that stop and wait for a person (a step or a plan to approve). */
  approvals: boolean;
  /** Explore sessions that end. */
  explore: boolean;
  /** A short chime with each notification while the page is in front. */
  sound: boolean;
  /** System notifications while the page is in the background (needs the browser's permission). */
  desktop: boolean;
  /** The one-time "turn on desktop notifications" offer was shown. */
  offered: boolean;
}

export const NOTIFY_KEY = "hercules.notify";
export const DEFAULT_PREFS: NotifyPrefs = { runs: "all", approvals: true, explore: true, sound: false, desktop: false, offered: false };

function parse(raw: string | null): NotifyPrefs {
  if (!raw) return DEFAULT_PREFS;
  try {
    const value = JSON.parse(raw) as Partial<NotifyPrefs>;
    return {
      runs: value.runs === "failures" || value.runs === "off" ? value.runs : "all",
      approvals: value.approvals !== false,
      explore: value.explore !== false,
      sound: value.sound === true,
      desktop: value.desktop === true,
      offered: value.offered === true,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

let current: NotifyPrefs | null = null;
const listeners = new Set<() => void>();

export function readPrefs(): NotifyPrefs {
  if (current) return current;
  try {
    current = parse(window.localStorage.getItem(NOTIFY_KEY));
  } catch {
    current = DEFAULT_PREFS;
  }
  return current;
}

export function writePrefs(patch: Partial<NotifyPrefs>): void {
  current = { ...readPrefs(), ...patch };
  try {
    window.localStorage.setItem(NOTIFY_KEY, JSON.stringify(current));
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab changed them.
  const onStorage = (event: StorageEvent) => {
    if (event.key !== NOTIFY_KEY) return;
    current = parse(event.newValue);
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Forget the cached prefs (tests swap localStorage between cases). */
export function resetPrefsCache(): void {
  current = null;
}

export function useNotifyPrefs(): [NotifyPrefs, (patch: Partial<NotifyPrefs>) => void] {
  const prefs = useSyncExternalStore(subscribe, readPrefs, readPrefs);
  return [prefs, useCallback((patch: Partial<NotifyPrefs>) => writePrefs(patch), [])];
}

/** Desktop notifications need a secure page (127.0.0.1 and localhost count) and a browser that has them. */
export function desktopSupported(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && "Notification" in window;
}

export function desktopPermission(): NotificationPermission | "unsupported" {
  return desktopSupported() ? window.Notification.permission : "unsupported";
}
