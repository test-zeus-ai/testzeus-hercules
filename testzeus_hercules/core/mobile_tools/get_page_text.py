import time
from typing import Annotated

from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

@tool(
    agent_names=["navigation_nav_agent"],
    description="""Retrieve all visible text from the current mobile screen""",
    name="get_page_text",
)
async def get_page_text() -> Annotated[str, "Text content from current mobile screen"]:
    add_event(EventType.INTERACTION, EventData(detail="get_page_text"))
    logger.info("Executing get_page_text for mobile screen")
    start_time = time.time()

    appium_manager = AppiumManager.get_instance()
    if not appium_manager.driver:
        return "No active Appium session found"

    try:
        tree = await appium_manager.get_accessibility_tree()
        
        def extract_text(node: dict) -> list[str]:
            texts = []
            # Extract text content based on platform
            if appium_manager.platformName.lower() == "android":
                if node.get("text"):
                    texts.append(node["text"])
                if node.get("content-desc"):
                    texts.append(node["content-desc"])
            else:  # iOS
                if node.get("label"):
                    texts.append(node["label"])
                if node.get("value"):
                    texts.append(node["value"])
                if node.get("name"):
                    texts.append(node["name"])
            
            # Recursively extract from children
            for child in node.get("children", []):
                texts.extend(extract_text(child))
            return texts

        all_text = extract_text(tree)
        # Join all text items with newlines, keeping duplicates
        text_content = "\n".join(filter(None, all_text))

        elapsed_time = time.time() - start_time
        logger.info(f"Get page text completed in {elapsed_time} seconds")

        return text_content or "No text content found on screen"

    except Exception as e:
        logger.error(f"Error getting page text: {e}")
        return f"Error retrieving text content: {str(e)}"
