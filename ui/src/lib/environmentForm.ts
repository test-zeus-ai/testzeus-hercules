import { BASE_URL_PATTERN, BUILTIN_PLACEHOLDERS, DATA_KEY_PATTERN, PLACEHOLDER, SECRET_NAME_PATTERN, VIEWPORT_PATTERN } from "./caseForm";
import type { FormDatum } from "./caseForm";
import { EMPTY_BROWSER_OPTIONS, browserOptionsBody, browserOptionsToForm, browserSummary, validateBrowserOptions } from "./browserOptionsForm";
import type { BrowserOptionsForm } from "./browserOptionsForm";
import type { Environment, MobileApp } from "./types";

const ENV_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const PACKAGE = /^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$/;
const ACTIVITY = /^[A-Za-z0-9_.$]+$/;
const BUNDLE_ID = /^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const VERSION = /^[0-9]+(?:\.[0-9]+){0,2}$/;
const CLOUD_APP = /^(?:bs:\/\/[A-Za-z0-9_-]+|storage:(?:filename=)?[A-Za-z0-9._-]+)$/;
const APP_FILE = /\.(apk|aab|app|ipa|zip)\/?$/i;
const APPIUM_URL = /^https?:\/\/[^\s/?#@]+(?:\/[^\s?#]*)?$/;

/** What an environment tests: a web app in a desktop browser, a web app on an emulated phone, or a native app. */
export type Target = "web" | "app";

export interface EnvForm {
  name: string;
  target: Target;
  baseUrl: string;
  viewport: string;
  browser: string;
  browserOptions: BrowserOptionsForm;
  device: string;
  platform: "android" | "ios";
  app: string;
  appPackage: string;
  activity: string;
  bundleId: string;
  appDevice: string;
  platformVersion: string;
  appiumUrl: string;
  cloud: "" | "browserstack" | "saucelabs";
  variables: FormDatum[];
}

export const EMPTY_ENV: EnvForm = {
  name: "",
  target: "web",
  baseUrl: "",
  viewport: "1280x800",
  browser: "",
  browserOptions: EMPTY_BROWSER_OPTIONS,
  device: "",
  platform: "android",
  app: "",
  appPackage: "",
  activity: "",
  bundleId: "",
  appDevice: "",
  platformVersion: "",
  appiumUrl: "",
  cloud: "",
  variables: [],
};

export function environmentVariables(env: Pick<Environment, "secret_names" | "variables">): FormDatum[] {
  const rows: FormDatum[] = Object.entries(env.variables ?? {}).map(([key, value]) => (typeof value === "string" ? { key, value, secret: false } : { key, value: value.secret, secret: true }));
  for (const name of env.secret_names) {
    if (!rows.some((row) => row.key === name)) rows.push({ key: name, value: name, secret: true });
  }
  return rows;
}

export function environmentNames(env: Pick<Environment, "secret_names" | "variables"> | undefined): string[] {
  return env ? environmentVariables(env).map((row) => row.key) : [];
}

export function secretVariables(rows: FormDatum[]): string[] {
  return [...new Set(rows.filter((row) => row.secret && SECRET_NAME_PATTERN.test(row.value.trim())).map((row) => row.value.trim()))].sort();
}

export function envToForm(env: Environment): EnvForm {
  const app = env.mobile;
  return {
    ...EMPTY_ENV,
    name: env.name,
    target: app ? "app" : "web",
    baseUrl: env.base_url ?? "",
    viewport: env.viewport ?? "",
    browser: env.browser ?? "",
    browserOptions: browserOptionsToForm(env.browser_options),
    device: env.device ?? "",
    platform: app?.platform ?? "android",
    app: app?.app ?? "",
    appPackage: app?.package ?? "",
    activity: app?.activity ?? "",
    bundleId: app?.bundle_id ?? "",
    appDevice: app?.device ?? "",
    platformVersion: app?.platform_version ?? "",
    appiumUrl: app?.appium_url ?? "",
    cloud: app?.cloud ?? "",
    variables: environmentVariables(env),
  };
}

/** Problems by field; the daemon checks the same rules again (and knows the device profiles). */
export function validateEnvironment(form: EnvForm): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!ENV_NAME.test(form.name)) errors.name = "Lowercase letters, digits, '_' or '-'.";
  const baseUrl = form.baseUrl.trim();
  if (form.target === "web" && !BASE_URL_PATTERN.test(baseUrl)) errors.baseUrl = "An http(s) URL with a host, e.g. http://localhost:3000.";
  if (form.target === "app" && baseUrl && !BASE_URL_PATTERN.test(baseUrl)) errors.baseUrl = "An http(s) URL with a host, or leave it empty.";
  if (form.target === "web" && form.viewport.trim() && !VIEWPORT_PATTERN.test(form.viewport.trim())) errors.viewport = "WIDTHxHEIGHT, e.g. 1280x800.";
  if (form.target === "web") Object.assign(errors, validateBrowserOptions(form.browserOptions, form.browser));
  if (form.target === "web" && form.browser === "firefox" && form.device.trim()) {
    errors.device = "Firefox has no phone or tablet mode: choose Chromium, Chrome, Edge or WebKit for a device.";
  }
  if (form.target === "app") Object.assign(errors, validateApp(form));
  const keys = new Set<string>();
  form.variables.forEach((row, offset) => {
    const key = row.key.trim();
    if (!DATA_KEY_PATTERN.test(key)) errors[`variables.${offset}.key`] = "Names start with a letter or '_' and use letters, digits or '_'.";
    else if (BUILTIN_PLACEHOLDERS.includes(key)) errors[`variables.${offset}.key`] = `'${key}' is a built-in placeholder.`;
    else if (keys.has(key)) errors[`variables.${offset}.key`] = "This name is used twice.";
    keys.add(key);
    if (row.secret && !SECRET_NAME_PATTERN.test(row.value.trim())) errors[`variables.${offset}.value`] = "A secret names an environment variable, e.g. SHOP_PASSWORD.";
  });
  if (!errors.baseUrl) {
    const missing = [...baseUrl.matchAll(PLACEHOLDER)].map((match) => match[1].trim()).filter((name) => !keys.has(name) && !BUILTIN_PLACEHOLDERS.includes(name));
    if (missing.length) errors.baseUrl = `Add {{${missing[0]}}} below as a variable or secret.`;
  }
  return errors;
}

