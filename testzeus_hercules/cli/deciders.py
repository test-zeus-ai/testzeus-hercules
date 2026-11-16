"""Who decides in a run, as the CLI sets it up around the engine factory.

* The JEV key comes from the environment or the private key file (see :mod:`testzeus_hercules.cli.keys`).
* Hybrid is the default: ``auto`` mode picks it when a key is found and the egress
  policy lets page content leave the machine (``hybrid`` by default). Without a key
  the run is pure, with one notice line (:func:`fallback_notice`), never an error.
* In hybrid mode JEV weighs every candidate on the page; a pick larger than one JEV
  request is split into parts by the backend.
* In pure mode a reachable local Clef server (``decider.clef_url``, or
  ``decider.backend: clef``) makes the decisions, with the local model covering its
  outages and doing every language task; the engine factory sets that up (see
  :mod:`testzeus_hercules.engine.factory`), and when the server cannot be reached the
  local model decides and a note says why.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from testzeus_hercules.cli.keys import DEFAULT_KEY_ENV, KeyStatus, with_key, with_model_key
from testzeus_hercules.decide.backends.clef import DEFAULT_CLEF_URL, clef_url
from testzeus_hercules.engine.factory import RunEngines
from testzeus_hercules.kernel.settings import Settings
from testzeus_hercules.llm.providers import model_key_variable

__all__ = ["DEFAULT_CLEF_URL", "DeciderSetup", "clef_url", "fallback_notice", "key_origin", "prepare"]


@dataclass(frozen=True)
class DeciderSetup:
    """The environment handed to the engine factory, and where the JEV key came from."""

    env: dict[str, str]
    settings: Settings
    key: KeyStatus


def key_variable(settings: Settings, override: str | None = None) -> str:
    return override or settings.decider.api_key_env or DEFAULT_KEY_ENV


def prepare(settings: Settings, env: Mapping[str, str], *, key_env: str | None = None) -> DeciderSetup:
    """The run's environment, with the JEV key from the key file when the environment has none."""
    merged, status = with_key(env, settings.home_dir, key_variable(settings, key_env))
    merged = with_model_key(merged, settings.home_dir, model_key_variable(settings.llm))
    return DeciderSetup(env=merged, settings=settings, key=status)


def key_origin(key: KeyStatus) -> str | None:
    """The key file's path when the key came from it (for the mode's reason), else None: the environment case says "is set"."""
    return str(key.path) if key.source == "file" else None


def fallback_notice(engines: RunEngines, key: KeyStatus, requested_mode: str) -> str | None:
    """One line when the default hybrid mode fell back to pure because no JEV key is configured."""
    if requested_mode != "auto" or engines.mode != "pure" or key.found or not engines.mode_reason.startswith("auto:"):
        return None
    problem = f" ({key.problem})" if key.problem else ""
    return f"notice: hybrid mode (JEV decides) needs an OpenRouter key and none is configured{problem}; " "this run is pure: the local model makes every decision. Add a key with `hercules setup`."
