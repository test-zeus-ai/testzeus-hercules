import json
import os
import re
import traceback
from typing import Annotated, Any

import aiofiles
from playwright.async_api import Page
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.logger import logger

space_delimited_md = re.compile(r"^[\d ]+$")


async def rename_children(d: dict) -> dict:
    if "children" in d:
        d["c"] = d.pop("children")
        for child in d["c"]:
            await rename_children(child)
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


async def __inject_attributes(page: Page) -> None:
    """
    Injects 'md' and 'aria-keyshortcuts' into all DOM elements. If an element already has an 'aria-keyshortcuts',
    it renames it to 'orig-aria-keyshortcuts' before injecting the new 'aria-keyshortcuts'
    This will be captured in the accessibility tree and thus make it easier to reconcile the tree with the DOM.
    'aria-keyshortcuts' is choosen because it is not widely used aria attribute.
    """

    last_md = await page.evaluate(
        """() => {
            // A recursive function to handle elements in DOM, shadow DOM, and iframes
            const processElements = (elements, idCounter) => {
                elements.forEach(element => {
                    // If the element has a shadowRoot, process its children too
                    if (element.shadowRoot) {
                        idCounter = processElements(element.shadowRoot.querySelectorAll('*'), idCounter);
                    }

                    // If the element is an iframe, process its contentDocument if accessible
                    if (element.tagName.toLowerCase() === 'iframe') {
                        let iframeDocument;
                        try {
                            // Access the iframe's document if it's same-origin
                            iframeDocument = element.contentDocument || element.contentWindow.document;
                        } catch (e) {
                            // Cannot access cross-origin iframe; skip to the next element
                            return;
                        }
                        if (iframeDocument) {
                            const iframeElements = iframeDocument.querySelectorAll('*');
                            idCounter = processElements(iframeElements, idCounter);
                        }
                    }

                    // Check if the element is interactive (buttons, inputs, etc.)
                    if (isInteractiveElement(element)) {
                        const origAriaAttribute = element.getAttribute('aria-keyshortcuts');
                        const md = `${++idCounter}`;
                        element.setAttribute('md', md);
                        element.setAttribute('aria-keyshortcuts', md);

                        // Preserve the original aria-keyshortcuts if it exists
                        if (origAriaAttribute) {
                            element.setAttribute('orig-aria-keyshortcuts', origAriaAttribute);
                        }
                    }
                });
                return idCounter;
            };
            function isInteractiveElement(element) {
                // Immediately return false for body tag
                if (element.tagName.toLowerCase() === 'body') {
                    return false;
                }

                // Base interactive elements and roles
                const interactiveElements = new Set([
                    'a', 'button', 'details', 'embed', 'input', 'label',
                    'menu', 'menuitem', 'object', 'select', 'textarea', 'summary'
                ]);

                const interactiveRoles = new Set([
                    'button', 'menu', 'menuitem', 'link', 'checkbox', 'radio',
                    'slider', 'tab', 'tabpanel', 'textbox', 'combobox', 'grid',
                    'listbox', 'option', 'progressbar', 'scrollbar', 'searchbox',
                    'switch', 'tree', 'treeitem', 'spinbutton', 'tooltip', 'a-button-inner', 'a-dropdown-button', 'click', 
                    'menuitemcheckbox', 'menuitemradio', 'a-button-text', 'button-text', 'button-icon', 'button-icon-only', 'button-text-icon-only', 'dropdown', 'combobox'
                ]);

                const tagName = element.tagName.toLowerCase();
                const role = element.getAttribute('role');
                const ariaRole = element.getAttribute('aria-role');
                const tabIndex = element.getAttribute('tabindex');

                // Add check for specific class
                const hasAddressInputClass = element.classList.contains('address-input__container__input');

                // Basic role/attribute checks
                const hasInteractiveRole = hasAddressInputClass ||
                    interactiveElements.has(tagName) ||
                    interactiveRoles.has(role) ||
                    interactiveRoles.has(ariaRole) ||
                    (tabIndex !== null && tabIndex !== '-1' && element.parentElement?.tagName.toLowerCase() !== 'body') ||
                    element.getAttribute('data-action') === 'a-dropdown-select' ||
                    element.getAttribute('data-action') === 'a-dropdown-button';

                if (hasInteractiveRole) return true;

                // Get computed style
                const style = window.getComputedStyle(element);

                if (
                    style.cursor === 'pointer' || 
                    style.cursor === 'hand' ||
                    style.cursor === 'move' ||
                    style.cursor === 'grab' ||
                    style.cursor === 'grabbing'
                ) {
                    return true;
                }

                // Check for event listeners
                const hasClickHandler = element.onclick !== null ||
                    element.getAttribute('onclick') !== null ||
                    element.hasAttribute('ng-click') ||
                    element.hasAttribute('@click') ||
                    element.hasAttribute('v-on:click');

                // Helper function to safely get event listeners
                function getEventListeners(el) {
                    try {
                        // Try to get listeners using Chrome DevTools API
                        return window.getEventListeners?.(el) || {};
                    } catch (e) {
                        // Fallback: check for common event properties
                        const listeners = {};

                        // List of common event types to check
                        const eventTypes = [
                            'click', 'mousedown', 'mouseup',
                            'touchstart', 'touchend',
                            'keydown', 'keyup', 'focus', 'blur'
                        ];

                        for (const type of eventTypes) {
                            const handler = el[`on${type}`];
                            if (handler) {
                                listeners[type] = [{
                                    listener: handler,
                                    useCapture: false
                                }];
                            }
                        }

                        return listeners;
                    }
                }

                // Check for click-related events on the element itself
                const listeners = getEventListeners(element);
                const hasClickListeners = listeners && (
                    listeners.click?.length > 0 ||
                    listeners.mousedown?.length > 0 ||
                    listeners.mouseup?.length > 0 ||
                    listeners.touchstart?.length > 0 ||
                    listeners.touchend?.length > 0
                );

                // Check for ARIA properties that suggest interactivity
                const hasAriaProps = element.hasAttribute('aria-expanded') ||
                    element.hasAttribute('aria-pressed') ||
                    element.hasAttribute('aria-selected') ||
                    element.hasAttribute('aria-checked');

                // Check for form-related functionality
                const isFormRelated = element.form !== undefined ||
                    element.hasAttribute('contenteditable') ||
                    style.userSelect !== 'none';

                // Check if element is draggable
                const isDraggable = element.draggable ||
                    element.getAttribute('draggable') === 'true';

                // Additional check to prevent body from being marked as interactive
                if (element.tagName.toLowerCase() === 'body' || element.parentElement?.tagName.toLowerCase() === 'body') {
                    return false;
                }

                return hasAriaProps ||
                    // hasClickStyling ||
                    hasClickHandler ||
                    hasClickListeners ||
                    // isFormRelated ||
                    isDraggable;
            }

            // Helper function to determine if an element is interactive
            // const isInteractiveElement = (element) => {
            //     const interactiveTags = ['button', 'a', 'input', 'select', 'textarea'];
            //     return interactiveTags.includes(element.tagName.toLowerCase()) || element.hasAttribute('tabindex');
            // };

            // Start processing the DOM
            const allElements = document.querySelectorAll('*');
            let id = processElements(allElements, 0);

            return id;
        };
        """
    )
    logger.debug(f"Added MD into {last_md} elements")


