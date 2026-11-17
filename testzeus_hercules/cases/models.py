"""Canonical test case input (:class:`TestCase`) and run output (:class:`RunResult`).

Both models are immutable and reject unknown fields, so an agent or a person
writing JSON by hand gets a readable validation error instead of a silently
ignored typo. Their JSON Schemas are published next to this module (see
:mod:`testzeus_hercules.cases.schemas`).

A step may carry a structured ``api`` (:class:`ApiCall`), ``sql`` (:class:`SqlCall`),
``quality`` (:class:`QualityCheck`) or ``security`` (:class:`SecurityScan`) payload: the
request, query, page check or scan to run and what makes it pass, executed and judged in
code. ``action`` stays the human-readable sentence. Values a step saves (``save``) are
available to later steps as ``{{name}}`` placeholders. A run's accessibility and visual
findings are reported in ``RunResult.quality`` (:class:`QualitySection`), its security
scan findings in ``RunResult.security``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import AbstractSet, Annotated, ClassVar, Literal, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

from testzeus_hercules.cases.automation import AutomationSpec
from testzeus_hercules.cases.browser_options import BrowserName, BrowserOptions, engine_of
from testzeus_hercules.cases.plan import PlanSummary
from testzeus_hercules.cases.security import DEFAULT_SCAN_TAGS, INTRUSIVE_TAGS, SCAN_TAG_PATTERN, SecuritySection, Severity
from testzeus_hercules.quality.findings import IMPACT_ORDER, DiffClassification, Finding, FixSuggestion, Impact, StyleDelta, at_or_above

CASE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,99}$"
SECRET_NAME_PATTERN = r"^[A-Z_][A-Z0-9_]*$"
DATA_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
VIEWPORT_PATTERN = r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$"
BASELINE_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,100}$"
BASE_URL_PATTERN = r"^https?://[^\s/?#]+"

BUILTIN_PLACEHOLDERS: frozenset[str] = frozenset({"run_id", "timestamp"})
DOWNLOAD_PLACEHOLDERS: frozenset[str] = frozenset({"downloaded_file", "downloaded_file_name"})
RESERVED_PLACEHOLDERS: frozenset[str] = BUILTIN_PLACEHOLDERS | DOWNLOAD_PLACEHOLDERS
MAX_STEPS = 200

_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")
_NAME = re.compile(DATA_KEY_PATTERN)
_FIELD_CLIP = 400
_MIN_SUMMARY_CHARS = 80

CaseId = Annotated[str, StringConstraints(pattern=CASE_ID_PATTERN)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
DataKey = Annotated[str, StringConstraints(pattern=DATA_KEY_PATTERN)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Count = Annotated[int, Field(ge=0)]

Priority = Literal["low", "medium", "high", "critical"]
# chromium (Playwright's build), chrome / msedge (installed Chrome / Edge), firefox, webkit.
Browser = BrowserName
# known_bug: a failing case that reproduces an open bug (written by exploration); runs report it, never fail on it.
CaseStatus = Literal["draft", "ready", "known_bug"]
RunStatus = Literal["queued", "running", "needs_approval", "needs_plan_approval", "passed", "failed", "error", "cancelled"]
VerdictSource = Literal["ledger", "decider", "llm", "none"]
StepStatus = Literal["passed", "failed", "error", "skipped", "blocked", "unverified"]
FailureCategory = Literal["environment", "contract_violation", "test_fragility", "product_bug", "test_data", "unclassified"]
EgressPolicy = Literal["local", "hybrid", "hybrid-text"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- TestCase


class SecretRef(_Model):
    """A secret read from the named environment variable at run time; never the value itself."""

    secret: Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN)]


TestDataValue = Union[str, SecretRef]


MobilePlatform = Literal["android", "ios"]
CloudProvider = Literal["browserstack", "saucelabs", "testmu"]
# Apps a file in the project may hold: Android packages, iOS simulator builds (a .app folder or a zip of one), iOS device builds.
APP_SUFFIXES = (".apk", ".aab", ".app", ".ipa", ".zip")
# An app already in a device cloud's storage: BrowserStack (bs://<id>), Sauce Labs (storage:<id>, storage:filename=<name>)
# or TestMu AI, formerly LambdaTest (lt://<id>).
CLOUD_APP_PATTERN = r"^(?:bs://[A-Za-z0-9_-]+|storage:(?:filename=)?[A-Za-z0-9._-]+|lt://[A-Za-z0-9_-]+)$"
_CLOUD_APP = re.compile(CLOUD_APP_PATTERN)
# Capability names that look like credentials: device cloud credentials come from environment variables named in config.
_CREDENTIAL_KEY = re.compile(r"(?:user(?:name)?|access[_-]?key|api[_-]?key|password|passwd|secret|token)$", re.IGNORECASE)
# Capabilities Hercules sets from the case's own fields: written once, in one place.
_OWN_CAPABILITIES = frozenset({"platformName", "appium:app", "appium:appPackage", "appium:appActivity", "appium:bundleId", "appium:deviceName", "appium:udid", "appium:platformVersion"})
AppiumUrl = Annotated[str, StringConstraints(pattern=r"^https?://[^\s/?#@]+(?:/[^\s?#]*)?$", max_length=500)]


class MobileApp(_Model):
    """A native app to test on a device, through an Appium server: local (default) or a device cloud.

    The app is a file inside the project (``app``: an .apk, .aab, .app, .ipa or .zip), an app already in the device
    cloud's storage (``bs://...``, ``storage:...``, ``lt://...``), or an app installed on the device (``package`` and ``activity`` on
    Android, ``bundle_id`` on iOS). Device cloud credentials are never part of the case: they come from the environment
    variables the project's config names (``mobile.browserstack`` / ``mobile.saucelabs`` / ``mobile.testmu``).
    """

    platform: MobilePlatform = Field(description="android (UiAutomator2) or ios (XCUITest)")
    app: NonEmpty | None = Field(default=None, description="An app file inside the project (.apk, .aab, .app, .ipa, .zip), or a device cloud app id (bs://..., storage:..., lt://...)")
    package: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")] | None = Field(
        default=None, description="Android: the installed app's package, e.g. com.android.settings"
    )
    activity: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.$]+$")] | None = Field(default=None, description="Android: the activity to start, e.g. .Settings")
    bundle_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")] | None = Field(
        default=None, description="iOS: the installed app's bundle id, e.g. com.apple.Preferences"
    )
    device: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = Field(default=None, description="Device name, e.g. emulator-5554, Pixel 7, iPhone 15")
    udid: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:._-]{1,100}$")] | None = Field(default=None, description="A local device's id (adb serial or simulator UDID)")
    platform_version: Annotated[str, StringConstraints(pattern=r"^[0-9]+(?:\.[0-9]+){0,2}$")] | None = Field(default=None, description="OS version, e.g. 14 or 17.5")
    appium_url: AppiumUrl | None = Field(default=None, description="The Appium server (default from config: http://127.0.0.1:4723); not with cloud")
    cloud: CloudProvider | None = Field(default=None, description="Run on a device cloud instead of a local Appium server")
    no_reset: bool = Field(default=False, description="Keep the app's data between runs (Appium noReset)")
    capabilities: dict[Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")], JsonValue] = Field(
        default_factory=dict, max_length=50, description='Extra Appium capabilities, e.g. {"appium:language": "en"}; never credentials'
    )

    @field_validator("app")
    @classmethod
    def _app_reference(cls, value: str | None) -> str | None:
        if value is None or _CLOUD_APP.match(value):
            return value
        path = value.replace("\\", "/")
        if "://" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in path.split("/"):
            raise ValueError("app must be a path inside the project (relative, without '..') or a device cloud app id (bs://..., storage:..., lt://...)")
        if not path.rstrip("/").lower().endswith(APP_SUFFIXES):
            raise ValueError(f"app must be an app file ({', '.join(APP_SUFFIXES)}) or a device cloud app id (bs://..., storage:..., lt://...)")
        return value

    @field_validator("capabilities")
    @classmethod
    def _no_credentials(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        def keys(node: object) -> list[str]:
            if isinstance(node, dict):
                return [str(key) for key in node] + [inner for item in node.values() for inner in keys(item)]
            if isinstance(node, list):
                return [inner for item in node for inner in keys(item)]
            return []

        found = sorted({key for key in keys(value) if _CREDENTIAL_KEY.search(key.split(":")[-1])})
        if found:
            raise ValueError(
                f"capabilities may not hold credentials ({', '.join(found)}): name the environment variables that hold them in the project's config (mobile.browserstack / mobile.saucelabs / mobile.testmu)"
            )
        own = sorted(_OWN_CAPABILITIES.intersection(value))
        if own:
            raise ValueError(f"set {', '.join(own)} with the app's own fields (platform, app, package, activity, bundle_id, device, udid, platform_version), not in capabilities")
        return value

    @model_validator(mode="after")
    def _one_app(self) -> MobileApp:
        if self.platform == "android" and self.bundle_id is not None:
            raise ValueError("bundle_id is for iOS apps; an Android app uses package (and activity)")
        if self.platform == "ios" and (self.package is not None or self.activity is not None):
            raise ValueError("package and activity are for Android apps; an iOS app uses bundle_id")
        if self.activity is not None and self.package is None:
            raise ValueError("activity needs package")
        if self.app is None and self.package is None and self.bundle_id is None:
            raise ValueError("name the app: app (a file in the project or a device cloud app id), package (Android) or bundle_id (iOS)")
        if self.app is not None and _CLOUD_APP.match(self.app) and self.cloud is None:
            raise ValueError("a device cloud app id (bs://..., storage:..., lt://...) needs cloud: browserstack, saucelabs or testmu")
        if self.cloud is not None and self.appium_url is not None:
            raise ValueError("cloud and appium_url do not go together: a device cloud has its own server (configure it in mobile.<provider>.hub_url)")
        if self.cloud is not None and self.app is None:
            raise ValueError("a device cloud runs an app you give it: set app to a file in the project or the cloud's app id")
        if self.cloud is not None and self.udid is not None:
            raise ValueError("udid is for a local device; a device cloud picks its device by device and platform_version")
        return self

    @property
    def app_id(self) -> str:
        """The app's name for people: its package, bundle id or file."""
        return self.package or self.bundle_id or self.app or self.platform


