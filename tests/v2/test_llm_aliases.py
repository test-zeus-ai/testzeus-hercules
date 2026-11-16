"""Tests for canonical model aliases and model resolution against /v1/models."""

import pytest

from testzeus_hercules.llm.aliases import (
    GEMMA_4_26B_A4B,
    GEMMA_4_E4B,
    TIER_MODEL_ALIAS,
    ModelNotFound,
    alias_candidates,
    resolve_model,
)


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("ollama", "gemma4:26b-a4b-it-qat"),
        ("lmstudio", "google/gemma-4-26b-a4b"),
        ("vllm", "google/gemma-4-26B-A4B-it"),
        ("mlx-vlm", "mlx-community/gemma-4-26B-A4B-it-4bit"),
    ],
)
def test_canonical_26b_alias_resolves_on_each_server(provider: str, model_id: str) -> None:
    available = ["something-else", model_id]

    assert resolve_model(GEMMA_4_26B_A4B, provider, available) == model_id


def test_llamacpp_resolves_by_case_insensitive_prefix_of_the_alias() -> None:
    available = ["nomic-embed", "gemma-4-26B-A4B-it-Q4_K_M.gguf"]

    assert resolve_model(GEMMA_4_26B_A4B, "llamacpp", available) == "gemma-4-26B-A4B-it-Q4_K_M.gguf"


def test_exact_id_wins_over_alias_candidates() -> None:
    available = ["gemma4:26b-a4b-it-qat", "gemma-4-26b-a4b"]

    assert resolve_model("gemma-4-26b-a4b", "ollama", available) == "gemma-4-26b-a4b"


def test_ollama_26b_prefers_qat_on_standard_tier_and_q4_with_vision_above() -> None:
    available = ["gemma4:26b-a4b-it-q4_K_M", "gemma4:26b-a4b-it-qat"]

    assert resolve_model(GEMMA_4_26B_A4B, "ollama", available, tier="standard") == "gemma4:26b-a4b-it-qat"
    assert resolve_model(GEMMA_4_26B_A4B, "ollama", available, tier="recommended") == "gemma4:26b-a4b-it-q4_K_M"
    assert alias_candidates(GEMMA_4_26B_A4B, "ollama", tier="comfortable")[0] == "gemma4:26b-a4b-it-q4_K_M"


def test_ollama_falls_back_to_the_other_26b_build() -> None:
    assert resolve_model(GEMMA_4_26B_A4B, "ollama", ["gemma4:26b-a4b-it-qat"], tier="recommended") == "gemma4:26b-a4b-it-qat"


def test_e4b_alias_on_ollama() -> None:
    assert resolve_model(GEMMA_4_E4B, "ollama", ["gemma4:e4b"]) == "gemma4:e4b"


def test_case_insensitive_prefix_of_requested_name() -> None:
    assert resolve_model("Qwen3", "openai-compatible", ["llama3", "qwen3-30b-a3b"]) == "qwen3-30b-a3b"


def test_tier_alias_map() -> None:
    assert TIER_MODEL_ALIAS["floor"] == GEMMA_4_E4B
    assert TIER_MODEL_ALIAS["unsupported"] == GEMMA_4_E4B
    for tier in ("standard", "recommended", "comfortable"):
        assert TIER_MODEL_ALIAS[tier] == GEMMA_4_26B_A4B


def test_missing_model_lists_up_to_ten_ids_and_the_ollama_pull_command() -> None:
    available = [f"model-{index:02d}" for index in range(15)]

    with pytest.raises(ModelNotFound) as caught:
        resolve_model(GEMMA_4_26B_A4B, "ollama", available, tier="standard")

    message = str(caught.value)
    assert "model-00" in message and "model-09" in message
    assert "model-10" not in message
    assert "5 more" in message
    assert "ollama pull gemma4:26b-a4b-it-qat" in message
    assert caught.value.fix == "ollama pull gemma4:26b-a4b-it-qat"
    assert caught.value.available == tuple(available)


def test_missing_model_on_empty_server() -> None:
    with pytest.raises(ModelNotFound, match="no models"):
        resolve_model(GEMMA_4_E4B, "lmstudio", [])


def test_missing_model_on_non_ollama_has_no_pull_command() -> None:
    with pytest.raises(ModelNotFound) as caught:
        resolve_model("my-model", "vllm", ["other"])

    assert "ollama pull" not in str(caught.value)


def test_ollama_pull_hint_uses_requested_tag_when_not_an_alias() -> None:
    with pytest.raises(ModelNotFound) as caught:
        resolve_model("qwen3:8b", "ollama", ["gemma4:e4b"])

    assert caught.value.fix == "ollama pull qwen3:8b"
