import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Broken({ fail }: { fail: boolean }) {
  if (fail) throw new Error("result.steps is undefined");
  return <p>All good</p>;
}

describe("ErrorBoundary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the error with a reload button instead of a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary>
        <Broken fail />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert").textContent).toMatch(/stopped working/i);
    expect(screen.getByText("result.steps is undefined")).toBeTruthy();
    expect(screen.getByRole("button", { name: /reload/i })).toBeTruthy();
  });

  it("recovers when the location changes", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(
      <ErrorBoundary resetKey="/runs/a">
        <Broken fail />
      </ErrorBoundary>,
    );
    rerender(
      <ErrorBoundary resetKey="/cases">
        <Broken fail={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("All good")).toBeTruthy();
  });
});