def check_web_settings(mobile: object, device: str | None, viewport: str | None, browser: str | None, options: BrowserOptions | None) -> None:
    """Raise ValueError for browser settings that cannot go together (shared by cases and saved environments)."""
    if mobile is not None and (device is not None or viewport is not None or browser is not None or options is not None):
        raise ValueError("a native app (mobile) runs on a device, not in a browser: leave out device, viewport, browser and browser_options")
    if options is not None:
        options.check_browser(browser)
    if device is not None and engine_of(browser) == "firefox":
        from testzeus_hercules.cases.devices import context_options

        if context_options(device).get("is_mobile"):
            raise ValueError(f"Firefox cannot emulate the phone or tablet {device!r} (no mobile mode): use chromium, chrome, msedge or webkit")


class EnvironmentRef(_Model):
    """Where the case runs: a named environment and/or explicit overrides.

    ``device`` emulates a phone or tablet in the browser (a Playwright device profile: viewport, user agent, scale,
    touch). ``mobile`` tests a native app on a device instead of a web page: its steps run through Appium.
    """

    ref: NonEmpty | None = None
    base_url: Annotated[str, StringConstraints(pattern=BASE_URL_PATTERN)] | None = None
    viewport: Annotated[str, StringConstraints(pattern=VIEWPORT_PATTERN)] | None = Field(default=None, description="WIDTHxHEIGHT, e.g. 1280x800 (with device: replaces the device's viewport)")
    browser: Browser | None = Field(default=None, description="chromium (default), chrome, msedge, firefox or webkit")
    browser_options: BrowserOptions | None = Field(default=None, description="Playwright launch and context options: locale, timezone, auth state, headers, proxy, ...")
    device: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = Field(default=None, description="Mobile web emulation: a Playwright device profile, e.g. iPhone 15 or Pixel 7")
    mobile: MobileApp | None = Field(default=None, description="A native app on a device (Appium); its steps run on the device instead of a browser")

    @field_validator("device")
    @classmethod
    def _known_device(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from testzeus_hercules.cases.devices import UnknownDevice, canonical_name

        try:
            return canonical_name(value)
        except UnknownDevice as exc:
            raise ValueError(str(exc)) from None

    @model_validator(mode="after")
    def _web_or_app(self) -> EnvironmentRef:
        check_web_settings(self.mobile, self.device, self.viewport, self.browser, self.browser_options)
        return self

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is not None and not urlsplit(value).hostname:
            raise ValueError("base_url must be an http(s) URL with a host")
        return value


JsonOperator = Literal["eq", "ne", "contains", "not_contains", "matches", "exists", "not_exists", "not_empty", "gt", "lt", "all_gte", "all_lte", "count_eq", "eq_path"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
StatusCode = Union[Annotated[int, Field(ge=100, le=599)], Annotated[str, StringConstraints(pattern=r"^[1-5](?:[0-9]{2}|xx|XX)$")]]


class JsonCheck(_Model):
    """One JSONPath assertion: the value(s) at ``path`` compared with ``value`` by ``op``.

    Per-value operators must hold for every match; ``exists``, ``not_exists`` and
    ``not_empty`` take no value; ``count_eq`` counts an array's elements (or the matches);
    ``eq_path`` compares with the value at a second JSONPath given as ``value``.
    """

    path: NonEmpty = Field(description="JSONPath, e.g. $.items[0].id (SQL rows are a list: $[0].name)")
    op: JsonOperator = "eq"
    value: JsonValue = Field(default=None, description="Expected value (any JSON); omit for exists/not_exists/not_empty")


class HeaderCheck(_Model):
    """One response header assertion (names are case-insensitive)."""

    name: NonEmpty
    op: Literal["eq", "ne", "contains", "not_contains", "matches", "exists", "not_exists"] = "eq"
    value: str | None = None


class Capture(_Model):
    """Save one value of the response (a JSONPath into the JSON body, or a header) or of the rows, as ``{{name}}`` for later steps."""

    name: DataKey
    json_path: NonEmpty | None = None
    header: NonEmpty | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Capture:
        if (self.json_path is None) == (self.header is None):
            raise ValueError("a capture takes exactly one of json_path or header")
        if self.name in RESERVED_PLACEHOLDERS:
            raise ValueError(f"{self.name!r} is a built-in placeholder and cannot be saved")
        return self


class ApiAuth(_Model):
    """Request authentication; ``basic`` takes ``user:password``."""

    type: Literal["basic", "bearer"]
    value: NonEmpty


class ApiExpect(_Model):
    """Checks on an HTTP response; each one becomes an assertion ledger entry."""

    status: StatusCode | list[StatusCode] | None = Field(default=None, description="200, a class such as 2xx, or a list of accepted codes")
    json_schema: NonEmpty | dict[str, JsonValue] | None = Field(default=None, description="An inline JSON Schema, or a file reference such as spec.yaml#/components/schemas/Pet")
    openapi: NonEmpty | None = Field(default=None, description="A spec file; the body must match the schema it declares for this request's operation and status")
    json_path: list[JsonCheck] = Field(default_factory=list)
    headers: list[HeaderCheck] = Field(default_factory=list)
    body_contains: list[str] = Field(default_factory=list)
    max_time_ms: Annotated[int, Field(ge=1)] | None = None


class ApiCall(_Model):
    """An HTTP request and the checks on its response; without ``path`` the checks and saves apply to the previous response."""

    method: HttpMethod = "GET"
    path: NonEmpty | None = Field(default=None, description="Relative to the case's base_url, or an absolute http(s) URL")
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Union[str, list[str]]] = Field(default_factory=dict)
    body: JsonValue = Field(default=None, description="Request body: JSON for body_mode json (the default), text for raw, a field map for urlencoded/multipart")
    body_mode: Literal["json", "raw", "urlencoded", "multipart"] | None = None
    auth: ApiAuth | None = None
    expect: ApiExpect | None = None
    save: list[Capture] = Field(default_factory=list)

    @model_validator(mode="after")
    def _request_parts_need_a_request(self) -> ApiCall:
        if self.path is None and (self.headers or self.query or self.body is not None or self.auth is not None):
            raise ValueError("headers, query, body and auth belong to a request: give the step a path")
        if self.path is None and self.expect is None and not self.save:
            raise ValueError("an api step needs a path (a request), expect (checks) or save")
        return self


class SqlExpect(_Model):
    """Checks on a query's rows (a list of column -> value objects)."""

    rows: Annotated[int, Field(ge=0)] | None = Field(default=None, description="Exact row count")
    min_rows: Annotated[int, Field(ge=0)] | None = None
    max_rows: Annotated[int, Field(ge=0)] | None = None
    json_path: list[JsonCheck] = Field(default_factory=list)


class SqlCall(_Model):
    """One read-only SELECT on the database whose connection string is ``test_data[db]``; without ``query`` the checks apply to the previous rows."""

    db: DataKey | None = Field(default=None, description="Name of the test_data entry holding the connection string (usually a secret reference)")
    query: NonEmpty | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)
    expect: SqlExpect | None = None
    save: list[Capture] = Field(default_factory=list)

    @model_validator(mode="after")
    def _query_needs_db(self) -> SqlCall:
        if self.query is not None and self.db is None:
            raise ValueError("a query needs db: the test_data entry with its connection string")
        if self.query is None and (self.params or (self.expect is None and not self.save)):
            raise ValueError("a sql step needs a query, or expect/save for the previous query's rows")
        if any(capture.header is not None for capture in self.save):
            raise ValueError("sql captures take a json_path into the rows, not a header")
        return self


