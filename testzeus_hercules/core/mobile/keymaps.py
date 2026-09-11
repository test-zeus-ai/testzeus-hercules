"""Pure key-name to keycode/character mappings for mobile device automation.

Three lookup families are provided:

- ``ANDROID_KEYCODES``: keyboard keys sent with ``press_keycode`` on Android.
- ``ANDROID_HARDWARE_KEYCODES``: hardware buttons (volume, power, home...).
- ``IOS_UNICODE_KEYS``: unicode key characters used for keyboard keys on iOS.

``expand_key_sequence`` turns a human sequence such as ``"Control+A,Enter"``
into per-segment ``(modifier_keycodes, main_keycode)`` tuples in press order.
All lookups are case-insensitive; unknown names raise ``ValueError``.
"""

from typing import List, Tuple

ANDROID_KEYCODES: dict = {
    "enter": 66,
    "return": 66,
    "tab": 61,
    "space": 62,
    "backspace": 67,
    "delete": 112,
    "escape": 111,
    "esc": 111,
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
}

ANDROID_HARDWARE_KEYCODES: dict = {
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "camera": 27,
    "call": 5,
    "end_call": 6,
    "menu": 82,
    "back": 4,
    "home": 3,
    "app_switch": 187,
}

# Modifier names -> Android modifier keycodes (held down around the main key).
ANDROID_MODIFIER_KEYCODES: dict = {
    "ctrl": 113,
    "control": 113,
    "shift": 59,
    "alt": 57,
    "meta": 117,
}

# Unicode key characters accepted by iOS keyboards (XCUITest).
IOS_UNICODE_KEYS: dict = {
    "enter": "\ue007",
    "return": "\ue006",
    "tab": "\ue004",
    "space": "\ue00d",
    "backspace": "\ue003",
    "delete": "\ue017",
    "escape": "\ue00c",
    "up": "\ue013",
    "down": "\ue015",
    "left": "\ue012",
    "right": "\ue014",
}


def _lookup(table: dict, name: str) -> int:
    key = (name or "").strip().lower()
    if key not in table:
        raise ValueError(f"Unknown key name: {name!r}. Supported keys: {sorted(set(table))}")
    return table[key]


def android_keycode(name: str) -> int:
    """Return the Android keycode for a keyboard key name."""
    return _lookup(ANDROID_KEYCODES, name)


def android_hardware_keycode(name: str) -> int:
    """Return the Android keycode for a hardware button name."""
    return _lookup(ANDROID_HARDWARE_KEYCODES, name)


def ios_unicode_key(name: str) -> str:
    """Return the unicode key character for a keyboard key name on iOS."""
    key = (name or "").strip().lower()
    if key not in IOS_UNICODE_KEYS:
        raise ValueError(f"Unknown iOS key name: {name!r}. Supported keys: {sorted(IOS_UNICODE_KEYS)}")
    return IOS_UNICODE_KEYS[key]


def single_char_keycode(char: str) -> int:
    """Return the Android keycode for a single letter or digit character.

    Android keycodes: ``A``..``Z`` are 29..54, ``0``..``9`` are 7..16.
    """
    if len(char) != 1:
        raise ValueError(f"Expected a single character, got {char!r}")
    lowered = char.lower()
    if "a" <= lowered <= "z":
        return 29 + (ord(lowered) - ord("a"))
    if "0" <= char <= "9":
        return 7 + (ord(char) - ord("0"))
    raise ValueError(f"Unsupported character for key sequence: {char!r}")


def expand_key_sequence(sequence: str) -> List[Tuple[List[int], int]]:
    """Expand a key sequence into press-order (modifiers, main-key) tuples.

    Segments are separated by ``,``; modifiers within a segment by ``+``.
    Example: ``"Control+A,Enter"`` -> ``[([113], 29), ([], 66)]``.
    Unknown key or modifier names raise ``ValueError``.
    """
    if not sequence or not sequence.strip():
        raise ValueError("Key sequence must not be empty")

    expanded: List[Tuple[List[int], int]] = []
    for segment in sequence.split(","):
        parts = [part.strip() for part in segment.split("+") if part.strip()]
        if not parts:
            raise ValueError(f"Empty segment in key sequence: {sequence!r}")

        modifier_names = parts[:-1]
        main_name = parts[-1]
        modifier_codes = []
        for modifier_name in modifier_names:
            mod_key = modifier_name.lower()
            if mod_key not in ANDROID_MODIFIER_KEYCODES:
                raise ValueError(f"Unknown modifier name: {modifier_name!r}. Supported modifiers: {sorted(ANDROID_MODIFIER_KEYCODES)}")
            modifier_codes.append(ANDROID_MODIFIER_KEYCODES[mod_key])

        main = main_name.strip().lower()
        if main in ANDROID_KEYCODES:
            main_code = ANDROID_KEYCODES[main]
        elif len(main_name) == 1:
            main_code = single_char_keycode(main_name)
        else:
            raise ValueError(f"Unknown key name: {main_name!r}. Supported keys: {sorted(set(ANDROID_KEYCODES))} or a single letter/digit")
        expanded.append((modifier_codes, main_code))

    return expanded
