import json
import os
import time
from typing import Annotated, Any, Union

from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
)
from testzeus_hercules.utils.logger import logger


@tool(
    agent_names=["browser_nav_agent"],
    description="""# DOM Retrieval Tool, output helps you to read the page content
Fetches DOM based on content type:
1. text_only: Plain text for information retrieval
2. input_fields: JSON list of text input elements with md
3. all_fields: JSON list of all interactive elements with md
Notes:
- Elements ordered as displayed
- Try different content types if information missing
- Consider ordinal/numbered item positions ALL TOOL ARGUMENTS ARE MANDATORY""",
    name="get_dom_with_content_type",
)
async def get_dom_with_content_type(
    content_type: Annotated[str, "Type: text_only/input_fields/all_fields, input_fields for list of inputable fields, text_only for all plain read text, all_fields for all types of fields"],
) -> Annotated[Union[dict[str, Any], str, list, None], "DOM content based on type to analyze and decide"]:
    """
    [previous docstring remains the same]
    """
    add_event(EventType.INTERACTION, EventData(detail="get_dom_with_content_type"))
    logger.info(f"Executing Get DOM Command based on content_type: {content_type}")
    start_time = time.time()
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    await browser_manager.wait_for_page_and_frames_load()
    page = await browser_manager.get_current_page()
    await page.wait_for_load_state()
    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    extracted_data = None
    await wait_for_non_loading_dom_state(page, 1)
    user_success_message = ""
    if content_type == "all_fields":
        user_success_message = "Fetched all the fields in the DOM"
        extracted_data = await do_get_accessibility_info(page, only_input_fields=False)
    elif content_type == "input_fields":
        logger.debug("Fetching DOM for input_fields")
        extracted_data = await do_get_accessibility_info(page, only_input_fields=True)
        if extracted_data is None:
            return "Could not fetch input fields. Please consider trying with content_type all_fields."

        # Flatten the hierarchy into a list of elements
        def flatten_elements(node: dict, parent_name: str = "", parent_title: str = "") -> list[dict]:
            elements = []
            if "children" in node:
                # Get current node's name and title for passing to children
                current_name = node.get("name", parent_name)
                current_title = node.get("title", parent_title)

                for child in node["children"]:
                    # If child doesn't have name/title, it will use parent's values
                    if "name" not in child and current_name:
                        child["name"] = current_name
                    if "title" not in child and current_title:
                        child["title"] = current_title
                    elements.extend(flatten_elements(child, current_name, current_title))
            if "md" in node:
                new_node = node.copy()
                new_node.pop("children", None)
                elements.append(new_node)
            return elements

        extracted_data = flatten_elements(extracted_data)
        user_success_message = "Fetched only input fields in the DOM"
    elif content_type == "text_only":
        logger.debug("Fetching DOM for text_only")
        text_content = await get_filtered_text_content(page)
        with open(
            os.path.join(get_global_conf().get_source_log_folder_path(), "text_only_dom.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(text_content)
        extracted_data = text_content
        user_success_message = "Fetched the text content of the DOM"
    else:
        raise ValueError(f"Unsupported content_type: {content_type}")

    elapsed_time = time.time() - start_time
    logger.info(f"Get DOM Command executed in {elapsed_time} seconds")

    # Count elements
    rr = 0
    if isinstance(extracted_data, (dict, list)):
        rr = len(extracted_data)
    elif extracted_data is not None:
        ed_t_tele = extracted_data.split("\n")
        rr = len([line for line in ed_t_tele if line.strip()])
    add_event(
        EventType.DETECTION,
        EventData(detail=f"DETECTED {rr} components"),
    )

    def rename_children(d: dict) -> dict:
        if "children" in d:
            d["c"] = d.pop("children")
            for child in d["c"]:
                rename_children(child)
        if "tag" in d:
            d["t"] = d.pop("tag")
        if "role" in d:
            d["r"] = d.pop("role")
        if "name" in d:
            d["n"] = d.pop("name")
        if "title" in d:
            d["tl"] = d.pop("title")
        if "md" in d:
            d["md"] = int(d.pop("md"))
        return d

    if not (isinstance(extracted_data, str)):
        if isinstance(extracted_data, list):
            for i, item in enumerate(extracted_data):
                extracted_data[i] = rename_children(item)
        elif isinstance(extracted_data, dict):
            extracted_data = rename_children(extracted_data)

        extracted_data = json.dumps(extracted_data, separators=(",", ":"))
        extracted_data_legend = """Given is the JSON object representing Accessibility Tree DOM of current web page, they keys have following meanings, rest keys have normal meaning:
t: tag
r: role
c: children
n: name
tl: title
JSON >>
"""
        extracted_data = extracted_data_legend + extracted_data
    return extracted_data or "Its Empty, try something else"  # type: ignore


def clean_text(text_content: str) -> str:
    # Split the text into lines
    lines = text_content.splitlines()

    # Create a set to track unique lines
    seen_lines = set()
    cleaned_lines = []

    for line in lines:
        # Strip leading/trailing spaces, replace tabs with spaces, and collapse multiple spaces
        cleaned_line = " ".join(line.strip().replace("\t", " ").split())
        cleaned_line = cleaned_line.strip()

        # Remove repeated words within the line
        # words = cleaned_line.split()
        # unique_words = []
        # seen_words = set()
        # for word in words:
        #     if word not in seen_words:
        #         unique_words.append(word)
        #         seen_words.add(word)
        # cleaned_line = " ".join(unique_words)

        # Skip empty or duplicate lines
        if cleaned_line and cleaned_line not in seen_lines:
            cleaned_lines.append(cleaned_line)
            # seen_lines.add(cleaned_line)

    # Join the cleaned and unique lines with a single newline
    return "\n".join(cleaned_lines)


async def get_filtered_text_content(page: Page) -> str:
    text_content = await page.evaluate(
        """
        () => {
        const selectorsToFilter = ['#hercules-overlay'];
        const originalStyles = [];

        /**
        * Hide elements by setting their visibility to "hidden".
        */
        function hideElements(root, selector) {
            if (!root) return;
            const elements = root.querySelectorAll(selector);
            elements.forEach(element => {
            originalStyles.push({
                element,
                originalStyle: element.style.visibility
            });
            element.style.visibility = 'hidden';
            });
        }

        /**
        * Recursively hide elements in shadow DOM.
        */
        function processElementsInShadowDOM(root, selector) {
            if (!root) return;
            hideElements(root, selector);

            const allNodes = root.querySelectorAll('*');
            allNodes.forEach(node => {
            if (node.shadowRoot) {
                processElementsInShadowDOM(node.shadowRoot, selector);
            }
            });
        }

        /**
        * Recursively hide elements in iframes.
        */
        function processElementsInIframes(root, selector) {
            if (!root) return;
            const iframes = root.querySelectorAll('iframe');
            iframes.forEach(iframe => {
            try {
                const iframeDoc = iframe.contentDocument;
                if (iframeDoc) {
                processElementsInShadowDOM(iframeDoc, selector);
                processElementsInIframes(iframeDoc, selector);
                }
            } catch (err) {
                console.log('Error accessing iframe content:', err);
            }
            });
        }

        /**
        * Create a TreeWalker that:
        * - Visits text nodes and element nodes
        * - Skips (<script> and <style>) elements entirely
        */
        function createSkippingTreeWalker(root) {
            return document.createTreeWalker(
            root,
            NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
            {
                acceptNode(node) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    const tag = node.tagName.toLowerCase();
                    if (tag === 'script' || tag === 'style') {
                    return NodeFilter.FILTER_REJECT; // skip <script> / <style>
                    }
                }
                return NodeFilter.FILTER_ACCEPT;
                }
            }
            );
        }

        /**
        * Gets text by walking the DOM, but skipping <script> and <style>.
        * Also recursively checks for shadow roots and iframes.
        */
        function getTextSkippingScriptsStyles(root) {
            if (!root) return '';

            let textContent = '';
            const walker = createSkippingTreeWalker(root);

            while (walker.nextNode()) {
            const node = walker.currentNode;
            
            // If it’s a text node, accumulate text
            if (node.nodeType === Node.TEXT_NODE) {
                textContent += node.nodeValue;
            }
            // If it has a shadowRoot, recurse
            else if (node.shadowRoot) {
                textContent += getTextSkippingScriptsStyles(node.shadowRoot);
            }
            }

            return textContent;
        }

        /**
        * Recursively gather text from iframes, also skipping <script> & <style>.
        */
        function getTextFromIframes(root) {
            if (!root) return '';
            let iframeText = '';

            const iframes = root.querySelectorAll('iframe');
            iframes.forEach(iframe => {
            try {
                const iframeDoc = iframe.contentDocument;
                if (iframeDoc) {
                // Grab text from iframe body, docElement, plus nested iframes
                iframeText += getTextSkippingScriptsStyles(iframeDoc.body);
                iframeText += getTextSkippingScriptsStyles(iframeDoc.documentElement);
                iframeText += getTextFromIframes(iframeDoc);
                }
            } catch (err) {
                console.log('Error accessing iframe content:', err);
            }
            });

            return iframeText;
        }

        /**
        * Collect alt texts for images (this part can remain simpler, as alt text
        * won't appear in <script> or <style> tags anyway).
        */
        function getAltTextsFromShadowDOM(root) {
            if (!root) return [];
            let altTexts = Array.from(root.querySelectorAll('img')).map(img => img.alt);

            const allNodes = root.querySelectorAll('*');
            allNodes.forEach(node => {
            if (node.shadowRoot) {
                altTexts = altTexts.concat(getAltTextsFromShadowDOM(node.shadowRoot));
            }
            });
            return altTexts;
        }

        function getAltTextsFromIframes(root) {
            if (!root) return [];
            let iframeAltTexts = [];

            const iframes = root.querySelectorAll('iframe');
            iframes.forEach(iframe => {
            try {
                const iframeDoc = iframe.contentDocument;
                if (iframeDoc) {
                iframeAltTexts = iframeAltTexts.concat(getAltTextsFromShadowDOM(iframeDoc));
                iframeAltTexts = iframeAltTexts.concat(getAltTextsFromIframes(iframeDoc));
                }
            } catch (err) {
                console.log('Error accessing iframe content:', err);
            }
            });

            return iframeAltTexts;
        }

        // 1) Hide overlays
        selectorsToFilter.forEach(selector => {
            processElementsInShadowDOM(document, selector);
            processElementsInIframes(document, selector);
        });

        // 2) Collect text from the main document
        let textContent = getTextSkippingScriptsStyles(document.body);
        textContent += getTextSkippingScriptsStyles(document.documentElement);

        // 3) Collect text from iframes
        textContent += getTextFromIframes(document);

        // 4) Collect alt texts
        let altTexts = getAltTextsFromShadowDOM(document);
        altTexts = altTexts.concat(getAltTextsFromIframes(document));
        const altTextsString = 'Other Alt Texts in the page: ' + altTexts.join(' ');

        // 5) Restore hidden overlays
        originalStyles.forEach(entry => {
            entry.element.style.visibility = entry.originalStyle;
        });

        // 6) Return final text
        textContent = textContent + ' ' + altTextsString;

        // Optional: sanitize whitespace, if needed
        // const sanitizeString = (input) => input.replace(/\s+/g, ' ');
        // textContent = sanitizeString(textContent);

        return textContent;
        }
    """
    )
    return clean_text(text_content)
