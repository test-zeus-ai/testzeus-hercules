"""``hercules init --agent claude|codex|cursor|copilot|gemini|generic``: connect a coding agent to Hercules.

* claude: ``.mcp.json`` entry, skill ``.claude/skills/hercules-test/SKILL.md``, subagent ``.claude/agents/qa-runner.md``;
* codex: skill ``.agents/skills/hercules-test/SKILL.md``, an ``AGENTS.md`` block, and the
  ``[mcp_servers.hercules]`` table for ``~/.codex/config.toml`` (printed; written only with ``--write-global``);
* cursor: ``.cursor/mcp.json`` entry and rule ``.cursor/rules/hercules.mdc``;
* copilot (GitHub Copilot in VS Code): ``.vscode/mcp.json`` entry, skill ``.github/skills/hercules-test/SKILL.md`` and
  instructions ``.github/instructions/hercules.instructions.md``;
* gemini (Gemini CLI): ``.gemini/settings.json`` entry, skill ``.agents/skills/hercules-test/SKILL.md`` and a
  ``GEMINI.md`` block;
* generic (any other MCP client): skill ``.agents/skills/hercules-test/SKILL.md``, an ``AGENTS.md`` block, and the MCP
  server entry printed as JSON for the agent's own config.

Existing files are never replaced without ``--force``; JSON configs are merged (other
servers kept) and ``AGENTS.md``/``GEMINI.md`` get a marked block appended. ``--print`` shows what
would be written and changes nothing. Exit code 1 when an existing config cannot be parsed.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, TextIO

from testzeus_hercules.cli import init_templates as templates

EXIT_OK = 0
EXIT_ERROR = 1
_FORCE_HINT = "use --force to replace it"


@dataclass(frozen=True)
class InitOptions:
    agent: str
    project_dir: Path | None = None
    print_only: bool = False
    force: bool = False
    write_global: bool = False
    command: str = "hercules"


@dataclass
class _Plan:
    """Files to write (path → new content) and messages, decided before anything is written."""

    writes: dict[Path, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_init(options: InitOptions, *, env: Mapping[str, str] | None = None, out: TextIO) -> int:
    environ = os.environ if env is None else env
    project = Path(os.path.abspath(options.project_dir or Path.cwd()))
    plan = _Plan()
    stdio = {"command": options.command, "args": ["mcp"]}
    shared_skill = project / ".agents" / "skills" / templates.SKILL_NAME / "SKILL.md"
    if options.agent == "claude":
        _merge_json_server(plan, project / ".mcp.json", {"type": "stdio", **stdio}, options.force)
        _new_file(plan, project / ".claude" / "skills" / templates.SKILL_NAME / "SKILL.md", templates.SKILL, options.force)
        _new_file(plan, project / ".claude" / "agents" / "qa-runner.md", templates.QA_RUNNER, options.force)
    elif options.agent == "codex":
        _new_file(plan, shared_skill, templates.SKILL, options.force)
        _agents_block(plan, project / "AGENTS.md", options.force)
        snippet = templates.codex_snippet(options.command)
        if options.write_global:
            _codex_config(plan, _codex_home(environ) / "config.toml", snippet, options.force)
        else:
            plan.notes.append(f"add this to {_codex_home(environ) / 'config.toml'} (or run again with --write-global):\n\n{snippet}")
    elif options.agent == "cursor":
        _merge_json_server(plan, project / ".cursor" / "mcp.json", stdio, options.force)
        _new_file(plan, project / ".cursor" / "rules" / "hercules.mdc", templates.CURSOR_RULE, options.force)
    elif options.agent == "copilot":
        _merge_json_server(plan, project / ".vscode" / "mcp.json", {"type": "stdio", **stdio}, options.force, key="servers")
        _new_file(plan, project / ".github" / "skills" / templates.SKILL_NAME / "SKILL.md", templates.SKILL, options.force)
        _new_file(plan, project / ".github" / "instructions" / "hercules.instructions.md", templates.COPILOT_INSTRUCTIONS, options.force)
    elif options.agent == "gemini":
        _merge_json_server(plan, project / ".gemini" / "settings.json", stdio, options.force)
        _new_file(plan, shared_skill, templates.SKILL, options.force)
        _agents_block(plan, project / "GEMINI.md", options.force)
    else:
        _new_file(plan, shared_skill, templates.SKILL, options.force)
        _agents_block(plan, project / "AGENTS.md", options.force)
        entry = json.dumps({"mcpServers": {"hercules": stdio}}, indent=2)
        plan.notes.append(f"add the hercules MCP server to your agent's MCP config (most use this shape), and point it at {_shown(shared_skill, project)}:\n\n{entry}\n")

    for message in plan.errors:
        out.write(f"error: {message}\n")
    if options.print_only:
        for path, content in plan.writes.items():
            out.write(f"=== {_shown(path, project)} ===\n{content}{'' if content.endswith(chr(10)) else chr(10)}\n")
    else:
        for path, content in plan.writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            out.write(f"wrote {_shown(path, project)}\n")
    for note in plan.notes:
        out.write(note if note.endswith("\n") else note + "\n")
    if not plan.errors and not options.print_only:
        _record_setup(options, project, environ)
        out.write(f"Hercules is set up for {options.agent}. Start the daemon with `hercules serve` (or let `hercules mcp` start it) and run `hercules doctor` to check the model server.\n")
    return EXIT_ERROR if plan.errors else EXIT_OK


AGENTS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "copilot": "GitHub Copilot (VS Code)",
    "gemini": "Gemini CLI",
    "generic": "another MCP agent",
}
_TELEMETRY_NAMES = {"claude": "claude-code", "generic": "other"}


def _record_setup(options: InitOptions, project: Path, env: Mapping[str, str]) -> None:
    """One anonymous ``agent_setup`` event naming the agent (a fixed name); never fails the command."""
    from testzeus_hercules.kernel.paths import HerculesPaths
    from testzeus_hercules.kernel.settings import Settings, SettingsError
    from testzeus_hercules.kernel.telemetry import TelemetryClient

    try:
        settings = Settings.load(project, env=env)
    except SettingsError:
        return
    with TelemetryClient(settings, HerculesPaths.from_settings(settings)) as telemetry:
        telemetry.emit("agent_setup", agent_client=_TELEMETRY_NAMES.get(options.agent, options.agent))


def _shown(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def _new_file(plan: _Plan, path: Path, content: str, force: bool) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8", errors="replace") == content:
            plan.notes.append(f"unchanged {path.name}")
            return
        if not force:
            plan.notes.append(f"skipped {path} (it exists; {_FORCE_HINT})")
            return
    plan.writes[path] = content


def _merge_json_server(plan: _Plan, path: Path, entry: dict[str, Any], force: bool, *, key: str = "mcpServers") -> None:
    data: Any = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError):
            plan.errors.append(f"{path} is not valid JSON; fix it or add the hercules server by hand")
            return
        if not isinstance(data, dict) or not isinstance(data.get(key, {}), dict):
            plan.errors.append(f"{path} does not hold an object with an '{key}' object; add the hercules server by hand")
            return
    servers = dict(data.get(key, {}))
    current = servers.get("hercules")
    if current == entry:
        plan.notes.append(f"unchanged {path.name} (hercules server already configured)")
        return
    if current is not None and not force:
        plan.notes.append(f"skipped the hercules entry in {path} (it exists with other settings; {_FORCE_HINT})")
        return
    servers["hercules"] = entry
    plan.writes[path] = json.dumps({**data, key: servers}, indent=2) + "\n"


def _agents_block(plan: _Plan, path: Path, force: bool) -> None:
    block = templates.AGENTS_BLOCK
    if not path.exists():
        plan.writes[path] = block
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    start, end = text.find(templates.AGENTS_START), text.find(templates.AGENTS_END)
    if start == -1 or end == -1:
        plan.writes[path] = text + ("" if text.endswith("\n") else "\n") + "\n" + block
        return
    current = text[start : end + len(templates.AGENTS_END)] + "\n"
    if current == block:
        plan.notes.append(f"unchanged {path.name}")
    elif not force:
        plan.notes.append(f"skipped the Hercules block in {path} (it exists; {_FORCE_HINT})")
    else:
        plan.writes[path] = text[:start] + block + text[end + len(templates.AGENTS_END) :].lstrip("\n")


def _codex_home(env: Mapping[str, str]) -> Path:
    configured = env.get("CODEX_HOME")
    if configured:
        return Path(os.path.abspath(os.path.expanduser(configured)))
    home = env.get("HOME")
    return (Path(home) if home else Path.home()) / ".codex"


_TABLE_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")


def _codex_config(plan: _Plan, path: Path, snippet: str, force: bool) -> None:
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            config = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            plan.errors.append(f"{path} is not valid TOML; add the snippet by hand:\n\n{snippet}")
            return
        current = config.get("mcp_servers", {}).get("hercules") if isinstance(config.get("mcp_servers"), dict) else None
        if current == tomllib.loads(snippet)["mcp_servers"]["hercules"]:
            plan.notes.append(f"unchanged {path} (hercules server already configured)")
            return
        if current is not None:
            if not force:
                plan.notes.append(f"skipped [mcp_servers.hercules] in {path} (it exists with other settings; {_FORCE_HINT})")
                return
            text = _drop_table(text, "mcp_servers.hercules")
    separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    plan.writes[path] = text + separator + snippet


def _drop_table(text: str, name: str) -> str:
    """``text`` without the ``[name]`` table and its sub-tables."""
    kept: list[str] = []
    dropping = False
    for line in text.splitlines(keepends=True):
        header = _TABLE_HEADER.match(line)
        if header is not None:
            table = header.group(1).replace(" ", "")
            dropping = table == name or table.startswith(name + ".")
        if not dropping:
            kept.append(line)
    return "".join(kept)
