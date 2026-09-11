from testzeus_hercules.core.agents.multimodal_base_nav_agent import (
    MultimodalBaseNavAgent,
)
from testzeus_hercules.core.mobile.prompts import MOBILE_NAV_SYSTEM_PROMPT


class MobileNavAgent(MultimodalBaseNavAgent):
    """Navigation agent driving native mobile apps through the Appium toolset."""

    agent_name: str = "mobile_nav_agent"
    prompt = MOBILE_NAV_SYSTEM_PROMPT

    def register_tools(self) -> None:
        """
        Register all the tools that the agent can perform.
        """

        self.load_tools()
