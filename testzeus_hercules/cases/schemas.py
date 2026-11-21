"""Published JSON Schemas for :class:`TestCase`, :class:`RunResult` and :class:`~testzeus_hercules.cases.plan.Plan`.

The generated files live in the ``schemas/`` directory beside this module and are
checked by a test against freshly generated ones, so they never drift. Regenerate
with ``python -m testzeus_hercules.cases.schemas``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from testzeus_hercules.cases.models import RunResult, TestCase
from testzeus_hercules.cases.plan import Plan

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_MODELS: dict[str, type[BaseModel]] = {
    "test_case.schema.json": TestCase,
    "run_result.schema.json": RunResult,
    "plan.schema.json": Plan,
}


def build_json_schemas() -> dict[str, dict[str, Any]]:
    """Generate every published schema, keyed by file name."""
    return {name: {"$schema": JSON_SCHEMA_DIALECT, **model.model_json_schema()} for name, model in _MODELS.items()}


def render_schema(schema: dict[str, Any]) -> str:
    """Serialise a schema deterministically (sorted keys, two-space indent, trailing newline)."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_json_schemas(out_dir: Path | str = SCHEMA_DIR) -> list[Path]:
    """Write every schema into ``out_dir`` (created if missing) and return the paths."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in build_json_schemas().items():
        path = target / name
        path.write_text(render_schema(schema), encoding="utf-8")
        written.append(path)
    return written


def load_json_schema(name: str) -> dict[str, Any]:
    """Read a published schema by file name; raises ``KeyError`` for unknown names."""
    if name not in _MODELS:
        raise KeyError(f"unknown schema {name!r}; published: {', '.join(sorted(_MODELS))}")
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    for exported in export_json_schemas():
        print(exported)
