TOOLS_LIBRARY = {
    "click" : '''
page_data_store = {}

# Function to set data
def set_page_data(page, data) -> None:
    page_data_store[page] = data

# Function to get data
def get_page_data(page): 
    data = page_data_store.get(page)
    return data if data is not None else {}

DOM_change_callback = []

def subscribe(callback) -> None:
    DOM_change_callback.append(callback)

def unsubscribe(callback) -> None:
    DOM_change_callback.remove(callback)

async def click(
    browser_manager,
    selector,
    user_input_dialog_response = "",
    expected_message_of_dialog = "",
    action_on_dialog = "",
    type_of_click = "click",
    wait_before_execution = 0.0):
    
    query_selector = selector   
    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    action_on_dialog = action_on_dialog.lower() if action_on_dialog else ""
    type_of_click = type_of_click.lower() if type_of_click else "click"

    async def handle_dialog(dialog: Any) -> None:
        try:
            await asyncio.sleep(0.5)
            data = get_page_data(page)
            user_input_dialog_response = data.get("user_input_dialog_response", "")
            expected_message_of_dialog = data.get("expected_message_of_dialog", "")
            action_on_dialog = data.get("action_on_dialog", "")
            if action_on_dialog:
                action_on_dialog = action_on_dialog.lower().strip()
            dialog_message = dialog.message if dialog.message is not None else ""

            # Check if the dialog message matches the expected message (if provided)
            if expected_message_of_dialog and dialog_message != expected_message_of_dialog:
                if action_on_dialog == "accept":
                    if dialog.type == "prompt":
                        await dialog.accept(user_input_dialog_response)
                    else:
                        await dialog.accept()
                elif action_on_dialog == "dismiss":
                    await dialog.dismiss()
                else:
                    await dialog.dismiss()  # Dismiss if the dialog message doesn't match
            elif user_input_dialog_response:
                await dialog.accept(user_input_dialog_response)
            else:
                await dialog.dismiss()

        except Exception as e:

            traceback.print_exc()

    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(query_selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    subscribe(detect_dom_changes)
    set_page_data(
        page,
        {
            "user_input_dialog_response": user_input_dialog_response,
            "expected_message_of_dialog": expected_message_of_dialog,
            "action_on_dialog": action_on_dialog,
            "type_of_click": type_of_click,
        },
    )

    page = await browser_manager.get_current_page()
    page.on("dialog", handle_dialog)
    result = await do_click(page, query_selector, user_input_dialog_response, expected_message_of_dialog, 
                            action_on_dialog, type_of_click, wait_before_execution)

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}. As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action to click {query_selector} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]

    
async def do_click(
        browser_manager,
        page, 
        selector: str, 
        user_input_dialog_response: str, 
        expected_message_of_dialog: str, 
        action_on_dialog: str, 
        type_of_click: str, 
        wait_before_execution: float) -> dict[str, str]:

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    # Wait for the selector to be present and ensure it's attached and visible. If timeout, try JavaScript click
    try:
        element = await browser_manager.find_element(selector, page, element_name="click")
        if element is None:
            # Initialize selector logger with proof path
            # Log failed selector interaction
            raise ValueError(f'Element with selector: "{selector}" not found')
        try:
            await element.scroll_into_view_if_needed(timeout=2000)
        except Exception as e:

            traceback.print_exc()
            # If scrollIntoView fails, just move on, not a big deal
            pass

        if not await element.is_visible():
            return {
                "summary_message": f'Element with selector: "{selector}" is not visible, Try another element',
                "detailed_message": f'Element with selector: "{selector}" is not visible, Try another element',
            }

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)


        # hack for aura component in salesforce
        element_title = (await element.get_attribute("title") or "").lower()
        if "upload" in element_title:
            return {
                "summary_message": "Use the click_and_upload_file tool to upload files",
                "detailed_message": "Use the click_and_upload_file tool to upload files",
            }

        if element_tag_name == "option":
            element_value = await element.get_attribute("value")
            parent_element = await element.evaluate_handle("element => element.parentNode")
            await parent_element.select_option(value=element_value)  # type: ignore

            return {
                "summary_message": f'Select menu option "{element_value}" selected',
                "detailed_message": f'Select menu option "{element_value}" selected. The select elements outer HTML is: {element_outer_html}.',
            }

        input_type = await element.evaluate("(el) => el.type")

        # Determine if it's checkable
        if element_tag_name == "input" and input_type in ["radio"]:
            await element.check()
            msg = f'Checked element with selector: "{selector}"'
        elif element_tag_name == "input" and input_type in ["checkbox"]:
            await element.type(" ")
            msg = f'Checked element with selector: "{selector}"'
        else:
            # Perform the click based on the type_of_click
            if type_of_click == "right_click":
                await element.click(button="right")
                msg = f'Right-clicked element with selector: "{selector}"'
            elif type_of_click == "double_click":
                await element.dblclick()
                msg = f'Double-clicked element with selector: "{selector}"'
            elif type_of_click == "middle_click":
                await element.click(button="middle")
                msg = f'Middle-clicked element with selector: "{selector}"'
            else:  # Default to regular click
                await element.click()
                msg = f'Clicked element with selector: "{selector}"'

        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The clicked element's outer HTML is: {element_outer_html}.",
        }  # type: ignore
    except Exception as e:
        # Try a JavaScript fallback click before giving up

        traceback.print_exc()
        try:

            msg = await browser_manager.perform_javascript_click(page, selector, type_of_click)

            if msg:
                # Initialize selector logger with proof path
                # Log successful JavaScript fallback click

                return {
                    "summary_message": msg,
                    "detailed_message": f"{msg}.",
                }
        except Exception as js_error:

            traceback.print_exc()
            # Both standard and fallback methods failed, proceed with original error handling
            pass

        traceback.print_exc()
        msg = f'Unable to click element with selector: "{selector}" since the selector is invalid. Proceed by retrieving DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}

async def get_element_outer_html(element, page, element_tag_name: str | None = None) -> str:
    tag_name: str = element_tag_name if element_tag_name else await page.evaluate("element => element.tagName.toLowerCase()", element)

    attributes_of_interest: list[str] = [
        "id",
        "name",
        "aria-label",
        "placeholder",
        "href",
        "src",
        "aria-autocomplete",
        "role",
        "type",
        "data-testid",
        "value",
        "selected",
        "aria-labelledby",
        "aria-describedby",
        "aria-haspopup",
        "title",
        "aria-controls",
    ]
    opening_tag: str = f"<{tag_name}"

    for attr in attributes_of_interest:
        value: str = await element.get_attribute(attr)  # type: ignore
        if value:
            opening_tag += f' {attr}="{value}"'
    opening_tag += ">"

    return opening_tag

    ''',

    "openurl": '''
async def openurl(
    browser_manager,
    url: str,
    timeout = 3,
    force_new_tab = False,
):
    await browser_manager.get_browser_context()

    # Use the new reuse_or_create_tab method to get a page
    page = await browser_manager.reuse_or_create_tab(force_new_tab=force_new_tab)

    try:
        # Special handling for browser-specific URLs that need special treatment
        special_browser_urls = [
            "about:blank",
            "about:newtab",
            "chrome://newtab/",
            "edge://newtab/",
        ]
        if url.strip().lower() in special_browser_urls:
            special_url = url.strip().lower()
            try:
                # Handle these special URLs with JavaScript navigation instead of goto
                await page.evaluate(f"window.location.href = '{special_url}'")
                await page.wait_for_load_state("domcontentloaded")
            except Exception as e:
                traceback.print_exc()
                # Fallback method: For about: URLs, try direct goto without adding protocol
                try:
                    if special_url.startswith("about:"):
                        await page.goto(special_url, timeout=timeout * 1000)
                    else:
                        # For chrome:// and other browser URLs, try setting empty content first
                        await page.set_content("<html><body></body></html>")
                        await page.evaluate(f"window.location.href = '{special_url}'")
                except Exception as fallback_err:
                    traceback.print_exc()
                    # Continue anyway - we'll try to get the title

            title = await page.title()
            return f"Navigated to {special_url}, Title: {title}"

        url = ensure_protocol(url)
        if page.url == url:
            try:
                title = await page.title()
                return f"Page already loaded: {url}, Title: {title}"  # type: ignore
            except Exception as e:
                traceback.print_exc()

        # Navigate to the URL with a short timeout to ensure the initial load starts
        function_name = inspect.currentframe().f_code.co_name  # type: ignore

        await browser_manager.take_screenshots(f"{function_name}_start", page)

        response = await page.goto(url, timeout=timeout * 10000)  # type: ignore
        await browser_manager.take_screenshots(f"{function_name}_end", page)

        # Get navigation details
        title = await page.title()
        final_url = page.url
        status = response.status if response else None
        ok = response.ok if response else False

        # Wait for the page to load
        try:
            await browser_manager.wait_for_load_state_if_enabled(page=page, state="domcontentloaded")

            # Additional wait time if specified
            if timeout > 0:
                await asyncio.sleep(timeout)
        except Exception as e:
            traceback.print_exc()

        # Wait for the network to idle
        await browser_manager.wait_for_page_and_frames_load()

        return f"Page loaded: {final_url}, Title: {title}"  # type: ignore

    except PlaywrightTimeoutError as pte:
        return f"Timeout error opening URL: {url}"

    except Exception as e:
        traceback.print_exc()
        return f"Error opening URL: {url}"

def ensure_protocol(url: str) -> str:
    # List of special browser URL schemes that should not be modified
    special_schemes = [
        "about:",
        "chrome:",
        "edge:",
        "brave:",
        "firefox:",
        "safari:",
        "data:",
        "file:",
        "view-source:",
    ]

    # Check if the URL starts with any special scheme
    if any(url.startswith(scheme) for scheme in special_schemes):
        return url

    # Regular URL handling
    if not url.startswith(("http://", "https://")):
        url = "https://" + url  # Default to https if no protocol is specified
    return url
''',

    "test_page_accessibility": '''
AXE_SCRIPT_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"

async def test_page_accessibility(
    browser_manager,
    page_path: str,
):
    try:
        page = await browser_manager.get_current_page()

        if not page:
            raise ValueError("No active page found. OpenURL command opens a new page.")

        await browser_manager.wait_for_load_state_if_enabled(page=page, state="domcontentloaded")

        # Inject the Axe-core script
        response = await page.evaluate(
            f"""
            fetch("{AXE_SCRIPT_URL}").then(res => res.text())
            """
        )
        await page.add_script_tag(content=response)

        # Run accessibility checks
        axe_results = await page.evaluate(
            """
            async () => {
                return await axe.run();
            }
            """
        )
        
        # Output summary of violations
        violations = axe_results.get("violations", [])
        incomplete = axe_results.get("incomplete", [])
        failureSummaries = []
        for violation in violations:
            nodes = violation.get("nodes", [])
            for node in nodes:
                failureSummaries.append(node.get("failureSummary"))

        # If no violation failures, return success
        if not failureSummaries:
            result_dict = {
                "status": "success",
                "message": "No accessibility violations found.",
                "details": "All good",
            }
            return json.dumps(result_dict, separators=(",", ":"))

        # Otherwise, report the failures
        result_dict = {
            "status": "failure",
            "message": f"Accessibility violations found: {len(failureSummaries)}",
            "details": failureSummaries,
        }
        return json.dumps(result_dict, separators=(",", ":"))

    except Exception as e:

        traceback.print_exc()
        # In case of error, return an error payload
        error_dict = {
            "status": "error",
            "message": "An error occurred while performing the accessibility test.",
            "error": str(e),
        }
        return json.dumps(error_dict, separators=(",", ":"))
''',

    "generic_http_api" : '''
async def generic_http_api(
    method: str,
    url: str,
    auth_type: str = None,
    auth_value = None,
    query_params = {},
    body = None,
    body_mode = None,
    headers = {},
):
    
    # Set authentication headers based on auth_type.
    if auth_type:
        auth_type = auth_type.lower()
        if (
            auth_type == "basic"
            and isinstance(auth_value, list)
            and len(auth_value) == 2
        ):
            creds = f"{auth_value[0]}:{auth_value[1]}"
            token = base64.b64encode(creds.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif auth_type == "jwt":
            headers["Authorization"] = f"JWT {auth_value}"
        elif auth_type == "form_login":
            headers["X-Form-Login"] = auth_value
        elif auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key":
            headers["x-api-key"] = auth_value
    return await _send_request(
        method,
        url,
        query_params=query_params,
        body=body,
        body_mode=body_mode,
        headers=headers,
    )

def file_logger(logging_string: str) -> None:
    api_logs_path = "api_logs.log"
    with open(api_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + " \\n")
        
async def log_request(request: httpx.Request) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    log_data = {
        "request_data": {
            "timestamp": timestamp,
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": (
                request.content.decode("utf-8", errors="ignore")
                if request.content
                else None
            ),
        }
    }
    file_logger(json.dumps(log_data))

async def log_response(response: httpx.Response) -> None:
    """
    Log details of the incoming HTTP response.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        body_bytes = await response.aread()
        body = body_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        import traceback

        traceback.print_exc()
        body = f"Failed to read response: {e}"
    log_data = {
        "response_data": {
            "timestamp": timestamp,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }
    }
    file_logger(json.dumps(log_data))
    
async def _send_request(
    method: str,
    url: str,
    *,
    query_params = None,
    body = None,
    body_mode = None,
    headers = None,
):
    query_params = query_params or {}
    headers = headers.copy() if headers else {}
    req_kwargs = {"params": query_params}

    if body_mode == "multipart" and body:
        form = httpx.FormData()
        for key, value in body.items():
            form.add_field(key, value)
        req_kwargs["data"] = form

    elif body_mode == "urlencoded" and body:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req_kwargs["data"] = body

    elif body_mode == "raw" and body:
        req_kwargs["content"] = body

    elif body_mode == "binary" and body:
        headers.setdefault("Content-Type", "application/octet-stream")
        req_kwargs["content"] = body

    elif body_mode == "json" and body:
        headers.setdefault("Content-Type", "application/json")
        req_kwargs["json"] = body

    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            event_hooks={"request": [log_request], "response": [log_response]},
            timeout=httpx.Timeout(5.0),
        ) as client:
            response = await client.request(method, url, headers=headers, **req_kwargs)
            response.raise_for_status()
            duration = time.perf_counter() - start_time

            try:
                parsed_body = response.json()
            except Exception as e:
                import traceback

                traceback.print_exc()
                parsed_body = response.text or ""
            result = {
                "status_code": response.status_code,
                "status_type": determine_status_type(response.status_code),
                "body": parsed_body,
            }
            # Minify the JSON response and replace double quotes with single quotes.
            result_str = json.dumps(result, separators=(",", ":")).replace('"', "'")
            return result_str, duration

    except httpx.HTTPStatusError as e:
        duration = time.perf_counter() - start_time
        error_data = await handle_error_response(e)
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration

    except Exception as e:
        import traceback

        traceback.print_exc()
        duration = time.perf_counter() - start_time
        error_data = {"error": str(e), "status_code": None, "status_type": "failure"}
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration

def determine_status_type(status_code: int) -> str:
    """
    Categorize the HTTP status code.
    """
    if 200 <= status_code < 300:
        return "success"
    elif 300 <= status_code < 400:
        return "redirect"
    elif 400 <= status_code < 500:
        return "client_error"
    elif 500 <= status_code < 600:
        return "server_error"
    return "unknown"


async def handle_error_response(e: httpx.HTTPStatusError) -> dict:

    try:
        error_detail = e.response.json()
    except Exception as ex:
        import traceback

        traceback.print_exc()
        error_detail = e.response.text or "No details"
    return {
        "error": str(e),
        "error_detail": error_detail,
        "status_code": e.response.status_code,
        "status_type": determine_status_type(e.response.status_code),
    }


''',

    "captcha_solver": '''
async def captcha_solver(
    browser_manager,
    captcha_type,
):
    try:
        page = await browser_manager.get_current_page()
        captcha_solver = getattr(playwright_recaptcha, captcha_type)
        async with captcha_solver.AsyncSolver(page) as solver:
            page = await browser_manager.get_current_page()
            token = await solver.solve_recaptcha()
        score_pattern = re.compile(r"Your score is: (\d\.\d)")
        score_locator = page.get_by_text(score_pattern)
        return True
    except Exception as e:

        traceback.print_exc()
        return False
''',

    "bulk_select_option" : '''

async def bulk_select_option(
    browser_manager,
    entries,
) :
    results: List[Dict[str, str]] = []

    for entry in entries:
        if len(entry) != 2:
            continue
        result = await select_option(browser_manager, (entry[0], entry[1]))
        if isinstance(result, str):
            if "new elements have appeared in view" in result and "success" in result.lower():
                success_part = result.split(". \\nAs a consequence")[0]
                results.append({"selector": entry[0], "result": success_part})
            else:
                results.append({"selector": entry[0], "result": result})
        else:
            results.append({"selector": entry[0], "result": str(result)})
    return results

async def select_option(
    browser_manager,
    entry,
):
    selector: str = entry[0]
    option_value: str = entry[1]

    # If the selector doesn't contain md=, wrap it accordingly.
    if "md=" not in selector:
        selector = f"[md='{selector}']"

    page = await browser_manager.get_current_page()
    if page is None:
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str) -> None:
        nonlocal dom_changes_detected
        dom_changes_detected = changes

    subscribe(detect_dom_changes)
    result = await do_select_option(browser_manager, page, selector, option_value)
    # Wait for page to stabilize after selection
    await browser_manager.wait_for_load_state_if_enabled(page=page)
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    # Simply return the detailed message
    return result["detailed_message"]

    
async def do_select_option(browser_manager, page: Page, selector: str, option_value: str) -> dict[str, str]:
   
    try:
        # Part 1: Find the element and get its properties
        element, properties = await find_element_select_type(browser_manager, page, selector)
        if not element:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        # Part 2: Interact with the element to select the option
        return await interact_with_element_select_type(browser_manager, page, element, selector, option_value, properties)

    except Exception as e:
        traceback.print_exc()
        error = f"Error selecting option in selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}


async def find_element_select_type(browser_manager, page: Page, selector: str) :
    element = await browser_manager.find_element(selector, page, element_name="select_option")
    if not element:
        return None, {}
    # Get element properties to determine the best selection strategy
    tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
    element_role = await element.evaluate("el => el.getAttribute('role') || ''")
    element_type = await element.evaluate("el => el.type || ''")
    element_outer_html = await get_element_outer_html(element, page)

    properties = {
        "tag_name": tag_name,
        "element_role": element_role,
        "element_type": element_type,
        "element_outer_html": element_outer_html,
    }
    return element, properties

async def interact_with_element_select_type(
    page: Page,
    element: ElementHandle,
    selector: str,
    option_value: str,
    properties: dict,
) -> dict[str, str]:
  
    tag_name = properties["tag_name"]
    element_role = properties["element_role"]
    element_type = properties["element_type"]
    element_outer_html = properties["element_outer_html"]
    element_attributes = properties["element_attributes"]

    # Strategy 1: Standard HTML select element
    if tag_name == "select":
        await element.select_option(value=option_value)
        await page.wait_for_load_state("domcontentloaded", timeout=1000)
        success_msg = f"Success. Option '{option_value}' selected in the dropdown with selector '{selector}'"
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
        }

    # Strategy 2: Input elements (text, number, etc.)
    elif tag_name in ["input", "button"]:
        input_roles = ["combobox", "listbox", "dropdown", "spinner", "select"]
        input_types = [
            "number",
            "range",
            "combobox",
            "listbox",
            "dropdown",
            "spinner",
            "select",
            "option",
        ]

        if element_type in input_types or element_role in input_roles:
            await element.click()
            try:
                await element.fill(option_value)
            except Exception as e:
                await element.type(option_value)

            if "lwc" in str(element) and "placeholder" in str(element):
                await asyncio.sleep(0.5)
                await press_key_combination("ArrowDown+Enter")
            else:
                await element.press("Enter")

            await page.wait_for_load_state("domcontentloaded", timeout=1000)

            success_msg = f"Success. Value '{option_value}' set in the input with selector '{selector}'"
            return {
                "summary_message": success_msg,
                "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
            }

    # Strategy 3: Generic click and select approach for all other elements
    # Click to open the dropdown
    await element.click()
    await page.wait_for_timeout(300)  # Short wait for dropdown to appear

    # Try to find and click the option by text content
    try:
        # Use a simple text-based selector that works in most cases
        option_selector = f"text={option_value}"
        await page.click(option_selector, timeout=2000)
        await page.wait_for_load_state("domcontentloaded", timeout=1000)

        success_msg = f"Success. Option '{option_value}' selected by text content"
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
        }
    except Exception as e:
        traceback.print_exc()
        # If all attempts fail, report failure
        error = f"Error: Option '{option_value}' not found in the element with selector '{selector}'. Try clicking the element first and then select the option."
        return {"summary_message": error, "detailed_message": error}
''',

    "bulk_set_date_time_value": '''
async def bulk_set_date_time_value(
    entries
    ): 
    results: List[dict[str, str]] = []  # noqa: UP006
    for entry in entries:
        result = await set_date_time_value(entry)  # Use dictionary directly
        results.append({"selector": entry[0], "result": result})

    return results

async def set_date_time_value(
    browser_manager,
    entry
):
    selector: str = entry[0]
    input_value: str = entry[1]

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)
    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    result = await do_set_date_time_value(browser_manager, page, selector, input_value)
    await asyncio.sleep(0.1)  # sleep to allow the mutation observer to detect changes
    unsubscribe(detect_dom_changes)

    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}. As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of setting input value '{input_value}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_set_date_time_value(browser_manager, page: Page, selector: str, input_value: str) -> dict[str, str]:
    try:
        element = await browser_manager.find_element(selector, page, element_name="enter_date_time")
        if element is None:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        # Get the element's tag name and type to determine how to interact with it
        tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
        input_type = await element.evaluate("el => el.type")

        if tag_name == "input" and input_type in ["date", "time", "datetime-local"]:
            # For date, time, or datetime-local inputs, set the value directly
            await element.fill(input_value)
            element_outer_html = await get_element_outer_html(element, page)
            success_msg = f"Success. Value '{input_value}' set in the input with selector '{selector}'"
            return {
                "summary_message": success_msg,
                "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
            }
        else:
            error = f"Error: Input type '{input_type}' not supported for setting value."
            return {"summary_message": error, "detailed_message": error}
    except Exception as e:
        traceback.print_exc()
        error = f"Error setting input value in selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}
''',

    "bulk_enter_text" : '''
async def bulk_enter_text(
    browser_manager,
    entries,
):
    results: List[Dict[str, str]] = []
    for entry in entries:
        if len(entry) != 2:
            continue
        result = await entertext(browser_manager, (entry[0], entry[1]))  # Create tuple with explicit values
        results.append({"selector": entry[0], "result": result})

    return results

async def custom_fill_element(page: Page, selector: str, text_to_enter: str) -> None:
    selector = f"{selector}"  # Ensures the selector is treated as a string
    try:
        js_code = """(inputParams) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const selector = inputParams.selector;
            let text_to_enter = inputParams.text_to_enter.trim();

            // Start by searching in the regular document (DOM)
            const element = findElementInShadowDOMAndIframes(document, selector);

            if (!element) {
                throw new Error(`Element not found: ${selector}`);
            }

            // Set the value for the element
            element.value = "";
            element.value = text_to_enter;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));

            return `Value set for ${selector}`;
        }"""

        result = await page.evaluate(
            get_js_with_element_finder(js_code),
            {"selector": selector, "text_to_enter": text_to_enter},
        )
    except Exception as e:
        traceback.print_exc()
        raise

async def entertext(
    browser_manager,
    entry
) :
    selector: str = entry[0]
    text_to_enter: str = entry[1]

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    page = await browser_manager.get_current_page()
    # await page.route("**/*", block_ads)
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    await page.evaluate(
        get_js_with_element_finder(
            """
        (selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const element = findElementInShadowDOMAndIframes(document, selector);
            if (element) {
                element.value = '';
            } else {
                console.error('Element not found:', selector);
            }
        }
        """
        ),
        selector,
    )

    result = await do_entertext(browser_manager, page, selector, text_to_enter)
    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}. As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of entering text {text_to_enter} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]


async def do_entertext(browser_manager, page: Page, selector: str, text_to_enter: str, use_keyboard_fill: bool = True) -> dict[str, str]:
    try:
        elem = await browser_manager.find_element(selector, page, element_name="entertext")
        # Initialize selector logger with proof path
        if not elem:
            error = f"Error: Selector {selector} not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}
        else:
            # Get element properties to determine the best selection strategy
            tag_name = await elem.evaluate("el => el.tagName.toLowerCase()")
            element_role = await elem.evaluate("el => el.getAttribute('role') || ''")
            element_type = await elem.evaluate("el => el.type || ''")
            input_roles = ["combobox", "listbox", "dropdown", "spinner", "select"]
            input_types = [
                "range",
                "combobox",
                "listbox",
                "dropdown",
                "spinner",
                "select",
                "option",
            ]
            if element_role in input_roles or element_type in input_types:
                properties = {
                    "tag_name": tag_name,
                    "element_role": element_role,
                    "element_type": element_type,
                    "element_outer_html": await get_element_outer_html(elem, page),
                }
                return await interact_with_element_select_type(page, elem, selector, text_to_enter, properties)

        element_outer_html = await get_element_outer_html(elem, page)

        if use_keyboard_fill:
            await elem.focus()
            await asyncio.sleep(0.01)
            await press_key_combination("Control+A")
            await asyncio.sleep(0.01)
            await press_key_combination("Delete")
            await asyncio.sleep(0.01)
            await page.keyboard.type(text_to_enter, delay=1)
        else:
            await custom_fill_element(page, selector, text_to_enter)

        await elem.focus()
        await browser_manager.wait_for_load_state_if_enabled(page=page)

        success_msg = f'Success. Text "{text_to_enter}" set successfully in the element with selector {selector}'
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg} and outer HTML: {element_outer_html}.",
        }
    except Exception as e:
        traceback.print_exc()
        error = f"Error entering text in selector {selector}."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}



FIND_ELEMENT_IN_SHADOW_DOM = """
const findElementInShadowDOMAndIframes = (parent, selector) => {
    // Try to find the element in the current context
    let element = parent.querySelector(selector);
    if (element) {
        return element; // Element found in the current context
    }

    // Search inside shadow DOMs and iframes
    const elements = parent.querySelectorAll('*');
    for (const el of elements) {
        // Search inside shadow DOMs
        if (el.shadowRoot) {
            element = findElementInShadowDOMAndIframes(el.shadowRoot, selector);
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
                element = findElementInShadowDOMAndIframes(iframeDocument, selector);
                if (element) {
                    return element; // Element found inside iframe
                }
            }
        }
    }
    return null; // Element not found
};
"""

TEMPLATES = {"FIND_ELEMENT_IN_SHADOW_DOM": FIND_ELEMENT_IN_SHADOW_DOM}

def get_js_with_element_finder(action_js_code: str) -> str:
    pattern = "/*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/"
    if pattern in action_js_code:
        return action_js_code.replace(pattern, TEMPLATES["FIND_ELEMENT_IN_SHADOW_DOM"])
    else:
        return action_js_code

''',

    "hover": '''
async def hover(
    browser_manager,
    selector,
    wait_before_execution = 0.0,
) :

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)

    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    result = await do_hover(page, selector, wait_before_execution)
    await asyncio.sleep(0.1)  # sleep to allow the mutation observer to detect changes

    await browser_manager.wait_for_load_state_if_enabled(page=page)

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"Success: {result['summary_message']}. As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. You may need further interaction. Get all_fields DOM to complete the interaction, if needed, also the tooltip data is already in the message"
    return result["detailed_message"]

async def do_hover(browser_manager, page: Page, selector: str, wait_before_execution: float) -> dict[str, str]:

    # Wait before execution if specified
    if wait_before_execution > 0:
        await asyncio.sleep(wait_before_execution)

    try:
        # Get the current page
        page = await browser_manager.get_current_page()

        # Wait for the page to load
        await browser_manager.wait_for_load_state_if_enabled(page=page)

        # Find the element
        element = await page.query_selector(selector)
        if not element:
            raise ValueError(f'Element with selector: "{selector}" not found')
        try:
            await element.scroll_into_view_if_needed(timeout=200)
        except Exception as e:
            traceback.print_exc()
            pass

        try:
            await element.wait_for_element_state("visible", timeout=200)
        except Exception as e:
            traceback.print_exc()
            # If the element is not visible, try to hover over it anyway
            pass

        element_tag_name = await element.evaluate("element => element.tagName.toLowerCase()")
        element_outer_html = await get_element_outer_html(element, page, element_tag_name)

        await perform_playwright_hover(element, selector)

        # Wait briefly to allow any tooltips to appear
        await asyncio.sleep(0.2)

        # Capture tooltip information
        tooltip_text = await get_tooltip_text(page)
        msg = f'Executed hover action on element with selector: "{selector}".'
        if tooltip_text:
            msg += f' Tooltip shown: "{tooltip_text}".'

        return {
            "summary_message": msg,
            "detailed_message": f"{msg} The hovered element's outer HTML is: {element_outer_html}.",
        }
    except Exception as e:
        traceback.print_exc()
        traceback.print_exc()
        msg = f'Unable to hover over element with selector: "{selector}" since the selector is invalid or the element is not interactable. Consider retrieving the DOM again.'
        return {"summary_message": msg, "detailed_message": f"{msg}. Error: {e}"}

async def get_tooltip_text(page: Page) -> str:
    # JavaScript code to find tooltip elements
    js_code = """
    () => {
        // Search for elements with role="tooltip"
        let tooltip = document.querySelector('[role="tooltip"]');
        if (tooltip && tooltip.innerText) {
            return tooltip.innerText.trim();
        }

        // Search for common tooltip classes
        let tooltipClasses = ['tooltip', 'ui-tooltip', 'tooltip-inner'];
        for (let cls of tooltipClasses) {
            tooltip = document.querySelector('.' + cls);
            if (tooltip && tooltip.innerText) {
                return tooltip.innerText.trim();
            }
        }

        return '';
    }
    """
    try:
        tooltip_text = await page.evaluate(js_code)
        return tooltip_text
    except Exception as e:
        traceback.print_exc()
        return ""

async def perform_playwright_hover(element: ElementHandle, selector: str) -> None:
    await element.hover(force=True, timeout=200)

''',
    
    "press_key_combination": '''
async def press_key_combination(
    browser_manager,
    key_combination,
):
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        raise ValueError("No active page found. OpenURL command opens a new page.")

    # Split the key combination if it's a combination of keys
    keys = key_combination.split("+")

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    # If it's a combination, hold down the modifier keys
    for key in keys[:-1]:  # All keys except the last one are considered modifier keys
        await page.keyboard.down(key)

    # Press the last key in the combination
    await page.keyboard.press(keys[-1])

    # Release the modifier keys
    for key in keys[:-1]:
        await page.keyboard.up(key)
    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots("press_key_combination_end", page)
    if dom_changes_detected:
        return f"Key {key_combination} executed successfully. As a consequence of this action, new elements have appeared in view:{dom_changes_detected}. This means that the action is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."

    return f"Key {key_combination} executed successfully"

''',

    "bulk_set_slider": '''
async def bulk_set_slider(
    entries,
):
    results: List[Dict[str, str]] = []
    for entry in entries:
        if len(entry) != 2:
            continue
        result = await setslider((entry[0], entry[1]))  # Create tuple with explicit values
        results.append({"selector": entry[0], "result": result})
    return results

async def setslider(
    browser_manager,
    entry
):
    selector: str = entry[0]
    value_to_set: str = entry[1]

    try:
        value_float = float(value_to_set)
    except ValueError:
        return f"Error: Invalid slider value '{value_to_set}'. Must be a number."

    if "md=" not in selector:
        selector = f"[md='{selector}']"
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore
    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)

    dom_changes_detected = None

    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    result = await do_setslider(browser_manager, page, selector, value_float)
    await asyncio.sleep(0.1)  # sleep to allow the mutation observer to detect changes

    await browser_manager.wait_for_load_state_if_enabled(page=page)
    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}. \\n As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of setting slider value {value_to_set} is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]

async def do_setslider(browser_manager, page: Page, selector: str, value_to_set: float) -> dict[str, str]:

    try:
        elem_handle = await browser_manager.find_element(selector, page, element_name="setslider")

        if elem_handle is None:
            error = f"Error: Selector {selector} not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        element_outer_html = await get_element_outer_html(elem_handle, page)

        # Get slider properties before setting value
        slider_props = await elem_handle.evaluate(
            """element => ({
            min: parseFloat(element.min) || 0,
            max: parseFloat(element.max) || 100,
            step: parseFloat(element.step) || 1,
            type: element.type
        })"""
        )

        if slider_props["type"] != "range":
            error = f"Error: Element is not a range input. Found type: {slider_props['type']}"
            return {"summary_message": error, "detailed_message": error}

        # Use the custom function to set the slider value
        await custom_set_slider_value(page, selector, value_to_set)

       
        await elem_handle.focus()
        await browser_manager.wait_for_load_state_if_enabled(page=page)
        success_msg = f"Success. Slider value {value_to_set} set successfully in the element with selector {selector}"
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg} and outer HTML: {element_outer_html}.",
        }

    except Exception as e:
        traceback.print_exc()
        error = f"Error setting slider value in selector {selector}."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}

async def custom_set_slider_value(page: Page, selector: str, value_to_set: float) -> None:
    selector = f"{selector}"  # Ensures the selector is treated as a string
    try:
        result = await page.evaluate(
            get_js_with_element_finder(
                """
            (inputParams) => {
                /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
                const { selector, value_to_set } = inputParams;
                const element = findElementInShadowDOMAndIframes(document, selector);
                if (!element) {
                    throw new Error(`Element not found: ${selector}`);
                }
                if (element.type !== 'range') {
                    throw new Error(`Element is not a range input: ${selector}`);
                }
                // Get min, max, and step values
                const min = parseFloat(element.min) || 0;
                const max = parseFloat(element.max) || 100;
                const step = parseFloat(element.step) || 1;
                // Clamp the value within the allowed range
                value_to_set = Math.max(min, Math.min(max, value_to_set));
                // Adjust value to the nearest step
                value_to_set = min + Math.round((value_to_set - min) / step) * step;
                // Set the value
                element.value = value_to_set;
                // Dispatch input and change events to simulate user interaction
                const inputEvent = new Event('input', { bubbles: true });
                const changeEvent = new Event('change', { bubbles: true });
                element.dispatchEvent(inputEvent);
                element.dispatchEvent(changeEvent);
                return `Value set for ${selector}`;
            }
            """
            ),
            {"selector": selector, "value_to_set": value_to_set},
        )
    except Exception as e:
        traceback.print_exc()
        raise
        
''',

    "execute_select_cte_query_sql": '''
async def execute_select_cte_query_sql(
    connection_string,
    query,
    schema_name = "",
    params = None,
):
    try:
        # Ensure only SELECT queries are allowed
        query_lower = query.strip().lower()
        if not (query_lower.startswith("select") or query_lower.startswith("with")):
            raise ValueError("Only SELECT queries are allowed.")

        # Create the async engine
        engine: AsyncEngine = create_async_engine(connection_string, echo=False)

        async with engine.connect() as connection:  # type: AsyncConnection
            if schema_name:
                if engine.dialect.name == "postgresql":
                    await connection.execute(text(f"SET search_path TO {schema_name}"))
                elif engine.dialect.name in ["mysql", "mariadb"]:
                    await connection.execute(text(f"USE {schema_name}"))
                # For SQLite, schema_name setting is not applicable

            result = await connection.execute(text(query), params or {})
            rows = [dict(row) for row in result]
            return rows
    except SQLAlchemyError as e:
        traceback.print_exc()
        return {"error": str(e)}
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        await engine.dispose()

''',

    "wait_for_duration": '''
async def wait_for_duration(
    duration: float,
):
    try:
        # Validate input
        if not isinstance(duration, (int, float)):
            return {"status": "error", "message": "Duration must be a number"}

        duration = float(duration)
        if duration < 0:
            return {"status": "error", "message": "Duration cannot be negative"}

        if duration > 3600:
            return {"status": "error", "message": "Duration cannot exceed 3600 seconds"}

        # Perform the wait
        await asyncio.sleep(duration)
        return {"status": "success", "message": f"Waited for {duration} seconds"}

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": f"Wait operation failed: {str(e)}"}
''',

    "get_current_timestamp": '''
async def get_current_timestamp():
    from datetime import datetime

    # Get current timestamp
    current_timestamp = datetime.now().isoformat()
    return {"timestamp": current_timestamp}
''',

    "click_and_upload_file": '''
async def click_and_upload_file(
    browser_manager,
    entry,
) :
    selector: str = entry[0]
    file_path: str = entry[1]

    if "md=" not in selector:
        selector = f"[md='{selector}']"

    # Create and use the PlaywrightManager
    page = await browser_manager.get_current_page()
    if page is None:  # type: ignore
        return "Error: No active page found. OpenURL command opens a new page."

    function_name = inspect.currentframe().f_code.co_name  # type: ignore

    await browser_manager.take_screenshots(f"{function_name}_start", page)
    await browser_manager.highlight_element(selector)
    
    dom_changes_detected = None
    
    def detect_dom_changes(changes: str):  # type: ignore
        nonlocal dom_changes_detected
        dom_changes_detected = changes  # type: ignore

    result = await click_and_upload(page, selector, file_path)
    await asyncio.sleep(0.1)  # sleep for 100ms to allow the mutation observer to detect changes

    await browser_manager.take_screenshots(f"{function_name}_end", page)

    if dom_changes_detected:
        return f"{result['detailed_message']}. As a consequence of this action, new elements have appeared in view: {dom_changes_detected}. This means that the action of uploading file '{file_path}' is not yet executed and needs further interaction. Get all_fields DOM to complete the interaction."
    return result["detailed_message"]

async def click_and_upload(browser_manager, page: Page, selector: str, file_path: str) -> dict[str, str]:
    try:
        element = await browser_manager.find_element(selector, page, element_name="upload_file")
        if element is None:
            error = f"Error: Selector '{selector}' not found. Unable to continue."
            return {"summary_message": error, "detailed_message": error}

        # Check if element is a file input
        element_type = await element.evaluate("el => el.type")
        if element_type != "file":
        
            # Use FileChooser API
            async with page.expect_file_chooser() as fc_info:
                await element.click()

            file_chooser = await fc_info.value
            await file_chooser.set_files(file_path)

            error = f"Error: Element is not a file input. Found type: {element_type}, pass selectors with type as file"
            return {"summary_message": error, "detailed_message": error}

        await element.set_input_files(file_path)
        element_outer_html = await get_element_outer_html(element, page)
        success_msg = f"Success. File '{file_path}' uploaded using the input with selector '{selector}'"
        return {
            "summary_message": success_msg,
            "detailed_message": f"{success_msg}. Outer HTML: {element_outer_html}",
        }

    except Exception as e:
        traceback.print_exc()
        error = f"Error uploading file to selector '{selector}'."
        return {"summary_message": error, "detailed_message": f"{error} Error: {e}"}

'''

}


def get_tool_code(tool_name):
    try:
        return TOOLS_LIBRARY[tool_name]
    except KeyError:
        available_functions = list(TOOLS_LIBRARY.keys())
        raise KeyError(
            f"Function '{tool_name}' not found. "
            f"Available functions: {available_functions}"
        ) from None
    

