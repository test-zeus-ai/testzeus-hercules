from copy import deepcopy
import json
from typing import Annotated, Any, Dict, List

from testzeus_hercules.core.appium_manager import AppiumManager, REFERENCE_DICT
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

ANDROID_INPUT_CLASSES = {
    "android.widget.EditText",
    "android.widget.AutoCompleteTextView",
    "android.widget.MultiAutoCompleteTextView",
    "android.widget.TextView",
    "android.widget.SearchView",
    "android.widget.NumberPicker",
    "android.widget.DatePicker",
    "android.widget.TimePicker",
    "android.widget.RatingBar",
}

IOS_INPUT_CLASSES = {
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeTextView",
    "XCUIElementTypePicker",
    "XCUIElementTypeDatePicker",
    "XCUIElementTypeSlider",
}


def substitute_keys(data: Dict[str, Any], platform: str) -> Dict[str, Any]:
    """
    Recursively substitute keys in the data dictionary according to reference dictionary.
    Handles special case of bounds_data where the key itself needs to be substituted.
    """
    if not isinstance(data, dict):
        return data

    ref_dict = REFERENCE_DICT.get(platform.lower(), {})
    result = {}

    for key, value in data.items():
        if isinstance(value, dict):
            if key == "bounds_data":
                # Special handling for bounds_data key and its contents
                bb_ref = ref_dict.get("bounds_data", {})
                # Use the base key mapping for bounds_data itself
                new_key = bb_ref.get("bounds_data", "bounds_data")
                # Process the inner bounding box values
                new_value = {}
                for bb_key, bb_value in value.items():
                    new_bb_key = bb_ref.get(bb_key, bb_key)
                    new_value[new_bb_key] = bb_value
                result[new_key] = new_value
            else:
                # Regular nested dictionary
                new_key = ref_dict.get(key, key)
                result[new_key] = substitute_keys(value, platform)
        elif isinstance(value, list):
            # Handle list items
            new_key = ref_dict.get(key, key)
            result[new_key] = [
                substitute_keys(item, platform) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            # Simple key-value pair
            new_key = ref_dict.get(key, key)
            result[new_key] = value

    return result


def generate_legend(platform: str) -> str:
    ref_dict = REFERENCE_DICT.get(platform.lower(), {})
    legend_items = []

    for key, value in ref_dict.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key != sub_value:  # Only include if different
                    legend_items.append(f"{sub_value}: {sub_key}")
        elif key != value:  # Only include if different
            legend_items.append(f"{value}: {key}")

    return "Key legend:\n" + "\n".join(legend_items) + "\nDict >>\n"


def filter_fields(node: Dict[str, Any], platform: str) -> Dict[str, Any]:
    result = {}

    # Get appropriate class set based on platform
    input_classes = (
        ANDROID_INPUT_CLASSES if platform == "android" else IOS_INPUT_CLASSES
    )

    # Check if current node's class is in input classes
    node_class = (
        node.get("class", "") if platform == "android" else node.get("type", "")
    )

    if True or any(cls in str(node_class) for cls in input_classes):
        result = node.copy()

    # Process children if they exist
    if "children" in node:
        input_children = []
        for child in node["children"]:
            child_result = filter_fields(child, platform)
            if child_result:
                input_children.append(child_result)
        if input_children:
            result["children"] = input_children
        elif not result:  # If no input fields found in this branch
            return {}

    return result


@tool(
    agent_names=["mobile_nav_agent"],
    description="Get information about all elements on the current mobile screen.",
    name="read_screen",
)
def read_screen() -> (
    Annotated[Dict[str, str], "Information about elements on current screen"]
):
    """
    Get information about all elements on the current mobile screen.
    """
    try:
        appium_manager = AppiumManager()
        tree = appium_manager.get_accessibility_tree()
        return {"status": "success", "tree": tree}
    except Exception as e:
        logger.error(f"Error reading screen: {str(e)}")
        return {"error": str(e)}
