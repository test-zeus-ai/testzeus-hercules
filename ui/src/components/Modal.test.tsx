import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "./Modal";

function setup() {
  const onClose = vi.fn();
  render(
    <Modal title="Start a run" onClose={onClose}>
      <p>Pick a case</p>
    </Modal>,
  );
  const dialog = screen.getByText("Pick a case").closest("dialog") as HTMLDialogElement;
  return { onClose, dialog };
}

describe("Modal", () => {
  it("closes on a click that starts and ends on the backdrop", () => {
    const { onClose, dialog } = setup();
    fireEvent.pointerDown(dialog);
    fireEvent.click(dialog);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("stays open when a text selection drag ends on the backdrop", () => {
    const { onClose, dialog } = setup();
    fireEvent.pointerDown(screen.getByText("Pick a case"));
    fireEvent.click(dialog);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("stays open on clicks inside", () => {
    const { onClose } = setup();
    const text = screen.getByText("Pick a case");
    fireEvent.pointerDown(text);
    fireEvent.click(text);
    expect(onClose).not.toHaveBeenCalled();
  });
});
