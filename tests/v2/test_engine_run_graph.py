"""Tests for the run graph: prepare, steps in order, finalize into a RunResult with evidence."""

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, AsyncIterator

from testzeus_hercules.cases.models import RunResult, Step, TestCase
from testzeus_hercules.engine.run_graph import RunDeps, parse_viewport, run_case
from testzeus_hercules.engine.step_graph import StepLimits
from testzeus_hercules.kernel.run_context import ListEventSink, RunContext
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.kernel.telemetry import ALLOWED_FIELDS, RecordingTransport, TelemetryClient

from tests.v2.engine_fixtures.fakes import FakeActions, FakePage, FakePerceiver, LabelDecider, ScriptedInterpreter, clicks, fills, intent, opens, sees

# How a model reads the steps of the cases below.
READINGS = {
    "Open /login": opens("/login"),
    'fill "alice" in "Username"': fills("Username", "alice"),
    'click "Sign in"': clicks("Sign in"),
    'I should see "Welcome"': sees("Welcome"),
    'I should see "Goodbye"': sees("Goodbye"),
    'I should see "nothing"': sees("nothing"),
    'fill "{{pw}}" in "Password"': fills("Password", "{{pw}}"),
    'check "Remember me"': intent("check", "Remember me"),
}


class FakeLauncher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.opened: list[dict[str, Any]] = []
        self.closed = 0

    @contextlib.asynccontextmanager
    async def open(self, *, viewport: tuple[int, int], headless: bool) -> AsyncIterator[Any]:
        if self.error is not None:
            raise self.error
        self.opened.append({"viewport": viewport, "headless": headless})
        try:
            yield FakePage(url="about:blank")
        finally:
            self.closed += 1


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        actions: FakeActions | None = None,
        launcher: FakeLauncher | None = None,
        limits: StepLimits | None = None,
        decider: Any = None,
        interpreter: ScriptedInterpreter | None = None,
    ) -> None:
        project = tmp_path / "project"
        project.mkdir(parents=True, exist_ok=True)
        self.settings = Settings.load(project, env={"HERCULES_HOME": str(tmp_path / "home")})
        self.transport = RecordingTransport()
        self.events = ListEventSink()
        telemetry = TelemetryClient(self.settings, _paths(self.settings), transport=self.transport)
        self.ctx = RunContext.create(self.settings, run_id="r_test_1", events=self.events, telemetry=telemetry)
        self.actions = actions or FakeActions(text="Welcome, alice")
        self.launcher = launcher or FakeLauncher()
        self.decider = decider or LabelDecider()
        self.interpreter = interpreter or ScriptedInterpreter(READINGS)
        self.deps = RunDeps(
            decider=self.decider,
            interpreter=self.interpreter,
            perceiver=FakePerceiver(),
            launcher=self.launcher,
            actions=self.actions,
            env={"APP_PW": "pw"},
            limits=limits or StepLimits(assert_timeout_ms=10),
        )

    def run(self, case: TestCase) -> RunResult:
        result = asyncio.run(run_case(case, self.ctx, self.deps))
        self.ctx.telemetry.flush()
        return result

    @property
    def run_dir(self) -> Path:
        return self.ctx.paths.run_dir(self.ctx.run_id)

    def telemetry_events(self) -> list[dict[str, Any]]:
        return list(self.transport.payloads)


def _paths(settings: Settings) -> Any:
    from testzeus_hercules.kernel.paths import HerculesPaths

    return HerculesPaths.from_settings(settings)


def login_case(expected: str = "Welcome") -> TestCase:
    return TestCase(
        id="login",
        title="Login",
        environment={"base_url": "https://app.test", "viewport": "1024x700"},
        steps=[
            Step(action="Open /login"),
            Step(action='fill "alice" in "Username"'),
            Step(action='click "Sign in"'),
            Step(action="verify", expected=f'I should see "{expected}"'),
        ],
    )


def test_parse_viewport() -> None:
    assert parse_viewport("1024x700") == (1024, 700)
    assert parse_viewport(None) == (1280, 800)


