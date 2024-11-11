import asyncio
import json
import os
import shutil
import tempfile
import time
import zipfile
from io import BytesIO
from typing import Dict, List, Optional, Union

import httpx
from testzeus_hercules.config import (
    get_browser_type,
    get_cdp_config,
    get_screen_shot_path,
    should_capture_network,
    should_record_video,
    should_run_headless,
    should_take_screenshots,
)
from testzeus_hercules.core.notification_manager import NotificationManager
from testzeus_hercules.core.ui_manager import UIManager
from testzeus_hercules.utils.dom_mutation_observer import (
    dom_mutation_change_detected,
    handle_navigation_for_mutation_observer,
)
from testzeus_hercules.utils.js_helper import beautify_plan_message, escape_js_message
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType
from playwright.async_api import BrowserContext, BrowserType, Page, Playwright
from playwright.async_api import async_playwright as playwright

# Ensures that playwright does not wait for font loading when taking screenshots.
# Reference: https://github.com/microsoft/playwright/issues/28995
os.environ["PW_TEST_SCREENSHOT_NO_FONTS_READY"] = "1"


class PlaywrightManager:
    """
    A singleton class to manage Playwright instances and browsers.

    Attributes:
        browser_type (str): The type of browser to use ('chromium', 'firefox', 'webkit').
        isheadless (bool): Flag to launch the browser in headless mode or not.
        cdp_config (Optional[dict]): Configuration for connecting over CDP.
        record_video (bool): Flag to enable video recording.
        video_dir (str): Directory to save video recordings.

    The class ensures only one instance of itself, Playwright, and the browser is created during the application lifecycle.
    """

    _homepage = "https://www.google.com"
    _instance = None

    def __new__(cls, *args, **kwargs) -> "PlaywrightManager":
        """
        Ensures that only one instance of PlaywrightManager is created (singleton pattern).
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.__initialized: bool = False
            logger.debug("PlaywrightManager instance created.")
        return cls._instance

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = False,
        gui_input_mode: bool = False,
        stake_id: Optional[str] = None,
        screenshots_dir: str = "",
        take_screenshots: bool = False,
        cdp_config: Optional[Dict] = None,
        record_video: bool = False,
        video_dir: Optional[str] = None,
        log_requests_responses: bool = False,
        request_response_log_file: Optional[str] = None,
    ):
        """
        Initializes the PlaywrightManager with the specified browser type and headless mode.
        Initialization occurs only once due to the singleton pattern.

        Args:
            browser_type (str, optional): The type of browser to use. Defaults to "chromium".
            headless (bool, optional): Flag to launch the browser in headless mode or not. Defaults to False.
            cdp_config (Optional[dict], optional): Configuration for connecting over CDP.
            record_video (bool, optional): Flag to enable video recording. Defaults to False.
            video_dir (Optional[str], optional): Directory to save video recordings. Defaults to None.
        """
        if self.__initialized and self._playwright is not None:
            return
        self.__initialized = True
        self.browser_type = get_browser_type() or browser_type
        self.isheadless = should_run_headless() or headless
        self.cdp_config = get_cdp_config() or cdp_config
        self.notification_manager = NotificationManager()
        self.user_response_future: Optional[asyncio.Future[str]] = None
        self.ui_manager: Optional[UIManager] = UIManager() if gui_input_mode else None
        self._take_screenshots = should_take_screenshots() or take_screenshots
        self.stake_id = stake_id
        self._screenshots_dir = os.path.join(
            get_screen_shot_path(self.stake_id) or screenshots_dir, "screenshots"
        )
        self._record_video = should_record_video() or record_video
        self._video_dir = os.path.join(
            get_screen_shot_path(self.stake_id) or video_dir, "videos"
        )
        self._playwright: Optional[Playwright] = None
        self._browser_context: Optional[BrowserContext] = None
        self.__async_initialize_done = False
        self._latest_screenshot_bytes: Optional[bytes] = (
            None  # Stores the latest screenshot bytes
        )
        self._latest_video_path: Optional[str] = (
            None  # Stores the latest video file path
        )
        self.log_requests_responses = should_capture_network() or log_requests_responses
        self.request_response_log_file = os.path.join(
            get_screen_shot_path(self.stake_id) or request_response_log_file,
            "network_logs.json",
        )
        self.request_response_logs: List[Dict] = []

        # Extension caching directory
        self._extension_cache_dir = os.path.join(
            ".", ".cache", "browser", self.browser_type, "extension"
        )
        self._extension_path: Optional[str] = None

    async def async_initialize(self) -> None:
        """
        Asynchronously initialize necessary components and handlers for the browser context.
        """
        if self.__async_initialize_done:
            return

        # Step 1: Ensure Playwright is started and browser context is created
        await self.start_playwright()
        await self.ensure_browser_context()

        # Step 2: Deferred setup of handlers
        await self.setup_handlers()

        # Step 3: Navigate to homepage
        await self.go_to_homepage()

        self.__async_initialize_done = True

    async def ensure_browser_context(self) -> None:
        """
        Ensure that a browser context exists, creating it if necessary.
        """
        if self._browser_context is None:
            await self.create_browser_context()

    async def setup_handlers(self) -> None:
        """
        Setup various handlers after the browser context has been ensured.
        """
        await self.set_overlay_state_handler()
        await self.set_user_response_handler()
        await self.set_navigation_handler()

    async def start_playwright(self) -> None:
        """
        Starts the Playwright instance if it hasn't been started yet. This method is idempotent.
        """
        if not self._playwright:
            self._playwright = await playwright().start()

    async def stop_playwright(self) -> None:
        """
        Stops the Playwright instance and resets it to None. This method should be called to clean up resources.
        """
        # Close the browser context if it's initialized
        await self.close_browser_context()

        # Stop the Playwright instance if it's initialized
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def prepare_extension(self) -> None:
        """
        Prepares the browser extension by downloading and caching it if necessary.
        """
        if self.browser_type == "chromium":
            extension_url = "https://github.com/gorhill/uBlock/releases/download/1.61.0/uBlock0_1.61.0.chromium.zip"
            extension_file_name = "uBlock0_1.61.0.chromium.zip"
            extension_dir_name = "uBlock0_1.61.0.chromium"
        elif self.browser_type == "firefox":
            extension_url = "https://addons.mozilla.org/firefox/downloads/file/4359936/ublock_origin-1.60.0.xpi"
            extension_file_name = "uBlock0_1.60.0.firefox.xpi"
            extension_dir_name = "uBlock0_1.60.0.firefox"
        else:
            logger.error(f"Unsupported browser type for extension: {self.browser_type}")
            return

        extension_dir = self._extension_cache_dir
        extension_file_path = os.path.join(extension_dir, extension_file_name)

        if not os.path.exists(extension_dir):
            os.makedirs(extension_dir)

        if not os.path.exists(extension_file_path):
            # Download the extension
            logger.info(f"Downloading extension from {extension_url}")
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(extension_url)
                if response.status_code == 200:
                    with open(extension_file_path, "wb") as f:
                        f.write(response.content)
                    logger.info(
                        f"Extension downloaded and saved to {extension_file_path}"
                    )
                else:
                    logger.error(
                        f"Failed to download extension from {extension_url}, status {response.status_code}"
                    )
                    return

        if self.browser_type == "chromium":
            # Unzip the extension
            extension_unzip_dir = os.path.join(extension_dir, extension_dir_name)
            if not os.path.exists(extension_unzip_dir):
                with zipfile.ZipFile(extension_file_path, "r") as zip_ref:
                    zip_ref.extractall(extension_unzip_dir)

            self._extension_path = extension_unzip_dir + "/uBlock0.chromium"
        elif self.browser_type == "firefox":
            # For Firefox, use the .xpi file directly
            self._extension_path = extension_file_path

    async def create_browser_context(self) -> None:
        user_dir: str = os.environ.get("BROWSER_STORAGE_DIR", "")
        disable_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            "--disable-popup-blocking",
            "--disable-notifications",
        ]
        if self.cdp_config:
            # Connect over CDP
            logger.info("Connecting over CDP with provided configuration.")
            endpoint_url = self.cdp_config.get("endpoint_url")
            if not endpoint_url:
                raise ValueError("CDP config must include 'endpoint_url'.")
            browser_type = getattr(self._playwright, self.browser_type)
            _browser = await browser_type.connect_over_cdp(endpoint_url)
            if self._record_video:
                # Record video in CDP mode
                context_options = {
                    "record_video_dir": self._video_dir,
                    "record_video_size": {"width": 1280, "height": 720},
                }
                self._browser_context = await _browser.new_context(**context_options)
                page = await self._browser_context.new_page()
                await page.goto("https://www.google.com")
                logger.info("Recording video in CDP mode.")
            else:
                self._browser_context = _browser.contexts[0]
        else:
            if self.browser_type != "chromium":
                disable_args = []
            browser_type = getattr(self._playwright, self.browser_type)
            # Prepare the extension
            await self.prepare_extension()
            if self._record_video:
                await self._launch_browser_with_video(
                    browser_type, user_dir, disable_args
                )
            else:
                await self._launch_persistent_browser(
                    browser_type, user_dir, disable_args
                )

    async def _launch_persistent_browser(
        self, browser_type: BrowserType, user_dir: str, disable_args=[]
    ) -> None:
        logger.info(f"Launching {self.browser_type} with user dir: {user_dir}")
        try:
            browser_context_kwargs = {
                "headless": self.isheadless,
                "args": disable_args,
                "no_viewport": True,
            }
            disable_args.append(f"--disable-extensions-except={self._extension_path}")
            disable_args.append(f"--load-extension={self._extension_path}")
            if self.browser_type == "firefox" and self._extension_path is not None:
                browser_context_kwargs["firefox_user_prefs"] = {
                    "xpinstall.signatures.required": False,  # Allow unsigned extensions
                    "extensions.autoDisableScopes": 0,  # Prevent auto-disable for the extension
                    "extensions.enabledScopes": 15,  # Enable all scopes
                    "extensions.installDistroAddons": False,  # Disable distro add-ons
                    "extensions.update.enabled": False,  # Prevent automatic extension updates
                    "browser.shell.checkDefaultBrowser": False,  # Prevent default browser check
                    "browser.startup.homepage": "about:blank",  # Start with a blank page
                    "toolkit.telemetry.reportingpolicy.firstRun": False,  # Disable telemetry
                    "extensions.webextensions.userScripts.enabled": True,  # Allow web extensions without prompt
                }

            self._browser_context = await browser_type.launch_persistent_context(
                user_dir, **browser_context_kwargs
            )
        except Exception as e:
            await self._handle_launch_exception(e, user_dir, browser_type, disable_args)

    async def _launch_browser_with_video(
        self, browser_type: BrowserType, user_dir: str, disable_args=[]
    ) -> None:
        logger.info(f"Launching {self.browser_type} with video recording enabled.")
        # Copy user data to a temporary directory
        temp_user_dir = tempfile.mkdtemp(prefix="playwright-user-data-")
        if user_dir and os.path.exists(user_dir):
            shutil.copytree(user_dir, temp_user_dir, dirs_exist_ok=True)
        else:
            user_dir = temp_user_dir

        try:
            if self.browser_type == "chromium" and self._extension_path is not None:
                disable_args.append(
                    f"--disable-extensions-except={self._extension_path}"
                )
                disable_args.append(f"--load-extension={self._extension_path}")
            browser = await browser_type.launch(
                headless=self.isheadless,
                args=disable_args,
            )
            context_options = {
                "record_video_dir": self._video_dir,
                "record_video_size": {"width": 1280, "height": 720},
            }
            if self.browser_type == "firefox" and self._extension_path is not None:
                context_options["extensions"] = [self._extension_path]
            self._browser_context = await browser.new_context(**context_options)
        except Exception as e:
            logger.error(f"Failed to launch browser with video recording: {e}")
            raise e

    async def _handle_launch_exception(
        self, e: Exception, user_dir: str, browser_type, args=None
    ) -> None:
        if "Target page, context or browser has been closed" in str(e):
            new_user_dir = tempfile.mkdtemp()
            logger.error(
                f"Failed to launch persistent context with user dir {user_dir}: {e}. "
                f"Trying to launch with a new user dir {new_user_dir}"
            )
            self._browser_context = await browser_type.launch_persistent_context(
                new_user_dir,
                headless=self.isheadless,
                args=args or [],
                no_viewport=True,
            )
        elif "Chromium distribution 'chrome' is not found" in str(e):
            raise ValueError(
                "Chrome is not installed on this device. Install Google Chrome or install playwright using 'playwright install chrome'. Refer to the readme for more information."
            ) from None
        else:
            raise e from None

    async def get_browser_context(self) -> BrowserContext:
        """
        Returns the existing browser context, or creates a new one if it doesn't exist.
        """
        await self.ensure_browser_context()
        if self._browser_context is None:
            raise RuntimeError("Browser context is not available.")
        return self._browser_context

    async def setup_request_response_logging(self, page: Page) -> None:
        """
        Sets up logging for request and response events.

        Args:
            page (Page): The Playwright Page object.
        """
        if not self.log_requests_responses:
            return

        # Handler for logging request details
        page.on("request", self.log_request)

        # Handler for logging response details
        page.on("response", self.log_response)

    def log_request(self, request) -> None:
        """
        Logs request details and writes to the file incrementally.

        Args:
            request: The Playwright Request object.
        """
        log_entry = {
            "type": "request",
            "timestamp": time.time(),
            "method": request.method,
            "url": request.url,
            "headers": request.headers,
            "post_data": request.post_data or None,
        }
        self.write_log_entry_to_file(log_entry)

    def log_response(self, response) -> None:
        """
        Logs response details and writes to the file incrementally.

        Args:
            response: The Playwright Response object.
        """
        log_entry = {
            "type": "response",
            "timestamp": time.time(),
            "status": response.status,
            "url": response.url,
            "headers": response.headers,
            "body": None,  # You can add logic to capture the response body if needed
        }
        self.write_log_entry_to_file(log_entry)

    def write_log_entry_to_file(self, log_entry: Dict) -> None:
        """
        Writes a single log entry to the log file in an incremental manner.

        Args:
            log_entry (dict): The log entry to write.
        """
        try:
            with open(self.request_response_log_file, "a", encoding="utf-8") as file:
                file.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write request/response log to file: {e}")

    async def get_current_url(self) -> Optional[str]:
        """
        Get the current URL of the current page.

        Returns:
            Optional[str]: The current URL if any.
        """
        try:
            current_page: Page = await self.get_current_page()
            return current_page.url
        except Exception as e:
            logger.warning(f"Failed to get current URL: {e}")
        return None

    async def get_current_page(self) -> Page:
        """
        Get the current page of the browser.

        Returns:
            Page: The current page if any.
        """
        try:
            browser_context = await self.get_browser_context()
            # Filter out closed pages
            pages: list[Page] = [
                page for page in browser_context.pages if not page.is_closed()
            ]
            page: Optional[Page] = pages[-1] if pages else None
            logger.debug(f"Current page: {page.url if page else None}")
            if page is not None:
                return page
            else:
                page = await browser_context.new_page()
                await self.setup_request_response_logging(page)
                return page
        except Exception as e:
            logger.warning(
                f"Error getting current page: {e}. Creating a new browser context."
            )
            self._browser_context = None
            await self.ensure_browser_context()
            browser_context = await self.get_browser_context()
            pages = [page for page in browser_context.pages if not page.is_closed()]
            if pages:
                return pages[-1]
            else:
                return await browser_context.new_page()

    async def close_all_tabs(self, keep_first_tab: bool = True) -> None:
        """
        Closes all tabs in the browser context, except for the first tab if `keep_first_tab` is set to True.

        Args:
            keep_first_tab (bool, optional): Whether to keep the first tab open. Defaults to True.
        """
        browser_context = await self.get_browser_context()
        pages: list[Page] = browser_context.pages
        if keep_first_tab:
            pages_to_close = pages[1:]
        else:
            pages_to_close = pages
        for page in pages_to_close:
            await page.close()

    async def close_except_specified_tab(self, page_to_keep: Page) -> None:
        """
        Closes all tabs in the browser context, except for the specified tab.

        Args:
            page_to_keep (Page): The Playwright page object representing the tab that should remain open.
        """
        browser_context = await self.get_browser_context()
        for page in browser_context.pages:
            if page != page_to_keep:
                await page.close()

    async def go_to_homepage(self) -> None:
        page: Page = await self.get_current_page()
        await page.goto(self._homepage)

    async def set_navigation_handler(self) -> None:
        page: Page = await self.get_current_page()

        # Set event listeners for the main page
        if self.ui_manager:
            page.on("domcontentloaded", self.ui_manager.handle_navigation)
        page.on("domcontentloaded", handle_navigation_for_mutation_observer)

        # Function to set event listeners for all iframes
        async def set_iframe_navigation_handlers() -> None:
            for frame in page.frames:
                # Check if the frame is not the main frame
                if frame != page.main_frame:
                    frame.on(
                        "domcontentloaded", handle_navigation_for_mutation_observer
                    )

        # Set event listeners for current iframes
        await set_iframe_navigation_handlers()

        # Expose the function for DOM mutation change detection
        await page.expose_function(
            "dom_mutation_change_detected", dom_mutation_change_detected
        )

        # Optionally, you can add a listener to capture new iframes as they are added dynamically
        page.on(
            "frameattached",
            lambda frame: frame.on(
                "domcontentloaded", handle_navigation_for_mutation_observer
            ),
        )

    async def set_overlay_state_handler(self) -> None:
        logger.debug("Setting overlay state handler")
        context = await self.get_browser_context()
        await context.expose_function(
            "overlay_state_changed", self.overlay_state_handler
        )
        await context.expose_function(
            "show_steps_state_changed", self.show_steps_state_handler
        )

    async def overlay_state_handler(self, is_collapsed: bool) -> None:
        page = await self.get_current_page()
        if self.ui_manager:
            self.ui_manager.update_overlay_state(is_collapsed)
            if not is_collapsed:
                await self.ui_manager.update_overlay_chat_history(page)

    async def show_steps_state_handler(self, show_details: bool) -> None:
        page = await self.get_current_page()
        if self.ui_manager:
            await self.ui_manager.update_overlay_show_details(show_details, page)

    async def set_user_response_handler(self) -> None:
        context = await self.get_browser_context()
        await context.expose_function("user_response", self.receive_user_response)

    async def notify_user(
        self, message: str, message_type: MessageType = MessageType.STEP
    ) -> None:
        """
        Notify the user with a message.

        Args:
            message (str): The message to notify the user with.
            message_type (MessageType, optional): The type of message. Defaults to MessageType.STEP.
        """
        message = message.strip(":,")
        if message_type == MessageType.PLAN:
            message = beautify_plan_message(message)
            message = "Plan:\n" + message
        elif message_type == MessageType.STEP:
            if "confirm" in message.lower():
                message = "Verify: " + message
            else:
                message = "Next step: " + message
        elif message_type == MessageType.QUESTION:
            message = "Question: " + message
        elif message_type == MessageType.ANSWER:
            message = "Response: " + message
        elif message_type == MessageType.DONE:
            message = "TERMINATE: " + message

        safe_message = escape_js_message(message)
        if self.ui_manager:
            self.ui_manager.new_system_message(safe_message, message_type)

        if self.ui_manager:
            if not self.ui_manager.overlay_show_details and message_type not in (
                MessageType.PLAN,
                MessageType.QUESTION,
                MessageType.ANSWER,
                MessageType.INFO,
            ):
                return
            if self.ui_manager.overlay_show_details and message_type not in (
                MessageType.PLAN,
                MessageType.QUESTION,
                MessageType.ANSWER,
                MessageType.INFO,
                MessageType.STEP,
            ):
                return

            safe_message_type = escape_js_message(message_type.value)
            try:
                js_code = f"addSystemMessage({safe_message}, is_awaiting_user_response=false, message_type={safe_message_type});"
                page = await self.get_current_page()
                await page.evaluate(js_code)
            except Exception as e:
                logger.error(
                    f'Failed to notify user with message "{message}". This may resolve after the page loads: {e}'
                )
        msg = message_type
        if not isinstance(msg, str):
            msg = message_type.value  # type: ignore
        self.notification_manager.notify(message, str(msg))

    async def highlight_element(self, selector: str, add_highlight: bool) -> None:
        try:
            page: Page = await self.get_current_page()

            # Helper function to apply or remove highlight in both regular and shadow DOM
            async def highlight_in_shadow_dom(selector, add_highlight) -> None:
                if add_highlight:
                    # Apply highlight, including in Shadow DOM elements
                    await page.evaluate(
                        """(selector) => {
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

                            const element = findElementInShadowDOMAndIframes(document, selector);
                            if (element) {
                                element.classList.add('hercules-ui-automation-highlight');
                                element.addEventListener('animationend', () => {
                                    element.classList.remove('hercules-ui-automation-highlight');
                                });
                            }
                        }
                        """,
                        selector,
                    )
                    logger.debug(
                        f"Applied pulsating border to element with selector {selector} to indicate operation"
                    )
                else:
                    # Remove highlight from both regular and shadow DOM elements
                    await page.evaluate(
                        """(selector) => {
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

                            const element = findElementInShadowDOMAndIframes(document, selector);
                            if (element) {
                                element.classList.remove('hercules-ui-automation-highlight');
                            }
                        }
                        """,
                        selector,
                    )
                    logger.debug(
                        f"Removed pulsating border from element with selector {selector} after operation"
                    )

            # Call the helper function to apply or remove highlight
            await highlight_in_shadow_dom(selector, add_highlight)

        except Exception as e:
            # This is not significant enough to fail the operation
            logger.warning(f"An error occurred in highlight_element: {e}")

    async def receive_user_response(self, response: str) -> None:
        logger.debug(f"Received user response to system prompt: {response}")
        if self.user_response_future and not self.user_response_future.done():
            self.user_response_future.set_result(response)

    async def prompt_user(self, message: str) -> str:
        """
        Prompt the user with a message and wait for a response.

        Args:
            message (str): The message to prompt the user with.

        Returns:
            str: The user's response.
        """
        logger.debug(f'Prompting user with message: "{message}"')
        page = await self.get_current_page()

        if self.ui_manager:
            await self.ui_manager.show_overlay(page)
            self.log_system_message(message, MessageType.QUESTION)
            safe_message = escape_js_message(message)
            js_code = f"addSystemMessage({safe_message}, is_awaiting_user_response=true, message_type='question');"
            await page.evaluate(js_code)

        self.user_response_future = asyncio.Future()
        result = await self.user_response_future
        logger.info(f'User prompt response to "{message}": {result}')
        self.user_response_future = None
        if self.ui_manager:
            self.ui_manager.new_user_message(result)
        return result

    def set_take_screenshots(self, take_screenshots: bool) -> None:
        self._take_screenshots = take_screenshots

    def get_take_screenshots(self) -> bool:
        return self._take_screenshots

    def set_screenshots_dir(self, screenshots_dir: str) -> None:
        self._screenshots_dir = screenshots_dir

    def get_screenshots_dir(self) -> str:
        return self._screenshots_dir

    async def take_screenshots(
        self,
        name: str,
        page: Optional[Page] = None,
        full_page: bool = True,
        include_timestamp: bool = True,
        load_state: str = "domcontentloaded",
        take_snapshot_timeout: int = 5 * 1000,
    ) -> None:
        if not self._take_screenshots:
            return
        if page is None:
            page = await self.get_current_page()

        screenshot_name = name

        if include_timestamp:
            screenshot_name = f"{screenshot_name}_{int(time.time_ns())}"
        screenshot_name += ".png"
        screenshot_path = os.path.join(self.get_screenshots_dir(), screenshot_name)
        try:
            await page.wait_for_load_state(
                state=load_state, timeout=take_snapshot_timeout
            )
            screenshot_bytes = await page.screenshot(
                path=screenshot_path,
                full_page=full_page,
                timeout=take_snapshot_timeout,
                caret="initial",
                scale="device",
            )
            self._latest_screenshot_bytes = (
                screenshot_bytes  # Store the latest screenshot bytes
            )
            logger.debug(f"Screenshot saved to: {screenshot_path}")
        except Exception as e:
            logger.error(
                f'Failed to take screenshot and save to "{screenshot_path}". Error: {e}'
            )

    async def get_latest_screenshot_stream(self) -> Optional[BytesIO]:
        """
        Retrieves the latest screenshot as a byte stream.

        Returns:
            Optional[BytesIO]: A BytesIO stream of the latest screenshot, or None if no screenshot is available.
        """
        if self._latest_screenshot_bytes:
            return BytesIO(self._latest_screenshot_bytes)
        else:
            logger.warning("No screenshot available.")
            return None

    async def get_latest_video_path(self) -> Optional[str]:
        """
        Retrieves the path to the latest video recording.

        Returns:
            Optional[str]: The file path of the latest video recording, or None if not available.
        """
        if self._latest_video_path and os.path.exists(self._latest_video_path):
            return self._latest_video_path
        else:
            logger.warning("No video recording available.")
            return None

    async def close_browser_context(self) -> None:
        """
        Closes the browser context and handles video recording finalization.
        """
        if self._browser_context is not None:
            if self._record_video:
                pages = self._browser_context.pages
                for page in pages:
                    if page.video:
                        video_path = await page.video.path()
                        # rename the video file to include the page URL
                        video_name = f"{self.stake_id}.webm" or os.path.basename(
                            video_path
                        )
                        video_dir = os.path.dirname(video_path)
                        video_url = "video_of" or page.url.replace("://", "_").replace(
                            "/", "_"
                        )
                        new_video_path = os.path.join(
                            video_dir, f"{video_url}_{video_name}"
                        )
                        os.rename(video_path, new_video_path)
                        self._latest_video_path = new_video_path
                        logger.info(f"Video recorded at: {new_video_path}")
            await self._browser_context.close()
            self._browser_context = None

    def log_user_message(self, message: str) -> None:
        """
        Log the user's message.

        Args:
            message (str): The user's message to log.
        """
        if self.ui_manager:
            self.ui_manager.new_user_message(message)

    def log_system_message(
        self, message: str, message_type: MessageType = MessageType.STEP
    ) -> None:
        """
        Log a system message.

        Args:
            message (str): The system message to log.
            message_type (MessageType, optional): The type of message. Defaults to MessageType.STEP.
        """
        if self.ui_manager:
            self.ui_manager.new_system_message(message, message_type)

    async def update_processing_state(self, processing_state: str) -> None:
        """
        Update the processing state of the overlay.

        Args:
            processing_state (str): "init", "processing", or "done"
        """
        page = await self.get_current_page()
        if self.ui_manager:
            await self.ui_manager.update_processing_state(processing_state, page)

    async def command_completed(
        self, command: str, elapsed_time: Optional[float] = None
    ) -> None:
        """
        Notify the overlay that the command has been completed.

        Args:
            command (str): The command that has been completed.
            elapsed_time (Optional[float]): The time taken to execute the command.
        """
        logger.debug(
            f'Command "{command}" has been completed. Focusing on the overlay input if it is open.'
        )
        page = await self.get_current_page()
        if self.ui_manager:
            await self.ui_manager.command_completed(page, command, elapsed_time)
