"""Plain-text rendering of the ``hercules doctor`` report."""

from __future__ import annotations

import json

from typing import TYPE_CHECKING

from testzeus_hercules.cli.style import Style
from testzeus_hercules.llm.tiers import GIB

if TYPE_CHECKING:
    from testzeus_hercules.cli.doctor import DoctorReport

_LABELS = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "skipped": "skip"}


def render_text(report: DoctorReport, style: Style | None = None) -> str:
    """The report as text; ``style`` colours the statuses (plain text without it)."""
    paint = style or Style()
    lines = [paint.bold(f"Hercules doctor ({report.hercules_version})"), ""]
    lines += _settings_lines(report, paint)
    lines += ["", paint.bold("Checks")]
    width = max((len(check.name) for check in report.checks), default=0)
    for check in report.checks:
        label = paint.status(check.status, f"{_LABELS[check.status]:<4}")
        lines.append(f"  {label}  {check.name:<{width}}  {check.detail}")
        if check.fix and check.status in ("warn", "fail"):
            lines.append(f"  {'':<4}  {'':<{width}}  {paint.accent('fix:')} {check.fix}")
    lines += ["", f"Result: {paint.status(report.status, report.status.upper())} (exit {report.exit_code})", ""]
    return "\n".join(lines)


def _settings_lines(report: DoctorReport, paint: Style) -> list[str]:
    rows: list[tuple[str, str]] = []
    settings = report.settings
    if settings is not None:
        model = settings.model
        if report.model_id:
            model = f"{settings.model} -> {report.model_id}" if report.model_id != settings.model else report.model_id
        elif report.requested_model and report.requested_model != settings.model:
            model = f"{settings.model} -> {report.requested_model}"
        rows += [("provider", settings.provider), ("model", model)]
    if report.endpoint is not None:
        rows.append(("endpoint", f"{report.endpoint.base_url} ({'local' if report.endpoint.is_local else 'remote'})"))
    if report.server_version:
        rows.append(("server", report.server_version))
    if settings is not None:
        rows += [("egress", settings.egress_policy), ("api key env", settings.api_key_env or "-")]
        if settings.request_params:
            rows.append(("request params", json.dumps(settings.request_params, sort_keys=True)))
        if settings.headers:
            rows.append(("headers", ", ".join(f"{name} from ${variable}" for name, variable in sorted(settings.headers.items()))))
        if settings.provider == "litellm" or settings.cache != "allow":
            rows.append(("proxy cache", settings.cache))
        rows.append(("telemetry", "on" if settings.telemetry_enabled else "off"))
    if report.decider is not None:
        rows.append(("decider", f"{report.decider.mode}: {report.decider.decider} [{report.decider.reason}]"))
    if report.tier is not None:
        memory = "unknown memory" if report.memory_bytes is None else f"{report.memory_bytes / GIB:.1f} GiB memory"
        tier = report.tier
        rows.append(("tier", f"{tier.name} ({'auto' if report.tier_auto else 'set by llm.tier'}, {memory})"))
        rows.append(
            (
                "budgets",
                f"context {tier.context_tokens} tokens, observation {tier.observation_max_elements} elements / {tier.observation_max_tokens} tokens, "
                f"vision {tier.vision_tokens} tokens, {tier.parallel_runs} parallel run(s)",
            )
        )
    if not rows:
        return []
    width = max(len(label) for label, _ in rows)
    return [paint.bold("Settings"), *(f"  {paint.dim(f'{label:<{width}}')}  {value}" for label, value in rows)]
