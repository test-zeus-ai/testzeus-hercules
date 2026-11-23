"""The result of one preflight check."""

from __future__ import annotations

from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict

CheckStatus = Literal["ok", "warn", "fail", "skipped"]

_SEVERITY: dict[str, int] = {"skipped": 0, "ok": 1, "warn": 2, "fail": 3}


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: CheckStatus
    detail: str
    fix: str | None = None


def overall_status(checks: Iterable[CheckResult]) -> CheckStatus:
    """The most severe status among the checks; skipped checks count as ok."""
    worst = max((_SEVERITY[check.status] for check in checks), default=1)
    return "ok" if worst <= 1 else ("warn" if worst == 2 else "fail")


def skipped(names: Iterable[str], reason: str) -> list[CheckResult]:
    return [CheckResult(name=name, status="skipped", detail=reason) for name in names]
