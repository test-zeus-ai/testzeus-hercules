"""pytest-playwright (Python, sync API) from an automation spec: one ``test_<case>`` function, a comment per step.

Browser steps use the locators that were unique on the live page during the run, API steps a
separate ``APIRequestContext`` (as Hercules' own HTTP client is), secrets ``os.environ`` by name,
and SQLite queries the standard library's ``sqlite3`` (read-only). Other databases and quality
steps are written as comments with what the run did.
"""

from __future__ import annotations

import json
from typing import Any

from testzeus_hercules.act.api import text_content_type
from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationStep, LocatorSpec
from testzeus_hercules.cases.models import ApiCall
from testzeus_hercules.export import browser_setup
from testzeus_hercules.export.common import BASE_URL_ENV, BROWSER_ENV, Helpers, Writer, address_pattern, json_path_keys, one_line, safe_id, snake, status_codes
from testzeus_hercules.export.common import viewport_size as _viewport
from testzeus_hercules.perceive.locators import css_string

WAIT_TEXT_MS = 10_000

HELPERS = Helpers(
    order=("base", "url", "api_url", "text", "missing", "at", "exists", "same", "contains", "matches", "filled", "number", "size", "bounded", "status_in", "with_query", "sqlite"),
    requires={
        "url": ("base",),
        "api_url": ("base",),
        "at": ("missing",),
        "exists": ("missing",),
        "same": ("text", "missing"),
        "contains": ("same",),
        "matches": ("text", "missing"),
        "filled": ("missing",),
        "size": ("missing",),
        "bounded": ("number", "missing"),
    },
    source={
        "base": "",  # written by the header (it needs the run's base URL)
        "url": '''
def url(path: str) -> str:
    """A page address: relative ones are resolved against BASE_URL, as the run did."""
    return urljoin(BASE_URL, path)
''',
        "api_url": '''
def api_url(path: str) -> str:
    """An API address: relative paths are appended to BASE_URL, as the run did."""
    return BASE_URL.rstrip("/") + "/" + path.lstrip("/")
''',
        "text": """
def text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
""",
        "missing": """
MISSING: Any = object()
""",
        "at": '''
def at(document: Any, path: list[str | int]) -> Any:
    """The value at a JSON path given as keys (MISSING when there is none)."""
    node = document
    for key in path:
        if isinstance(node, dict) and isinstance(key, str) and key in node:
            node = node[key]
        elif isinstance(node, list) and isinstance(key, int) and -len(node) <= key < len(node):
            node = node[key]
        else:
            return MISSING
    return node
''',
        "exists": """
def exists(value: Any) -> bool:
    return value is not MISSING
""",
        "same": '''
def same(actual: Any, expected: Any) -> bool:
    """Equal values; a number and its text are equal too (as in the run's checks), a boolean and a number never."""
    if actual is MISSING:
        return False
    if isinstance(actual, bool) != isinstance(expected, bool):
        return text(actual) == text(expected)
    return bool(actual == expected) or text(actual) == text(expected)
''',
        "contains": """
def contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str):
        return text(expected) in actual
    if isinstance(actual, list):
        return any(same(item, expected) for item in actual)
    if isinstance(actual, dict):
        return isinstance(expected, str) and expected in actual
    return False
""",
        "matches": """
def matches(actual: Any, pattern: Any) -> bool:
    return actual is not MISSING and re.search(text(pattern), text(actual)) is not None
""",
        "filled": """
def filled(value: Any) -> bool:
    if value is MISSING or value is None or value == "":
        return False
    return not isinstance(value, (list, dict)) or len(value) > 0
""",
        "number": """
def number(value: Any) -> float:
    if isinstance(value, bool):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return float("nan")
""",
        "size": """
def size(value: Any) -> int:
    return 0 if value is MISSING else len(value) if isinstance(value, list) else 1
""",
        "bounded": """
def bounded(value: Any, bound: Any, kind: str) -> bool:
    items = value if isinstance(value, list) else [] if value is MISSING else [value]
    return bool(items) and all(number(item) >= number(bound) if kind == "gte" else number(item) <= number(bound) for item in items)
""",
        "status_in": """
def status_in(status: int, codes: list[str]) -> bool:
    return any(code == str(status) or (code.endswith("xx") and code[0] == str(status)[0]) for code in codes)
""",
        "with_query": '''
def with_query(address: str, pairs: list[tuple[str, str]]) -> str:
    """The address with these query parameters (replacing any of the same name it already has)."""
    parts = urlsplit(address)
    names = {name for name, _ in pairs}
    kept = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True) if name not in names]
    return urlunsplit(parts._replace(query=urlencode(kept + list(pairs))))
''',
        "sqlite": '''
def sqlite_rows(address: str, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows of one read-only query on the SQLite database an SQLAlchemy URL names (sqlite:///app.db).

    A relative path is taken from the current directory (the run took it from the project folder).
    """
    path = address.split(":///", 1)[1] if ":///" in address else address
    connection = sqlite3.connect(Path(path.split("?", 1)[0]).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, params)]
    finally:
        connection.close()
''',
    },
)


