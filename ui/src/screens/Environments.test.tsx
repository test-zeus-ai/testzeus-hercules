import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EnvironmentsScreen } from "./Environments";

const fetchMock = vi.fn();

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url === "/api/environments" && init?.method === "POST") return respond({ error: "environment staging already exists" }, 409);
    if (url === "/api/environments") return respond({ environments: [] });
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("EnvironmentsScreen", () => {
  it("drops a stale 409 'Replace it' once the form changes", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "staging" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:3000" } });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /replace it/i })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:4000" } });
    await waitFor(() => expect(screen.queryByRole("button", { name: /replace it/i })).toBeNull());
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toHaveLength(1);
  });
});

describe("EnvironmentsScreen native apps", () => {
  it("saves a native app environment without a base URL", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "android" } });
    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "app" } });
    fireEvent.change(screen.getByLabelText("Package"), { target: { value: "com.android.settings" } });
    fireEvent.change(screen.getByLabelText("Activity"), { target: { value: ".Settings" } });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, call]) => (call as RequestInit | undefined)?.method === "POST")!;
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toMatchObject({ name: "android", base_url: null, device: null, mobile: { platform: "android", package: "com.android.settings", activity: ".Settings" } });
  });
});

describe("EnvironmentsScreen browser options", () => {
  it("saves the browser, the typed options and the JSON ones together", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "firefox-de" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:3000" } });
    fireEvent.change(screen.getByLabelText("Browser"), { target: { value: "firefox" } });
    fireEvent.change(screen.getByLabelText("Locale"), { target: { value: "de-DE" } });
    fireEvent.change(screen.getByLabelText("Time zone"), { target: { value: "Europe/Berlin" } });
    fireEvent.change(screen.getByLabelText("Signed-in state (auth JSON)"), { target: { value: ".hercules/auth/admin.json" } });
    fireEvent.click(screen.getByLabelText("Accept invalid HTTPS certificates"));
    fireEvent.change(screen.getByLabelText("More Playwright options (JSON)"), {
      target: { value: '{"extra_http_headers": {"Authorization": {"secret": "API_TOKEN"}}, "context_extra": {"strict_selectors": true}}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, call]) => (call as RequestInit | undefined)?.method === "POST")!;
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.browser).toBe("firefox");
    expect(body.browser_options).toEqual({
      extra_http_headers: { Authorization: { secret: "API_TOKEN" } },
      context_extra: { strict_selectors: true },
      locale: "de-DE",
      timezone_id: "Europe/Berlin",
      storage_state: ".hercules/auth/admin.json",
      ignore_https_errors: true,
    });
  });

  it("refuses bad JSON and an auth file outside the project before saving", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "bad" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:3000" } });
    fireEvent.change(screen.getByLabelText("Signed-in state (auth JSON)"), { target: { value: "../secrets.json" } });
    fireEvent.change(screen.getByLabelText("More Playwright options (JSON)"), { target: { value: "{not json" } });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));
    await waitFor(() => expect(screen.getByText("Not valid JSON.")).toBeTruthy());
    expect(screen.getByText(/A .json file inside the project/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });
});
