"""Visual, layout and accessibility quality checks with verified fix suggestions.

Code measures every value (axe-core, computed styles, element boxes, pixel diffs); a language model
only writes text (alt text, labels, rationale). Every suggested fix is applied to the live page as a
scoped override and re-checked before it is reported, so ``FixSuggestion.verified`` says whether it
actually resolved the finding.

Names are imported on first use, so the result models (:mod:`~testzeus_hercules.quality.findings`)
can be used without loading the browser-side runners.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, str] = {
    "audit_accessibility": "audit",
    "audit_layout": "audit",
    "DEFAULT_TAGS": "axe",
    "AxeResult": "axe",
    "run_axe": "axe",
    "KeyboardResult": "checks",
    "check_contrast": "checks",
    "check_images": "checks",
    "check_keyboard": "checks",
    "check_names": "checks",
    "check_target_size": "checks",
    "run_checks": "checks",
    "DiffClassification": "findings",
    "Finding": "findings",
    "FixSuggestion": "findings",
    "QualityReport": "findings",
    "StyleDelta": "findings",
    "likely_file": "fixes",
    "suggest_fix": "fixes",
    "suggest_fixes": "fixes",
    "verify_fix": "fixes",
    "DEFAULT_WIDTHS": "layout",
    "LayoutResult": "layout",
    "check_layout": "layout",
    "Baseline": "visual",
    "VisualDiff": "visual",
    "capture_baseline": "visual",
    "classify_diff": "visual",
    "compare": "visual",
    "load_baseline": "visual",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
