import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CloudsReport } from "../lib/types";
import { cloud, cloudDevices, cloudsReport } from "../test/cloudFixtures";
import { IntegrationsScreen } from "./Integrations";

let fetchMock: ReturnType<typeof vi.fn>;
let report: CloudsReport;

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  report = cloudsReport();
  fetchMock = vi.fn((url: string) => {
    if (url === "/api/clouds") return respond(report);
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function renderScreen() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <IntegrationsScreen />
    </QueryClientProvider>,
  );
}

const card = async (name: string) => within((await screen.findByRole("article", { name })) as HTMLElement);

describe("IntegrationsScreen", () => {
  it("shows each cloud with its connection and what it can run", async () => {
    renderScreen();
    const browserstack = await card("BrowserStack");
    expect(browserstack.getByText("Connected as alice")).toBeTruthy();
    expect(browserstack.getByRole("link", { name: /dashboard/i }).getAttribute("rel")).toContain("noopener");
    const sauce = await card("Sauce Labs");
    expect(sauce.getByText("Not connected")).toBeTruthy();
    expect(sauce.getByText(/saucectl is not installed/)).toBeTruthy();
    expect(sauce.getByRole("link", { name: /where do i find my key/i }).getAttribute("href")).toBe("https://example.test/keys");
    expect(sauce.getByRole("link", { name: /sign up/i })).toBeTruthy();
    expect(screen.getByText("~/.hercules/clouds.env")).toBeTruthy();
    expect(screen.queryByText(/egress policy is/i)).toBeNull();
  });

  it("connects a cloud, clears the key and shows the check", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/saucelabs" && init?.method === "PUT") {
        return respond({ ...cloud({ id: "saucelabs", name: "Sauce Labs", username: "carol" }), check: { ok: true, message: "signed in as carol" }, note: null });
      }
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const sauce = await card("Sauce Labs");
    const key = sauce.getByLabelText("Access key") as HTMLInputElement;
    expect(key.type).toBe("password");
    expect(key.getAttribute("autocomplete")).toBe("off");
    fireEvent.change(sauce.getByLabelText("User name"), { target: { value: "carol" } });
    fireEvent.change(key, { target: { value: "s3cret-key" } });
    fireEvent.click(sauce.getByRole("button", { name: "Connect Sauce Labs" }));
    await waitFor(() => expect(sauce.getByRole("status").textContent).toMatch(/Saved\. signed in as carol/));
    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(put?.[0]).toBe("/api/clouds/saucelabs");
    expect(JSON.parse(String((put?.[1] as RequestInit).body))).toEqual({ username: "carol", access_key: "s3cret-key" });
    expect(key.value).toBe("");
    expect(document.body.textContent).not.toContain("s3cret-key");
  });

  it("shows why connecting failed and keeps nothing secret in the page", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/saucelabs" && init?.method === "PUT") return respond({ error: "the user name has a newline" }, 422);
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const sauce = await card("Sauce Labs");
    fireEvent.click(sauce.getByRole("button", { name: "Connect Sauce Labs" }));
    expect(sauce.getByText("Paste the access key.")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PUT")).toBe(false);
    fireEvent.change(sauce.getByLabelText("User name"), { target: { value: "carol" } });
    fireEvent.change(sauce.getByLabelText("Access key"), { target: { value: "k" } });
    fireEvent.click(sauce.getByRole("button", { name: "Connect Sauce Labs" }));
    await waitFor(() => expect(sauce.getByRole("alert").textContent).toBe("the user name has a newline"));
  });

  it("names the environment variables of an env-sourced cloud and offers no disconnect", async () => {
    renderScreen();
    const testmu = await card("TestMu AI");
    expect(testmu.getByText("Connected as bob · from environment")).toBeTruthy();
    expect(testmu.getByText("LT_USERNAME")).toBeTruthy();
    expect(testmu.getByText("LT_ACCESS_KEY")).toBeTruthy();
    expect(testmu.queryByRole("button", { name: /disconnect/i })).toBeNull();
    expect(testmu.queryByLabelText("Access key")).toBeNull();
    expect(testmu.getByRole("button", { name: /test connection/i })).toBeTruthy();
  });

  it("tests and disconnects a saved cloud", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/browserstack/check") return respond({ ok: false, message: "401: wrong access key" });
      if (url === "/api/clouds/browserstack" && init?.method === "DELETE") return respond({ ...cloud({ connected: false, source: "none", username: null }), removed: true, note: null });
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const browserstack = await card("BrowserStack");
    fireEvent.click(browserstack.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(browserstack.getByRole("status").textContent).toMatch(/wrong access key/));
    fireEvent.click(browserstack.getByRole("button", { name: "Disconnect" }));
    fireEvent.click(browserstack.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(browserstack.getByRole("status").textContent).toMatch(/Saved keys removed/));
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/clouds/browserstack" && (init as RequestInit | undefined)?.method === "DELETE")).toBe(true);
  });

  it("saves the Sauce Labs region and shows the check that follows", async () => {
    report = cloudsReport();
    report.clouds[1] = { ...report.clouds[1], connected: true, source: "file", username: "carol" };
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/saucelabs/region" && init?.method === "PUT") {
        return respond({ ...report.clouds[1], region: "eu-central-1", region_source: "file", check: { ok: true, message: "connected to Sauce Labs as carol" }, note: null });
      }
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const sauce = await card("Sauce Labs");
    const region = sauce.getByLabelText("Region") as HTMLSelectElement;
    expect(region.value).toBe("us-west-1");
    expect(Array.from(region.options).map((option) => option.value)).toEqual(["us-west-1", "us-east-4", "eu-central-1"]);
    fireEvent.change(region, { target: { value: "eu-central-1" } });
    await waitFor(() => expect(sauce.getByRole("status").textContent).toMatch(/Region eu-central-1 saved\. connected to Sauce Labs as carol/));
    const put = fetchMock.mock.calls.find(([url]) => url === "/api/clouds/saucelabs/region");
    expect(JSON.parse(String((put?.[1] as RequestInit).body))).toEqual({ region: "eu-central-1" });
    expect((await card("BrowserStack")).queryByLabelText("Region")).toBeNull();
  });

  it("locks the region set by SAUCE_REGION", async () => {
    report = cloudsReport();
    report.clouds[1] = { ...report.clouds[1], region: "eu-central-1", region_source: "env" };
    renderScreen();
    const sauce = await card("Sauce Labs");
    expect((sauce.getByLabelText("Region") as HTMLSelectElement).disabled).toBe(true);
    expect(sauce.getByText("SAUCE_REGION")).toBeTruthy();
  });

  it("warns when the egress policy keeps everything local", async () => {
    report = cloudsReport({ egress_allows: false });
    renderScreen();
    expect((await screen.findByRole("note")).textContent).toMatch(/egress policy is local/i);
  });

  it("fetches a connected cloud's devices and lists them in a dialog", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/browserstack/devices" && init?.method === "POST") return respond(cloudDevices());
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const browserstack = await card("BrowserStack");
    expect((await card("Sauce Labs")).queryByRole("button", { name: /devices/i })).toBeNull();
    fireEvent.click(await browserstack.findByRole("button", { name: "Fetch devices" }));
    const dialog = within(await screen.findByRole("dialog", { name: "BrowserStack devices" }));
    expect(dialog.getByRole("tab", { name: /Mobile 4/ }).getAttribute("aria-selected")).toBe("true");
    expect(dialog.getByText("iPhone 16")).toBeTruthy();
    expect(dialog.getByText("Simulator")).toBeTruthy();
    expect(browserstack.getByText(/4 mobile devices · 2 desktop browsers/)).toBeTruthy();
    expect(browserstack.getByRole("button", { name: "View devices" })).toBeTruthy();

    fireEvent.change(dialog.getByLabelText("Platform"), { target: { value: "android" } });
    fireEvent.change(dialog.getByLabelText("Type"), { target: { value: "tablet" } });
    expect(dialog.getByText("Samsung Galaxy Tab S9")).toBeTruthy();
    expect(dialog.queryByText("Google Pixel 9")).toBeNull();
    fireEvent.change(dialog.getByLabelText("Search"), { target: { value: "nothing like this" } });
    expect(dialog.getByText("No device matches these filters.")).toBeTruthy();

    fireEvent.click(dialog.getByRole("tab", { name: /Desktop 2/ }));
    expect(dialog.getByText("Windows 11")).toBeTruthy();
    expect(dialog.getByText("131.0, 130.0")).toBeTruthy();
    fireEvent.change(dialog.getByLabelText("Operating system"), { target: { value: "macOS" } });
    expect(dialog.queryByText("Windows 11")).toBeNull();
    expect(dialog.getByText("Safari")).toBeTruthy();
  });

  it("shows the saved list on load, its notes, and marks one from older settings", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/clouds/browserstack/devices") return respond(cloudDevices({ stale: true, notes: ["desktop browsers: BrowserStack answered HTTP 500"] }));
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const browserstack = await card("BrowserStack");
    expect(await browserstack.findByText("older settings")).toBeTruthy();
    fireEvent.click(browserstack.getByRole("button", { name: "View devices" }));
    const dialog = within(await screen.findByRole("dialog", { name: "BrowserStack devices" }));
    expect(dialog.getByText(/fetched with another user name or region/)).toBeTruthy();
    expect(dialog.getByText(/BrowserStack answered HTTP 500/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/clouds/browserstack/devices" && (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("refreshes the list from the dialog", async () => {
    let fetched = cloudDevices();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/browserstack/devices" && init?.method === "POST") {
        fetched = cloudDevices({ mobile: [], fetched_at: "2026-11-18T12:00:00+00:00" });
        return respond(fetched);
      }
      if (url === "/api/clouds/browserstack/devices") return respond(fetched);
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const browserstack = await card("BrowserStack");
    fireEvent.click(await browserstack.findByRole("button", { name: "View devices" }));
    const dialog = within(await screen.findByRole("dialog", { name: "BrowserStack devices" }));
    fireEvent.click(dialog.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(dialog.getByText("BrowserStack listed no mobile devices for this account.")).toBeTruthy());
    expect(browserstack.getByText(/0 mobile devices · 2 desktop browsers/)).toBeTruthy();
  });

  it("says why fetching the devices failed", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/browserstack/devices" && init?.method === "POST") return respond({ error: "BrowserStack refused the credentials (HTTP 401): check the user name and access key" }, 502);
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const browserstack = await card("BrowserStack");
    fireEvent.click(await browserstack.findByRole("button", { name: "Fetch devices" }));
    await waitFor(() => expect(browserstack.getByRole("status").textContent).toMatch(/Could not fetch the devices: BrowserStack refused the credentials/));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("forgets the list when the cloud is disconnected", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds/browserstack" && init?.method === "DELETE") {
        report = cloudsReport();
        report.clouds[0] = cloud({ connected: false, source: "none", username: null });
        return respond({ ...report.clouds[0], removed: true, note: null });
      }
      if (url === "/api/clouds/browserstack/devices") return respond(cloudDevices());
      if (url === "/api/clouds") return respond(report);
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    const browserstack = await card("BrowserStack");
    expect(await browserstack.findByText(/4 mobile devices/)).toBeTruthy();
    fireEvent.click(browserstack.getByRole("button", { name: "Disconnect" }));
    fireEvent.click(browserstack.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(browserstack.queryByText(/4 mobile devices/)).toBeNull());
    expect(browserstack.queryByRole("button", { name: /devices/i })).toBeNull();
  });

  it("offers the TestMu AI regions and names LT_REGION when it is set", async () => {
    report = cloudsReport();
    report.clouds[2] = { ...report.clouds[2], region: "eu", region_source: "env" };
    renderScreen();
    const testmu = await card("TestMu AI");
    const region = testmu.getByLabelText("Region") as HTMLSelectElement;
    expect(Array.from(region.options).map((option) => option.value)).toEqual(["us", "eu", "ap"]);
    expect(region.value).toBe("eu");
    expect(region.disabled).toBe(true);
    expect(testmu.getByText("LT_REGION")).toBeTruthy();
  });
});
