import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { evidenceUrl } from "../lib/api";
import {
  bodyView,
  callFailed,
  curlCommand,
  exchangeText,
  formatBytes,
  jsonTokens,
  matchesFilter,
  splitMasked,
  splitUrl,
  statusClass,
  statusText,
  type HttpCall,
  type StatusFilter,
} from "../lib/apiCalls";
import { formatMs } from "../lib/format";
import { Icon } from "./Icon";

const HEADERS_SHOWN = 6;
const FILTERS: { id: StatusFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "2", label: "2xx" },
  { id: "4", label: "4xx" },
  { id: "5", label: "5xx" },
];

type DetailTab = "request" | "response" | "checks";

function Masked() {
  return (
    <span className="api-masked" title="Masked by the run; the value is not kept">
      masked
    </span>
  );
}

/** Text with each masked value shown as a chip. */
function WithMasks({ text }: { text: string }) {
  return (
    <>
      {splitMasked(text).map((part, index) => (part === null ? <Masked key={index} /> : <span key={index}>{part}</span>))}
    </>
  );
}

function Method({ method }: { method: string }) {
  return <span className={`api-method api-method-${method.toLowerCase()}`}>{method}</span>;
}

function Status({ call }: { call: HttpCall }) {
  return <span className={`api-status api-status-${statusClass(call)}`}>{call.response?.status ?? "–"}</span>;
}

