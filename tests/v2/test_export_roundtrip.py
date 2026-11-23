"""Round trip: run a case in real Chromium, export it, run the exported test against the same app, and it passes.

The Demo Shop (``bench/apps``) checkout runs with real perception and model stand-ins (a scripted step
reader and a label-matching decider): sign-in with a secret, a coupon in a shadow-DOM widget, a native
select, a radio, a date, card fields in an iframe, a modal, a check a model judges by meaning, and one
text check no Playwright assertion can express (a closed drop-down's option), which the export comments
out with a TODO. The API and SQL case runs against the local pet store and a SQLite file without a browser.

The exported Python file runs under pytest in its own process; without pytest-playwright a stand-in
``conftest.py`` provides its ``page`` and ``playwright`` fixtures (see ``export_harness``). The TypeScript
file is parsed with the TypeScript compiler, and type-checked when ``HERCULES_TEST_PLAYWRIGHT_NODE_MODULES``
names a folder holding ``@playwright/test``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.models import RunResult, TestCase
from testzeus_hercules.engine.ports import DefaultPerceiver
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.export import export_run
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import LabelDecider, ScriptedInterpreter, clicks, fills, intent, opens, sees
from tests.v2.export_harness import REPO, UI_TYPESCRIPT, node, playwright_types, run_exported_python, typescript_syntax_errors, typescript_type_errors
from tests.v2.fixtures.api.app import API_KEY, serve_api
from tests.v2.test_engine_api_e2e import NoBrowser

PASSWORD = "demo1234"

STEPS = [
    ('I open "/login.html"', opens("/login.html"), None, None),
    ('I fill "{{email}}" in "Email"', fills("Email", "{{email}}"), None, None),
    ('I fill "{{password}}" in "Password"', fills("Password", "{{password}}"), None, None),
    ('I click the "Sign in" button', clicks("Sign in", role="button"), 'I should see "Signed in as {{email}}"', sees("Signed in as {{email}}")),
    ('I open "/product.html?id=1"', opens("/product.html?id=1"), None, None),
    ('I click the "Add to cart" button', clicks("Add to cart", role="button"), 'I should see "Cart (1)"', sees("Cart (1)")),
    ('I open "/cart.html"', opens("/cart.html"), None, None),
    ('I fill "SAVE10" in "Coupon code"', fills("Coupon code", "SAVE10"), None, None),
    ('I click the "Apply" button', clicks("Apply", role="button"), 'I should see "Coupon applied: 10% off"', sees("Coupon applied: 10% off")),
    (
        'I click the "Proceed to checkout" link',
        clicks("Proceed to checkout", role="link"),
        'the url should contain "/checkout.html"',
        intent("assert_url", expected="/checkout.html", match="contains"),
    ),
    ('I fill "{{full_name}}" in "Full name"', fills("Full name", "{{full_name}}"), None, None),
    ('I fill "{{email}}" in the checkout "Email"', fills("Email", "{{email}}"), None, None),
    ('I fill "{{phone}}" in "Phone"', fills("Phone", "{{phone}}"), None, None),
    ('I fill "{{street}}" in "Street address"', fills("Street address", "{{street}}"), None, None),
    ('I select "Canada" from the "Country" dropdown', intent("select", "Country", value="Canada"), None, None),
    ('I click the "Express shipping" radio button', intent("check", "Express shipping ($15.00)", role="radio"), None, None),
    ('I fill "2030-01-15" in "Delivery date"', fills("Delivery date", "2030-01-15"), 'I should see "Delivery date: 2030-01-15"', sees("Delivery date: 2030-01-15")),
    ('I fill "4242 4242 4242 4242" in "Card number"', fills("Card number", "4242 4242 4242 4242"), None, None),
    ('I fill "12/30" in "Expiry (MM/YY)"', fills("Expiry (MM/YY)", "12/30"), None, None),
    ('I fill "123" in "CVC"', fills("CVC", "123"), None, None),
    ('I click the "Save card" button', clicks("Save card", role="button"), 'I should see "Card saved"', sees("Card saved")),
    ("verify", None, 'I should see "Payment method: card ending 4242"', sees("Payment method: card ending 4242")),
    (
        'I check "I agree to the terms and conditions"',
        intent("check", "I agree to the terms and conditions"),
        'I should not see "Choose a country"',
        intent("assert_not_text", expected="Choose a country"),
    ),
    ('I click the "Place order" button', clicks("Place order", role="button"), 'I should see "Confirm your order"', sees("Confirm your order")),
    ('I click the "Confirm" button', clicks("Confirm", role="button"), "the order is confirmed", None),
    ("verify", None, 'the url should contain "/success.html"', intent("assert_url", expected="/success.html", match="contains")),
]


def shop_case(base_url: str) -> TestCase:
    steps = []
    for action, _, expected, _ in STEPS:
        step: dict[str, Any] = {"action": action}
        if expected:
            step["expected"] = expected
            step["semantic"] = expected == "the order is confirmed"
        steps.append(step)
    data = {"email": "demo@example.com", "password": {"secret": "DEMO_SHOP_PASSWORD"}, "full_name": "Sam Lee", "phone": "555 0100 200", "street": "1 Main Street"}
    return TestCase.model_validate({"id": "shop-checkout", "title": "Shopper pays by card with a coupon", "environment": {"base_url": base_url}, "test_data": data, "steps": steps})


def readings() -> dict[str, Any]:
    found: dict[str, Any] = {}
    for action, read, expected, check in STEPS:
        if read is not None:
            found[action] = read
        if check is not None and expected is not None:
            found[expected] = check
    return found


def run(case: TestCase, tmp_path: Path, deps: RunDeps) -> RunResult:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
    ctx = RunContext.create(settings, telemetry=TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport()))
    return asyncio.run(run_case(case, ctx, deps))


def check_typescript(code: str, tmp_path: Path) -> None:
    if node() is not None and UI_TYPESCRIPT.is_dir():
        assert typescript_syntax_errors(code, tmp_path / "ts-parse") == []
    types = playwright_types()
    if types is not None and node() is not None:
        assert typescript_type_errors(code, tmp_path / "ts-types", types) == ""


def test_the_exported_shop_checkout_passes_against_the_shop(tmp_path: Path) -> None:
    chromium = require_chromium()
    with serve_fixtures(REPO / "bench" / "apps") as base:
        deps = RunDeps(decider=LabelDecider(), interpreter=ScriptedInterpreter(readings()), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium, "DEMO_SHOP_PASSWORD": PASSWORD})
        result = run(shop_case(base), tmp_path, deps)
        assert result.status == "passed", result.summary_text()
        python = export_run(result, "playwright-py")
        done = run_exported_python(python.code, tmp_path / "exported", env={"BASE_URL": base, "DEMO_SHOP_PASSWORD": PASSWORD}, chromium=chromium)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert "1 passed" in done.stdout
    assert PASSWORD not in python.code and 'os.environ["DEMO_SHOP_PASSWORD"]' in python.code
    assert 'content_frame.get_by_role("textbox", name="Card number", exact=True)' in python.code, "the card field is reached through its iframe"
    assert '# expect(page.locator("body")).not_to_contain_text("Choose a country"' in python.code, "an assertion no form expresses is commented out"
    assert [note.split(":")[0] for note in python.notes] == ["step 23", "step 25"]
    typescript = export_run(result, "playwright-ts")
    assert PASSWORD not in typescript.code and 'env("DEMO_SHOP_PASSWORD")' in typescript.code
    check_typescript(typescript.code, tmp_path)


def test_the_exported_api_and_sql_case_passes_against_the_api(tmp_path: Path) -> None:
    database = tmp_path / "shop.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE pets (id INTEGER, name TEXT, tag TEXT)")
        connection.executemany("INSERT INTO pets VALUES (?, ?, ?)", [(1, "Rex", "dog"), (2, "Tom", "cat"), (3, "Fido", "dog")])
    env = {"PETS_API_KEY": API_KEY, "APP_DB_URL": f"sqlite:///{database}"}
    with serve_api() as base:
        case = TestCase.model_validate(
            {
                "id": "pets-api",
                "title": "Create a pet and read it back",
                "environment": {"base_url": base},
                "test_data": {"api_key": {"secret": "PETS_API_KEY"}, "db": {"secret": "APP_DB_URL"}, "kind": "dog"},
                "steps": [
                    {
                        "action": "Create a pet",
                        "api": {
                            "method": "POST",
                            "path": "/pets",
                            "headers": {"X-API-Key": "{{api_key}}"},
                            "body": {"name": "Bella {{run_id}}", "tag": "{{kind}}"},
                            "expect": {
                                "status": 201,
                                "json_path": [{"path": "$.name", "op": "contains", "value": "Bella"}, {"path": "$.id", "op": "gt", "value": 2}],
                                "headers": [{"name": "Location", "op": "exists"}],
                                "max_time_ms": 5000,
                            },
                            "save": [{"name": "pet_id", "json_path": "$.id"}],
                        },
                    },
                    {
                        "action": "Read the pet back",
                        "api": {
                            "path": "/pets/{{pet_id}}",
                            "expect": {
                                "status": "2xx",
                                "json_path": [{"path": "$.id", "op": "eq", "value": "{{pet_id}}"}, {"path": "$['tag']", "op": "eq", "value": "dog"}],
                                "body_contains": ["Bella"],
                            },
                        },
                    },
                    {
                        "action": "List one pet",
                        "api": {
                            "path": "/pets",
                            "query": {"limit": "1"},
                            "expect": {"status": [200, 203], "json_path": [{"path": "$", "op": "count_eq", "value": 1}, {"path": "$[0].name", "op": "not_empty"}]},
                        },
                    },
                    {
                        "action": "The dogs are stored",
                        "sql": {
                            "db": "db",
                            "query": "SELECT name FROM pets WHERE tag = :tag ORDER BY id",
                            "params": {"tag": "{{kind}}"},
                            "expect": {"rows": 2, "json_path": [{"path": "$[0].name", "op": "eq", "value": "Rex"}]},
                        },
                    },
                ],
            }
        )
        result = run(case, tmp_path, RunDeps(decider=LabelDecider(), launcher=NoBrowser(), env=env))
        assert result.status == "passed", result.summary_text()
        python = export_run(result, "playwright-py")
        done = run_exported_python(python.code, tmp_path / "exported", env={"BASE_URL": base, **env}, chromium=None)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert API_KEY not in python.code and str(database) not in python.code
    assert python.notes == ()
    check_typescript(export_run(result, "playwright-ts").code, tmp_path)


def test_a_failed_run_is_exported_only_up_to_its_last_passed_step(tmp_path: Path) -> None:
    chromium = require_chromium()
    with serve_fixtures(REPO / "bench" / "apps") as base:
        case = shop_case(base)
        broken = case.model_copy(update={"steps": case.steps[:4] + [case.steps[4].model_copy(update={"expected": 'I should see "Out of stock forever"'})]})
        reading = {**readings(), 'I should see "Out of stock forever"': sees("Out of stock forever")}
        deps = RunDeps(decider=LabelDecider(), interpreter=ScriptedInterpreter(reading), perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium, "DEMO_SHOP_PASSWORD": PASSWORD})
        result = run(broken, tmp_path, deps)
        assert result.status == "failed"
        with pytest.raises(Exception, match="only passed runs are exported"):
            export_run(result, "playwright-py")
        partial = export_run(result, "playwright-py", allow_failed=True)
        assert partial.partial and partial.steps_exported == 4
        done = run_exported_python(partial.code, tmp_path / "exported", env={"BASE_URL": base, "DEMO_SHOP_PASSWORD": PASSWORD}, chromium=chromium)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
