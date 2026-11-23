// Every construct
// Exported by Hercules from run r_20261026_120000_abcdef (case golden-case). It repeats what the run did, with the
// locators that matched only the element used during the run. Adapt it to this project's tests (fixtures,
// page objects, playwright.config.ts) before keeping it, then run it with `npx playwright test`.
// Needs @playwright/test 1.43 or later.
// Environment: BASE_URL (optional), APP_PASSWORD (required), APP_TOKEN (required), PETS_API_KEY (required).
import { test, expect } from "@playwright/test";
import type { APIResponse } from "@playwright/test";

// The browser in HERCULES_CHROMIUM when it is set (a Chrome for Testing build), else Playwright's own.
test.use({ launchOptions: process.env.HERCULES_CHROMIUM ? { executablePath: process.env.HERCULES_CHROMIUM } : {} });

const BASE_URL = process.env.BASE_URL ?? "http://shop.test";
const TIMESTAMP = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
const RUN_ID = "run-" + TIMESTAMP;
const DATA = {
  country: "Canada",
  email: "user+" + RUN_ID + "@shop.test",
  name: "Sam",
};

/** A page address: relative ones are resolved against BASE_URL, as the run did. */
function url(path: string): string {
  return new URL(path, BASE_URL).toString();
}

/** An API address: relative paths go under BASE_URL's folder (a page at its end, such as /index.html, is left out), as the run did. */
function apiUrl(path: string): string {
  const base = new URL(BASE_URL);
  const folder = /\.(?:html?|php|aspx?|jsp)$/i.test(base.pathname) ? base.pathname.replace(/[^/]*$/, "") : base.pathname;
  return base.origin + folder.replace(/\/+$/, "") + "/" + path.replace(/^\/+/, "");
}

function env(name: string): string {
  const value = process.env[name];
  if (value === undefined) throw new Error(`set the environment variable ${name}`);
  return value;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** The value at a JSON path given as keys (undefined when there is none). */
function at(document: unknown, path: Array<string | number>): unknown {
  let node: unknown = document;
  for (const key of path) {
    if (node === null || typeof node !== "object" || !(key in node)) return undefined;
    node = (node as Record<string | number, unknown>)[key];
  }
  return node;
}

function exists(value: unknown): boolean {
  return value !== undefined;
}

/** Equal values; a number and its text are equal too (as in the run's checks), a boolean and a number never. */
function same(actual: unknown, expected: unknown): boolean {
  if (actual === undefined) return false;
  if ((typeof actual === "boolean") !== (typeof expected === "boolean")) return text(actual) === text(expected);
  return JSON.stringify(actual) === JSON.stringify(expected) || text(actual) === text(expected);
}

function contains(actual: unknown, expected: unknown): boolean {
  if (typeof actual === "string") return actual.includes(text(expected));
  if (Array.isArray(actual)) return actual.some((item) => same(item, expected));
  if (actual !== null && typeof actual === "object") return typeof expected === "string" && expected in actual;
  return false;
}

function number(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "") return Number(value);
  return Number.NaN;
}

function size(value: unknown): number {
  return value === undefined ? 0 : Array.isArray(value) ? value.length : 1;
}

