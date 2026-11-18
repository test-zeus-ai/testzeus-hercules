import type { Cloud, CloudDevices, CloudRun, CloudsReport } from "../lib/types";

export function cloud(overrides: Partial<Cloud> = {}): Cloud {
  return {
    id: "browserstack",
    name: "BrowserStack",
    connected: true,
    source: "file",
    username: "alice",
    username_env: "BROWSERSTACK_USERNAME",
    access_key_env: "BROWSERSTACK_ACCESS_KEY",
    region: null,
    region_source: null,
    region_env: null,
    regions: [],
    problem: null,
    web_mode: "connect",
    web_ready: null,
    mobile_ready: null,
    website_url: "https://example.test/",
    dashboard_url: "https://example.test/dashboard",
    keys_url: "https://example.test/keys",
    signup_url: "https://example.test/signup",
    ci_runner: "browserstack-sdk",
    ci_config: "browserstack.yml",
    ...overrides,
  };
}

export function cloudsReport(overrides: Partial<CloudsReport> = {}): CloudsReport {
  return {
    clouds: [
      cloud(),
      cloud({ id: "saucelabs", name: "Sauce Labs", connected: false, source: "none", username: null, username_env: "SAUCE_USERNAME", access_key_env: "SAUCE_ACCESS_KEY", region: "us-west-1", region_source: "default", region_env: "SAUCE_REGION", regions: ["us-west-1", "us-east-4", "eu-central-1"], web_mode: "saucectl", web_ready: "saucectl is not installed: npm install -g saucectl", ci_config: ".sauce/config.yml", ci_runner: "saucectl" }),
      cloud({ id: "testmu", name: "TestMu AI", connected: true, source: "env", username: "bob", username_env: "LT_USERNAME", access_key_env: "LT_ACCESS_KEY", region: "us", region_source: "default", region_env: "LT_REGION", regions: ["us", "eu", "ap"], ci_config: "hyperexecute.yaml" }),
    ],
    keys_file: "~/.hercules/clouds.env",
    egress_allows: true,
    browsers: ["chrome", "edge", "chromium"],
    os_names: ["windows", "macos"],
    ...overrides,
  };
}

export function cloudDevices(overrides: Partial<CloudDevices> = {}): CloudDevices {
  return {
    cloud: "browserstack",
    username: "alice",
    region: null,
    fetched_at: "2026-11-18T10:20:00+00:00",
    mobile: [
      { name: "Google Pixel 9", platform: "android", os_version: "15.0", kind: "phone", real: true, tier: null, status: null },
      { name: "Samsung Galaxy Tab S9", platform: "android", os_version: "14.0", kind: "tablet", real: true, tier: null, status: null },
      { name: "iPhone 16", platform: "ios", os_version: "18", kind: "phone", real: true, tier: "premium", status: null },
      { name: "iPhone Simulator", platform: "ios", os_version: "17.5", kind: "phone", real: false, tier: null, status: null },
    ],
    desktop: [
      { os: "Windows", os_version: "11", browser: "chrome", versions: ["131.0", "130.0"] },
      { os: "macOS", os_version: "Sonoma", browser: "safari", versions: ["17.3"] },
    ],
    notes: [],
    stale: false,
    ...overrides,
  };
}

export function cloudRun(overrides: Partial<CloudRun> = {}): CloudRun {
  return {
    id: "cr_1",
    run_id: "r_parent",
    case_id: "demo-login",
    cloud: "browserstack",
    cloud_name: "BrowserStack",
    kind: "web",
    mode: "connect",
    options: { browser: "chrome", os: "windows" },
    status: "running",
    created_at: "2026-11-12T10:00:00Z",
    started_at: "2026-11-12T10:00:02Z",
    finished_at: null,
    dashboard_url: "https://example.test/dashboard",
    session_url: null,
    message: null,
    exit_code: null,
    ...overrides,
  };
}
