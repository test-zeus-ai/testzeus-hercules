"""Perception on pages whose scripts break the walker: bad elements are skipped, a page that cannot be read says so.

Some apps replace or lock down built-in browser functions (globals, prototypes, DOM APIs), or define
custom elements whose getters throw. The walker must
list what it can read, and a page it cannot read must end the step as an ``error`` with the page's
own error (never as "no listed element matched").
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from testzeus_hercules.cases.gherkin import compile_feature
from testzeus_hercules.engine.ports import DefaultPerceiver, PerceptionFailed
from testzeus_hercules.engine.run_graph import RunDeps, run_case
from testzeus_hercules.kernel.run_context import RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import RecordingTransport, TelemetryClient
from testzeus_hercules.perceive import PerceptionError, Snapshot, resolve, take_snapshot
from testzeus_hercules.perceive import snapshot as snapshot_module

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401
from tests.v2.engine_fixtures.browser import require_chromium, serve_fixtures
from tests.v2.engine_fixtures.fakes import LabelDecider, ScriptedInterpreter, clicks, opens


def _names(snap: Snapshot) -> set[str]:
    return {element.name for element in snap.elements}


def test_a_page_that_patches_globals_and_prototypes_still_lists_its_good_elements(browser: BrowserHarness) -> None:  # noqa: F811
    async def scenario() -> tuple[Snapshot, str]:
        async with browser.page("poisoned.html") as page:
            snap = await take_snapshot(page, seq=1)
            good = next(element for element in snap.elements if element.name == "Good button")
            await (await resolve(page, snap, good.md)).click()
            return snap, await page.text_content("#result") or ""  # type: ignore[attr-defined]

    snap, result = browser.run(scenario())

    assert {"Good button", "Good link", "Good field", "Unstyled button", "Trapped button", "Shadow button"} <= _names(snap)
    assert "Closed shadow button" not in _names(snap), "a closed shadow root is not readable, and is no error"
    # The page's own world skipped the button whose rect the page broke; the isolated world, which the page's
    # scripts cannot touch, lists it too, so that read is kept.
    assert "Cursed button" in _names(snap) and snap.world == "isolated" and snap.viewport == (1280, 800)
    assert any("kept 1 element from being read" in note and "rect access denied" in note for note in snap.notes), snap.notes
    assert result == "clicked", "an element read on a poisoned page is still acted on"


def test_the_walker_itself_skips_only_the_bad_element_in_the_pages_own_world(browser: BrowserHarness) -> None:  # noqa: F811
    async def scenario() -> dict[str, Any]:
        async with browser.page("poisoned.html") as page:
            return await page.evaluate(snapshot_module.walker_source(), {"start": 1, "frames": []})  # type: ignore[attr-defined]

    result = browser.run(scenario())
    names = {element["name"] for element in result["elements"]}

    assert {"Good button", "Good link", "Good field", "Unstyled button", "Trapped button", "Shadow button"} <= names
    assert "Cursed button" not in names and result["skipped"] == 1
    assert any(record["stage"] == "element" and record["tag"] == "button" and "rect access denied" in record["message"] for record in result["errors"])


def test_blocked_id_lookups_on_shadow_roots_still_name_fields_in_the_pages_own_world(browser: BrowserHarness) -> None:  # noqa: F811
    async def scenario() -> dict[str, Any]:
        async with browser.page("shadow_ids_blocked.html") as page:
            return await page.evaluate(snapshot_module.walker_source(), {"start": 1, "frames": []})  # type: ignore[attr-defined]

    result = browser.run(scenario())
    names = {element["name"] for element in result["elements"]}

    assert {"Record Name", "Team", "Save"} <= names, names
    assert result["skipped"] == 0, result["errors"]


def test_blocked_id_lookups_on_shadow_roots_are_read_as_named_fields(browser: BrowserHarness) -> None:  # noqa: F811
    async def scenario() -> Snapshot:
        async with browser.page("shadow_ids_blocked.html") as page:
            return await take_snapshot(page, seq=1)

    snap = browser.run(scenario())

    assert {"Record Name", "Team", "Save"} <= _names(snap)
    assert snap.world == "main" and not snap.read_errors
    assert "Team is required" in snap.messages, "an error message found through a blocked id lookup still reaches the continuation"


def test_a_page_whose_scripts_break_the_walk_is_read_in_an_isolated_world(browser: BrowserHarness) -> None:  # noqa: F811
    async def scenario() -> tuple[Snapshot, str]:
        async with browser.page("unwalkable.html") as page:
            snap = await take_snapshot(page, seq=1)
            target = next(element for element in snap.elements if element.name == "Continue")
            locator = await resolve(page, snap, target.md)
            return snap, await locator.get_attribute("id") or ""  # type: ignore[attr-defined]

    snap, found = browser.run(scenario())

    assert snap.world == "isolated" and snap.viewport == (1280, 800)
    assert "Continue" in _names(snap)
    assert any("read in an isolated world" in note and "innerWidth is not available in this sandbox" in note for note in snap.notes), snap.notes
    assert found == "go"


# ── a walker that cannot run at all (a fake page) ────────────────────────────


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeFrame:
    def __init__(self, result: Any = None, error: str | None = None, children: tuple[Any, ...] = (), name: str = "") -> None:
        self.result, self.error, self.child_frames, self.name = result, error, list(children), name
        self.calls = 0

    def is_detached(self) -> bool:
        return False

    async def frame_element(self) -> Any:
        return _FakeHandle()

    async def evaluate(self, source: str, arg: Any = None) -> Any:
        from playwright.async_api import Error as PlaywrightError

        self.calls += 1
        if self.error is not None:
            raise PlaywrightError(self.error)
        return self.result


class _FakeHandle:
    async def dispose(self) -> None:
        return None


class _NoProtocol:
    async def new_cdp_session(self, page: Any) -> Any:
        raise RuntimeError("no protocol session in this browser")


class _FakePage:
    url = "https://app.test/list?sid=SESSION-1"

    def __init__(self, main: _FakeFrame) -> None:
        self.main_frame = main
        self.context = _NoProtocol()

    async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def evaluate(self, source: str, arg: Any = None) -> Any:
        return {"ms": 0, "settled": True}

    async def screenshot(self, **kwargs: Any) -> bytes:
        return _jpeg()

    async def title(self) -> str:
        return "Accounts"


def _result(*names: str, frames: tuple[Any, ...] = ()) -> dict[str, Any]:
    elements = [
        {
            "md": n,
            "role": "button",
            "name": name,
            "tag": "button",
            "attrs": {},
            "rect": [0, 10 * n, 50, 8],
            "visible": [0, 10 * n, 50, 8],
            "tier": 1,
            "value": None,
            "checked": None,
            "disabled": False,
            "path": [f"#b{n}"],
        }
        for n, name in enumerate(names, start=1)
    ]
    return {"viewport": [800, 600], "next": len(names) + 1, "elements": elements, "sections": [], "messages": [], "frames": list(frames), "errors": [], "skipped": 0}


def test_a_walker_that_cannot_run_raises_a_perception_error_with_the_pages_error() -> None:
    main = _FakeFrame(error="ReferenceError: sandboxProxy is not defined")

    with pytest.raises(PerceptionError) as caught:
        asyncio.run(take_snapshot(_FakePage(main), seq=1))  # type: ignore[arg-type]

    error = caught.value
    assert error.message == "ReferenceError: sandboxProxy is not defined"
    assert main.calls == 2, "tried once more after the page settled"
    assert error.attempts == ("ReferenceError: sandboxProxy is not defined",) * 2
    assert error.title == "Accounts" and error.screenshot, "what the page looked like is kept for the evidence"


def test_the_perceiver_reports_a_failed_walk_as_perception_failed() -> None:
    with pytest.raises(PerceptionFailed) as caught:
        asyncio.run(DefaultPerceiver().snapshot(_FakePage(_FakeFrame(error="TypeError: x is not a function")), 1))

    assert caught.value.error == "TypeError: x is not a function"


def test_every_element_failing_to_read_is_a_failed_walk() -> None:
    broken = {**_result(), "errors": [{"stage": "element", "tag": "x-row", "message": "TypeError: frozen", "count": 40}], "skipped": 40}

    with pytest.raises(PerceptionError) as caught:
        asyncio.run(take_snapshot(_FakePage(_FakeFrame(result=broken)), seq=1))  # type: ignore[arg-type]

    assert "every element failed to read: 40 elements skipped (first error: TypeError: frozen (element <x-row>))" in caught.value.message
    assert caught.value.errors[0]["count"] == 40


def test_a_child_frame_that_cannot_be_read_is_noted_and_the_rest_is_kept() -> None:
    child = _FakeFrame(error="Error: blocked frame", name="widget")
    frame_info = {"content": [0, 100, 300, 200], "visible": [0, 100, 300, 200], "label": "widget", "path": ["iframe"]}
    main = _FakeFrame(result=_result("Save", frames=(frame_info,)), children=(child,))
    main.result["errors"] = [{"stage": "style", "tag": "div", "message": "Error: style access denied", "count": 3}]

    snap = asyncio.run(take_snapshot(_FakePage(main), seq=1))  # type: ignore[arg-type]

    assert [element.name for element in snap.elements] == ["Save"]
    assert "frames not read: widget (Error: blocked frame)" in snap.notes
    assert any(note.startswith("3 reads failed and were left out (first error: Error: style access denied") for note in snap.notes)
    header = snap.observation_text().splitlines()
    assert "# read: frames not read: widget (Error: blocked frame)" in header


# ── a page that cannot be read ends the step as an error (real Chromium) ─────


SCENARIO = """\
Feature: Unreadable page
  Scenario: The walker cannot run
    Given I open "/login.html"
    When I click the "Sign in" button
