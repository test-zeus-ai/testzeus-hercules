"""Canonical model names and their per-server ids, resolved against ``/v1/models``.

Users configure one canonical name (``gemma-4-26b-a4b``) and Hercules finds
the id the running server uses for it. Resolution order: the exact id, then
the alias's known ids for the provider, then a case-insensitive prefix match
(of the requested name and of each known id, also ignoring an ``org/`` prefix).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Sequence

TierName = Literal["unsupported", "floor", "standard", "recommended", "comfortable"]

GEMMA_4_26B_A4B = "gemma-4-26b-a4b"
GEMMA_4_E4B = "gemma-4-e4b"

TIER_MODEL_ALIAS: Mapping[str, str] = MappingProxyType({"unsupported": GEMMA_4_E4B, "floor": GEMMA_4_E4B, "standard": GEMMA_4_26B_A4B, "recommended": GEMMA_4_26B_A4B, "comfortable": GEMMA_4_26B_A4B})

OLLAMA_26B_QAT = "gemma4:26b-a4b-it-qat"
OLLAMA_26B_Q4 = "gemma4:26b-a4b-it-q4_K_M"
# Assumed tag: the Ollama library lists E4B under the gemma4 family; verify with `ollama list` once pulled.
OLLAMA_E4B = "gemma4:e4b"

# Tiers that run the 4-bit build with vision on Ollama; the others prefer the smaller QAT build.
_Q4_FIRST_TIERS = frozenset({"recommended", "comfortable"})

_ALIASES: Mapping[tuple[str, str], tuple[str, ...]] = MappingProxyType(
    {
        (GEMMA_4_26B_A4B, "ollama"): (OLLAMA_26B_QAT, OLLAMA_26B_Q4),
        (GEMMA_4_26B_A4B, "lmstudio"): ("google/gemma-4-26b-a4b",),
        (GEMMA_4_26B_A4B, "vllm"): ("google/gemma-4-26B-A4B-it",),
        (GEMMA_4_26B_A4B, "mlx-vlm"): ("mlx-community/gemma-4-26B-A4B-it-4bit",),
        (GEMMA_4_26B_A4B, "mlx"): ("mlx-community/gemma-4-26B-A4B-it-4bit",),
        (GEMMA_4_26B_A4B, "openrouter"): ("google/gemma-4-26b-a4b-it",),
        (GEMMA_4_E4B, "ollama"): (OLLAMA_E4B,),
        # Assumed ids, following each server's naming for the 26B build.
        (GEMMA_4_E4B, "lmstudio"): ("google/gemma-4-e4b",),
        (GEMMA_4_E4B, "vllm"): ("google/gemma-4-E4B-it",),
        (GEMMA_4_E4B, "mlx-vlm"): ("mlx-community/gemma-4-E4B-it-4bit",),
        (GEMMA_4_E4B, "mlx"): ("mlx-community/gemma-4-E4B-it-4bit",),
    }
)

_MAX_LISTED = 10


class ModelNotFound(LookupError):
    """The requested model is not served. ``fix`` holds a pull command when one is known."""

    def __init__(self, message: str, *, available: Sequence[str], fix: str | None) -> None:
        super().__init__(message)
        self.available = tuple(available)
        self.fix = fix


def is_canonical(name: str) -> bool:
    """True for a canonical model name (``gemma-4-26b-a4b``) that is resolved to a server's own id."""
    return name.lower() in set(TIER_MODEL_ALIAS.values()) or any(alias == name.lower() for alias, _ in _ALIASES)


def alias_candidates(alias: str, provider: str, *, tier: str | None = None) -> tuple[str, ...]:
    """Known server ids for a canonical alias, most preferred first (empty if not an alias)."""
    candidates = _ALIASES.get((alias.lower(), provider), ())
    if provider == "ollama" and candidates == (OLLAMA_26B_QAT, OLLAMA_26B_Q4) and tier in _Q4_FIRST_TIERS:
        return (OLLAMA_26B_Q4, OLLAMA_26B_QAT)
    return candidates


def resolve_model(requested: str, provider: str, available_ids: Iterable[str], *, tier: str | None = None, verbatim: bool = False) -> str:
    """Return the served id for ``requested`` or raise ``ModelNotFound`` with a useful message.

    ``verbatim`` (a generic OpenAI-compatible endpoint or a proxy): a name that is not a canonical model name is
    used exactly as written (``gemini/gemini-2.5-flash``, ``bedrock/...``), listed by the server or not.
    """
    available = list(dict.fromkeys(available_ids))
    if requested in available:
        return requested
    if verbatim and not is_canonical(requested):
        return requested
    candidates = alias_candidates(requested, provider, tier=tier)
    by_lower = {model_id.lower(): model_id for model_id in available}
    for candidate in candidates:
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    for prefix in (requested, *candidates):
        matches = sorted(model_id for model_id in available if _has_prefix(model_id, prefix))
        if matches:
            return matches[0]
    raise _not_found(requested, provider, available, candidates)


def _has_prefix(model_id: str, prefix: str) -> bool:
    needle = prefix.lower()
    short_needle = needle.rsplit("/", 1)[-1]
    if not short_needle:
        return False
    lowered = model_id.lower()
    return lowered.startswith(needle) or lowered.rsplit("/", 1)[-1].startswith(short_needle)


def _not_found(requested: str, provider: str, available: Sequence[str], candidates: Sequence[str]) -> ModelNotFound:
    fix = f"ollama pull {candidates[0] if candidates else requested}" if provider == "ollama" else None
    if available:
        listed = ", ".join(available[:_MAX_LISTED])
        more = f" (+{len(available) - _MAX_LISTED} more)" if len(available) > _MAX_LISTED else ""
        served = f"Available: {listed}{more}."
    else:
        served = "The server reports no models."
    tried = f" (tried {', '.join(candidates)})" if candidates else ""
    hint = f" Pull it with: {fix}" if fix else " Load or download the model on the server, or set llm.model to one of the available ids."
    return ModelNotFound(f"model '{requested}'{tried} is not served by {provider}. {served}{hint}", available=available, fix=fix)
