import json
import os
from typing import Annotated, Dict, Optional

import autogen
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger


def write_json(filepath: str, data: Dict) -> None:
    """Write data to a JSON file."""
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def _write_comparison_to_file(comparison_data: Dict, filepath: str) -> None:
    """Write comparison data to a JSON file."""
    try:
        write_json(filepath, comparison_data)
        logger.info(f"Comparison data written to {filepath}")
    except Exception as e:
        logger.error(f"Error writing comparison data: {e}")


@tool(
    agent_names=["visual_nav_agent"],
    description="Compare the current screenshot with a reference image.",
    name="compare_visual_screenshot",
)
def compare_visual_screenshot(
    reference_image_path: Annotated[str, "Path to the reference image."],
    comparison_description: Annotated[str, "Description of what to compare."],
) -> Annotated[Dict[str, str], "Result of the visual comparison."]:
    """
    Compare the current screenshot with a reference image.
    """
    try:
        browser_manager = PlaywrightManager()
        screenshot_stream = browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = browser_manager.get_current_page()
            browser_manager.take_screenshots("comparison_screenshot", page)
            screenshot_stream = browser_manager.get_latest_screenshot_stream()

        if not screenshot_stream:
            return {"error": "Failed to capture current browser view"}

        # Initialize image comparison agents
        image_agent = autogen.AssistantAgent(
            name="image_comparison_agent",
            llm_config={"config_list": get_global_conf().get_llm_config()},
            system_message="You are an expert at comparing images and identifying visual differences.",
        )

        image_ex_user_proxy = autogen.UserProxyAgent(
            name="image_comparison_user",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=1,
            code_execution_config=False,
        )

        # Prepare the message for the image agent
        message = f"""
        Compare these two images:
        1. Reference image: {reference_image_path}
        2. Current screenshot (in memory)

        Focus on: {comparison_description}

        Provide a detailed comparison highlighting any differences.
        """

        # Perform the comparison
        chat_response = image_ex_user_proxy.initiate_chat(image_agent, message=message)

        # Process the results
        comparison_data = {
            "reference_image": reference_image_path,
            "comparison_description": comparison_description,
            "comparison_result": chat_response.get(
                "content", "No response from comparison"
            ),
        }

        # Save the comparison results
        comparison_file = os.path.join(
            get_global_conf().get_proof_path(), "visual_comparison.json"
        )
        _write_comparison_to_file(comparison_data, comparison_file)

        return {
            "success": True,
            "message": "Visual comparison completed successfully",
            "details": comparison_data["comparison_result"],
        }

    except Exception as e:
        logger.error(f"Error in visual comparison: {str(e)}")
        comparison_data = {
            "error": str(e),
            "reference_image": reference_image_path,
            "comparison_description": comparison_description,
        }
        comparison_file = os.path.join(
            get_global_conf().get_proof_path(), "visual_comparison_error.json"
        )
        _write_comparison_to_file(comparison_data, comparison_file)
        return {"error": str(e)}


@tool(
    agent_names=["visual_nav_agent"],
    description="Validate visual features in the current view.",
    name="validate_visual_feature",
)
def validate_visual_feature(
    feature_description: Annotated[
        str, "Description of the visual feature to validate."
    ],
) -> Annotated[Dict[str, str], "Result of the visual validation."]:
    """
    Validate visual features in the current view.
    """
    try:
        browser_manager = PlaywrightManager()
        screenshot_stream = browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = browser_manager.get_current_page()
            browser_manager.take_screenshots("feature_validation", page)
            screenshot_stream = browser_manager.get_latest_screenshot_stream()

        if not screenshot_stream:
            return {"error": "Failed to capture current browser view"}

        # Initialize image validation agents
        image_agent = autogen.AssistantAgent(
            name="image_validation_agent",
            llm_config={"config_list": get_global_conf().get_llm_config()},
            system_message="You are an expert at validating visual features in user interfaces.",
        )

        image_ex_user_proxy = autogen.UserProxyAgent(
            name="image_validation_user",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=1,
            code_execution_config=False,
        )

        # Prepare the validation prompt
        validation_prompt = f"""
        Analyze the current screenshot and validate the following feature:
        {feature_description}

        Provide a detailed assessment of whether the feature is present and correctly displayed.
        """

        # Perform the validation
        chat_response = image_ex_user_proxy.initiate_chat(
            image_agent, message=validation_prompt
        )

        # Process the results
        validation_data = {
            "feature_description": feature_description,
            "validation_result": chat_response.get(
                "content", "No response from validation"
            ),
        }

        # Save the validation results
        validation_file = os.path.join(
            get_global_conf().get_proof_path(), "visual_validation.json"
        )
        _write_comparison_to_file(validation_data, validation_file)

        return {
            "success": True,
            "message": "Visual validation completed successfully",
            "details": validation_data["validation_result"],
        }

    except Exception as e:
        logger.error(f"Error in visual validation: {str(e)}")
        validation_data = {
            "error": str(e),
            "feature_description": feature_description,
        }
        validation_file = os.path.join(
            get_global_conf().get_proof_path(), "visual_validation_error.json"
        )
        _write_comparison_to_file(validation_data, validation_file)
        return {"error": str(e)}
