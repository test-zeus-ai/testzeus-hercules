from typing import Any

from testzeus_hercules.core.agents.multimodal_base_nav_agent import (
    MultimodalBaseNavAgent,
)
from testzeus_hercules.core.mobile import ensure_mobile_tools_registered
from testzeus_hercules.core.mobile.prompts import (
    MOBILE_NAV_SYSTEM_PROMPT,
    get_mobile_nav_system_prompt,
)


class MobileNavAgent(MultimodalBaseNavAgent):
    """Navigation agent driving native mobile apps through the Appium toolset."""

    agent_name: str = "mobile_nav_agent"
    prompt = MOBILE_NAV_SYSTEM_PROMPT
    # The mobile prompt carries its own UI-assertion block (read_mobile_screen
    # based); the generic addendum points at a page-text tool this agent lacks.
    include_generic_ui_assertion_addendum = False

    def __init__(
        self,
        model_config: dict[str, Any],
        llm_config_params: dict[str, Any],
        system_prompt: str | None,
        agent_name: str = None,
        agent_prompt: str | None = None,
    ) -> None:
        # The prompt is resolved at construction time so opt-in tool guidance
        # (UI assertions) reflects the configuration in effect for this run.
        super().__init__(
            model_config,
            llm_config_params,
            system_prompt,
            agent_name=agent_name,
            agent_prompt=agent_prompt or get_mobile_nav_system_prompt(),
        )

    def register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """
        # The package gate is evaluated lazily, so make sure the toolset is
        # registered even when the package was imported before it was enabled.
        ensure_mobile_tools_registered()
        self.load_tools()
