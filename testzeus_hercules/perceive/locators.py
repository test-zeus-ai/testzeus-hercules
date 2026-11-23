"""Locator candidates for an element the engine acts on, proven on the live page (for exporting a run as code).

When the engine acts on an element (or checks that one is visible), :func:`record_target` lists the
ways a test could find that element again, best first: a test id attribute, the role with its
accessible name, a label, a placeholder, the exact text, the alt text or title, then CSS (an id that
does not look generated, the ``name`` attribute, the element's path). Every candidate is evaluated in
the element's frame on the live page; only those that match exactly one element, and that element is
the one acted on, are kept. The iframes on the way are handled the same way. Nothing here decides
which element to use: the engine already acted on it; this only records how code can find it again.

:func:`text_form` records, for a literal text check that held, which way of writing it as a Playwright
assertion also holds on the live page. Both only read the page.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from testzeus_hercules.cases.automation import FrameSpec, LocatorSpec, TargetSpec, TextForm
from testzeus_hercules.perceive.snapshot import walker_source

logger = logging.getLogger(__name__)

TEST_ID_ATTRIBUTES = ("data-testid", "data-test-id", "data-test", "data-qa", "data-cy")
RECORD_BUDGET_S = 4.0
MAX_FALLBACKS = 3
_MAX_TEXT = 80
_COUNT_TIMEOUT_MS = 1_000
# Roles a getByRole query cannot name usefully (no ARIA role, or the walker's own fallbacks).
_NO_ROLE = frozenset({"", "generic", "label", "none", "presentation"})
_FIELD_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"})
# Ids frameworks generate (React's :r1:, long digit runs, hex hashes, framework prefixes) change between builds.
_GENERATED_ID = re.compile(r"\d{3,}|:|^[0-9a-f]{8,}$|^(ember|react|ext-|yui|mui-|rc-|radix-|headlessui-)", re.IGNORECASE)
_SAME_JS = "(el, target) => el === target"
_FACTS_JS = """
(el, testIds) => {
  const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const ids = {};
  for (const name of testIds) {
    const value = el.getAttribute(name);
    if (value) ids[name] = value;
  }
  const labels = el.labels ? Array.from(el.labels).map((label) => clean(label.innerText || label.textContent)).filter(Boolean) : [];
  const own = ["input", "select", "textarea"].includes(el.localName) ? "" : clean(el.innerText);
  return {
    tag: el.localName,
    id: el.id || null,
    name: el.getAttribute("name"),
    placeholder: clean(el.getAttribute("placeholder")) || null,
    aria_label: clean(el.getAttribute("aria-label")) || null,
    alt: clean(el.getAttribute("alt")) || null,
    title: clean(el.getAttribute("title")) || null,
    test_ids: ids,
    labels,
    text: own,
  };
}
"""
_FRAME_FACTS_JS = """
(el) => ({ tag: el.localName, id: el.id || null, name: el.getAttribute("name"), title: el.getAttribute("title") })
"""


def css_string(value: str) -> str:
    """``value`` as a double-quoted CSS string."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ") + '"'


def css_identifier(value: str) -> str:
    """``value`` escaped as a CSS identifier (for ``#id``)."""
    out = []
    for index, char in enumerate(value):
        if char.isalnum() and not (index == 0 and char.isdigit()) or char in "-_" or ord(char) > 127:
            out.append(char)
        else:
            out.append(f"\\{ord(char):x} ")
    return "".join(out)


def build_locator(scope: Any, spec: LocatorSpec) -> Any:
    """The Playwright locator ``spec`` describes, inside ``scope`` (a page, a frame locator or a frame)."""
    if spec.strategy == "test_id":
        attribute = spec.attribute or "data-testid"
        return scope.locator(f"[{attribute}={css_string(spec.value or '')}]")
    if spec.strategy == "role":
        if spec.name is None:
            return scope.get_by_role(spec.role)
        return scope.get_by_role(spec.role, name=spec.name, exact=spec.exact)
    if spec.strategy == "label":
        return scope.get_by_label(spec.value, exact=spec.exact)
    if spec.strategy == "placeholder":
        return scope.get_by_placeholder(spec.value, exact=spec.exact)
    if spec.strategy == "text":
        return scope.get_by_text(spec.value, exact=spec.exact)
    if spec.strategy == "alt_text":
        return scope.get_by_alt_text(spec.value, exact=spec.exact)
    if spec.strategy == "title":
        return scope.get_by_title(spec.value, exact=spec.exact)
    locator = scope.locator(spec.selectors[0])
    for selector in spec.selectors[1:]:
        locator = locator.locator(selector)
    return locator


