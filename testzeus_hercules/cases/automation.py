"""The automation spec of a run (:class:`AutomationSpec`): what the run did, as code-neutral data.

A run records, for every step, the actions it carried out (navigations, clicks, fills ...), the
locators that were proven unique on the live page for every element it acted on, the values it
typed (as templates: ``{{name}}`` placeholders, never secret values), the checks it verified, and
the API, SQL and quality payloads it executed. A native app step records its device actions and the
Appium locators of each element it used. ``hercules export`` renders the spec as Playwright Test
(TypeScript), pytest-playwright (Python) or, for a native app run, pytest with the Appium Python client
(see :mod:`testzeus_hercules.export`).

The spec only describes what the run did; nothing here decides anything. A step or check that
cannot be written as code faithfully (a check a model judged by its meaning, an element without a
unique locator) is kept with ``faithful: false`` and a note saying why.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from testzeus_hercules.cases.browser_options import BrowserName, BrowserOptions

# cases.models.DATA_KEY_PATTERN (cases.models imports this module): data names become identifiers in exported code.
_DATA_KEY = r"^[A-Za-z_][A-Za-z0-9_]*$"

AUTOMATION_VERSION = 1

LocatorStrategy = Literal["test_id", "role", "label", "placeholder", "text", "alt_text", "title", "css"]
ActionKind = Literal["navigate", "click", "fill", "select", "upload", "download", "check", "uncheck", "hover", "press", "wait_text", "wait_time"]
CheckKind = Literal["text", "not_text", "url", "visible_text", "element_visible", "field", "semantic", "visual", "file"]
# How a literal text check is written as code, chosen from what held on the live page when the check passed:
# text (one element shows the text), text_first (several do; the first is visible), body (the body's text holds it),
# count_zero (no element shows it), body_not (the body's text does not hold it).
TextForm = Literal["text", "text_first", "body", "count_zero", "body_not"]
StepKind = Literal["browser", "api", "sql", "quality", "mobile", "security"]
DataKind = Literal["value", "secret", "builtin", "saved"]
SelectBy = Literal["label", "value", "option_role", "text"]
# Native app steps: what the run did on the device, and the Appium locator strategies it recorded.
DeviceActionKind = Literal["tap", "type", "clear", "long_press", "swipe", "scroll", "back", "home", "wait"]
DeviceLocatorStrategy = Literal["accessibility id", "id", "-android uiautomator", "xpath"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocatorSpec(_Model):
    """One way to find an element, with the number of elements it matched on the live page (exported locators matched one)."""

    strategy: LocatorStrategy
    value: str | None = Field(default=None, description="test id, label, placeholder, text, alt text or title")
    attribute: str | None = Field(default=None, description="test_id: the attribute holding it (data-testid, data-test, data-qa, data-cy ...)")
    role: str | None = Field(default=None, description="role: the ARIA role")
    name: str | None = Field(default=None, description="role: the accessible name (None: the role alone)")
    exact: bool = True
    selectors: list[str] = Field(default_factory=list, description="css: one selector per tree, each inside the shadow root of the element the previous one matched")
    count: Annotated[int, Field(ge=0)] = 1


class FrameSpec(_Model):
    """One iframe on the way to an element: how to find the iframe element in its parent document."""

    locator: LocatorSpec
    fallbacks: list[LocatorSpec] = Field(default_factory=list)
    label: str | None = None


class TargetSpec(_Model):
    """The element an action used: its role and name as perceived, the frames it is in, and its unique locators (best first)."""

    role: str = ""
    name: str = ""
    frames: list[FrameSpec] = Field(default_factory=list)
    locator: LocatorSpec | None = Field(default=None, description="The preferred locator; None when no candidate matched only this element")
    fallbacks: list[LocatorSpec] = Field(default_factory=list)
    checked: Annotated[int, Field(ge=0)] = Field(default=0, description="Candidates evaluated on the live page")


class DeviceLocator(_Model):
    """One way to find a native app element again (an Appium locator), with what it matched on the screen when the run used it."""

    using: DeviceLocatorStrategy
    value: str
    count: Annotated[int, Field(ge=0)] = Field(default=1, description="Elements it matched on the screen's page source")
    position: Annotated[int, Field(ge=1)] = Field(default=1, description="Which of those (in document order) was the element")
    inner: bool = Field(default=False, description="It finds a text drawn inside the element (a list row named after its title); a tap on it reaches the element")


class DeviceElement(_Model):
    """The native app element an action used: its role and name as read from the screen, its locators (best first) and where it was drawn."""

    role: str = ""
    name: str = ""
    kind: str = Field(default="", description="Its class (Android) or element type (iOS), e.g. EditText, Button")
    locators: list[DeviceLocator] = Field(default_factory=list)
    bounds: list[int] | None = Field(default=None, description="x, y, width, height in screen pixels")
    screen: list[int] | None = Field(default=None, description="The screen's width and height in pixels, when the page source says")


class DeviceAction(_Model):
    """One action a native app step carried out on the device, as it happened."""

    kind: DeviceActionKind
    target: DeviceElement | None = None
    value: str | None = Field(default=None, description="type: the text as a template; {{name}} names an entry of the spec's data")
    direction: Literal["up", "down", "left", "right"] | None = Field(default=None, description="scroll: what came into view; swipe: the way the finger moved")
    at_center: bool = Field(default=False, description="tap: the screen had not changed when the run looked again after the tap, so the run also tapped where the element was drawn")
    faithful: bool = True
    note: str | None = None


class AutomationAction(_Model):
    """One action the run carried out, as it happened."""

    kind: ActionKind
    target: TargetSpec | None = None
    value: str | None = Field(default=None, description='fill/select: the value as a template; {{name}} names an entry of the spec\'s data. upload: the file names, " | " between several')
    url: str | None = Field(default=None, description="navigate: the address, relative to base_url when it is under it")
    files: list[str] | None = Field(default=None, description="upload: the files attached, relative to the project")
    saved_as: str | None = Field(default=None, description="download: the file's name as the run saved it")
    key: str | None = Field(default=None, description="press: the key")
    seconds: float | None = Field(default=None, description="wait_time: seconds")
    text: str | None = Field(default=None, description="wait_text: the text waited for")
    select_by: SelectBy | None = Field(default=None, description="select: how the option was chosen")
    option: str | None = Field(default=None, description="select: the option's label or value as chosen (a template when it is the typed value)")
    option_role: str | None = Field(default=None, description="select (custom drop-down): the role of the option clicked")
    option_exact: bool = Field(default=True, description="select (custom drop-down): whether the option's name matched exactly")
    typed: bool = Field(default=False, description="select (custom drop-down): the value was typed into it before the option was clicked")
    navigated_to: str | None = Field(default=None, description="The path a navigation the action caused ended on (the export waits for it)")
    navigated_from: str | None = Field(default=None, description="The path the page left in that navigation (the export waits to leave it when the new path holds an id made for the run)")
    faithful: bool = True
    note: str | None = None


class AutomationCheck(_Model):
    """One check of a step and how it was verified."""

    kind: CheckKind
    expected: str = Field(description="The expected text, URL or outcome as a template")
    match: Literal["contains", "equals"] = "contains"
    form: TextForm | None = None
    target: TargetSpec | None = Field(default=None, description="element_visible and field: the element; text checks: the frames the text is in (when not in the top document)")
    verdict: Literal["passed", "failed", "unverified"] = "passed"
    source: Literal["deterministic", "llm"] = "deterministic"
    observed: str | None = Field(default=None, description="Judged checks: literal evidence of the page it was judged on (title and address)")
    file_check: Literal["name", "type", "text", "not_text", "rows", "column", "json"] | None = Field(default=None, description="file: what is checked in the run's latest downloaded file")
    subject: str | None = Field(default=None, description="file column: the column; file json: the JSON path; field: the field's name in the step")
    read: Literal["value", "option", "text"] | None = Field(
        default=None, description="field: how its value is read: an input's value, a native drop-down's chosen option label, or the text a custom drop-down shows"
    )
    where: str | None = Field(default=None, description='file column: the row checked, as "<column> = <value>" (None: every row)')
    faithful: bool = True
    note: str | None = None


class AutomationData(_Model):
    """Where a ``{{name}}`` value comes from: a plain value of the case, a secret (an environment variable), a built-in, or a value a step saved."""

    kind: DataKind
    value: str | None = Field(default=None, description="value: the case's value as written (may hold built-in placeholders)")
    env: str | None = Field(default=None, description="secret: the environment variable holding it")
    step: int | None = Field(default=None, description="saved: the step that saves it")


class AutomationStep(_Model):
    """What one step did: its actions in order, then its checks (browser steps), or its API, SQL, quality or security payload."""

    index: Annotated[int, Field(ge=1)]
    action: str
    status: Literal["passed", "failed", "error", "skipped", "blocked", "unverified"]
    kind: StepKind = "browser"
    actions: list[AutomationAction] = Field(default_factory=list)
    checks: list[AutomationCheck] = Field(default_factory=list)
    api: dict[str, JsonValue] | None = Field(default=None, description="api steps: the request and checks as executed (ApiCall, placeholders kept)")
    sql: dict[str, JsonValue] | None = Field(default=None, description="sql steps: the query and checks as executed (SqlCall, placeholders kept)")
    sql_dialect: str | None = None
    quality: dict[str, JsonValue] | None = Field(default=None, description="quality steps: the check as executed (QualityCheck)")
    security: dict[str, JsonValue] | None = Field(default=None, description="security steps: the scan as executed (SecurityScan)")
    device: list[DeviceAction] | None = Field(default=None, description="mobile steps: the actions done on the device, in order (their checks are in checks)")
    payload_source: Literal["payload", "llm"] | None = Field(default=None, description="api/sql/quality/security: written in the case, or read from the step's text by the model")
    faithful: bool = True
    notes: list[str] = Field(default_factory=list)


class AutomationSpec(_Model):
    """A run as framework-neutral automation: the steps that ran, in order, with their data references."""

    version: Literal[1] = AUTOMATION_VERSION
    case_id: str
    title: str
    base_url: str | None = None
    viewport: str | None = None
    device: str | None = Field(default=None, description="Mobile web emulation: the Playwright device profile the run used")
    browser_name: BrowserName | None = Field(default=None, description="The browser the run used: chromium (when unset), chrome, msedge, firefox or webkit")
    browser_options: BrowserOptions | None = Field(default=None, description="The Playwright launch and context options the run used (secrets by variable name)")
    app: dict[str, JsonValue] | None = Field(default=None, description="Native app runs: the app and device (MobileApp); exported as Appium code, not Playwright code")
    appium: dict[str, JsonValue] | None = Field(
        default=None, description="Native app runs: the Appium server or device cloud hub the run used, and the environment variables holding the cloud's credentials (never their values)"
    )
    browser: bool = True
    data: dict[Annotated[str, StringConstraints(pattern=_DATA_KEY)], AutomationData] = Field(default_factory=dict)
    steps: list[AutomationStep] = Field(default_factory=list)