async def __fetch_dom_info(page: Page, accessibility_tree: dict[str, Any], only_input_fields: bool) -> dict[str, Any]:
    """
    Iterates over the accessibility tree, fetching additional information from the DOM based on 'md',
    and constructs a new JSON structure with detailed information.

    Args:
        page (Page): The page object representing the web page.
        accessibility_tree (dict[str, Any]): The accessibility tree JSON structure.
        only_input_fields (bool): Flag indicating whether to include only input fields in the new JSON structure.

    Returns:
        dict[str, Any]: The pruned tree with detailed information from the DOM.
    """

    logger.debug("Reconciling the Accessibility Tree with the DOM")
    # Define the attributes to fetch for each element
    attributes = [
        "name",
        "aria-label",
        "placeholder",
        "md",
        "id",
        "for",
        "data-testid",
        "title",
        "aria-controls",
        "aria-describedby",
        "class",
        "ng-click",
        "ng-mouseover",
        "ng-mouseenter",
        "ng-mouseleave",
        "ng-dblclick",
        "ng-keydown",
        "ng-keyup",
        "ng-keypress",
        "ng-focus",
        "ng-blur",
        "ng-change",
        "ng-submit",
        "ng-repeat",
        "ng-if",
        "ng-show",
        "ng-hide",
        "ng-model",
        "ng-bind",
        "ng-class",
        "ng-style",
        "data-toggle",
        "data-target",
        "data-bs-toggle",
        "data-bs-target",
        "tabindex",
        "role",
    ]
    backup_attributes = []  # if the attributes are not found, then try to get these attributes
    tags_to_ignore = [
        "head",
        "style",
        "script",
        "link",
        "meta",
        "noscript",
        "template",
        "iframe",
        "g",
        "main",
        "c-wiz",
        "svg",
        "path",
    ]
    attributes_to_delete = ["level", "multiline", "haspopup", "id", "for"]
    ids_to_ignore = ["agentDriveAutoOverlay"]

    # Recursive function to process each node in the accessibility tree
    async def process_node(node: dict[str, Any]) -> None:
        if node:
            if "children" in node:
                for child in node["children"]:
                    await process_node(child)

            # Use 'name' attribute from the accessibility node as 'md'
            md_temp: str = node.get("keyshortcuts")  # type: ignore

            # If the name has multiple mds, take the last one
            if md_temp and is_space_delimited_md(md_temp):
                # TODO: consider if we should grab each of the mds and process them separately as seperate nodes copying this node's attributes
                md_temp = md_temp.split(" ")[-1]

            # focusing on nodes with md, which is the attribute we inject
            try:
                md = int(md_temp)
            except (ValueError, TypeError):
                # logger.error(f"'name attribute contains \"{node.get('name')}\", which is not a valid numeric md. Adding node as is: {node}")
                return node.get("name")

            if node["role"] == "menuitem":
                return node.get("name")

            if node.get("role") == "dialog" and node.get("modal") == True:  # noqa: E712
                node["important information"] = (
                    "This is a modal dialog. Please interact with this dialog and close it to be able to interact with the full page (e.g. by pressing the close button or selecting an option)."
                )

            if md:
                # Determine if we need to fetch 'innerText' based on the absence of 'children' in the accessibility node
                should_fetch_inner_text = "children" not in node

                js_code = """
                    (input_params) => {
                        const should_fetch_inner_text = input_params.should_fetch_inner_text;
                        const md = input_params.md;
                        const attributes = input_params.attributes;
                        const tags_to_ignore = input_params.tags_to_ignore;
                        const ids_to_ignore = input_params.ids_to_ignore;

                        // Helper function to search for an element by md across DOM, shadow DOMs, and iframes
                        const findElementByMd = (parent, md) => {
                            // Look in the parent context (can be document or shadow root)
                            const element = parent.querySelector(`[md="${md}"]`);

                            if (element) {
                                return element; // Found in parent context
                            }

                            // If the element is not found, try looking inside shadow DOMs and iframes
                            const elements = parent.querySelectorAll('*');
                            for (const el of elements) {
                                // Check for shadow DOM
                                if (el.shadowRoot) {
                                    const shadowElement = findElementByMd(el.shadowRoot, md);
                                    if (shadowElement) {
                                        return shadowElement; // Found in shadow DOM
                                    }
                                }
                                // Check for iframes
                                if (el.tagName.toLowerCase() === 'iframe') {
                                    let iframeDocument;
                                    try {
                                        iframeDocument = el.contentDocument || el.contentWindow.document;
                                    } catch (e) {
                                        // Cannot access cross-origin iframe; skip to the next element
                                        continue;
                                    }
                                    if (iframeDocument) {
                                        const iframeElement = findElementByMd(iframeDocument, md);
                                        if (iframeElement) {
                                            return iframeElement; // Found in iframe
                                        }
                                    }
                                }
                            }

                            return null; // Not found
                        };

                        // Start the search in the document (regular DOM)
                        const element = findElementByMd(document, md);

                        if (!element) {
                            console.log(`No element found with md: ${md}`);
                            return null;
                        }

                        if (ids_to_ignore.includes(element.id)) {
                            console.log(`Ignoring element with id: ${element.id}`, element);
                            return null;
                        }

                        if (tags_to_ignore.includes(element.tagName.toLowerCase()) || element.tagName.toLowerCase() === "option") {
                            return null;
                        }

                        let attributes_to_values = {
                            'tag': element.tagName.toLowerCase() // Always include the tag name
                        };

                        if (element.hasAttribute('aria-describedby')) {
                            const describedbyId = element.getAttribute('aria-describedby');
                            const describedElement = findElementById(document, describedbyId);
                            if (describedElement) {
                                attributes_to_values['tooltip'] = describedElement.innerText || describedElement.textContent;
                            }
                        }
                        
                        if (element.tagName.toLowerCase() === 'input') {
                            attributes_to_values['tag_type'] = element.type;
                        } else if (element.tagName.toLowerCase() === 'select') {
                            attributes_to_values["md"] = element.getAttribute('md');
                            attributes_to_values["role"] = "combobox";
                            attributes_to_values["options"] = [];

                            for (const option of element.options) {
                                let option_attributes_to_values = {
                                    "md": option.getAttribute('md'),
                                    "text": option.text,
                                    "value": option.value,
                                    "selected": option.selected
                                };
                                attributes_to_values["options"].push(option_attributes_to_values);
                            }
                            return attributes_to_values;
                        }

                        // Capture all specified attributes
                        for (const attribute of attributes) {
                            let value = element.getAttribute(attribute);

                            if (value) {
                                attributes_to_values[attribute] = value;
                            }
                        }
                        
                        // Capture all AngularJS attributes dynamically
                        const angularAttrs = {};
                        for (const attr of element.attributes) {
                            if (attr.name.startsWith('ng-')) {
                                angularAttrs[attr.name] = attr.value;
                            }
                        }
                        if (Object.keys(angularAttrs).length > 0) {
                            attributes_to_values['angular_attributes'] = angularAttrs;
                        }
                        
                        // Capture all data attributes dynamically
                        const dataAttrs = {};
                        for (const attr of element.attributes) {
                            if (attr.name.startsWith('data-')) {
                                dataAttrs[attr.name] = attr.value;
                            }
                        }
                        if (Object.keys(dataAttrs).length > 0) {
                            attributes_to_values['data_attributes'] = dataAttrs;
                        }
                        
                        // Capture all aria attributes dynamically
                        const ariaAttrs = {};
                        for (const attr of element.attributes) {
                            if (attr.name.startsWith('aria-')) {
                                ariaAttrs[attr.name] = attr.value;
                            }
                        }
                        if (Object.keys(ariaAttrs).length > 0) {
                            attributes_to_values['aria_attributes'] = ariaAttrs;
                        }

                        if (should_fetch_inner_text && element.innerText) {
                            attributes_to_values['description'] = element.innerText;
                        }

                        let role = element.getAttribute('role');
                        if (role === 'listbox' || element.tagName.toLowerCase() === 'ul') {
                            let children = element.children;
                            let filtered_children = Array.from(children).filter(child => child.getAttribute('role') === 'option');
                            console.log("Listbox or ul found: ", filtered_children);
                            let attributes_to_include = ['md', 'role', 'aria-label', 'value'];
                            attributes_to_values["additional_info"] = [];

                            for (const child of children) {
                                let children_attributes_to_values = {};

                                for (let attr of child.attributes) {
                                    if (attributes_to_include.includes(attr.name)) {
                                        children_attributes_to_values[attr.name] = attr.value;
                                    }
                                }

                                attributes_to_values["additional_info"].push(children_attributes_to_values);
                            }
                        }

                        const minimalKeys = ['tag', 'md'];
                        const hasMoreThanMinimalKeys = Object.keys(attributes_to_values).length > minimalKeys.length;

                        if (!hasMoreThanMinimalKeys) {
                            for (const backupAttribute of input_params.backup_attributes) {
                                let value = element.getAttribute(backupAttribute);
                                if (value) {
                                    attributes_to_values[backupAttribute] = value;
                                }
                            }

                            if (Object.keys(attributes_to_values).length <= minimalKeys.length) {
                                if (element.tagName.toLowerCase() === 'button') {
                                    attributes_to_values["md"] = element.getAttribute('md');
                                    attributes_to_values["role"] = "button";
                                    attributes_to_values["additional_info"] = [];
                                    let children = element.children;
                                    let attributes_to_exclude = ['width', 'height', 'path', 'class', 'viewBox', 'md'];

                                    if (element.innerText.trim() === '') {
                                        for (const child of children) {
                                            let children_attributes_to_values = {};

                                            for (let attr of child.attributes) {
                                                if (!attributes_to_exclude.includes(attr.name)) {
                                                    children_attributes_to_values[attr.name] = attr.value;
                                                }
                                            }

                                            attributes_to_values["additional_info"].push(children_attributes_to_values);
                                        }
                                        console.log("Button with no text and no attributes: ", attributes_to_values);
                                        return attributes_to_values;
                                    }
                                }

                                return null;
                            }
                        }

                        return attributes_to_values;
                    }

                """

                # Fetch attributes and possibly 'innerText' from the DOM element by 'md'
                element_attributes = await page.evaluate(
                    js_code,
                    {
                        "md": md,
                        "attributes": attributes,
                        "backup_attributes": backup_attributes,
                        "should_fetch_inner_text": should_fetch_inner_text,
                        "tags_to_ignore": tags_to_ignore,
                        "ids_to_ignore": ids_to_ignore,
                    },
                )

                if "keyshortcuts" in node:
                    del node["keyshortcuts"]  # remove keyshortcuts since it is not needed

                node["md"] = md

                # Update the node with fetched information
                if element_attributes:
                    node.update(element_attributes)

                    # check if 'name' and 'md' are the same
                    if node.get("name") == node.get("md") and node.get("role") != "textbox":
                        del node["name"]  # Remove 'name' from the node

                    if (
                        "name" in node
                        and "description" in node
                        and (node["name"] == node["description"] or node["name"] == node["description"].replace("\n", " ") or node["description"].replace("\n", "") in node["name"])
                    ):
                        del node["description"]  # if the name is same as description, then remove the description to avoid duplication

                    if "name" in node and "aria-label" in node and node["aria-label"] in node["name"]:
                        del node["aria-label"]  # if the name is same as the aria-label, then remove the aria-label to avoid duplication

                    if "name" in node and "text" in node and node["name"] == node["text"]:
                        del node["text"]  # if the name is same as the text, then remove the text to avoid duplication

                    if node.get("tag") == "select":  # children are not needed for select menus since "options" attriburte is already added
                        node.pop("children", None)
                        node.pop("role", None)
                        node.pop("description", None)

                    # role and tag can have the same info. Get rid of role if it is the same as tag
                    if node.get("role") == node.get("tag"):
                        del node["role"]

                    # avoid duplicate aria-label
                    if node.get("aria-label") and node.get("placeholder") and node.get("aria-label") == node.get("placeholder"):
                        del node["aria-label"]

                    if node.get("role") == "link":
                        del node["role"]
                        if node.get("description"):
                            node["text"] = node["description"]
                            del node["description"]

                    # textbox just means a text input and that is expressed well enough with the rest of the attributes returned
                    # if node.get('role') == "textbox":
                    #    del node['role']

                    if node.get("role") == "textbox":
                        # get the id attribute of this field from the DOM
                        if "id" in element_attributes and element_attributes["id"]:
                            # find if there is an element in the DOM that has this id in aria-labelledby.
                            js_code = """
                                (inputParams) => {
                                    const findElementByAriaLabelledBy = (parent, ariaLabelledByValue) => {
                                        // Search in the current DOM context (can be document or shadow root)
                                        let referencedElement = parent.querySelector(`[aria-labelledby="${ariaLabelledByValue}"]`);

                                        if (referencedElement) {
                                            return referencedElement; // Found in the current context
                                        }

                                        // Search inside shadow DOMs and iframes
                                        const elements = parent.querySelectorAll('*');
                                        for (const element of elements) {
                                            // Search inside shadow DOM
                                            if (element.shadowRoot) {
                                                referencedElement = findElementByAriaLabelledBy(element.shadowRoot, ariaLabelledByValue);
                                                if (referencedElement) {
                                                    return referencedElement; // Found inside shadow DOM
                                                }
                                            }
                                            // Search inside iframes
                                            if (element.tagName.toLowerCase() === 'iframe') {
                                                let iframeDocument;
                                                try {
                                                    // Access the iframe's document if it's same-origin
                                                    iframeDocument = element.contentDocument || element.contentWindow.document;
                                                } catch (e) {
                                                    // Cannot access cross-origin iframe; skip to the next element
                                                    continue;
                                                }
                                                if (iframeDocument) {
                                                    referencedElement = findElementByAriaLabelledBy(iframeDocument, ariaLabelledByValue);
                                                    if (referencedElement) {
                                                        return referencedElement; // Found inside iframe
                                                    }
                                                }
                                            }
                                        }

                                        return null; // Element not found in this context
                                    };

                                    // Start the search from the main document
                                    const referencedElement = findElementByAriaLabelledBy(document, inputParams.aria_labelled_by_query_value);

                                    if (referencedElement) {
                                        const md = referencedElement.getAttribute('md');
                                        if (md) {
                                            return { "md": md, "tag": referencedElement.tagName.toLowerCase() };
                                        }
                                    }

                                    return null;
                                }

                            """
                        # textbox just means a text input and that is expressed well enough with the rest of the attributes returned
                        # del node['role']

                # remove attributes that are not needed once processing of a node is complete
                for attribute_to_delete in attributes_to_delete:
                    if attribute_to_delete in node:
                        node.pop(attribute_to_delete, None)
            else:
                logger.debug(f"No element found with md: {md}, deleting node: {node}")
                node["marked_for_deletion_by_mm"] = True

    # Process each node in the tree starting from the root
    await process_node(accessibility_tree)

    pruned_tree = __prune_tree(accessibility_tree, only_input_fields)

    logger.debug("Reconciliation complete")
    return pruned_tree


