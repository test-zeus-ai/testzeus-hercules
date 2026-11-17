import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn((..._args: unknown[]) => Promise.resolve({}));
vi.mock("../lib/api", () => ({ post: (...args: unknown[]) => post(...args), evidenceUrl: () => "" }));
vi.mock("../lib/screencast", () => ({ useScreencast: () => ({ state: "live", frames: 3, fps: 4, size: { width: 393, height: 659 } }) }));

import { fitWidth, LiveView } from "./LiveView";

function drive() {
  render(<LiveView runId="r_1" active paused fallbackRef={null} />);
  fireEvent.click(screen.getByRole("button", { name: /take over/i }));
  return screen.getByLabelText("Live browser view: keys you press here go to the page");
}

describe("LiveView take-over", () => {
  beforeEach(() => post.mockClear());

  it("types characters and presses keys pressed on the live view", () => {
    const frame = drive();
    fireEvent.keyDown(frame, { key: "3" });
    fireEvent.keyDown(frame, { key: "Backspace" });
    fireEvent.keyDown(frame, { key: "ArrowDown" });
    expect(post.mock.calls.map((call) => call[1])).toEqual([
      { kind: "type", text: "3" },
      { kind: "key", key: "Backspace" },
      { kind: "key", key: "ArrowDown" },
    ]);
  });

  it("leaves shortcuts and keys it cannot press to the tester UI", () => {
    const frame = drive();
    fireEvent.keyDown(frame, { key: "a", metaKey: true });
    fireEvent.keyDown(frame, { key: "F5" });
    expect(post).not.toHaveBeenCalled();
  });

  it("shows a phone's page at the phone's width", () => {
    vi.stubGlobal("innerHeight", 1000);
    render(<LiveView runId="r_1" active paused={false} fallbackRef={null} />);
    expect(screen.getByRole("img", { name: "Live browser view" }).style.maxWidth).toBe("393px");
    vi.unstubAllGlobals();
  });

  it("keeps a tall device screen within the window's height", () => {
    expect(fitWidth({ width: 1080, height: 2400 }, 800)).toBe(270);
    expect(fitWidth({ width: 393, height: 659 }, 1000)).toBe(393);
    expect(fitWidth({ width: 1280, height: 0 }, 800)).toBe(1280);
  });
});
