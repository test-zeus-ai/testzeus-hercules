import json
from typing import Annotated, Any, Dict, List

from testzeus_hercules.core.appium_manager import AppiumManager
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

@tool(
    agent_names=["navigation_nav_agent"],
    description="""Retrieve all input fields from the current mobile screen""",
    name="get_input_fields"
)
async def get_input_fields() -> Annotated[str, "List of input fields on current screen"]:
    add_event(EventType.INTERACTION, EventData(detail="get_input_fields"))
    logger.info("Getting input fields from mobile screen")

    appium_manager = AppiumManager.get_instance()
    if not appium_manager.driver:
        return "No active Appium session found"

    try:
        tree = await appium_manager.get_accessibility_tree()
        is_android = appium_manager.platformName.lower() == "android"
        
        def extract_input_fields(node: Dict[str, Any], path: str = "") -> List[Dict[str, Any]]:
            fields = []
            
            current_class = node.get("class", "")
            input_classes = ANDROID_INPUT_CLASSES if is_android else IOS_INPUT_CLASSES
            
            is_input = any(cls in current_class for cls in input_classes)
            
            if is_input:
                field = {
                    "type": current_class,
                    "path": path,
                }
                
                # Add platform-specific attributes
                if is_android:
                    field.update({
                        "text": node.get("text", ""),
                        "hint": node.get("content-desc", ""),
                        "resource_id": node.get("resource-id", ""),
                        "editable": "EditText" in current_class or "SearchView" in current_class,
                        "enabled": node.get("enabled", "false"),
                        "focused": node.get("focused", "false"),
                    })
                else:  # iOS
                    field.update({
                        "label": node.get("label", ""),
                        "value": node.get("value", ""),
                        "placeholder": node.get("placeholder", ""),
                        "enabled": node.get("enabled", "false"),
                        "visible": node.get("visible", "false"),
                        "secured": "SecureTextField" in current_class,
                    })
                
                fields.append(field)

            # Recursively process children
            for i, child in enumerate(node.get("children", [])):
                child_path = f"{path}/{i}" if path else str(i)
                fields.extend(extract_input_fields(child, child_path))

            return fields

        # input_fields = extract_input_fields(tree)
        input_fields = tree
        return json.dumps(input_fields, indent=2)

    except Exception as e:
        logger.error(f"Error getting input fields: {e}")
        return f"Error retrieving input fields: {str(e)}"