QualityKind = Literal["a11y", "visual", "layout"]
WcagLevel = Literal["A", "AA", "AAA"]
_A11Y_ONLY = ("level", "rules", "keyboard")
_VISUAL_ONLY = ("baseline", "mask", "threshold", "key_elements", "full_page")
_LAYOUT_ONLY = ("viewports",)


class QualityCheck(_Model):
    """A check of the page as it is now: accessibility (``a11y``), a ``visual`` baseline, or ``layout`` across widths.

    * ``a11y``: axe-core plus contrast, names, target size, keyboard and focus checks at WCAG ``level``;
      findings at or above ``fail_on`` fail the step, each with a fix value verified in the page.
    * ``visual``: compares the page with the stored ``baseline`` (pixels with ``mask`` regions left out,
      and the computed styles of ``key_elements``). A missing baseline is created and the step is
      ``unverified``; a difference above ``threshold`` is classified (intended change, layout break,
      content change) by the decision engine, and a layout break fails the step.
    * ``layout``: overlapping, clipped and off-screen elements at each width in ``viewports``.

    ``scope`` (a11y, layout) keeps only findings on elements inside that CSS selector.
    """

    kind: QualityKind
    scope: NonEmpty | None = Field(default=None, description="a11y/layout: report only elements inside this CSS selector")
    fail_on: Impact = Field(default="serious", description="Findings at or above this impact fail the step (critical > serious > moderate > minor)")
    level: WcagLevel = Field(default="AA", description="a11y: WCAG conformance level")
    rules: list[NonEmpty] = Field(default_factory=list, description="a11y: only these rule ids (axe ids such as color-contrast, or image-alt, target-size, focus-not-visible); empty for all")
    keyboard: bool = Field(default=True, description="a11y: also check keyboard reach and visible focus (presses Tab on the page)")
    baseline: Annotated[str, StringConstraints(pattern=BASELINE_NAME_PATTERN)] | None = Field(default=None, description="visual: baseline name, stored in .hercules/baselines/")
    mask: list[NonEmpty] = Field(default_factory=list, description="visual: CSS selectors of dynamic regions left out of the comparison")
    threshold: Annotated[float, Field(ge=0.0, le=1.0)] | None = Field(default=None, description="visual: share of compared pixels that may differ (default 0.001)")
    key_elements: list[NonEmpty] = Field(default_factory=list, description="visual: CSS selectors whose computed styles are compared property by property")
    full_page: bool = Field(default=False, description="visual: the whole scrollable page instead of the viewport")
    viewports: list[Annotated[int, Field(ge=200, le=4000)]] = Field(default_factory=list, max_length=6, description="layout: viewport widths in CSS px (default 375, 768, 1280)")

    @model_validator(mode="after")
    def _fields_for_kind(self) -> QualityCheck:
        if self.kind == "visual" and self.baseline is None:
            raise ValueError("a visual check needs a baseline name")
        other = {"a11y": _VISUAL_ONLY + _LAYOUT_ONLY, "visual": _A11Y_ONLY + _LAYOUT_ONLY + ("scope",), "layout": _A11Y_ONLY + _VISUAL_ONLY}[self.kind]
        wrong = sorted(name for name in other if name in self.model_fields_set)
        if wrong:
            raise ValueError(f"{', '.join(wrong)}: not used by {self.kind} checks")
        return self


