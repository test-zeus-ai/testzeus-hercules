/** The run page's API calls (`GET /api/runs/{id}/http`): types, and the helpers the viewer uses to show and copy them. */

export interface HttpMessage {
  headers: [string, string][];
  body: string | null;
}

export interface HttpResponseView extends HttpMessage {
  status: number | null;
  reason: string;
  bytes: number | null;
}

export interface HttpCheckView {
  step: number;
  check: string;
  expected: string;
  actual: string;
  verdict: "passed" | "failed";
  text: string;
}

export interface HttpSaveView {
  step: number;
  name: string;
  value: string;
}

export interface HttpCall {
  step: number;
  action: string;
  evidence: string;
  method: string;
  url: string;
  duration_ms: number | null;
  error: string | null;
  request: HttpMessage;
  response: HttpResponseView | null;
  checks: HttpCheckView[];
  saves: HttpSaveView[];
  used_by: number[];
}

export interface HttpCalls {
  run_id: string;
  calls: HttpCall[];
  more: boolean;
}

/** What the run wrote in place of a secret value (the HTTP redactor's marker and the test-data mask). */
export const MASKS = ["[REDACTED]", "******"] as const;

export function isMasked(text: string): boolean {
  return MASKS.includes(text as (typeof MASKS)[number]);
}

/** `text` cut at each mask: the parts between masks, with `null` where a mask stood. */
export function splitMasked(text: string): (string | null)[] {
  const parts: (string | null)[] = [];
  let rest = text;
  for (;;) {
    const found = MASKS.map((mask) => ({ mask, at: rest.indexOf(mask) }))
      .filter((hit) => hit.at >= 0)
      .sort((a, b) => a.at - b.at)[0];
    if (!found) break;
    if (found.at > 0) parts.push(rest.slice(0, found.at));
    parts.push(null);
    rest = rest.slice(found.at + found.mask.length);
  }
  if (rest) parts.push(rest);
  return parts;
}

export type StatusClass = "2" | "3" | "4" | "5" | "error";

export function statusClass(call: HttpCall): StatusClass {
  const status = call.response?.status;
  if (!status || call.error) return "error";
  const first = String(status)[0];
  return first === "2" || first === "3" || first === "4" || first === "5" ? first : "error";
}

/** A call that needs attention: no response, a 4xx/5xx, or a check on its response failed. */
export function callFailed(call: HttpCall): boolean {
  const cls = statusClass(call);
  return cls === "4" || cls === "5" || cls === "error" || call.checks.some((check) => check.verdict === "failed");
}

export function statusText(call: HttpCall): string {
  if (call.error || !call.response) return "No response";
  return `${call.response.status ?? ""} ${call.response.reason}`.trim();
}

export function splitUrl(url: string): { host: string; path: string } {
  try {
    const parsed = new URL(url);
    return { host: parsed.host, path: `${parsed.pathname}${parsed.search}` || "/" };
  } catch {
    return { host: "", path: url };
  }
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "–";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type StatusFilter = "all" | "2" | "4" | "5";

export function matchesFilter(call: HttpCall, text: string, status: StatusFilter): boolean {
  const cls = statusClass(call);
  if (status !== "all" && !(status === "5" ? cls === "5" || cls === "error" : cls === status)) return false;
  const query = text.trim().toLowerCase();
  if (!query) return true;
  return `${call.method} ${call.url} ${call.response?.status ?? ""} step ${call.step}`.toLowerCase().includes(query);
}

const TRUNCATED = /\.\.\.\[truncated: (\d+) more characters\]$/;

export interface BodyView {
  /** The body to show: pretty-printed JSON, or the text as sent. */
  text: string;
  json: boolean;
  /** Characters the run left out of a long body. */
  truncated: number | null;
}

export function bodyView(body: string | null): BodyView | null {
  if (body === null || body === "") return null;
  const cut = TRUNCATED.exec(body);
  const text = cut ? body.slice(0, cut.index) : body;
  const truncated = cut ? Number(cut[1]) : null;
  if (!cut) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed !== null && typeof parsed === "object") return { text: JSON.stringify(parsed, null, 2), json: true, truncated };
    } catch {
      // not JSON: shown as sent
    }
  }
  return { text, json: false, truncated };
}

export type JsonToken = { kind: "key" | "string" | "number" | "literal" | "plain" | "masked"; text: string };

const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g;

/** Pretty-printed JSON as coloured tokens; a masked string value becomes a `masked` token. */
export function jsonTokens(text: string): JsonToken[] {
  const tokens: JsonToken[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const at = match.index ?? 0;
    if (at > last) tokens.push({ kind: "plain", text: text.slice(last, at) });
    const [whole, quoted, colon] = match;
    if (quoted && colon) {
      tokens.push({ kind: "key", text: quoted }, { kind: "plain", text: colon });
    } else if (quoted) {
      tokens.push(isMasked(quoted.slice(1, -1)) ? { kind: "masked", text: quoted } : { kind: "string", text: quoted });
    } else {
      tokens.push({ kind: /^[tfn]/.test(whole) ? "literal" : "number", text: whole });
    }
    last = at + whole.length;
  }
  if (last < text.length) tokens.push({ kind: "plain", text: text.slice(last) });
  return tokens;
}

function shellQuote(text: string): string {
  return `'${text.replace(/'/g, `'\\''`)}'`;
}

/** The request as a cURL command, masked values left masked. */
export function curlCommand(call: HttpCall): string {
  const lines = [`curl -X ${call.method} ${shellQuote(call.url)}`];
  for (const [name, value] of call.request.headers) lines.push(`  -H ${shellQuote(`${name}: ${value}`)}`);
  if (call.request.body) lines.push(`  --data-raw ${shellQuote(call.request.body)}`);
  return lines.join(" \\\n");
}

/** The request and the response as plain text. */
export function exchangeText(call: HttpCall): string {
  const headers = (pairs: [string, string][]) => pairs.map(([name, value]) => `${name}: ${value}`).join("\n");
  const request = [`=== REQUEST ===`, `${call.method} ${call.url}`, headers(call.request.headers), "", bodyView(call.request.body)?.text ?? ""];
  const response = call.response
    ? [`=== RESPONSE ===`, `${statusText(call)}${call.duration_ms !== null ? ` (${call.duration_ms} ms)` : ""}`, headers(call.response.headers), "", bodyView(call.response.body)?.text ?? ""]
    : [`=== NO RESPONSE ===`, call.error ?? ""];
  return [...request, "", ...response].join("\n").trim();
}

/** The steps whose evidence names an `http.json`: the run has API calls to show. */
export function httpCallCount(steps: { evidence: string[] }[]): number {
  return new Set(steps.flatMap((step) => step.evidence.filter((ref) => ref.endsWith("/http.json")))).size;
}
