"""``hercules setup``, step (a) for a hosted model: OpenRouter, or any OpenAI-compatible endpoint (a URL and its key).

The configuration gets the provider, the URL, the model and the NAME of the variable that holds the key; the key itself
goes to ``<hercules home>/llm.env`` (mode 600), or for OpenRouter to the OpenRouter key file of step (b), which the same
key serves. Runs, ``hercules doctor`` and the daemon read it from there, so the tester UI opens already configured.

One ``GET /models`` with the key checks the endpoint and lists its models to pick from. The key is never printed.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TextIO

import httpx

from testzeus_hercules.cli.keys import DEFAULT_KEY_ENV, MODEL_KEY_FILENAME, find_key, write_key_file
from testzeus_hercules.kernel.settings import LLMSettings, Settings
from testzeus_hercules.llm.providers import PROVIDERS, redact_url

if TYPE_CHECKING:
    from testzeus_hercules.cli.setup import SetupDeps, SetupOptions, _Prompter

__all__ = ["HOSTED_PROVIDERS", "HOSTED_MENU", "DEFAULT_MODEL_KEY_ENV", "configure_hosted", "is_hosted"]

# Providers configured here: a hosted endpoint with a key (openai-compatible may also point at a server on this machine).
HOSTED_PROVIDERS = ("openrouter", "openai-compatible", "openai", "anthropic", "azure")
HOSTED_MENU: Mapping[str, tuple[str, str]] = {
    "o": ("openrouter", "OpenRouter: a hosted model, with your OpenRouter key (the same key turns on hybrid mode)"),
    "u": ("openai-compatible", "another OpenAI-compatible endpoint: its URL and key (OpenAI, Groq, Together, a LiteLLM proxy, ...)"),
}
DEFAULT_MODEL_KEY_ENV = "HERCULES_LLM_API_KEY"
MODELS_TIMEOUT_S = 15.0
_LISTED = 15

EXIT_OK = 0
EXIT_KEY_FAILED = 1
EXIT_BAD_INPUT = 2


def is_hosted(provider: str | None) -> bool:
    return provider in HOSTED_PROVIDERS


@dataclass(frozen=True)
class _Check:
    ok: bool
    detail: str
    models: tuple[str, ...] = ()


def _list_models(base_url: str, key: str | None, deps: SetupDeps) -> _Check:
    """One ``GET <base_url>/models``; the detail never contains the key."""

    async def go() -> _Check:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        async with deps.make_client(MODELS_TIMEOUT_S) as client:
            response = await client.get(base_url.rstrip("/") + "/models", headers=headers)
        if response.status_code in (401, 403):
            return _Check(False, f"the endpoint refused the key (HTTP {response.status_code})")
        if response.status_code >= 400:
            return _Check(False, f"HTTP {response.status_code} from {redact_url(base_url)}/models")
        try:
            data = response.json().get("data", [])
            models = tuple(str(item["id"]) for item in data if isinstance(item, dict) and "id" in item)
        except (ValueError, AttributeError, TypeError):
            return _Check(True, "the endpoint answered, but not with a model list")
        return _Check(True, f"the endpoint answered with {len(models)} model{'s' if len(models) != 1 else ''}", models)

    try:
        return asyncio.run(go())
    except httpx.HTTPError as exc:
        return _Check(False, f"could not reach {redact_url(base_url)} ({type(exc).__name__})")


def _base_url(provider: str, options: SetupOptions, prompt: _Prompter, out: TextIO) -> str | None:
    spec = PROVIDERS[provider]
    if options.base_url:
        return options.base_url
    if not spec.requires_base_url:
        return None
    if not prompt.interactive:
        out.write(f"  error: --provider {provider} needs --base-url, the endpoint's URL (for example https://api.groq.com/openai/v1)\n")
        return ""
    while True:
        answer = prompt.ask("  Endpoint URL (OpenAI-compatible, usually ending in /v1)", "").strip()
        if not answer:
            out.write("  no URL given; skipped (run `hercules setup` again to add an endpoint)\n")
            return ""
        try:
            LLMSettings.model_validate({"provider": provider, "base_url": answer})
        except ValueError:
            out.write("  that is not a valid http(s) URL\n")
            continue
        if answer.startswith(("http://", "https://")):
            return answer
        out.write("  the URL must start with http:// or https://\n")


def _key(provider: str, variable: str, settings: Settings, options: SetupOptions, deps: SetupDeps, prompt: _Prompter, env: Mapping[str, str], out: TextIO) -> tuple[str | None, bool, int]:
    """The endpoint's key: (value, pasted now, exit code). Found keys are reported by where they are, never shown."""
    if options.llm_key_stdin:
        value = (deps.stdin or sys.stdin).readline().strip() or None
        if value is None:
            out.write("  --llm-key-stdin: no key on standard input\n")
            return None, False, EXIT_BAD_INPUT
        return value, True, EXIT_OK
    value, status = find_key(env, settings.home_dir, variable, MODEL_KEY_FILENAME)
    if value:
        out.write(f"  found the key {status.describe()}\n")
        return value, False, EXIT_OK
    if status.problem:
        out.write(f"  {status.problem}\n")
    pasted = prompt.secret(f"  Paste the {provider} endpoint's key (input is hidden; leave empty if it needs none): ") or None
    if pasted is None and not prompt.interactive:
        out.write(f"  no key yet: set {variable}, or run `hercules setup --provider {provider}` in your own terminal to paste it\n")
    return pasted, pasted is not None, EXIT_OK


