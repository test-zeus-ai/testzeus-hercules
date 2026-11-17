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
  it("marks the fields each target requires", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    const required = (label: string) => screen.getByLabelText(label).getAttribute("aria-required") === "true";
    expect(screen.getByText("Required")).toBeTruthy();
    expect([required("Name"), required("Base URL"), required("Viewport")]).toEqual([true, true, false]);
    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "app" } });
    expect([required("Base URL"), required("App")]).toEqual([false, false]);
    fireEvent.change(screen.getByLabelText("Runs on"), { target: { value: "saucelabs" } });
    expect(required("App")).toBe(true);
    expect(screen.getByText("Base URL").classList.contains("is-required")).toBe(false);
    expect(screen.getByText("App").classList.contains("is-required")).toBe(true);
  });

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

describe("EnvironmentsScreen variables and secrets", () => {
  function renderScreen() {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
  }

  it("saves variables and secrets the base URL can use", async () => {
    renderScreen();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "sf" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://acme.example/secur/frontdoor.jsp?sid={{sid}}" } });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));
    expect(await screen.findByText("Add {{sid}} below as a variable or secret.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Add variable" }));
    fireEvent.change(screen.getAllByLabelText("Variable name")[0], { target: { value: "sid" } });
    fireEvent.click(screen.getByLabelText("Secret"));
    fireEvent.change(screen.getByLabelText("Environment variable"), { target: { value: "SF_SID" } });
    fireEvent.click(screen.getByRole("button", { name: "Add variable" }));
    fireEvent.change(screen.getAllByLabelText("Variable name")[1], { target: { value: "user" } });
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "admin@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, call]) => (call as RequestInit | undefined)?.method === "POST")!;
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({ secret_names: [], variables: { sid: { secret: "SF_SID" }, user: "admin@acme.example" } });
  });

  it("shows where each secret comes from and saves a value it never shows again", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/environments") return respond({ environments: [{ name: "sf", base_url: "https://acme.example", secret_names: ["SF_SID"], variables: { token: { secret: "API_TOKEN" } } }] });
      if (url.startsWith("/api/secrets?names=")) return respond({ secrets: [{ name: "API_TOKEN", source: "env" }, { name: "SF_SID", source: "none" }], file: "/home/me/.hercules/secrets.env" });
      if (url === "/api/secrets/SF_SID" && init?.method === "PUT") return respond({ name: "SF_SID", source: "file", note: null });
      return respond({ error: "not found" }, 404);
    });
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /edit/i }));
    expect(await screen.findByText(/API_TOKEN: set in the daemon's environment/)).toBeTruthy();
    expect(screen.getByText(/SF_SID: not set/)).toBeTruthy();
    expect(screen.queryByLabelText("Value of API_TOKEN")).toBeNull();
    const value = screen.getByLabelText("Value of SF_SID") as HTMLInputElement;
    expect(value.type).toBe("password");
    fireEvent.change(value, { target: { value: "00Dxx-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save value" }));

    await waitFor(() => expect(screen.getByText("Saved SF_SID.")).toBeTruthy());
    const put = fetchMock.mock.calls.find(([url]) => url === "/api/secrets/SF_SID");
    expect(JSON.parse((put?.[1] as RequestInit).body as string)).toEqual({ value: "00Dxx-secret" });
    expect(value.value).toBe("");
    expect(document.body.textContent).not.toContain("00Dxx-secret");
  });
});

