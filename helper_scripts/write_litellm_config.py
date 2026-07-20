#!/usr/bin/env python3
"""Write a CI LiteLLM agents config from GitHub Actions secrets."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

AGENT_NAMES = ("planner_agent", "nav_agent", "helper_agent")
DEFAULT_MODEL_NAME = "gemini-2.5-flash"
DEFAULT_MAX_TOKENS = 4096


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} must be set.")
    return value


def int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer.") from exc


def main() -> None:
    output_file = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else os.getenv("AGENTS_LLM_CONFIG_FILE", "agents_llm_config.json")
    )
    provider = (
        os.getenv("AGENTS_LLM_CONFIG_FILE_REF_KEY", "litellm").strip() or "litellm"
    )
    model_name = (
        os.getenv("HERCULES_CI_LITELLM_MODEL_NAME", DEFAULT_MODEL_NAME).strip()
        or DEFAULT_MODEL_NAME
    )
    api_key = required_env("HERCULES_CI_LITELLM_API_KEY")
    base_url = required_env("HERCULES_CI_LITELLM_BASE_URL").rstrip("/")
    max_tokens = int_env("HERCULES_CI_LITELLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)

    agent_config = {
        "model_name": model_name,
        "model_api_key": api_key,
        "model_base_url": base_url,
        "model_api_type": "litellm",
        "llm_config_params": {
            "temperature": 0.0,
            "max_tokens": max_tokens,
        },
    }

    config = {provider: {agent_name: agent_config for agent_name in AGENT_NAMES}}
    output_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote LiteLLM agents config to {output_file} with provider '{provider}'.")


if __name__ == "__main__":
    main()
