import asyncio
import json
import os
import time
from typing import Annotated, Dict, Union

from autogen import UserProxyAgent
from PIL import Image
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.llm_helper import create_multimodal_agent
from testzeus_hercules.utils.logger import logger

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


@tool(
    agent_names=["browser_nav_agent"],
    name="compare_visual_screenshot",
    description="Compare the current browser view with a reference image or screenshot and log results",
)
async def compare_visual_screenshot(
    reference_image_path: Annotated[str, "Path to the reference image/screenshot to compare against"],
    comparison_title: Annotated[str, "Title/description for this comparison"],
) -> Union[str, Dict[str, str]]:
    """

    Args:
        reference_image_path: Path to the reference image file
        comparison_title: Title or description for this comparison

    Returns:
        str: Comparison results
        dict: Error message if something fails
    """
    try:
        if not os.path.exists(reference_image_path):
            return {"error": f"Reference image not found: {reference_image_path}"}

        # Get current screenshot
        browser_manager = PlaywrightManager()
        screenshot_stream = await browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = await browser_manager.get_current_page()
            await browser_manager.take_screenshots("comparison_screenshot", page)
            screenshot_stream = await browser_manager.get_latest_screenshot_stream()

        if not screenshot_stream:
            return {"error": "Failed to capture current browser view"}

        # Get the proof path from CONF for storing comparison logs
        proof_path = get_global_conf().get_proof_path() or "."
        comparisons_dir = os.path.join(proof_path, "visual_comparisons")
        os.makedirs(comparisons_dir, exist_ok=True)

        # Create a timestamped filename for this comparison
        timestamp = int(time.time())
        base_filename = comparison_title.replace(" ", "_").replace("/", "_").replace(":", "_").lower() + f"_{timestamp}"
        # base_filename = f"comparison_{timestamp}"

        # Define paths for all files
        comparison_file = os.path.join(comparisons_dir, f"{base_filename}.json")
        screenshot_file = os.path.join(comparisons_dir, f"{base_filename}_current.png")

        # Save the current screenshot
        screenshot = Image.open(screenshot_stream)
        screenshot.save(screenshot_file)

        # Update comparison metadata to include screenshot path
        comparison_data = {
            "title": comparison_title,
            "timestamp": timestamp,
            "reference_image": reference_image_path,
            "current_screenshot": screenshot_file,
            "screenshot_taken": timestamp,
        }

        # Prepare the comparison prompt
        comparison_prompt = """Compare these two images in detail:
        1. Reference Image:
        <img {reference}>
        2. Current Screenshot:
        <img {screenshot}>
        
        Please analyze and describe:
        1. Visual similarities and differences
        2. Layout differences
        3. Any missing or extra elements
        4. Color or styling differences
        5. Whether they can be considered visually equivalent
        
        Be specific and detailed in your comparison.
        If images are not similar with minimal differences then return that comparioson failed with results.
        """

        # Format the prompt with actual image URIs
        # in comparison prompt pass the path instead base64 encoded string
        message = comparison_prompt.format(reference=reference_image_path, screenshot=screenshot_file)

        logger.debug(f"Comparison prompt: {message}")

        chat_response = await asyncio.to_thread(image_ex_user_proxy.initiate_chat, image_agent, message=message)

        last_message = None
        for msg in reversed(chat_response.chat_history):
            if msg.get("role") == "user":
                last_message = msg.get("content")
                break

        if not last_message:
            error_msg = "No response received from image comparison agent"
            # Log the error in the comparison file
            comparison_data["status"] = "error"
            comparison_data["error"] = error_msg
            await _write_comparison_to_file(comparison_data, comparison_file)
            return {"error": error_msg}

        # Add the comparison results to the data
        comparison_data["status"] = "success"
        comparison_data["comparison_results"] = last_message

        # Save the comparison data
        await _write_comparison_to_file(comparison_data, comparison_file)

        # log cost of the comparison
        logger.info(f"Cost of comparison: {chat_response.cost}")

        # Return the comparison results
        return f"Comparison saved to {comparison_file}\n" f"Current screenshot saved to {screenshot_file}\n\n" f"Results:\n{last_message}"

    except Exception as e:
        logger.exception(f"Error in compare_visual: {e}")
        return {"error": str(e)}


