import { describe, expect, it } from "vitest";
import { caseCount, caseLabel, casesIn, childFolders, folderMoveError, folderNameError, folderOptions, newFolderError, pathUrl, relocated, searchTree } from "./caseTree";
import type { CaseEntry } from "./types";

const entry = (name: string, title = name): CaseEntry => ({
  name,
  folder: name.includes("/") ? name.slice(0, name.lastIndexOf("/")) : "",
  path: `.hercules/cases/${name}`,
  kind: name.endsWith(".feature") ? "feature" : "json",
  valid: true,
  title,
});

const FOLDERS = ["API", "Checkout", "Checkout/Payments", "Help center"];
const CASES = [entry("login.json", "Sign in"), entry("Checkout/place-an-order.feature"), entry("Checkout/Payments/card-declined.feature", "A declined card"), entry("API/health.json")];

describe("case tree", () => {
  it("escapes each name of a path but keeps its slashes", () => {
    expect(pathUrl("Help center/a b.feature")).toBe("Help%20center/a%20b.feature");
    expect(pathUrl("login.json")).toBe("login.json");
  });

  it("checks folder names as the daemon does", () => {
    expect(folderNameError("  ")).toBe("Enter a name.");
    expect(folderNameError("a/b")).toMatch(/can’t contain/);
    expect(folderNameError(".git")).toMatch(/start with a dot/);
    expect(folderNameError("notes.")).toMatch(/end with a dot/);
    expect(folderNameError("COM1")).toMatch(/reserved/);
    expect(folderNameError("Help center")).toBeNull();
    expect(newFolderError("payments", "Checkout", FOLDERS)).toBe("“Payments” is already in Checkout.");
    expect(newFolderError("Payments", "Checkout", FOLDERS, "Checkout/Payments")).toBeNull();
    expect(newFolderError("x", "a/b/c/d/e/f/g/h", FOLDERS)).toMatch(/8 levels/);
  });

  it("says why a folder cannot move somewhere", () => {
    expect(folderMoveError("Checkout", "Checkout", FOLDERS)).toBe("This folder");
    expect(folderMoveError("Checkout", "Checkout/Payments", FOLDERS)).toBe("Inside this folder");
    expect(folderMoveError("Checkout/Payments", "Checkout", FOLDERS)).toBe("It is here now");
    expect(folderMoveError("Checkout/Payments", "API", FOLDERS)).toBeNull();
    expect(folderMoveError("Checkout", "a/b/c/d/e/f/g", [...FOLDERS, "a/b/c/d/e/f/g"])).toBe("Too deep");
    expect(folderMoveError("API", "Checkout", [...FOLDERS, "Checkout/api"])).toBe("Has a folder named API");
  });

  it("lists folders and cases in order with counts that include subfolders", () => {
    expect(childFolders(FOLDERS, "", "asc")).toEqual(["API", "Checkout", "Help center"]);
    expect(childFolders(FOLDERS, "", "desc")).toEqual(["Help center", "Checkout", "API"]);
    expect(casesIn(CASES, "", "asc").map((item) => item.name)).toEqual(["login.json"]);
    expect(caseCount(CASES, "Checkout")).toBe(2);
    expect(caseCount(CASES, "Help center")).toBe(0);
    expect(folderOptions(FOLDERS).map((option) => [option.path, option.depth])).toEqual([
      ["API", 1],
      ["Checkout", 1],
      ["Checkout/Payments", 2],
      ["Help center", 1],
    ]);
  });

  it("names a case by its title, else its file name, and sorts by that", () => {
    const untitled: CaseEntry = { ...entry("zz-first.json"), title: undefined };
    expect(caseLabel(entry("Checkout/place-an-order.feature", "Place an order"))).toBe("Place an order");
    expect(caseLabel(untitled)).toBe("zz-first.json");
    expect(casesIn([entry("b.json", "Alpha"), entry("a.json", "Zulu"), untitled], "", "asc").map(caseLabel)).toEqual(["Alpha", "Zulu", "zz-first.json"]);
  });

  it("finds folders by name and cases by file name or title", () => {
    const hits = searchTree(CASES, FOLDERS, "pay", "asc");
    expect(hits.folders).toEqual(["Checkout/Payments"]);
    expect(searchTree(CASES, FOLDERS, "declined", "asc").cases.map((item) => item.name)).toEqual(["Checkout/Payments/card-declined.feature"]);
    expect(searchTree(CASES, FOLDERS, "sign", "asc").cases.map((item) => item.name)).toEqual(["login.json"]);
    expect(searchTree(CASES, FOLDERS, " ", "asc")).toEqual({ folders: [], cases: [] });
  });

  it("follows a case when its folder is renamed, moved or deleted", () => {
    expect(relocated("Checkout/Payments/x.feature", "Checkout/Payments", "Checkout/Cards")).toBe("Checkout/Cards/x.feature");
    expect(relocated("Checkout/Payments/x.feature", "Checkout/Payments", "Checkout")).toBe("Checkout/x.feature");
    expect(relocated("Checkout/x.feature", "Checkout", "")).toBe("x.feature");
    expect(relocated("Checkout/x.feature", "Checkout", null)).toBeNull();
    expect(relocated("Checkout2/x.feature", "Checkout", "Other")).toBe("Checkout2/x.feature");
    expect(relocated("a/x.json", "a/x.json", "b/x.json")).toBe("b/x.json");
  });
});