describe("EnvironmentsScreen reference files", () => {
  it("asks to save first, then lists files and uploads new ones as raw bytes", async () => {
    let files = [{ name: "rules.md", kind: "text", size: 2048, chars: 1800, sha256: "a" }];
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/environments") return respond({ environments: [{ name: "shop", base_url: "http://127.0.0.1:8201", secret_names: [] }] });
      if (url === "/api/environments/shop/files") return respond({ environment: "shop", files, max_files: 20, max_bytes: 26214400, model: "local" });
      if (url === "/api/environments/shop/files/prices.csv" && init?.method === "PUT") {
        files = [...files, { name: "prices.csv", kind: "text", size: 24, chars: 22, sha256: "b" }];
        return respond({ file: files[1], environment: "shop", files, max_files: 20, max_bytes: 26214400, model: "local" });
      }
      return respond({ error: "not found" }, 404);
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    expect(screen.getByText(/read by the local model only, never sent to the hosted decider/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: /edit/i }));
    expect(await screen.findByText("rules.md")).toBeTruthy();
    expect(screen.getByText(/1,800 characters read/)).toBeTruthy();

    const file = new File(["item,price\ngreen tea,4\n"], "prices.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Add reference files"), { target: { files: [file] } });

    expect(await screen.findByText("prices.csv")).toBeTruthy();
    const put = fetchMock.mock.calls.find(([url]) => url === "/api/environments/shop/files/prices.csv");
    const init = put?.[1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/octet-stream");
    expect(init.body).toBe(file);
  });
});

describe("EnvironmentsScreen reference files on a new environment", () => {
  it("keeps files picked before the first save and uploads them once it is saved", async () => {
    const uploaded: string[] = [];
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/environments" && init?.method === "POST") return respond({ name: "shop", base_url: "http://127.0.0.1:8201", secret_names: [] }, 201);
      if (url === "/api/environments") return respond({ environments: [] });
      if (url === "/api/environments/shop/files/rules.md" && init?.method === "PUT") {
        uploaded.push(url);
        return respond({ environment: "shop", files: [], max_files: 20, max_bytes: 26214400, model: "local" });
      }
      if (url === "/api/environments/shop/files") return respond({ environment: "shop", files: uploaded.length ? [{ name: "rules.md", kind: "text", size: 40, chars: 38, sha256: "a" }] : [], max_files: 20, max_bytes: 26214400, model: "local" });
      return respond({ error: "not found" }, 404);
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Add reference files"), { target: { files: [new File(["The team coupon is PANTRY10."], "rules.md", { type: "text/markdown" })] } });
    expect(screen.getByText(/added when you save the environment/)).toBeTruthy();
    expect(uploaded).toEqual([]);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "shop" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://127.0.0.1:8201" } });
    fireEvent.click(screen.getByRole("button", { name: /save environment/i }));

    await waitFor(() => expect(uploaded).toEqual(["/api/environments/shop/files/rules.md"]));
    expect(await screen.findByText(/38 characters read/)).toBeTruthy();
  });
});

describe("EnvironmentsScreen view and edit", () => {
  it("shows a saved environment read-only, edits it on request and deletes it after asking", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/environments") return respond({ environments: [{ name: "shop", base_url: "http://127.0.0.1:8201", viewport: "1280x800", secret_names: [], variables: { user: "ana", password: { secret: "SHOP_PASSWORD" } } }] });
      if (url.startsWith("/api/secrets?names=")) return respond({ secrets: [{ name: "SHOP_PASSWORD", source: "file" }], file: "/home/me/.hercules/secrets.env" });
      if (url === "/api/environments/shop/files") return respond({ environment: "shop", files: [{ name: "rules.md", kind: "text", size: 40, chars: 38, sha256: "a" }], max_files: 20, max_bytes: 26214400, model: "local" });
      if (url === "/api/environments/shop" && init?.method === "DELETE") return respond({ name: "shop", removed: true });
      return respond({ error: "not found" }, 404);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <EnvironmentsScreen />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "View shop" }));

    expect(screen.getByRole("heading", { name: "shop" })).toBeTruthy();
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(screen.getAllByText("http://127.0.0.1:8201").length).toBeGreaterThan(0);
    expect(screen.getByText("ana")).toBeTruthy();
    expect(await screen.findByText(/SHOP_PASSWORD: saved in Hercules/)).toBeTruthy();
    expect(await screen.findByText("rules.md")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit environment" }));
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("shop");
    fireEvent.click(screen.getByRole("button", { name: "Close editor" }));
    expect(screen.queryByLabelText("Name")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Delete environment" }));
    expect(confirm.mock.calls[0][0]).toMatch(/Delete the environment shop/);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, call]) => url === "/api/environments/shop" && (call as RequestInit | undefined)?.method === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.getByLabelText("Name")).toBeTruthy());
    confirm.mockRestore();
  });
});
