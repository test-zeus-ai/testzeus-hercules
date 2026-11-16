import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CloudsReport } from "../lib/types";
import { cloud, cloudsReport } from "../test/cloudFixtures";
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
    expect(browserstack.getByText("Connected as alice · saved in Hercules")).toBeTruthy();
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

  it("warns when the egress policy keeps everything local", async () => {
    report = cloudsReport({ egress_allows: false });
    renderScreen();
    expect((await screen.findByRole("note")).textContent).toMatch(/egress policy is local/i);
  });
});