ScanTag = Annotated[str, StringConstraints(pattern=SCAN_TAG_PATTERN)]


class SecurityScan(_Model):
    """A security scan of the case's own app with the nuclei scanner (installed separately; see ``hercules scan --install``).

    The scan targets the case's ``environment.base_url`` (``path`` narrows it to a URL under it), or the operations
    of an OpenAPI ``openapi`` spec inside the project, sent to the base URL. Templates are chosen by ``tags`` (default
    cve, exposure, misconfig). Intrusive, denial-of-service and fuzzing templates are left out unless ``intrusive`` is
    true. Findings at or above ``fail_on`` fail the step; every finding is evidence.
    """

    tags: list[ScanTag] = Field(default_factory=list, max_length=30, description="Template tags to run, e.g. cve, exposure, misconfig, xss, sqli (empty: cve, exposure, misconfig)")
    exclude_tags: list[ScanTag] = Field(default_factory=list, max_length=30, description="Template tags to leave out")
    path: NonEmpty | None = Field(default=None, description="A path under the base URL to scan instead of the base URL itself, e.g. /api")
    openapi: NonEmpty | None = Field(default=None, description="An OpenAPI spec file in the project: scan its operations (sent to the base URL)")
    headers: dict[str, str] = Field(default_factory=dict, description="Headers sent with every request; use {{name}} placeholders for secrets")
    auth: ApiAuth | None = None
    fail_on: Severity = Field(default="medium", description="Findings at or above this severity fail the step (critical > high > medium > low > info)")
    intrusive: bool = Field(default=False, description="Also run intrusive, denial-of-service and fuzzing templates (only on apps you may break)")
    rate_limit: Annotated[int, Field(ge=1, le=1000)] = Field(default=50, description="Most requests per second")
    timeout_s: Annotated[int, Field(ge=10, le=7200)] = Field(default=240, description="Time limit of the scan in seconds")

    @model_validator(mode="after")
    def _one_target(self) -> SecurityScan:
        if self.path is not None and self.openapi is not None:
            raise ValueError("a security scan takes path or openapi, not both")
        if not self.intrusive:
            risky = sorted(set(self.tags) & set(INTRUSIVE_TAGS))
            if risky:
                raise ValueError(f"tag(s) {', '.join(risky)} run intrusive templates: set intrusive: true to allow them (only on an app you may break)")
        return self

    def effective_tags(self) -> list[str]:
        return list(self.tags) or list(DEFAULT_SCAN_TAGS)


