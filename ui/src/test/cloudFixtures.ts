import type { Cloud, CloudRun, CloudsReport } from "../lib/types";

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
      cloud({ id: "saucelabs", name: "Sauce Labs", connected: false, source: "none", username: null, username_env: "SAUCE_USERNAME", access_key_env: "SAUCE_ACCESS_KEY", region: "us-west-1", region_source: "default", regions: ["us-west-1", "us-east-4", "eu-central-1"], web_mode: "saucectl", web_ready: "saucectl is not installed: npm install -g saucectl", ci_config: ".sauce/config.yml", ci_runner: "saucectl" }),
      cloud({ id: "testmu", name: "TestMu AI", connected: true, source: "env", username: "bob", username_env: "LT_USERNAME", access_key_env: "LT_ACCESS_KEY", ci_config: "hyperexecute.yaml" }),
    ],
    keys_file: "~/.hercules/clouds.env",
    egress_allows: true,
    browsers: ["chrome", "edge", "chromium"],
    os_names: ["windows", "macos"],
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
