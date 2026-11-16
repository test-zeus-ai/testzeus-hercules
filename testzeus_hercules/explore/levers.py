"""The lever library: the legal moves on the page an explorer is looking at.

Code offers moves; a model chooses among them (``explore.driver``). Every value typed comes from a fixed, inert corpus:
never a working script, never a real person's data. Moves the step runner can express are plain-language step text;
the rest (back, reload, a double submit, an offline moment) are *direct* moves the explorer does with Playwright.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from testzeus_hercules.explore.models import Move

__all__ = ["CORPUS", "FIELD_ROLES", "candidate_moves", "PERSONAS"]

FIELD_ROLES = frozenset({"textbox", "searchbox", "spinbutton", "combobox"})
_CLICKABLE = frozenset({"button", "link", "menuitem", "tab", "checkbox", "radio", "switch"})

# Inert inputs, by variant name. Long values are made at use (a 300- and a 2000-character text).
CORPUS: dict[str, str] = {
    "empty": "",
    "spaces": "   ",
    "long": "a" * 300,
    "very_long": "lorem " * 200,
    "just_over_40": "x" * 41,
    "zero": "0",
    "negative": "-3",
    "huge_number": "999999999",
    "decimal": "1.5",
    "unicode": "Zoë Ångström 😀",
    "quotes": 'O\'Brien "the second"',
    "markup": "<b>bold</b> & <i>italic</i>",
    "padded": "  padded value  ",
    "impossible_date": "31/02/2026",
}
_NUMERIC_FIRST = ("negative", "zero", "huge_number", "decimal", "empty")
_TEXT_FIRST = ("just_over_40", "unicode", "empty", "long", "quotes", "markup", "spaces", "padded", "very_long", "impossible_date")
PERSONAS: dict[str, dict[str, float]] = {
    "impatient": {"order": 3.0, "time": 2.0},
    "careful": {"inputs": 1.5, "people": 2.0},
    "confused": {"order": 2.5},
    "screen-reader": {"people": 3.0},
}
_STATE_CHANGING = ("save", "submit", "place", "add", "remove", "delete", "apply", "send", "create", "update", "confirm", "pay", "order", "sign", "post")


@dataclass(frozen=True)
class PageElement:
    """What the lever library needs of a perceived element."""

    role: str
    name: str
    section: str = ""
    value: str | None = None
    disabled: bool = False
    numeric: bool = False


def elements_of(snapshot: Any) -> list[PageElement]:
    """The snapshot's offered elements, as plain data (a real Snapshot or a test fake)."""
    found = getattr(snapshot, "offered", None)
    items: Iterable[Any] = found if found is not None else getattr(snapshot, "elements", ())
    result = []
    for element in items:
        name = str(getattr(element, "name", "") or "").strip()
        role = str(getattr(element, "role", "") or "")
        if not name or getattr(element, "disabled", False):
            continue
        attrs = getattr(element, "attrs", {}) or {}
        numeric = role == "spinbutton" or str(attrs.get("type", "")).lower() == "number" or "quantity" in name.lower() or "qty" in name.lower()
        result.append(PageElement(role=role, name=name[:120], section=str(getattr(element, "section", "") or ""), value=getattr(element, "value", None), numeric=numeric))
    return result


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def writes(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in _STATE_CHANGING)


def _submit_for(field: PageElement, elements: Sequence[PageElement]) -> PageElement | None:
    """The button that most likely submits ``field``: the first button after it in its section (else the first button after it)."""
    after = elements[elements.index(field) + 1 :] if field in elements else list(elements)
    buttons = [element for element in after if element.role == "button"]
    same = [button for button in buttons if button.section == field.section]
    found = same or buttons
    return found[0] if found else None


def candidate_moves(snapshot: Any, *, levers: Sequence[str], tried: set[tuple[str, str, str]] | None = None, persona: str | None = None, limit: int = 40, area_hint: str = "") -> list[Move]:
    """The moves worth offering on this page, untried first. ``tried`` holds ``(element, lever, variant)``."""
    elements = elements_of(snapshot)
    seen = tried or set()
    moves: list[Move] = []
    fields = [element for element in elements if element.role in FIELD_ROLES]
    clickables = [element for element in elements if element.role in _CLICKABLE]
    if "inputs" in levers:
        for field in fields:
            submit = _submit_for(field, elements)
            order = _NUMERIC_FIRST if field.numeric else _TEXT_FIRST
            for variant in order:
                value = CORPUS[variant]
                then = f" and click the {_quoted(submit.name)} button" if submit is not None else ""
                text = f"Fill {_quoted(value)} in {_quoted(field.name)}{then}" if value else f"Clear {_quoted(field.name)}{then}"
                moves.append(
                    Move(
                        lever="inputs",
                        variant=variant,
                        text=text,
                        element=f"{field.role} {field.name}",
                        value=value,
                        clicks=submit.name if submit is not None else None,
                        writes=submit is not None and writes(submit.name),
                    )
                )
    if "order" in levers:
        for element in clickables:
            if element.role == "button" and writes(element.name):
                moves.append(
                    Move(
                        lever="order",
                        variant="double_submit",
                        text=f"Click the {_quoted(element.name)} button twice quickly",
                        direct="resubmit",
                        element=f"button {element.name}",
                        clicks=element.name,
                        writes=True,
                    )
                )
        moves.append(Move(lever="order", variant="reload", text="Reload the page", direct="reload"))
        moves.append(Move(lever="order", variant="back", text="Go back", direct="back"))
    if "time" in levers:
        moves.append(Move(lever="time", variant="session_expired", text="Lose the session (clear cookies) and reload", direct="clear_cookies"))
        moves.append(Move(lever="time", variant="wait", text="Wait 5 seconds", direct="wait", value="5"))
    if "conditions" in levers:
        moves.append(Move(lever="conditions", variant="narrow", text="Make the window 360 pixels wide", direct="viewport", value="360x740"))
        for element in clickables[:6]:
            if element.role == "button":
                moves.append(
                    Move(
                        lever="conditions",
                        variant="offline",
                        text=f"Go offline, click the {_quoted(element.name)} button, then come back online",
                        direct="offline_once",
                        element=f"button {element.name}",
                        clicks=element.name,
                        writes=writes(element.name),
                    )
                )
    if "people" in levers or persona:
        for field in fields[:4]:
            moves.append(Move(lever="people", variant="keyboard_submit", text=f'Type "test" in {_quoted(field.name)} and press "Enter"', element=f"{field.role} {field.name}", value="test"))
    # Plain navigation keeps coverage moving; it is offered under every lever.
    for element in clickables:
        if element.role == "link":
            moves.append(Move(lever=levers[0], variant="visit", text=f"Click the {_quoted(element.name)} link", element=f"link {element.name}", clicks=element.name))  # type: ignore[arg-type]
        elif element.role == "button" and not writes(element.name):
            moves.append(Move(lever=levers[0], variant="press", text=f"Click the {_quoted(element.name)} button", element=f"button {element.name}", clicks=element.name))  # type: ignore[arg-type]
    weights = PERSONAS.get(persona or "", {})
    fresh = [move for move in moves if (move.element or "", move.lever, move.variant) not in seen]
    stale = [move for move in moves if (move.element or "", move.lever, move.variant) in seen]
    fresh.sort(key=lambda move: (-weights.get(move.lever, 1.0), move.variant == "visit"))
    unique: list[Move] = []
    texts: set[str] = set()
    for move in fresh + stale:
        if move.text not in texts:
            texts.add(move.text)
            unique.append(move)
    return unique[:limit]
