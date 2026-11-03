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
        mobile_prompt = agent_prompt or get_mobile_nav_system_prompt()
        # The shared nav system prompt (``other_settings.system_prompt``) is
        # written for every nav agent. Passed to the base class it would
        # REPLACE this prompt and drop the mobile tool rules, so it is
        # appended as extra instructions instead.
        custom_prompt = self._custom_prompt_text(system_prompt)
        if custom_prompt:
            mobile_prompt = f"{mobile_prompt}\n\n## Additional instructions\n{custom_prompt}"
        super().__init__(
            model_config,
            llm_config_params,
            None,
            agent_name=agent_name,
            agent_prompt=mobile_prompt,
        )
        # The base class fills $basic_test_information only when long-term
        # memory exists; otherwise the literal placeholder would reach the LLM.
        message = getattr(self, "system_message", None)
        if isinstance(message, str):
            self.system_message = message.replace("${basic_test_information}", "").replace("$basic_test_information", "")

    @staticmethod
    def _custom_prompt_text(system_prompt: "str | list[str] | None") -> str:
        if not system_prompt:
            return ""
        text = "\n".join(system_prompt) if isinstance(system_prompt, list) else str(system_prompt)
        return text.strip()

    def register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """
        # The package gate is evaluated lazily, so make sure the toolset is
        # registered even when the package was imported before it was enabled.
        ensure_mobile_tools_registered()
        self.load_tools()
