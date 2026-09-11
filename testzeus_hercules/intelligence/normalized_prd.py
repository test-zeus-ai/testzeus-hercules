"""Pydantic models for a normalized Product Requirements Document (PRD).

A normalized PRD is the canonical, structured representation of a product
requirements document that the Test Intelligence modules operate on: a list of
individual requirements, each with a stable id, title, description and
acceptance criteria, plus project level metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PRDRequirement(BaseModel):
    """A single, testable product requirement extracted from a PRD source."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    source: str = ""


class NormalizedPRD(BaseModel):
    """Structured representation of a full PRD document."""

    project_name: str = "Unnamed Project"
    summary: str = ""
    requirements: list[PRDRequirement] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


def prd_to_json(prd: NormalizedPRD, path: str) -> None:
    """Serialize a :class:`NormalizedPRD` to a JSON file."""
    Path(path).write_text(prd.model_dump_json(indent=2), encoding="utf-8")


def prd_from_json(path: str) -> NormalizedPRD:
    """Load a :class:`NormalizedPRD` from a JSON file produced by :func:`prd_to_json`."""
    return NormalizedPRD.model_validate_json(Path(path).read_text(encoding="utf-8"))