function CopyButton({ text, label, done }: { text: string; label: string; done: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    let copied = false;
    try {
      if (typeof navigator !== "undefined" && typeof navigator.clipboard?.writeText === "function") {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch {
      copied = false;
    }
    setState(copied ? "copied" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 2000);
  };
  return (
    <button type="button" className="button button-small" onClick={() => void copy()} title={state === "failed" ? "The clipboard is unavailable here" : undefined}>
      <Icon name={state === "copied" ? "check" : "copy"} size={13} /> {state === "copied" ? done : state === "failed" ? "Copy blocked" : label}
    </button>
  );
}

function Headers({ pairs }: { pairs: [string, string][] }) {
  const [all, setAll] = useState(false);
  if (!pairs.length) return <p className="muted small">No headers.</p>;
  const shown = all ? pairs : pairs.slice(0, HEADERS_SHOWN);
  return (
    <>
      <table className="api-kv">
        <tbody>
          {shown.map(([name, value], index) => (
            <tr key={`${name}-${index}`}>
              <th scope="row">{name}</th>
              <td>
                <WithMasks text={value} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {pairs.length > HEADERS_SHOWN ? (
        <button type="button" className="link-button small" onClick={() => setAll((value) => !value)}>
          {all ? "Show fewer headers" : `Show ${pairs.length - HEADERS_SHOWN} more header${pairs.length - HEADERS_SHOWN === 1 ? "" : "s"}`}
        </button>
      ) : null}
    </>
  );
}

function Body({ body, runId, evidence }: { body: string | null; runId: string; evidence: string }) {
  const view = bodyView(body);
  if (!view) return <p className="muted small">No body.</p>;
  return (
    <>
      <pre className="api-body">
        <code>
          {view.json
            ? jsonTokens(view.text).map((token, index) =>
                token.kind === "masked" ? (
                  <Masked key={index} />
                ) : token.kind === "plain" ? (
                  token.text
                ) : (
                  <span key={index} className={`api-tok-${token.kind}`}>
                    {token.text}
                  </span>
                ),
              )
            : <WithMasks text={view.text} />}
        </code>
      </pre>
      {view.truncated !== null ? (
        <p className="muted small">
          {view.truncated.toLocaleString()} more characters were not kept.{" "}
          <a href={evidenceUrl(runId, evidence)} target="_blank" rel="noopener">
            Open http.json
          </a>
        </p>
      ) : null}
    </>
  );
}

function Section({ title, meta, children }: { title: string; meta?: string; children: ReactNode }) {
  return (
    <section className="api-section">
      <div className="api-section-head">
        <h4>{title}</h4>
        {meta ? <span className="muted small">{meta}</span> : null}
      </div>
      {children}
    </section>
  );
}

function StepLink({ step, onStep }: { step: number; onStep: (step: number) => void }) {
  return (
    <button type="button" className="link-button api-step-link" onClick={() => onStep(step)}>
      Step {step}
    </button>
  );
}

function CallDetail({ call, runId, onStep }: { call: HttpCall; runId: string; onStep: (step: number) => void }) {
  const [tab, setTab] = useState<DetailTab>(call.response ? "response" : "request");
  const failedChecks = call.checks.filter((check) => check.verdict === "failed").length;
  const results = call.checks.length + call.saves.length;
  const tabs: { id: DetailTab; label: string; count?: number }[] = [
    { id: "request", label: "Request" },
    { id: "response", label: "Response" },
    { id: "checks", label: "Checks", count: results },
  ];
  const contentType = (pairs: [string, string][]) => pairs.find(([name]) => name.toLowerCase() === "content-type")?.[1].split(";")[0];
  return (
    <div className="api-detail">
      <div className="api-detail-head">
        <div className="api-detail-url">
          <Method method={call.method} />
          <span className="mono">
            <WithMasks text={call.url} />
          </span>
        </div>
        <div className="api-detail-meta">
          <span className={`api-status api-status-${statusClass(call)}`}>{statusText(call)}</span>
          <span>{formatMs(call.duration_ms)}</span>
          {call.response ? <span>{formatBytes(call.response.bytes)}</span> : null}
          <StepLink step={call.step} onStep={onStep} />
          {failedChecks ? (
            <span className="api-verdict api-verdict-failed">
              {failedChecks} check{failedChecks === 1 ? "" : "s"} failed
            </span>
          ) : null}
          <span className="api-actions">
            <CopyButton text={curlCommand(call)} label="Copy as cURL" done="Copied" />
            <CopyButton text={exchangeText(call)} label="Copy all" done="Copied" />
          </span>
        </div>
      </div>
      <div className="api-tabs" role="tablist" aria-label="Call details">
        {tabs.map((item) => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={`api-tab${tab === item.id ? " api-tab-on" : ""}`} onClick={() => setTab(item.id)}>
            {item.label}
            {item.count !== undefined ? <span className="segment-count"> {item.count}</span> : null}
          </button>
        ))}
      </div>
      <div className="api-tab-body" role="tabpanel">
        {tab === "request" ? (
          <>
            <Section title="Headers" meta={String(call.request.headers.length)}>
              <Headers pairs={call.request.headers} />
            </Section>
            <Section title="Body" meta={contentType(call.request.headers)}>
              <Body body={call.request.body} runId={runId} evidence={call.evidence} />
            </Section>
          </>
        ) : tab === "response" ? (
          call.response ? (
            <>
              <Section title="Headers" meta={String(call.response.headers.length)}>
                <Headers pairs={call.response.headers} />
              </Section>
              <Section title="Body" meta={[contentType(call.response.headers), call.response.bytes !== null ? formatBytes(call.response.bytes) : null].filter(Boolean).join(" · ")}>
                <Body body={call.response.body} runId={runId} evidence={call.evidence} />
              </Section>
            </>
          ) : (
            <p className="callout callout-fail">No response came back: {call.error ?? "the request failed"}.</p>
          )
        ) : results ? (
          <ul className="api-checks">
            {call.checks.map((check, index) => (
              <li key={`c-${index}`} className="api-check">
                <span className={`api-verdict api-verdict-${check.verdict}`}>{check.verdict}</span>
                <div className="api-check-text">
                  <p>{check.text || check.check}</p>
                  <p className="mono muted">
                    {check.check}: expected <WithMasks text={check.expected} />, got <WithMasks text={check.actual} />
                  </p>
                </div>
                <StepLink step={check.step} onStep={onStep} />
              </li>
            ))}
            {call.saves.map((save, index) => (
              <li key={`s-${index}`} className="api-check">
                <span className="api-verdict api-verdict-saved">saved</span>
                <div className="api-check-text">
                  <p>
                    <span className="mono">{`{{${save.name}}}`}</span> = <WithMasks text={save.value} />
                  </p>
                </div>
                <StepLink step={save.step} onStep={onStep} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">No check or save used this response.</p>
        )}
      </div>
    </div>
  );
}

/** Every API call the run sent: a filterable list beside the selected call's request, response and checks. */
export function ApiCalls({
  runId,
  calls,
  more,
  selected,
  onSelect,
  onStep,
}: {
  runId: string;
  calls: HttpCall[];
  more: boolean;
  selected: number | null;
  onSelect: (step: number) => void;
  onStep: (step: number) => void;
}) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const list = useRef<HTMLUListElement>(null);
  const visible = calls.filter((call) => matchesFilter(call, text, status));
  const current = calls.find((call) => call.step === selected) ?? calls[0] ?? null;
  const slowest = Math.max(1, ...calls.map((call) => call.duration_ms ?? 0));
  const total = calls.reduce((sum, call) => sum + (call.duration_ms ?? 0), 0);

  const onKey = (event: KeyboardEvent<HTMLUListElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const at = visible.findIndex((call) => call.step === current?.step);
    const next = visible[Math.min(visible.length - 1, Math.max(0, at + (event.key === "ArrowDown" ? 1 : -1)))];
    if (!next) return;
    onSelect(next.step);
    (list.current?.querySelector(`[data-call="${next.step}"]`) as HTMLElement | null)?.focus();
  };

  return (
    <div className="panel api-calls">
      <div className="api-list">
        <div className="api-toolbar">
          <label className="visually-hidden" htmlFor="api-filter">
            Filter API calls
          </label>
          <input id="api-filter" className="api-filter" type="search" placeholder="Filter by URL, method or status" value={text} onChange={(event) => setText(event.target.value)} autoComplete="off" />
          <div className="segmented segmented-small" role="group" aria-label="Status">
            {FILTERS.map((item) => (
              <button key={item.id} type="button" className={`segment${status === item.id ? " segment-on" : ""}`} aria-pressed={status === item.id} onClick={() => setStatus(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
        </div>
        {visible.length ? (
          <ul className="api-rows" role="listbox" aria-label="API calls" ref={list} onKeyDown={onKey}>
            {visible.map((call) => {
              const { host, path } = splitUrl(call.url);
              const on = call.step === current?.step;
              return (
                <li
                  key={call.step}
                  role="option"
                  aria-selected={on}
                  tabIndex={on ? 0 : -1}
                  data-call={call.step}
                  className={`api-row${on ? " api-row-on" : ""}${callFailed(call) ? " api-row-failed" : ""}`}
                  onClick={() => onSelect(call.step)}
                >
                  <Method method={call.method} />
                  <span className="api-row-path">
                    <span className="mono" title={call.url}>
                      <WithMasks text={path} />
                    </span>
                    <span className="muted">
                      {host} · Step {call.step}
                    </span>
                  </span>
                  <Status call={call} />
                  <span className="api-row-meta">
                    <span className="api-bar" aria-hidden="true">
                      <i style={{ width: `${Math.max(3, ((call.duration_ms ?? 0) / slowest) * 100)}%` }} />
                    </span>
                    <span>
                      {formatMs(call.duration_ms)}
                      {call.response ? ` · ${formatBytes(call.response.bytes)}` : ""}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted small api-empty">
            No call matches the filter; {calls.length} call{calls.length === 1 ? " is" : "s are"} hidden.
          </p>
        )}
        <p className="muted small api-foot">
          {visible.length} of {calls.length} call{calls.length === 1 ? "" : "s"} · {formatMs(total)} in total
          {more ? " · more calls than the list keeps; the rest are in each step's http.json" : ""}
        </p>
      </div>
      {current ? <CallDetail key={current.step} call={current} runId={runId} onStep={onStep} /> : <p className="muted api-empty">Select a call.</p>}
    </div>
  );
}

/** The call a step sent or checked, in brief, on the Steps tab. */
export function ApiCallCard({ call, step, onOpen }: { call: HttpCall; step: number; onOpen: () => void }) {
  const checks = call.checks.filter((check) => check.step === step);
  const saves = call.saves.filter((save) => save.step === step);
  const failed = checks.filter((check) => check.verdict === "failed").length;
  return (
    <div className={`api-card${callFailed(call) ? " api-card-failed" : ""}`}>
      <div className="api-card-row">
        <Method method={call.method} />
        <span className="mono api-card-url">
          <WithMasks text={call.url} />
        </span>
        <span className={`api-status api-status-${statusClass(call)}`}>{statusText(call)}</span>
      </div>
      <div className="api-card-row muted small">
        <span>{call.step === step ? "Sent by this step" : `Uses the response of step ${call.step}`}</span>
        <span>{formatMs(call.duration_ms)}</span>
        {call.response ? <span>{formatBytes(call.response.bytes)}</span> : null}
        {checks.length ? <span>{failed ? `${failed} of ${checks.length} checks failed` : `${checks.length} check${checks.length === 1 ? "" : "s"} passed`}</span> : null}
        {saves.map((save) => (
          <span key={save.name}>
            saved <span className="mono">{`{{${save.name}}}`}</span>
          </span>
        ))}
        <button type="button" className="link-button api-card-open" onClick={onOpen}>
          Open in API calls <Icon name="chevron" size={11} />
        </button>
      </div>
    </div>
  );
}
