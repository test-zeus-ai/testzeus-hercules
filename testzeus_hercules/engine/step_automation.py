"""What a step did, recorded for exporting the run as code (a mixin of the step graph's runner).

The step graph calls these as it goes: after an action succeeds (``_direct_record``,
``_element_record``), before an element is used (``_record_target``: the element's locators,
proven unique on the live page, and ``locators.json`` with every candidate tried), after the
checks ran (``_check_records``), and when the step ends (``_automation``). Recording never fails
a step: the step graph catches what goes wrong here and notes it. Typed values are recorded as
templates (see :func:`~testzeus_hercules.engine.automation.value_template`), never secret values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from testzeus_hercules.cases.automation import AutomationAction, AutomationCheck, AutomationStep, LocatorSpec, TargetSpec
from testzeus_hercules.cases.models import ApiCall, SecurityScan, Step, StepStatus
from testzeus_hercules.engine.automation import navigated_from, navigated_path, relative_url, value_template
from testzeus_hercules.engine.intent import StepIntent
from testzeus_hercules.engine.verify import Check, LedgerEntry

if TYPE_CHECKING:
    from testzeus_hercules.engine.step_graph import StepDeps, StepState

# Element actions as the automation spec records them; any other kind is performed as a click.
ELEMENT_KINDS = frozenset({"click", "fill", "select", "check", "uncheck", "hover", "press"})


class AutomationRecorder:
    """Records a step's actions, locators and checks; ``deps`` is the step runner's."""

    deps: StepDeps

    def _automation(self, index: int, step: Step, status: StepStatus, final: StepState) -> AutomationStep:
        """What the step did, as one step of the run's automation spec."""
        notes = list(final.get("auto_notes", ()))
        reading = final.get("service")
        if reading is not None and reading.call is not None:
            call = reading.call
            payload = call.model_dump(mode="json", exclude_defaults=True)
            if isinstance(call, ApiCall):
                return AutomationStep(index=index, action=step.action, status=status, kind="api", api=payload, payload_source=reading.source, notes=notes)
            if isinstance(call, SecurityScan):
                notes.append("the security scan runs the nuclei scanner; the export writes it as a comment with the command to run in your pipeline")
                return AutomationStep(index=index, action=step.action, status=status, kind="security", security=payload, payload_source=reading.source, faithful=False, notes=notes)
            query = getattr(self.deps.service, "query", None)
            dialect = getattr(getattr(query, "result", None), "dialect", None) or None
            return AutomationStep(index=index, action=step.action, status=status, kind="sql", sql=payload, sql_dialect=dialect, payload_source=reading.source, notes=notes)
        if final.get("mobile"):
            device, checks = list(final.get("auto_device", ())), list(final.get("auto_checks", ()))
            faithful = all(item.faithful for item in device) and all(item.faithful for item in checks)
            return AutomationStep(index=index, action=step.action, status=status, kind="mobile", device=device, checks=checks, faithful=faithful, notes=notes)
        quality = final.get("quality")
        if quality is not None and quality.check is not None:
            notes.append(f"the {quality.check.kind} check runs Hercules' own checks; the export writes it as a comment to replace with your project's tools")
            payload = quality.check.model_dump(mode="json", exclude_defaults=True)
            return AutomationStep(index=index, action=step.action, status=status, kind="quality", quality=payload, payload_source=quality.source, faithful=False, notes=notes)
        actions, checks = list(final.get("auto_actions", ())), list(final.get("auto_checks", ()))
        faithful = all(item.faithful for item in actions) and all(item.faithful for item in checks)
        return AutomationStep(index=index, action=step.action, status=status, actions=actions, checks=checks, faithful=faithful, notes=notes)

    def _page_url(self) -> str:
        return str(getattr(self.deps.page, "url", "") or "")

    def _direct_record(self, intent: StepIntent, before: str, url_after: str) -> AutomationAction:
        """A direct action (navigation, wait, key press) as it ran."""
        data = self.deps.data
        if intent.kind == "navigate":
            template, problem = value_template(intent.template("url"), intent.url, data)
            if template is None:
                return AutomationAction(kind="navigate", faithful=False, note=problem)
            return AutomationAction(kind="navigate", url=relative_url(template, self.deps.base_url))
        if intent.kind == "wait" and intent.expected is not None:
            text, problem = value_template(intent.template("expected"), intent.expected, data)
            return AutomationAction(kind="wait_text", text=text, faithful=text is not None, note=problem)
        if intent.kind == "wait":
            return AutomationAction(kind="wait_time", seconds=float(intent.value or 0), note="the step waits a fixed time")
        return AutomationAction(kind="press", key=intent.value or "", navigated_to=navigated_path(before, url_after), navigated_from=navigated_from(before, url_after))

    def _element_record(self, state: StepState, intent: StepIntent, result: Any, before: str) -> AutomationAction:
        """An action on an element as it ran: the element's unique locators, and the value as a template."""
        # Any other kind is performed as a click (see ``_perform``).
        kind, data = (state["kind"] if state["kind"] in ELEMENT_KINDS else "click"), self.deps.data
        target = state.get("target_spec")
        problems: list[str] = []
        if target is None:
            problems.append("no locator candidates were recorded for this element")
        elif target.locator is None:
            problems.append(f'no locator matched only this element ({target.role} "{target.name}")')
        value: str | None = None
        option: str | None = None
        select_by: Any = None
        if kind in ("fill", "select"):
            # A value the run re-entered the way the field asks for (see the step graph) is recorded as it was entered.
            rewritten = state.get("value") is not None and intent.value is not None and state.get("value") != intent.value
            value, problem = value_template(None if rewritten else intent.template("value"), state.get("value"), data)
            if problem:
                problems.append(problem)
        if kind == "select":
            chosen = getattr(result, "chosen", None) or {}
            select_by = chosen.get("by")
            shown = chosen.get("option")
            option = value if shown is not None and shown == state.get("value") else value_template(shown, shown, data)[0]
            if select_by == "option_role" and not chosen.get("exact", True):
                problems.append("the drop-down's option was found by a partial name")
            if select_by == "text":
                problems.append("the drop-down's option was found by its text; check that the text is unique on the page")
        key = (state.get("value") or intent.value or "") if kind == "press" else None
        after = str(getattr(result, "url_after", "") or self._page_url())
        chosen = (getattr(result, "chosen", None) or {}) if kind == "select" else {}
        return AutomationAction(
            option_role=chosen.get("role"),
            option_exact=bool(chosen.get("exact", True)),
            typed=bool(chosen.get("typed", False)),
            kind=kind,  # type: ignore[arg-type]
            target=target,
            value=value if kind == "fill" else None,
            key=key,
            select_by=select_by,
            option=option,
            navigated_to=navigated_path(before, after),
            navigated_from=navigated_from(before, after),
            faithful=not problems,
            note="; ".join(problems) or None,
        )

    async def _record_target(self, state: StepState, md: int, locator: Any) -> dict[str, Any]:
        """Record the element's unique locators on the live page (when the perceiver can), and log every candidate tried."""
        deps = self.deps
        record = getattr(deps.perceiver, "locators", None)
        if not callable(record) or deps.page is None:
            return {"target_spec": None}
        try:
            spec, tried = await record(deps.page, state["snapshot"], md, locator)
            spec = _without_secrets(spec, deps.data.redact)
            log = state.get("locator_log", ()) + ({"action": len(state.get("auto_actions", ())) + 1, "kind": state.get("kind"), **tried},)
            uri = deps.evidence.save_json(state["index"], "locators.json", {"actions": list(log)})
        except Exception as exc:  # recording for the export never fails a step
            return {"target_spec": TargetSpec(), "auto_notes": state.get("auto_notes", ()) + (f"locators not recorded ({type(exc).__name__})",)}
        return {"target_spec": spec, "locator_log": log, "evidence": state["evidence"] + tuple(item for item in (uri,) if item not in state["evidence"])}

    async def _check_records(self, state: StepState, checks: list[tuple[Check, LedgerEntry]], visible: LedgerEntry | None) -> tuple[AutomationCheck, ...]:
        """The step's checks as the spec records them: each with its verdict and, for a literal text check that held,
        how it is written as an assertion that also held on the page."""
        deps, intent = self.deps, state.get("intent")
        records: list[AutomationCheck] = []
        if visible is not None and intent is not None:
            target, (expected, problem) = state.get("target_spec"), value_template(intent.template("target"), intent.target, deps.data)
            if state.get("locator") is not None and target is not None:
                note = None if target.locator is not None else f'no locator matched only this element ({target.role} "{target.name}")'
                records.append(
                    AutomationCheck(kind="element_visible", expected=expected or "", target=target, verdict=visible.verdict, faithful=note is None and problem is None, note=note or problem)
                )
            else:
                records.append(AutomationCheck(kind="visible_text", expected=expected or "", verdict=visible.verdict, faithful=problem is None, note=problem))
        for check, entry in checks:
            expected, problem = value_template(check.template, check.expected, deps.data)
            base: dict[str, Any] = {"expected": expected or "", "verdict": entry.verdict, "source": entry.source}
            if check.kind in ("semantic", "visual"):
                observed = await self._observed()
                note = f"{entry.actual}; write an assertion for it in code"
                records.append(AutomationCheck(kind=check.kind, observed=observed, faithful=False, note=note, **base))
                continue
            kind = {"text": "text", "not_text": "not_text", "url": "url", "visible": "visible_text"}[check.kind]
            form, frames = None, []
            if check.kind in ("text", "not_text") and entry.verdict == "passed":
                probe = getattr(deps.perceiver, "text_form", None)
                if callable(probe) and deps.page is not None:
                    form, frames = await probe(deps.page, check.expected, present=check.kind == "text")
                    if form is None:
                        problem = problem or "no Playwright text assertion held on the page; check this text by hand"
            target = TargetSpec(frames=frames) if frames else None
            records.append(AutomationCheck(kind=kind, match=check.match, form=form, target=target, faithful=problem is None, note=problem, **base))  # type: ignore[arg-type]
        return tuple(records)

    async def _observed(self) -> str | None:
        """Literal evidence of the page a model judged: its title and address."""
        page = self.deps.page
        if page is None:
            return None
        title = ""
        try:
            title = str(await page.title()) if callable(getattr(page, "title", None)) else ""
        except Exception:  # evidence is best effort
            title = ""
        address = relative_url(self._page_url(), self.deps.base_url)
        text = f'page "{" ".join(title.split())[:120]}" at {address}' if title else f"page at {address}"
        return self.deps.data.redact(text)


def _without_secrets(spec: TargetSpec, redact: Callable[[str], str]) -> TargetSpec:
    """The target without locators whose text holds a secret value (they would be masked in the run's files)."""

    def clean(locator: LocatorSpec) -> bool:
        texts = [locator.value or "", locator.name or "", *locator.selectors]
        return all(redact(text) == text for text in texts)

    kept = [locator for locator in ([spec.locator] if spec.locator is not None else []) + list(spec.fallbacks) if clean(locator)]
    if len(kept) == (1 if spec.locator is not None else 0) + len(spec.fallbacks) and redact(spec.name) == spec.name:
        return spec
    return spec.model_copy(update={"locator": kept[0] if kept else None, "fallbacks": kept[1:], "name": redact(spec.name)})
