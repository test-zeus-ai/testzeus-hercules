// Shows a notifier event: a toast on the page, a chime, a system notification while the page is in the
// background, and a count in the tab title until the tester comes back.

import { chime, toast, type ToastAction } from "@yusufceng1z/ding";
import { dissolvePage } from "../pageDissolve";
import { message, type NotifyEvent } from "./events";
import { desktopPermission, desktopSupported, writePrefs, type NotifyPrefs } from "./prefs";

/** How long good news stays while the tester is looking; failures, approvals and serious findings, and anything
 * that arrives while they are away, stay until dismissed. */
export const TOAST_MS = 10_000;
const VOLUME = 0.6;

export interface Delivery {
  prefs: NotifyPrefs;
  /** The page the tester is on (wouter's location). */
  path: string;
  navigate: (href: string) => void;
}

/** The tester is not looking at this page: another tab, another app, or a minimised window. */
export function away(): boolean {
  return document.visibilityState === "hidden" || !document.hasFocus();
}

let unseen = 0;
let waiting = false;
let baseTitle: string | null = null;

function renderTitle(): void {
  baseTitle ??= document.title;
  document.title = unseen === 0 ? baseTitle : waiting ? `⚠ Approval needed · ${baseTitle}` : `(${unseen}) ${baseTitle}`;
}

/** Back on the page: the tab title loses its count. */
export function clearTitleBadge(): void {
  if (unseen === 0) return;
  unseen = 0;
  waiting = false;
  renderTitle();
}

/** Asks the browser for permission (call it from a click); remembers the answer. */
export async function enableDesktop(): Promise<NotificationPermission | "unsupported"> {
  if (!desktopSupported()) return "unsupported";
  const permission = await window.Notification.requestPermission();
  writePrefs({ desktop: permission === "granted" });
  return permission;
}

/** The first toast offers desktop notifications once, while the browser has not been asked yet. */
function offer(prefs: NotifyPrefs): ToastAction | undefined {
  if (prefs.offered || prefs.desktop || desktopPermission() !== "default") return undefined;
  writePrefs({ offered: true });
  return { label: "Desktop alerts", onClick: () => void enableDesktop() };
}

function notifyDesktop(title: string, body: string, tag: string, requireInteraction: boolean, open: () => void): boolean {
  try {
    // The tag makes a second tab's copy of the same event replace this one instead of stacking.
    const shown = new window.Notification(title, { body, tag, icon: "/favicon.svg", requireInteraction });
    shown.onclick = () => {
      window.focus();
      open();
      shown.close();
    };
    return true;
  } catch {
    return false; // some browsers allow notifications only through a service worker
  }
}

/** A toast's button changes page with the sidebar's mosaic dissolve, spreading from the toasts at the top. */
function openFromToast(href: string, navigate: (href: string) => void): void {
  if (`${window.location.pathname}${window.location.search}` !== href) dissolvePage(document.getElementById("main"), { x: window.innerWidth / 2, y: 0 });
  navigate(href);
}

export function deliver(event: NotifyEvent, { prefs, path, navigate }: Delivery): void {
  if (event.kind === "approval-cleared") {
    toast.dismiss(event.key);
    return;
  }
  const note = message(event);
  const open = () => navigate(note.href);
  const quiet = event.kind === "approval" && event.quiet;
  const elsewhere = away();
  // Away from the page (another app, another tab), a toast that closed on its own would be gone before they are back.
  const stays = note.sticky || elsewhere;
  // On the run's or session's own page the screen already shows it (the approval card, the result).
  const onItsPage = event.kind !== "batch" && path === note.href;
  if (!onItsPage) {
    toast[note.type](note.title, {
      id: event.key,
      description: note.body || undefined,
      duration: stays ? 0 : TOAST_MS,
      action: { label: note.action, onClick: () => openFromToast(note.href, navigate) },
      // A second button takes the close button off a toast that stays, so the offer rides on one that closes itself.
      secondary: stays ? undefined : offer(prefs),
    });
  }
  if (quiet) return;
  // Only a question keeps the system notification on screen; the rest go to the system's notification centre.
  const desktop = elsewhere && prefs.desktop && desktopPermission() === "granted" && notifyDesktop(note.title, note.body, event.key, event.kind === "approval", open);
  // A system notification makes its own sound.
  if (prefs.sound && !desktop) chime(note.type, VOLUME);
  if (elsewhere) {
    unseen += 1;
    waiting ||= event.kind === "approval";
    renderTitle();
  }
}

/** Settings' "Send a test notification": a toast, the chime and (when allowed) a system notification, now. */
export function deliverSample(prefs: NotifyPrefs): void {
  const title = "Sample run passed";
  const body = "Finished runs, approvals and Explore sessions look like this";
  toast.success(title, { id: `sample:${Date.now()}`, description: body, duration: TOAST_MS });
  const desktop = prefs.desktop && desktopPermission() === "granted" && notifyDesktop(title, body, "hercules-sample", false, () => undefined);
  if (prefs.sound && !desktop) chime("success", VOLUME);
}
