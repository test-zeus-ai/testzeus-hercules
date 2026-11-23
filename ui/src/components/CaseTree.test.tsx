import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import type { CaseEntry } from "../lib/types";
import { CaseTree, type CaseTreeProps } from "./CaseTree";

const entry = (name: string, extra: Partial<CaseEntry> = {}): CaseEntry => ({
  name,
  folder: name.includes("/") ? name.slice(0, name.lastIndexOf("/")) : "",
  path: `.hercules/cases/${name}`,
  kind: name.endsWith(".feature") ? "feature" : "json",
  valid: true,
  title: `Title of ${name}`,
  ...extra,
});

const FOLDERS = ["API", "Checkout", "Checkout/Payments"];
const CASES = [
  entry("login.json"),
  entry("Checkout/place-an-order.feature"),
  entry("Checkout/guest.feature", { status: "draft" }),
  entry("Checkout/Payments/card-declined.feature"),
  entry("API/health.json"),
];

let fetchMock: ReturnType<typeof vi.fn>;

function respond(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)) });
}

beforeEach(() => {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/folders" && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { parent: string; name: string };
      return respond({ folder: body.parent ? `${body.parent}/${body.name}` : body.name }, 201);
    }
    if (url.startsWith("/api/folders/") && init?.method === "PATCH") return respond({ folder: "Checkout/Cards", was: "Checkout/Payments" });
    if (url.startsWith("/api/folders/") && init?.method === "DELETE") return respond({ folder: "Checkout", mode: "move_up", moved: 3, deleted: 0 });
    if (url === "/api/cases/move") return respond({ folder: "API", names: ["API/login.json"] });
    return respond({ error: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderTree(props: Partial<CaseTreeProps> = {}) {
  const handlers = { onCollapse: vi.fn(), onOpen: vi.fn(), onNewCase: vi.fn(), onRunCase: vi.fn(), onRunFolder: vi.fn(), onRelocate: vi.fn() };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const location = memoryLocation({ path: "/cases" });
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <CaseTree cases={CASES} folders={FOLDERS} selected={null} collapsed={false} {...handlers} {...props} />
      </Router>
    </QueryClientProvider>,
  );
  return handlers;
}

const calls = (method: string) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === method);

describe("CaseTree", () => {
  it("lists folders before cases, with counts that include subfolders, and opens folders on click", () => {
    renderTree();
    const tree = screen.getByRole("navigation", { name: "Saved cases" });
    const rows = within(tree)
      .getAllByRole("button", { name: /^(All cases|API|Checkout)/ })
      .map((row) => row.textContent);
    expect(rows).toEqual(["All cases5", "API1 case", "Checkout3 cases"]);
    expect(screen.getByRole("link", { name: /login\.json/ }).getAttribute("href")).toBe("/cases/login.json");
    const inside = screen.getByRole("link", { name: /place-an-order\.feature/ }).closest(".tree-collapse") as HTMLElement;
    expect(inside.hasAttribute("inert")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /^Checkout/ }));
    expect(screen.getByRole("button", { name: /^Checkout/ }).getAttribute("aria-expanded")).toBe("true");
    expect(inside.hasAttribute("inert")).toBe(false);
    expect(inside.classList.contains("tree-collapse-closed")).toBe(false);
    expect(screen.getByLabelText("draft")).toBeTruthy();
  });

  it("opens the folders above the selected case and marks it current", () => {
    renderTree({ selected: "Checkout/Payments/card-declined.feature" });
    const link = screen.getByRole("link", { name: /card-declined\.feature/ });
    expect(link.getAttribute("aria-current")).toBe("page");
    expect(link.getAttribute("href")).toBe("/cases/Checkout/Payments/card-declined.feature");
  });

  it("checks a new folder's name before asking the daemon, then creates it inside the open folder", async () => {
    renderTree({ selected: "Checkout/place-an-order.feature" });
    fireEvent.click(screen.getByRole("button", { name: /^New$/ }));
    const input = screen.getByLabelText("New folder name");
    fireEvent.change(input, { target: { value: "payments" } });
    fireEvent.click(screen.getByRole("button", { name: "Create folder" }));
    expect(screen.getByRole("alert").textContent).toBe("“Payments” is already in Checkout.");
    expect(calls("POST")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("New folder name"), { target: { value: "Refunds" } });
    fireEvent.click(screen.getByRole("button", { name: "Create folder" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(JSON.parse(String((calls("POST")[0][1] as RequestInit).body))).toEqual({ parent: "Checkout", name: "Refunds" });
    expect(await screen.findByText("Created Checkout/Refunds")).toBeTruthy();
  });

  it("renames a folder from its menu and tells the screen where its cases went", async () => {
    const handlers = renderTree({ selected: "Checkout/Payments/card-declined.feature" });
    fireEvent.click(screen.getByRole("button", { name: "Actions for folder Payments" }));
    const menu = screen.getByRole("menu", { name: "Actions for folder Payments" });
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["New case here", "New form case here", "New folder here", "Add existing cases…", "Run folder", "RenameF2", "Move to…", "Delete folderDel"]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Rename/ }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "Cards" } });
    fireEvent.submit(screen.getByLabelText("Folder name").closest("form")!);
    await waitFor(() => expect(handlers.onRelocate).toHaveBeenCalledWith("Checkout/Payments", "Checkout/Cards"));
    expect(String(calls("PATCH")[0][0])).toBe("/api/folders/Checkout/Payments");
  });

  it("runs a folder and starts a case in it from the folder menu", () => {
    const handlers = renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Actions for folder Checkout" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Run folder" }));
    expect(handlers.onRunFolder).toHaveBeenCalledWith("Checkout");
    fireEvent.click(screen.getByRole("button", { name: "Actions for folder API" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "New case here" }));
    expect(handlers.onNewCase).toHaveBeenCalledWith("API", "feature");
  });

  it("deletes a folder keeping its cases unless asked to delete them too", async () => {
    const handlers = renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Actions for folder Checkout" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Delete folder/ }));
    expect(screen.getByText("It holds 3 cases and 1 subfolder.")).toBeTruthy();
    expect((screen.getByRole("radio", { name: /Keep the cases/ }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Delete folder" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(String(calls("DELETE")[0][0])).toBe("/api/folders/Checkout?mode=move_up");
    await waitFor(() => expect(handlers.onRelocate).toHaveBeenCalledWith("Checkout", ""));
  });

  it("moves a case to another folder from its menu", async () => {
    const handlers = renderTree({ selected: "login.json" });
    fireEvent.click(screen.getByRole("button", { name: "Actions for Title of login.json" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Move to…" }));
    expect((screen.getByRole("radio", { name: /Top level/ }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /^API/ }));
    fireEvent.click(screen.getByRole("button", { name: "Move here" }));
    await waitFor(() => expect(handlers.onRelocate).toHaveBeenCalledWith("login.json", "API/login.json"));
    expect(JSON.parse(String((calls("POST")[0][1] as RequestInit).body))).toEqual({ names: ["login.json"], folder: "API" });
  });

  it("searches cases and folders, showing where each one is", () => {
    renderTree();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search cases and folders" }), { target: { value: "pay" } });
    const results = screen.getByRole("list", { name: "Search results" });
    expect(within(results).getByRole("button", { name: /^Payments/ }).textContent).toContain("Checkout");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search cases and folders" }), { target: { value: "nothing-like-this" } });
    expect(screen.getByText(/Nothing matches/)).toBeTruthy();
  });

  it("collapses the whole tree under All cases and keeps it out of reach while closed", () => {
    renderTree();
    const root = screen.getByRole("button", { name: /^All cases/ });
    const tree = screen.getByRole("link", { name: /login\.json/ }).closest(".tree-collapse") as HTMLElement;
    expect(root.getAttribute("aria-expanded")).toBe("true");
    expect(tree.hasAttribute("inert")).toBe(false);
    fireEvent.click(root);
    expect(root.getAttribute("aria-expanded")).toBe("false");
    expect(tree.classList.contains("tree-collapse-closed")).toBe(true);
    expect(tree.hasAttribute("inert")).toBe(true);
  });

  it("hides into a rail and shows again", () => {
    const handlers = renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide cases" }));
    expect(handlers.onCollapse).toHaveBeenCalledWith(true);
  });
});
