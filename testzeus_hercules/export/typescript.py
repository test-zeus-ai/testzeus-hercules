"""Playwright Test (TypeScript) from an automation spec: one ``test()`` with one ``test.step`` per case step.

Browser steps use the locators that were unique on the live page during the run, API steps use the
``request`` fixture (a separate context, as Hercules' own HTTP client is), secrets come from
``process.env`` by name. SQL and quality steps have no Playwright equivalent and are written as
comments with what the run did.
"""

from __future__ import annotations

import json
from typing import Any

from testzeus_hercules.act.api import text_content_type
from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationStep, LocatorSpec
from testzeus_hercules.cases.models import ApiCall
from testzeus_hercules.export import browser_setup
from testzeus_hercules.export.common import (
    BASE_URL_ENV,
    BROWSER_ENV,
    DOWNLOADED_FILE,
    Helpers,
    Writer,
    address_pattern,
    file_type_prefixes,
    json_path_keys,
    one_line,
    row_comparison,
    safe_id,
    status_codes,
    uploads_download,
)
from testzeus_hercules.export.common import viewport_size as _viewport
from testzeus_hercules.perceive.locators import css_string

WAIT_TEXT_MS = 10_000

HELPERS = Helpers(
    order=(
        "base",
        "url",
        "api_url",
        "env",
        "text",
        "at",
        "exists",
        "same",
        "contains",
        "matches",
        "filled",
        "number",
        "size",
        "bounded",
        "status_in",
        "escape",
        "with_query",
        "download",
        "download_start",
        "download_text",
        "download_rows",
    ),
    requires={
        "url": ("base",),
        "api_url": ("base",),
        "same": ("text",),
        "contains": ("same",),
        "matches": ("text",),
        "bounded": ("number",),
        "download_start": ("download",),
        "download_text": ("download",),
        "download_rows": ("download_text",),
    },
    source={
        "base": "",  # written by the header (it needs the run's base URL)
        "url": """
/** A page address: relative ones are resolved against BASE_URL, as the run did. */
function url(path: string): string {
  return new URL(path, BASE_URL).toString();
}
""",
        "api_url": """
/** An API address: relative paths go under BASE_URL's folder (a page at its end, such as /index.html, is left out), as the run did. */
function apiUrl(path: string): string {
  const base = new URL(BASE_URL);
  const folder = /\\.(?:html?|php|aspx?|jsp)$/i.test(base.pathname) ? base.pathname.replace(/[^/]*$/, "") : base.pathname;
  return base.origin + folder.replace(/\\/+$/, "") + "/" + path.replace(/^\\/+/, "");
}
""",
        "env": """
function env(name: string): string {
  const value = process.env[name];
  if (value === undefined) throw new Error(`set the environment variable ${name}`);
  return value;
}
""",
        "text": """
function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}
""",
        "at": """
/** The value at a JSON path given as keys (undefined when there is none). */
function at(document: unknown, path: Array<string | number>): unknown {
  let node: unknown = document;
  for (const key of path) {
    if (node === null || typeof node !== "object" || !(key in node)) return undefined;
    node = (node as Record<string | number, unknown>)[key];
  }
  return node;
}
""",
        "exists": """
function exists(value: unknown): boolean {
  return value !== undefined;
}
""",
        "same": """
/** Equal values; a number and its text are equal too (as in the run's checks), a boolean and a number never. */
function same(actual: unknown, expected: unknown): boolean {
  if (actual === undefined) return false;
  if ((typeof actual === "boolean") !== (typeof expected === "boolean")) return text(actual) === text(expected);
  return JSON.stringify(actual) === JSON.stringify(expected) || text(actual) === text(expected);
}
""",
        "contains": """
function contains(actual: unknown, expected: unknown): boolean {
  if (typeof actual === "string") return actual.includes(text(expected));
  if (Array.isArray(actual)) return actual.some((item) => same(item, expected));
  if (actual !== null && typeof actual === "object") return typeof expected === "string" && expected in actual;
  return false;
}
""",
        "matches": """
function matches(actual: unknown, pattern: unknown): boolean {
  return actual !== undefined && new RegExp(text(pattern)).test(text(actual));
}
""",
        "filled": """
function filled(value: unknown): boolean {
  if (value === undefined || value === null || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  return typeof value !== "object" || Object.keys(value).length > 0;
}
""",
        "number": """
function number(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "") return Number(value);
  return Number.NaN;
}
""",
        "size": """
function size(value: unknown): number {
  return value === undefined ? 0 : Array.isArray(value) ? value.length : 1;
}
""",
        "bounded": """
function bounded(value: unknown, bound: unknown, kind: "gte" | "lte"): boolean {
  const items = Array.isArray(value) ? value : value === undefined ? [] : [value];
  return items.length > 0 && items.every((item) => (kind === "gte" ? number(item) >= number(bound) : number(item) <= number(bound)));
}
""",
        "status_in": """
function statusIn(status: number, codes: string[]): boolean {
  return codes.some((code) => code === String(status) || (code.endsWith("xx") && code[0] === String(status)[0]));
}
""",
        "escape": """
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");
}
""",
        "download": """
let downloadedFile = "";

/** Start a download (a click, or opening its address) and keep the file, as the run did. */
async function keepDownload(page: import("@playwright/test").Page, start: () => Promise<unknown>): Promise<string> {
  const download = page.waitForEvent("download");
  await start().catch((error: unknown) => {
    if (!String(error).includes("Download is starting")) throw error;
  });
  const file = await download;
  downloadedFile = test.info().outputPath(file.suggestedFilename());
  await file.saveAs(downloadedFile);
  return downloadedFile;
}

/** Fetch a file from its address with the page's cookies and keep it, as the run did. */
async function fetchDownload(page: import("@playwright/test").Page, address: string): Promise<string> {
  const response = await page.request.get(address);
  expect(response.ok(), `${address} answered HTTP ${response.status()}`).toBe(true);
  const disposition = response.headers()["content-disposition"] ?? "";
  const named = disposition.includes("filename") ? (disposition.split("filename")[1].replace(/^[*= ]+/, "").split(";")[0].split("''").pop() ?? "").replace(/"/g, "") : "";
  const { writeFileSync } = await import("node:fs");
  downloadedFile = test.info().outputPath(decodeURIComponent(named || new URL(response.url()).pathname.split("/").pop() || "download"));
  writeFileSync(downloadedFile, await response.body());
  return downloadedFile;
}
""",
        "download_start": """
/** The first bytes of the latest download, to tell its type. */
async function downloadedStart(): Promise<string> {
  const { readFileSync } = await import("node:fs");
  return readFileSync(downloadedFile).subarray(0, 8).toString("latin1");
}
""",
        "download_text": """
/** The latest download's text. */
async function downloadedText(): Promise<string> {
  const { readFileSync } = await import("node:fs");
  return readFileSync(downloadedFile, "utf8").replace(/^\\uFEFF/, "");
}
""",
        "download_rows": """
/** The latest download's column names and rows (a simple CSV: no commas or line breaks inside cells). */
async function downloadedRows(): Promise<{ names: string[]; rows: string[][] }> {
  const lines = (await downloadedText()).split(/\\r?\\n/).filter((line) => line.trim());
  const cells = lines.map((line) => line.split(",").map((cell) => cell.trim().replace(/^"|"$/g, "")));
  return { names: cells[0] ?? [], rows: cells.slice(1) };
}
""",
        "with_query": """
/** The address with these query parameters (replacing any of the same name it already has). */
function withQuery(address: string, pairs: Array<[string, string]>): string {
  const target = new URL(address);
  for (const [name] of pairs) target.searchParams.delete(name);
  for (const [name, value] of pairs) target.searchParams.append(name, value);
  return target.toString();
}
""",
    },
)


