import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { emptyForm, type CaseFormValue } from "../lib/caseForm";
import { CaseForm } from "./CaseForm";
import { GherkinEditor } from "./GherkinEditor";
import { matchingSuggestions, placeholderSuggestions, type Suggestion } from "./PlaceholderSuggest";

const ENVIRONMENTS: Suggestion[] = [
  { name: "salesforce", detail: "https://acme.example" },
  { name: "secret-test", detail: "http://127.0.0.1:8201/" },
];

const SHOP = { name: "shop", secret_names: [], variables: { user: "ana", password: { secret: "SHOP_PASSWORD" } } };

function Form({ suggestions }: { suggestions: Suggestion[] }) {
  const [value, setValue] = useState<CaseFormValue>({ ...emptyForm(), id: "x", title: "T" });
  return <CaseForm value={value} errors={{}} onChange={setValue} suggestions={suggestions} />;
}

function Editor({ suggestions, initial, environments = [] }: { suggestions: Suggestion[]; initial: string; environments?: Suggestion[] }) {
  const [value, setValue] = useState(initial);
  return <GherkinEditor value={value} onChange={setValue} suggestions={suggestions} environments={environments} />;
}

describe("placeholder suggestions", () => {
  it("offers test data first, then the environment's variables and secrets, then built-ins", () => {
    const found = placeholderSuggestions(SHOP, [{ key: "user", value: "bob", secret: false }, { key: "coupon", value: "PANTRY10", secret: false }]);
    expect(found.map((item) => item.name)).toEqual(["user", "coupon", "password", "run_id", "timestamp", "downloaded_file", "downloaded_file_name"]);
    expect(found.find((item) => item.name === "downloaded_file")?.detail).toBe("after a download step");
    expect(found.find((item) => item.name === "password")?.detail).toBe("shop · secret SHOP_PASSWORD");
    expect(matchingSuggestions(found, "ord").map((item) => item.name)).toEqual(["password"]);
    expect(matchingSuggestions(found, "").length).toBe(7);
  });

  it("suggests names after {{ in a step and inserts the chosen one", () => {
    render(<Form suggestions={placeholderSuggestions(SHOP, [])} />);
    const action = screen.getByLabelText("Step 1 action") as HTMLInputElement;
    fireEvent.change(action, { target: { value: 'Fill "{{pa' } });
    expect(screen.getByRole("listbox", { name: "Placeholders" }).textContent).toContain("{{password}}");
    expect(action.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(action, { key: "Enter" });
    expect(action.value).toBe('Fill "{{password}}');
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("moves through the list with the arrow keys and closes on Escape", () => {
    render(<Form suggestions={placeholderSuggestions(SHOP, [])} />);
    const expected = screen.getByLabelText("Step 1 expected outcome") as HTMLInputElement;
    fireEvent.change(expected, { target: { value: "I see {{" } });
    fireEvent.keyDown(expected, { key: "ArrowDown" });
    expect(within(screen.getByRole("listbox", { name: "Placeholders" })).getAllByRole("option")[1].getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(expected, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("suggests in the Gherkin editor and keeps a closing }} that is already there", () => {
    render(<Editor suggestions={placeholderSuggestions(SHOP, [])} initial="" />);
    const editor = screen.getByLabelText("Gherkin") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: 'Feature: x\n  Scenario: y\n    When I fill "{{u' } });
    fireEvent.mouseDown(screen.getByRole("option", { name: /\{\{user\}\}/ }));
    expect(editor.value).toBe('Feature: x\n  Scenario: y\n    When I fill "{{user}}');
  });

  it("lists saved environments after @env: on a tag line of the Gherkin", () => {
    render(<Editor suggestions={[]} environments={ENVIRONMENTS} initial="" />);
    const editor = screen.getByLabelText("Gherkin") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "@smoke @env:s" } });
    const list = screen.getByRole("listbox", { name: "Environments" });
    expect(within(list).getAllByRole("option").map((option) => option.textContent)).toEqual(["@env:salesforcehttps://acme.example", "@env:secret-testhttp://127.0.0.1:8201/"]);
    fireEvent.keyDown(editor, { key: "ArrowDown" });
    fireEvent.keyDown(editor, { key: "Enter" });
    expect(editor.value).toBe("@smoke @env:secret-test");

    fireEvent.change(editor, { target: { value: "@env:secret-test\nFeature: x\n  Scenario: y\n    Given I open @env:" } });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does not offer environments in the form's steps", () => {
    render(<Form suggestions={[]} />);
    fireEvent.change(screen.getByLabelText("Step 1 action"), { target: { value: "@env:" } });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("indents with Tab in the Gherkin editor, picks a suggestion with Tab when one is open, and leaves after Esc", () => {
    render(<Editor suggestions={placeholderSuggestions(SHOP, [])} initial={"Feature: x\nScenario: y"} />);
    const editor = screen.getByLabelText("Gherkin") as HTMLTextAreaElement;
    editor.setSelectionRange(11, 11);
    expect(fireEvent.keyDown(editor, { key: "Tab" })).toBe(false);
    expect(editor.value).toBe("Feature: x\n  Scenario: y");
    expect(editor.selectionStart).toBe(13);

    fireEvent.keyDown(editor, { key: "Tab", shiftKey: true });
    expect(editor.value).toBe("Feature: x\nScenario: y");

    fireEvent.change(editor, { target: { value: "Feature: x\nScenario: y\n  When I fill \"{{us" } });
    fireEvent.keyDown(editor, { key: "Tab" });
    expect(editor.value.endsWith('"{{user}}')).toBe(true);

    fireEvent.keyDown(editor, { key: "Escape" });
    expect(fireEvent.keyDown(editor, { key: "Tab" })).toBe(true);
  });

  it("moves lines with Alt+Up and Alt+Down in the Gherkin editor", () => {
    render(<Editor suggestions={[]} initial={"Given a\nWhen b\nThen c"} />);
    const editor = screen.getByLabelText("Gherkin") as HTMLTextAreaElement;
    editor.setSelectionRange(10, 10);
    expect(fireEvent.keyDown(editor, { key: "ArrowUp", altKey: true })).toBe(false);
    expect(editor.value).toBe("When b\nGiven a\nThen c");
    expect(editor.selectionStart).toBe(2);
    fireEvent.keyDown(editor, { key: "ArrowDown", altKey: true });
    fireEvent.keyDown(editor, { key: "ArrowDown", altKey: true });
    expect(editor.value).toBe("Given a\nThen c\nWhen b");
  });

  it("colours placeholders in the Gherkin editor by where their value comes from", () => {
    const { container } = render(<Editor suggestions={placeholderSuggestions(SHOP, [{ key: "coupon", value: "X", secret: false }])} initial={'@env:shop\nWhen I fill "{{user}}" "{{coupon}}" "{{nope}}"'} />);
    const coloured = Array.from(container.querySelectorAll(".code-highlight .tok:not(.tok-keyword)")).map((node) => [node.textContent, node.className]);
    expect(container.querySelector(".code-highlight .tok-keyword")?.textContent).toBe("When");
    expect(coloured).toEqual([
      ["@env:shop", "tok tok-input"],
      ["{{user}}", "tok tok-env"],
      ["{{coupon}}", "tok tok-data"],
      ["{{nope}}", "tok tok-unknown"],
    ]);
  });

  it("undoes and redoes with Cmd/Ctrl+Z and Cmd/Ctrl+Shift+Z or Ctrl+Y, one step per typing burst or edit", () => {
    render(<Editor suggestions={placeholderSuggestions(SHOP, [])} initial="Given a" />);
    const editor = screen.getByLabelText("Gherkin") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "Given ab" } });
    fireEvent.change(editor, { target: { value: "Given abc" } });
    editor.setSelectionRange(0, 0);
    fireEvent.keyDown(editor, { key: "Tab" });
    expect(editor.value).toBe("  Given abc");
    fireEvent.change(editor, { target: { value: '  Given abc "{{us' } });
    fireEvent.keyDown(editor, { key: "Enter" });
    expect(editor.value).toBe('  Given abc "{{user}}');

    expect(fireEvent.keyDown(editor, { key: "z", metaKey: true })).toBe(false);
    expect(editor.value).toBe('  Given abc "{{us');
    fireEvent.keyDown(editor, { key: "z", ctrlKey: true });
    expect(editor.value).toBe("  Given abc");
    fireEvent.keyDown(editor, { key: "z", ctrlKey: true });
    expect(editor.value).toBe("Given abc");
    fireEvent.keyDown(editor, { key: "z", ctrlKey: true });
    expect(editor.value).toBe("Given a");
    fireEvent.keyDown(editor, { key: "z", ctrlKey: true });
    expect(editor.value).toBe("Given a");

    fireEvent.keyDown(editor, { key: "Z", metaKey: true, shiftKey: true });
    expect(editor.value).toBe("Given abc");
    fireEvent.keyDown(editor, { key: "y", ctrlKey: true });
    expect(editor.value).toBe("  Given abc");

    fireEvent.change(editor, { target: { value: "  Given abc!" } });
    fireEvent.keyDown(editor, { key: "y", ctrlKey: true });
    expect(editor.value).toBe("  Given abc!");
  });
});
