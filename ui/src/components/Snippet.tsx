import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

type CopyState = "idle" | "copied" | "failed";

const RESET_MS = 2000;

/** Selects the text of `node`, so it can be copied by hand where the clipboard API is unavailable. */
function selectText(node: HTMLElement | null) {
  const selection = typeof window.getSelection === "function" ? window.getSelection() : null;
  if (!node || !selection || typeof document.createRange !== "function") return;
  const range = document.createRange();
  range.selectNodeContents(node);
  selection.removeAllRanges();
  selection.addRange(range);
}

/**
 * A code snippet with a copy button. Without a usable clipboard (insecure origin, denied
 * permission, old browser) the snippet is selected instead and the button says so.
 */
export function Snippet({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<CopyState>("idle");
  const code = useRef<HTMLElement>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    let copied = false;
    try {
      const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
      if (clipboard && typeof clipboard.writeText === "function") {
        await clipboard.writeText(text);
        copied = true;
      }
    } catch {
      copied = false;
    }
    if (!copied) selectText(code.current);
    setState(copied ? "copied" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), RESET_MS);
  };

  return (
    <div className="snippet">
      <pre className="snippet-code">
        <code ref={code}>{text}</code>
      </pre>
      <button type="button" className={`button button-small snippet-copy${state === "copied" ? " snippet-copied" : ""}`} onClick={() => void copy()}>
        <Icon name={state === "copied" ? "check" : "copy"} size={13} />
        {state === "copied" ? "Copied" : state === "failed" ? "Selected: press Ctrl+C" : "Copy"}
        <span className="visually-hidden"> {label}</span>
      </button>
      <span className="visually-hidden" role="status">
        {state === "copied" ? `${label} copied to the clipboard` : state === "failed" ? "The clipboard is unavailable; the snippet is selected, copy it with Ctrl+C or Cmd+C" : ""}
      </span>
    </div>
  );
}
