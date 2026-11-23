"""``hercules setup``: a guided first-time setup. It starts nothing and downloads nothing.

It walks through four choices and writes them to ``<hercules home>/config.yaml``
(``--scope project``: the project's ``.hercules/config.yaml``):

a. **Local model server**: each local provider's default address is asked for its
   model list (one ``GET /models`` each). The hardware tier's recommended model is
   marked when a server has it. Hercules uses the local model in both modes.
b. **OpenRouter key** for JEV (hybrid mode): taken from the environment variable, or
   from ``<hercules home>/openrouter.env`` (mode 0600). A pasted key is read without
   echo and saved there. One tiny JEV decision checks that the key works (it costs a
   few millionths of a dollar). The key is never printed or written anywhere else.
c. **Egress policy**: ``hybrid`` (default: page text, and screenshots when JEV is
   unsure, may go to JEV), ``hybrid-text`` (text only) or ``local`` (nothing leaves).
d. **Telemetry**: the notice is shown, and the choice saved.
e. **Coding agents**: the agents found on this machine or in the project (Claude Code, Codex, Cursor, GitHub
   Copilot, Gemini CLI) are listed, and for each one not yet connected setup asks before adding the Hercules skill
   and MCP server to the project (``hercules init``), so the agent tests its changes through Hercules. Nothing is
   written without a yes; ``--agents`` names them up front instead (and is the only way with ``--yes``).

``--yes`` answers every question with its default (detected server, the key from the
environment or ``--key-stdin``, hybrid egress, telemetry on) for CI. Flags override
single answers. Exit codes: 0 done, 1 the key did not work, 2 bad input.
"""

from __future__ import annotations

import asyncio
import getpass
import io
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import httpx
import yaml

from testzeus_hercules.cli.first_run import record_notice_shown
from testzeus_hercules.cli.keys import DEFAULT_KEY_ENV, find_key, key_file, write_key_file
from testzeus_hercules.decide.contract import DecisionRequest, NoulQuestion
from testzeus_hercules.decide.errors import DecisionError
from testzeus_hercules.kernel.paths import HerculesPaths
from testzeus_hercules.kernel.settings import CONFIG_FILENAME, PROJECT_DIRNAME, LLMSettings, Settings, SettingsError
from testzeus_hercules.kernel.telemetry import TELEMETRY_NOTICE
from testzeus_hercules.llm.aliases import ModelNotFound, resolve_model
from testzeus_hercules.llm.providers import PROVIDERS, http_client, redact_url
from testzeus_hercules.llm.tiers import detect_memory_bytes, select_tier

__all__ = ["SetupDeps", "SetupOptions", "ServerFound", "detect_servers", "run_setup"]

EXIT_OK = 0
EXIT_KEY_FAILED = 1
EXIT_BAD_INPUT = 2
DETECT_TIMEOUT_S = 2.0
KEY_CHECK_TIMEOUT_S = 20.0

EGRESS_CHOICES: Mapping[str, str] = {
    "hybrid": "page text, and screenshots when JEV is unsure, may go to JEV (hosted); everything else stays local",
    "hybrid-text": "page text may go to JEV; screenshots never leave this machine",
    "local": "nothing leaves this machine (pure mode: the local model decides)",
}


def _default_client(timeout_s: float) -> httpx.AsyncClient:
    # Local servers are reached directly; an environment proxy applies to hosted services (the JEV key check) only.
    return http_client(timeout=timeout_s, follow_redirects=False)


@dataclass(frozen=True)
class SetupDeps:
    """Seams for tests: prompts, the HTTP client factory and memory detection."""

    ask: Callable[[str], str] = input
    ask_secret: Callable[[str], str] = getpass.getpass
    make_client: Callable[[float], httpx.AsyncClient] = _default_client
    detect_memory: Callable[[], int | None] = detect_memory_bytes
    stdin: TextIO | None = None
    which: Callable[[str], str | None] = shutil.which


