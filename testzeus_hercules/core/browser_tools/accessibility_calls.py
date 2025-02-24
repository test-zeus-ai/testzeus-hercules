from typing import Annotated, Dict, Optional

from playwright.sync_api import Page
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["accessibility_nav_agent"],
    description="Test the accessibility of the current page.",
    name="test_page_accessibility",
)
def test_page_accessibility() -> (
    Annotated[Dict[str, str], "Result of the accessibility test."]
):
    """
    Test the accessibility of the current page using axe-core.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()

        # Wait for the page to be ready
        page.wait_for_load_state("domcontentloaded")

        # Load axe-core from CDN
        response = page.evaluate(
            """
            fetch('https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.0/axe.min.js')
                .then(response => response.text())
        """
        )

        # Add axe-core to the page
        page.add_script_tag(content=response)

        # Run accessibility tests
        axe_results = page.evaluate(
            """
            () => {
                return axe.run();
            }
        """
        )

        # Process results
        violations = axe_results.get("violations", [])
        if not violations:
            return {
                "success": True,
                "message": "No accessibility violations found.",
            }
        else:
            violation_details = []
            for violation in violations:
                violation_details.append(
                    {
                        "impact": violation.get("impact", "unknown"),
                        "description": violation.get("description", "No description"),
                        "help": violation.get("help", "No help available"),
                        "nodes": len(violation.get("nodes", [])),
                    }
                )

            return {
                "success": False,
                "message": "Accessibility violations found.",
                "violations": violation_details,
            }

    except Exception as e:
        logger.error(f"Error testing page accessibility: {str(e)}")
        return {"error": str(e)}
