import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./Settings";

const HEALTH = {
  status: "ok",
  version: "2.0.0.dev0",
  project_dir: "/Users/ada/testzeus-hercules",
  parallel_runs: 2,
  queue: {},
  pid: 4242,
  started_at: "2026-11-17T10:00:00+00:00",
  ui_url: "http://127.0.0.1:7373",
  user: "ada",
  host: "ada-mac",
  home_dir: "/Users/ada/.hercules",
  egress_policy: "hybrid",
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(HEALTH)) })),
  );
  const saved = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => void saved.set(key, value),
    removeItem: (key: string) => void saved.delete(key),
  });
  delete document.documentElement.dataset.theme;
});

afterEach(() => vi.unstubAllGlobals());

describe("SettingsScreen", () => {
  it("chooses the theme and shows who and where Hercules runs", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SettingsScreen />
      </QueryClientProvider>,
    );
    const themes = within(screen.getByRole("radiogroup", { name: "Theme" }));
    expect(themes.getByRole("radio", { name: /System/ }).getAttribute("aria-checked")).toBe("true");

    fireEvent.click(themes.getByRole("radio", { name: /Dark/ }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("hercules.theme")).toBe("dark");
    expect(themes.getByRole("radio", { name: /Dark/ }).getAttribute("aria-checked")).toBe("true");

    const fonts = within(screen.getByRole("radiogroup", { name: "Font" }));
    expect(fonts.getByRole("radio", { name: "Manrope" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(fonts.getByRole("radio", { name: "Atkinson Hyperlegible" }));
    expect(document.documentElement.dataset.font).toBe("atkinson");
    expect(window.localStorage.getItem("hercules.font")).toBe("atkinson");
    fireEvent.click(fonts.getByRole("radio", { name: "Manrope" }));
    expect(document.documentElement.dataset.font).toBeUndefined();
    expect(window.localStorage.getItem("hercules.font")).toBeNull();
    expect(await screen.findByText("ada")).toBeTruthy();
    const gherkin = screen.getByRole("link", { name: /Writing Gherkin/ });
    expect(gherkin.getAttribute("href")).toBe("https://github.com/test-zeus-ai/testzeus-hercules/blob/main/docs/guide/gherkin.md");
    expect(gherkin.getAttribute("target")).toBe("_blank");
    for (const value of ["ada-mac", "/Users/ada/testzeus-hercules", "/Users/ada/.hercules", "2.0.0.dev0", "http://127.0.0.1:7373", "4242", "hybrid"]) {
      expect(screen.getByText(value)).toBeTruthy();
    }
  });
});