async def __cleanup_dom(page: Page) -> None:
    """
    Cleans up the DOM by removing injected 'aria-description' attributes and restoring any original 'aria-keyshortcuts'
    from 'orig-aria-keyshortcuts'.
    """
    logger.debug("Cleaning up the DOM's previous injections")
    await page.evaluate(
        """() => {
            // Recursive function to process elements in DOM, shadow DOM, and iframes
            const processElements = (parent) => {
                // Select all elements with the 'md' attribute in the current parent (regular DOM or shadow DOM)
                const allElements = parent.querySelectorAll('*[md]');

                // Iterate through each element and process its attributes
                allElements.forEach(element => {
                    element.removeAttribute('aria-keyshortcuts');
                    const origAriaLabel = element.getAttribute('orig-aria-keyshortcuts');
                    if (origAriaLabel) {
                        element.setAttribute('aria-keyshortcuts', origAriaLabel);
                        element.removeAttribute('orig-aria-keyshortcuts');
                    }

                    // Check if the element has a shadow DOM and recursively process its shadow root
                    if (element.shadowRoot) {
                        processElements(element.shadowRoot); // Process elements inside shadow DOM
                    }

                    // Check if the element is an iframe and process its content
                    if (element.tagName.toLowerCase() === 'iframe') {
                        let iframeDocument;
                        try {
                            // Access the iframe's document if it's same-origin
                            iframeDocument = element.contentDocument || element.contentWindow.document;
                        } catch (e) {
                            // Cannot access cross-origin iframe; skip to the next element
                            return;
                        }
                        if (iframeDocument) {
                            processElements(iframeDocument); // Process elements inside the iframe
                        }
                    }
                });
            };

            // Start the process with the regular DOM (document)
            processElements(document);
        };

    """
    )
    logger.debug("DOM cleanup complete")


