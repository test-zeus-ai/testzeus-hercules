"""Shared Gherkin generation helpers (LiteLLM-backed)."""

from __future__ import annotations

import re

from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model
from testzeus_hercules.utils.test_builder import collect_requirements, generate_gherkin


def clean_gherkin_output(raw: str) -> str:
    """Strip markdown fences and preamble from LLM-generated Gherkin."""
    text = raw.strip()
    if "```" in text:
        match = re.search(r"```(?:gherkin|feature)?\s*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
    for line in text.splitlines():
        if line.strip().startswith("Feature:"):
            idx = text.index(line)
            return text[idx:].strip()
    return text


def print_feature_block(feature: str) -> None:
    """Print Gherkin in the bordered format used by guided mode."""
    print("\n" + "=" * 50)
    print(feature)
    print("=" * 50)


async def generate_gherkin_from_description(description: str) -> str:
    """Generate a Gherkin feature file from plain English using LiteLLM."""
    llm = get_litellm_chat_model("planner_agent")
    req = collect_requirements(description)
    raw = await generate_gherkin(req, llm)
    return clean_gherkin_output(str(raw))
