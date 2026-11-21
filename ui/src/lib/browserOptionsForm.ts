/** The "Browser options" part of the environment form: Playwright launch and context options by their Python names.
 * The common ones have their own fields; any other option goes in the JSON box. The daemon checks every name
 * against Playwright's own and refuses secrets written as values. */

export interface BrowserOptionsForm {
  locale: string;
  timezoneId: string;
  colorScheme: "" | "light" | "dark" | "no-preference";
  userAgent: string;
  storageState: string;
  args: string;
  ignoreHttpsErrors: boolean;
  headed: boolean;
  recordVideo: boolean;
  more: string;
}

export const EMPTY_BROWSER_OPTIONS: BrowserOptionsForm = {
  locale: "",
  timezoneId: "",
  colorScheme: "",
  userAgent: "",
  storageState: "",
  args: "",
  ignoreHttpsErrors: false,
  headed: false,
  recordVideo: true,
  more: "",
};

export const BROWSER_CHOICES: { value: string; label: string }[] = [
  { value: "", label: "Default (Chromium)" },
  { value: "chromium", label: "Chromium (Playwright's build)" },
  { value: "chrome", label: "Google Chrome (installed)" },
  { value: "msedge", label: "Microsoft Edge (installed)" },
  { value: "firefox", label: "Firefox" },
  { value: "webkit", label: "WebKit (Safari's engine)" },
];

const TYPED = ["locale", "timezone_id", "color_scheme", "user_agent", "storage_state", "args", "ignore_https_errors", "headless", "record_video"];
const LOCALE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;
const ZONE = /^[A-Za-z]+(?:\/[A-Za-z0-9_+-]+)+$|^UTC$/;

export function browserOptionsToForm(options: Record<string, unknown> | null | undefined): BrowserOptionsForm {
  const source = options ?? {};
  const text = (key: string) => (typeof source[key] === "string" ? (source[key] as string) : "");
  const rest = Object.fromEntries(Object.entries(source).filter(([key, value]) => !TYPED.includes(key) && !isEmpty(value)));
  return {
    locale: text("locale"),
    timezoneId: text("timezone_id"),
    colorScheme: (text("color_scheme") as BrowserOptionsForm["colorScheme"]) || "",
    userAgent: text("user_agent"),
    storageState: text("storage_state"),
    args: Array.isArray(source.args) ? (source.args as string[]).join(" ") : "",
    ignoreHttpsErrors: source.ignore_https_errors === true,
    headed: source.headless === false,
    recordVideo: source.record_video !== false, // recorded unless the environment turns it off
    more: Object.keys(rest).length ? JSON.stringify(rest, null, 2) : "",
  };
}

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || (Array.isArray(value) && value.length === 0) || (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0);
}

function parseMore(text: string): Record<string, unknown> | string {
  if (!text.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
    return "A JSON object, e.g. {\"geolocation\": {\"latitude\": 52.52, \"longitude\": 13.4}}.";
  } catch {
    return "Not valid JSON.";
  }
}

/** Problems by field (keys prefixed "bo."). */
export function validateBrowserOptions(form: BrowserOptionsForm, browser: string): Record<string, string> {
  const errors: Record<string, string> = {};
  if (form.locale.trim() && !LOCALE.test(form.locale.trim())) errors["bo.locale"] = "A locale such as en-GB or de-DE.";
  if (form.timezoneId.trim() && !ZONE.test(form.timezoneId.trim())) errors["bo.timezoneId"] = "An IANA time zone such as Europe/Berlin.";
  const state = form.storageState.trim();
  if (state && (state.startsWith("/") || state.startsWith("~") || state.split(/[\\/]/).includes("..") || !state.toLowerCase().endsWith(".json"))) {
    errors["bo.storageState"] = "A .json file inside the project, e.g. .hercules/auth/admin.json.";
  }
  const bad = splitArgs(form.args).filter((arg) => !arg.startsWith("-"));
  if (bad.length) errors["bo.args"] = `Switches start with - or --: ${bad.join(", ")}.`;
  const more = parseMore(form.more);
  if (typeof more === "string") errors["bo.more"] = more;
  else if (more.firefox_user_prefs && browser !== "firefox") errors["bo.more"] = "firefox_user_prefs is for Firefox only.";
  return errors;
}

function splitArgs(text: string): string[] {
  return text.split(/\s+/).filter(Boolean);
}

/** ``browser_options`` for the request body, or null when nothing is set. */
export function browserOptionsBody(form: BrowserOptionsForm): Record<string, unknown> | null {
  const more = parseMore(form.more);
  const body: Record<string, unknown> = typeof more === "string" ? {} : { ...more };
  const set = (key: string, value: unknown) => {
    if (value !== "" && value !== false && !(Array.isArray(value) && value.length === 0)) body[key] = value;
  };
  set("locale", form.locale.trim());
  set("timezone_id", form.timezoneId.trim());
  set("color_scheme", form.colorScheme);
  set("user_agent", form.userAgent.trim());
  set("storage_state", form.storageState.trim());
  set("args", splitArgs(form.args));
  set("ignore_https_errors", form.ignoreHttpsErrors);
  if (!form.recordVideo) body.record_video = false; // on by default: only turning it off is saved
  if (form.headed) body.headless = false;
  return Object.keys(body).length ? body : null;
}

/** A short line for lists: the browser and the options set. */
export function browserSummary(browser: string | null | undefined, options: Record<string, unknown> | null | undefined): string {
  const name = BROWSER_CHOICES.find((choice) => choice.value === (browser ?? ""))?.label.split(" (")[0] ?? browser ?? "";
  const count = Object.values(options ?? {}).filter((value) => !isEmpty(value) && value !== false).length;
  if (!browser && !count) return "";
  return count ? `${name || "Chromium"} · ${count} option${count === 1 ? "" : "s"}` : name;
}
