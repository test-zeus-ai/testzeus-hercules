import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExportView } from "../lib/types";
import { ExportDialog, downloadName } from "./ExportDialog";

const RUN = "r_20261026_101500_abc123";

function exported(format: ExportView["format"]): ExportView {
  const code = format === "appium-py" ? "def test_sign_in(driver: WebDriver) -> None:\n    tap(driver, find(driver, (AppiumBy.ACCESSIBILITY_ID, \"Sign in\")))\n" : format === "playwright-py" ? "def test_sign_in(page: Page) -> None:\n    page.goto(url(\"/login\"))\n" : 'test("Sign in", async ({ page }) => {\n  await page.goto(url("/login"));\n});\n';
  return {
    format,
    run_id: RUN,
    case_id: "sign-in",
    run_status: "passed",
    filename: format === "playwright-py" || format === "appium-py" ? "tests/test_sign_in.py" : "tests/sign-in.spec.ts",
    code,
    spec: {},
    env: [{ name: "APP_PASSWORD", required: true, purpose: "secret {{password}}" }],
    notes: ["step 3: judged by its meaning by a model; write an assertion for it in code"],
    steps_exported: 3,
    steps_total: 3,
    partial: false,
    guidance: "Adapt it to this repository's test conventions.",
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

function respond(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)) });
}

beforeEach(() => {
  fetchMock = vi.fn((url: string) => {
    const format = new URL(url, "http://localhost").searchParams.get("format") as ExportView["format"];
    if (url.startsWith(`/api/runs/${RUN}/export`)) return respond(exported(format));
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDialog(onClose = () => undefined, native = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExportDialog runId={RUN} native={native} onClose={onClose} />
    </QueryClientProvider>,
  );
}

describe("ExportDialog", () => {
  it("shows the TypeScript export with its file, environment and review notes", async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText("tests/sign-in.spec.ts")).toBeTruthy());
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/runs/${RUN}/export?format=playwright-ts`);
    expect(screen.getByText(/await page\.goto/)).toBeTruthy();
    expect(screen.getByText("APP_PASSWORD")).toBeTruthy();
    expect(screen.getByText(/judged by its meaning/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /copy/i })).toBeTruthy();
  });

  it("switches to the Python export", async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText("tests/sign-in.spec.ts")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /pytest/i }));
    await waitFor(() => expect(screen.getByText("tests/test_sign_in.py")).toBeTruthy());
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(`/api/runs/${RUN}/export?format=playwright-py`);
    expect(screen.getByText(/def test_sign_in/)).toBeTruthy();
  });

  it("offers Appium code, not Playwright, for a native app run", async () => {
    renderDialog(undefined, true);
    await waitFor(() => expect(screen.getByText(/AppiumBy\.ACCESSIBILITY_ID/)).toBeTruthy());
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/runs/${RUN}/export?format=appium-py`);
    expect(screen.getByRole("button", { name: "Appium (pytest)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Automation spec/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Playwright/ })).toBeNull();
  });

  it("shows why a run cannot be exported", async () => {
    fetchMock.mockImplementation(() => respond({ error: "run r_1 did not pass (failed at step 2): only passed runs are exported" }, 409));
    renderDialog();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/only passed runs are exported/));
  });

  it("names the download after the suggested file", () => {
    expect(downloadName("tests/e2e/sign-in.spec.ts")).toBe("sign-in.spec.ts");
    expect(downloadName("")).toBe("hercules-export.txt");
  });
});