async def _write_comparison_to_file(comparison_data: Dict, filepath: str) -> None:
    """Write comparison data to a JSON file asynchronously."""
    try:

        def write_json(path: str, data: Dict) -> None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        await asyncio.to_thread(write_json, filepath, comparison_data)
        logger.info(f"Comparison data saved to: {filepath}")
    except Exception as e:
        logger.error(f"Failed to write comparison data to file: {e}")


@tool(
    agent_names=["browser_nav_agent"],
    name="validate_visual_feature",
    description="Validate if specific features or items are present in the current browser view",
)
async def validate_visual_feature(
    feature_description: Annotated[str, "Description of features/items to look for in the current view"],
    search_title: Annotated[str, "Title for this feature search"],
) -> Union[str, Dict[str, str]]:
    """
    Analyze the current browser view to validate presence of specific features.

    Args:
        feature_description: Description of what to look for in the current view
        search_title: Title or description for this feature search

    Returns:
        str: Validation results
        dict: Error message if something fails
    """
    try:
        # Get current screenshot
        browser_manager = PlaywrightManager()
        screenshot_stream = await browser_manager.get_latest_screenshot_stream()
        if not screenshot_stream:
            page = await browser_manager.get_current_page()
            await browser_manager.take_screenshots("feature_validation", page)
            screenshot_stream = await browser_manager.get_latest_screenshot_stream()

        if not screenshot_stream:
            return {"error": "Failed to capture current browser view"}

        # Get the proof path for storing validation logs
        proof_path = get_global_conf().get_proof_path() or "."
        validation_dir = os.path.join(proof_path, "visual_validations")
        os.makedirs(validation_dir, exist_ok=True)

        # Create timestamped files
        timestamp = int(time.time())
        base_filename = search_title.replace(" ", "_").replace("/", "_").replace(":", "_").lower() + f"_{timestamp}"
        validation_file = os.path.join(validation_dir, f"{base_filename}.json")
        screenshot_file = os.path.join(validation_dir, f"{base_filename}.png")

        # Save the current screenshot
        screenshot = Image.open(screenshot_stream)
        screenshot.save(screenshot_file)

        # Prepare the validation prompt
        validation_prompt = f"""Analyze this image and validate the following features/items:
        {feature_description}

        Image to analyze:
        <img {screenshot_file}>
        
        Please provide:
        1. Whether each requested feature/item is present
        2. Location and appearance details of found items
        3. Any missing features
        4. Confidence level in your findings
        
        Be specific and detailed in your analysis."""

        logger.debug(f"Validation prompt: {validation_prompt}")

        chat_response = await asyncio.to_thread(image_ex_user_proxy.initiate_chat, image_agent, message=validation_prompt)

        last_message = None
        for msg in reversed(chat_response.chat_history):
            if msg.get("role") == "user":
                last_message = msg.get("content")
                break

        if not last_message:
            error_msg = "No response received from image analysis agent"
            validation_data = {"timestamp": timestamp, "feature_description": feature_description, "screenshot": screenshot_file, "status": "error", "error": error_msg}
            await _write_comparison_to_file(validation_data, validation_file)
            return {"error": error_msg}

        # Save validation results
        validation_data = {
            "search_title": search_title,
            "timestamp": timestamp,
            "feature_description": feature_description,
            "screenshot": screenshot_file,
            "status": "success",
            "validation_results": last_message,
        }
        await _write_comparison_to_file(validation_data, validation_file)

        # log cost of the validation
        logger.info(f"Cost of validation: {chat_response.cost}")

        return f"Feature validation saved to {validation_file}\n" f"Screenshot saved to {screenshot_file}\n\n" f"Results:\n{last_message}"

    except Exception as e:
        logger.exception(f"Error in validate_visual_feature: {e}")
        return {"error": str(e)}
