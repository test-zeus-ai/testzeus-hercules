from typing import Annotated, Dict
import json
import os
import time
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.llm_helper import create_multimodal_agent
from autogen import UserProxyAgent

# Initialize the image comparison agent
image_agent = create_multimodal_agent(
    name="image-comparer",
    system_message="You are a visual comparison agent. You can compare images and provide feedback. Your only purpose is to do visual comparison of images",
)

# Create a UserProxyAgent for the image comparison agent
image_ex_user_proxy = UserProxyAgent(
    name="image_ex_user_proxy",
    system_message="A human admin requesting image comparison.",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=0,
    code_execution_config={"use_docker": False},
)


def write_json(filepath: str, data: Dict) -> None:
    """Write data to a JSON file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@tool(
    agent_names=["mobile_nav_agent"],
    description="Compare the current screen with a reference image.",
    name="compare_visual_screenshot",
)
def compare_visual_screenshot(
    reference_image_path: Annotated[str, "Path to the reference image."],
    comparison_description: Annotated[str, "Description of what to compare."],
) -> Annotated[Dict[str, str], "Result of the visual comparison."]:
    """
    Compare the current screen with a reference image.
    """
    try:
        appium_manager = AppiumManager()
        current_screen = appium_manager.see_screen()
        if not current_screen:
            return {"error": "Failed to capture current screen"}

        if not os.path.exists(reference_image_path):
            return {"error": f"Reference image not found: {reference_image_path}"}

        # Save current screen for comparison
        timestamp = int(time.time())
        screenshots_dir = os.path.join(
            get_global_conf().get_proof_path(), "screenshots"
        )
        os.makedirs(screenshots_dir, exist_ok=True)
        current_screen_path = os.path.join(
            screenshots_dir, f"current_screen_{timestamp}.png"
        )
        current_screen.save(current_screen_path)

        # Prepare comparison data
        comparison_data = {
            "reference_image": reference_image_path,
            "current_screen": current_screen_path,
            "comparison_description": comparison_description,
            "timestamp": timestamp,
        }

        # Save comparison data
        comparison_file = os.path.join(screenshots_dir, f"comparison_{timestamp}.json")
        write_json(comparison_file, comparison_data)

        return {
            "status": "success",
            "message": "Visual comparison completed",
            "reference": reference_image_path,
            "current": current_screen_path,
            "comparison_file": comparison_file,
        }

    except Exception as e:
        logger.error(f"Error in visual comparison: {str(e)}")
        return {"error": str(e)}


@tool(
    agent_names=["mobile_nav_agent"],
    description="Validate visual features in the current screen.",
    name="validate_visual_feature",
)
def validate_visual_feature(
    feature_description: Annotated[str, "Description of features to validate."],
) -> Annotated[Dict[str, str], "Result of the visual validation."]:
    """
    Validate visual features in the current screen.
    """
    try:
        appium_manager = AppiumManager()
        current_screen = appium_manager.see_screen()
        if not current_screen:
            return {"error": "Failed to capture current screen"}

        # Save current screen for validation
        timestamp = int(time.time())
        screenshots_dir = os.path.join(
            get_global_conf().get_proof_path(), "screenshots"
        )
        os.makedirs(screenshots_dir, exist_ok=True)
        current_screen_path = os.path.join(
            screenshots_dir, f"validation_{timestamp}.png"
        )
        current_screen.save(current_screen_path)

        # Prepare validation data
        validation_data = {
            "feature_description": feature_description,
            "screenshot": current_screen_path,
            "timestamp": timestamp,
        }

        # Save validation data
        validation_file = os.path.join(screenshots_dir, f"validation_{timestamp}.json")
        write_json(validation_file, validation_data)

        return {
            "status": "success",
            "message": "Visual validation completed",
            "screenshot": current_screen_path,
            "validation_file": validation_file,
            "features": feature_description,
        }

    except Exception as e:
        logger.error(f"Error in visual validation: {str(e)}")
        return {"error": str(e)}