def __prune_tree_old(node: dict[str, Any], only_input_fields: bool) -> dict[str, Any] | None:
    """
    Recursively prunes a tree starting from `node`, based on pruning conditions and handling of 'unraveling'.

    The function has two main jobs:
    1. Pruning: Remove nodes that don't meet certain conditions, like being marked for deletion.
    2. Unraveling: For nodes marked with 'marked_for_unravel_children', we replace them with their children,
       effectively removing the node and lifting its children up a level in the tree.

    This happens in place, meaning we modify the tree as we go, which is efficient but means you should
    be cautious about modifying the tree outside this function during a prune operation.

    Args:
    - node (Dict[str, Any]): The node we're currently looking at. We'll check this node, its children,
      and so on, recursively down the tree.
    - only_input_fields (bool): If True, we're only interested in pruning input-related nodes (like form fields).
      This lets you narrow the focus if, for example, you're only interested in cleaning up form-related parts
      of a larger tree.

    Returns:
    - dict[str, Any] | None: The pruned version of `node`, or None if `node` was pruned away. When we 'unravel'
      a node, we directly replace it with its children in the parent's list of children, so the return value
      will be the parent, updated in place.

    Notes:
    - 'marked_for_deletion_by_mm' is our flag for nodes that should definitely be removed.
    - Unraveling is neat for flattening the tree when a node is just a wrapper without semantic meaning.
    - We use a while loop with manual index management to safely modify the list of children as we iterate over it.
    """
    if not node:
        return None
    if "marked_for_deletion_by_mm" in node:
        return None

    if "children" in node:
        i = 0
        while i < len(node["children"]):
            child = node["children"][i]
            if "marked_for_unravel_children" in child:
                # Replace the current child with its children
                if "children" in child:
                    node["children"] = node["children"][:i] + child["children"] + node["children"][i + 1 :]
                    i += len(child["children"]) - 1  # Adjust the index for the new children
                else:
                    # If the node marked for unraveling has no children, remove it
                    node["children"].pop(i)
                    i -= 1  # Adjust the index since we removed an element
            else:
                # Recursively prune the child if it's not marked for unraveling
                pruned_child = __prune_tree(child, only_input_fields)
                if pruned_child is None:
                    # If the child is pruned, remove it from the children list
                    node["children"].pop(i)
                    i -= 1  # Adjust the index since we removed an element
                else:
                    # Update the child with the pruned version
                    node["children"][i] = pruned_child
            i += 1  # Move to the next child

        # After processing all children, if the children array is empty, remove it
        if not node["children"]:
            del node["children"]

    # Apply existing conditions to decide if the current node should be pruned
    return None if __should_prune_node(node, only_input_fields) else node


