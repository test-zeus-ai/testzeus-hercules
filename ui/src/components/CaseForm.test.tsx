import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { emptyForm, validateCaseForm, type CaseFormValue } from "../lib/caseForm";
import { CaseForm } from "./CaseForm";

function Harness({ initial, environments }: { initial: CaseFormValue; environments?: string[] }) {
  const [value, setValue] = useState(initial);
  return <CaseForm value={value} errors={validateCaseForm(value)} onChange={setValue} environments={environments} />;
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

describe("CaseForm with an environment", () => {
  it("knows the names its environment defines", () => {
    const value = { ...emptyForm(), id: "x", title: "T", environmentRef: "shop", steps: [{ action: 'Fill "{{user}}" in "Name"', expected: "" }] };
    expect(validateCaseForm(value)["steps.0.placeholders"]).toMatch(/environment's variables/);
    expect(validateCaseForm(value, ["user"])["steps.0.placeholders"]).toBeUndefined();
  });

  it("picks the environment from the saved ones and keeps an unsaved name", () => {
    render(<Harness initial={{ ...emptyForm(), environmentRef: "old-env" }} environments={["shop", "staging"]} />);
    const select = screen.getByLabelText("Environment") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual(["None", "shop", "staging", "old-env (not saved)"]);
    fireEvent.change(select, { target: { value: "shop" } });
    expect((screen.getByLabelText("Environment") as HTMLSelectElement).value).toBe("shop");
    expect(screen.queryByText("old-env (not saved)")).toBeNull();
  });

  it("hints to quote a bare placeholder in a step", () => {
    render(<Harness initial={{ ...emptyForm(), id: "x", title: "T", testData: [{ key: "name", value: "ana", secret: false }], steps: [{ action: 'Fill {{name}} in "Name"', expected: "" }] }} />);
    expect(screen.getByText('Put placeholder values in quotes: {{name}} → "{{name}}"')).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Step 1 action"), { target: { value: 'Fill "{{name}}" in "Name"' } });
    expect(screen.queryByText(/Put placeholder values in quotes/)).toBeNull();
  });
});