def _model(check: _Check, provider: str, options: SetupOptions, prompt: _Prompter, out: TextIO) -> str | None:
    if options.model:
        if check.models and options.model not in check.models:
            out.write(f"  note: {options.model} is not in the endpoint's model list; using it as written\n")
        return options.model
    if not check.models:
        if prompt.interactive:
            return prompt.ask("  Model id (as the endpoint names it)", "").strip() or None
        out.write("  no model chosen: pass --model with the endpoint's model id\n")
        return None
    shown = check.models[:_LISTED]
    for index, name in enumerate(shown, start=1):
        out.write(f"    {index}. {name}\n")
    if len(check.models) > _LISTED:
        out.write(f"    (+{len(check.models) - _LISTED} more: type any id)\n")
    if not prompt.interactive:
        out.write("  no model chosen: pass --model with one of the ids above\n")
        return None
    answer = prompt.ask("  Use which model? (number or id)", "1").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1]
    return answer or shown[0]


def configure_hosted(
    provider: str,
    settings: Settings,
    options: SetupOptions,
    deps: SetupDeps,
    prompt: _Prompter,
    env: Mapping[str, str],
    out: TextIO,
) -> tuple[dict[str, Any], int]:
    """Step (a) for a hosted provider: the ``llm`` settings to write and an exit code."""
    spec = PROVIDERS[provider]
    if provider == "openrouter":
        out.write("  provider openrouter: uses your OpenRouter key, asked for in (b); it also turns on hybrid mode\n")
        chosen: dict[str, Any] = {"provider": provider}
        model = options.model or prompt.ask("  Model on OpenRouter ('auto': Gemma 4 26B)", "auto").strip()
        if model and model != "auto":
            chosen["model"] = model
        if options.api_key_env and options.api_key_env != DEFAULT_KEY_ENV:
            chosen["api_key_env"] = options.api_key_env
        return chosen, EXIT_OK

    base_url = _base_url(provider, options, prompt, out)
    if base_url == "":
        return {}, EXIT_BAD_INPUT if not prompt.interactive else EXIT_OK
    shown_url = redact_url(base_url or spec.default_base_url or "")
    out.write(f"  provider {provider} at {shown_url}\n")
    variable = options.api_key_env or spec.default_api_key_env or DEFAULT_MODEL_KEY_ENV
    key, pasted, code = _key(provider, variable, settings, options, deps, prompt, env, out)
    if code != EXIT_OK:
        return {}, code
    check = _Check(True, "not checked (--no-key-check)")
    if options.validate_key:
        check = _list_models(base_url or spec.default_base_url or "", key, deps)
    out.write(f"  endpoint check: {'ok, ' if check.ok else 'FAILED: '}{check.detail}\n")
    if not check.ok and pasted and not prompt.yes_no("  Save this endpoint and key anyway?", False):
        return {}, EXIT_KEY_FAILED
    picked = _model(check, provider, options, prompt, out)
    if key and pasted:
        saved = write_key_file(settings.home_dir, key, variable, MODEL_KEY_FILENAME)
        out.write(f"  key saved to {saved} (mode 600) as {variable}\n")
    endpoint: dict[str, Any] = {"provider": provider}
    if base_url:
        endpoint["base_url"] = base_url
    if picked:
        endpoint["model"] = picked
    if variable != spec.default_api_key_env:
        endpoint["api_key_env"] = variable
    return endpoint, EXIT_OK