class Step(_Model):
    """One instruction in plain language, optionally with the outcome to verify and a structured API, SQL, quality or security payload."""

    action: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    expected: NonEmpty | None = None
    semantic: bool = Field(default=False, description="Compare `expected` by meaning (judge model) instead of literally")
    timeout_s: Annotated[int, Field(ge=1, le=7200)] | None = None
    api: ApiCall | None = None
    sql: SqlCall | None = None
    quality: QualityCheck | None = None
    security: SecurityScan | None = None

    @model_validator(mode="after")
    def _semantic_needs_expected(self) -> Step:
        if self.semantic and self.expected is None:
            raise ValueError("a semantic step needs an `expected` outcome to compare")
        if sum(payload is not None for payload in (self.api, self.sql, self.quality, self.security)) > 1:
            raise ValueError("a step takes one payload: api, sql, quality or security")
        return self


class CaseSource(_Model):
    """Provenance of a case written by a coding agent."""

    agent: NonEmpty | None = None
    commit: NonEmpty | None = None
    files_changed: list[NonEmpty] = Field(default_factory=list)


_SAVES_AS = re.compile(
    r"\b(?:save|saves|store|stores|remember|remembers|keep|keeps|capture|captures)\b[^\n]*?\bas\s+(?:\"|\u201c)?\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|"
    r"\b(?:save|saves|store|stores|remember|remembers|keep|keeps|capture|captures)\b[^\n]*?\bas\s+(?:\"|\u201c)([A-Za-z_][A-Za-z0-9_]*)(?:\"|\u201d)",
    re.IGNORECASE,
)


def saved_names_in(text: str) -> set[str]:
    """The names a plain-language step says it saves for later steps (``I save the response JSON "$.code" as "order_code"``)."""
    return {first or second for first, second in _SAVES_AS.findall(text or "")}


def placeholder_names_in(text: str) -> list[str]:
    return [raw.strip() for raw in _PLACEHOLDER.findall(text) if _NAME.fullmatch(raw.strip())]


_environment_names: Callable[[str], AbstractSet[str]] | None = None
_EXTRA_NAMES: ContextVar[frozenset[str]] = ContextVar("hercules_extra_placeholder_names", default=frozenset())


def set_environment_names(lookup: Callable[[str], AbstractSet[str]] | None) -> None:
    global _environment_names
    _environment_names = lookup


@contextmanager
def placeholder_names(names: AbstractSet[str]) -> Iterator[None]:
    token = _EXTRA_NAMES.set(frozenset(names) | _EXTRA_NAMES.get())
    try:
        yield
    finally:
        _EXTRA_NAMES.reset(token)


def outside_names(ref: str | None) -> frozenset[str]:
    found = set(_EXTRA_NAMES.get())
    if ref is not None and _environment_names is not None:
        found |= set(_environment_names(ref))
    return frozenset(found)