def __prune_tree(node: dict[str, Any], only_input_fields: bool) -> dict[str, Any] | None:
    """
    Recursively prunes the tree according to rules:
      1) Drop 'level' from all nodes.
      2) If a parent (without 'md') has exactly one child, and both share the same 'name',
         collapse the parent into its child.
      3) If a node with 'md' has a child with the same 'name', drop attributes in the child
         that match the parent's attributes (to prevent duplication).
      4) Retain 'md' nodes otherwise (except for dropping 'level').

      'only_input_fields' is used by the existing __should_prune_node logic.
    """
    if not node or "marked_for_deletion_by_mm" in node:
        return None

    # 1) Drop the 'level' field from the current node
    node.pop("level", None)

    # Recursively prune children first
    if "children" in node:
        pruned_children = []
        for child in node["children"]:
            pruned_child = __prune_tree(child, only_input_fields)
            if pruned_child:
                pruned_children.append(pruned_child)
        node["children"] = pruned_children

        # ------------------------------------------------
        # 2) Collapse a non-md parent with a single child if they share the same name
        # ------------------------------------------------
        if "md" not in node and len(node["children"]) == 1:  # Parent must NOT have md  # Only one child
            child = node["children"][0]
            if child.get("name") == node.get("name"):
                # If parent has no unique attributes aside from 'children'
                # that child doesn't have, we can collapse.
                parent_keys = set(node.keys()) - {"children"}
                child_keys = set(child.keys()) - {"children"}
                # If parent's non-children keys are contained in child's keys
                # or they are exactly the same, collapse the parent
                if parent_keys.issubset(child_keys):
                    return child  # effectively drop the parent

        # ------------------------------------------------
        # 3) If this node has md, check if any child has the same name. Drop duplicates in child.
        # ------------------------------------------------
        if "md" in node:
            for child in node["children"]:
                if child.get("name") == node.get("name"):
                    _drop_duplicate_attrs(parent=node, child=child)

        # Remove `children` if empty
        if not node["children"]:
            node.pop("children", None)

    # If a node has `md`, we do NOT collapse or remove it (except 'level' which we already removed).
    if "md" in node:
        # We still run __should_prune_node in case there's a condition that truly prunes it,
        # but typically md nodes are not pruned.
        if __should_prune_node(node, only_input_fields):
            return None
        return node

    # Existing pruning logic
    if __should_prune_node(node, only_input_fields):
        return None

    return node


def _drop_duplicate_attrs(parent: dict[str, Any], child: dict[str, Any]) -> None:
    """
    If child has the same attributes as the parent (same key AND same value),
    drop those from the child to avoid duplication.
    We skip 'children' and 'md' since they are structural or important IDs.
    """
    skip_keys = {"children", "md"}
    for key in list(child.keys()):
        if key not in skip_keys and key in parent and parent[key] == child[key]:
            child.pop(key)


