"""One reading of a device screen: Appium's page source as a numbered element list a model reads.

Android (UiAutomator2) and iOS (XCUITest) page sources are XML. Each element that shows text, has a
label or can be acted on becomes one :class:`Element` with a number, a role (button, textbox, switch, ...), a
name (its text, label, content description or hint), its bounds and the locators that find it again. The
model sees one line per element::

    [3] button "Network & internet" | clickable, at 0,412 1080x168
    [7] switch "Wi-Fi" | checked

Each element also keeps its :class:`Locator` candidates for exporting the run as code: every locator that finds
it again, with how many elements on the screen it matches and which of them is this one, and, for a control
named after the texts inside it (a list row), the locators of its first text.

The page source is parsed with a tolerant tag scanner, not an XML parser: page sources often hold characters
an XML parser rejects, and nothing in them is ever fetched or expanded.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Literal, NamedTuple

Platform = Literal["android", "ios"]

_TAG = re.compile(r"<([A-Za-z_][\w.:-]*)\s+([^<>]*?)\s*/?>")
_ATTR = re.compile(r'([\w:.-]+)="([^"]*)"')
_ANDROID_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_NAME_CLIP = 80
_CONTAINER_NAMES = 2

_ROLES = {
    "Button": "button",
    "ImageButton": "button",
    "EditText": "textbox",
    "AutoCompleteTextView": "textbox",
    "MultiAutoCompleteTextView": "textbox",
    "CheckBox": "checkbox",
    "RadioButton": "radio",
    "Switch": "switch",
    "ToggleButton": "switch",
    "Spinner": "combobox",
    "SeekBar": "slider",
    "TextView": "text",
    "CheckedTextView": "checkbox",
    "ImageView": "image",
    "XCUIElementTypeButton": "button",
    "XCUIElementTypeTextField": "textbox",
    "XCUIElementTypeSecureTextField": "textbox",
    "XCUIElementTypeSearchField": "textbox",
    "XCUIElementTypeTextView": "textbox",
    "XCUIElementTypeSwitch": "switch",
    "XCUIElementTypeSlider": "slider",
    "XCUIElementTypeStaticText": "text",
    "XCUIElementTypeImage": "image",
    "XCUIElementTypeCell": "listitem",
    "XCUIElementTypeLink": "link",
    "XCUIElementTypeTab": "tab",
    "XCUIElementTypePickerWheel": "combobox",
    "XCUIElementTypeKey": "key",
}
_ACTIONABLE_ROLES = frozenset({"button", "textbox", "checkbox", "radio", "switch", "combobox", "slider", "listitem", "link", "tab", "key"})
_SECURE = frozenset({"XCUIElementTypeSecureTextField"})
# Android resource ids many elements share: no use as a locator.
_SHARED_IDS = frozenset({"android:id/title", "android:id/summary", "android:id/text1", "android:id/text2", "android:id/icon", "android:id/checkbox", "android:id/switch_widget"})


def xpath_literal(value: str) -> str:
    """``value`` as an XPath string literal, whatever quotes it holds."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def java_string(value: str) -> str:
    """``value`` as a Java string literal (for UiSelector expressions)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Locator(NamedTuple):
    """An Appium locator for an element, for the exported test: ``matched`` elements on the screen match it, and the
    element is the ``position``-th of them in document order. ``inner``: it finds a text inside the element."""

    using: str
    value: str
    matched: int = 1
    position: int = 1
    inner: bool = False


# The attribute (and, for iOS labels, the element type) each locator compares: elements with the same key match it.
_Key = tuple[str, ...]


@dataclass(frozen=True)
class Element:
    number: int
    role: str
    name: str
    kind: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool = False
    enabled: bool = True
    checked: bool | None = None
    focused: bool = False
    secure: bool = False
    resource_id: str = ""
    locators: tuple[tuple[str, str], ...] = ()
    # Every locator for exporting, best first (see the module docstring).
    candidates: tuple[Locator, ...] = ()

    @property
    def center(self) -> tuple[int, int] | None:
        if self.bounds is None:
            return None
        x, y, width, height = self.bounds
        return x + width // 2, y + height // 2

    @property
    def actionable(self) -> bool:
        return self.clickable or self.role in _ACTIONABLE_ROLES

    def line(self) -> str:
        parts = [f"[{self.number}] {self.role}"]
        if self.name:
            parts.append('"' + self.name.replace('"', "'") + '"')
        attrs = []
        if self.clickable:
            attrs.append("clickable")
        if self.checked is not None:
            attrs.append("checked" if self.checked else "not checked")
        if self.focused:
            attrs.append("focused")
        if not self.enabled:
            attrs.append("disabled")
        if self.secure:
            attrs.append("password")
        if self.resource_id and self.resource_id not in _SHARED_IDS:
            attrs.append(f"id={self.resource_id.rsplit('/', 1)[-1]}")
        if self.bounds is not None:
            x, y, width, height = self.bounds
            attrs.append(f"at {x},{y} {width}x{height}")
        return " ".join(parts) + (" | " + ", ".join(attrs) if attrs else "")


@dataclass(frozen=True)
class Screen:
    """The elements of one page source, in document order (numbers start at 1)."""

    platform: Platform
    elements: tuple[Element, ...] = ()
    source: str = field(default="", repr=False)
    # The app in the foreground: its Android package or iOS application name ("" when the source does not say).
    app: str = ""
    # The screen's width and height in pixels, when the page source says.
    size: tuple[int, int] | None = None

    def element(self, number: int | None) -> Element | None:
        if number is None:
            return None
        return next((element for element in self.elements if element.number == number), None)

    def text(self) -> str:
        """The element list as the model reads it."""
        if not self.elements:
            return "(the screen shows no elements with text or a label)"
        return "\n".join(element.line() for element in self.elements)

    def words(self) -> str:
        """Every name on the screen, one per line (for literal text checks)."""
        return "\n".join(element.name for element in self.elements if element.name)

    def shows(self, text: str) -> bool:
        """True when an element's name contains ``text`` (case and spacing ignored)."""
        wanted = " ".join(text.split()).casefold()
        return bool(wanted) and any(wanted in " ".join(element.name.split()).casefold() for element in self.elements)

    def fingerprint(self) -> str:
        return "\n".join(f"{element.role}|{element.name}|{element.checked}|{element.focused}" for element in self.elements)


