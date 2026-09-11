"""Offline tests for the keymap tables, sequence expansion, and key-press ordering."""

import asyncio

import pytest
from tests import conftest_mobile as cm
from tests.conftest_mobile import FakeDriver, fake_manager, mobile_env  # noqa: F401

pytest_plugins = ["tests.conftest_mobile"]

# ── Android keyboard keycodes ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("enter", 66),
        ("tab", 61),
        ("space", 62),
        ("backspace", 67),
        ("delete", 112),
        ("escape", 111),
        ("up", 19),
        ("down", 20),
        ("left", 21),
        ("right", 22),
    ],
)
def test_android_keyboard_keycodes(name: str, code: int) -> None:
    assert cm.mobile_keymaps.android_keycode(name) == code


def test_android_aliases_and_case_insensitivity() -> None:
    assert cm.mobile_keymaps.android_keycode("RETURN") == 66
    assert cm.mobile_keymaps.android_keycode(" Esc ") == 111


# ── Android hardware keycodes ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("volume_up", 24),
        ("volume_down", 25),
        ("power", 26),
        ("camera", 27),
        ("call", 5),
        ("end_call", 6),
        ("menu", 82),
        ("back", 4),
        ("home", 3),
        ("app_switch", 187),
    ],
)
def test_android_hardware_keycodes(name: str, code: int) -> None:
    assert cm.mobile_keymaps.android_hardware_keycode(name) == code


# ── iOS unicode key characters ──────────────────────────────────────────────


def test_ios_unicode_keys() -> None:
    assert cm.mobile_keymaps.ios_unicode_key("enter") == "\ue007"
    assert cm.mobile_keymaps.ios_unicode_key("return") == "\ue006"
    assert cm.mobile_keymaps.ios_unicode_key("backspace") == "\ue003"
    assert cm.mobile_keymaps.ios_unicode_key("delete") == "\ue017"
    assert cm.mobile_keymaps.ios_unicode_key("tab") == "\ue004"
    assert cm.mobile_keymaps.ios_unicode_key("space") == "\ue00d"
    assert cm.mobile_keymaps.ios_unicode_key("escape") == "\ue00c"
    assert cm.mobile_keymaps.ios_unicode_key("up") == "\ue013"
    assert cm.mobile_keymaps.ios_unicode_key("down") == "\ue015"
    assert cm.mobile_keymaps.ios_unicode_key("left") == "\ue012"
    assert cm.mobile_keymaps.ios_unicode_key("right") == "\ue014"


def test_unknown_names_raise_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown key name"):
        cm.mobile_keymaps.android_keycode("warp_drive")
    with pytest.raises(ValueError, match="Unknown key name"):
        cm.mobile_keymaps.android_hardware_keycode("warp_drive")
    with pytest.raises(ValueError, match="Unknown iOS key name"):
        cm.mobile_keymaps.ios_unicode_key("warp_drive")
    with pytest.raises(ValueError):
        cm.mobile_keymaps.single_char_keycode("ab")
    with pytest.raises(ValueError):
        cm.mobile_keymaps.single_char_keycode("!")


def test_single_char_keycodes() -> None:
    assert cm.mobile_keymaps.single_char_keycode("a") == 29
    assert cm.mobile_keymaps.single_char_keycode("A") == 29
    assert cm.mobile_keymaps.single_char_keycode("z") == 54
    assert cm.mobile_keymaps.single_char_keycode("0") == 7
    assert cm.mobile_keymaps.single_char_keycode("9") == 16


# ── Sequence expansion ──────────────────────────────────────────────────────


def test_expand_key_sequence_modifiers_and_segments() -> None:
    assert cm.mobile_keymaps.expand_key_sequence("Control+A,Enter") == [([113], 29), ([], 66)]
    assert cm.mobile_keymaps.expand_key_sequence("Enter") == [([], 66)]
    assert cm.mobile_keymaps.expand_key_sequence("Shift+Tab") == [([59], 61)]
    assert cm.mobile_keymaps.expand_key_sequence("ctrl+alt+z") == [([113, 57], 29 + 25)]
    assert cm.mobile_keymaps.expand_key_sequence("5") == [([], 12)]  # KEYCODE_0 is 7, so 5 -> 12


