"""Tests for the on-disk capability cache."""

import json
from pathlib import Path

from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache

BASE = "http://127.0.0.1:11434/v1"


def _caps(**overrides: object) -> Capabilities:
    values: dict[str, object] = {
        "server_version": "0.32.1",
        "model_id": "gemma4:e4b",
        "context_tokens": 32768,
        "supports_tools": True,
        "supports_tool_choice": False,
        "supports_json_schema": True,
        "image_mode": "data_url",
    }
    values.update(overrides)
    return Capabilities.model_validate(values)


def test_round_trip(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "home" / "capabilities.json")
    caps = _caps()

    cache.put(BASE, caps)

    assert CapabilityCache(cache.path).get(BASE, "gemma4:e4b", "0.32.1") == caps


def test_key_includes_base_url_model_and_server_version(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "capabilities.json")
    cache.put(BASE, _caps())

    assert cache.get(BASE, "gemma4:e4b", "0.33.0") is None
    assert cache.get(BASE, "other", "0.32.1") is None
    assert cache.get("http://127.0.0.1:8080/v1", "gemma4:e4b", "0.32.1") is None


def test_unknown_server_version_is_a_valid_key(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "capabilities.json")
    caps = _caps(server_version=None)
    cache.put(BASE, caps)

    assert cache.get(BASE, "gemma4:e4b", None) == caps


def test_entries_accumulate(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "capabilities.json")
    cache.put(BASE, _caps())
    cache.put(BASE, _caps(model_id="gemma4:26b-a4b-it-qat"))

    assert cache.get(BASE, "gemma4:e4b", "0.32.1") is not None
    assert cache.get(BASE, "gemma4:26b-a4b-it-qat", "0.32.1") is not None


def test_corrupt_file_is_tolerated_and_replaced(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text("{not json", encoding="utf-8")
    cache = CapabilityCache(path)

    assert cache.get(BASE, "gemma4:e4b", "0.32.1") is None
    cache.put(BASE, _caps())
    assert json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_invalid_entries_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({"version": 1, "entries": {f"{BASE}|gemma4:e4b|0.32.1": {"image_mode": "telepathy"}}}), encoding="utf-8")

    assert CapabilityCache(path).get(BASE, "gemma4:e4b", "0.32.1") is None


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "capabilities.json")
    cache.put(BASE, _caps())

    assert sorted(p.name for p in tmp_path.iterdir()) == ["capabilities.json"]
