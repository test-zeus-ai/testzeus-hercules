import json
import os
import re
import traceback
from typing import Annotated, Any, Dict, List, Optional, Union

from playwright.sync_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger

space_delimited_md = re.compile(r"^[\d ]+$")


def rename_children(d: Dict[str, Any]) -> Dict[str, Any]:
    """Rename 'children' key to 'childNodes' recursively."""
    if "children" in d:
        d["childNodes"] = d.pop("children")
        for child in d["childNodes"]:
            rename_children(child)
    return d


def is_space_delimited_md(s: str) -> bool:
    """
    Check if the given string matches the the md pattern of number space repeated.

    Parameters:
    - s (str): The string to check against the pattern.

    Returns:
    - bool: True if the string matches the pattern, False otherwise.
    """
    # Use fullmatch() to ensure the entire string matches the pattern
    return bool(space_delimited_md.fullmatch(s))


def __inject_attributes(page: Page) -> None:
    """Inject md attributes into the DOM."""
    try:
        last_md = page.evaluate(
            """() => {
            let lastMd = 0;
            function injectMdAttribute(element) {
                if (!element.hasAttribute('md')) {
                    element.setAttribute('md', ++lastMd);
                }
                return lastMd;
            }
            
            function processNode(node) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    injectMdAttribute(node);
                    for (const child of node.children) {
                        processNode(child);
                    }
                }
            }
            
            processNode(document.documentElement);
            return lastMd;
        }"""
        )
        logger.debug(f"Injected md attributes up to {last_md}")
    except Exception as e:
        logger.error(f"Error injecting md attributes: {e}")


def __fetch_dom_info(
    page: Page, accessibility_tree: Dict[str, Any], only_input_fields: bool
) -> Dict[str, Any]:
    """
    Fetch DOM information and enhance the accessibility tree.
    """

    def process_node(node: Dict[str, Any]) -> None:
        """Process a single node in the accessibility tree."""
        try:
            if "md" in node:
                md = str(node["md"])
                element_attributes = page.evaluate(
                    """(md) => {
                    const element = document.querySelector(`[md="${md}"]`);
                    if (!element) return null;
                    
                    const computedStyle = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    
                    return {
                        tag: element.tagName.toLowerCase(),
                        id: element.id,
                        class: element.className,
                        type: element.type,
                        value: element.value,
                        checked: element.checked,
                        selected: element.selected,
                        disabled: element.disabled,
                        readOnly: element.readOnly,
                        required: element.required,
                        placeholder: element.placeholder,
                        href: element.href,
                        src: element.src,
                        alt: element.alt,
                        title: element.title,
                        ariaLabel: element.getAttribute('aria-label'),
                        ariaDescribedby: element.getAttribute('aria-describedby'),
                        ariaExpanded: element.getAttribute('aria-expanded'),
                        ariaHidden: element.getAttribute('aria-hidden'),
                        ariaDisabled: element.getAttribute('aria-disabled'),
                        role: element.getAttribute('role'),
                        tabIndex: element.tabIndex,
                        style: {
                            display: computedStyle.display,
                            visibility: computedStyle.visibility,
                            position: computedStyle.position,
                            zIndex: computedStyle.zIndex,
                            opacity: computedStyle.opacity,
                            backgroundColor: computedStyle.backgroundColor,
                            color: computedStyle.color,
                            fontSize: computedStyle.fontSize,
                            fontWeight: computedStyle.fontWeight,
                            textAlign: computedStyle.textAlign,
                            width: computedStyle.width,
                            height: computedStyle.height,
                            padding: computedStyle.padding,
                            margin: computedStyle.margin,
                            border: computedStyle.border,
                            borderRadius: computedStyle.borderRadius,
                            boxShadow: computedStyle.boxShadow,
                            cursor: computedStyle.cursor,
                            transform: computedStyle.transform,
                            transition: computedStyle.transition,
                            animation: computedStyle.animation,
                            filter: computedStyle.filter,
                            backdropFilter: computedStyle.backdropFilter,
                            clipPath: computedStyle.clipPath,
                            mask: computedStyle.mask,
                            perspective: computedStyle.perspective,
                            transformStyle: computedStyle.transformStyle,
                            transformOrigin: computedStyle.transformOrigin,
                            backfaceVisibility: computedStyle.backfaceVisibility,
                            willChange: computedStyle.willChange,
                            content: computedStyle.content,
                            pointerEvents: computedStyle.pointerEvents,
                            userSelect: computedStyle.userSelect,
                            touchAction: computedStyle.touchAction,
                            scrollBehavior: computedStyle.scrollBehavior,
                            scrollSnapType: computedStyle.scrollSnapType,
                            scrollSnapAlign: computedStyle.scrollSnapAlign,
                            scrollSnapStop: computedStyle.scrollSnapStop,
                            overscrollBehavior: computedStyle.overscrollBehavior,
                            isolation: computedStyle.isolation,
                            mixBlendMode: computedStyle.mixBlendMode,
                            backgroundBlendMode: computedStyle.backgroundBlendMode,
                        },
                        rect: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            top: rect.top,
                            right: rect.right,
                            bottom: rect.bottom,
                            left: rect.left,
                        }
                    };
                }""",
                    md,
                )

                if element_attributes:
                    node.update(element_attributes)

            if "childNodes" in node:
                for child in node["childNodes"]:
                    process_node(child)

        except Exception as e:
            logger.error(f"Error processing node: {e}")

    try:
        process_node(accessibility_tree)
        return accessibility_tree
    except Exception as e:
        logger.error(f"Error fetching DOM info: {e}")
        return accessibility_tree


