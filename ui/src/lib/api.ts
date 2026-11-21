import type { Guard } from "./guards";

// Same-origin calls to the daemon. The session is an HttpOnly cookie set by /login,
// so the UI never holds the API token.

export const UNAUTHORIZED_EVENT = "hercules:unauthorized";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** A 2xx response whose body is not the shape the UI expects (another daemon version, a proxy page). */
export const INVALID_RESPONSE = 0;

interface RequestOptions<T> {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  raw?: Blob;
  signal?: AbortSignal;
  /** Checks the payload's key fields; a mismatch throws an ApiError instead of returning it. */
  guard?: Guard<T>;
}

export async function api<T>(path: string, options: RequestOptions<T> = {}): Promise<T> {
  const init: RequestInit = { method: options.method ?? "GET", credentials: "same-origin", signal: options.signal, headers: { Accept: "application/json" } };
  if (options.raw !== undefined) {
    init.body = options.raw;
    init.headers = { ...init.headers, "Content-Type": "application/octet-stream" };
  } else if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
    init.headers = { ...init.headers, "Content-Type": "application/json" };
  }
  const response = await fetch(path, init);
  if (response.status === 401) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const body = (payload ?? {}) as { error?: string; detail?: unknown };
    throw new ApiError(response.status, body.error ?? `HTTP ${response.status}`, body.detail);
  }
  if (options.guard && !options.guard(payload)) {
    const where = path.split("?")[0];
    throw new ApiError(INVALID_RESPONSE, `Unexpected response from the daemon for ${where}. Is the UI the same version as the daemon? Reload the page or restart \`hercules ui\`.`, payload);
  }
  return payload as T;
}

export const post = <T>(path: string, body?: unknown, guard?: Guard<T>) => api<T>(path, { method: "POST", body: body ?? {}, guard });
export const put = <T>(path: string, body: unknown, guard?: Guard<T>) => api<T>(path, { method: "PUT", body, guard });
export const patch = <T>(path: string, body: unknown, guard?: Guard<T>) => api<T>(path, { method: "PATCH", body, guard });
export const remove = <T>(path: string, guard?: Guard<T>) => api<T>(path, { method: "DELETE", guard });
export const upload = <T>(path: string, raw: Blob, guard?: Guard<T>) => api<T>(path, { method: "PUT", raw, guard });

/** What `evidenceUrl` returns for a reference that would leave the run's directory. */
export const BLOCKED_EVIDENCE_URL = "about:blank";

/**
 * `hercules://runs/<id>/steps/2/som.jpg` (or `steps/2/som.jpg`) as a URL of the evidence
 * endpoint. Each segment is encoded; a reference with an empty, `.` or `..` segment (which the
 * browser would resolve before sending) gets BLOCKED_EVIDENCE_URL instead.
 */
export function evidenceUrl(runId: string, ref: string): string {
  const prefix = `hercules://runs/${runId}/`;
  let relative = ref.startsWith(prefix) ? ref.slice(prefix.length) : ref;
  if (relative.startsWith("hercules://")) {
    relative = relative.replace(/^hercules:\/\/runs\/[^/]+\//, "");
  }
  relative = relative.replace(/^\/+/, "");
  if (relative.split(/[/\\]/).some((part) => part === "" || part === "." || part === "..")) return BLOCKED_EVIDENCE_URL;
  const safe = relative
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `/api/runs/${encodeURIComponent(runId)}/evidence/${safe}`;
}

/** The file name at the end of an evidence reference. */
export function evidenceName(ref: string): string {
  return ref.split("/").pop() ?? ref;
}