function statusIn(status: number, codes: string[]): boolean {
  return codes.some((code) => code === String(status) || (code.endsWith("xx") && code[0] === String(status)[0]));
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The address with these query parameters (replacing any of the same name it already has). */
function withQuery(address: string, pairs: Array<[string, string]>): string {
  const target = new URL(address);
  for (const [name] of pairs) target.searchParams.delete(name);
  for (const [name, value] of pairs) target.searchParams.append(name, value);
  return target.toString();
}

test("Every construct", async ({ page, request }) => {
  let response: APIResponse | undefined;
  let body: unknown;
  const saved: Record<string, unknown> = {};
  await test.step("1. I open \"/login\"", async () => {
    await page.goto(url("/login"));
  });
  await test.step("2. I sign in", async () => {
    // also unique on the page: page.getByLabel("Email", { exact: true })
    await page.getByTestId("email").fill(DATA.email);
    await page.getByLabel("Password", { exact: true }).fill(env("APP_PASSWORD"));
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.waitForURL((url) => url.pathname === "/dashboard");
    await expect(page.getByText("Welcome, " + DATA.name)).toBeVisible();
    await expect(page).toHaveURL(new RegExp(escapeRegExp("/dashboard")));
  });
  await test.step("3. Choose options", async () => {
    await page.getByRole("combobox", { name: "Country", exact: true }).selectOption({ label: DATA.country });
    await page.locator("#colour").selectOption("blue");
    await page.getByRole("combobox", { name: "Book", exact: true }).evaluate((element) => element.scrollIntoView({ block: "center" }));
    await page.getByRole("combobox", { name: "Book", exact: true }).click();
    await page.getByRole("combobox", { name: "Book", exact: true }).pressSequentially("Peter Pan");
    await page.getByRole("option", { name: "Peter Pan", exact: true }).click();
    await page.getByRole("checkbox", { name: "I agree", exact: true }).check();
    await page.locator("[data-qa=\"help\"]").hover();
    await page.getByPlaceholder("Search", { exact: true }).press("Enter");
    await page.keyboard.press("Escape");
    await expect(page.getByText("Saved").first()).toBeVisible({ timeout: 10_000 });
    // the step waits a fixed time
    await page.waitForTimeout(1500);
    await expect(page.getByText("Items").first()).toBeVisible();
    await expect(page.getByText("Error")).toHaveCount(0);
    // TODO: no Playwright text assertion held on the page; check this text by hand
    // await expect(page.locator("body")).not.toContainText("Choose a country", { ignoreCase: true });
    await expect(page.getByText("Profile").first()).toBeVisible();
    await expect(page).toHaveURL(url("/account"));
  });
  await test.step("4. Pay by card", async () => {
    await page.locator("iframe[title=\"Card details\"]").contentFrame().getByRole("textbox", { name: "Card number", exact: true }).fill("4242 4242 4242 4242");
    await page.locator("#cart > coupon-box").locator("#apply").click();
    // TODO: no locator matched only the element to click (button "Remove"); this one is a guess
    await page.getByRole("button", { name: "Remove", exact: true }).click();
    await expect(page.locator("iframe[title=\"Card details\"]").contentFrame().getByText("Card saved")).toBeVisible();
    await expect(page.getByRole("link", { name: "Receipt", exact: true })).toBeVisible();
    // Judged by its meaning by a model during the run: "the order is confirmed"
    // The run's verdict: judged by meaning by jev: 93% likely to hold
    // Observed: page "Thanks" at /success
    // TODO: assert in code: the order is confirmed
  });
  await test.step("5. Create a pet", async () => {
    const started = Date.now();
    response = await request.fetch(withQuery(apiUrl("/pets"), [["dry", "no"], ["tags", "a"], ["tags", "b"]]), { method: "POST", headers: { "X-API-Key": env("PETS_API_KEY"), "Authorization": "Bearer " + env("APP_TOKEN") }, data: { "name": "Bella " + RUN_ID, "owner": DATA.email, "age": 2, "vaccinated": true, "notes": null }, maxRedirects: 0 });
    const elapsed = Date.now() - started;
    expect(response!.status(), "HTTP status").toBe(201);
    // TODO: validate the response body against the JSON Schema spec.yaml#/components/schemas/Pet with your project's validator
    body = await response!.json();
    expect(contains(at(body, ["name"]), "Bella"), "$.name contains \"Bella\"").toBe(true);
    expect(number(at(body, ["id"])) > number(0), "$.id gt 0").toBe(true);
    // TODO: check $.items[*].price all_gte 0 (a JSONPath this export does not translate)
    expect(!exists(at(body, ["missing"])), "$.missing not_exists").toBe(true);
    expect(exists(response!.headers()["location"]), "header Location exists").toBe(true);
    expect(await response!.text()).toContain("Bella");
    expect(elapsed).toBeLessThan(2000);
    saved.pet_id = at(body, ["id"]);
    saved.where = response!.headers()["location"];
  });
  await test.step("6. Read it back", async () => {
    response = await request.fetch(apiUrl("/pets/" + text(saved.pet_id)), { method: "GET", headers: { "Authorization": "Basic " + Buffer.from("admin:" + env("APP_PASSWORD")).toString("base64") }, maxRedirects: 0 });
    expect(statusIn(response!.status(), ["2xx", "304"]), "HTTP status 2xx or 304").toBe(true);
    body = await response!.json();
    expect(same(at(body, ["id"]), saved.pet_id), "$['id'] eq \"{{pet_id}}\"").toBe(true);
    expect(same(size(at(body, ["tags"])), 2), "$.tags count_eq 2").toBe(true);
  });
  await test.step("7. Send a note", async () => {
    response = await request.fetch("https://hooks.test/note", { method: "POST", headers: { "Content-Type": "text/plain" }, data: "hello " + DATA.name, maxRedirects: 0 });
    expect(response!.status(), "HTTP status").toBe(200);
  });
  await test.step("8. The dogs are stored", async () => {
    // The run queried the database {{db}} (sqlite): SELECT name FROM pets WHERE tag = :tag
    // and checked: {"rows": 2, "json_path": [{"path": "$[0].name", "value": "Rex"}]}
    // TODO: query the database with your project's client (read-only), then check the rows as above
  });
  await test.step("9. Accessible", async () => {
    // the a11y check runs Hercules' own checks; the export writes it as a comment to replace with your project's tools
    // The run checked the page's accessibility: {"kind": "a11y"}
    // To keep it in code, add @axe-core/playwright and:
    //   const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    //   expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
    // TODO: replace this a11y check with your project's tools
  });
});
