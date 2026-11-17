import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Router, Switch } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { CasesScreen, DISCARD_PROMPT, caseNameFromParam } from "./Cases";

const LOGIN = `Feature: Login
  Scenario: Sign in
    Given I open "/login"
`;

const CASES = {
  cases: [
    { name: "login.feature", path: ".hercules/cases/login.feature", kind: "feature", valid: true, id: "login", title: "Login", steps: 1 },
    { name: "cart.feature", path: ".hercules/cases/cart.feature", kind: "feature", valid: true, id: "cart", title: "Cart", steps: 1 },
  ],
};

function saved(name: string, source: string) {
  return { name, kind: "feature", source, test_case: { id: name.replace(".feature", ""), title: name, steps: [{ action: 'Open "/login"' }] } };
}

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

let loginSource = LOGIN;

beforeEach(() => {
  loginSource = LOGIN;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url === "/api/cases") return respond(CASES);
      if (url === "/api/cases/login.feature") return respond(saved("login.feature", loginSource));
      if (url === "/api/cases/cart.feature") return respond(saved("cart.feature", "Feature: Cart\n"));
      if (url === "/api/cases/validate") return respond({ valid: true, error: null, test_case: { id: "login", title: "Login", steps: [{ action: "Open" }] } });
      return respond({ error: "not found" }, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  const location = memoryLocation({ path, record: true });
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook}>
        <Switch>
          <Route path="/cases">{() => <CasesScreen />}</Route>
          <Route path="/cases/:name">{(params) => <CasesScreen selected={params.name} />}</Route>
        </Switch>
      </Router>
    </QueryClientProvider>,
  );
  return { client, location };
}

const editor = () => screen.getByRole("textbox", { name: /gherkin/i }) as HTMLTextAreaElement;

describe("caseNameFromParam", () => {
  it("decodes only what wouter leaves encoded, and never throws on %", () => {
    expect(caseNameFromParam(undefined)).toBeNull();
    expect(caseNameFromParam("login.feature")).toBe("login.feature");
    expect(caseNameFromParam("100%.feature")).toBe("100%.feature"); // "%25" after wouter's decodeURI
    expect(caseNameFromParam("a%2Fb%3F.json")).toBe("a/b?.json");
    expect(caseNameFromParam("%41.json")).toBe("%41.json"); // literal, already decoded once
  });
});