def __should_prune_node(node: dict[str, Any], only_input_fields: bool) -> bool:
    """
    Determines if a node should be pruned based on its 'role' and 'element_attributes'.

    Args:
        node (dict[str, Any]): The node to be evaluated.
        only_input_fields (bool): Flag indicating whether only input fields should be considered.

    Returns:
        bool: True if the node should be pruned, False otherwise.
    """
    if not node.get("md"):
        return False
    list_of_interactive_roles = set(
        [
            "WebArea",
            "button",
            "checkbox",
            "combobox",
            "gridcell",
            "listbox",
            "menuitem",
            "menuitemcheckbox",
            "menuitemradio",
            "option",
            "radio",
            "searchbox",
            "slider",
            "spinbutton",
            "switch",
            "textbox",
            "treeitem",
        ]
    )
    # If the request is for only input fields and this is not an input field, then mark the node for prunning
    if node.get("tag") == "noscript":
        return True
    if not only_input_fields:
        if node.get("role") in list_of_interactive_roles or node.get("tag") in ("input", "button", "textarea"):
            return False

    if node.get("role") == "generic" and "children" not in node and not ("name" in node and node.get("name")):  # The presence of 'children' is checked after potentially deleting it above
        return True

    if node.get("role") in ["separator", "LineBreak"]:
        return True
    processed_name = ""
    if "name" in node:
        processed_name: str = node.get("name")  # type: ignore
        processed_name = processed_name.replace(",", "")
        processed_name = processed_name.replace(":", "")
        processed_name.replace("\n", "")
        processed_name = processed_name.strip()
        if len(processed_name) < 3:
            processed_name = ""

    # check if the node only have name and role, then delete that node
    if len(node) == 2 and "name" in node and "role" in node and not (node.get("role") == "text" and processed_name != ""):
        return True

    if node.get("tag") == "span" and not node.get("role") and not node.get("md"):
        return True
    return False


async def get_node_dom_element(page: Page, md: str) -> Any:
    return await page.evaluate(
        """
        (md) => {
            return document.querySelector(`[md="${md}"]`);
        }
    """,
        md,
    )


async def get_element_attributes(page: Page, md: str, attributes: list[str]) -> dict[str, Any]:
    return await page.evaluate(
        """
        (inputParams) => {
            const md = inputParams.md;
            const attributes = inputParams.attributes;

            // Helper function to recursively search for the element with the md in regular DOM, shadow DOMs, and iframes
            const findElementByMd = (parent, md) => {
                // First, try to find the element in the current DOM context (either document, shadowRoot, or iframe document)
                let element = parent.querySelector(`[md="${md}"]`);
                
                if (element) {
                    return element; // Found the element in the current context
                }

                // If not found, look inside shadow roots and iframes of elements in this context
                const elements = parent.querySelectorAll('*');
                for (const el of elements) {
                    // Search inside shadow DOMs
                    if (el.shadowRoot) {
                        element = findElementByMd(el.shadowRoot, md);
                        if (element) {
                            return element; // Element found in shadow DOM
                        }
                    }
                    // Search inside iframes
                    if (el.tagName.toLowerCase() === 'iframe') {
                        let iframeDocument;
                        try {
                            // Access the iframe's document if it's same-origin
                            iframeDocument = el.contentDocument || el.contentWindow.document;
                        } catch (e) {
                            // Cannot access cross-origin iframe; skip to the next element
                            continue;
                        }
                        if (iframeDocument) {
                            element = findElementByMd(iframeDocument, md);
                            if (element) {
                                return element; // Element found inside iframe
                            }
                        }
                    }
                }

                return null; // Return null if element is not found
            };

            // Start searching from the regular DOM (document)
            const element = findElementByMd(document, md);
            if (!element) return null;  // Return null if element is not found

            // Collect the requested attributes from the found element
            let attrs = {};
            for (let attr of attributes) {
                attrs[attr] = element.getAttribute(attr);
            }

            return attrs;
        }

    """,
        {"md": md, "attributes": attributes},
    )


async def get_dom_with_accessibility_info() -> Annotated[
    dict[str, Any] | None,
    "A minified representation of the HTML DOM for the current webpage",
]:
    """
    Retrieves, processes, and minifies the Accessibility tree of the active page in a browser instance.
    Strictly follow the name and role tag for any interaction with the nodes.

    Returns:
    - The minified JSON content of the browser's active page.
    """
    logger.debug("Executing Get Accessibility Tree Command")
    # Create and use the PlaywrightManager
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        raise ValueError("No active page found")

    return await do_get_accessibility_info(page)


