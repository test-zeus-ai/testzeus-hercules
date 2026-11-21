"""Concrete fix values for findings, each tried on the live page before it is reported.

Code computes every value (colours, sizes, attributes); a language model, when given, only writes
text: an ``aria-label`` or alt text and the rationale. :func:`verify_fix` applies a fix as a scoped
CSS override (a ``data-hq-fix`` attribute plus a style rule in the element's own tree) or as an
attribute change, re-runs the check for that one element, records ``after_fix`` and ``verified``,
and always puts the page back as it was.
"""

from __future__ import annotations

import html
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from testzeus_hercules.quality.checks import MIN_TARGET_PX, contrast_measure, file_stem, focus_changes
from testzeus_hercules.quality.color import RGBA, composite, contrast_ratio, flatten, floor2, nearest_passing
from testzeus_hercules.quality.findings import Finding, FixSuggestion
from testzeus_hercules.quality.frames import locator_for, probe_element

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

    from testzeus_hercules.llm.structured import StructuredLLM

logger = logging.getLogger(__name__)

__all__ = ["RECHECKS", "apply_fix", "aria_label_for", "likely_file", "register_recheck", "suggest_fix", "suggest_fixes", "verify_fix"]

Recheck = Callable[["Page", Finding, "Locator"], Awaitable[tuple[bool, dict[str, Any]]]]
RECHECKS: dict[str, Recheck] = {}
FAMILY: dict[str, str] = {
    "color-contrast": "contrast",
    "color-contrast-enhanced": "contrast",
    "control-name": "name",
    "button-name": "name",
    "link-name": "name",
    "label": "name",
    "input-button-name": "name",
    "select-name": "name",
    "aria-command-name": "name",
    "aria-input-field-name": "name",
    "aria-toggle-field-name": "name",
    "target-size": "target",
    "image-alt": "alt",
    "input-image-alt": "alt",
    "role-img-alt": "alt",
    "svg-img-alt": "alt",
    "focus-not-visible": "focus",
    "keyboard-unreachable": "keyboard",
}
FOCUS_OUTLINE_PX = 2
NON_TEXT_CONTRAST = 3.0
_STATE_SELECTORS = {":focus-visible": (":focus", ":focus-visible")}

ICON_WORDS = {
    "close": "Close", "dismiss": "Dismiss", "times": "Close", "x": "Close", "cancel": "Cancel",
    "search": "Search", "magnifier": "Search", "menu": "Menu", "hamburger": "Menu", "burger": "Menu",
    "delete": "Delete", "trash": "Delete", "bin": "Delete", "remove": "Remove", "edit": "Edit", "pencil": "Edit",
    "settings": "Settings", "gear": "Settings", "cog": "Settings", "account": "Account", "user": "Account", "profile": "Profile",
    "cart": "Cart", "basket": "Cart", "bag": "Bag", "share": "Share", "download": "Download", "upload": "Upload",
    "play": "Play", "pause": "Pause", "stop": "Stop", "next": "Next", "prev": "Previous", "previous": "Previous",
    "back": "Back", "forward": "Forward", "add": "Add", "plus": "Add", "minus": "Decrease", "more": "More options",
    "ellipsis": "More options", "kebab": "More options", "dots": "More options", "filter": "Filter", "sort": "Sort",
    "info": "Information", "help": "Help", "question": "Help", "home": "Home", "bell": "Notifications",
    "notifications": "Notifications", "mail": "Email", "email": "Email", "copy": "Copy", "refresh": "Refresh",
    "reload": "Refresh", "expand": "Expand", "collapse": "Collapse", "logout": "Sign out", "login": "Sign in",
    "heart": "Favourite", "favorite": "Favourite", "favourite": "Favourite", "star": "Favourite", "calendar": "Calendar",
    "print": "Print", "save": "Save", "send": "Send", "attach": "Attach", "paperclip": "Attach", "link": "Link",
    "external": "Open in new tab", "lock": "Lock", "unlock": "Unlock", "eye": "Show", "visibility": "Show",
}  # fmt: skip
_GENERIC_TOKENS = frozenset(
    {
        "btn",
        "button",
        "icon",
        "svg",
        "img",
        "image",
        "container",
        "wrapper",
        "inner",
        "outer",
        "row",
        "col",
        "item",
        "active",
        "primary",
        "secondary",
        "small",
        "large",
        "lg",
        "sm",
        "md",
        "xs",
        "shadow",
        "host",
        "main",
        "content",
    }
)
_WORD = re.compile(r"[A-Za-z]+")