def parse(source: str, platform: Platform) -> Screen:
    """The screen's elements from an Appium page source."""
    ios = platform == "ios" or "XCUIElementType" in source
    nodes = [(match.group(1), {key: html.unescape(value) for key, value in _ATTR.findall(match.group(2))}) for match in _TAG.finditer(source or "")]
    totals: Counter[_Key] = Counter(key for tag, attrs in nodes for key in _keys(tag, attrs, ios).values())
    seen: Counter[_Key] = Counter()
    elements: list[Element] = []
    app = ""
    size: tuple[int, int] | None = None
    for tag, attrs in nodes:
        if not app:
            app = attrs.get("package", "") if not ios else (attrs.get("name", "") if (attrs.get("type") or tag) == "XCUIElementTypeApplication" else "")
        if size is None:
            size = _size(tag, attrs, ios)
        keys = _keys(tag, attrs, ios)
        seen.update(keys.values())
        matches = {name: (totals[key], seen[key]) for name, key in keys.items()}
        element = _ios(tag, attrs, len(elements) + 1, matches) if ios else _android(tag, attrs, len(elements) + 1, matches)
        if element is not None:
            elements.append(element)
    return Screen(platform="ios" if ios else "android", elements=_name_containers(elements), source=source or "", app=app, size=size)


def _keys(tag: str, attrs: dict[str, str], ios: bool) -> dict[str, _Key]:
    """What a locator built from each attribute of this node compares (the same key on two nodes: both match)."""
    if ios:
        kind = attrs.get("type") or tag
        keys: dict[str, _Key] = {"name": ("name", attrs["name"])} if attrs.get("name") else {}
        if attrs.get("label"):
            keys["label"] = ("label", kind, attrs["label"])
        return keys
    names = {"content-desc": "content-desc", "resource-id": "resource-id", "text": "text", "hint": "hint"}
    return {name: (attribute, attrs[attribute]) for name, attribute in names.items() if attrs.get(attribute)}