def element_candidates(facts: dict[str, Any], role: str, name: str, segments: Sequence[str] | None) -> list[LocatorSpec]:
    """Every candidate for an element, best first, from its attributes and its perceived role and name."""
    found: list[LocatorSpec] = []
    for attribute, value in facts.get("test_ids", {}).items():
        found.append(LocatorSpec(strategy="test_id", attribute=attribute, value=value))
    if role not in _NO_ROLE:
        if name:
            found.append(LocatorSpec(strategy="role", role=role, name=name))
        else:
            found.append(LocatorSpec(strategy="role", role=role))
    for label in [*facts.get("labels", []), facts.get("aria_label")]:
        if label and len(label) <= _MAX_TEXT and not any(item.strategy == "label" and item.value == label for item in found):
            found.append(LocatorSpec(strategy="label", value=label))
    if facts.get("placeholder"):
        found.append(LocatorSpec(strategy="placeholder", value=facts["placeholder"]))
    text = facts.get("text") or ""
    if text and len(text) <= _MAX_TEXT and role not in _FIELD_ROLES:
        found.append(LocatorSpec(strategy="text", value=text))
    if facts.get("alt"):
        found.append(LocatorSpec(strategy="alt_text", value=facts["alt"]))
    if facts.get("title"):
        found.append(LocatorSpec(strategy="title", value=facts["title"]))
    found.extend(_css_candidates(facts, segments))
    return found


def _css_candidates(facts: dict[str, Any], segments: Sequence[str] | None) -> list[LocatorSpec]:
    found: list[LocatorSpec] = []
    element_id = facts.get("id")
    if element_id and not _GENERATED_ID.search(element_id):
        found.append(LocatorSpec(strategy="css", selectors=[f"#{css_identifier(element_id)}"]))
    if facts.get("name") and facts.get("tag"):
        found.append(LocatorSpec(strategy="css", selectors=[f"{facts['tag']}[name={css_string(facts['name'])}]"]))
    if segments:
        found.append(LocatorSpec(strategy="css", selectors=list(segments)))
    return found


def frame_candidates(facts: dict[str, Any], segments: Sequence[str]) -> list[LocatorSpec]:
    """Candidates for an iframe element: its name, title or id, then its path."""
    tag = facts.get("tag") or "iframe"
    found = [LocatorSpec(strategy="css", selectors=[f"{tag}[{attribute}={css_string(facts[attribute])}]"]) for attribute in ("name", "title") if facts.get(attribute)]
    found.extend(_css_candidates({"id": facts.get("id")}, segments))
    return found


async def _unique(scope: Any, candidates: Sequence[LocatorSpec], target: Any) -> tuple[list[LocatorSpec], list[dict[str, Any]]]:
    """The candidates that match exactly ``target`` in ``scope``, and a record of every candidate tried."""
    from playwright.async_api import Error as PlaywrightError

    kept: list[LocatorSpec] = []
    tried: list[dict[str, Any]] = []
    for spec in candidates:
        record: dict[str, Any] = spec.model_dump(mode="json", exclude_defaults=True, exclude={"count"})
        try:
            locator = build_locator(scope, spec)
            count = await locator.count()
            same = count == 1 and bool(await locator.evaluate(_SAME_JS, target, timeout=_COUNT_TIMEOUT_MS))
        except PlaywrightError as exc:
            record.update(count=None, kept=False, error=str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__)
            tried.append(record)
            continue
        record.update(count=count, kept=same)
        if count == 1 and not same:
            record["note"] = "matches one element, not the one acted on"
        tried.append(record)
        if same and spec not in kept:
            kept.append(spec.model_copy(update={"count": 1}))
    return kept, tried


async def record_target(
    page: Any, locator: Any, *, frames: Sequence[Sequence[str]] = (), segments: Sequence[str] | None = None, budget_s: float = RECORD_BUDGET_S
) -> tuple[TargetSpec, dict[str, Any]]:
    """The element ``locator`` points at (inside ``frames``, from the top document down) as a :class:`TargetSpec`,
    and a record of every candidate tried (for ``locators.json``). Never raises; bounded by ``budget_s``."""
    try:
        return await asyncio.wait_for(_record(page, locator, frames, segments), timeout=budget_s)
    except Exception as exc:  # recording is best effort: the action itself must never fail because of it
        logger.debug("locator candidates not recorded: %s", exc)
        return TargetSpec(), {"error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}".strip()}