def register_recheck(family: str, rules: Sequence[str], recheck: Recheck) -> None:
    """Let another module (layout, visual) add a re-check for its rules."""
    RECHECKS[family] = recheck
    for rule in rules:
        FAMILY[rule] = family


# --------------------------------------------------------------------------- applying and verifying


def _css_rule(token: str, changes: Mapping[str, str], state: str | None) -> str:
    selectors = _STATE_SELECTORS.get(state or "", (state or "",))
    scoped = ", ".join(f'[data-hq-fix~="{token}"]{suffix}' for suffix in selectors)
    body = " ".join(f"{prop}: {value} !important;" for prop, value in changes.items())
    return f"{scoped} {{ {body} }}"


@asynccontextmanager
async def apply_fix(page: "Page", finding: Finding, fix: FixSuggestion) -> Any:
    """Apply ``fix`` to the live page for the duration of the ``async with`` block, then undo it."""
    selector = fix.target_selector or finding.selector
    target = locator_for(page, finding.frame_path, selector)
    if fix.method == "css_override":
        token = uuid.uuid4().hex[:12]
        await probe_element(target, "mark", token=token, css=_css_rule(token, fix.changes, fix.state))
        try:
            yield
        finally:
            await probe_element(target, "unmark", token=token)
        return
    previous: dict[str, str | None] = {}
    try:
        for name, value in fix.changes.items():
            previous[name] = await probe_element(target, "attr", name=name, value=value)
        yield
    finally:
        for name, old in previous.items():
            await probe_element(target, "attr", name=name, value=old)


async def verify_fix(page: "Page", finding: Finding, fix: FixSuggestion) -> FixSuggestion:
    """Apply the fix, re-run the finding's check on its element, undo; return the fix with the outcome."""
    from playwright.async_api import Error as PlaywrightError

    recheck = RECHECKS.get(FAMILY.get(finding.rule, ""))
    if recheck is None or not fix.changes:
        return fix
    element = locator_for(page, finding.frame_path, finding.selector)
    try:
        async with apply_fix(page, finding, fix):
            passes, after = await recheck(page, finding, element)
    except PlaywrightError as exc:
        logger.info("could not verify fix for %s: %s", finding.id, exc)
        return fix.model_copy(update={"verified": False, "after_fix": {"error": str(exc).splitlines()[0]}})
    return fix.model_copy(update={"verified": passes, "after_fix": after})


def _required(finding: Finding) -> float:
    measured = finding.measured or {}
    if "required" in measured:
        return float(measured["required"])
    expected = str((measured.get("data") or {}).get("expectedContrastRatio", "4.5")).split(":")[0]
    try:
        return float(expected)
    except ValueError:
        return 4.5


