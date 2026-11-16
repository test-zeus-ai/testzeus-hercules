import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BrandLockup, Bugsy, Monogram, Wordmark } from "./Brand";

describe("Brand", () => {
  it("names the wordmark and the monogram for assistive technology", () => {
    render(
      <>
        <Wordmark />
        <Monogram alt="TestZeus monogram" />
      </>,
    );
    expect(screen.getByRole("img", { name: "TestZeus" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "TestZeus monogram" })).toBeTruthy();
  });

  it("ships the monogram in both theme versions, naming it once", () => {
    const { container } = render(<Monogram />);
    const images = container.querySelectorAll("img");
    expect(images).toHaveLength(2);
    expect([...images].map((image) => image.getAttribute("alt"))).toEqual(["TestZeus", ""]);
  });

  it("keeps a decorative wordmark out of the accessibility tree", () => {
    const { container } = render(<Wordmark aria-hidden="true" />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBeNull();
  });

  it("renders the lockup as visuals only, so the surrounding link names it", () => {
    const { container } = render(<BrandLockup />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(container.textContent).toContain("Hercules");
    expect(container.textContent).toContain("QA engine by");
  });

  it("shows Bugsy as a decorative image", () => {
    const { container } = render(<Bugsy />);
    expect(container.querySelector("img")?.getAttribute("alt")).toBe("");
  });
});
