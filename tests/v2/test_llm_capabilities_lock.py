"""Concurrent writers to the capability cache never drop each other's entries."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.llm.capabilities import Capabilities, CapabilityCache


def _caps(model: str) -> Capabilities:
    return Capabilities(server_version="1", model_id=model, context_tokens=8192, supports_tools=True, supports_tool_choice=False, supports_json_schema=True, image_mode="none")


def test_parallel_puts_keep_every_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "home" / "capabilities.json"
    original = CapabilityCache._entries

    def slow_entries(self: CapabilityCache) -> dict[str, Any]:
        entries = original(self)
        time.sleep(0.01)  # widen the read-modify-write window
        return entries

    monkeypatch.setattr(CapabilityCache, "_entries", slow_entries)
    models = [f"model-{index}" for index in range(12)]
    threads = [threading.Thread(target=lambda model=model: CapabilityCache(path).put("http://127.0.0.1:11434/v1", _caps(model))) for model in models]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    cache = CapabilityCache(path)
    assert all(cache.get("http://127.0.0.1:11434/v1", model, "1") is not None for model in models)
