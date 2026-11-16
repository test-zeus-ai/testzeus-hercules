import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DebugToolbar } from "./DebugToolbar";

describe("DebugToolbar", () => {
  it("offers pause and step while running, with the keyboard shortcuts", () => {
    const onAction = vi.fn();
    render(<DebugToolbar status="running" onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(onAction).toHaveBeenCalledWith("pause");
    expect((screen.getByRole("button", { name: /skip/i }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(window, { key: " " });
    fireEvent.keyDown(window, { key: "n" });
    expect(onAction.mock.calls.map((call) => call[0])).toEqual(["pause", "pause", "step"]);
  });

  it("shows the paused state and offers resume, skip and retry", () => {
    const onAction = vi.fn();
    render(<DebugToolbar status="paused" pausedAt={{ step: 2, where: "step", action: "Fill quantity" }} onAction={onAction} />);
    expect(screen.getByRole("status").textContent).toMatch(/paused before step 2/i);
    fireEvent.click(screen.getByRole("button", { name: /resume/i }));
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onAction.mock.calls.map((call) => call[0])).toEqual(["resume", "skip", "retry"]);
  });

  it("ignores shortcuts while typing and when the run is over", () => {
    const onAction = vi.fn();
    const { rerender } = render(
      <>
        <input aria-label="notes" />
        <DebugToolbar status="running" onAction={onAction} />
      </>,
    );
    fireEvent.keyDown(screen.getByRole("textbox", { name: "notes" }), { key: " " });
    expect(onAction).not.toHaveBeenCalled();
    rerender(<DebugToolbar status="passed" onAction={onAction} />);
    fireEvent.keyDown(window, { key: " " });
    expect(onAction).not.toHaveBeenCalled();
  });

  it("leaves Space to a focused button or checkbox instead of pausing", () => {
    const onAction = vi.fn();
    render(
      <main>
        <button type="button">Expand step</button>
        <input type="checkbox" aria-label="follow" />
        <DebugToolbar status="running" onAction={onAction} />
      </main>,
    );
    const button = screen.getByRole("button", { name: "Expand step" });
    const pressed = fireEvent.keyDown(button, { key: " " });
    fireEvent.keyDown(screen.getByRole("checkbox", { name: "follow" }), { key: " " });
    fireEvent.keyDown(screen.getByRole("button", { name: /pause/i }), { key: "n" });
    expect(onAction).not.toHaveBeenCalled();
    expect(pressed).toBe(true); // not prevented: the button still gets its Space
  });

  it("does nothing while a dialog is open", () => {
    const onAction = vi.fn();
    render(
      <main>
        <dialog open>
          <p>Start a run</p>
        </dialog>
        <DebugToolbar status="paused" onAction={onAction} />
      </main>,
    );
    fireEvent.keyDown(document.body, { key: " " });
    expect(onAction).not.toHaveBeenCalled();
  });
});