def __cleanup_dom(page: Page) -> None:
    """Remove injected attributes from the DOM."""
    try:
        page.evaluate(
            """() => {
            function removeInjectedAttributes(element) {
                element.removeAttribute('md');
                for (const child of element.children) {
                    removeInjectedAttributes(child);
                }
            }
            removeInjectedAttributes(document.documentElement);
        }"""
        )
    except Exception as e:
        logger.error(f"Error cleaning up DOM: {e}")


def get_node_dom_element(page: Page, md: str) -> Any:
    """Get DOM element by md attribute."""
    return page.evaluate(
        f"""() => {{
        const element = document.querySelector('[md="{md}"]');
        if (!element) return null;
        return element;
    }}"""
    )


def get_element_attributes(
    page: Page, md: str, attributes: List[str]
) -> Dict[str, Any]:
    """Get specific attributes of a DOM element."""
    return page.evaluate(
        f"""(attributes) => {{
        const element = document.querySelector('[md="{md}"]');
        if (!element) return null;
        
        const result = {{}};
        for (const attr of attributes) {{
            result[attr] = element[attr] || element.getAttribute(attr);
        }}
        return result;
    }}""",
        attributes,
    )


def do_get_accessibility_info(
    page: Page, only_input_fields: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Get detailed accessibility information from the page.
    """
    try:
        # Inject md attributes
        __inject_attributes(page)

        # Get accessibility tree
        js_code = """() => {
            function getAccessibilityInfo(element, index) {
                const info = {
                    md: element.getAttribute('md'),
                    tag: element.tagName.toLowerCase(),
                    type: element.type || '',
                    value: element.value || '',
                    placeholder: element.placeholder || '',
                    'aria-label': element.getAttribute('aria-label') || '',
                    role: element.getAttribute('role') || '',
                    name: element.name || '',
                    id: element.id || '',
                    class: element.className || '',
                    disabled: element.disabled || false,
                    required: element.required || false,
                    readOnly: element.readOnly || false,
                    maxLength: element.maxLength || '',
                    pattern: element.pattern || '',
                    autocomplete: element.autocomplete || '',
                    checked: element.checked || false,
                    selected: element.selected || false,
                    multiple: element.multiple || false,
                    size: element.size || '',
                    step: element.step || '',
                    min: element.min || '',
                    max: element.max || '',
                    'aria-required': element.getAttribute('aria-required') || '',
                    'aria-invalid': element.getAttribute('aria-invalid') || '',
                    'aria-expanded': element.getAttribute('aria-expanded') || '',
                    'aria-haspopup': element.getAttribute('aria-haspopup') || '',
                    'aria-controls': element.getAttribute('aria-controls') || '',
                    'aria-owns': element.getAttribute('aria-owns') || '',
                    'aria-describedby': element.getAttribute('aria-describedby') || '',
                    'data-testid': element.getAttribute('data-testid') || '',
                };

                // Add computed styles
                const computedStyle = window.getComputedStyle(element);
                info.styles = {
                    display: computedStyle.display,
                    visibility: computedStyle.visibility,
                    position: computedStyle.position,
                    width: computedStyle.width,
                    height: computedStyle.height,
                    backgroundColor: computedStyle.backgroundColor,
                    color: computedStyle.color,
                    fontSize: computedStyle.fontSize,
                    fontFamily: computedStyle.fontFamily,
                    border: computedStyle.border,
                    padding: computedStyle.padding,
                    margin: computedStyle.margin,
                    opacity: computedStyle.opacity,
                };

                return info;
            }

            const elements = document.querySelectorAll('*');
            const result = {};
            elements.forEach((element, index) => {
                result[index] = getAccessibilityInfo(element, index);
            });

            return result;
        }"""

        accessibility_tree = page.evaluate(js_code)

        # Save raw tree for debugging if needed
        if get_global_conf().get_debug_mode():
            debug_dir = os.path.join(get_global_conf().get_proof_path(), "debug")
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, "raw_tree.json"), "w") as f:
                json.dump(accessibility_tree, f, indent=2)

        # Clean up injected attributes
        __cleanup_dom(page)

        # Enhance tree with additional DOM info
        enhanced_tree = __fetch_dom_info(page, accessibility_tree, only_input_fields)

        # Save enhanced tree for debugging if needed
        if get_global_conf().get_debug_mode():
            with open(os.path.join(debug_dir, "enhanced_tree.json"), "w") as f:
                json.dump(enhanced_tree, f, indent=2)

        return enhanced_tree

    except Exception as e:
        logger.error(f"Error getting accessibility info: {e}")
        return None


def get_dom_with_accessibility_info() -> Dict[str, Any]:
    """
    Get accessibility information from the current page.
    """
    try:
        browser_manager = PlaywrightManager()
        page = browser_manager.get_current_page()
        if not page:
            logger.error("No active page found")
            return {"error": "No active page found"}

        return do_get_accessibility_info(page)
    except Exception as e:
        logger.error(f"Error getting DOM accessibility info: {e}")
        return {"error": str(e)}