class TypeScriptWriter(Writer):
    comment = "//"
    indent_unit = "  "
    content_frame = ".contentFrame()"
    equals = "==="

    # ------------------------------------------------------------------ expressions

    def option(self, value: Any) -> str:
        """A browser option's value as a JavaScript literal; secrets read from the environment."""
        return browser_setup.literal(value, string=self.string, secret=self.env_expr, python=False)

    def launch_use(self) -> list[str]:
        spec = self.spec
        engine = browser_setup.engine(spec)
        items = browser_setup.launch_items(spec)
        channel = next((value for name, value in items if name == "channel"), None)
        launch = [(name, value) for name, value in items if name != "channel"]
        if engine == "chromium" and channel is None and not launch:
            return [
                f"// The browser in {BROWSER_ENV} when it is set (a Chrome for Testing build), else Playwright's own.",
                f"test.use({{ launchOptions: process.env.{BROWSER_ENV} ? {{ executablePath: process.env.{BROWSER_ENV} }} : {{}} }});",
            ]
        parts = [f"browserName: {self.string(engine)}"]
        if channel is not None:
            parts.append(f"channel: {self.string(str(channel))}")
        options = [f"{browser_setup.camel(name)}: {self.option(value)}" for name, value in launch]
        if engine == "chromium" and channel is None:
            options.insert(0, f"...(process.env.{BROWSER_ENV} ? {{ executablePath: process.env.{BROWSER_ENV} }} : {{}})")
        if options:
            parts.append("launchOptions: { " + ", ".join(options) + " }")
        return ["// The browser and launch options the run used.", "test.use({ " + ", ".join(parts) + " });"]

    def context_use(self) -> list[str]:
        """The context options the run used, as one test.use line (unknown names go in contextOptions)."""
        spec = self.spec
        opts = browser_setup.options_of(spec)
        typed = [(name, value) for name, value in browser_setup.context_items(spec) if name not in opts.context_extra]
        parts = [f"{browser_setup.camel(name)}: {self.option(value)}" for name, value in typed]
        if opts.storage_state:
            parts.append(f"storageState: process.env.{browser_setup.STORAGE_STATE_ENV} ?? {self.string(opts.storage_state)}")
        if opts.record_video:
            parts.append('video: "on"')
        if opts.context_extra:
            parts.append("contextOptions: { " + ", ".join(f"{browser_setup.camel(name)}: {self.option(value)}" for name, value in opts.context_extra.items()) + " }")
        return ["// The browser context the run used.", "test.use({ " + ", ".join(parts) + " });"] if parts else []

    def env_expr(self, name: str) -> str:
        self.helpers.add("env")
        return f"env({self.string(name)})"

    def data_expr(self, name: str) -> str:
        return f"DATA.{name}"

    def saved_expr(self, name: str) -> str:
        return f"saved.{name}"

    def text_expr(self, expr: str) -> str:
        return f"text({expr})"

    def dict_literal(self, items: list[tuple[str, str]]) -> str:
        return "{ " + ", ".join(f"{self.string(key)}: {value}" for key, value in items) + " }" if items else "{}"

    def negate(self, expr: str) -> str:
        return f"!{expr}"

    def options(self, items: list[tuple[str, str]]) -> str:
        return "{ " + ", ".join(f"{key}: {value}" for key, value in items) + " }"

    def locator_call(self, spec: LocatorSpec) -> str:
        exact = [("exact", "true")] if spec.exact else []
        value = self.string(spec.value or "")
        if spec.strategy == "test_id":
            if (spec.attribute or "data-testid") == "data-testid":
                return f".getByTestId({value})"
            selector = f"[{spec.attribute}={css_string(spec.value or '')}]"
            return f".locator({self.string(selector)})"
        if spec.strategy == "role":
            if spec.name is None:
                return f".getByRole({self.string(spec.role or '')})"
            return f".getByRole({self.string(spec.role or '')}, {self.options([('name', self.string(spec.name)), *exact])})"
        method = {"label": "getByLabel", "placeholder": "getByPlaceholder", "text": "getByText", "alt_text": "getByAltText", "title": "getByTitle"}.get(spec.strategy)
        if method is not None:
            return f".{method}({value}, {self.options(exact)})" if exact else f".{method}({value})"
        return "".join(f".locator({self.string(selector)})" for selector in spec.selectors)

    # ------------------------------------------------------------------ file structure

    def header(self) -> None:
        spec = self.spec
        lines = [
            f"// {one_line(spec.title)}",
            f"// Exported by Hercules from run {safe_id(self.run_id)} (case {safe_id(spec.case_id)}). It repeats what the run did, with the",
            "// locators that matched only the element used during the run. Adapt it to this project's tests (fixtures,",
            "// page objects, playwright.config.ts) before keeping it, then run it with `npx playwright test`.",
            "// Needs @playwright/test 1.43 or later.",
        ]
        if self.partial_note:
            lines.append(f"// {one_line(self.partial_note)}")
        if spec.browser:
            for name in browser_setup.secrets_of(spec):
                self.secrets.setdefault(name, "a browser option")
        state = browser_setup.options_of(spec).storage_state if spec.browser else None
        env = [f"{BASE_URL_ENV} (optional)"] if self.uses_base() else []
        env += [f"{browser_setup.STORAGE_STATE_ENV} (optional)"] if state else []
        env += [f"{name} (required)" for name in sorted(self.secrets)]
        if env:
            lines.append("// " + one_line("Environment: " + ", ".join(env) + "."))
        lines.append('import { test, expect, devices } from "@playwright/test";' if spec.device else 'import { test, expect } from "@playwright/test";')
        if self.api_used:
            lines.append('import type { APIResponse } from "@playwright/test";')
        lines.append("")
        if spec.browser:
            lines += self.launch_use()
            if spec.device:
                # The phone or tablet the run emulated, in the browser the run used (the profile may name another one).
                lines.append(f"test.use({{ ...devices[{self.string(spec.device)}], defaultBrowserType: {self.string(browser_setup.engine(spec))} }});")
            viewport = _viewport(spec.viewport)
            if viewport is not None:
                lines.append(f"test.use({{ viewport: {{ width: {viewport[0]}, height: {viewport[1]} }} }});")
            lines += self.context_use()
            lines.append("")
        if self.uses_base():
            lines.append(f"const BASE_URL = process.env.{BASE_URL_ENV} ?? {self.string(spec.base_url or '')};")
        builtins = [name for name, entry in spec.data.items() if entry.kind == "builtin"]
        if "timestamp" in builtins or "run_id" in builtins:
            lines.append('const TIMESTAMP = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);')
        if "run_id" in builtins:
            lines.append('const RUN_ID = "run-" + TIMESTAMP;')
        values = [(name, entry.value or "") for name, entry in spec.data.items() if entry.kind == "value"]
        if values:
            lines.append("const DATA = {")
            for name, value in values:
                lines.append(f"  {name}: {self.expr_builtins(value)},")
            lines.append("};")
        if lines[-1] != "":
            lines.append("")
        lines.extend(HELPERS.emit(self.helpers - {"base"}))
        self.lines = lines

    def uses_base(self) -> bool:
        return bool(HELPERS.closure(self.helpers) & {"base"})

    def expr_builtins(self, template: str) -> str:
        """A case value: only built-ins are substituted in it (as the run did)."""
        saved = self.spec.data
        builtins = {name: entry for name, entry in saved.items() if entry.kind == "builtin"}
        writer = TypeScriptWriter(self.spec.model_copy(update={"data": builtins}), run_id=self.run_id)
        return writer.expr(template)

    def open_test(self) -> None:
        fixtures = ", ".join(name for name, used in (("page", self.spec.browser), ("request", self.api_used)) if used) or "page"
        self.emit(f"test({self.string(self.spec.title)}, async ({{ {fixtures} }}) => {{")
        self.depth += 1
        if self.api_used:
            self.emit("let response: APIResponse | undefined;")
            self.emit("let body: unknown;")
        if any(entry.kind == "saved" for entry in self.spec.data.values()):
            self.emit("const saved: Record<string, unknown> = {};")
        self.depth -= 1

    def close_test(self) -> None:
        self.emit("});")

    def open_step(self, step: AutomationStep) -> None:
        title = " ".join(step.action.split())
        self.emit(f"await test.step({self.string(f'{step.index}. {title}')}, async () => {{")

    def close_step(self, step: AutomationStep) -> None:
        self.emit("});")

    # ------------------------------------------------------------------ browser steps

    def action(self, step: AutomationStep, action: AutomationAction) -> None:
        self.action_notes(step, action)
        kind = action.kind
        if kind == "navigate":
            if action.url is None:
                self.todo(step, "the address this step opened was not recorded")
                return
            self.emit(f"await page.goto({self.url_expr(action.url)});")
        elif kind == "wait_text":
            self.emit(f"await expect(page.getByText({self.expr(action.text or '')}).first()).toBeVisible({{ timeout: {WAIT_TEXT_MS:_} }});")
        elif kind == "wait_time":
            self.emit(f"await page.waitForTimeout({int((action.seconds or 0) * 1000)});")
        elif kind == "press" and action.target is None:
            self.emit(f"await page.keyboard.press({self.string(action.key or '')});")
        elif kind == "download":
            self.helpers.add("download")
            if action.target is not None:
                self.emit(f"await keepDownload(page, () => {self.target_expr(step, action.target, kind)}.click());")
            elif action.url is not None:
                self.emit(f"await fetchDownload(page, {self.url_expr(action.url)});")
            else:
                self.todo(step, "the download this step started was not recorded")
        else:
            self.element_action(step, action)
        if action.navigated_to:
            compare = "url.pathname" if action.navigated_to.startswith("/") else "url.origin + url.pathname"
            pattern = address_pattern(action.navigated_to)
            if pattern is not None and action.navigated_from:
                # The new address holds an id made for the run (a new record's page, which an app may also show under
                # another address): wait to leave this page; the next steps check what it shows.
                left = "url.pathname" if action.navigated_from.startswith("/") else "url.origin + url.pathname"
                self.emit(f"await page.waitForURL((url) => {left} !== {self.string(action.navigated_from)});")
            elif pattern is not None:  # a record id or number made for the run: the next run gets another
                self.emit(f"await page.waitForURL((url) => new RegExp({self.string('^' + pattern + '$')}).test({compare}));")
            else:
                self.emit(f"await page.waitForURL((url) => {compare} === {self.string(action.navigated_to)});")

    def element_action(self, step: AutomationStep, action: AutomationAction) -> None:
        kind = action.kind
        locator = self.target_expr(step, action.target, kind)
        if kind in ("click", "hover", "check", "uncheck"):
            self.emit(f"await {locator}.{kind}();")
        elif kind == "press":
            self.emit(f"await {locator}.press({self.string(action.key or '')});")
        elif kind == "fill":
            if action.value is None:
                self.todo(step, "the value typed here was not recorded (it held a secret not referenced by name)")
                return
            self.emit(f"await {locator}.fill({self.expr(action.value)});")
        elif kind == "select":
            self.select(step, action, locator)
        elif kind == "upload" and uploads_download(action):
            if "".join((action.value or "").split()) != DOWNLOADED_FILE:
                self.todo(step, "attach the file the test downloaded together with the other files here")
                return
            self.helpers.add("download")
            self.emit(f"await {locator}.setInputFiles([downloadedFile]);")
        elif kind == "upload":
            if not action.files:
                self.todo(step, "the files attached here were not recorded")
                return
            self.emit(f"await {locator}.setInputFiles([{', '.join(self.string(path) for path in action.files)}]);")

    def select(self, step: AutomationStep, action: AutomationAction, locator: str) -> None:
        option = self.expr(action.option) if action.option is not None else None
        if option is None:
            self.todo(step, "the option chosen here was not recorded")
            return
        if action.select_by == "label":
            self.emit(f"await {locator}.selectOption({{ label: {option} }});")
        elif action.select_by == "value":
            self.emit(f"await {locator}.selectOption({option});")
        else:
            # A custom drop-down's list opens next to it: centred, the list fits on screen (clicking an option the page
            # would scroll to closes many such lists).
            self.emit(f'await {locator}.evaluate((element) => element.scrollIntoView({{ block: "center" }}));')
            self.emit(f"await {locator}.click();")
            if action.typed and action.value is not None:
                self.emit(f"await {locator}.pressSequentially({self.expr(action.value)});")
            if action.select_by == "option_role":
                exact = ", exact: true" if action.option_exact else ""
                self.emit(f"await page.getByRole({self.string(action.option_role or 'option')}, {{ name: {option}{exact} }}).click();")
            else:
                self.emit(f"await page.getByText({option}, {{ exact: true }}).last().click();")

    def check(self, step: AutomationStep, check: AutomationCheck) -> None:
        if check.kind in ("semantic", "visual"):
            how = "by its meaning" if check.kind == "semantic" else "on a screenshot"
            self.note(f"Judged {how} by a model during the run: {json.dumps(check.expected, ensure_ascii=False)}")
            if check.note:
                self.note(f"The run's verdict: {check.note.split('; write')[0]}")
            if check.observed:
                self.note(f"Observed: {check.observed}")
            if check.kind == "visual":
                self.note("For example: await expect(page).toHaveScreenshot();")
            self.todo(step, f"assert in code: {' '.join(check.expected.split())[:160]}")
            return
        if not check.faithful and check.note:
            self.todo(step, check.note)
        expected = self.expr(check.expected)
        scope = self.scope_expr(check.target.frames) if check.target is not None and check.kind in ("text", "not_text") else "page"
        if check.kind == "text":
            if check.form in ("text", "text_first"):
                self.emit(f"await expect({scope}.getByText({expected}){'.first()' if check.form == 'text_first' else ''}).toBeVisible();")
            else:
                self.unproven(check, f'await expect({scope}.locator("body")).toContainText({expected}, {{ ignoreCase: true }});')
        elif check.kind == "not_text":
            if check.form == "count_zero":
                self.emit(f"await expect(page.getByText({expected})).toHaveCount(0);")
            else:
                self.unproven(check, f'await expect(page.locator("body")).not.toContainText({expected}, {{ ignoreCase: true }});')
        elif check.kind == "url":
            if check.match == "equals":
                value, _ = self.url_check_value(check)
                self.emit(f"await expect(page).toHaveURL({value});")
            else:
                self.helpers.add("escape")
                self.emit(f"await expect(page).toHaveURL(new RegExp(escapeRegExp({expected})));")
        elif check.kind == "visible_text":
            self.emit(f"await expect(page.getByText({expected}).first()).toBeVisible();")
        elif check.kind == "element_visible":
            self.emit(f"await expect({self.target_expr(step, check.target, 'check')}).toBeVisible();")
        elif check.kind == "file":
            self.file_check(step, check, expected)

    def file_check(self, step: AutomationStep, check: AutomationCheck, expected: str) -> None:
        if not check.faithful:
            return
        self.helpers.add("download")
        compare = "toBe" if check.match == "equals" else "toContain"
        if check.file_check == "name":
            self.emit(f"expect(downloadedFile.split(/[\\\\/]/).pop()).{compare}({expected});")
        elif check.file_check == "type":
            prefixes = file_type_prefixes(check.expected)
            if prefixes is None:
                self.todo(step, f"check that the downloaded file is a {check.expected}")
                return
            self.helpers.add("download_start")
            tests = " || ".join(f"start.startsWith({self.string(prefix.decode('latin1'))})" for prefix in prefixes)
            self.emit(f"{{ const start = await downloadedStart(); expect({tests}).toBe(true); }}")
        elif check.file_check in ("text", "not_text"):
            self.helpers.add("download_text")
            negate = ".not" if check.file_check == "not_text" else ""
            self.emit(f'expect((await downloadedText()).replace(/\\s+/g, " ").toLowerCase()){negate}.toContain(({expected}).toLowerCase());')
        elif check.file_check == "rows":
            found = row_comparison(check.expected)
            if found is None:
                self.todo(step, f"check that the downloaded file has {check.expected} rows")
                return
            self.helpers.add("download_rows")
            matcher = {"==": "toBe", ">=": "toBeGreaterThanOrEqual", "<=": "toBeLessThanOrEqual", ">": "toBeGreaterThan", "<": "toBeLessThan"}[found[0]]
            self.emit(f"expect((await downloadedRows()).rows.length).{matcher}({found[1]});")
        elif check.file_check == "column":
            self.helpers.add("download_rows")
            column = self.string(check.subject or "")
            picked = "rows"
            if check.where:
                key, _, value = check.where.partition("=")
                picked = f"rows.filter((row) => row[names.indexOf({self.string(key.strip())})] === {self.string(value.strip())})"
            self.emit(
                f"{{ const {{ names, rows }} = await downloadedRows(); const picked = {picked}; expect(picked.length).toBeGreaterThan(0); for (const row of picked) expect(row[names.indexOf({column})]).{compare}({expected}); }}"
            )
        elif check.file_check == "json":
            keys = json_path_keys(check.subject or "")
            if keys is None:
                self.todo(step, f"check {check.subject} in the downloaded JSON")
                return
            self.helpers.update({"download_text", "at", "text"})
            self.emit(f"expect(text(at(JSON.parse(await downloadedText()), {json.dumps(keys)}))).{compare}({expected});")

    # ------------------------------------------------------------------ API, SQL and quality steps

    def api_step(self, step: AutomationStep) -> None:
        call = self.api_call(step)
        if call is None or self.masked(step, step.api):
            if call is None:
                self.todo(step, "the API call could not be read back from the run")
            return
        expect = call.expect
        if call.path is not None:
            timed = expect is not None and expect.max_time_ms is not None
            if timed:
                self.emit("const started = Date.now();")
            self.emit(f"response = await request.fetch({self.request_url(call)}, {self.request_options(step, call)});")
            self.response_seq += 1
            if timed:
                self.emit("const elapsed = Date.now() - started;")
        if expect is not None:
            self.api_checks(step, call)
        for capture in call.save:
            if capture.header is not None:
                self.emit(f"saved.{capture.name} = response!.headers()[{self.string(capture.header.lower())}];")
                continue
            keys = json_path_keys(capture.json_path or "$")
            if keys is None:
                self.todo(step, f"save {capture.json_path} as {{{{{capture.name}}}}} (a JSONPath this export does not translate)")
                continue
            self.parse_body()
            self.helpers.add("at")
            self.emit(f"saved.{capture.name} = at(body, {self.keys_literal(keys)});")

    def parse_body(self) -> None:
        if self.body_seq != self.response_seq:
            self.emit("body = await response!.json();")
            self.body_seq = self.response_seq

    def request_url(self, call: ApiCall) -> str:
        address = self.api_url_expr(call.path or "")
        if not call.query:
            return address
        self.helpers.add("with_query")
        pairs: list[str] = []
        for name, value in call.query.items():
            for item in value if isinstance(value, list) else [value]:
                pairs.append(f"[{self.expr(name)}, {self.expr(item)}]")
        return f"withQuery({address}, [{', '.join(pairs)}])"

    def request_options(self, step: AutomationStep, call: ApiCall) -> str:
        headers = [(name, self.expr(value)) for name, value in call.headers.items()]
        if call.auth is not None:
            token = self.expr(call.auth.value)
            headers.append(("Authorization", f'"Basic " + Buffer.from({token}).toString("base64")' if call.auth.type == "basic" else f'"Bearer " + {token}'))
        options: list[tuple[str, str]] = [("method", self.string(call.method))]
        body = self.request_body(step, call, headers)
        if headers:
            options.append(("headers", self.dict_literal(headers)))
        options.extend(body)
        options.append(("maxRedirects", "0"))
        return self.options(options)

    def request_body(self, step: AutomationStep, call: ApiCall, headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
        body, mode = call.body, call.body_mode
        if body is None:
            return []
        has_type = any(name.lower() == "content-type" for name, _ in headers)
        mode = mode or ("raw" if isinstance(body, str) else "json")
        if mode == "json":
            if isinstance(body, str):
                if not has_type:
                    headers.append(("Content-Type", self.string("application/json")))
                return [("data", f"JSON.stringify({self.expr(body, typed=True)})")]
            return [("data", self.json_value(body))]
        if mode == "raw":
            if isinstance(body, str) and call.body_mode is None and not has_type:
                headers.append(("Content-Type", self.string(text_content_type(body))))
            return [("data", self.expr(body) if isinstance(body, str) else f"JSON.stringify({self.json_value(body)})")]
        if mode == "urlencoded" and isinstance(body, dict):
            return [("form", self.dict_literal([(str(key), self.expr(value) if isinstance(value, str) else self.json_value(value)) for key, value in body.items()]))]
        if mode == "multipart" and isinstance(body, dict) and not any(isinstance(value, dict) for value in body.values()):
            return [("multipart", self.dict_literal([(str(key), self.expr(value) if isinstance(value, str) else f"JSON.stringify({self.json_value(value)})") for key, value in body.items()]))]
        self.todo(step, f"send the {mode} body this step sent (files and binary bodies are not exported)")
        return []

    def api_checks(self, step: AutomationStep, call: ApiCall) -> None:
        expect = call.expect
        assert expect is not None
        if expect.status is not None:
            codes = status_codes(expect.status)
            if len(codes) == 1 and codes[0].isdigit():
                self.emit(f'expect(response!.status(), "HTTP status").toBe({codes[0]});')
            else:
                self.emit(f'expect({self.status_condition("response!.status()", codes)}, "HTTP status {" or ".join(codes)}").toBe(true);')
        for label, reference in (("JSON Schema", expect.json_schema if isinstance(expect.json_schema, str) else ("inline schema" if expect.json_schema else None)), ("OpenAPI", expect.openapi)):
            if reference:
                self.todo(step, f"validate the response body against the {label} {reference} with your project's validator")
        if expect.json_path:
            self.parse_body()
        for item in expect.json_path:
            condition = self.json_condition("body", item.path, item.op, item.value, "value" in item.model_fields_set)
            self.assertion(step, condition, f"{item.path} {item.op}" + ("" if item.op in ("exists", "not_exists", "not_empty") else f" {json.dumps(item.value, ensure_ascii=False)}"))
        for header in expect.headers:
            actual = f"response!.headers()[{self.string(header.name.lower())}]"
            value = self.expr(header.value) if header.value is not None else self.null
            self.assertion(step, self.operator_condition(actual, header.op, value), f"header {header.name} {header.op}")
        for text in expect.body_contains:
            self.emit(f"expect(await response!.text()).toContain({self.expr(text)});")
        if expect.max_time_ms is not None and call.path is None:
            self.todo(step, f"check the response time is under {expect.max_time_ms} ms (this step sent no request of its own to time)")
        elif expect.max_time_ms is not None:
            self.emit(f"expect(elapsed).toBeLessThan({expect.max_time_ms});")

    def assertion(self, step: AutomationStep, condition: str | None, message: str) -> None:
        if condition is None:
            self.todo(step, f"check {message} (a JSONPath this export does not translate)")
            return
        self.emit(f"expect({condition}, {self.string(message)}).toBe(true);")

    def sql_step(self, step: AutomationStep) -> None:
        call = self.sql_call(step)
        if call is not None and call.query:
            self.note(f"The run queried the database {{{{{call.db}}}}} ({step.sql_dialect or 'unknown dialect'}): {' '.join(call.query.split())}")
        if call is not None and call.expect is not None:
            self.note(f"and checked: {json.dumps(call.expect.model_dump(mode='json', exclude_defaults=True), ensure_ascii=False)}")
        self.todo(step, "query the database with your project's client (read-only), then check the rows as above")

    def quality_step(self, step: AutomationStep) -> None:
        check = step.quality or {}
        kind = check.get("kind")
        self.note(f"The run checked the page's {'accessibility' if kind == 'a11y' else 'look against a baseline' if kind == 'visual' else 'layout'}: {json.dumps(check, ensure_ascii=False)}")
        if kind == "a11y":
            self.note("To keep it in code, add @axe-core/playwright and:")
            self.note('  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();')
            self.note('  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);')
        elif kind == "visual":
            self.note("For example: await expect(page).toHaveScreenshot();")
        self.todo(step, f"replace this {kind} check with your project's tools")