async def _recheck_contrast(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    rec = await probe_element(element, "contrast")
    measured = contrast_measure(rec)
    required = _required(finding)
    return floor2(measured["ratio"]) >= required, {"ratio": measured["ratio"], "fg": measured["fg"], "bg": measured["bg"], "required": required}


async def _recheck_name(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    rec = await probe_element(element, "name")
    return bool(rec["name"]), {"name": rec["name"]}


async def _recheck_target(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    rec = await probe_element(element, "rect")
    width, height = round(rec["rect"][2], 2), round(rec["rect"][3], 2)
    return width >= MIN_TARGET_PX - 0.01 and height >= MIN_TARGET_PX - 0.01, {"width": width, "height": height}


async def _recheck_alt(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    rec = await probe_element(element, "image")
    return rec["has_alt"], {"alt": rec["alt"]}


async def _recheck_focus(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    probe = await probe_element(element, "focus_probe")
    changed = focus_changes(probe["unfocused"], probe["focused"])
    after: dict[str, Any] = {"changed": changed}
    focused = probe["focused"]["self"]
    if focused.get("outline-style") not in (None, "none") and focused.get("outline-width") not in (None, "0px"):
        bg = flatten([RGBA.parse(layer) for layer in probe["bg"]])
        ratio = floor2(contrast_ratio(_parse_css_colour(focused.get("outline-color", "")) or RGBA(0, 0, 0), bg))
        after.update({"outline": f"{focused['outline-width']} {focused['outline-style']} {focused.get('outline-color')}", "outline_contrast": ratio})
        return bool(changed) and ratio >= NON_TEXT_CONTRAST, after
    return bool(changed), after


# The focused element of the element's document (through open shadow roots), and putting focus back on it.
_ACTIVE_ELEMENT = "(el) => { let a = el.ownerDocument.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement; return a; }"
_RESTORE_FOCUS = """(el, previous) => {
  const doc = el.ownerDocument;
  let active = doc.activeElement;
  while (active && active.shadowRoot && active.shadowRoot.activeElement) active = active.shadowRoot.activeElement;
  if (previous && previous !== doc.body && previous.isConnected && previous.focus) previous.focus({ preventScroll: true });
  else if (active && active.blur) active.blur();
}"""


async def _recheck_keyboard(page: "Page", finding: Finding, element: "Locator") -> tuple[bool, dict[str, Any]]:
    """Whether one Tab from the element before it reaches the element; focus is put back where it was afterwards."""
    previous = await element.evaluate_handle(_ACTIVE_ELEMENT)
    try:
        await probe_element(element, "focus_before")
        await page.keyboard.press("Tab")
        reached = bool(await probe_element(element, "is_active"))
    finally:
        try:
            await element.evaluate(_RESTORE_FOCUS, previous)
        finally:
            await previous.dispose()
    return reached, {"reached_with_tab": reached}


RECHECKS.update({"contrast": _recheck_contrast, "name": _recheck_name, "target": _recheck_target, "alt": _recheck_alt, "focus": _recheck_focus, "keyboard": _recheck_keyboard})


def _parse_css_colour(value: str) -> RGBA | None:
    numbers = re.findall(r"[\d.]+", value)
    if value.startswith("rgb") and len(numbers) >= 3:
        return RGBA.parse([float(n) for n in numbers[:4]])
    return None


# --------------------------------------------------------------------------- likely file


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def likely_file(hints: Sequence[str], files_changed: Sequence[str], file_contents: Mapping[str, str] | None = None) -> str | None:
    """The changed file most likely to hold the element: its id, classes, test ids or component names
    appearing in a file's path (strongest) or contents. ``None`` when nothing matches."""
    tokens = [t.lstrip("#.") for t in hints]
    tokens = [t for t in tokens if len(_normal(t)) >= 3 and _normal(t) not in _GENERIC_TOKENS]
    best: tuple[int, str] | None = None
    for path in files_changed:
        flat = _normal(path)
        content = (file_contents or {}).get(path, "")
        score = 0
        for token in tokens:
            if _normal(token) in flat:
                score += 3
            if content and re.search(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])", content):
                score += 2
        if score and (best is None or score > best[0]):
            best = (score, path)
    return best[1] if best else None


# --------------------------------------------------------------------------- suggestions


class _Label(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    rationale: str = ""


class _AltText(BaseModel):
    alt: str = Field(max_length=200)
    decorative: bool = False
    rationale: str = ""


def _words(text: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w.lower() for w in _WORD.findall(spaced)]


def aria_label_for(measured: Mapping[str, Any], hints: Sequence[str]) -> str | None:
    """Deterministic label: a tied caption, an icon word from ids and classes, else a humanised token."""
    if measured.get("caption"):
        return str(measured["caption"])
    sources = [*measured.get("icon_hints", []), *(h.lstrip("#.") for h in hints)]
    for source in sources:
        for word in _words(str(source)):
            if word in ICON_WORDS:
                return ICON_WORDS[word]
    for source in sources:
        words = [w for w in _words(str(source)) if w not in _GENERIC_TOKENS and len(w) > 1]
        if words:
            return " ".join(words).capitalize()
    return None


def _css_hint(finding: Finding) -> str:
    for hint in finding.hints:
        if hint.startswith((".", "#")):
            return hint
    return finding.selector


def _fix(finding: Finding, **values: Any) -> FixSuggestion:
    return FixSuggestion(finding_ref=finding.id, **values)


async def _suggest_contrast(page: "Page", finding: Finding, element: "Locator") -> FixSuggestion | None:
    rec = await probe_element(element, "contrast")
    measured = contrast_measure(rec)
    bg = RGBA.parse(measured["bg"])
    fg = composite(RGBA.parse(rec["fg"]), bg)
    required = _required(finding)
    found = nearest_passing(RGBA(fg.r, fg.g, fg.b), bg, required)
    if found is None:
        return None
    rationale = f"Nearest {found.side} with the same hue that reaches {required}:1 (ΔE OK {found.delta_e}); was {measured['ratio']}:1, becomes {found.ratio}:1."
    return _fix(
        finding,
        property_or_attr=found.side,
        current=found.current,
        suggested=found.suggested,
        changes={found.side: found.suggested},
        method="css_override",
        snippet=f"{_css_hint(finding)} {{ {found.side}: {found.suggested}; }}",
        rationale=rationale,
        confidence=0.9,
    )


async def _suggest_name(page: "Page", finding: Finding, element: "Locator", llm: "StructuredLLM | None") -> FixSuggestion | None:
    rec = await probe_element(element, "name")
    measured = {**(finding.measured or {}), "caption": rec["caption"], "icon_hints": rec["icon_hints"], "near": rec["near"]}
    label = aria_label_for(measured, finding.hints)
    rationale = "Caption shown next to the field." if rec["caption"] else "Derived from the element's icon, id and class names."
    confidence = 0.8 if rec["caption"] else 0.5
    if llm is not None and not rec["caption"]:
        try:
            context = f"Element: {rec['desc']}\nRole: {rec['role']}\nIcon/id/class hints: {', '.join(rec['icon_hints'])}\nNearby heading or text: {rec['near']}"
            reply = await llm.complete(system="You write short accessible names (aria-label) for unlabelled web controls: 1-4 words, the action the control performs.", user=context, schema=_Label)
            label, rationale, confidence = reply.label.strip(), reply.rationale or "Written by the language model from the element's context.", 0.7
        except Exception as exc:  # noqa: BLE001 - the deterministic label stays usable when the model fails
            logger.info("label from model failed, keeping the deterministic one: %s", exc)
    if not label:
        return None
    return _fix(
        finding,
        property_or_attr="aria-label",
        current=None,
        suggested=label,
        changes={"aria-label": label},
        method="attribute",
        snippet=f'aria-label="{html.escape(label)}"',
        rationale=rationale,
        confidence=confidence,
    )


async def _suggest_target(page: "Page", finding: Finding, element: "Locator") -> FixSuggestion | None:
    styles = (await probe_element(element, "styles"))["styles"]
    width, height = (await probe_element(element, "rect"))["rect"][2:4]
    px = {
        key: float(str(styles.get(key, "0")).rstrip("px") or 0)
        for key in ("padding-left", "padding-right", "padding-top", "padding-bottom", "border-left-width", "border-right-width", "border-top-width", "border-bottom-width")
    }
    if styles.get("box-sizing") == "border-box":
        min_w = min_h = MIN_TARGET_PX
    else:
        min_w = max(0.0, MIN_TARGET_PX - px["padding-left"] - px["padding-right"] - px["border-left-width"] - px["border-right-width"])
        min_h = max(0.0, MIN_TARGET_PX - px["padding-top"] - px["padding-bottom"] - px["border-top-width"] - px["border-bottom-width"])
    changes = {"min-width": f"{min_w:g}px", "min-height": f"{min_h:g}px"}
    if styles.get("display") == "inline":
        changes["display"] = "inline-block"
    declarations = "; ".join(f"{k}: {v}" for k, v in changes.items())
    return _fix(
        finding,
        property_or_attr=", ".join(changes),
        current=f"{width:g}px × {height:g}px",
        suggested=declarations,
        changes=changes,
        method="css_override",
        snippet=f"{_css_hint(finding)} {{ {declarations}; }}",
        rationale=f"Grows the target to at least {MIN_TARGET_PX:g}×{MIN_TARGET_PX:g} CSS px including padding and border.",
        confidence=0.85,
    )


async def _suggest_alt(page: "Page", finding: Finding, element: "Locator", llm: "StructuredLLM | None") -> FixSuggestion | None:
    rec = await probe_element(element, "image")
    if llm is not None and llm.accepts_images:
        try:
            crop = await element.screenshot(type="png")
            reply = await llm.complete(
                system="You write alt text for images on web pages: what the image shows that matters in context, under 125 characters, no 'image of'. Mark purely decorative images.",
                user=f"Image element: {rec['desc']}\nFile: {file_stem(rec['src'])}",
                schema=_AltText,
                images=[crop],
            )
            alt = "" if reply.decorative else reply.alt.strip()
            return _fix(
                finding,
                property_or_attr="alt",
                current=None,
                suggested=alt,
                changes={"alt": alt},
                method="text",
                snippet=f'alt="{html.escape(alt)}"',
                rationale=reply.rationale or "Written by the vision model from the image crop.",
                confidence=0.7,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the placeholder below
            logger.info("alt text from model failed: %s", exc)
    stem = file_stem(rec["src"])
    placeholder = " ".join(_words(stem)).capitalize() if stem else "Describe this image"
    return _fix(
        finding,
        property_or_attr="alt",
        current=None,
        suggested=placeholder,
        method="text",
        snippet=f'alt="{html.escape(placeholder)}"',
        rationale="Placeholder from the file name: no vision model was available to describe the image. Replace it with what the image shows.",
        confidence=0.2,
    )


async def _suggest_focus(page: "Page", finding: Finding, element: "Locator") -> FixSuggestion | None:
    probe = await probe_element(element, "focus_probe")
    bg = flatten([RGBA.parse(layer) for layer in probe["bg"]])
    own = _parse_css_colour(probe["unfocused"]["self"].get("color", ""))
    candidates = [c for c in (own, RGBA(0, 0, 0), RGBA(255, 255, 255)) if c is not None]
    colour = next((c for c in candidates if contrast_ratio(c, bg) >= NON_TEXT_CONTRAST), max(candidates, key=lambda c: contrast_ratio(c, bg)))
    changes = {"outline": f"{FOCUS_OUTLINE_PX}px solid {colour.hex}", "outline-offset": f"{FOCUS_OUTLINE_PX}px"}
    return _fix(
        finding,
        property_or_attr="outline",
        current=probe["focused"]["self"].get("outline-style", "none"),
        suggested=f"{changes['outline']}; outline-offset: {changes['outline-offset']}",
        changes=changes,
        state=":focus-visible",
        method="css_override",
        snippet=f"{_css_hint(finding)}:focus-visible {{ outline: {changes['outline']}; outline-offset: {changes['outline-offset']}; }}",
        rationale=f"A {FOCUS_OUTLINE_PX}px outline in {colour.hex} has {floor2(contrast_ratio(colour, bg))}:1 against the surrounding {bg.hex} (at least {NON_TEXT_CONTRAST:g}:1 needed).",
        confidence=0.85,
    )


def _suggest_keyboard(finding: Finding) -> FixSuggestion:
    generic = bool((finding.measured or {}).get("generic"))
    changes = {"tabindex": "0", **({"role": "button"} if generic else {})}
    attrs = " ".join(f'{k}="{v}"' for k, v in changes.items())
    return _fix(
        finding,
        property_or_attr=", ".join(changes),
        current=str((finding.measured or {}).get("tab_index")),
        suggested=attrs,
        changes=changes,
        method="attribute",
        snippet=attrs,
        rationale="Puts the element in the Tab order" + (" and exposes it as a button; it also needs Enter and Space key handlers (or use a <button>)." if generic else "."),
        confidence=0.6 if generic else 0.75,
    )


async def suggest_fix(
    page: "Page",
    finding: Finding,
    *,
    llm: "StructuredLLM | None" = None,
    files_changed: Sequence[str] = (),
    file_contents: Mapping[str, str] | None = None,
    verify: bool = True,
) -> FixSuggestion | None:
    """A concrete, verified (when possible) fix for one accessibility finding, or None when there is none."""
    from playwright.async_api import Error as PlaywrightError

    family = FAMILY.get(finding.rule)
    element = locator_for(page, finding.frame_path, finding.selector)
    try:
        if family == "contrast":
            fix = await _suggest_contrast(page, finding, element)
        elif family == "name":
            fix = await _suggest_name(page, finding, element, llm)
        elif family == "target":
            fix = await _suggest_target(page, finding, element)
        elif family == "alt":
            fix = await _suggest_alt(page, finding, element, llm)
        elif family == "focus":
            fix = await _suggest_focus(page, finding, element)
        elif family == "keyboard":
            fix = _suggest_keyboard(finding)
        else:
            return None
    except PlaywrightError as exc:
        logger.info("no fix for %s: %s", finding.id, exc)
        return None
    if fix is None:
        return None
    fix = fix.model_copy(update={"likely_file": likely_file(finding.hints, files_changed, file_contents)})
    return await verify_fix(page, finding, fix) if verify else fix


async def suggest_fixes(page: "Page", findings: Sequence[Finding], **kwargs: Any) -> list[FixSuggestion]:
    """Fixes for every finding that has one. Findings of the same family on the same element (axe and
    the own checks often agree) share one computed and verified fix, referenced from each finding; contrast
    findings share one only when they require the same ratio (a 4.5:1 fix does not meet a 7:1 finding)."""
    fixes: list[FixSuggestion] = []
    done: dict[tuple[str, tuple[str, ...], str, float | None], FixSuggestion | None] = {}
    for finding in findings:
        family = FAMILY.get(finding.rule, finding.rule)
        key = (family, tuple(finding.frame_path), finding.selector, _required(finding) if family == "contrast" else None)
        if key in done:
            shared = done[key]
            if shared is not None:
                fixes.append(shared.model_copy(update={"finding_ref": finding.id}))
            continue
        fix = await suggest_fix(page, finding, **kwargs)
        done[key] = fix
        if fix is not None:
            fixes.append(fix)
    return fixes
