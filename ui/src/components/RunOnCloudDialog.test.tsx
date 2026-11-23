import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CloudsReport } from "../lib/types";
import { cloudRun, cloudsReport } from "../test/cloudFixtures";
import { RunOnCloudDialog } from "./RunOnCloudDialog";

let fetchMock: ReturnType<typeof vi.fn>;
let report: CloudsReport;

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  report = cloudsReport();
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/clouds") return respond(report);
    if (url === "/api/runs/r_1/cloud-runs" && init?.method === "POST") return respond(cloudRun({ id: "cr_new", status: "starting", options: JSON.parse(String(init.body)) }), 202);
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function renderDialog(onStarted = vi.fn(), native = false) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunOnCloudDialog runId="r_1" native={native} onClose={() => undefined} onStarted={onStarted} />
    </QueryClientProvider>,
  );
  return onStarted;
}

describe("RunOnCloudDialog", () => {
  it("disables the run, with the reason, for a cloud that is not ready", async () => {
    renderDialog();
    fireEvent.click(await screen.findByRole("radio", { name: /Sauce Labs/ }));
    const button = screen.getByRole("button", { name: "Run on Sauce Labs" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("Not connected yet.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /connect a cloud under integrations/i }).getAttribute("href")).toBe("/integrations");
    // The CI project is still offered.
    expect(screen.getByRole("link", { name: /download project for sauce labs ci/i }).getAttribute("href")).toBe("/api/runs/r_1/cloud-bundle?cloud=saucelabs&os=windows");
  });

  it("blocks every cloud when the egress policy is local", async () => {
    report = cloudsReport({ egress_allows: false });
    renderDialog();
    const button = (await screen.findByRole("button", { name: "Run on BrowserStack" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getAllByText(/egress policy is local/).length).toBeGreaterThan(0);
  });

  it("starts a web run with the chosen browser and OS and hands it back", async () => {
    const onStarted = renderDialog();
    expect(((await screen.findByRole("radio", { name: /BrowserStack/ })) as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByLabelText("Browser"), { target: { value: "edge" } });
    fireEvent.change(screen.getByLabelText("Operating system"), { target: { value: "macos" } });
    fireEvent.click(screen.getByRole("button", { name: "Run on BrowserStack" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledTimes(1));
    const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({ cloud: "browserstack", browser: "edge", os: "macos" });
    expect(onStarted.mock.calls[0][0]).toMatchObject({ id: "cr_new", status: "starting" });
  });

  it("asks for a device and version, not a browser, for a native app run", async () => {
    const onStarted = renderDialog(vi.fn(), true);
    await screen.findByRole("radio", { name: /BrowserStack/ });
    expect(screen.queryByLabelText("Browser")).toBeNull();
    fireEvent.change(screen.getByLabelText("Device"), { target: { value: "Pixel 8" } });
    fireEvent.click(screen.getByRole("button", { name: "Run on BrowserStack" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({ cloud: "browserstack", device: "Pixel 8" });
  });

  it("shows why the daemon would not start it", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/clouds") return respond(report);
      if (init?.method === "POST") return respond({ error: "set APP_PASSWORD in the daemon's environment" }, 409);
      return respond({ error: "not found" }, 404);
    });
    renderDialog();
    fireEvent.click(await screen.findByRole("button", { name: "Run on BrowserStack" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/APP_PASSWORD/));
  });
});
