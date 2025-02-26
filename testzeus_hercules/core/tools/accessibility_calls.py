import json
import time
from typing import Annotated, Any, Dict, List

from playwright.async_api import Page

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import (
    accessibility_logger,
    accessibility_logger_json,
    tool,
)
from testzeus_hercules.utils.logger import logger

AXE_SCRIPT_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.0/axe.min.js"


@tool(
    agent_names=["browser_nav_agent"],
    description="Test the current page a11y accessibility using Axe-core. This tool is used to check only the a11y accessibility of the page.",
    name="test_page_accessibility",
)
async def test_page_accessibility(
    page_path: Annotated[str, "Current page URL"],
) -> Annotated[str, "Minified JSON string of accessibility test results"]:
    """
    Performs an accessibility test on the current page.

    Parameters:

    Returns:
    - A **string** with the Axe-core accessibility test results in minified JSON format.
    """
    try:
        # Create and use the PlaywrightManager
        browser_manager = PlaywrightManager()
        page = await browser_manager.get_current_page()

        if not page:
            raise ValueError("No active page found. OpenURL command opens a new page.")

        await browser_manager.wait_for_load_state_if_enabled(
            page=page, state="domcontentloaded"
        )

        # Inject the Axe-core script
        response = await page.evaluate(
            f"""
            fetch("{AXE_SCRIPT_URL}").then(res => res.text())
            """
        )
        await page.add_script_tag(content=response)

        # Run accessibility checks
        axe_results = await page.evaluate(
            """
            async () => {
                return await axe.run();
            }
            """
        )

        # Output summary of violations
        violations = axe_results.get("violations", [])
        incomplete = axe_results.get("incomplete", [])
        failureSummaries = []
        for violation in violations:
            nodes = violation.get("nodes", [])
            for node in nodes:
                failureSummaries.append(node.get("failureSummary"))

        # Log details
        accessibility_logger(page_path, violations + incomplete)
        accessibility_logger_json(page_path, json.dumps(axe_results, indent=4))

        # If no violation failures, return success
        if not failureSummaries:
            result_dict = {
                "status": "success",
                "message": "No accessibility violations found.",
                "details": "All good",
            }
            return json.dumps(result_dict, separators=(",", ":"))

        # Otherwise, report the failures
        result_dict = {
            "status": "failure",
            "message": f"Accessibility violations found: {len(failureSummaries)}",
            "details": failureSummaries,
        }
        return json.dumps(result_dict, separators=(",", ":"))

    except Exception as e:
        # In case of error, return an error payload
        error_dict = {
            "status": "error",
            "message": "An error occurred while performing the accessibility test.",
            "error": str(e),
        }
        return json.dumps(error_dict, separators=(",", ":"))
