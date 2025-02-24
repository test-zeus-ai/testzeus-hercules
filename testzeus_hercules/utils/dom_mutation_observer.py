import json
from typing import Callable, List
from playwright.sync_api import Page

DOM_change_callback: List[Callable[[str], None]] = []


def subscribe(callback: Callable[[str], None]) -> None:
    """
    Subscribe to DOM change events.

    Args:
        callback: Function to be called when DOM changes occur.
    """
    DOM_change_callback.append(callback)


def unsubscribe(callback: Callable[[str], None]) -> None:
    """
    Unsubscribe from DOM change events.

    Args:
        callback: Function to be called when DOM changes occur.
    """
    DOM_change_callback.remove(callback)


def dom_mutation_change_detected(changes_detected: str) -> None:
    """
    Detects changes in the DOM (new nodes added) and emits the event to all subscribed callbacks.
    The changes_detected is a string in JSON format containing the tag and content of the new nodes added to the DOM.

    Args:
        changes_detected: JSON string containing DOM changes
    """
    changes = json.loads(changes_detected.replace("\t", "").replace("\n", ""))
    if len(changes) > 0:
        # Emit the event to all subscribed callbacks
        for callback in DOM_change_callback:
            callback(changes)


def setup_dom_mutation_observer(page: Page) -> None:
    """
    Set up a DOM mutation observer using Playwright's sync API.

    Args:
        page: The Playwright page object.
    """
    # JavaScript code to set up mutation observer
    js_code = """() => {
        const callback = function(mutationsList, observer) {
            for(const mutation of mutationsList) {
                if (mutation.type === 'childList' || mutation.type === 'attributes') {
                    window.dom_mutation_change_detected(document.documentElement.outerHTML);
                    break;
                }
            }
        };
        
        const observer = new MutationObserver(callback);
        observer.observe(document.documentElement, { 
            attributes: true, 
            childList: true, 
            subtree: true 
        });
    }"""

    # Expose the callback function to JavaScript
    page.expose_function(
        "dom_mutation_change_detected",
        lambda html: [cb(html) for cb in DOM_change_callback],
    )

    # Execute the JavaScript code
    page.evaluate(js_code)
