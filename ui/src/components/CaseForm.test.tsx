import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { emptyForm, validateCaseForm, type CaseFormValue } from "../lib/caseForm";
import { CaseForm } from "./CaseForm";

function Harness({ initial }: { initial: CaseFormValue }) {
  const [value, setValue] = useState(initial);
  return <CaseForm value={value} errors={validateCaseForm(value)} onChange={setValue} />;
}

describe("CaseForm", () => {
  it("shows field errors and clears them when the value becomes valid", () => {
    render(<Harness initial={{ ...emptyForm(), id: "Bad Id", title: "T", steps: [{ action: "Go", expected: "" }] }} />);
    const id = screen.getByLabelText("Case id");
    expect(id.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText(/Use lowercase letters/)).toBeTruthy();
    fireEvent.change(id, { target: { value: "good-id" } });
    expect(screen.getByLabelText("Case id").getAttribute("aria-invalid")).toBeNull();
    expect(screen.queryByText(/Use lowercase letters/)).toBeNull();
  });

  it("marks the case id, title and steps as required", () => {
    render(<Harness initial={{ ...emptyForm(), id: "x", title: "T", steps: [{ action: "Go", expected: "" }] }} />);
    expect(screen.getByLabelText("Case id").getAttribute("aria-required")).toBe("true");
    expect(screen.getByLabelText("Title").getAttribute("aria-required")).toBe("true");
    expect(screen.getByText("Steps").classList.contains("is-required")).toBe(true);
    expect(screen.getByLabelText("Tags").getAttribute("aria-required")).toBeNull();
  });

  it("adds, edits and removes steps", () => {
    render(<Harness initial={{ ...emptyForm(), id: "x", title: "T", steps: [{ action: "First", expected: "" }] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Add step" }));
    expect(screen.getByText("Describe the action.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Step 2 action"), { target: { value: "Second" } });
    expect(screen.queryByText("Describe the action.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Move step 2 up" }));
    expect((screen.getByLabelText("Step 1 action") as HTMLInputElement).value).toBe("Second");
    fireEvent.click(screen.getByRole("button", { name: "Remove step 1" }));
    expect((screen.getByLabelText("Step 1 action") as HTMLInputElement).value).toBe("First");
    expect(screen.getByRole("button", { name: "Remove step 1" }).hasAttribute("disabled")).toBe(true);
  });

  it("flags a secret that is not an environment variable name", () => {
    render(<Harness initial={{ ...emptyForm(), id: "x", title: "T", steps: [{ action: "Go", expected: "" }], testData: [{ key: "pw", value: "hunter", secret: true }] }} />);
    expect(screen.getByRole("alert").textContent).toMatch(/Secrets name an environment variable, e.g. SHOP_PASSWORD/);
  });
});