async def _record(page: Any, locator: Any, frames: Sequence[Sequence[str]], segments: Sequence[str] | None) -> tuple[TargetSpec, dict[str, Any]]:
    scope: Any = page
    frame_specs: list[FrameSpec] = []
    tried_frames: list[dict[str, Any]] = []
    for frame_segments in frames:
        element = scope.locator(frame_segments[0])
        for selector in frame_segments[1:]:
            element = element.locator(selector)
        element = element.first
        handle = await element.element_handle(timeout=_COUNT_TIMEOUT_MS)
        facts = await element.evaluate(_FRAME_FACTS_JS)
        kept, tried = await _unique(scope, frame_candidates(facts, frame_segments), handle)
        tried_frames.append({"candidates": tried})
        label = facts.get("name") or facts.get("title") or facts.get("id")
        if not kept:
            raise LookupError("no unique locator for an iframe on the way to the element")
        frame_specs.append(FrameSpec(locator=kept[0], fallbacks=kept[1 : 1 + MAX_FALLBACKS], label=label))
        scope = element.content_frame
    target = await locator.element_handle(timeout=_COUNT_TIMEOUT_MS)
    described = await locator.evaluate(walker_source(), {"describe": 1}, timeout=_COUNT_TIMEOUT_MS)
    facts = await locator.evaluate(_FACTS_JS, list(TEST_ID_ATTRIBUTES), timeout=_COUNT_TIMEOUT_MS)
    role, name = str(described.get("role") or ""), str(described.get("name") or "")
    kept, tried = await _unique(scope, element_candidates(facts, role, name, segments), target)
    spec = TargetSpec(role=role, name=name, frames=frame_specs, locator=kept[0] if kept else None, fallbacks=kept[1 : 1 + MAX_FALLBACKS], checked=len(tried))
    return spec, {"role": role, "name": name, "frames": tried_frames, "candidates": tried}


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


async def frame_chain(frame: Any) -> list[FrameSpec]:
    """How to reach ``frame`` from the top document: each iframe element's unique locator in its parent (LookupError when one has none)."""
    chain: list[Any] = []
    node = frame
    while node.parent_frame is not None:
        chain.insert(0, node)
        node = node.parent_frame
    specs: list[FrameSpec] = []
    for child in chain:
        element = await child.frame_element()
        facts = await element.evaluate(_FRAME_FACTS_JS)
        kept, _ = await _unique(child.parent_frame, frame_candidates(facts, ()), element)
        if not kept:
            raise LookupError("no unique locator for an iframe")
        specs.append(FrameSpec(locator=kept[0], fallbacks=kept[1 : 1 + MAX_FALLBACKS], label=facts.get("name") or facts.get("title") or facts.get("id")))
    return specs


async def text_form(page: Any, text: str, *, present: bool) -> tuple[TextForm | None, list[FrameSpec]]:
    """How a text check that held can be written so it also holds on this page, and the frames it is in (when not in the top
    document); None when no simple form does.

    ``present``: the text is shown (``text``, ``text_first``, ``body``); else it is not (``count_zero``, ``body_not``).
    A shown text found in no simple form in the top document is looked for in the page's frames.
    """
    from playwright.async_api import Error as PlaywrightError

    try:
        form = await _form_in(page, text, present=present)
        if form is not None or not present:
            return form, []
        for frame in page.frames:
            if frame is page.main_frame or frame.is_detached():
                continue
            matches = frame.get_by_text(text)
            if await matches.count() >= 1 and await matches.first.is_visible():
                return ("text" if await matches.count() == 1 else "text_first"), await frame_chain(frame)
        return None, []
    except (PlaywrightError, LookupError) as exc:
        logger.debug("text form not recorded: %s", exc)
        return None, []


async def _form_in(scope: Any, text: str, *, present: bool) -> TextForm | None:
    matches = scope.get_by_text(text)
    count = await matches.count()
    body = _normalise(await scope.locator("body").text_content(timeout=_COUNT_TIMEOUT_MS) or "")
    wanted = _normalise(text)
    if not present:
        return "count_zero" if count == 0 else "body_not" if wanted not in body else None
    if count >= 1 and await matches.first.is_visible():
        return "text" if count == 1 else "text_first"
    return "body" if wanted in body else None