def test_passing_case(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    result = h.run(login_case())

    assert result.status == "passed", result.summary_text()
    assert [step.status for step in result.steps] == ["passed"] * 4
    assert [step.index for step in result.steps] == [1, 2, 3, 4]
    assert result.assertions.total == 1 and result.assertions.passed == 1
    assert result.verdict_source == "ledger"
    assert result.engines.decider == "scripted"
    assert h.launcher.opened == [{"viewport": (1024, 700), "headless": True}]
    assert h.launcher.closed == 1
    stored = RunResult.model_validate_json((h.run_dir / "run.json").read_text())
    assert stored == result
    assert result.steps[1].decisions == 1
    assert (h.run_dir / "steps" / "2" / "som.jpg").exists()


def test_failing_step_stops_run_and_skips_rest(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    case = login_case(expected="Goodbye")
    case = case.model_copy(update={"steps": [*case.steps, Step(action='click "Sign in"')]})

    result = h.run(case)

    assert result.status == "failed"
    assert [step.status for step in result.steps] == ["passed", "passed", "passed", "failed", "skipped"]
    failed = result.steps[3]
    assert failed.expected == 'I should see "Goodbye"'
    assert "Welcome, alice" in (failed.actual or "")
    assert failed.evidence
    assert result.assertions.failed == 1
    assert "Step 4 FAILED" in result.summary_text()


def test_telemetry_and_events(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    h.run(login_case(expected="Goodbye"))

    events = h.telemetry_events()
    names = [event["event"] for event in events]
    assert names == ["run_started", "step_failed", "run_finished"]
    for event in events:
        assert set(event) <= ALLOWED_FIELDS
    assert events[0]["step_count"] == 4
    assert events[1]["error_class"] == "AssertionFailed"
    assert events[2]["run_status"] == "failed"
    kinds = [event.kind for event in h.events.events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    assert kinds.count("step_started") == 4 and kinds.count("step_finished") == 4


def test_cancelled_before_start_skips_everything(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.ctx.cancel.cancel("user stopped")

    result = h.run(login_case())

    assert result.status == "cancelled"
    assert {step.status for step in result.steps} == {"skipped"}


def test_cancel_during_run_stops_after_current_step(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    original = h.actions.open_url

    async def open_and_cancel(*args: Any, **kwargs: Any) -> Any:
        h.ctx.cancel.cancel("stop")
        return await original(*args, **kwargs)

    h.actions.open_url = open_and_cancel  # type: ignore[method-assign]
    result = h.run(login_case())

    assert result.status == "cancelled"
    assert result.steps[0].status == "passed"
    assert {step.status for step in result.steps[1:]} == {"skipped"}


def test_browser_launch_failure_is_an_error(tmp_path: Path) -> None:
    h = Harness(tmp_path, launcher=FakeLauncher(error=RuntimeError("Executable doesn't exist at /nowhere")))

    result = h.run(login_case())

    assert result.status == "error"
    assert result.steps == [] or {step.status for step in result.steps} == {"skipped"}
    assert any("browser" in hint.lower() for hint in result.hints)
    assert (h.run_dir / "run.json").exists()


def test_step_timeout_is_an_error(tmp_path: Path) -> None:
    class SlowActions(FakeActions):
        async def open_url(self, page: Any, url: str, *, base_url: str | None = None, timeout_ms: int = 0, file_roots: Any = None) -> Any:
            await asyncio.sleep(5)
            return await super().open_url(page, url, base_url=base_url)

    h = Harness(tmp_path, actions=SlowActions(), limits=StepLimits(step_timeout_s=0.2, assert_timeout_ms=10))

    result = h.run(login_case())

    assert result.status == "error"
    assert result.steps[0].status == "error"
    assert "timed out" in (result.steps[0].actual or "")


def test_secret_values_never_reach_run_json(tmp_path: Path) -> None:
    h = Harness(tmp_path, actions=FakeActions(text="token pw-secret-value shown"))
    h.deps.env = {"APP_PW": "pw-secret-value"}
    case = TestCase(
        id="s",
        title="s",
        test_data={"pw": {"secret": "APP_PW"}},
        steps=[Step(action='fill "{{pw}}" in "Password"'), Step(action="verify", expected='I should see "nothing"')],
    )

    h.run(case)

    text = (h.run_dir / "run.json").read_text()
    assert "pw-secret-value" not in text
    for path in (h.run_dir / "steps").rglob("*"):
        if path.suffix in (".txt", ".jsonl"):
            assert "pw-secret-value" not in path.read_text()
    assert json.loads(text)["status"] == "failed"


def test_engine_info_and_cost_come_from_the_run_engines(tmp_path: Path) -> None:
    from testzeus_hercules.cases.models import CostInfo

    h = Harness(tmp_path)
    h.deps.mode = "hybrid"
    h.deps.decider_model = "typesafe/jev-1.14"
    h.deps.usage = lambda: (CostInfo(tokens_in=1200, tokens_out=80, usd=0.0042), {"jev_calls": 3, "fallbacks": 1})
    case = TestCase(id="c", title="t", environment={"base_url": "https://app.test"}, steps=[Step(action="Open /login")])

    result = h.run(case)

    assert result.engines.mode == "hybrid"
    assert result.engines.decider_model == "typesafe/jev-1.14"
    assert (result.engines.jev_calls, result.engines.fallbacks) == (3, 1)
    assert result.cost.usd == 0.0042
    assert result.cost.tokens_in == 1200
    saved = json.loads((h.ctx.paths.run_dir(result.run_id) / "run.json").read_text())
    assert saved["cost"]["usd"] == 0.0042


def test_the_case_is_read_once_before_the_first_step(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    h.run(login_case())

    assert len(h.interpreter.case_calls) == 1
    items = [(item.step, item.role, item.text) for item in h.interpreter.case_calls[0]]
    assert items == [
        (1, "action", "Open /login"),
        (2, "action", 'fill "alice" in "Username"'),
        (3, "action", 'click "Sign in"'),
        (4, "expected", 'I should see "Welcome"'),
    ], "a check-only step is read as an outcome to check"
    assert h.interpreter.calls == []  # every step came from the case reading
    plan = [json.loads(line) for line in (h.run_dir / "steps" / "2" / "plan.jsonl").read_text().splitlines()]
    assert plan[0]["stage"] == "interpret" and plan[0]["reading"] == "model" and plan[0]["source"] == "case"


def test_without_a_case_reading_each_step_is_read_on_the_page(tmp_path: Path) -> None:
    h = Harness(tmp_path, interpreter=ScriptedInterpreter(READINGS, case_reading=False))

    result = h.run(login_case())

    assert result.status == "passed", result.summary_text()
    texts = [text for text, _ in h.interpreter.calls]
    assert texts == ["Open /login", 'fill "alice" in "Username"', 'click "Sign in"', 'I should see "Welcome"']
    assert all(page is not None for _, page in h.interpreter.calls)  # read with the page's controls


def test_a_run_without_a_step_reader_errors_on_browser_steps(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.deps.interpreter = None

    result = h.run(login_case())

    assert result.status == "error"
    assert result.steps[0].status == "error"
    assert "no model is configured to read this step" in (result.steps[0].actual or "")


def test_unmet_precondition_is_a_run_error_naming_it(tmp_path: Path) -> None:
    h = Harness(tmp_path, decider=LabelDecider(judge=0.05))
    case = login_case().model_copy(update={"preconditions": ["The user is signed out"]})

    result = h.run(case)

    assert result.status == "error"
    assert {step.status for step in result.steps} == {"skipped"}
    assert "precondition not met: 'The user is signed out'" in result.hints[0]
    assert h.actions.calls[0] == ("open_url", "https://app.test", "https://app.test")  # the blank page went to the base URL first
    assert (h.run_dir / "preconditions" / "decisions.jsonl").exists()
    judged = [purpose for _, purpose in h.decider.calls if purpose != "coverage"]  # the plan's coverage audit asks first
    assert judged[0] == "precondition"


def test_met_preconditions_let_the_run_go_on_and_unsure_ones_are_noted(tmp_path: Path) -> None:
    met = Harness(tmp_path / "met", decider=LabelDecider(judge=0.95))
    case = login_case().model_copy(update={"preconditions": ["The login page is reachable"]})
    assert met.run(case).status == "passed"

    unsure = Harness(tmp_path / "unsure", decider=LabelDecider(judge=0.5))
    result = unsure.run(case)
    assert result.status == "passed"  # an unconfirmed precondition is noted, not a failure
    assert any("precondition not confirmed (the model was not sure): 'The login page is reachable'" in hint for hint in result.hints)


def test_a_failed_run_is_classified_by_the_decider_with_a_factual_summary(tmp_path: Path) -> None:
    h = Harness(tmp_path, decider=LabelDecider(category="product_bug"))

    result = h.run(login_case(expected="Goodbye"))

    assert result.status == "failed"
    assert result.failure is not None and result.failure.category == "product_bug" and result.failure.confidence == 0.9
    assert result.failure.summary is not None and result.failure.summary.startswith("step 4 failed")
    assert "failure" in [purpose for _, purpose in h.decider.calls]


def test_an_unsure_classification_is_unclassified_and_a_pass_has_none(tmp_path: Path) -> None:
    from testzeus_hercules.decide.backends.stub import StubDecider

    probabilities = {"environment": 0.25, "contract_violation": 0.2, "test_fragility": 0.25, "product_bug": 0.3}
    unsure = StubDecider({"category": {"type": "choice", "choice": "product_bug", "confidence": 0.3, "probabilities": probabilities}})

    class Both:
        engine = "scripted"

        async def decide(self, request: Any, *, purpose: str) -> Any:
            return await (unsure if purpose == "failure" else LabelDecider()).decide(request, purpose=purpose)

    failed = Harness(tmp_path / "f", decider=Both()).run(login_case(expected="Goodbye"))
    assert failed.failure is not None and failed.failure.category == "unclassified" and failed.failure.confidence == 0.3

    passed = Harness(tmp_path / "p").run(login_case())
    assert passed.failure is None


def test_junit_xml_and_evidence_paths_are_written(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ElementTree

    h = Harness(tmp_path)

    result = h.run(login_case(expected="Goodbye"))

    assert result.evidence.junit == "junit.xml"
    assert result.evidence.trace is None and result.evidence.video is None
    suite = ElementTree.parse(h.run_dir / "junit.xml").getroot()
    assert suite.tag == "testsuite" and suite.get("name") == "login"
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors")) == ("4", "1", "0")
    cases = suite.findall("testcase")
    assert cases[3].find("failure") is not None and "Goodbye" in (cases[3].find("failure").text or "")
    assert cases[0].find("failure") is None


def test_page_console_and_network_are_logged_and_redacted(tmp_path: Path) -> None:
    class Request:
        method = "GET"
        url = "https://app.test/api?token=abc123secret"
        resource_type = "fetch"
        headers = {"Authorization": "Bearer abc123secret", "Accept": "application/json"}
        failure = "net::ERR_FAILED"

    class Response:
        url = Request.url
        status = 200
        request = Request()

    class Message:
        type = "error"
        text = "boom pw-value-123 leaked"
        location = {"url": "https://app.test/app.js", "lineNumber": 3}

    class LoggingPage(FakePage):
        def on(self, event: str, handler: Any) -> None:
            {"console": lambda: handler(Message()), "response": lambda: handler(Response()), "requestfailed": lambda: handler(Request())}.get(event, lambda: None)()

    class Launcher(FakeLauncher):
        @contextlib.asynccontextmanager
        async def open(self, *, viewport: tuple[int, int], headless: bool) -> AsyncIterator[Any]:
            yield LoggingPage(url="about:blank")

    h = Harness(tmp_path, launcher=Launcher())
    h.deps.env = {"APP_PW": "pw-value-123"}
    case = TestCase.model_validate({**login_case().model_dump(), "test_data": {"pw": {"secret": "APP_PW"}}})

    result = h.run(case)

    assert (result.evidence.console, result.evidence.network) == ("console.jsonl", "network.jsonl")
    console = [json.loads(line) for line in (h.run_dir / "console.jsonl").read_text().splitlines()]
    assert console[0]["type"] == "error" and "pw-value-123" not in console[0]["text"]
    network = (h.run_dir / "network.jsonl").read_text()
    assert "abc123secret" not in network
    records = [json.loads(line) for line in network.splitlines()]
    assert records[0]["status"] == 200 and dict(records[0]["request_headers"])["Accept"] == "application/json"
    assert records[1]["failed"] == "net::ERR_FAILED"