"""


def test_a_step_on_a_page_that_cannot_be_read_ends_as_an_error_with_the_error_in_its_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chromium = require_chromium()
    monkeypatch.setattr(snapshot_module, "walker_source", lambda: '() => { throw new Error("walker exploded"); }')
    project = tmp_path / "project"
    project.mkdir()
    settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
    from testzeus_hercules.kernel.paths import HerculesPaths

    telemetry = TelemetryClient(settings, HerculesPaths.from_settings(settings), transport=RecordingTransport())
    ctx = RunContext.create(settings, telemetry=telemetry)
    decider = LabelDecider()
    interpreter = ScriptedInterpreter({'I open "/login.html"': opens("/login.html"), 'I click the "Sign in" button': clicks("Sign in", role="button")})
    with serve_fixtures() as base_url:
        case = compile_feature(SCENARIO, base_url=base_url)
        deps = RunDeps(decider=decider, interpreter=interpreter, perceiver=DefaultPerceiver(), env={"HERCULES_CHROMIUM": chromium})
        result = asyncio.run(run_case(case, ctx, deps))
    run_dir = ctx.paths.run_dir(ctx.run_id)

    assert result.status == "error"
    step = result.steps[1]
    assert step.status == "error"
    assert step.actual == "the page could not be read: Error: walker exploded"
    assert "no listed element matched" not in json.dumps(result.model_dump(mode="json"))
    step_dir = run_dir / "steps" / "2"
    record = json.loads((step_dir / "perception_error.json").read_text())
    assert record["error"] == "Error: walker exploded" and len(record["attempts"]) >= 2
    observation = (step_dir / "observation.txt").read_text()
    assert "the page could not be read" in observation and "# read error: Error: walker exploded" in observation
    assert (step_dir / "plain.jpg").exists(), "what the page looked like"
    assert any(uri.endswith("perception_error.json") for uri in step.evidence)
    assert result.failure is not None and result.failure.category == "environment"
    assert "the page could not be read" in result.failure.signals[0]
    assert not [request for request, _ in decider.calls if "category" in request.questions], "no model guesses why the run failed"
