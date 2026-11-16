// Debug controls of a running run: which actions apply in which state, and the keyboard shortcuts.

export type DebugAction = "pause" | "resume" | "step" | "skip" | "retry";

const ACTIONS: Record<string, DebugAction[]> = {
  running: ["pause", "step"],
  paused: ["resume", "step", "skip", "retry"],
  // While a question waits, the run can only be asked to stop after it (answer the question first).
  needs_approval: ["pause"],
};

/** The debug actions the daemon accepts for a run in `status`. */
export function availableActions(status: string | null | undefined): DebugAction[] {
  return ACTIONS[status ?? ""] ?? [];
}

// Anything that has its own use for Space or a letter key: typing, pressing a control, a tab, a
// disclosure, or a modal on top of the page. Shortcuts never fire there (WCAG 2.1.4).
const INTERACTIVE = [
  "input",
  "textarea",
  "select",
  "button",
  "a[href]",
  "summary",
  "dialog",
  "audio[controls]",
  "video[controls]",
  "[contenteditable]:not([contenteditable='false'])",
  "[role='button']",
  "[role='link']",
  "[role='tab']",
  "[role='checkbox']",
  "[role='radio']",
  "[role='switch']",
  "[role='menuitem']",
  "[role='option']",
  "[role='combobox']",
  "[role='textbox']",
  "[role='slider']",
  "[role='spinbutton']",
  "[role='dialog']",
  "[role='alertdialog']",
].join(", ");

const OPEN_MODAL = "dialog[open], [aria-modal='true']";

interface ElementLike {
  closest?: (selector: string) => Element | null;
  isContentEditable?: boolean;
  ownerDocument?: Document | null;
}

/** Where the keyboard focus is decides whether a page-level shortcut may fire. */
function shortcutTargetAllowed(target: EventTarget | null, scope: Element | null | undefined): boolean {
  const element = target as ElementLike | null;
  if (!element || typeof element.closest !== "function") return true; // window or document
  const doc = element.ownerDocument ?? (typeof document !== "undefined" ? document : null);
  if (doc?.querySelector(OPEN_MODAL)) return false;
  if (element.isContentEditable || element.closest(INTERACTIVE)) return false;
  if (doc && (element === doc.body || element === doc.documentElement)) return true;
  return scope ? scope.contains(element as unknown as Node) : false;
}

/**
 * Space pauses or resumes, `n` steps once. Only when the focus is on the page itself (the body)
 * or on something non-interactive inside `scope` (the run page's main region); never on a
 * control, a text field or inside an open modal, and never with a modifier key held.
 */
export function shortcutAction(event: KeyboardEvent, status: string | null | undefined, scope?: Element | null): DebugAction | null {
  if (event.defaultPrevented || event.repeat) return null;
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return null;
  if (!shortcutTargetAllowed(event.target, scope)) return null;
  const allowed = availableActions(status);
  let wanted: DebugAction | null = null;
  if (event.key === " " || event.key === "Spacebar") wanted = status === "paused" ? "resume" : "pause";
  else if (event.key === "n" || event.key === "N") wanted = "step";
  return wanted && allowed.includes(wanted) ? wanted : null;
}
