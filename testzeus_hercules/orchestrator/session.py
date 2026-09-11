"""Session state for the Hercules MCP server.

Kept dependency-free (stdlib only) so it can be imported anywhere without
pulling in the automation stack. A session groups everything a coding agent
configures for one testing engagement: the app URL, project path, PRD, the
artifacts generated along the way (code summary, standardized PRD, test plan)
and the summaries of runs executed in the session.

Sessions persist (credentials redacted) to ``.hercules/sessions.json`` under
``TESTZEUS_ROOT`` (or the current working directory when unset).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from testzeus_hercules.utils.redact import redact_url

_LOGGER = logging.getLogger(__name__)

# Upper bound for run summaries kept per session (persistence grows per run).
MAX_RUN_SUMMARIES = 10
# Upper bound on persisted sessions: each configure_session call creates one, and an
# unauthenticated local MCP client could otherwise grow sessions.json without bound.
MAX_SESSIONS = 100

# Backwards-compatible alias: the URL-redaction implementation (and its regex
# fallback) moved to testzeus_hercules.utils.redact so every logger shares one
# implementation; existing callers (mcp_server, tests) keep these names working.
_redact_url = redact_url


def _sessions_file_path() -> str:
    """Absolute path of the sessions persistence file (cwd or TESTZEUS_ROOT based)."""
    root = os.environ.get("TESTZEUS_ROOT", os.getcwd())
    return os.path.join(root, ".hercules", "sessions.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionState:
    """State of one MCP testing session."""

    session_id: str
    app_url: str
    project_path: str = ""
    test_type: str = "frontend"
    test_scope: str = "codebase"
    credentials: dict = field(default_factory=dict)
    prd_path: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    code_summary_path: str = ""
    standard_prd_path: str = ""
    test_plan_path: str = ""
    last_run_summaries: list = field(default_factory=list)
    app_url_reachable: bool | None = None

    def __repr__(self) -> str:  # keep credentials out of logs/repr
        return (
            f"SessionState(session_id={self.session_id!r}, app_url={_redact_url(self.app_url)!r}, "
            f"project_path={self.project_path!r}, test_type={self.test_type!r}, "
            f"test_scope={self.test_scope!r}, credentials={'***' if self.credentials else '{}'!r}, "
            f"prd_path={self.prd_path!r}, created_at={self.created_at!r}, "
            f"code_summary_path={self.code_summary_path!r}, standard_prd_path={self.standard_prd_path!r}, "
            f"test_plan_path={self.test_plan_path!r}, "
            f"last_run_summaries={len(self.last_run_summaries)} run(s), "
            f"app_url_reachable={self.app_url_reachable!r})"
        )

    def to_dict(self, redact_credentials: bool = False) -> dict:
        """Serialize to a JSON-compatible dict.

        Credentials are optionally masked and the app_url is always userinfo-
        redacted; only the last ``MAX_RUN_SUMMARIES`` run summaries are kept.
        """
        credentials = dict(self.credentials or {})
        if redact_credentials:
            credentials = {key: "***" for key in credentials}
        return {
            "session_id": self.session_id,
            "app_url": _redact_url(self.app_url),
            "project_path": self.project_path,
            "test_type": self.test_type,
            "test_scope": self.test_scope,
            "credentials": credentials,
            "prd_path": self.prd_path,
            "created_at": self.created_at,
            "code_summary_path": self.code_summary_path,
            "standard_prd_path": self.standard_prd_path,
            "test_plan_path": self.test_plan_path,
            "last_run_summaries": list(self.last_run_summaries)[-MAX_RUN_SUMMARIES:],
            "app_url_reachable": self.app_url_reachable,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        known = {name for name in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{key: value for key, value in (data or {}).items() if key in known})


def _redact(credentials: dict | None) -> dict:
    """Redact every credential value to '***' ({"username": "***"} style)."""
    return {key: "***" for key in (credentials or {})}


class SessionManager:
    """In-memory session registry with best-effort JSON persistence."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self.load_sessions()

    # -- creation / lookup -------------------------------------------------

    def create_session(
        self,
        app_url: str,
        project_path: str = "",
        test_type: str = "frontend",
        test_scope: str = "codebase",
        credentials: dict | None = None,
        prd_path: str = "",
        touch_recent: bool = True,
    ) -> SessionState:
        """Create a new session, register it, persist it and return it.

        ``touch_recent=False`` marks a synthetic/ephemeral session (e.g. the
        legacy run_test alias auto-creating one): it must not repoint the
        "most recent session" that empty ``session_id`` lookups resolve to.
        """
        if test_type not in ("frontend", "backend", "mixed"):
            raise ValueError(f"Invalid test_type {test_type!r}; expected one of: frontend, backend, mixed")
        if test_scope not in ("codebase", "diff"):
            raise ValueError(f"Invalid test_scope {test_scope!r}; expected one of: codebase, diff")

        state = SessionState(
            session_id=f"sess_{secrets.token_hex(4)}",
            app_url=app_url or "",
            project_path=project_path or "",
            test_type=test_type,
            test_scope=test_scope,
            credentials=dict(credentials or {}),
            prd_path=prd_path or "",
        )
        self._sessions[state.session_id] = state
        while len(self._sessions) > MAX_SESSIONS:
            oldest = next(iter(self._sessions))
            if oldest == state.session_id:
                break
            del self._sessions[oldest]
        if not touch_recent and len(self._sessions) > 1:
            # Most-recent = last entry of the insertion-ordered dict; re-insert
            # the synthetic session FIRST so session_id="" keeps resolving to
            # the session that existed before it. (An empty registry keeps the
            # synthetic session — there is nothing else to resolve to.)
            others = {sid: session for sid, session in self._sessions.items() if sid != state.session_id}
            self._sessions.clear()
            self._sessions[state.session_id] = state
            self._sessions.update(others)
        self.save_session_state(state)
        return state

    def get_session(self, session_id: str) -> SessionState:
        """Return the session with the given id; raise KeyError when unknown."""
        try:
            return self._sessions[session_id]
        except KeyError:
            # Deliberately no session-id enumeration: this error surfaces to
            # MCP clients, and listing every known id would leak registry
            # contents on a multi-tenant server.
            raise KeyError(f"Unknown session_id {session_id!r}.") from None

    def resolve_session(self, session_id: str = "") -> SessionState:
        """Return the named session, or the most recent one when session_id is empty."""
        if session_id:
            return self.get_session(session_id)
        if not self._sessions:
            raise ValueError("No session. Call hercules_configure_session first.")
        # dict preserves insertion order; the last entry is the most recent session.
        return self._sessions[next(reversed(self._sessions))]

    def list_sessions(self) -> list[SessionState]:
        """Return all known sessions in creation order."""
        return list(self._sessions.values())

    # -- persistence -------------------------------------------------------

    def save_session_state(self, state: SessionState) -> None:
        """Persist all sessions to disk (credentials redacted); best-effort, never raises."""
        self._sessions[state.session_id] = state
        try:
            path = _sessions_file_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {sid: session.to_dict(redact_credentials=True) for sid, session in self._sessions.items()}
            # Atomic write (tmp + os.replace) so a crash mid-write cannot leave
            # a truncated sessions.json behind.
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            # Session metadata (app URLs, project paths) is operator-private:
            # keep it unreadable to other local users on shared hosts.
            if hasattr(os, "chmod"):
                os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
        except Exception as exc:
            _LOGGER.warning("Could not persist sessions to %s: %s", _sessions_file_path(), exc)

    def load_sessions(self) -> None:
        """Load sessions from disk if present; best-effort, never raises.

        Credentials were redacted before persisting, so reloaded sessions
        carry '***' placeholders.
        """
        try:
            path = _sessions_file_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                _LOGGER.warning(
                    "Could not load sessions from %s: unexpected payload of type %s; expected a JSON object.",
                    _sessions_file_path(),
                    type(payload).__name__,
                )
                return
            malformed: list[str] = []
            for sid, data in payload.items():
                try:
                    self._sessions[sid] = SessionState.from_dict(data)
                except (TypeError, AttributeError):
                    # A non-dict entry raises AttributeError (.items() on the
                    # payload), a dict missing required fields raises TypeError;
                    # either way skip the entry, not the whole load.
                    malformed.append(str(sid))
            if malformed:
                # Warn once per load instead of per entry; cap the key list so a
                # large corrupted payload cannot flood the log.
                shown = ", ".join(malformed[:10])
                more = f" (+{len(malformed) - 10} more)" if len(malformed) > 10 else ""
                _LOGGER.warning(
                    "Skipped %d malformed session entries in %s: %s%s",
                    len(malformed),
                    _sessions_file_path(),
                    shown,
                    more,
                )
        except Exception as exc:
            _LOGGER.warning("Could not load sessions from %s: %s", _sessions_file_path(), exc)


_MANAGER: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Return the module-level SessionManager singleton (created on first use)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = SessionManager()
    return _MANAGER