class TestCase(_Model):
    """A test case: environment, data, and ordered steps. NL-compiled cases start as drafts."""

    __test__: ClassVar[bool] = False  # not a pytest test class despite the Test prefix

    id: CaseId
    title: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    description: str | None = None
    priority: Priority = "medium"
    tags: list[NonEmpty] = Field(default_factory=list)
    environment: EnvironmentRef = Field(default_factory=EnvironmentRef)
    preconditions: list[NonEmpty] = Field(default_factory=list)
    # Keys double as placeholder names; the schema must reject keys outside the pattern.
    test_data: dict[DataKey, TestDataValue] = Field(default_factory=dict, json_schema_extra={"additionalProperties": False})
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)
    timeout_s: Annotated[int, Field(ge=10, le=7200)] = 300
    source: CaseSource | None = None
    status: CaseStatus = "ready"

    @field_validator("test_data")
    @classmethod
    def _no_builtin_shadowing(cls, value: dict[str, TestDataValue]) -> dict[str, TestDataValue]:
        reserved = sorted(RESERVED_PLACEHOLDERS.intersection(value))
        if reserved:
            raise ValueError(f"test_data key(s) {', '.join(reserved)} are reserved for built-in placeholders")
        return value

    @model_validator(mode="after")
    def _known_placeholders(self) -> TestCase:
        """Every ``{{name}}`` is test data, an environment variable, a built-in, or a value an earlier step saves; every ``db`` names test data."""
        known = BUILTIN_PLACEHOLDERS | DOWNLOAD_PLACEHOLDERS | set(self.test_data) | outside_names(self.environment.ref)
        unknown: list[str] = []
        malformed: list[str] = []

        def check(where: str, text: str, names: AbstractSet[str]) -> None:
            for raw in _PLACEHOLDER.findall(text):
                name = raw.strip()
                if not _NAME.fullmatch(name):
                    malformed.append(f"'{{{{{raw}}}}}' in {where} is not a name")
                elif name not in names:
                    unknown.append(f"{{{{{name}}}}} in {where}")

        for key, value in self.test_data.items():
            if isinstance(value, str):
                check(f"test_data[{key!r}]", value, known)
        saved: set[str] = set()
        for number, step in enumerate(self.steps, start=1):
            for where, text in _step_texts(number, step):
                check(where, text, known | saved)
            payload = step.api or step.sql
            if isinstance(payload, SqlCall) and payload.db is not None and payload.db not in self.test_data:
                malformed.append(f"steps[{number}].sql.db {payload.db!r} is not a test_data entry; add it with the connection string (a secret reference)")
            saved |= {capture.name for capture in (payload.save if payload is not None else [])}
            said = saved_names_in(step.action)
            malformed.extend(f"steps[{number}].action saves {{{{{name}}}}}, a built-in placeholder; save it under another name" for name in sorted(said & RESERVED_PLACEHOLDERS))
            saved |= said - RESERVED_PLACEHOLDERS
        if unknown or malformed:
            problems = malformed + (
                [f"unknown placeholder(s) {', '.join(unknown)}; known names: {', '.join(sorted(known | saved))}; define them in test_data or as variables of the case's environment"] if unknown else []
            )
            raise ValueError("; ".join(problems))
        return self


def _step_texts(number: int, step: Step) -> list[tuple[str, str]]:
    """Every text of a step that may hold ``{{name}}`` placeholders, with where it is."""
    texts = [(f"steps[{number}].action", step.action)]
    if step.expected is not None:
        texts.append((f"steps[{number}].expected", step.expected))
    for kind, payload in (("api", step.api), ("sql", step.sql), ("quality", step.quality), ("security", step.security)):
        if payload is not None:
            texts.extend((f"steps[{number}].{kind}", text) for text in _strings(payload.model_dump(exclude={"save"})))
    return texts


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for key, value in node.items() for text in (*_strings(key), *_strings(value))]
    if isinstance(node, list):
        return [text for item in node for text in _strings(item)]
    return []


# --------------------------------------------------------------------------- RunResult


class StepResult(_Model):
    """Outcome of one step; ``index`` is the 1-based position in the case."""

    index: Annotated[int, Field(ge=1)]
    action: str
    status: StepStatus
    expected: str | None = None
    actual: str | None = None
    duration_ms: Count = 0
    decisions: Count = 0
    evidence: list[str] = Field(default_factory=list)
    sequence: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="When the step ran, 1-based, when that differs from its index (a field it depends on ran first); null when it ran in the written order"
    )
    notes: list[str] = Field(default_factory=list, description="What the run changed about the step: its order, a value re-entered the way a field asks for it")


class AssertionCounts(_Model):
    """Assertion ledger totals."""

    total: Count = 0
    passed: Count = 0
    failed: Count = 0
    unverified: Count = 0

    @model_validator(mode="after")
    def _within_total(self) -> AssertionCounts:
        if self.passed + self.failed + self.unverified > self.total:
            raise ValueError("passed + failed + unverified assertions exceed the total")
        return self


class FailureInfo(_Model):
    """Why a run failed: a model's classification (``unclassified`` when it is not sure) and a factual summary."""

    category: FailureCategory
    confidence: Unit
    signals: list[str] = Field(default_factory=list)
    summary: str | None = None


class EvidencePaths(_Model):
    """Locations of run artifacts, relative to the run directory.

    ``junit`` (JUnit XML of the steps), ``console`` (``console.jsonl``: the page's console
    messages and errors) and ``network`` (``network.jsonl``: the page's requests and
    responses, secrets and auth headers redacted) are written by every run that has them.
    ``video`` is the run's recording when ``browser_options.record_video`` is set; ``trace``, ``report_md`` and
    ``bundle`` are not produced by this version and stay null.
    """

    junit: str | None = None
    report_md: str | None = None
    trace: str | None = None
    video: str | None = None
    network: str | None = None
    console: str | None = None
    bundle: str | None = None


