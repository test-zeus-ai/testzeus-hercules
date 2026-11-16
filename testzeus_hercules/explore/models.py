"""The data an exploration session works with: what was asked, the charters, every turn, clues and findings.

A session explores an app from a starting state for a set time, with a mission. Explorers run **charters** in
**turns**; an oracle that notices something raises a **clue**; a clue that reproduces from a fresh start becomes a
**finding**, with a failing test case. Severity is proposed by a model and set by a person; every change is audited.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

__all__ = [
    "Budget",
    "Charter",
    "Clue",
    "EnvironmentInfo",
    "ExploreSpec",
    "Finding",
    "LEVERS",
    "Move",
    "Promise",
    "Scope",
    "SessionView",
    "TriageEvent",
    "Turn",
    "now_iso",
]

Severity = Literal["P0", "P1", "P2", "P3"]
SEVERITIES: tuple[str, ...] = ("P0", "P1", "P2", "P3")
Lever = Literal["inputs", "order", "conditions", "time", "people"]
LEVERS: tuple[str, ...] = ("inputs", "order", "conditions", "time", "people")
OracleTier = Literal["code", "invariant", "judged"]
OracleKind = Literal["page_error", "console_error", "server_error", "request_failed", "dead_action", "broken_link", "a11y", "layout", "invariant", "persistence", "judged"]
Isolation = Literal["accounts", "partition", "serial-writes", "single"]
GateOn = Literal["confirmed", "proposed-hard"]
TriageState = Literal["new", "confirmed", "duplicate", "by_design", "wont_fix", "cannot_reproduce", "known_issue", "in_tracker", "verify_fixed", "closed"]
# Triage states that keep a signature from being reported as new again.
SUPPRESSED: frozenset[str] = frozenset({"by_design", "wont_fix", "known_issue", "duplicate"})
OPEN: frozenset[str] = frozenset({"new", "confirmed", "in_tracker", "verify_fixed"})
Flaky = Literal["none", "app", "replay"]
DirectMove = Literal["back", "reload", "resubmit", "offline_once", "clear_cookies", "wait", "second_tab", "viewport", "goto"]

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HOST = r"^[A-Za-z0-9.-]+(:\d{1,5})?$"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- the request


class Scope(_Model):
    """Where explorers may go and what they may never do."""

    allow_hosts: list[Annotated[str, StringConstraints(pattern=_HOST)]] = Field(default_factory=list, description="hosts explorers may reach (default: the base URL's)")
    deny_paths: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(default_factory=list, description="path prefixes never opened or posted to, e.g. /logout")
    forbid: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(default_factory=list, description="actions never done, by words in their name (added to the defaults)")
    allow: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(default_factory=list, description="default forbidden words this session may use anyway")
    rate_per_s: Annotated[float, Field(gt=0, le=50)] = 5.0

    @field_validator("deny_paths")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        bad = [path for path in value if not path.startswith("/")]
        if bad:
            raise ValueError(f"deny_paths are paths starting with '/': {bad[0]!r}")
        return value


class Budget(_Model):
    time_s: Annotated[int, Field(ge=60, le=86_400)] = 1800
    max_cost_usd: Annotated[float, Field(ge=0)] | None = None
    target_findings: Annotated[int, Field(ge=1)] | None = Field(default=None, description="stop once this many findings at or above the severity floor are confirmed")
    stall_charters: Annotated[int, Field(ge=1, le=50)] = Field(default=4, description="stop when this many charters in a row add no coverage")
    recon_share: Annotated[float, Field(gt=0, le=0.5)] = 0.1
    reproduce_grace_s: Annotated[int, Field(ge=0, le=3600)] = 300


class Promise(_Model):
    """Something the app should do, checked after every turn on the route it names.

    ``specified`` promises come from requirements or a person; ``learned`` ones from recon (they can encode today's bug,
    so they never back a P0 or P1 alone). Kinds: the text is shown (``text_visible``) or never shown (``text_absent``)
    on the route; two numbers on the page are equal (``equal``: ``target`` and ``other`` are CSS selectors); text a
    form saved is still there after a reload (``persist``).
    """

    id: Annotated[str, StringConstraints(pattern=_ID.pattern)]
    text: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    kind: Literal["text_visible", "text_absent", "equal", "persist", "not_negative"]
    route: Annotated[str, StringConstraints(min_length=1, max_length=200)] = "/"
    target: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    other: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    source: Literal["specified", "learned"] = "specified"
    requirement: Annotated[str, StringConstraints(max_length=200)] | None = None

    @model_validator(mode="after")
    def _pair(self) -> Promise:
        if self.kind == "equal" and not self.other:
            raise ValueError("an 'equal' promise needs 'other' (the second number's selector)")
        return self


class ExploreSpec(_Model):
    """What a session was asked to do. The swarm size (``workers``, ``reproducers``) is the user's and never changed by Hercules."""

    mission: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}(:[a-z-]{1,30})?$")] = "p1"
    prompt: Annotated[str, StringConstraints(max_length=4000)] | None = Field(default=None, description="replaces the mission's target text")
    start_case: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = Field(default=None, description="a case that reaches the starting state (path in the project)")
    environment: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    base_url: Annotated[str, StringConstraints(pattern=r"^https?://[^\s/?#]+")] | None = None
    start_url: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = Field(
        default=None, description="where explorers begin (default: where the start case ends, else the base URL)"
    )
    budget: Budget = Field(default_factory=Budget)
    scope: Scope = Field(default_factory=Scope)
    workers: Annotated[int, Field(ge=1, le=64)] = 2
    reproducers: Annotated[int, Field(ge=1, le=16)] = 1
    allow_more_workers: bool = False
    levers: list[Lever] = Field(default_factory=lambda: list(LEVERS))  # type: ignore[arg-type]
    personas: list[Annotated[str, StringConstraints(pattern=r"^[a-z-]{1,30}$")]] = Field(default_factory=list)
    severity_floor: Severity = "P2"
    gate_on: GateOn = "confirmed"
    reproduce_runs: Annotated[int, Field(ge=1, le=9)] = 3
    isolation: Isolation = "serial-writes"
    accounts: list[Annotated[str, StringConstraints(pattern=r"^[A-Z_][A-Z0-9_]*$")]] = Field(
        default_factory=list, description="accounts mode: one start-case variable per explorer (test data name for the user)"
    )
    promises: list[Promise] = Field(default_factory=list)
    changed_paths: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(default_factory=list, description="the changed mission: routes, components or files that changed")
    build: Annotated[str, StringConstraints(max_length=200)] | None = None
    build_header: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,100}$")] | None = None
    teardown_case: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    ask_before_forbidden: bool = False
    production_ok: bool = False
    dry_run: bool = False

    @field_validator("base_url")
    @classmethod
    def _no_credentials(cls, value: str | None) -> str | None:
        if value is not None and "@" in (urlsplit(value).netloc or ""):
            raise ValueError("base_url must not carry a user name or password: sign in with a start case instead")
        return value

    @model_validator(mode="after")
    def _where(self) -> ExploreSpec:
        if self.start_case is None and self.environment is None and self.base_url is None:
            raise ValueError("say where to explore: a start case, a saved environment or a base URL")
        if not self.levers:
            raise ValueError("choose at least one lever")
        if self.isolation == "accounts" and len(self.accounts) < self.workers:
            raise ValueError(f"accounts isolation needs one account per explorer: {self.workers} explorers, {len(self.accounts)} accounts")
        return self