@dataclass(frozen=True)
class SetupOptions:
    yes: bool = False
    project_dir: Path | None = None
    scope: str = "global"
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    egress: str | None = None
    telemetry: bool | None = None
    key_stdin: bool = False
    save_key: bool = False
    validate_key: bool = True
    clef_url: str | None = None
    agents: tuple[str, ...] | None = None  # connect these without asking; () for none; None: ask (none with --yes)
    api_key_env: str | None = None  # the variable holding a hosted model endpoint's key
    llm_key_stdin: bool = False  # read the model endpoint's key from standard input


AGENT_CHOICES = ("claude", "codex", "cursor", "copilot", "gemini", "generic")


@dataclass(frozen=True)
class ServerFound:
    """A local server that answered ``GET /models``: its address, the providers using that address, and its models."""

    base_url: str
    providers: tuple[str, ...]
    models: tuple[str, ...]
    recommended: str | None = None


@dataclass
class _Answers:
    llm: dict[str, Any] = field(default_factory=dict)
    egress: str | None = None
    telemetry: bool | None = None
    decider: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- detection


def _local_addresses() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, spec in PROVIDERS.items():
        if spec.local and spec.default_base_url:
            found.setdefault(spec.default_base_url, []).append(name)
    return found


async def detect_servers(make_client: Callable[[float], httpx.AsyncClient], tier_alias: str, tier_name: str) -> list[ServerFound]:
    """Every local provider address that lists models, with the tier's recommended model when served."""

    async def probe(client: httpx.AsyncClient, url: str, providers: list[str]) -> ServerFound | None:
        try:
            response = await client.get(url.rstrip("/") + "/models", timeout=DETECT_TIMEOUT_S)
            response.raise_for_status()
            models = tuple(str(item["id"]) for item in response.json()["data"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None
        recommended = None
        for provider in providers:
            try:
                recommended = resolve_model(tier_alias, provider, models, tier=tier_name)
                break
            except ModelNotFound:
                continue
        return ServerFound(base_url=url, providers=tuple(providers), models=models, recommended=recommended)

    async with make_client(DETECT_TIMEOUT_S) as client:
        results = await asyncio.gather(*(probe(client, url, providers) for url, providers in _local_addresses().items()))
    return [found for found in results if found is not None]


# --------------------------------------------------------------------------- prompts


class _Prompter:
    def __init__(self, options: SetupOptions, deps: SetupDeps, out: TextIO) -> None:
        self.interactive = not options.yes
        self.deps = deps
        self.out = out

    def ask(self, question: str, default: str) -> str:
        if not self.interactive:
            return default
        try:
            answer = self.deps.ask(f"{question} [{default}]: ").strip()
        except EOFError:
            return default
        return answer or default

    def yes_no(self, question: str, default: bool) -> bool:
        if not self.interactive:
            return default
        answer = self.ask(question, "Y/n" if default else "y/N").lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        return default

    def secret(self, question: str) -> str:
        if not self.interactive:
            return ""
        try:
            return self.deps.ask_secret(question).strip()
        except EOFError:
            return ""


# --------------------------------------------------------------------------- steps


def _choose_model(settings: Settings, options: SetupOptions, deps: SetupDeps, prompt: _Prompter, env: Mapping[str, str], out: TextIO) -> tuple[dict[str, Any], int]:
    """Step (a): a local model server, OpenRouter, or another OpenAI-compatible endpoint; returns (llm settings, exit code)."""
    from testzeus_hercules.cli.setup_model import configure_hosted, is_hosted

    out.write("\n(a) Model: reads the steps, writes values and covers JEV outages (in pure mode it also decides)\n")
    if is_hosted(options.provider):
        return configure_hosted(str(options.provider), settings, options, deps, prompt, env, out)
    chosen = _choose_server(settings, options, deps, prompt, out)
    if isinstance(chosen, str):
        return configure_hosted(chosen, settings, options, deps, prompt, env, out)
    return chosen, EXIT_OK


def _hosted_menu(out: TextIO) -> None:
    from testzeus_hercules.cli.setup_model import HOSTED_MENU

    for letter, (_provider, text) in HOSTED_MENU.items():
        out.write(f"  {letter}. {text}\n")


def _hosted_answer(answer: str) -> str | None:
    from testzeus_hercules.cli.setup_model import HOSTED_MENU

    found = HOSTED_MENU.get(answer.strip().lower())
    return found[0] if found else None


def _choose_server(settings: Settings, options: SetupOptions, deps: SetupDeps, prompt: _Prompter, out: TextIO) -> dict[str, Any] | str:
    """A local server (the ``llm`` settings), or the hosted provider picked from the menu."""
    if options.provider or options.base_url:
        chosen = {"provider": options.provider or settings.llm.provider, **({"base_url": options.base_url} if options.base_url else {}), **({"model": options.model} if options.model else {})}
        out.write(f"  using {chosen['provider']}{' at ' + redact_url(options.base_url) if options.base_url else ''} (from the command line)\n")
        return chosen
    selection = select_tier(deps.detect_memory(), settings.llm.tier)
    tier = selection.profile
    out.write(f"  hardware tier {tier.name}: recommended model {tier.model_alias}\n")
    servers = asyncio.run(detect_servers(deps.make_client, tier.model_alias, tier.name))
    if not servers:
        out.write("  no local model server answered on the usual ports (11434 Ollama, 8080 mlx-vlm / llama.cpp, 1234 LM Studio, 8000 vLLM)\n")
        if prompt.interactive:
            out.write("  or use a hosted model:\n")
            _hosted_menu(out)
            out.write("  s. skip: start a local server later\n")
            hosted = _hosted_answer(prompt.ask("  Use which?", "s"))
            if hosted:
                return hosted
        out.write(f"  keeping provider {settings.llm.provider}; start a server and run `hercules doctor` (setup starts nothing)\n")
        return {}
    default_index = 1
    for index, server in enumerate(servers, start=1):
        if settings.llm.provider in server.providers:
            default_index = index
        mark = f"; has the recommended {server.recommended}" if server.recommended else ""
        shown = ", ".join(server.models[:3]) + (f" (+{len(server.models) - 3} more)" if len(server.models) > 3 else "")
        out.write(f"  {index}. {redact_url(server.base_url)} ({' / '.join(server.providers)}): {shown or 'no models loaded'}{mark}\n")
    _hosted_menu(out)
    answer = prompt.ask("  Use which server?", str(default_index))
    hosted = _hosted_answer(answer)
    if hosted:
        return hosted
    try:
        server = servers[int(answer) - 1]
    except (ValueError, IndexError):
        out.write(f"  '{answer}' is not one of the listed numbers; using {default_index}\n")
        server = servers[default_index - 1]
    provider = settings.llm.provider if settings.llm.provider in server.providers else server.providers[0]
    chosen: dict[str, Any] = {"provider": provider}
    if options.model:
        chosen["model"] = options.model
    out.write(f"  provider {provider} at {redact_url(server.base_url)}\n")
    return chosen


def _key_check(settings: Settings, variable: str, key: str, deps: SetupDeps) -> tuple[bool, str]:
    """One tiny JEV decision with ``key``; returns (ok, what happened). Messages never contain the key."""
    from testzeus_hercules.decide.backends.jev import JevBackend

    request = DecisionRequest(state={"note": "Hercules setup: key check."}, questions={"ok": NoulQuestion(instructions="Is this a key check?")})

    async def ask() -> tuple[bool, str]:
        async with deps.make_client(KEY_CHECK_TIMEOUT_S) as client:
            jev = JevBackend(
                transport=settings.decider.jev_transport,
                model=settings.decider.model,
                api_key_env=variable,
                egress_policy="hybrid-text",
                env={variable: key},
                http=client,
                timeout_s=KEY_CHECK_TIMEOUT_S,
                max_retries=1,
                zdr=settings.decider.zdr,
            )
            try:
                decision = await jev.decide(request, purpose="key check")
            except DecisionError as exc:
                return False, str(exc).replace(key, "[redacted]")
            cost = f", cost ${decision.cost_usd:.6f}" if decision.cost_usd is not None else ""
            return True, f"JEV ({decision.model_version}) answered in {decision.latency_ms} ms{cost}"

    return asyncio.run(ask())


def _key(settings: Settings, env: Mapping[str, str], options: SetupOptions, deps: SetupDeps, prompt: _Prompter, out: TextIO) -> tuple[bool, int]:
    """Find, read, check and save the OpenRouter key; returns (a key is configured, exit code)."""
    variable = settings.decider.api_key_env or DEFAULT_KEY_ENV
    path = key_file(settings.home_dir)
    out.write("\n(b) OpenRouter key for JEV (hybrid mode: JEV makes the decisions)\n")
    out.write(f"  Hercules reads it from the environment variable {variable}, or from {path} (one line {variable}=..., readable only by you).\n")
    value, status = find_key(env, settings.home_dir, variable)
    pasted = False
    if options.key_stdin:
        stream = deps.stdin or sys.stdin
        value = stream.readline().strip() or None
        pasted = value is not None
        if not pasted:
            out.write("  --key-stdin: no key on standard input\n")
            return False, EXIT_BAD_INPUT
    elif value:
        out.write(f"  found a key {status.describe()}\n")
    else:
        if status.problem:
            out.write(f"  {status.problem}\n")
        value = prompt.secret("  Paste your OpenRouter key (input is hidden; leave empty to skip and use pure mode): ") or None
        pasted = value is not None
    if value is None:
        out.write("  no key: runs use pure mode (the local model decides). Run `hercules setup` again to add one.\n")
        return False, EXIT_OK
    if options.validate_key:
        ok, detail = _key_check(settings, variable, value, deps)
        out.write(f"  key check: {'ok, ' if ok else 'FAILED: '}{detail}\n")
        if not ok and not (pasted and prompt.yes_no("  Save the key anyway?", False)):
            return status.source == "env", EXIT_KEY_FAILED
    if pasted or (options.save_key and status.source == "env"):
        saved = write_key_file(settings.home_dir, value, variable)
        out.write(f"  saved to {saved} (mode 600)\n")
    return True, EXIT_OK


def _hosted_model(settings: Settings, llm: Mapping[str, Any]) -> bool:
    """Whether the chosen model endpoint is off this machine (egress policy local would block it)."""
    from testzeus_hercules.llm.providers import is_local_url

    provider = str(llm.get("provider") or settings.llm.provider)
    base_url = llm.get("base_url") or settings.llm.base_url or PROVIDERS[provider].default_base_url
    return bool(base_url) and not is_local_url(str(base_url), settings.egress.local_hosts)


def _egress(settings: Settings, options: SetupOptions, has_key: bool, prompt: _Prompter, out: TextIO) -> str:
    out.write("\n(c) Egress policy: what may leave this machine\n")
    for name, text in EGRESS_CHOICES.items():
        out.write(f"  {name:<11}  {text}\n")
    if options.egress:
        out.write(f"  using {options.egress} (from the command line)\n")
        return options.egress
    default = "hybrid" if has_key else settings.egress.policy
    while True:
        answer = prompt.ask("  Policy?", default)
        if answer in EGRESS_CHOICES:
            return answer
        out.write(f"  choose one of: {', '.join(EGRESS_CHOICES)}\n")


def _telemetry(settings: Settings, options: SetupOptions, prompt: _Prompter, out: TextIO) -> bool:
    out.write("\n(d) Telemetry\n")
    out.write("  " + TELEMETRY_NOTICE + "\n")
    record_notice_shown(settings.home_dir)
    if options.telemetry is not None:
        enabled = options.telemetry
    else:
        enabled = prompt.yes_no("  Keep anonymous telemetry on?", settings.telemetry.enabled)
    out.write(f"  telemetry {'on' if enabled else 'off'}\n")
    return enabled


# --------------------------------------------------------------------------- config file


def _config_path(settings: Settings, options: SetupOptions) -> Path:
    return settings.project_dir / PROJECT_DIRNAME / CONFIG_FILENAME if options.scope == "project" else settings.home_dir / CONFIG_FILENAME


def _write_config(path: Path, answers: _Answers) -> None:
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise SettingsError(f"{path}: cannot update the config file ({type(exc).__name__}); fix or remove it, then run setup again") from None
        existing = dict(loaded) if isinstance(loaded, dict) else {}
    updates: dict[str, dict[str, Any]] = {}
    if answers.llm:
        updates["llm"] = answers.llm
    if answers.egress is not None:
        updates["egress"] = {"policy": answers.egress}
    if answers.telemetry is not None:
        updates["telemetry"] = {"enabled": answers.telemetry}
    if answers.decider:
        updates["decider"] = answers.decider
    for section, values in updates.items():
        current = existing.get(section)
        existing[section] = {**(current if isinstance(current, dict) else {}), **values}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------- command


def _validate(options: SetupOptions) -> str | None:
    if options.provider is not None and options.provider not in PROVIDERS:
        return f"unknown provider {options.provider!r}; use one of {', '.join(PROVIDERS)}"
    if options.egress is not None and options.egress not in EGRESS_CHOICES:
        return f"unknown egress policy {options.egress!r}; use one of {', '.join(EGRESS_CHOICES)}"
    if options.scope not in ("global", "project"):
        return "scope must be global or project"
    unknown = [agent for agent in options.agents or () if agent not in AGENT_CHOICES]
    if unknown:
        return f"unknown coding agent {unknown[0]!r}; use any of {', '.join(AGENT_CHOICES)}, or none"
    if options.key_stdin and options.llm_key_stdin:
        return "--key-stdin and --llm-key-stdin both read standard input; give one key at a time"
    if options.provider or options.base_url or options.model or options.api_key_env:
        try:
            LLMSettings.model_validate(
                {key: value for key, value in (("provider", options.provider), ("base_url", options.base_url), ("model", options.model), ("api_key_env", options.api_key_env)) if value}
            )
        except ValueError as exc:
            return f"invalid model server option: {exc}"
    return None


def _validate_clef(options: SetupOptions, settings: Settings) -> str | None:
    """A Clef URL must be on this machine or on a host listed in egress.local_hosts."""
    if options.clef_url is None:
        return None
    from testzeus_hercules.decide.backends.clef import clef_endpoint

    try:
        clef_endpoint(options.clef_url, settings.egress.local_hosts)
    except ValueError as exc:
        return str(exc)
    return None


def run_setup(options: SetupOptions, *, env: Mapping[str, str] | None, deps: SetupDeps, out: TextIO) -> int:
    """Run the guided setup; returns the exit code."""
    import os

    environ: Mapping[str, str] = os.environ if env is None else env
    problem = _validate(options)
    if problem is not None:
        out.write(f"error: {problem}\n")
        return EXIT_BAD_INPUT
    try:
        settings = Settings.load(options.project_dir, env=environ)
    except SettingsError as exc:
        out.write(f"error: {exc}\n")
        return EXIT_BAD_INPUT
    problem = _validate_clef(options, settings)
    if problem is not None:
        out.write(f"error: {problem}\n")
        return EXIT_BAD_INPUT
    HerculesPaths.from_settings(settings).home_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    prompt = _Prompter(options, deps, out)
    out.write("Hercules setup: nothing is started or downloaded; one tiny JEV request checks the key when one is given.\n")
    answers = _Answers()
    answers.llm, code = _choose_model(settings, options, deps, prompt, environ, out)
    if code != EXIT_OK:
        return code
    hosted = _hosted_model(settings, answers.llm)
    has_key, code = _key(settings, environ, options, deps, prompt, out)
    if code == EXIT_BAD_INPUT:
        return code
    if hosted and options.egress == "local":
        out.write("error: egress policy local keeps everything on this machine, so it cannot use a hosted model; choose hybrid or hybrid-text\n")
        return EXIT_BAD_INPUT
    answers.egress = _egress(settings, options, has_key or hosted, prompt, out)
    answers.telemetry = _telemetry(settings, options, prompt, out)
    if options.clef_url:
        answers.decider = {"clef_url": options.clef_url}
    path = _config_path(settings, options)
    try:
        _write_config(path, answers)
        updated = Settings.load(options.project_dir, env=environ)
    except SettingsError as exc:
        out.write(f"error: {exc}\n")
        return EXIT_BAD_INPUT
    out.write(f"\nSaved to {path}\n")
    out.write(_mode_line(updated, environ) + "\n")
    _coding_agents(options, deps, prompt, out, environ)
    out.write("Next: `hercules doctor` checks the model server; `hercules run <case>` runs a test.\n")
    return code


def _mode_line(settings: Settings, env: Mapping[str, str]) -> str:
    from testzeus_hercules.cli.deciders import key_origin, prepare
    from testzeus_hercules.engine.factory import EngineFactoryError, FactoryOptions, resolve_mode

    setup = prepare(settings, env)
    try:
        mode, reason, _ = resolve_mode(setup.settings, setup.env, FactoryOptions(key_origin=key_origin(setup.key)))
    except EngineFactoryError as exc:
        return f"Runs: cannot pick a mode yet: {exc}"
    who = "JEV decides, the local model does the rest" if mode == "hybrid" else "the local model decides (or a configured local Clef server)"
    return f"Runs use {mode} mode: {who} [{reason}]"


def _coding_agents(options: SetupOptions, deps: SetupDeps, prompt: _Prompter, out: TextIO, env: Mapping[str, str]) -> None:
    """Offer to connect the coding agents found here; nothing is written without a yes (or ``--agents``)."""
    from testzeus_hercules.cli.agents_detect import find_agents, looks_like_project
    from testzeus_hercules.cli.init import AGENTS, InitOptions, run_init

    out.write("\n(e) Coding agents\n")
    if options.agents == ():
        out.write("  skipped (--agents none)\n")
        return
    project = Path(options.project_dir or Path.cwd()).resolve()
    if options.agents is None and not looks_like_project(project):
        out.write(f"  {project} does not look like a project folder; run `hercules init --agent <agent>` inside your project to connect a coding agent\n")
        return
    found = find_agents(project, deps.which)
    to_connect: list[str] = list(options.agents or ())
    if options.agents is None:
        if not found:
            out.write("  no coding agent found; connect one later with `hercules init --agent claude` (or codex, cursor, copilot, gemini, generic)\n")
            return
        for agent in found:
            state = "Hercules is already set up for it" if agent.set_up else "not connected yet"
            out.write(f"  {AGENTS[agent.agent]}: found ({agent.why}); {state}\n")
        waiting = [agent for agent in found if not agent.set_up]
        if not waiting:
            return
        if not prompt.interactive:
            names = ",".join(agent.agent for agent in waiting)
            out.write(f"  not changed: with --yes, pass --agents {names} to connect them\n")
            return
        out.write(f"  Connecting an agent adds a Hercules skill and the hercules MCP server to {project}, so the agent tests\n" "  its changes through Hercules (existing files are never replaced).\n")
        to_connect = [agent.agent for agent in waiting if prompt.yes_no(f"  Connect {AGENTS[agent.agent]}?", True)]
    for name in to_connect:
        written = io.StringIO()
        code = run_init(InitOptions(agent=name, project_dir=project), env=env, out=written)
        out.write(f"  {AGENTS[name]}: {'connected' if code == 0 else 'could not be connected'}\n")
        for line in written.getvalue().splitlines():
            if line.strip():
                out.write(f"    {line}\n")
    if not to_connect:
        out.write("  nothing connected; run `hercules init --agent <agent>` any time\n")


def parse_agents(value: str | None) -> tuple[str, ...] | None:
    """``--agents claude,codex`` as a tuple; ``none`` as (); None when not given."""
    if value is None:
        return None
    names = tuple(dict.fromkeys(name.strip().lower() for name in value.split(",") if name.strip()))
    return () if names in ((), ("none",)) else names


def parse_on_off(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("on", "true", "yes", "1")


def setup_options_from_args(args: Any) -> SetupOptions:
    return SetupOptions(
        yes=args.yes,
        project_dir=args.project,
        scope=args.scope,
        provider=args.provider,
        base_url=args.base_url,
        model=args.model,
        egress=args.egress,
        telemetry=parse_on_off(args.telemetry),
        key_stdin=args.key_stdin,
        save_key=args.save_key,
        validate_key=not args.no_key_check,
        clef_url=args.clef_url,
        agents=parse_agents(getattr(args, "agents", None)),
        api_key_env=getattr(args, "api_key_env", None),
        llm_key_stdin=getattr(args, "llm_key_stdin", False),
    )
