import asyncio
import json
from typing import Callable  # noqa: UP035

from playwright.async_api import Page

# Create an event loop
loop = asyncio.get_event_loop()

DOM_change_callback: list[Callable[[str], None]] = []


def subscribe(callback: Callable[[str], None]) -> None:
    DOM_change_callback.append(callback)


def unsubscribe(callback: Callable[[str], None]) -> None:
    DOM_change_callback.remove(callback)


async def add_mutation_observer(page: Page):
    """
    Adds a mutation observer to the page to detect changes in the DOM.
    When changes are detected, the observer calls the dom_mutation_change_detected function in the browser context.
    This changes can be detected by subscribing to the dom_mutation_change_detected function by individual skills.

    Current implementation only detects when a new node is added to the DOM.
    However, in many cases, the change could be a change in the style or class of an existing node (e.g. toggle visibility of a hidden node).
    """

    await page.evaluate(
        """
            console.log('Adding a mutation observer for DOM changes');

            const observeMutations = (root) => {
                new MutationObserver((mutationsList, observer) => {
                    let changes_detected = [];
                    for (let mutation of mutationsList) {
                        if (mutation.type === 'childList') {
                            let allAddedNodes = mutation.addedNodes;
                            for (let node of allAddedNodes) {
                                if (node.tagName && !['SCRIPT', 'NOSCRIPT', 'STYLE'].includes(node.tagName.toUpperCase()) && !node.closest('#agentDriveAutoOverlay')) {
                                    let visibility = true;
                                    let content = node.innerText ? node.innerText.trim() : '';
                                    if (visibility && content) {
                                        changes_detected.push({ tag: node.tagName, content: content });
                                    }
                                    // If the added node has a shadow DOM, observe it as well
                                    if (node.shadowRoot) {
                                        observeMutations(node.shadowRoot);
                                    }
                                    // If the added node is an iframe, and same-origin, observe its document
                                    if (node.tagName.toLowerCase() === 'iframe') {
                                        let iframeDocument;
                                        try {
                                            iframeDocument = node.contentDocument || node.contentWindow.document;
                                        } catch (e) {
                                            // Cannot access cross-origin iframe; skip to the next node
                                            continue;
                                        }
                                        if (iframeDocument) {
                                            observeMutations(iframeDocument);
                                        }
                                    }
                                }
                            }
                        } else if (mutation.type === 'characterData') {
                            let node = mutation.target;
                            if (
                                node.parentNode &&
                                node.parentNode.tagName &&
                                !['SCRIPT', 'NOSCRIPT', 'STYLE'].includes(node.parentNode.tagName.toUpperCase()) &&
                                !node.parentNode.closest('#agentDriveAutoOverlay')
                            ) {
                                let visibility = true;
                                let content = node.data.trim();
                                if (visibility && content && window.getComputedStyle(node.parentNode).display !== 'none') {
                                    if (!changes_detected.some(change => change.content.includes(content))) {
                                        changes_detected.push({ tag: node.parentNode.tagName, content: content });
                                    }
                                }
                            }
                        }
                    }
                    if (changes_detected.length > 0) {
                        window.dom_mutation_change_detected(JSON.stringify(changes_detected));
                    }
                }).observe(root, { subtree: true, childList: true, characterData: true });
            };

            // Start observing the regular document (DOM)
            observeMutations(document);

            // Optionally, if there are known shadow roots or iframes, start observing them immediately
            document.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot) {
                    observeMutations(el.shadowRoot);
                }
                if (el.tagName && el.tagName.toLowerCase() === 'iframe') {
                    let iframeDocument;
                    try {
                        iframeDocument = el.contentDocument || el.contentWindow.document;
                    } catch (e) {
                        // Cannot access cross-origin iframe; skip to the next element
                        return;
                    }
                    if (iframeDocument) {
                        observeMutations(iframeDocument);
                    }
                }
            });

        """
    )


async def handle_navigation_for_mutation_observer(page: Page):
    await add_mutation_observer(page)


async def dom_mutation_change_detected(changes_detected: str):
    """
    Detects changes in the DOM (new nodes added) and emits the event to all subscribed callbacks.
    The changes_detected is a string in JSON formatt containing the tag and content of the new nodes added to the DOM.

    e.g.  The following will be detected when autocomplete recommendations show up when one types Nelson Mandela on google search
    [{'tag': 'SPAN', 'content': 'nelson mandela wikipedia'}, {'tag': 'SPAN', 'content': 'nelson mandela movies'}]
    """
    changes_detected = json.loads(changes_detected.replace("\t", "").replace("\n", ""))
    if len(changes_detected) > 0:
        # Emit the event to all subscribed callbacks
        for callback in DOM_change_callback:
            # If the callback is a coroutine function
            if asyncio.iscoroutinefunction(callback):
                await callback(changes_detected)
            # If the callback is a regular function
            else:
                callback(changes_detected)
