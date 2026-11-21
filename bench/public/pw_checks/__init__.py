"""Hand-written Playwright replays of the public demo-app scenarios, keyed by scenario name."""

from __future__ import annotations

from typing import Callable

from . import orangehrm, parabank, saucedemo, xyzbank
from .flow import Flow, StepFailed

CHECKS: dict[str, Callable[[Flow], None]] = {**saucedemo.CHECKS, **orangehrm.CHECKS, **parabank.CHECKS, **xyzbank.CHECKS}

__all__ = ["CHECKS", "Flow", "StepFailed"]
