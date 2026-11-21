import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { withUtm } from "../lib/links";
import { ProPromo } from "./ProPromo";

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
    clear: () => values.clear(),
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("ProPromo", () => {
  it("links to the TestZeus free trial and pricing, in new tabs, with the Hercules campaign", () => {
    render(<ProPromo />);
    const trial = screen.getByRole("link", { name: /start free trial/i });
    const plans = screen.getByRole("link", { name: /plans and pricing/i });
    expect(trial.getAttribute("href")).toBe("https://prod.testzeus.app/sign-up?utm_source=hercules&utm_medium=tester-ui&utm_campaign=try-pro");
    expect(plans.getAttribute("href")).toContain("https://testzeus.com/pricing?utm_source=hercules");
    for (const link of [trial, plans]) {
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    }
  });

  it("can be hidden, then stays a single link to the trial", () => {
    const { unmount } = render(<ProPromo />);
    fireEvent.click(screen.getByRole("button", { name: /hide the testzeus pro card/i }));
    expect(screen.queryByText(/go pro with testzeus/i)).toBeNull();
    const link = screen.getByRole("link", { name: /try testzeus pro/i });
    expect(link.getAttribute("href")).toContain("prod.testzeus.app/sign-up");
    expect(link.classList.contains("pro-link-narrow")).toBe(false);
    unmount();
    render(<ProPromo />);
    expect(screen.queryByText(/go pro with testzeus/i)).toBeNull();
  });

  it("adds only the UTM parameters to a link", () => {
    expect(withUtm("https://testzeus.com/?a=1", "x")).toBe("https://testzeus.com/?a=1&utm_source=hercules&utm_medium=tester-ui&utm_campaign=x");
  });
});
