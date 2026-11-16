"""Which coding agents this machine and project use, so ``hercules setup`` can offer to connect them.

An agent counts as found when its command is on ``PATH`` or the project already has its files (``.claude/``,
``AGENTS.md``, ``.cursor/``, ``.vscode/``, ``.gemini/``). Nothing is read from those files; only their existence is
checked. Whether Hercules is already set up for it is the presence of the file ``hercules init`` writes.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSignals:
    agent: str
    commands: tuple[str, ...]
    markers: tuple[str, ...]
    installed_file: str  # written by `hercules init --agent <agent>`


SIGNALS: tuple[AgentSignals, ...] = (
    AgentSignals("claude", ("claude",), (".claude", ".mcp.json", "CLAUDE.md"), ".claude/skills/hercules-test/SKILL.md"),
    AgentSignals("codex", ("codex",), (".codex", "AGENTS.md"), ".agents/skills/hercules-test/SKILL.md"),
    AgentSignals("cursor", ("cursor", "cursor-agent"), (".cursor", ".cursorrules"), ".cursor/rules/hercules.mdc"),
    AgentSignals("copilot", ("code", "code-insiders"), (".vscode", ".github/copilot-instructions.md"), ".github/instructions/hercules.instructions.md"),
    AgentSignals("gemini", ("gemini",), (".gemini", "GEMINI.md"), ".gemini/settings.json"),
)


@dataclass(frozen=True)
class FoundAgent:
    agent: str
    why: str  # "the claude command" or "the project's .claude/ folder"
    set_up: bool  # Hercules is already set up for it in this project


def find_agents(project: Path, which: Callable[[str], str | None] = shutil.which) -> list[FoundAgent]:
    """The coding agents found on this machine or in ``project``, in a fixed order."""
    found = []
    for signals in SIGNALS:
        why = next((f"the {command} command" for command in signals.commands if which(command)), None)
        if why is None:
            marker = next((name for name in signals.markers if (project / name).exists()), None)
            why = f"the project's {marker}" if marker else None
        if why is not None:
            found.append(FoundAgent(signals.agent, why, (project / signals.installed_file).exists()))
    return found


def looks_like_project(path: Path) -> bool:
    """Whether ``path`` looks like a code project (where skills belong), not a home or system folder."""
    if path == Path.home() or path == Path(path.anchor):
        return False
    markers = (".git", "package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml", "build.gradle", ".hercules")
    return any((path / marker).exists() for marker in markers)