class PythonWriter(Writer):
    comment = "#"
    indent_unit = "    "
    content_frame = ".content_frame"
    equals = "=="
    true = "True"
    false = "False"
    null = "None"

    # ------------------------------------------------------------------ expressions

    def env_expr(self, name: str) -> str:
        return f"os.environ[{self.string(name)}]"

    def option(self, value: Any) -> str:
        """A browser option's value as a Python literal; secrets read from the environment."""
        return browser_setup.literal(value, string=self.string, secret=self.env_expr, python=True)

    def launch_fixture(self) -> list[str]:
        spec = self.spec
        items = browser_setup.launch_items(spec)
        chromium_build = browser_setup.engine(spec) == "chromium" and not any(name == "channel" for name, _ in items)
        if not items and not chromium_build:
            return []  # Playwright's own Firefox or WebKit, with nothing to change
        lines = ["", '@pytest.fixture(scope="session")', "def browser_type_launch_args(browser_type_launch_args: dict) -> dict:"]
        if not items:
            lines += [
                f'    """The browser in {BROWSER_ENV} when it is set (a Chrome for Testing build), else Playwright\'s own."""',
                f"    executable = os.environ.get({self.string(BROWSER_ENV)})",
                '    return {**browser_type_launch_args, "executable_path": executable} if executable else browser_type_launch_args',
                "",
            ]
            return lines
        lines += ['    """The launch options the run used."""', "    args = {**browser_type_launch_args}"]
        if chromium_build:
            lines += [
                f"    executable = os.environ.get({self.string(BROWSER_ENV)})  # a Chrome for Testing build, else Playwright's own",
                "    if executable:",
                '        args["executable_path"] = executable',
            ]
        lines += [f"    args[{self.string(name)}] = {self.option(value)}" for name, value in items]
        lines += ["    return args", ""]
        return lines

    def context_lines(self) -> list[str]:
        """The context options the run used, as lines of the browser_context_args fixture."""
        spec = self.spec
        if not spec.browser:
            return []
        opts = browser_setup.options_of(spec)
        lines = [f"    args[{self.string(name)}] = {self.option(value)}" for name, value in browser_setup.context_items(spec)]
        if opts.storage_state:
            lines.append(f'    args["storage_state"] = os.environ.get({self.string(browser_setup.STORAGE_STATE_ENV)}, {self.string(opts.storage_state)})  # the signed-in state')
        if opts.record_video:
            lines.append(f'    args["record_video_dir"] = {self.string(browser_setup.VIDEO_DIR)}')
        return lines

    def data_expr(self, name: str) -> str:
        return f"DATA[{self.string(name)}]"

    def saved_expr(self, name: str) -> str:
        return f"saved[{self.string(name)}]"

    def text_expr(self, expr: str) -> str:
        return f"text({expr})"

    def dict_literal(self, items: list[tuple[str, str]]) -> str:
        return "{" + ", ".join(f"{self.string(key)}: {value}" for key, value in items) + "}"

    def negate(self, expr: str) -> str:
        return f"not {expr}"

    def locator_call(self, spec: LocatorSpec) -> str:
        exact = ", exact=True" if spec.exact else ""
        value = self.string(spec.value or "")
        if spec.strategy == "test_id":
            if (spec.attribute or "data-testid") == "data-testid":
                return f".get_by_test_id({value})"
            selector = f"[{spec.attribute}={css_string(spec.value or '')}]"
            return f".locator({self.string(selector)})"
        if spec.strategy == "role":
            if spec.name is None:
                return f".get_by_role({self.string(spec.role or '')})"
            return f".get_by_role({self.string(spec.role or '')}, name={self.string(spec.name)}{exact})"
        method = {"label": "get_by_label", "placeholder": "get_by_placeholder", "text": "get_by_text", "alt_text": "get_by_alt_text", "title": "get_by_title"}.get(spec.strategy)
        if method is not None:
            return f".{method}({value}{exact})"
        return "".join(f".locator({self.string(selector)})" for selector in spec.selectors)

    # ------------------------------------------------------------------ file structure

    def header(self) -> None:
        spec = self.spec
        used = HELPERS.closure(self.helpers)
        lines = [
            '"""' + self.docstring(spec.title),
            "",
            f"Exported by Hercules from run {safe_id(self.run_id)} (case {safe_id(spec.case_id)}). It repeats what the run did, with the",
            "locators that matched only the element used during the run. Adapt it to this project's tests (fixtures,",
            "page objects, conftest.py) before keeping it, then run it with pytest (pytest-playwright).",
        ]
        if self.partial_note:
            lines.append(self.docstring(self.partial_note))
        if spec.browser:
            for name in browser_setup.secrets_of(spec):
                self.secrets.setdefault(name, "a browser option")
        engine = browser_setup.engine(spec)
        if spec.browser and engine != "chromium":
            lines.append(f"Run it in {engine.capitalize()}, as the run did: pytest --browser {engine}.")
        state = browser_setup.options_of(spec).storage_state if spec.browser else None
        env = [f"{BASE_URL_ENV} (optional)"] if "base" in used else []
        env += [f"{browser_setup.STORAGE_STATE_ENV} (optional)"] if state else []
        env += [f"{name} (required)" for name in sorted(self.secrets)]
        if env:
            lines.append(self.docstring("Environment: " + ", ".join(env) + "."))
        lines += ['"""', "", "from __future__ import annotations", ""]
        modules = {"os"} if ("base" in used or self.secrets or spec.browser) else set()
        if used & {"text"}:
            modules.add("json")
        generated = any(action.navigated_to and not action.navigated_from and address_pattern(action.navigated_to) for step in spec.steps for action in step.actions)
        if used & {"matches", "escape_url"} or generated or any(check.kind == "url" and check.match == "contains" for step in spec.steps for check in step.checks):
            modules.add("re")
        if "sqlite" in used:
            modules.add("sqlite3")
        if any(entry.kind == "builtin" for entry in spec.data.values()):
            modules.add("datetime")
        if self.timed:
            modules.add("time")
        if self.basic_auth:
            modules.add("base64")
        lines += [f"import {name}" for name in sorted(modules)]
        if self.api_used:
            lines.append("from collections.abc import Iterator")
        if "sqlite" in used:
            lines.append("from pathlib import Path")
        if used & {"text", "missing", "sqlite"} or self.saved_used:
            lines.append("from typing import Any")
        parse = sorted(
            {
                name
                for name, helper in (("urljoin", "url"), ("urlsplit", "wait"), ("parse_qsl", "with_query"), ("urlencode", "with_query"), ("urlsplit", "with_query"), ("urlunsplit", "with_query"))
                if helper in used
            }
        )
        if parse:
            lines.append(f"from urllib.parse import {', '.join(parse)}")
        lines.append("")
        if self.api_used or spec.browser:
            lines.append("import pytest")
        names = (["expect", "Page"] if spec.browser else []) + (["APIRequestContext", "Playwright"] if self.api_used else [])
        if spec.browser and (spec.device or _viewport(spec.viewport)) and "Playwright" not in names:
            names.append("Playwright")
        if names:
            lines.append(f"from playwright.sync_api import {', '.join(sorted(names))}")
        lines.append("")
        if "base" in used:
            lines.append(f"BASE_URL = os.environ.get({self.string(BASE_URL_ENV)}, {self.string(spec.base_url or '')})")
        builtins = [name for name, entry in spec.data.items() if entry.kind == "builtin"]
        if builtins:
            lines.append('TIMESTAMP = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")')
        if "run_id" in builtins:
            lines.append('RUN_ID = "run-" + TIMESTAMP')
        values = [(name, entry.value or "") for name, entry in spec.data.items() if entry.kind == "value"]
        if values:
            lines.append("DATA = {")
            for name, value in values:
                lines.append(f"    {self.string(name)}: {self.expr_builtins(value)},")
            lines.append("}")
        lines.append("")
        if spec.browser:
            lines += self.launch_fixture()
            viewport = _viewport(spec.viewport)
            if engine != "chromium":
                lines += ["", f"pytestmark = pytest.mark.only_browser({self.string(engine)})  # the browser the run used", ""]
            if spec.device or viewport is not None or self.context_lines():
                lines += ["", '@pytest.fixture(scope="session")', "def browser_context_args(browser_context_args: dict, playwright: Playwright) -> dict:"]
                lines.append(f'    """{self.docstring(("The phone or tablet the run emulated: " + spec.device) if spec.device else "The browser context the run used")}."""')
                lines.append("    args = {**browser_context_args}")
                if spec.device:
                    lines.append(f"    args.update(playwright.devices[{self.string(spec.device)}])")
                    lines.append('    args.pop("default_browser_type", None)  # Chromium, as the run used')
                if viewport is not None:
                    lines.append(f'    args["viewport"] = {{"width": {viewport[0]}, "height": {viewport[1]}}}')
                lines += self.context_lines()
                lines += ["    return args", ""]
        for line in HELPERS.emit(self.helpers - {"base"}):
            if line and not line.startswith((" ", ")")) and lines[-1] == "" and lines[-2] != "":
                lines.append("")  # two blank lines before each top-level helper
            lines.append(line)
        if self.api_used:
            lines += [
                "",
                "@pytest.fixture",
                "def api(playwright: Playwright) -> Iterator[APIRequestContext]:",
                '    """A request context of its own, as the run sent its API requests apart from the browser."""',
                "    context = playwright.request.new_context()",
                "    yield context",
                "    context.dispose()",
                "",
            ]
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", ""]
        self.lines = lines

    def uses_base(self) -> bool:
        return "base" in HELPERS.closure(self.helpers)

    @property
    def saved_used(self) -> bool:
        return any(entry.kind == "saved" for entry in self.spec.data.values())

    def expr_builtins(self, template: str) -> str:
        """A case value: only built-ins are substituted in it (as the run did)."""
        builtins = {name: entry for name, entry in self.spec.data.items() if entry.kind == "builtin"}
        return PythonWriter(self.spec.model_copy(update={"data": builtins}), run_id=self.run_id).expr(template)

    def open_test(self) -> None:
        self.timed = False
        self.basic_auth = False
        fixtures = [name for name, used in (("page: Page", self.spec.browser), ("api: APIRequestContext", self.api_used)) if used] or ["page: Page"]
        self.emit(f"def test_{snake(self.spec.case_id)}({', '.join(fixtures)}) -> None:")
        self.depth += 1
        self.emit(f'"""{self.docstring(self.spec.title)}"""')
        if self.saved_used:
            self.emit("saved: dict[str, Any] = {}")
        self.depth -= 1

    @staticmethod
    def docstring(text: str) -> str:
        """``text`` on one line, safe inside a triple-quoted docstring: backslashes and every double quote escaped."""
        return " ".join(one_line(text).split()).replace("\\", "\\\\").replace('"', '\\"')

    def close_test(self) -> None:
        return None

    def open_step(self, step: AutomationStep) -> None:
        self.emit("")
        self.emit(f"# Step {step.index}: {' '.join(one_line(step.action).split())}")
        self.depth -= 1  # Python steps are comments, not blocks

    def close_step(self, step: AutomationStep) -> None:
        self.depth += 1

    # ------------------------------------------------------------------ browser steps

    def action(self, step: AutomationStep, action: AutomationAction) -> None:
        self.action_notes(step, action)
        kind = action.kind
        if kind == "navigate":
            if action.url is None:
                self.todo(step, "the address this step opened was not recorded")
                return
            self.emit(f"page.goto({self.url_expr(action.url)})")
        elif kind == "wait_text":
            self.emit(f"expect(page.get_by_text({self.expr(action.text or '')}).first).to_be_visible(timeout={WAIT_TEXT_MS:_})")
        elif kind == "wait_time":
            self.emit(f"page.wait_for_timeout({int((action.seconds or 0) * 1000)})")
        elif kind == "press" and action.target is None:
            self.emit(f"page.keyboard.press({self.string(action.key or '')})")
        else:
            self.element_action(step, action)
        if action.navigated_to:
            self.helpers.add("wait")
            pattern = address_pattern(action.navigated_to)
            if pattern is not None and action.navigated_from:
                # The new address holds an id made for the run (a new record's page, which an app may also show under
                # another address): wait to leave this page; the next steps check what it shows.
                part = "urlsplit(address).path" if action.navigated_from.startswith("/") else 'urlsplit(address)._replace(query="", fragment="").geturl()'
                self.emit(f"page.wait_for_url(lambda address: {part} != {self.string(action.navigated_from)})")
            elif pattern is not None:  # a record id or number made for the run: the next run gets another
                part = "urlsplit(address).path" if action.navigated_to.startswith("/") else 'urlsplit(address)._replace(query="", fragment="").geturl()'
                self.emit(f"page.wait_for_url(lambda address: re.fullmatch({self.string(pattern)}, {part}) is not None)")
            elif action.navigated_to.startswith("/"):
                self.emit(f"page.wait_for_url(lambda address: urlsplit(address).path == {self.string(action.navigated_to)})")
            else:
                self.emit(f'page.wait_for_url(lambda address: urlsplit(address)._replace(query="", fragment="").geturl() == {self.string(action.navigated_to)})')

    def element_action(self, step: AutomationStep, action: AutomationAction) -> None:
        kind = action.kind
        locator = self.target_expr(step, action.target, kind)
        if kind in ("click", "hover", "check", "uncheck"):
            self.emit(f"{locator}.{kind}()")
        elif kind == "press":
            self.emit(f"{locator}.press({self.string(action.key or '')})")
        elif kind == "fill":
            if action.value is None:
                self.todo(step, "the value typed here was not recorded (it held a secret not referenced by name)")
                return
            self.emit(f"{locator}.fill({self.expr(action.value)})")
        elif kind == "select":
            self.select(step, action, locator)

    def select(self, step: AutomationStep, action: AutomationAction, locator: str) -> None:
        if action.option is None:
            self.todo(step, "the option chosen here was not recorded")
            return
        option = self.expr(action.option)
        if action.select_by == "label":
            self.emit(f"{locator}.select_option(label={option})")
        elif action.select_by == "value":
            self.emit(f"{locator}.select_option({option})")
        else:
            # A custom drop-down's list opens next to it: centred, the list fits on screen (clicking an option the page
            # would scroll to closes many such lists).
            self.emit(f"{locator}.evaluate(\"element => element.scrollIntoView({{block: 'center'}})\")")
            self.emit(f"{locator}.click()")
            if action.typed and action.value is not None:
                self.emit(f"{locator}.press_sequentially({self.expr(action.value)})")
            if action.select_by == "option_role":
                exact = ", exact=True" if action.option_exact else ""
                self.emit(f"page.get_by_role({self.string(action.option_role or 'option')}, name={option}{exact}).click()")
            else:
                self.emit(f"page.get_by_text({option}, exact=True).last.click()")

    def check(self, step: AutomationStep, check: AutomationCheck) -> None:
        if check.kind in ("semantic", "visual"):
            how = "by its meaning" if check.kind == "semantic" else "on a screenshot"
            self.note(f"Judged {how} by a model during the run: {json.dumps(check.expected, ensure_ascii=False)}")
            if check.note:
                self.note(f"The run's verdict: {check.note.split('; write')[0]}")
            if check.observed:
                self.note(f"Observed: {check.observed}")
            self.todo(step, f"assert in code: {' '.join(check.expected.split())[:160]}")
            return
        if not check.faithful and check.note:
            self.todo(step, check.note)
        expected = self.expr(check.expected)
        scope = self.scope_expr(check.target.frames) if check.target is not None and check.kind in ("text", "not_text") else "page"
        if check.kind == "text":
            if check.form in ("text", "text_first"):
                self.emit(f"expect({scope}.get_by_text({expected}){'.first' if check.form == 'text_first' else ''}).to_be_visible()")
            else:
                self.unproven(check, f'expect({scope}.locator("body")).to_contain_text({expected}, ignore_case=True)')
        elif check.kind == "not_text":
            if check.form == "count_zero":
                self.emit(f"expect(page.get_by_text({expected})).to_have_count(0)")
            else:
                self.unproven(check, f'expect(page.locator("body")).not_to_contain_text({expected}, ignore_case=True)')
        elif check.kind == "url":
            if check.match == "equals":
                value, _ = self.url_check_value(check)
                self.emit(f"expect(page).to_have_url({value})")
            else:
                self.emit(f"expect(page).to_have_url(re.compile(re.escape({expected})))")
        elif check.kind == "visible_text":
            self.emit(f"expect(page.get_by_text({expected}).first).to_be_visible()")
        elif check.kind == "element_visible":
            self.emit(f"expect({self.target_expr(step, check.target, 'check')}).to_be_visible()")

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
                self.timed = True
                self.emit("started = time.monotonic()")
            self.emit(f"response = api.fetch({self.request_url(call)}, {self.request_options(step, call)})")
            self.response_seq += 1
            if timed:
                self.emit("elapsed_ms = (time.monotonic() - started) * 1000")
        if expect is not None:
            self.api_checks(step, call)
        for capture in call.save:
            if capture.header is not None:
                self.helpers.add("missing")
                self.emit(f"saved[{self.string(capture.name)}] = response.headers.get({self.string(capture.header.lower())}, MISSING)")
                continue
            keys = json_path_keys(capture.json_path or "$")
            if keys is None:
                self.todo(step, f"save {capture.json_path} as {{{{{capture.name}}}}} (a JSONPath this export does not translate)")
                continue
            self.parse_body()
            self.helpers.add("at")
            self.emit(f"saved[{self.string(capture.name)}] = at(body, {self.keys_literal(keys)})")

    def parse_body(self) -> None:
        if self.body_seq != self.response_seq:
            self.emit("body = response.json()")
            self.body_seq = self.response_seq

    def request_url(self, call: ApiCall) -> str:
        address = self.api_url_expr(call.path or "")
        if not call.query:
            return address
        self.helpers.add("with_query")
        pairs: list[str] = []
        for name, value in call.query.items():
            for item in value if isinstance(value, list) else [value]:
                pairs.append(f"({self.expr(name)}, {self.expr(item)})")
        return f"with_query({address}, [{', '.join(pairs)}])"

    def request_options(self, step: AutomationStep, call: ApiCall) -> str:
        headers = [(name, self.expr(value)) for name, value in call.headers.items()]
        if call.auth is not None:
            token = self.expr(call.auth.value)
            if call.auth.type == "basic":
                self.basic_auth = True
                headers.append(("Authorization", f'"Basic " + base64.b64encode(({token}).encode()).decode()'))
            else:
                headers.append(("Authorization", f'"Bearer " + {token}'))
        options: list[str] = [f"method={self.string(call.method)}"]
        body = self.request_body(step, call, headers)
        if headers:
            options.append(f"headers={self.dict_literal(headers)}")
        options.extend(body)
        options.append("max_redirects=0")
        return ", ".join(options)

    def request_body(self, step: AutomationStep, call: ApiCall, headers: list[tuple[str, str]]) -> list[str]:
        body, mode = call.body, call.body_mode
        if body is None:
            return []
        has_type = any(name.lower() == "content-type" for name, _ in headers)
        mode = mode or ("raw" if isinstance(body, str) else "json")
        if mode == "json":
            if isinstance(body, str):
                self.helpers.add("text")
                if not has_type:
                    headers.append(("Content-Type", self.string("application/json")))
                return [f"data=json.dumps({self.expr(body, typed=True)})"]
            return [f"data={self.json_value(body)}"]
        if mode == "raw":
            if isinstance(body, str) and call.body_mode is None and not has_type:
                headers.append(("Content-Type", self.string(text_content_type(body))))
            if not isinstance(body, str):
                self.helpers.add("text")
            return [f"data={self.expr(body) if isinstance(body, str) else f'json.dumps({self.json_value(body)})'}"]
        if mode == "urlencoded" and isinstance(body, dict):
            return [f"form={self.dict_literal([(str(key), self.expr(value) if isinstance(value, str) else self.json_value(value)) for key, value in body.items()])}"]
        if mode == "multipart" and isinstance(body, dict) and not any(isinstance(value, dict) for value in body.values()):
            self.helpers.add("text")
            return [f"multipart={self.dict_literal([(str(key), self.expr(value) if isinstance(value, str) else f'text({self.json_value(value)})') for key, value in body.items()])}"]
        self.todo(step, f"send the {mode} body this step sent (files and binary bodies are not exported)")
        return []

    def api_checks(self, step: AutomationStep, call: ApiCall) -> None:
        expect = call.expect
        assert expect is not None
        if expect.status is not None:
            codes = status_codes(expect.status)
            self.emit(f"assert {self.status_condition('response.status', codes)}, {self.string('HTTP status ' + ' or '.join(codes))}")
        for label, reference in (("JSON Schema", expect.json_schema if isinstance(expect.json_schema, str) else ("inline schema" if expect.json_schema else None)), ("OpenAPI", expect.openapi)):
            if reference:
                self.todo(step, f"validate the response body against the {label} {reference} with your project's validator")
        if expect.json_path:
            self.parse_body()
        for item in expect.json_path:
            condition = self.json_condition("body", item.path, item.op, item.value, "value" in item.model_fields_set)
            self.assertion(step, condition, f"{item.path} {item.op}" + ("" if item.op in ("exists", "not_exists", "not_empty") else f" {json.dumps(item.value, ensure_ascii=False)}"))
        for header in expect.headers:
            self.helpers.add("missing")
            actual = f"response.headers.get({self.string(header.name.lower())}, MISSING)"
            value = self.expr(header.value) if header.value is not None else self.null
            self.assertion(step, self.operator_condition(actual, header.op, value), f"header {header.name} {header.op}")
        for text in expect.body_contains:
            self.emit(f"assert {self.expr(text)} in response.text(), {self.string('body contains ' + text)}")
        if expect.max_time_ms is not None and call.path is None:
            self.todo(step, f"check the response time is under {expect.max_time_ms} ms (this step sent no request of its own to time)")
        elif expect.max_time_ms is not None:
            self.emit(f"assert elapsed_ms < {expect.max_time_ms}, {self.string(f'response time under {expect.max_time_ms} ms')}")

    def assertion(self, step: AutomationStep, condition: str | None, message: str) -> None:
        if condition is None:
            self.todo(step, f"check {message} (a JSONPath this export does not translate)")
            return
        self.emit(f"assert {condition}, {self.string(message)}")

    def sql_step(self, step: AutomationStep) -> None:
        call = self.sql_call(step)
        if call is None:
            self.todo(step, "the query could not be read back from the run")
            return
        if self.masked(step, step.sql):
            return
        if step.sql_dialect != "sqlite":
            self.note(f"The run queried {{{{{call.db}}}}} ({step.sql_dialect or 'unknown dialect'}): {' '.join((call.query or '(the previous query)').split())}")
            if call.expect is not None:
                self.note(f"and checked: {json.dumps(call.expect.model_dump(mode='json', exclude_defaults=True), ensure_ascii=False)}")
            self.todo(step, "query this database with your project's client (read-only), then check its rows")
            return
        if call.query is not None:
            self.helpers.add("sqlite")
            params = self.dict_literal([(name, self.json_value(value)) for name, value in call.params.items()])
            self.emit(f"rows = sqlite_rows({self.expr('{{' + (call.db or '') + '}}')}, {self.expr(call.query)}, {params})")
        expect = call.expect
        if expect is not None:
            for operator, bound in (("==", expect.rows), (">=", expect.min_rows), ("<=", expect.max_rows)):
                if bound is not None:
                    self.emit(f"assert len(rows) {operator} {bound}, {self.string(f'row count {operator} {bound}')}")
            for item in expect.json_path:
                condition = self.json_condition("rows", item.path, item.op, item.value, "value" in item.model_fields_set)
                self.assertion(step, condition, f"rows {item.path} {item.op}" + ("" if item.op in ("exists", "not_exists", "not_empty") else f" {json.dumps(item.value, ensure_ascii=False)}"))
        for capture in call.save:
            keys = json_path_keys(capture.json_path or "$")
            if keys is None:
                self.todo(step, f"save {capture.json_path} as {{{{{capture.name}}}}} (a JSONPath this export does not translate)")
                continue
            self.helpers.add("at")
            self.emit(f"saved[{self.string(capture.name)}] = at(rows, {self.keys_literal(keys)})")

    def quality_step(self, step: AutomationStep) -> None:
        check = step.quality or {}
        kind = check.get("kind")
        self.note(f"The run checked the page's {'accessibility' if kind == 'a11y' else 'look against a baseline' if kind == 'visual' else 'layout'}: {json.dumps(check, ensure_ascii=False)}")
        self.todo(step, f"replace this {kind} check with your project's tools")
