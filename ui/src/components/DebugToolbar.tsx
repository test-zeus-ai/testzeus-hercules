import { useEffect, useRef } from "react";
import { availableActions, shortcutAction, type DebugAction } from "../lib/control";
import type { PausePoint } from "../lib/timeline";
import { Icon, type IconName } from "./Icon";

const BUTTONS: Array<{ action: DebugAction; label: string; icon: IconName; hint: string; key?: string }> = [
  { action: "pause", label: "Pause", icon: "pause", hint: "Stop before the next step or action", key: "Space" },
  { action: "resume", label: "Resume", icon: "play", hint: "Continue the run", key: "Space" },
  { action: "step", label: "Step", icon: "step", hint: "Run one step, then pause", key: "N" },
  { action: "skip", label: "Skip", icon: "skip", hint: "Skip the next step (marks it skipped)" },
  { action: "retry", label: "Retry", icon: "rerun", hint: "Run the previous step again, then pause" },
];

interface Props {
  status: string;
  pausedAt?: PausePoint | null;
  busy?: boolean;
  onAction: (action: DebugAction) => void;
}

function pausedText(point: PausePoint | null | undefined): string {
  if (!point?.step) return "Paused";
  return point.where === "action" ? `Paused in step ${point.step}, before its next action` : `Paused before step ${point.step}`;
}

/**
 * Pause, resume, step, skip and retry a running run. Space and N work while the focus is on the
 * page or on non-interactive content of the main region, never on a control or in a dialog.
 */
export function DebugToolbar({ status, pausedAt = null, busy = false, onAction }: Props) {
  const allowed = availableActions(status);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const scope = ref.current?.closest("main") ?? ref.current?.parentElement ?? null;
      const action = shortcutAction(event, status, scope);
      if (!action || busy) return;
      event.preventDefault();
      onAction(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [status, busy, onAction]);

  const paused = status === "paused";
  return (
    <div ref={ref} className={`debug-toolbar${paused ? " debug-paused" : ""}`} role="toolbar" aria-label="Debug controls">
      <span className="debug-state" role="status">
        {paused ? (
          <>
            <Icon name="pause" size={13} /> {pausedText(pausedAt)}
          </>
        ) : status === "needs_approval" ? (
          "Waiting for your decision"
        ) : (
          <>
            <span className="live-dot" aria-hidden="true" /> Running
          </>
        )}
      </span>
      <span className="debug-buttons">
        {BUTTONS.filter((button) => (button.action === "pause" ? !paused : button.action === "resume" ? paused : true)).map((button) => (
          <button
            key={button.action}
            type="button"
            className={`button button-small${button.action === "resume" ? " button-primary" : ""}`}
            disabled={busy || !allowed.includes(button.action)}
            title={button.key ? `${button.hint} (${button.key})` : button.hint}
            aria-keyshortcuts={button.key === "Space" ? "Space" : button.key}
            onClick={() => onAction(button.action)}
          >
            <Icon name={button.icon} size={13} /> {button.label}
            {button.key ? <kbd className="kbd">{button.key}</kbd> : null}
          </button>
        ))}
      </span>
    </div>
  );
}
