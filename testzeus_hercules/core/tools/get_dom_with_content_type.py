import os
import time
from typing import Annotated, Any

from playwright.async_api import Page
from testzeus_hercules.config import get_source_log_folder_path
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.dom_helper import wait_for_non_loading_dom_state
from testzeus_hercules.utils.get_detailed_accessibility_tree import (
    do_get_accessibility_info,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


async def get_dom_with_content_type(
    content_type: Annotated[
        str,
        "The type of content to extract: 'text_only': Extracts the innerText of the highest element in the document and responds with text, or 'input_fields': Extracts the text input and button elements in the dom.",
    ]
) -> Annotated[dict[str, Any] | str | None, "The output based on the specified content type."]:
    """
    Retrieves and processes the DOM of the active page in a browser instance based on the specified content type.

    Parameters
    ----------
    content_type : str
        The type of content to extract. Possible values are:
        - 'text_only': Extracts the innerText of the highest element in the document and responds with text.
        - 'input_fields': Extracts the text input and button elements in the DOM and responds with a JSON object.
        - 'all_fields': Extracts all the fields in the DOM and responds with a JSON object.

    Returns
    -------
    dict[str, Any] | str | None
        The processed content based on the specified content type. This could be:
        - A JSON object for 'input_fields' with just inputs.
        - Plain text for 'text_only'.
        - A minified DOM represented as a JSON object for 'all_fields'.

    Raises
    ------
    ValueError
        If an unsupported content_type is provided.
    """
    add_event(EventType.INTERACTION, EventData(detail="get_dom_with_content_type"))
    logger.info(f"Executing Get DOM Command based on content_type: {content_type}")
    start_time = time.time()
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    extracted_data = None
    await wait_for_non_loading_dom_state(page, 5000)  # wait for the DOM to be ready, non loading means external resources do not need to be loaded
    user_success_message = ""
    if content_type == "all_fields":
        user_success_message = "Fetched all the fields in the DOM"
        extracted_data = await do_get_accessibility_info(page, only_input_fields=False)
    elif content_type == "input_fields":
        logger.debug("Fetching DOM for input_fields")
        extracted_data = await do_get_accessibility_info(page, only_input_fields=True)
        if extracted_data is None:
            return "Could not fetch input fields. Please consider trying with content_type all_fields."
        user_success_message = "Fetched only input fields in the DOM"
    elif content_type == "text_only":
        # Extract text from the body or the highest-level element
        logger.debug("Fetching DOM for text_only")
        text_content = await get_filtered_text_content(page)
        with open(
            os.path.join(get_source_log_folder_path(), "text_only_dom.txt"),
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
    await browser_manager.notify_user(user_success_message, message_type=MessageType.ACTION)

    # split extracted data into multiple lines and drop empty stripped lines and then get a count of lines.
    rr = 0
    if isinstance(extracted_data, dict):
        rr = len(extracted_data)
    else:
        ed_t_tele = extracted_data.split("\n")
        rr = len([line for line in ed_t_tele if line.strip()])
    add_event(
        EventType.DETECTION,
        EventData(detail=f"DETECTED {rr} components"),
    )

    return extracted_data  # type: ignore


async def get_filtered_text_content(page: Page) -> str:
    text_content = await page.evaluate(
        """
        () => {
            const selectorsToFilter = ['#hercules-overlay'];
            const originalStyles = [];
            function hideElements(root, selector) {
                if (!root) return;
                const elements = root.querySelectorAll(selector);
                elements.forEach(element => {
                    originalStyles.push({
                        element: element,
                        originalStyle: element.style.visibility
                    });
                    element.style.visibility = 'hidden';
                });
            }
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
            function processElementsInIframes(root, selector) {
                if (!root) return;
                const iframes = root.querySelectorAll('iframe');
                iframes.forEach(iframe => {
                    try {
                        const iframeDoc = iframe.contentDocument;
                        if (iframeDoc) {
                            // Hide elements inside the iframe
                            processElementsInShadowDOM(iframeDoc, selector);
                            // Also recurse into nested iframes, if any
                            processElementsInIframes(iframeDoc, selector);
                        }
                    } catch (err) {
                        console.log('Error accessing iframe content:', err);
                    }
                });
            }
            function getTextContentFromShadowDOM(root) {
                if (!root) return '';
                let textContent = root.innerText || '';
                const allNodes = root.querySelectorAll('*');
                allNodes.forEach(node => {
                    if (node.shadowRoot) {
                        textContent += getTextContentFromShadowDOM(node.shadowRoot);
                    }
                });
                return textContent;
            }
            function getTextContentFromIframes(root) {
                if (!root) return '';
                let iframeText = '';
                const iframes = root.querySelectorAll('iframe');
                iframes.forEach(iframe => {
                    try {
                        const iframeDoc = iframe.contentDocument;
                        if (iframeDoc) {
                            iframeText += getTextContentFromShadowDOM(iframeDoc.body);
                            iframeText += getTextContentFromShadowDOM(iframeDoc.documentElement);
                            iframeText += getTextContentFromIframes(iframeDoc);
                        }
                    } catch (err) {
                        console.log('Error accessing iframe content:', err);
                    }
                });
                return iframeText;
            }
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
            selectorsToFilter.forEach(selector => {
                // Hide in the main document
                processElementsInShadowDOM(document, selector);
                // Hide inside iframes
                processElementsInIframes(document, selector);
            });

            let textContent = '';
            textContent += getTextContentFromShadowDOM(document.body);
            textContent += getTextContentFromShadowDOM(document.documentElement);

            textContent += getTextContentFromIframes(document);
            let altTexts = getAltTextsFromShadowDOM(document);
            altTexts = altTexts.concat(getAltTextsFromIframes(document));
            let altTextsString = 'Other Alt Texts in the page: ' + altTexts.join(' ');

            originalStyles.forEach(entry => {
                entry.element.style.visibility = entry.originalStyle;
            });

            textContent = textContent + ' ' + altTextsString;
            return textContent;
        }
    """
    )
    return text_content