def _size(tag: str, attrs: dict[str, str], ios: bool) -> tuple[int, int] | None:
    """The screen size from the page source's root (Android ``<hierarchy width height>``, the iOS application)."""
    try:
        if not ios and tag == "hierarchy":
            return int(attrs["width"]), int(attrs["height"])
        if ios and (attrs.get("type") or tag) == "XCUIElementTypeApplication":
            return int(attrs["width"]), int(attrs["height"])
    except (KeyError, ValueError):
        return None
    return None


def _inside(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    x, y, width, height = inner
    cx, cy = x + width / 2, y + height / 2
    ox, oy, ow, oh = outer
    return ox <= cx <= ox + ow and oy <= cy <= oy + oh and width * height < ow * oh


def _name_containers(elements: list[Element]) -> tuple[Element, ...]:
    """Name each unnamed control after the texts drawn inside it (a list row is a clickable container whose
    texts are its children): ``button "Internet · T-Mobile"``. Its children follow it in document order."""
    named: list[Element] = []
    for position, element in enumerate(elements):
        if element.name or element.bounds is None or not element.actionable:
            named.append(element)
            continue
        texts: list[str] = []
        first: Element | None = None  # the first named element inside: its locators find the container too
        for other in elements[position + 1 :]:
            if other.bounds is None or not _inside(other.bounds, element.bounds):
                continue
            if other.name and first is None:
                first = other
            if other.name and other.name not in texts:
                texts.append(other.name)
            if len(texts) == _CONTAINER_NAMES:
                break
        inner = tuple(item._replace(inner=True) for item in first.candidates if not item.inner) if first is not None else ()
        named.append(replace(element, name=_clip(" · ".join(texts)), candidates=element.candidates + inner) if texts else element)
    # A control still unnamed (a switch in a settings row) takes the name of the smallest named element around it.
    for position, element in enumerate(named):
        if element.name or element.bounds is None or not element.actionable:
            continue
        around = [other for other in named[:position] if other.name and other.bounds is not None and _inside(element.bounds, other.bounds)]
        if around:
            smallest = min(around, key=lambda other: other.bounds[2] * other.bounds[3])  # type: ignore[index]
            named[position] = replace(element, name=smallest.name)
    return tuple(named)


def _flag(attrs: dict[str, str], name: str, default: bool = False) -> bool:
    value = attrs.get(name)
    return default if value is None else value.strip().lower() == "true"


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _NAME_CLIP else flat[: _NAME_CLIP - 1] + "…"


def _android(tag: str, attrs: dict[str, str], number: int, matches: dict[str, tuple[int, int]] | None = None) -> Element | None:
    if "bounds" not in attrs and "class" not in attrs:
        return None  # the <hierarchy> root
    if not _flag(attrs, "displayed", True):
        return None
    text, desc, hint = (attrs.get(key, "").strip() for key in ("text", "content-desc", "hint"))
    clickable = _flag(attrs, "clickable") or _flag(attrs, "long-clickable")
    kind = attrs.get("class") or tag
    short = kind.rsplit(".", 1)[-1]
    role = _ROLES.get(short, "button" if clickable else "group")
    if role == "group" and (text or desc):
        role = "text"
    if not (text or desc or hint or clickable or role in _ACTIONABLE_ROLES):
        return None
    found = _ANDROID_BOUNDS.match(attrs.get("bounds", ""))
    bounds = None
    if found:
        x1, y1, x2, y2 = (int(part) for part in found.groups())
        bounds = (x1, y1, max(x2 - x1, 0), max(y2 - y1, 0))
    checkable = _flag(attrs, "checkable")
    secure = _flag(attrs, "password")
    resource_id = attrs.get("resource-id", "")
    locators: list[tuple[str, str]] = []
    if desc:
        locators.append(("accessibility id", desc))
    if resource_id and resource_id not in _SHARED_IDS:
        locators.append(("id", resource_id))
    if text and not secure:
        locators.append(("xpath", f"//*[@text={xpath_literal(text)}]"))
    elif hint:
        locators.append(("xpath", f"//*[@hint={xpath_literal(hint)}]"))
    matches = matches or {}
    raw = {key: attrs.get(key, "") for key in ("content-desc", "resource-id", "text", "hint")}
    candidates: list[Locator] = []
    if raw["content-desc"]:
        candidates.append(Locator("accessibility id", raw["content-desc"], *matches.get("content-desc", (1, 1))))
    if raw["resource-id"] and raw["resource-id"] not in _SHARED_IDS:
        candidates.append(Locator("id", raw["resource-id"], *matches.get("resource-id", (1, 1))))
    if raw["text"] and not secure:
        shown = matches.get("text", (1, 1))
        candidates.append(Locator("-android uiautomator", f"new UiSelector().text({java_string(raw['text'])})", *shown))
        candidates.append(Locator("xpath", f"//*[@text={xpath_literal(raw['text'])}]", *shown))
    if raw["hint"]:
        candidates.append(Locator("xpath", f"//*[@hint={xpath_literal(raw['hint'])}]", *matches.get("hint", (1, 1))))
    return Element(
        number=number,
        role=role,
        name=_clip("•" * len(text) if secure and text else (text or desc or hint)),
        kind=short,
        bounds=bounds,
        clickable=clickable,
        enabled=_flag(attrs, "enabled", True),
        checked=_flag(attrs, "checked") if checkable else None,
        focused=_flag(attrs, "focused"),
        secure=secure,
        resource_id=resource_id,
        locators=tuple(locators),
        candidates=tuple(candidates),
    )


def _ios(tag: str, attrs: dict[str, str], number: int, matches: dict[str, tuple[int, int]] | None = None) -> Element | None:
    kind = attrs.get("type") or tag
    if not kind.startswith("XCUIElementType") or kind in ("XCUIElementTypeApplication", "XCUIElementTypeWindow"):
        return None
    if not _flag(attrs, "visible", True):
        return None
    label, name, value = (attrs.get(key, "").strip() for key in ("label", "name", "value"))
    role = _ROLES.get(kind, "group")
    accessible = _flag(attrs, "accessible")
    if not (label or name or role in _ACTIONABLE_ROLES or accessible):
        return None
    if role == "group" and not (label or (accessible and name)):
        return None
    secure = kind in _SECURE
    try:
        bounds: tuple[int, int, int, int] | None = (int(attrs["x"]), int(attrs["y"]), int(attrs["width"]), int(attrs["height"]))
    except (KeyError, ValueError):
        bounds = None
    checked = None
    if role == "switch" and value in ("0", "1"):
        checked = value == "1"
    locators: list[tuple[str, str]] = []
    if name:
        locators.append(("accessibility id", name))
    if label:
        locators.append(("xpath", f"//{kind}[@label={xpath_literal(label)}]"))
    matches = matches or {}
    candidates: list[Locator] = []
    if attrs.get("name"):
        candidates.append(Locator("accessibility id", attrs["name"], *matches.get("name", (1, 1))))
    if attrs.get("label"):
        candidates.append(Locator("xpath", f"//{kind}[@label={xpath_literal(attrs['label'])}]", *matches.get("label", (1, 1))))
    shown = label or name or ("" if secure or checked is not None else value)
    if name == "BackButton" and label:
        # A navigation bar's back button carries the previous screen's title, which would read as a link to it.
        shown = f"Back ({label})"
    return Element(
        number=number,
        role=role,
        name=_clip(shown),
        kind=kind.removeprefix("XCUIElementType"),
        bounds=bounds,
        clickable=role in ("button", "link", "listitem", "tab", "key"),
        enabled=_flag(attrs, "enabled", True),
        checked=checked,
        focused=_flag(attrs, "focused"),
        secure=secure,
        locators=tuple(locators),
        candidates=tuple(candidates),
    )
