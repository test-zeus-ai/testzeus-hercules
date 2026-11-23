"""Exporters: golden files for Playwright Test (TypeScript) and pytest-playwright (Python), refusals and partial exports.

The golden files in ``fixtures/export`` are the expected output for one spec that uses every construct
(frames, shadow paths, test ids, secrets, built-ins, saved values, API, SQL and quality steps, judged
checks). Regenerate them after an intended change with ``HERCULES_UPDATE_GOLDEN=1``. The Python golden
must compile, the TypeScript golden must parse with the TypeScript compiler (and type-check against
``@playwright/test`` when ``HERCULES_TEST_PLAYWRIGHT_NODE_MODULES`` names a folder holding it).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationData, AutomationSpec, AutomationStep, FrameSpec, LocatorSpec, TargetSpec
from testzeus_hercules.cases.models import RunResult, StepResult
from testzeus_hercules.export import ExportError, export_run
from testzeus_hercules.export.common import address_pattern, json_path_keys, template_parts

from tests.v2.export_harness import UI_TYPESCRIPT, node, playwright_types, typescript_syntax_errors, typescript_type_errors

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "export"
SECRET = "hunter2-secret"


def role(name: str, kind: str = "button") -> LocatorSpec:
    return LocatorSpec(strategy="role", role=kind, name=name)


def spec() -> AutomationSpec:
    frame = FrameSpec(locator=LocatorSpec(strategy="css", selectors=['iframe[title="Card details"]']), label="Card details")
    steps = [
        AutomationStep(index=1, action='I open "/login"', status="passed", actions=[AutomationAction(kind="navigate", url="/login")]),
        AutomationStep(
            index=2,
            action="I sign in",
            status="passed",
            actions=[
                AutomationAction(
                    kind="fill",
                    value="{{email}}",
                    target=TargetSpec(
                        role="textbox", name="Email", locator=LocatorSpec(strategy="test_id", attribute="data-testid", value="email"), fallbacks=[LocatorSpec(strategy="label", value="Email")]
                    ),
                ),
                AutomationAction(kind="fill", value="{{password}}", target=TargetSpec(role="textbox", name="Password", locator=LocatorSpec(strategy="label", value="Password"))),
                AutomationAction(kind="click", target=TargetSpec(role="button", name="Sign in", locator=role("Sign in")), navigated_to="/dashboard"),
            ],
            checks=[AutomationCheck(kind="text", expected="Welcome, {{name}}", form="text"), AutomationCheck(kind="url", expected="/dashboard")],
        ),
        AutomationStep(
            index=3,
            action="Choose options",
            status="passed",
            actions=[
                AutomationAction(kind="select", value="{{country}}", option="{{country}}", select_by="label", target=TargetSpec(role="combobox", name="Country", locator=role("Country", "combobox"))),
                AutomationAction(
                    kind="select", value="Blue", option="blue", select_by="value", target=TargetSpec(role="combobox", name="Colour", locator=LocatorSpec(strategy="css", selectors=["#colour"]))
                ),
                AutomationAction(
                    kind="select",
                    value="Peter Pan",
                    option="Peter Pan",
                    select_by="option_role",
                    option_role="option",
                    typed=True,
                    target=TargetSpec(role="combobox", name="Book", locator=role("Book", "combobox")),
                ),
                AutomationAction(kind="check", target=TargetSpec(role="checkbox", name="I agree", locator=role("I agree", "checkbox"))),
                AutomationAction(kind="hover", target=TargetSpec(role="link", name="Help", locator=LocatorSpec(strategy="test_id", attribute="data-qa", value="help"))),
                AutomationAction(kind="press", key="Enter", target=TargetSpec(role="searchbox", name="Search", locator=LocatorSpec(strategy="placeholder", value="Search"))),
                AutomationAction(kind="press", key="Escape"),
                AutomationAction(kind="wait_text", text="Saved"),
                AutomationAction(kind="wait_time", seconds=1.5, note="the step waits a fixed time"),
            ],
            checks=[
                AutomationCheck(kind="text", expected="Items", form="text_first"),
                AutomationCheck(kind="not_text", expected="Error", form="count_zero"),
                AutomationCheck(kind="not_text", expected="Choose a country", form=None, faithful=False, note="no Playwright text assertion held on the page; check this text by hand"),
                AutomationCheck(kind="visible_text", expected="Profile"),
                AutomationCheck(kind="url", expected="http://shop.test/account", match="equals"),
            ],
        ),
        AutomationStep(
            index=4,
            action="Pay by card",
            status="passed",
            actions=[
                AutomationAction(kind="fill", value="4242 4242 4242 4242", target=TargetSpec(role="textbox", name="Card number", frames=[frame], locator=role("Card number", "textbox"))),
                AutomationAction(kind="click", target=TargetSpec(role="button", name="Apply", locator=LocatorSpec(strategy="css", selectors=["#cart > coupon-box", "#apply"]))),
                AutomationAction(kind="click", target=TargetSpec(role="button", name="Remove"), faithful=False, note='no locator matched only this element (button "Remove")'),
            ],
            checks=[
                AutomationCheck(kind="text", expected="Card saved", form="text", target=TargetSpec(frames=[frame])),
                AutomationCheck(kind="element_visible", expected="Receipt", target=TargetSpec(role="link", name="Receipt", locator=role("Receipt", "link"))),
                AutomationCheck(
                    kind="semantic",
                    expected="the order is confirmed",
                    source="llm",
                    observed='page "Thanks" at /success',
                    faithful=False,
                    note="judged by meaning by jev: 93% likely to hold; write an assertion for it in code",
                ),
            ],
            faithful=False,
        ),
        AutomationStep(
            index=5,
            action="Create a pet",
            status="passed",
            kind="api",
            api={
                "method": "POST",
                "path": "/pets",
                "headers": {"X-API-Key": "{{api_key}}"},
                "query": {"dry": "no", "tags": ["a", "b"]},
                "body": {"name": "Bella {{run_id}}", "owner": "{{email}}", "age": 2, "vaccinated": True, "notes": None},
                "auth": {"type": "bearer", "value": "{{token}}"},
                "expect": {
                    "status": 201,
                    "json_path": [
                        {"path": "$.name", "op": "contains", "value": "Bella"},
                        {"path": "$.id", "op": "gt", "value": 0},
                        {"path": "$.items[*].price", "op": "all_gte", "value": 0},
                        {"path": "$.missing", "op": "not_exists"},
                    ],
                    "headers": [{"name": "Location", "op": "exists"}],
                    "body_contains": ["Bella"],
                    "max_time_ms": 2000,
                    "json_schema": "spec.yaml#/components/schemas/Pet",
                },
                "save": [{"name": "pet_id", "json_path": "$.id"}, {"name": "where", "header": "Location"}],
            },
        ),
        AutomationStep(
            index=6,
            action="Read it back",
            status="passed",
            kind="api",
            api={
                "path": "/pets/{{pet_id}}",
                "auth": {"type": "basic", "value": "admin:{{password}}"},
                "expect": {"status": ["2xx", 304], "json_path": [{"path": "$['id']", "op": "eq", "value": "{{pet_id}}"}, {"path": "$.tags", "op": "count_eq", "value": 2}]},
            },
        ),
        AutomationStep(index=7, action="Send a note", status="passed", kind="api", api={"method": "POST", "path": "https://hooks.test/note", "body": "hello {{name}}", "expect": {"status": 200}}),
        AutomationStep(
            index=8,
            action="The dogs are stored",
            status="passed",
            kind="sql",
            sql_dialect="sqlite",
            sql={"db": "db", "query": "SELECT name FROM pets WHERE tag = :tag", "params": {"tag": "dog"}, "expect": {"rows": 2, "json_path": [{"path": "$[0].name", "op": "eq", "value": "Rex"}]}},
        ),
        AutomationStep(
            index=9,
            action="Accessible",
            status="passed",
            kind="quality",
            quality={"kind": "a11y"},
            faithful=False,
            notes=["the a11y check runs Hercules' own checks; the export writes it as a comment to replace with your project's tools"],
        ),
    ]
    data = {
        "api_key": AutomationData(kind="secret", env="PETS_API_KEY"),
        "country": AutomationData(kind="value", value="Canada"),
        "db": AutomationData(kind="secret", env="APP_DB_URL"),
        "email": AutomationData(kind="value", value="user+{{run_id}}@shop.test"),
        "name": AutomationData(kind="value", value="Sam"),
        "password": AutomationData(kind="secret", env="APP_PASSWORD"),
        "pet_id": AutomationData(kind="saved", step=5),
        "run_id": AutomationData(kind="builtin"),
        "token": AutomationData(kind="secret", env="APP_TOKEN"),
    }
    return AutomationSpec(case_id="golden-case", title="Every construct", base_url="http://shop.test", data=data, steps=steps)


def result(status: str = "passed", automation: AutomationSpec | None = None, step_statuses: list[str] | None = None) -> RunResult:
    automation = automation if automation is not None else spec()
    statuses = step_statuses or ["passed"] * len(automation.steps)
    steps = [StepResult(index=step.index, action=step.action, status=state) for step, state in zip(automation.steps, statuses)]  # type: ignore[arg-type]
    if status != "passed":
        automation = automation.model_copy(update={"steps": [step.model_copy(update={"status": state}) for step, state in zip(automation.steps, statuses)]})
    return RunResult(run_id="r_20261026_120000_abcdef", status=status, case_id=automation.case_id, steps=steps, automation=automation)  # type: ignore[arg-type]


def check_golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("HERCULES_UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, f"{name} changed; regenerate with HERCULES_UPDATE_GOLDEN=1 if intended"


def test_typescript_golden() -> None:
    exported = export_run(result(), "playwright-ts")
    check_golden("golden.spec.ts", exported.code)
    assert exported.filename == "tests/golden-case.spec.ts"
    assert [item.name for item in exported.env] == ["BASE_URL", "APP_PASSWORD", "APP_TOKEN", "PETS_API_KEY"], "the SQL step is a TODO in TypeScript: no database variable"
    assert SECRET not in exported.code


def test_python_golden_compiles() -> None:
    exported = export_run(result(), "playwright-py")
    check_golden("golden_pytest.py.txt", exported.code)  # not test_*.py: pytest would collect it
    ast.parse(exported.code)
    assert exported.filename == "tests/test_golden_case.py"
    assert {item.name: item.required for item in exported.env} == {"BASE_URL": False, "APP_DB_URL": True, "APP_PASSWORD": True, "APP_TOKEN": True, "PETS_API_KEY": True}


@pytest.mark.parametrize(
    ("address", "pattern"),
    [
        ("/app/records/k3J9xQ2mZ7pL4wB8/view", "/app/records/[^/]+/view"),
        ("/orders/12345", "/orders/[^/]+"),
        ("https://shop.test/r/3f2b6c1e-1111-4a2b-9c3d-123456789abc/x", "https://shop\\.test/r/[^/]+/x"),
        ("/app/records/list", None),
        ("/products.html", None),
        ("/v2/items", None),
    ],
)
def test_an_address_with_an_id_made_for_the_run_is_waited_for_as_a_pattern(address: str, pattern: str | None) -> None:
    assert address_pattern(address) == pattern


def test_a_navigation_to_a_new_record_waits_for_any_record_id() -> None:
    saved = AutomationSpec(
        case_id="record",
        title="Save a record",
        steps=[
            AutomationStep(
                index=1,
                action='click "Save"',
                status="passed",
                actions=[AutomationAction(kind="click", target=TargetSpec(role="button", name="Save", locator=role("Save")), navigated_to="/records/k3J9xQ2mZ7pL4wB8/view")],
            )
        ],
    )
    python = export_run(result(automation=saved), "playwright-py").code
    typescript = export_run(result(automation=saved), "playwright-ts").code
    ast.parse(python)
    assert "import re\n" in python and 're.fullmatch("/records/[^/]+/view", urlsplit(address).path) is not None' in python
    assert 'new RegExp("^/records/[^/]+/view$").test(url.pathname)' in typescript
    assert "k3J9xQ2mZ7pL4wB8" not in python + typescript


def test_a_navigation_to_a_new_record_from_a_known_page_waits_to_leave_that_page() -> None:
    action = AutomationAction(kind="click", target=TargetSpec(role="button", name="Save", locator=role("Save")), navigated_to="/records/k3J9xQ2mZ7pL4wB8/view", navigated_from="/records/new")
    saved = AutomationSpec(case_id="record", title="Save a record", steps=[AutomationStep(index=1, action='click "Save"', status="passed", actions=[action])])
    python = export_run(result(automation=saved), "playwright-py").code
    typescript = export_run(result(automation=saved), "playwright-ts").code
    ast.parse(python)
    assert 'page.wait_for_url(lambda address: urlsplit(address).path != "/records/new")' in python and "import re\n" not in python
    assert 'await page.waitForURL((url) => url.pathname !== "/records/new");' in typescript
    assert "k3J9xQ2mZ7pL4wB8" not in python + typescript


def test_spec_export_is_the_automation_spec() -> None:
    exported = export_run(result(), "spec")
    check_golden("golden-case.automation.json", exported.code)
    assert exported.spec["case_id"] == "golden-case"
    assert any("judged by meaning" in note for note in exported.notes)


def test_review_notes_name_every_step_that_is_not_exported_faithfully() -> None:
    notes = export_run(result(), "playwright-py").notes
    joined = "\n".join(notes)
    for expected in (
        "step 3: no Playwright text assertion",
        "step 4: no locator matched only the element",
        "step 4: assert in code: the order is confirmed",
        "step 5: check $.items[*].price",
        "step 5: validate the response body",
        "step 9: replace this a11y check",
    ):
        assert expected in joined, expected


def test_rendering_is_deterministic() -> None:
    for fmt in ("spec", "playwright-ts", "playwright-py"):
        assert export_run(result(), fmt).code == export_run(result(), fmt).code  # type: ignore[arg-type]


@pytest.mark.skipif(node() is None or not UI_TYPESCRIPT.is_dir(), reason="node and ui/node_modules/typescript are needed to parse TypeScript")
def test_typescript_golden_parses(tmp_path: Path) -> None:
    assert typescript_syntax_errors(export_run(result(), "playwright-ts").code, tmp_path) == []


@pytest.mark.skipif(playwright_types() is None or node() is None, reason="set HERCULES_TEST_PLAYWRIGHT_NODE_MODULES to a node_modules folder holding @playwright/test")
def test_typescript_golden_type_checks(tmp_path: Path) -> None:
    types = playwright_types()
    assert types is not None
    assert typescript_type_errors(export_run(result(), "playwright-ts").code, tmp_path, types) == ""


def test_a_failed_run_is_refused_with_the_reason() -> None:
    failed = result("failed", step_statuses=["passed", "passed", "failed", "skipped", "skipped", "skipped", "skipped", "skipped", "skipped"])
    with pytest.raises(ExportError, match=r"did not pass \(failed at step 3 \(failed\)\): only passed runs are exported"):
        export_run(failed, "playwright-ts")


def test_a_failed_run_exports_up_to_its_last_passed_step_when_allowed() -> None:
    failed = result("failed", step_statuses=["passed", "passed", "failed", "skipped", "skipped", "skipped", "skipped", "skipped", "skipped"])
    exported = export_run(failed, "playwright-py", allow_failed=True)
    assert exported.partial and (exported.steps_exported, exported.steps_total) == (2, 9)
    assert "PARTIAL: the run failed at step 3 (failed); only steps 1-2" in exported.code
    assert "# Step 3" not in exported.code
    assert exported.notes[0].startswith("PARTIAL")


def test_nothing_to_export_when_the_first_step_failed() -> None:
    failed = result("error", step_statuses=["error"] + ["skipped"] * 8)
    with pytest.raises(ExportError, match="no step passed"):
        export_run(failed, "spec", allow_failed=True)


def test_a_run_without_an_automation_record_is_refused() -> None:
    old = RunResult(run_id="r_1", status="passed", case_id="c", steps=[StepResult(index=1, action="a", status="passed")])
    with pytest.raises(ExportError, match="no automation record"):
        export_run(old, "playwright-ts")


def test_formats_and_target_folders_are_checked() -> None:
    with pytest.raises(ExportError, match="unknown format"):
        export_run(result(), "cypress")  # type: ignore[arg-type]
    with pytest.raises(ExportError, match="relative path"):
        export_run(result(), "playwright-ts", target_dir="../outside")
    assert export_run(result(), "playwright-ts", target_dir="e2e/flows/").filename == "e2e/flows/golden-case.spec.ts"
    assert export_run(result(), "spec").filename == "golden-case.automation.json"


def test_json_paths_and_templates_are_parsed() -> None:
    assert json_path_keys("$") == []
    assert json_path_keys("$.a.b[0]['c d'][\"e\"]") == ["a", "b", 0, "c d", "e"]
    assert json_path_keys("a.b") == ["a", "b"]
    assert json_path_keys("$.items[*].price") is None
    assert json_path_keys("$..name") is None
    assert template_parts("Hi {{name}} and ${token}!") == [("Hi ", None), ("{{name}}", "name"), (" and ", None), ("${token}", "token"), ("!", None)]


def test_unknown_names_stay_as_written() -> None:
    plain = spec().model_copy(
        update={"steps": [AutomationStep(index=1, action="a", status="passed", actions=[AutomationAction(kind="fill", value="{{nobody}} x", target=TargetSpec(locator=role("A")))])]}
    )
    code = export_run(result(automation=plain), "playwright-ts").code
    assert '.fill("{{nobody}} x");' in code


def _upload_result(files: list[str] | None) -> RunResult:
    target = TargetSpec(role="button", name="Photo", locator=LocatorSpec(strategy="label", value="Photo"))
    steps = [AutomationStep(index=1, action='I upload "case.png" to "Photo"', status="passed", actions=[AutomationAction(kind="upload", value="case.png", files=files, target=target)])]
    return result(automation=spec().model_copy(update={"steps": steps}))


def test_an_upload_exports_the_files_it_attached() -> None:
    attached = _upload_result(["fixtures/case.png", ".hercules/case-files/claim/scan.pdf"])

    typescript = export_run(attached, "playwright-ts").code
    python = export_run(attached, "playwright-py").code

    assert '.setInputFiles(["fixtures/case.png", ".hercules/case-files/claim/scan.pdf"]);' in typescript
    assert '.set_input_files(["fixtures/case.png", ".hercules/case-files/claim/scan.pdf"])' in python
    ast.parse(python)
    assert "the files attached here were not recorded" in export_run(_upload_result(None), "playwright-py", allow_failed=True).code


def _download_result() -> RunResult:
    link = TargetSpec(role="link", name="Export CSV", locator=role("Export CSV", "link"))
    checks = [
        AutomationCheck(kind="file", file_check="name", expected="report.csv", match="equals"),
        AutomationCheck(kind="file", file_check="type", expected="CSV"),
        AutomationCheck(kind="file", file_check="rows", expected=">= 3"),
        AutomationCheck(kind="file", file_check="column", expected="Paid", match="equals", subject="Status", where="Order ID = 1002"),
        AutomationCheck(kind="file", file_check="text", expected="Grace"),
    ]
    steps = [
        AutomationStep(index=1, action='I click "Export CSV"', status="passed", actions=[AutomationAction(kind="download", target=link, saved_as="report.csv")], checks=checks),
        AutomationStep(
            index=2,
            action='I download "/data/orders.json"',
            status="passed",
            actions=[AutomationAction(kind="download", url="/data/orders.json")],
            checks=[AutomationCheck(kind="file", file_check="json", expected="2", match="equals", subject="$.count"), AutomationCheck(kind="file", file_check="type", expected="PDF")],
        ),
        AutomationStep(
            index=3,
            action="check the PDF",
            status="passed",
            checks=[AutomationCheck(kind="file", file_check="text", expected="Total", faithful=False, note="the export does not read the text of a pdf file; check it by hand")],
        ),
    ]
    return result(automation=spec().model_copy(update={"steps": steps}))


def test_downloads_and_file_checks_export_to_both_languages(tmp_path: Path) -> None:
    typescript = export_run(_download_result(), "playwright-ts").code
    python = export_run(_download_result(), "playwright-py").code

    assert 'await keepDownload(page, () => page.getByRole("link", { name: "Export CSV", exact: true }).click());' in typescript
    assert 'await fetchDownload(page, url("/data/orders.json"));' in typescript and "page.request.get(address)" in typescript
    assert 'expect(folded(downloadedFile.split(/[\\\\/]/).pop() ?? "")).toBe(folded("report.csv"));' in typescript
    assert "expect((await downloadedRows()).rows.length).toBeGreaterThanOrEqual(3);" in typescript
    assert 'const cells = await downloadedCells("Status", ["Order ID", "1002"]);' in typescript and 'expect(folded(cell)).toBe(folded("Paid"));' in typescript
    assert 'expect(folded(await downloadedValue(["count"]))).toBe(folded("2"));' in typescript
    assert 'expect(await downloadedType()).toBe("pdf");' in typescript and "TODO" in typescript and "check that the downloaded file is a CSV" in typescript
    assert 'keep_download(page, lambda: page.get_by_role("link", name="Export CSV", exact=True).click())' in python
    assert 'fetch_download(page, url("/data/orders.json"))' in python and "from urllib.parse import unquote, urljoin, urlsplit" in python
    assert "assert len(downloaded_rows()[1]) >= 3" in python
    assert 'cells = downloaded_cells("Status", ("Order ID", "1002"))' in python and 'assert all(folded(cell) == folded("Paid") for cell in cells)' in python
    assert "assert folded(downloaded_value(['count'])) == folded(\"2\")" in python
    assert 'assert downloaded_type() == "pdf"' in python
    assert "check it by hand" in python and "check it by hand" in typescript
    ast.parse(python)
    for name in ("import csv", "import io", "import tempfile", "import json", "import zipfile", "from pathlib import Path"):
        assert name in python
    if node() is not None and UI_TYPESCRIPT.is_dir():
        assert typescript_syntax_errors(typescript, tmp_path) == []


def test_an_upload_of_the_downloaded_file_attaches_the_tests_own_download() -> None:
    target = TargetSpec(role="button", name="Photo", locator=LocatorSpec(strategy="label", value="Photo"))
    run_copy = [".hercules/runs/r_1/steps/3/downloads/guide.pdf"]
    steps = [
        AutomationStep(index=1, action="download", status="passed", actions=[AutomationAction(kind="download", url="/files/guide.pdf")]),
        AutomationStep(index=2, action="upload", status="passed", actions=[AutomationAction(kind="upload", value="{{downloaded_file}}", files=run_copy, target=target)]),
    ]
    attached = result(automation=spec().model_copy(update={"steps": steps}))

    typescript = export_run(attached, "playwright-ts").code
    python = export_run(attached, "playwright-py").code

    assert ".setInputFiles([downloadedFile]);" in typescript and ".set_input_files([downloaded[-1]])" in python
    assert ".hercules/runs" not in typescript + python


def test_a_value_saved_by_the_last_step_is_kept_although_no_step_uses_it() -> None:
    """The run saved the token at its end; the export keeps it in ``saved``, declared before the step that writes it."""
    automation = AutomationSpec(
        case_id="auth-token",
        title="Generate an authentication token",
        browser=False,
        steps=[
            AutomationStep(
                index=1,
                action='I send a POST request to "/auth"',
                status="passed",
                kind="api",
                api={"method": "POST", "path": "/auth", "body": {"username": "admin"}, "expect": {"status": 200}, "save": [{"name": "authToken", "json_path": "$.token"}]},
            ),
        ],
    )
    python = export_run(result(automation=automation), "playwright-py").code
    ast.parse(python)
    assert 0 < python.index("saved: dict[str, Any] = {}") < python.index('saved["authToken"] = at(body, ["token"])')
    typescript = export_run(result(automation=automation), "playwright-ts").code
    assert 0 < typescript.index("const saved: Record<string, unknown> = {};") < typescript.index('saved.authToken = at(body, ["token"]);')