async def do_get_accessibility_info(page: Page, only_input_fields: bool = False) -> dict[str, Any] | None:
    """
    Retrieves the accessibility information of a web page and saves it as JSON files.

    Args:
        page (Page): The page object representing the web page.
        only_input_fields (bool, optional): If True, only retrieves accessibility information for input fields.
            Defaults to False.

    Returns:
        dict[str, Any] or None: The enhanced accessibility tree as a dictionary, or None if an error occurred.
    """
    await __inject_attributes(page)
    # accessibility_tree: dict[str, Any] = await page.accessibility.snapshot(interesting_only=True)  # type: ignore
    js_code = """
            () => {
                function generateAccessibilityTree(rootElement, level) {
                    const requiredAriaAttributesByRole = {
                        'alert': [],
                        'button': [],
                        'checkbox': ['aria-checked'],
                        'combobox': ['aria-expanded', 'aria-controls'],
                        'dialog': [],
                        'gridcell': [],
                        'link': [],
                        'listbox': ['aria-multiselectable'],
                        'menuitemcheckbox': ['aria-checked'],
                        'menuitemradio': ['aria-checked'],
                        'option': ['aria-selected'],
                        'progressbar': ['aria-valuenow'],
                        'radio': ['aria-checked'],
                        'scrollbar': ['aria-controls', 'aria-valuenow', 'aria-valuemin', 'aria-valuemax', 'aria-orientation'],
                        'searchbox': [],
                        'slider': ['aria-valuenow', 'aria-valuemin', 'aria-valuemax', 'aria-orientation'],
                        'spinbutton': ['aria-valuenow', 'aria-valuemin', 'aria-valuemax'],
                        'tab': ['aria-selected'],
                        'tabpanel': [],
                        'textbox': ['aria-multiline'],
                        'treeitem': ['aria-expanded']
                    };

                    function isElementHidden(element) {
                        const style = window.getComputedStyle(element);
                        return (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            element.getAttribute('aria-hidden') === 'true'
                        );
                    }

                    function getAccessibleName(element) {
                        try {
                            // Chromium-based accessibility API
                            if (window.getComputedAccessibleNode) {
                                const accessibilityInfo = window.getComputedAccessibleNode(element);
                                console.log('accessibilityInfo:', accessibilityInfo);
                                if (accessibilityInfo?.name) {
                                    return cleanName(accessibilityInfo.name);
                                }
                            }
                        } catch (error) {
                            console.warn("Chromium accessibility API failed, falling back to manual method:", error);
                        }

                        // Existing manual accessibility extraction
                        let name = element.getAttribute('aria-label');
                        if (name) return cleanName(name);

                        const labelledby = element.getAttribute('aria-labelledby');
                        if (labelledby) {
                            const labelElement = document.getElementById(labelledby);
                            if (labelElement) return cleanName(labelElement.innerText);
                        }

                        if (element.alt) return cleanName(element.alt);
                        if (element.title) return cleanName(element.title);
                        if (element.placeholder) return cleanName(element.placeholder);

                        if (
                            element.value &&
                            (element.tagName.toLowerCase() === 'input' || element.tagName.toLowerCase() === 'textarea')
                        ) {
                            return cleanName(element.value);
                        }

                        // Return class names for better identification
                        if (element.className && element.className.trim()) {
                            return element.className;
                        }

                        // Check for text content in child elements
                        if (element.children.length > 0) {
                            for (const child of element.children) {
                                if (child.innerText && child.innerText.trim()) {
                                    const text = cleanName(child.innerText);
                                    if (text && text !== '') return text;
                                }
                            }
                        }

                        if (element.innerText) return cleanName(element.innerText);
                        return '';
                    }

                    function cleanName(name) {
                        if (typeof name !== 'string') {
                            console.warn('Expected a string, but received:', name);
                            return '';
                        }
                        const firstLine = name.split('\\n')[0];
                        return firstLine.trim();
                    }

                    function getRole(element) {
                        const role = element.getAttribute('role');
                        if (role) return role;

                        const tagName = element.tagName.toLowerCase();
                        if (tagName === 'button') return 'button';
                        if (tagName === 'a' && element.hasAttribute('href')) return 'link';
                        if (tagName === 'a' && (element.hasAttribute('ng-click') || element.hasAttribute('onclick'))) return 'button';
                        if (tagName === 'input') {
                            const type = element.type.toLowerCase();
                            switch (type) {
                                case 'button':
                                case 'submit':
                                case 'reset':
                                case 'image':
                                    return 'button';
                                case 'checkbox':
                                    return 'checkbox';
                                case 'radio':
                                    return 'radio';
                                case 'range':
                                    return 'slider';
                                case 'number':
                                    return 'spinbutton';
                                case 'search':
                                    return 'searchbox';
                                case 'file':
                                    return 'button';
                                case 'color':
                                case 'date':
                                case 'datetime-local':
                                case 'month':
                                case 'time':
                                case 'week':
                                    return 'combobox';
                                case 'email':
                                case 'tel':
                                case 'url':
                                case 'password':
                                case 'text':
                                    return 'textbox';
                                case 'hidden':
                                    return '';
                                default:
                                    return 'textbox';
                            }
                        }

                        if (tagName === 'select') return 'listbox';
                        if (tagName === 'textarea') return 'textbox';
                        
                        // Handle AngularJS and other interactive elements
                        if (element.hasAttribute('ng-click') || element.hasAttribute('onclick')) {
                            if (tagName === 'h1' || tagName === 'h2' || tagName === 'h3' || tagName === 'h4' || tagName === 'h5' || tagName === 'h6') return 'button';
                            if (tagName === 'div' || tagName === 'span') return 'button';
                            if (tagName === 'li') return 'menuitem';
                        }
                        
                        if (element.hasAttribute('ng-mouseover') || element.hasAttribute('onmouseover')) {
                            if (tagName === 'li') return 'menuitem';
                            if (tagName === 'div' || tagName === 'span') return 'button';
                        }
                        
                        // Handle menu-specific elements generically based on attributes
                        if (element.hasAttribute('ng-click') || element.hasAttribute('onclick') ||
                            element.hasAttribute('ng-mouseover') || element.hasAttribute('onmouseover')) {
                            if (tagName === 'span') return 'button';
                            if (tagName === 'li') return 'menuitem';
                            if (tagName === 'ul') return 'menu';
                        }
                        
                        if (tagName === 'nav') return 'navigation';
                        if (tagName === 'header') return 'banner';
                        if (tagName === 'footer') return 'contentinfo';
                        if (tagName === 'main') return 'main';
                        if (tagName === 'section') return 'region';
                        if (tagName === 'article') return 'article';
                        if (tagName === 'aside') return 'complementary';
                        if (tagName === 'ul' || tagName === 'ol') return 'list';
                        if (tagName === 'li') return 'listitem';
                        
                        return '';
                    }

                    function getRequiredAriaAttributes(element) {
                        const role = getRole(element);
                        return requiredAriaAttributesByRole[role] || [];
                    }

                    function processElement(element, level) {
                        if (isElementHidden(element)) return null;

                        const node = {};

                        const md = element.getAttribute('md');
                        if (md) node.md = md;

                        node.tag = element.tagName.toLowerCase();

                        const role = getRole(element);
                        if (role) node.role = role;

                        const name = getAccessibleName(element);
                        if (name) node.name = name;

                        const title = element.getAttribute('title');
                        if (title) node.title = cleanName(title);

                        // Capture additional attributes for better identification
                        if (element.className && element.className.trim()) {
                            node.class = element.className;
                        }
                        
                        if (element.id) {
                            node.id = element.id;
                        }
                        
                        // Capture AngularJS attributes
                        const angularAttrs = {};
                        for (const attr of element.attributes) {
                            if (attr.name.startsWith('ng-')) {
                                angularAttrs[attr.name] = attr.value;
                            }
                        }
                        if (Object.keys(angularAttrs).length > 0) {
                            node.angular_attributes = angularAttrs;
                        }
                        
                        // Capture data attributes
                        const dataAttrs = {};
                        for (const attr of element.attributes) {
                            if (attr.name.startsWith('data-')) {
                                dataAttrs[attr.name] = attr.value;
                            }
                        }
                        if (Object.keys(dataAttrs).length > 0) {
                            node.data_attributes = dataAttrs;
                        }

                        if (level) node.level = level;

                        // Detect interactive properties
                        const hasEventHandlers = element.hasAttribute('ng-click') || 
                                               element.hasAttribute('onclick') ||
                                               element.hasAttribute('ng-mouseover') ||
                                               element.hasAttribute('ng-mouseenter') ||
                                               element.hasAttribute('ng-mouseleave') ||
                                               element.hasAttribute('ng-dblclick') ||
                                               element.hasAttribute('ng-keydown') ||
                                               element.hasAttribute('ng-keyup') ||
                                               element.hasAttribute('ng-keypress') ||
                                               element.hasAttribute('ng-focus') ||
                                               element.hasAttribute('ng-blur') ||
                                               element.hasAttribute('ng-change') ||
                                               element.hasAttribute('ng-submit') ||
                                               element.onclick !== null ||
                                               element.onmouseover !== null ||
                                               element.onmouseenter !== null ||
                                               element.onmouseleave !== null ||
                                               element.ondblclick !== null ||
                                               element.onkeydown !== null ||
                                               element.onkeyup !== null ||
                                               element.onkeypress !== null ||
                                               element.onfocus !== null ||
                                               element.onblur !== null ||
                                               element.onchange !== null ||
                                               element.onsubmit !== null;

                        // Check for AngularJS directives that make elements interactive
                        const hasAngularDirectives = element.hasAttribute('ng-click') || 
                                                   element.hasAttribute('ng-mouseover') ||
                                                   element.hasAttribute('ng-repeat') ||
                                                   element.hasAttribute('ng-if') ||
                                                   element.hasAttribute('ng-show') ||
                                                   element.hasAttribute('ng-hide') ||
                                                   element.hasAttribute('ng-model') ||
                                                   element.hasAttribute('ng-bind') ||
                                                   element.hasAttribute('ng-class') ||
                                                   element.hasAttribute('ng-style');

                        // Check for other interactive attributes
                        const hasInteractiveAttributes = element.hasAttribute('tabindex') ||
                                                       element.hasAttribute('data-toggle') ||
                                                       element.hasAttribute('data-target') ||
                                                       element.hasAttribute('data-bs-toggle') ||
                                                       element.hasAttribute('data-bs-target') ||
                                                       element.hasAttribute('role') ||
                                                       element.style.cursor === 'pointer' ||
                                                       element.style.cursor === 'hand';

                        // Set interactive properties
                        if (hasEventHandlers) node.clickable = true;
                        if (hasAngularDirectives) node.angular_interactive = true;
                        if (hasInteractiveAttributes) node.interactive_attributes = true;

                        node.children = [];

                        if (element.shadowRoot) {
                            for (const child of element.shadowRoot.children) {
                                const childNode = processElement(child, level + 1);
                                if (childNode) node.children.push(childNode);
                            }
                        }

                        for (const child of element.children) {
                            if (child.tagName.toLowerCase() === 'iframe') {
                                try {
                                    const iframeDoc = child.contentDocument || child.contentWindow.document;
                                    if (iframeDoc && iframeDoc.body) {
                                        const iframeTree = generateAccessibilityTree(iframeDoc.body, level + 1);
                                        if (iframeTree) {
                                            const iframeNode = {
                                                tag: 'iframe',
                                                role: 'document',
                                                level: level + 1,
                                                children: [iframeTree]
                                            };
                                            const iframeMd = child.getAttribute('md');
                                            if (iframeMd) iframeNode.md = iframeMd;
                                            node.children.push(iframeNode);
                                        }
                                    }
                                } catch (e) {
                                    // Handle cross-origin policy restrictions
                                }
                            } else {
                                const childNode = processElement(child, level + 1);
                                if (childNode) node.children.push(childNode);
                            }
                        }

                        if (!node.md && !node.name && node.children.length === 0 && !node.role) {
                            return null;
                        }

                        const requiredAriaAttrs = getRequiredAriaAttributes(element);
                        for (const attr of requiredAriaAttrs) {
                            if (!element.hasAttribute(attr)) {
                                // Handle missing required ARIA attribute warning
                            }
                        }

                        return node;
                    }

                    return processElement(rootElement || document.body, level || 1);
                }

                return generateAccessibilityTree();
            }
    """
    accessibility_tree = await page.evaluate(js_code)

    # logger.info("Consolidated Snapshot:", consolidated_snapshot)
    # accessibility_tree2: dict[str, Any] = await page.accessibility.snapshot(interesting_only=True)  # type: ignore

    # Update file write operation to use aiofiles
    async with aiofiles.open(
        os.path.join(get_global_conf().get_source_log_folder_path(), "json_accessibility_dom.json"),
        "w",
        encoding="utf-8",
    ) as f:
        await f.write(json.dumps(accessibility_tree, indent=2))
        logger.debug("json_accessibility_dom.json saved")

    await __cleanup_dom(page)
    try:
        enhanced_tree = await __fetch_dom_info(page, accessibility_tree, only_input_fields)

        logger.debug("Enhanced Accessibility Tree ready")

        # Update file write operation to use aiofiles
        async with aiofiles.open(
            os.path.join(
                get_global_conf().get_source_log_folder_path(),
                "json_accessibility_dom_enriched.json",
            ),
            "w",
            encoding="utf-8",
        ) as f:
            await f.write(json.dumps(enhanced_tree, indent=2))
            logger.debug("json_accessibility_dom_enriched.json saved")

        return enhanced_tree
    except Exception as e:
        logger.error(f"Error while fetching DOM info: {e}")
        traceback.print_exc()
        return None
