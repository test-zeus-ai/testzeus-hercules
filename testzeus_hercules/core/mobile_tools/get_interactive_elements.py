import json
from typing import Annotated, Any, Dict, List

from testzeus_hercules.core.appium_manager import AppiumManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

ANDROID_INTERACTIVE_CLASSES = {
    "android.widget.Button",
    "android.widget.ImageButton",
    "android.widget.EditText",
    "android.widget.CheckBox",
    "android.widget.RadioButton",
    "android.widget.Switch",
    "android.widget.Spinner",
    "android.widget.TextView",
    "android.widget.ImageView",
    "android.widget.ListView",
    "android.widget.ScrollView",
    "android.widget.SeekBar",
}

IOS_INTERACTIVE_CLASSES = {
    "XCUIElementTypeButton",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSwitch",
    "XCUIElementTypeSlider",
    "XCUIElementTypePicker",
    "XCUIElementTypeLink",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeScrollView",
    "XCUIElementTypeTable",
    "XCUIElementTypeCollectionView",
}

@tool(
    agent_names=["navigation_nav_agent"],
    description="""Retrieve all interactive elements from the current mobile screen""",
    name="get_interactive_elements",
)
async def get_interactive_elements() -> Annotated[str, "List of interactive elements on current screen"]:
    add_event(EventType.INTERACTION, EventData(detail="get_interactive_elements"))
    logger.info("Getting interactive elements from mobile screen")

    appium_manager = AppiumManager.get_instance()
    if not appium_manager.driver:
        return "No active Appium session found"

    try:
        tree = await appium_manager.get_accessibility_tree()
        is_android = appium_manager.platformName.lower() == "android"
        
        def extract_interactive_elements(node: Dict[str, Any], path: str = "") -> List[Dict[str, Any]]:
            elements = []
            
            current_class = node.get("class", "")
            interactive_classes = ANDROID_INTERACTIVE_CLASSES if is_android else IOS_INTERACTIVE_CLASSES
            
            is_interactive = (
                any(cls in current_class for cls in interactive_classes) or
                node.get("clickable") == "true" or
                node.get("enabled") == "true"
            )
            
            if is_interactive:
                element = {
                    "type": current_class,
                    "path": path,
                }
                
                # Add platform-specific attributes
                if is_android:
                    element.update({
                        "text": node.get("text", ""),
                        "description": node.get("content-desc", ""),
                        "resource_id": node.get("resource-id", ""),
                        "clickable": node.get("clickable", "false"),
                        "enabled": node.get("enabled", "false"),
                    })
                else:  # iOS
                    element.update({
                        "label": node.get("label", ""),
                        "value": node.get("value", ""),
                        "name": node.get("name", ""),
                        "enabled": node.get("enabled", "false"),
                        "visible": node.get("visible", "false"),
                    })
                
                elements.append(element)

            # Recursively process children
            for i, child in enumerate(node.get("children", [])):
                child_path = f"{path}/{i}" if path else str(i)
                elements.extend(extract_interactive_elements(child, child_path))

            return elements

        # elements = extract_interactive_elements(tree)
        elements = tree
        return json.dumps(elements, indent=2)

    except Exception as e:
        logger.error(f"Error getting interactive elements: {e}")
        return f"Error retrieving interactive elements: {str(e)}"
