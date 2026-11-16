import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ApprovalRequest } from "../lib/timeline";
import { ApprovalCard } from "./ApprovalCard";

const RUN = "r_20261026_101500_abc123";

function request(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: "a1b2",
    kind: "destructive",
    step: 3,
    reason: 'about to click "Remove", which looks destructive ("remove")',
    action: 'click button "Remove"',
    target: "Remove",
    screenshot: `hercules://runs/${RUN}/steps/3/plain.jpg`,
    allowEdit: true,
    timeoutS: 600,
    ...overrides,
  };
}

describe("ApprovalCard", () => {
  it("shows the reason, the action and the screenshot", () => {
    render(<ApprovalCard runId={RUN} approval={request()} stepText='I click "Remove"' onDecide={() => undefined} />);
    expect(screen.getByRole("heading", { name: /destructive action/i })).toBeTruthy();
    expect(screen.getByText(/which looks destructive/)).toBeTruthy();
    expect(screen.getByText('click button "Remove"')).toBeTruthy();
    const image = screen.getByRole("img", { name: /step 3/i }) as HTMLImageElement;
    expect(image.src).toContain(`/api/runs/${RUN}/evidence/steps/3/plain.jpg`);
  });

  it("approves and rejects", () => {
    const onDecide = vi.fn();
    render(<ApprovalCard runId={RUN} approval={request()} stepText="x" onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: /^approve/i }));
    fireEvent.click(screen.getByRole("button", { name: /^reject/i }));
    expect(onDecide.mock.calls).toEqual([["approve"], ["reject"]]);
  });

  it("edits the step's text and sends it", () => {
    const onDecide = vi.fn();
    render(<ApprovalCard runId={RUN} approval={request()} stepText='I click "Remove"' onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: /edit step/i }));
    const box = screen.getByRole("textbox", { name: /new text for step 3/i }) as HTMLTextAreaElement;
    expect(box.value).toBe('I click "Remove"');
    fireEvent.change(box, { target: { value: 'I click "Keep"' } });
    fireEvent.click(screen.getByRole("button", { name: /run edited step/i }));
    expect(onDecide).toHaveBeenCalledWith("edit", 'I click "Keep"');
  });

  it("does not send an empty edit", () => {
    const onDecide = vi.fn();
    render(<ApprovalCard runId={RUN} approval={request()} stepText="" onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: /edit step/i }));
    const send = screen.getByRole("button", { name: /run edited step/i }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
  });

  it("offers no edit for a draft and shows no screenshot when there is none", () => {
    render(<ApprovalCard runId={RUN} approval={request({ kind: "draft", step: 0, allowEdit: false, screenshot: null, reason: "the case is a draft" })} stepText="" onDecide={() => undefined} />);
    expect(screen.queryByRole("button", { name: /edit step/i })).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("heading", { name: /drafted case/i })).toBeTruthy();
  });

  it("disables the buttons while an answer is on its way and shows errors", () => {
    render(<ApprovalCard runId={RUN} approval={request()} stepText="" onDecide={() => undefined} busy error="run changed state" />);
    expect((screen.getByRole("button", { name: /^approve/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("run changed state");
  });

  it("keeps a half-typed edit when the step text refreshes, and starts over for a new question", () => {
    const props = { runId: RUN, onDecide: () => undefined };
    const { rerender } = render(<ApprovalCard {...props} approval={request()} stepText='I click "Remove"' />);
    fireEvent.click(screen.getByRole("button", { name: /edit step/i }));
    const box = () => screen.getByRole("textbox", { name: /new text/i }) as HTMLTextAreaElement;
    fireEvent.change(box(), { target: { value: 'I click "Keep"' } });
    rerender(<ApprovalCard {...props} approval={request()} stepText='I click "Remove" (2)' />);
    expect(box().value).toBe('I click "Keep"');
    rerender(<ApprovalCard {...props} approval={request({ id: "c3d4", step: 4 })} stepText="I submit" />);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /edit step/i }));
    expect(box().value).toBe("I submit");
  });

  it("announces a new question to screen readers", () => {
    render(<ApprovalCard runId={RUN} approval={request()} stepText="x" onDecide={() => undefined} />);
    const region = document.querySelector("[aria-live='assertive']");
    expect(region?.textContent).toMatch(/waiting for your decision at step 3/i);
  });
});