def test_expand_key_sequence_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="Unknown modifier"):
        cm.mobile_keymaps.expand_key_sequence("Hyper+A")
    with pytest.raises(ValueError, match="Unknown key name"):
        cm.mobile_keymaps.expand_key_sequence("Control+Frobnicate")
    with pytest.raises(ValueError, match="must not be empty"):
        cm.mobile_keymaps.expand_key_sequence("   ")


# ── Manager key presses against a FakeDriver ────────────────────────────────


def test_press_key_sequence_keycode_order_on_fake_driver(fake_manager) -> None:
    manager, driver = fake_manager
    asyncio.run(manager.press_key_sequence("Control+A,Enter"))
    # modifier down, main key, modifier up (reverse order), then next segment
    assert driver.keycodes == [113, 29, 113, 66]


def test_press_key_and_hardware_key_on_fake_driver(fake_manager) -> None:
    manager, driver = fake_manager
    asyncio.run(manager.press_key("enter"))
    asyncio.run(manager.press_hardware_key("volume_up"))
    asyncio.run(manager.press_back())
    asyncio.run(manager.press_home())
    assert driver.keycodes == [66, 24, 4, 3]


def test_manager_unknown_key_raises_value_error(fake_manager) -> None:
    manager, _driver = fake_manager
    with pytest.raises(ValueError, match="Unknown key name"):
        asyncio.run(manager.press_key("warp_drive"))
    with pytest.raises(ValueError, match="not supported on iOS|Unknown key name"):
        asyncio.run(manager.press_hardware_key("warp_drive"))
    with pytest.raises(ValueError):
        asyncio.run(manager.press_key_sequence("Control+Nope"))


# ── iOS: cache invalidation and honest unsupported-key reporting ────────────


def _ios_manager(cm, monkeypatch, stake, driver):
    monkeypatch.setenv("DEVICE_OS", "ios")
    manager = cm.AppiumManager(stake_id=stake, _driver_factory=lambda: driver)
    manager.driver = driver
    return manager


def test_ios_press_key_invalidates_accessibility_cache(mobile_env, clean_manager_state, monkeypatch) -> None:
    cm.install_stub_selenium(monkeypatch)
    driver = cm.CountingSourceDriver()
    manager = _ios_manager(cm, monkeypatch, "ios-key", driver)
    try:
        asyncio.run(manager.read_screen())  # fetch 1, result cached
        asyncio.run(manager.press_key("enter"))
        asyncio.run(manager.read_screen())  # must fetch a fresh tree
        assert driver.page_source_fetches == 2
    finally:
        manager.shutdown_pool()


def test_ios_press_key_sequence_invalidates_accessibility_cache(mobile_env, clean_manager_state, monkeypatch) -> None:
    cm.install_stub_selenium(monkeypatch)
    driver = cm.CountingSourceDriver()
    manager = _ios_manager(cm, monkeypatch, "ios-seq", driver)
    try:
        asyncio.run(manager.read_screen())  # fetch 1, result cached
        asyncio.run(manager.press_key_sequence("Enter"))
        asyncio.run(manager.read_screen())  # must fetch a fresh tree
        assert driver.page_source_fetches == 2
    finally:
        manager.shutdown_pool()


def test_ios_key_sequence_unmapped_key_raises_instead_of_silent_skip(mobile_env, clean_manager_state, monkeypatch) -> None:
    driver = cm.FakeDriver()
    manager = _ios_manager(cm, monkeypatch, "ios-tab", driver)
    try:
        with pytest.raises(ValueError, match="tab"):
            asyncio.run(manager.press_key_sequence("Tab"))
        assert driver.keycodes == []  # nothing partially pressed
    finally:
        manager.shutdown_pool()


def test_ios_key_sequence_mapped_keys_still_sent_on_ios(mobile_env, clean_manager_state, monkeypatch) -> None:
    sent = cm.install_stub_selenium(monkeypatch)
    driver = cm.FakeDriver()
    manager = _ios_manager(cm, monkeypatch, "ios-ok", driver)
    try:
        asyncio.run(manager.press_key_sequence("Enter,A"))
        enter_char = cm.mobile_keymaps.ios_unicode_key("enter")
        assert sent == [(enter_char,), ("a",)]
    finally:
        manager.shutdown_pool()