describe("CasesScreen", () => {
  it("opens the form when + Form is pressed while a saved case is open", async () => {
    const { location } = renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(LOGIN));
    fireEvent.click(screen.getByRole("button", { name: /form/i }));
    await waitFor(() => expect(location.history?.at(-1)).toBe("/cases"));
    await waitFor(() => expect(screen.getByRole("heading", { level: 2, name: /new case/i })).toBeTruthy());
    expect(screen.queryByRole("tab", { name: "Gherkin" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Form" }).getAttribute("aria-selected")).toBe("true");
  });

  it("keeps unsaved edits when the case is fetched again", async () => {
    const { client } = renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(LOGIN));
    fireEvent.change(editor(), { target: { value: `${LOGIN}    Then I see "Welcome"\n` } });
    loginSource = `${LOGIN}  # changed elsewhere\n`;
    await act(async () => {
      await client.refetchQueries({ queryKey: ["case", "login.feature"] });
    });
    expect(editor().value).toContain('Then I see "Welcome"');
    await waitFor(() => expect(screen.getByText(/changed while you were editing/i)).toBeTruthy());
  });

  it("asks before dropping unsaved edits to open another case", async () => {
    const { location } = renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(LOGIN));
    fireEvent.change(editor(), { target: { value: `${LOGIN}# mine\n` } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("link", { name: /cart/i }));
    expect(confirm).toHaveBeenCalledWith(DISCARD_PROMPT);
    expect(location.history?.at(-1)).toBe("/cases/login.feature");
    expect(editor().value).toContain("# mine");

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("link", { name: /cart/i }));
    await waitFor(() => expect(location.history?.at(-1)).toBe("/cases/cart.feature"));
  });

  it("does not ask when nothing was changed", async () => {
    renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(LOGIN));
    const confirm = vi.spyOn(window, "confirm");
    fireEvent.click(screen.getByRole("button", { name: /gherkin/i }));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("edits a feature's test data and saves it with the Gherkin", async () => {
    const posted: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url === "/api/cases") {
          if (init?.method === "POST") {
            posted.push(JSON.parse(String(init.body)));
            return respond({ name: "login.feature", path: ".hercules/cases/login.feature", id: "login" }, 201);
          }
          return respond(CASES);
        }
        if (url === "/api/cases/login.feature") return respond({ ...saved("login.feature", LOGIN), test_case: { ...saved("login.feature", LOGIN).test_case, test_data: { user: "ana" } } });
        if (url === "/api/cases/login.feature/files") return respond({ case: "login", files: [], max_files: 20, max_bytes: 26214400 });
        if (url === "/api/cases/validate") return respond({ valid: true, error: null, test_case: { id: "login", title: "Login", steps: [{ action: "Open" }] } });
        return respond({ error: "not found" }, 404);
      }),
    );
    renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(LOGIN));
    fireEvent.click(screen.getByRole("tab", { name: "Form" }));
    await waitFor(() => expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("user"));
    expect(screen.getByRole("button", { name: "Add value" }).hasAttribute("disabled")).toBe(false);
    await waitFor(() => expect(screen.getByText(/\.hercules\/case-files\/login\//)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Add files" }).hasAttribute("disabled")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Add value" }));
    fireEvent.change(screen.getAllByLabelText("Name")[1], { target: { value: "password" } });
    fireEvent.click(screen.getAllByRole("checkbox", { name: /secret/i })[1]);
    fireEvent.change(screen.getByLabelText("Environment variable"), { target: { value: "SHOP_PASSWORD" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({ gherkin: LOGIN, test_data: { user: "ana", password: { secret: "SHOP_PASSWORD" } }, overwrite: true });
  });

  it("deletes the open case after asking", async () => {
    const deleted: string[] = [];
    const base = (globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>).bind(globalThis);
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          deleted.push(url);
          return respond({ name: "login.feature", removed: true });
        }
        return base(url, init);
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { location } = renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(LOGIN));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted).toEqual(["/api/cases/login.feature"]));
    await waitFor(() => expect(location.history?.at(-1)).toBe("/cases"));
    expect(await screen.findByText("Deleted login.feature")).toBeTruthy();
  });

  it("lists saved cases by title and step count, with the file name only as a hint", async () => {
    renderAt("/cases");
    const item = await screen.findByRole("link", { name: /Login/ });
    expect(item.textContent).toBe("Login1 step");
    expect(item.getAttribute("title")).toBe("login.feature");
  });

  it("explains @env tags and adds one for a saved environment", async () => {
    const base = (globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>).bind(globalThis);
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => (url === "/api/environments" ? respond({ environments: [{ name: "shop", base_url: "http://127.0.0.1:8201", secret_names: [] }] }) : base(url, init))),
    );
    renderAt("/cases");
    const help = await screen.findByRole("button", { name: "How to choose an environment" });
    expect(help.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(help);
    expect(screen.getByText(/tag above/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "@env:shop" }));
    expect(editor().value.startsWith("@smoke @env:shop\nFeature: Sign in")).toBe(true);
    expect(screen.getByRole("button", { name: "@env:shop" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("asks to quote bare placeholders on save and can add the quotes", async () => {
    const posted: Record<string, unknown>[] = [];
    const bare = 'Feature: Login\n  Scenario: Sign in\n    Given I open {{url}}\n';
    const base = (globalThis.fetch as unknown as (url: string, init?: RequestInit) => Promise<Response>).bind(globalThis);
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url === "/api/cases" && init?.method === "POST") {
          posted.push(JSON.parse(String(init.body)));
          return respond({ name: "login.feature", path: ".hercules/cases/login.feature", id: "login" }, 201);
        }
        return base(url, init);
      }),
    );
    loginSource = bare;
    renderAt("/cases/login.feature");
    await waitFor(() => expect(editor().value).toBe(bare));
    expect(screen.getByRole("note").textContent).toContain('line 3: {{url}} → "{{url}}"');

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByRole("button", { name: "Add quotes and save" })).toBeTruthy();
    expect(posted).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Add quotes and save" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].gherkin).toBe('Feature: Login\n  Scenario: Sign in\n    Given I open "{{url}}"\n');
    expect(editor().value).toContain('Given I open "{{url}}"');
  });
});
