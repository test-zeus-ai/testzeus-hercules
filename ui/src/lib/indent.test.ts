import { describe, expect, it } from "vitest";
import { indentEdit, moveLinesEdit } from "./indent";

const apply = (value: string, start: number, end: number, outdent = false) => {
  const edit = indentEdit(value, start, end, outdent);
  return edit ? { value: value.slice(0, edit.from) + edit.text + value.slice(edit.to), selection: [edit.selectionStart, edit.selectionEnd] } : null;
};

describe("indentEdit", () => {
  it("inserts two spaces at the caret", () => {
    expect(apply("Given x", 0, 0)).toEqual({ value: "  Given x", selection: [2, 2] });
  });

  it("indents and outdents every selected line", () => {
    const text = "Scenario: s\nGiven a\nWhen b\nThen c";
    expect(apply(text, 12, 26)?.value).toBe("Scenario: s\n  Given a\n  When b\nThen c");
    expect(apply("  Given a\n    When b\nThen c", 0, 20, true)?.value).toBe("Given a\n  When b\nThen c");
  });

  it("outdents the caret's line and keeps the caret on it", () => {
    expect(apply("    Given a", 6, 6, true)).toEqual({ value: "  Given a", selection: [4, 4] });
    expect(apply("Given a", 3, 3, true)).toBeNull();
  });

  it("leaves the line after a selection that ends with a line break alone", () => {
    expect(apply("Given a\nWhen b", 0, 8)?.value).toBe("  Given a\nWhen b");
  });
});

describe("moveLinesEdit", () => {
  const move = (value: string, start: number, end: number, direction: "up" | "down") => {
    const edit = moveLinesEdit(value, start, end, direction);
    return edit ? { value: value.slice(0, edit.from) + edit.text + value.slice(edit.to), selection: [edit.selectionStart, edit.selectionEnd] } : null;
  };
  const text = "Given a\nWhen b\nThen c";

  it("swaps the caret's line with the one above or below and keeps the caret on it", () => {
    expect(move(text, 10, 10, "up")).toEqual({ value: "When b\nGiven a\nThen c", selection: [2, 2] });
    expect(move(text, 10, 10, "down")).toEqual({ value: "Given a\nThen c\nWhen b", selection: [17, 17] });
  });

  it("moves every selected line together", () => {
    expect(move(text, 2, 10, "down")?.value).toBe("Then c\nGiven a\nWhen b");
    expect(move(text, 8, 21, "up")?.value).toBe("When b\nThen c\nGiven a");
  });

  it("does nothing at the first or last line", () => {
    expect(move(text, 1, 1, "up")).toBeNull();
    expect(move(text, 20, 20, "down")).toBeNull();
  });
});
