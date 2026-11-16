import { useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent } from "react";
import { evidenceUrl, post } from "../lib/api";
import { useScreencast } from "../lib/screencast";
import { Icon } from "./Icon";

const KEYS = ["Enter", "Tab", "Backspace", "Escape"] as const;
/** Keys the daemon presses on the page (``RunInput.key``); other keys with a single character are typed. */
const PRESSABLE = new Set<string>([...KEYS, "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);

interface Props {
  runId: string;
  /** The run is open (running, paused or waiting): stream the browser. */
  active: boolean;
  paused: boolean;
  /** Shown until the first frame arrives (or when the live view is not available). */
  fallbackRef: string | null;
  lastInput?: { ok: boolean; detail: string } | null;
}

/** The run's browser, live; while the run is paused the tester may take over with clicks and typing. */
export function LiveView({ runId, active, paused, fallbackRef, lastInput = null }: Props) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const cast = useScreencast(runId, active, canvas);
  const [takeOver, setTakeOver] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const driving = takeOver && paused;
  const hasFrames = cast.frames > 0;

  const send = async (body: Record<string, unknown>) => {
    setError(null);
    try {
      await post(`/api/runs/${encodeURIComponent(runId)}/input`, body);
    } catch (exc) {
      setError((exc as Error).message);
    }
  };

  const onClick = (event: MouseEvent<HTMLCanvasElement>) => {
    if (!driving) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const x = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const y = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    event.currentTarget.parentElement?.focus();  // so the keys pressed next go to the page
    void send({ kind: "click", x, y });
  };

  /** While driving, keys pressed on the focused live view go to the page: characters are typed, the keys the daemon
   * knows are pressed. Shortcuts with Ctrl, Cmd or Alt stay with the tester UI. */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!driving || event.ctrlKey || event.metaKey || event.altKey) return;
    if (PRESSABLE.has(event.key)) {
      event.preventDefault();
      void send({ kind: "key", key: event.key });
    } else if (event.key.length === 1) {
      event.preventDefault();
      void send({ kind: "type", text: event.key });
    }
  };

  const onType = (event: FormEvent) => {
    event.preventDefault();
    if (!text) return;
    void send({ kind: "type", text });
    setText("");
  };

  const label = cast.state === "live" ? "Live" : cast.state === "ended" ? "Ended" : cast.state === "connecting" || cast.state === "waiting" ? "Connecting…" : "Latest screenshot";
  return (
    <figure className={`live-view${driving ? " live-driving" : ""}`}>
      <figcaption className="shot-head">
        <span>
          {cast.state === "live" ? <span className="live-dot" aria-hidden="true" /> : null}
          {label}
          {hasFrames ? (
            <span className="muted mono small">
              {" "}
              · {cast.fps.toFixed(1)} fps · {cast.frames} frames{cast.size ? ` · ${cast.size.width}×${cast.size.height}` : ""}
            </span>
          ) : null}
        </span>
        {paused ? (
          <button type="button" className={`button button-small${takeOver ? " button-primary" : ""}`} aria-pressed={takeOver} onClick={() => setTakeOver((on) => !on)}>
            <Icon name="hand" size={13} /> {takeOver ? "Stop driving" : "Take over"}
          </button>
        ) : null}
      </figcaption>
      {driving ? (
        <p className="takeover-banner" role="note">
          <Icon name="hand" size={14} /> You are driving the browser. Click the page, then type: keys go to the page. The run stays paused until you resume it.
        </p>
      ) : null}
      <div className="live-frame" tabIndex={driving ? 0 : undefined} onKeyDown={onKeyDown} aria-label={driving ? "Live browser view: keys you press here go to the page" : undefined}>
        <canvas
          ref={canvas}
          className="live-canvas"
          hidden={!hasFrames}
          style={cast.size ? { maxWidth: `${cast.size.width}px` } : undefined /* a phone's page stays phone-sized, not stretched */}
          onClick={onClick}
          aria-label={driving ? "Live browser view: click to click on the page" : "Live browser view"}
          role="img"
        />
        {!hasFrames ? (
          fallbackRef ? (
            <img src={evidenceUrl(runId, fallbackRef)} alt="Latest screenshot of the run" />
          ) : (
            <div className="shot-empty">{active ? "Waiting for the browser…" : "No screenshot yet."}</div>
          )
        ) : null}
      </div>
      {driving ? (
        <div className="takeover-controls">
          <form onSubmit={onType} className="takeover-type">
            <label htmlFor="takeover-text" className="visually-hidden">
              Text to type into the focused field
            </label>
            <input id="takeover-text" value={text} onChange={(event) => setText(event.target.value)} placeholder="Type into the focused field" maxLength={1000} />
            <button type="submit" className="button button-small" disabled={!text}>
              Type
            </button>
          </form>
          <span className="takeover-keys">
            {KEYS.map((key) => (
              <button key={key} type="button" className="button button-small button-ghost" onClick={() => void send({ kind: "key", key })}>
                {key}
              </button>
            ))}
          </span>
          {error ? <span className="small fail-text">{error}</span> : lastInput ? <span className={`small ${lastInput.ok ? "muted" : "fail-text"}`}>{lastInput.detail}</span> : null}
        </div>
      ) : null}
    </figure>
  );
}