# --------------------------------------------------------------------------- the work


class Charter(_Model):
    """One sentence with a timebox: what an explorer does next."""

    id: Annotated[str, StringConstraints(pattern=_ID.pattern)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    kind: Literal["setup", "recon", "explore", "dig"] = "explore"
    area: Annotated[str, StringConstraints(max_length=200)] = "/"
    levers: list[Lever] = Field(default_factory=list)
    persona: str | None = None
    timebox_s: Annotated[int, Field(ge=30, le=3600)] = 300
    max_turns: Annotated[int, Field(ge=1, le=500)] = 40
    priority: Annotated[float, Field(ge=0, le=1)] = 0.5
    origin: Literal["coordinator", "person"] = "coordinator"
    pinned: bool = False
    writes: bool = Field(default=True, description="the charter may change server data (serial-writes isolation runs these one at a time)")
    status: Literal["queued", "running", "done", "vetoed", "failed", "stopped"] = "queued"
    run_id: str | None = None
    explorer: int | None = None
    notes: list[str] = Field(default_factory=list)
    debrief: dict[str, Any] | None = None
    dig_of: str | None = Field(default=None, description="dig charters: the clue they follow")


class Move(_Model):
    """One push, as the explorer chose it: plain-language step text, or a direct browser move the step runner cannot express."""

    lever: Lever
    variant: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    direct: DirectMove | None = None
    element: Annotated[str, StringConstraints(max_length=300)] | None = Field(default=None, description="the element's role and name, for coverage and the forbid check")
    value: Annotated[str, StringConstraints(max_length=12_000)] | None = None
    clicks: Annotated[str, StringConstraints(max_length=300)] | None = Field(default=None, description="the control the move clicks or submits (what the forbid check reads)")
    writes: bool = False


class Turn(_Model):
    n: Annotated[int, Field(ge=1)]
    move: Move
    status: str
    url_before: str = ""
    url_after: str = ""
    state: str | None = Field(default=None, description="the page state key after the turn")
    duration_ms: int = 0
    automation: dict[str, JsonValue] | None = Field(default=None, description="the step's recorded actions and locators (AutomationStep), for a model-free replay")
    skipped: str | None = Field(default=None, description="why the move was not done (forbidden, out of scope)")


class Clue(_Model):
    """An anomaly an oracle raised, not reproduced yet."""

    id: Annotated[str, StringConstraints(pattern=_ID.pattern)]
    oracle: OracleKind
    tier: OracleTier
    signature: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    detail: Annotated[str, StringConstraints(max_length=4000)] = ""
    route: str = "/"
    expected: Annotated[str, StringConstraints(max_length=1000)] = ""
    actual: Annotated[str, StringConstraints(max_length=2000)] = ""
    confidence: Annotated[float, Field(ge=0, le=1)] = 1.0
    charter: str | None = None
    run_id: str | None = None
    start_url: str = ""
    turns: list[Turn] = Field(default_factory=list, description="the turns from the starting state to the anomaly")
    promise: str | None = None
    opinion: bool = Field(default=False, description="a judged oracle said this is a design preference, not a bug: a UX note")
    at: str = Field(default_factory=now_iso)


class EnvironmentInfo(_Model):
    """Where a finding happened, captured when the session started."""

    build: str = "unknown"
    base_url: str = ""
    browser: str = "chromium"
    browser_version: str = ""
    viewport: str = ""
    locale: str = ""
    timezone: str = ""
    account: str = ""
    hercules_version: str = ""
    llm: str = ""
    decider: str = ""
    session: str = ""
    charter: str = ""
    at: str = Field(default_factory=now_iso)


class TriageEvent(_Model):
    at: str = Field(default_factory=now_iso)
    by: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    field: Literal["state", "severity", "priority", "tracker", "case"]
    before: str | None = None
    after: str | None = None
    reason: Annotated[str, StringConstraints(max_length=1000)] = ""


class Finding(_Model):
    """A clue that reproduced from a fresh start, with its failing case. ``severity`` is a person's call."""

    id: Annotated[str, StringConstraints(pattern=_ID.pattern)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    signature: str
    oracle: OracleKind
    tier: OracleTier
    promise: str | None = None
    promise_source: Literal["specified", "learned"] | None = None
    requirement: str | None = None
    proposed_severity: Severity
    proposed_priority: Severity
    severity_reason: str = ""
    severity: Severity | None = None
    priority: Severity | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] = 1.0
    category: str = "product_bug"
    steps: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    minimized_from: int = 0
    reproduced_recorded: str = ""
    reproduced_interpreted: str = ""
    flaky: Flaky = "none"
    case_path: str | None = None
    # What a person decided about the case (see explore/cases.py): proposed (outside the suite), kept (known_bug in the
    # project's cases), regression (a ready case that fails if the bug returns) or rejected (never run, never proposed again).
    case: Literal["proposed", "kept", "regression", "rejected"] = "proposed"
    run_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    environment: EnvironmentInfo = Field(default_factory=EnvironmentInfo)
    state: TriageState = "new"
    duplicate_of: str | None = None
    tracker: str | None = None
    audit: list[TriageEvent] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list, description="every session that saw it")
    builds: list[str] = Field(default_factory=list, description="every build it was seen on")
    regression: bool = False
    first_seen: str = Field(default_factory=now_iso)
    last_seen: str = Field(default_factory=now_iso)

    @property
    def effective_severity(self) -> str:
        return self.severity or self.proposed_severity


class SessionView(_Model):
    """A session's status for the API, the CLI and the UI."""

    id: str
    status: Literal["starting", "recon", "exploring", "reproducing", "stopping", "finished", "failed", "stopped"]
    spec: ExploreSpec
    phase_detail: str = ""
    workers: int
    reproducers: int
    size_changes: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None
    elapsed_s: float = 0.0
    cost_usd: float = 0.0
    # True when part of cost_usd prices hosted model calls at explore.cost_per_decision_usd (the provider billed no dollars into the runs).
    cost_estimated: bool = False
    charters: list[Charter] = Field(default_factory=list)
    clues: int = 0
    findings: list[Finding] = Field(default_factory=list)
    ux_notes: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list)
    message: str = ""
    exit_code: int | None = None
