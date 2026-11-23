"""Running exported tests: a pytest-playwright stand-in, the exported Python file under pytest, and TypeScript checks.

pytest-playwright is not a Hercules dependency. When it is not installed, :func:`run_exported_python`
writes a ``conftest.py`` next to the exported file that provides the ``playwright`` and ``page`` fixtures
it would (the installed ``playwright`` package, headless Chromium), so the exported file runs unchanged.
TypeScript exports are type-checked against ``@playwright/test`` when ``HERCULES_TEST_PLAYWRIGHT_NODE_MODULES``
names a ``node_modules`` folder holding it, and otherwise parsed with the TypeScript compiler in ``ui/node_modules``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UI_TYPESCRIPT = REPO / "ui" / "node_modules" / "typescript"

CONFTEST = '''\
"""Stand-in for pytest-playwright's fixtures (written by the Hercules export tests)."""

import os

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def playwright():
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="session")
def browser(playwright):
    launched = playwright.chromium.launch(executable_path=os.environ.get("HERCULES_EXPORT_CHROMIUM") or None, headless=True)
    yield launched
    launched.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    yield context.new_page()
    context.close()
'''


def has_pytest_playwright() -> bool:
    return importlib.util.find_spec("pytest_playwright") is not None


def run_exported_python(code: str, directory: Path, *, env: dict[str, str], chromium: str | None, filename: str = "test_exported.py") -> subprocess.CompletedProcess[str]:
    """Run an exported pytest-playwright file with pytest in its own process; returns the completed process."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(code, encoding="utf-8")
    (directory / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    if not has_pytest_playwright():
        (directory / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("HERCULES_", "PYTEST_"))}
    environment.update(env)
    if chromium:
        environment["HERCULES_EXPORT_CHROMIUM"] = chromium
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-c", str(directory / "pytest.ini"), "--rootdir", str(directory), str(directory / filename)]
    return subprocess.run(command, cwd=directory, env=environment, capture_output=True, text=True, timeout=300)


def node() -> str | None:
    return shutil.which("node")


def playwright_types() -> Path | None:
    """A node_modules folder holding @playwright/test (for type checks), from HERCULES_TEST_PLAYWRIGHT_NODE_MODULES."""
    configured = os.environ.get("HERCULES_TEST_PLAYWRIGHT_NODE_MODULES")
    if configured and (Path(configured) / "@playwright" / "test" / "package.json").is_file():
        return Path(configured)
    return None


_PARSE = """
const ts = require(process.argv[2]);
const fs = require("fs");
const source = fs.readFileSync(process.argv[3], "utf8");
const out = ts.transpileModule(source, { reportDiagnostics: true, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const problems = (out.diagnostics || []).map((d) => ts.flattenDiagnosticMessageText(d.messageText, "\\n"));
process.stdout.write(JSON.stringify(problems));
"""


def typescript_syntax_errors(code: str, directory: Path) -> list[str]:
    """Syntax errors the TypeScript compiler reports for ``code`` (transpiled on its own, no types)."""
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "exported.spec.ts"
    source.write_text(code, encoding="utf-8")
    script = directory / "parse.cjs"
    script.write_text(_PARSE, encoding="utf-8")
    done = subprocess.run([node() or "node", str(script), str(UI_TYPESCRIPT), str(source)], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(done.stderr)
    return list(json.loads(done.stdout or "[]"))


def typescript_type_errors(code: str, directory: Path, node_modules: Path) -> str:
    """``tsc --noEmit`` output for ``code`` against @playwright/test in ``node_modules`` ("" when it type-checks)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "exported.spec.ts").write_text(code, encoding="utf-8")
    link = directory / "node_modules"
    if not link.exists():
        link.symlink_to(node_modules, target_is_directory=True)
    # Playwright projects have @types/node; when the folder lacks it, declare the one Node global the exports use.
    has_node_types = (node_modules / "@types" / "node" / "package.json").is_file()
    if not has_node_types:
        (directory / "node-globals.d.ts").write_text("declare const process: { env: Record<string, string | undefined> };\n", encoding="utf-8")
    config = {
        "compilerOptions": {
            "target": "ES2022",
            "module": "commonjs",
            "moduleResolution": "node",
            "strict": True,
            "noEmit": True,
            "lib": ["ES2022", "DOM"],
            "skipLibCheck": True,
            "types": ["node"] if has_node_types else [],
        },
        "files": ["exported.spec.ts"] + ([] if has_node_types else ["node-globals.d.ts"]),
    }
    (directory / "tsconfig.json").write_text(json.dumps(config), encoding="utf-8")
    done = subprocess.run([node() or "node", str(UI_TYPESCRIPT / "bin" / "tsc"), "-p", str(directory / "tsconfig.json")], capture_output=True, text=True, timeout=300)
    return (done.stdout + done.stderr).strip()