RunMode = Literal["pure", "hybrid", "test"]


class EngineInfo(_Model):
    """Which engines produced the result.

    ``mode`` is ``pure`` (the local model does everything), ``hybrid`` (JEV decides,
    the local model does the rest) or ``test`` (a model-free test decider).
    """

    llm: str | None = None
    decider: str | None = None
    egress: EgressPolicy = "local"
    mode: RunMode | None = None
    decider_model: str | None = None
    jev_calls: Count = 0
    # Decisions asked of a local Clef server (pure mode with Clef configured); JEV calls are counted separately.
    clef_calls: Count = 0
    fallbacks: Count = 0
    # Local-model calls a caching proxy (llm.cache: allow) answered from its cache; their timings say nothing about the model.
    llm_cache_hits: Count = 0
    # Local-model calls (cache hits excluded) that went to a hosted endpoint, whose provider bills them though the run records no dollars.
    hosted_llm_calls: Count = 0


class CostInfo(_Model):
    """Model usage for the run: local-model tokens, and the hosted decision cost in USD (None when nothing was billed)."""

    tokens_in: Count = 0
    tokens_out: Count = 0
    usd: Annotated[float, Field(ge=0.0)] | None = None


class QualityVisual(_Model):
    """A visual comparison with a baseline: the pixel gate, the style deltas, the images and the decider's reading."""

    baseline: str
    created: bool = Field(default=False, description="The baseline did not exist and was created from this page")
    diff_ratio: Annotated[float, Field(ge=0.0)] = 0.0
    threshold: Annotated[float, Field(ge=0.0)] = 0.0
    changed: bool = False
    regions: list[list[int]] = Field(default_factory=list)
    style_deltas: list[StyleDelta] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list, description="Key elements no longer on the page")
    classification: DiffClassification | None = None
    note: str | None = Field(default=None, description="Why a difference was not classified (no decider that can see screenshots, no answer)")
    images: dict[str, str] = Field(default_factory=dict, description="before / after / diff evidence URIs")


class QualityPage(_Model):
    """One audit of one page state: by a quality step (``source: step``) or by the run's a11y/visual option (``run``)."""

    kind: Literal["a11y", "visual", "layout"]
    source: Literal["step", "run"]
    step: Annotated[int, Field(ge=1)] = Field(description="The step that ran the check, or after which the page was audited")
    url: str = ""
    title: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[FixSuggestion] = Field(default_factory=list)
    not_scanned: list[str] = Field(default_factory=list, description="Frames that could not be scanned (cross-origin)")
    visual: QualityVisual | None = None
    evidence: list[str] = Field(default_factory=list)
    error: str | None = None


class QualityCounts(_Model):
    """Distinct findings (by id) per impact, and totals of findings, fixes, verified fixes and audited pages."""

    critical: Count = 0
    serious: Count = 0
    moderate: Count = 0
    minor: Count = 0
    findings: Count = 0
    fixes: Count = 0
    verified_fixes: Count = 0
    pages: Count = 0


class QualitySection(_Model):
    """Every page audited in a run with its findings and verified fixes, and totals by impact.

    Run-level audits (the run's ``a11y``/``visual`` options) fail the run only when ``fail_on`` is set
    and a finding is at or above it; quality steps fail their own step.
    """

    a11y: bool = False
    visual: bool = False
    fail_on: Impact | None = None
    pages: list[QualityPage] = Field(default_factory=list)
    counts: QualityCounts = Field(default_factory=lambda: QualityCounts(), description="Computed from pages; any value given is replaced")

    @model_validator(mode="after")
    def _count(self) -> QualitySection:
        findings = {finding.id: finding for page in self.pages for finding in page.findings}
        fixes = [fix for page in self.pages for fix in page.fixes]
        counts = QualityCounts(
            **{impact: sum(finding.impact == impact for finding in findings.values()) for impact in IMPACT_ORDER},
            findings=len(findings),
            fixes=len(fixes),
            verified_fixes=sum(fix.verified for fix in fixes),
            pages=len(self.pages),
        )
        object.__setattr__(self, "counts", counts)  # derived, never trusted from input
        return self

    def gate_failures(self) -> list[Finding]:
        """Run-level findings at or above ``fail_on`` (none when ``fail_on`` is not set)."""
        if self.fail_on is None:
            return []
        gate = self.fail_on
        seen: dict[str, Finding] = {}
        for page in self.pages:
            if page.source == "run":
                seen.update({finding.id: finding for finding in page.findings if at_or_above(finding.impact, gate)})
        return list(seen.values())

    def top_fixes(self, limit: int = 3) -> list[tuple[Finding, FixSuggestion]]:
        """The most severe findings that have a verified fix, with that fix (verified first, then by impact)."""
        pairs: dict[str, tuple[Finding, FixSuggestion]] = {}
        for page in self.pages:
            by_id = {finding.id: finding for finding in page.findings}
            for fix in page.fixes:
                finding = by_id.get(fix.finding_ref)
                if finding is not None and (finding.id not in pairs or (fix.verified and not pairs[finding.id][1].verified)):
                    pairs[finding.id] = (finding, fix)
        ranked = sorted(pairs.values(), key=lambda pair: (not pair[1].verified, IMPACT_ORDER.index(pair[0].impact)))
        return ranked[:limit]

    def summary_line(self) -> str:
        counts = self.counts
        by_impact = ", ".join(f"{getattr(counts, impact)} {impact}" for impact in IMPACT_ORDER if getattr(counts, impact))
        fixes = f"; {counts.verified_fixes}/{counts.fixes} fixes verified" if counts.fixes else ""
        return f"Quality: {counts.findings} finding(s) on {counts.pages} page(s){f' ({by_impact})' if by_impact else ''}{fixes}"


