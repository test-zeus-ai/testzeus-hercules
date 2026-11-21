import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CloudMark } from "./CloudCard";

describe("CloudMark", () => {
  it("shows each supported cloud's logo, kept out of the accessibility tree", () => {
    for (const [cloud, name, element] of [
      ["browserstack", "BrowserStack", "svg"],
      ["testmu", "TestMu AI", "svg"],
      ["saucelabs", "Sauce Labs", "img"],
    ] as const) {
      const { container, unmount } = render(<CloudMark cloud={cloud} name={name} />);
      const mark = container.querySelector(".cloud-mark");
      expect(mark?.classList.contains("cloud-mark-logo")).toBe(true);
      expect(mark?.getAttribute("aria-hidden")).toBe("true");
      expect(mark?.querySelector(element)).toBeTruthy();
      expect(mark?.textContent).toBe("");
      unmount();
    }
  });

  it("falls back to the monogram for a cloud without a logo", () => {
    const { container } = render(<CloudMark cloud="other" name="Example Grid" />);
    expect(container.querySelector(".cloud-mark")?.textContent).toBe("EG");
  });
});