function validateApp(form: EnvForm): Record<string, string> {
  const errors: Record<string, string> = {};
  const app = form.app.trim();
  const android = form.platform === "android";
  if (app && !CLOUD_APP.test(app) && (app.startsWith("/") || app.includes("..") || app.includes("://") || !APP_FILE.test(app))) {
    errors.app = "A file inside the project (.apk, .aab, .app, .ipa, .zip) or a device cloud app id (bs://…, storage:…).";
  }
  if (android && form.appPackage.trim() && !PACKAGE.test(form.appPackage.trim())) errors.appPackage = "A package name, e.g. com.android.settings.";
  if (android && form.activity.trim() && !ACTIVITY.test(form.activity.trim())) errors.activity = "An activity, e.g. .Settings.";
  if (android && form.activity.trim() && !form.appPackage.trim()) errors.activity = "An activity needs the package.";
  if (!android && form.bundleId.trim() && !BUNDLE_ID.test(form.bundleId.trim())) errors.bundleId = "A bundle id, e.g. com.apple.Preferences.";
  const named = app || (android ? form.appPackage.trim() : form.bundleId.trim());
  if (!named) errors.app = errors.app ?? (android ? "Give the app file, or the installed app's package." : "Give the app file, or the installed app's bundle id.");
  if (form.platformVersion.trim() && !VERSION.test(form.platformVersion.trim())) errors.platformVersion = "A version such as 14 or 17.5.";
  if (form.cloud && form.appiumUrl.trim()) errors.appiumUrl = "A device cloud has its own server: leave this empty.";
  else if (form.appiumUrl.trim() && !APPIUM_URL.test(form.appiumUrl.trim())) errors.appiumUrl = "An http(s) URL without a user name or password, e.g. http://127.0.0.1:4723.";
  if (form.cloud && !app) errors.app = "A device cloud runs an app you give it: a file in the project or the cloud's app id.";
  if (app && CLOUD_APP.test(app) && !form.cloud) errors.cloud = "A cloud app id needs its device cloud.";
  return errors;
}

function mobileApp(form: EnvForm): MobileApp {
  const android = form.platform === "android";
  const value = (text: string) => text.trim() || null;
  return {
    platform: form.platform,
    app: value(form.app),
    package: android ? value(form.appPackage) : null,
    activity: android ? value(form.activity) : null,
    bundle_id: android ? null : value(form.bundleId),
    device: value(form.appDevice),
    platform_version: value(form.platformVersion),
    appium_url: form.cloud ? null : value(form.appiumUrl),
    cloud: form.cloud || null,
  };
}

/** The body of ``POST /api/environments``. */
export function environmentBody(form: EnvForm, overwrite: boolean): Record<string, unknown> {
  const web = form.target === "web";
  return {
    name: form.name,
    base_url: form.baseUrl.trim() || null,
    viewport: web ? form.viewport.trim() || null : null,
    browser: web ? form.browser || null : null,
    browser_options: web ? browserOptionsBody(form.browserOptions) : null,
    device: web ? form.device.trim() || null : null,
    mobile: web ? null : mobileApp(form),
    secret_names: [],
    variables: Object.fromEntries(form.variables.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.secret ? { secret: row.value.trim() } : row.value])),
    overwrite,
  };
}

/** One line saying where an environment's cases run, for lists. */
export function targetSummary(env: Environment): string {
  const app = env.mobile;
  if (app) {
    const name = app.package ?? app.bundle_id ?? app.app ?? "app";
    const where = app.cloud ?? (app.device || "local device");
    return `${app.platform === "ios" ? "iOS" : "Android"} app ${name} · ${where}`;
  }
  const browser = browserSummary(env.browser, env.browser_options);
  return [env.base_url ?? "", env.device ?? "", browser].filter(Boolean).join(" · ");
}