_PROBLEM_STATUSES: tuple[StepStatus, ...] = ("failed", "error", "blocked", "unverified")
_STATUS_ORDER: tuple[StepStatus, ...] = ("passed", "failed", "error", "blocked", "unverified", "skipped")


class RunResult(_Model):
    """Result of one run, returned by the API and MCP tools and stored as ``run.json``."""

    run_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    status: RunStatus
    case_id: CaseId
    verdict_source: VerdictSource = "none"
    steps: list[StepResult] = Field(default_factory=list)
    assertions: AssertionCounts = Field(default_factory=AssertionCounts)
    failure: FailureInfo | None = None
    hints: list[str] = Field(default_factory=list)
    evidence: EvidencePaths = Field(default_factory=EvidencePaths)
    engines: EngineInfo = Field(default_factory=EngineInfo)
    cost: CostInfo = Field(default_factory=CostInfo)
    duration_s: Annotated[float, Field(ge=0.0)] = 0.0
    ui_url: str | None = None
    quality: QualitySection | None = None
    security: SecuritySection | None = Field(default=None, description="The run's security scans (security steps) and their findings")
    automation: AutomationSpec | None = Field(default=None, description="What the run did as framework-neutral automation (steps, verified locators, values, checks); see `hercules export`")
    plan: PlanSummary | None = Field(default=None, description="The run's plan in numbers (criteria results, amendments, lint); the plan itself is plan.json beside run.json")

    @field_validator("steps")
    @classmethod
    def _increasing_indexes(cls, value: list[StepResult]) -> list[StepResult]:
        for previous, current in zip(value, value[1:]):
            if current.index <= previous.index:
                raise ValueError(f"step index {current.index} must be greater than the previous index {previous.index}")
        return value

    @model_validator(mode="after")
    def _passed_is_clean(self) -> RunResult:
        if self.status != "passed":
            return self
        if not self.steps or any(step.status != "passed" for step in self.steps):
            raise ValueError("a passed run needs at least one step and every step passed")
        if self.assertions.failed or self.assertions.unverified:
            raise ValueError("a passed run cannot have failed or unverified assertions")
        if self.failure is not None:
            raise ValueError("a passed run cannot carry failure details")
        if self.quality is not None and self.quality.gate_failures():
            raise ValueError("a passed run cannot have quality findings at or above its fail_on impact")
        return self

    def first_problem_step(self) -> StepResult | None:
        """The first step that failed, errored, was blocked or stayed unverified."""
        return next((step for step in self.steps if step.status in _PROBLEM_STATUSES), None)

    def summary_text(self, max_chars: int = 2000) -> str:
        """A short plain-text summary for agents: status, failing step, expected vs actual, top hint."""
        if max_chars < _MIN_SUMMARY_CHARS:
            raise ValueError(f"max_chars must be at least {_MIN_SUMMARY_CHARS}")
        text = "\n".join(self._summary_lines())
        return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"

    def _summary_lines(self) -> list[str]:
        lines = [f"Run {self.run_id} {self.status.upper()} (case {self.case_id}, {self.duration_s:.1f}s, verdict: {self.verdict_source})"]
        if self.steps:
            counts = [f"{sum(step.status == status for step in self.steps)} {status}" for status in _STATUS_ORDER if any(step.status == status for step in self.steps)]
            lines.append(f"Steps: {', '.join(counts)} of {len(self.steps)} reported")
        if self.assertions.total:
            a = self.assertions
            lines.append(f"Assertions: {a.passed}/{a.total} passed, {a.failed} failed, {a.unverified} unverified")
        step = self.first_problem_step()
        if step is not None:
            lines.append(f"Step {step.index} {step.status.upper()}: {_clip(step.action)}")
            if step.expected is not None:
                lines.append(f"Expected: {_clip(step.expected)}")
            lines.append(f"Actual: {_clip(step.actual) if step.actual else '(not recorded)'}")
        if self.failure is not None:
            signals = f"; signals: {_clip(', '.join(self.failure.signals))}" if self.failure.signals else ""
            summary = f": {_clip(self.failure.summary)}" if self.failure.summary else ""
            lines.append(f"Failure: {self.failure.category} (confidence {self.failure.confidence:.2f}){summary}{signals}")
        if self.security is not None and self.security.scans:
            lines.append(self.security.summary_line())
        if self.quality is not None and self.quality.pages:
            lines.append(self.quality.summary_line())
            for finding, fix in self.quality.top_fixes(2):
                state = "verified" if fix.verified else "not verified"
                lines.append(f"Fix ({finding.impact}, {state}): {_clip(fix.snippet or f'{fix.property_or_attr}: {fix.suggested}')} for {_clip(finding.selector)}")
        if self.hints:
            lines.append(f"Hint: {_clip(self.hints[0])}")
        if self.ui_url:
            lines.append(f"Details: {self.ui_url}")
        return lines


def _clip(text: str) -> str:
    single_line = " ".join(text.split())
    return single_line if len(single_line) <= _FIELD_CLIP else single_line[: _FIELD_CLIP - 1] + "…"
