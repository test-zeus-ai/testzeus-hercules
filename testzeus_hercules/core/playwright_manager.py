import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import traceback  # Add this import
import zipfile
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from playwright.async_api import BrowserContext, BrowserType, ElementHandle
from playwright.async_api import Error as PlaywrightError  # for exception handling
from playwright.async_api import Page, Playwright
from playwright.async_api import async_playwright as playwright
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.notification_manager import NotificationManager
from testzeus_hercules.utils.dom_mutation_observer import (
    dom_mutation_change_detected,
    handle_navigation_for_mutation_observer,
)
from testzeus_hercules.utils.js_helper import get_js_with_element_finder
from testzeus_hercules.utils.logger import logger

# Ensures that playwright does not wait for font loading when taking screenshots.
# Reference: https://github.com/microsoft/playwright/issues/28995
os.environ["PW_TEST_SCREENSHOT_NO_FONTS_READY"] = "1"

MAX_WAIT_PAGE_LOAD_TIME = 1
WAIT_FOR_NETWORK_IDLE = 5
MIN_WAIT_PAGE_LOAD_TIME = 0.05

ALL_POSSIBLE_PERMISSIONS = [
    # "accelerometer",
    # "accessibility-events",
    # "ambient-light-sensor",
    # "background-sync",
    # "camera",
    # "clipboard-read",
    # "clipboard-write",
    "geolocation",
    # "gyroscope",
    # "magnetometer",
    # "microphone",
    # "midi-sysex",  # system-exclusive MIDI
    # "midi",
    "notifications",
    # "payment-handler",
    # "storage-access",
]


class PlaywrightManager:
    """
    Manages Playwright instances and browsers. Now supports stake_id-based singleton instances.
    """

    _instances: Dict[str, "PlaywrightManager"] = {}
    _default_instance: Optional["PlaywrightManager"] = None
    _homepage = "about:blank"

    def __new__(cls, *args, stake_id: Optional[str] = None, **kwargs) -> "PlaywrightManager":
        # If no stake_id provided and we have a default instance, return it
        if stake_id is None:
            if cls._default_instance is None:
                # Create default instance with stake_id "0"
                instance = super().__new__(cls)
                instance.__initialized = False
                cls._default_instance = instance
                cls._instances["0"] = instance
                logger.debug("Created default PlaywrightManager instance with stake_id '0'")
            return cls._default_instance

        # If stake_id provided, get or create instance for that stake_id
        if stake_id not in cls._instances:
            instance = super().__new__(cls)
            instance.__initialized = False
            cls._instances[stake_id] = instance
            logger.debug(f"Created new PlaywrightManager instance for stake_id '{stake_id}'")
            # If this is the first instance ever, make it the default
            if cls._default_instance is None:
                cls._default_instance = instance
        return cls._instances[stake_id]

    @classmethod
    def get_instance(cls, stake_id: Optional[str] = None) -> "PlaywrightManager":
        """Get PlaywrightManager instance for given stake_id, or default instance if none provided."""
        if stake_id is None:
            if cls._default_instance is None:
                # This will create the default instance
                return cls()
            return cls._default_instance
        if stake_id not in cls._instances:
            # This will create a new instance for this stake_id
            return cls(stake_id=stake_id)
        return cls._instances[stake_id]

    @classmethod
    def close_instance(cls, stake_id: Optional[str] = None) -> None:
        """Close and remove a specific PlaywrightManager instance."""
        target_id = stake_id if stake_id is not None else "0"
        if target_id in cls._instances:
            instance = cls._instances[target_id]
            asyncio.create_task(instance.stop_playwright())
            del cls._instances[target_id]
            if instance == cls._default_instance:
                cls._default_instance = None
                # If there are other instances, make the first one the default
                if cls._instances:
                    cls._default_instance = next(iter(cls._instances.values()))

    @classmethod
    def close_all_instances(cls) -> None:
        """Close all PlaywrightManager instances."""
        for stake_id in list(cls._instances.keys()):
            cls.close_instance(stake_id)

    def __init__(
        self,
        # ----------------------
        # FALLBACKS via CONF
        # ----------------------
        browser_type: Optional[str] = None,
        headless: Optional[bool] = None,
        gui_input_mode: bool = False,  # This parameter is now unused but kept for compatibility
        stake_id: Optional[str] = None,
        screenshots_dir: Optional[str] = None,
        take_screenshots: Optional[bool] = None,
        cdp_config: Optional[Dict] = None,
        record_video: Optional[bool] = None,
        video_dir: Optional[str] = None,
        log_requests_responses: Optional[bool] = None,
        request_response_log_file: Optional[str] = None,
        # --- Emulation-specific args ---
        device_name: Optional[str] = None,
        viewport: Optional[Tuple[int, int]] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,  # e.g. "America/New_York"
        geolocation: Optional[Dict[str, float]] = None,  # {"latitude": 51.5, "longitude": -0.13}
        color_scheme: Optional[str] = None,  # "light", "dark", "no-preference"
        allow_all_permissions: bool = True,
        log_console: Optional[bool] = None,
        console_log_file: Optional[str] = None,
    ):
        """
        Initialize the PlaywrightManager.

        If any argument is None, we fallback to the appropriate CONF method.
        Otherwise, we use the constructor-provided value.

        If `device_name` is provided, the built-in descriptor overrides user-agent,
        viewport, etc., *unless* you explicitly override them via other parameters.
        """
        self.allow_all_permissions = allow_all_permissions
        if self.__initialized:
            return  # Already inited, no-op

        self.__initialized = True

        # Store stake_id
        self.stake_id = stake_id or "0"

        # Video recording settings
        self._record_video = record_video if record_video is not None else get_global_conf().should_record_video()
        self._latest_video_path = None
        self._video_dir = None

        proof_path = get_global_conf().get_proof_path(test_id=self.stake_id)

        # ----------------------
        # 1) BROWSER / HEADLESS
        # ----------------------
        self.browser_type = browser_type or get_global_conf().get_browser_type() or "chromium"
        self.isheadless = headless if headless is not None else get_global_conf().should_run_headless()
        self.cdp_config = cdp_config or get_global_conf().get_cdp_config()

        # ----------------------
        # 2) BASIC FLAGS
        # ----------------------
        self.notification_manager = NotificationManager()
        self.user_response_future: Optional[asyncio.Future[str]] = None
        self._take_screenshots = take_screenshots if take_screenshots is not None else get_global_conf().should_take_screenshots()
        self.stake_id = stake_id

        # ----------------------
        # 3) PATHS
        # ----------------------
        self._screenshots_dir = proof_path + "/screenshots"
        self._video_dir = proof_path + "/videos"
        self.request_response_log_file = proof_path + "/network_logs.json"
        self.console_log_file = proof_path + "/console_logs.json"
        # Add trace directory path
        self._enable_tracing = get_global_conf().should_enable_tracing()
        self._trace_dir = None
        if self._enable_tracing:
            proof_path = get_global_conf().get_proof_path(test_id=self.stake_id)
            self._trace_dir = os.path.join(proof_path, "traces")
            logger.info(f"Tracing enabled. Traces will be saved to: {self._trace_dir}")

        # ----------------------
        # 4) LOGS
        # ----------------------
        self.log_requests_responses = log_requests_responses if log_requests_responses is not None else get_global_conf().should_capture_network()
        self.request_response_logs: List[Dict] = []

        # ----------------------
        # 5) INIT PLAYWRIGHT & BROWSERS
        # ----------------------
        self._playwright: Optional[Playwright] = None
        self._browser_context: Optional[BrowserContext] = None
        self.__async_initialize_done = False
        self._latest_screenshot_bytes: Optional[bytes] = None
        self._latest_video_path: Optional[str] = None

        # Extension caching directory
        self._extension_cache_dir = os.path.join(".", ".cache", "browser", self.browser_type, "extension")
        self._extension_path: Optional[str] = None

        # ----------------------
        # 6) EMULATION: DEVICE & OVERRIDES
        # ----------------------
        # If device_name is None, try from CONF
        device_name = device_name or get_global_conf().get_run_device()
        self.device_name = device_name
        # If no device or device doesn't override viewport, fallback to conf
        conf_res_str = get_global_conf().get_resolution() or "1280,720"
        cw, ch = conf_res_str.split(",")
        conf_viewport = (int(cw), int(ch))
        self.user_viewport = viewport or conf_viewport

        self.user_locale = locale or get_global_conf().get_locale()  # or None
        self.user_timezone = timezone or get_global_conf().get_timezone()  # or None
        self.user_geolocation = geolocation or get_global_conf().get_geolocation()  # or None
        self.user_color_scheme = color_scheme or get_global_conf().get_color_scheme() or "light"

        # If iPhone, override browser
        if self.device_name and "iphone" in self.device_name.lower():
            logger.info(f"Detected iPhone in device_name='{self.device_name}'; forcing browser_type=webkit.")
            self.browser_type = "webkit"

        # logging console messages
        self.log_console = log_console if log_console is not None else True

        logger.debug(
            f"PlaywrightManager init - "
            f"browser_type={self.browser_type}, headless={self.isheadless}, "
            f"device={self.device_name}, viewport={self.user_viewport}, "
            f"locale={self.user_locale}, timezone={self.user_timezone}, "
            f"geolocation={self.user_geolocation}, color_scheme={self.user_color_scheme}"
        )

    async def async_initialize(self) -> None:
        if self.__async_initialize_done:
            return

        # Create required directories
        os.makedirs(self._screenshots_dir, exist_ok=True)
        os.makedirs(self._video_dir, exist_ok=True)
        if self._enable_tracing:
            os.makedirs(self._trace_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.request_response_log_file), exist_ok=True)
        os.makedirs(os.path.dirname(self.console_log_file), exist_ok=True)

        await self.start_playwright()
        await self.ensure_browser_context()

        # Additional setup
        await self.setup_handlers()
        await self.go_to_homepage()

        self.__async_initialize_done = True

    async def ensure_browser_context(self) -> None:
        if self._browser_context is None:
            await self.create_browser_context()

    async def setup_handlers(self) -> None:
        await self.set_navigation_handler()

    async def start_playwright(self) -> None:
        if not self._playwright:
            self._playwright = await playwright().start()

    async def stop_playwright(self) -> None:
        await self.close_browser_context()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def prepare_extension(self) -> None:
        if os.name == "nt":
            logger.info("Skipping extension preparation on Windows.")
            return

        if self.browser_type == "chromium":
            extension_url = "https://github.com/gorhill/uBlock/releases/download/1.61.0/" "uBlock0_1.61.0.chromium.zip"
            extension_file_name = "uBlock0_1.61.0.chromium.zip"
            extension_dir_name = "uBlock0_1.61.0.chromium"
        elif self.browser_type == "firefox":
            extension_url = "https://addons.mozilla.org/firefox/downloads/file/4359936/" "ublock_origin-1.60.0.xpi"
            extension_file_name = "uBlock0_1.60.0.firefox.xpi"
            extension_dir_name = "uBlock0_1.60.0.firefox"
        else:
            logger.error(f"Unsupported browser type for extension: {self.browser_type}")
            return

        extension_dir = self._extension_cache_dir
        extension_file_path = os.path.join(extension_dir, extension_file_name)

        if not os.path.exists(extension_dir):
            await asyncio.to_thread(os.makedirs, extension_dir)

        if not os.path.exists(extension_file_path):
            logger.info(f"Downloading extension from {extension_url}")
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(extension_url)
                if response.status_code == 200:
                    # Write asynchronously
                    async def write_file_async(path, content):
                        await asyncio.to_thread(lambda: open(path, "wb").write(content))

                    await write_file_async(extension_file_path, response.content)
                    logger.info(f"Extension downloaded and saved to {extension_file_path}")
                else:
                    logger.error(f"Failed to download extension from {extension_url}, " f"status {response.status_code}")
                    return

        if self.browser_type == "chromium":
            extension_unzip_dir = os.path.join(extension_dir, extension_dir_name)
            if not os.path.exists(extension_unzip_dir):
                # Unzip asynchronously
                def unzip_archive(zip_path, extract_dir):
                    with zipfile.ZipFile(zip_path, "r") as zip_ref:
                        zip_ref.extractall(extract_dir)

                await asyncio.to_thread(unzip_archive, extension_file_path, extension_unzip_dir)
            self._extension_path = extension_unzip_dir + "/uBlock0.chromium"
        elif self.browser_type == "firefox":
            self._extension_path = extension_file_path

    async def _start_tracing(self, context_type: str = "browser") -> None:
        """Helper method to start tracing for a browser context."""
        if not self._enable_tracing:
            return

        try:
            await self._browser_context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=True,
            )
            logger.info(f"Tracing started for {context_type} context")
        except Exception as e:
            logger.error(f"Failed to start tracing for {context_type} context: {e}")

    async def create_browser_context(self) -> None:
        """
        Creates the browser context with device descriptor if any,
        plus locale, timezone, geolocation, color scheme, etc.
        """
        user_dir: str = os.environ.get("BROWSER_STORAGE_DIR", "")

        disable_args = [
            "--disable-session-crashed-bubble",
            "--disable-notifications",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-background-timer-throttling",
            "--disable-popup-blocking",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-window-activation",
            "--disable-focus-on-load",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-position=0,0",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

        if not self.device_name:
            w, h = self.user_viewport
            disable_args.append(f"--window-size={w},{h}")

        if self.cdp_config:
            logger.info("Connecting over CDP with provided configuration.")
            endpoint_url = self.cdp_config.get("endpoint_url")
            if not endpoint_url:
                raise ValueError("CDP config must include 'endpoint_url'.")

            browser_type = getattr(self._playwright, self.browser_type)
            recording_supported = True

            # have to skip the recording for browserstack and LT as they don't support connect_over_cdp
            if "browserstack" in endpoint_url or "LT%3AOptions" in endpoint_url:
                _browser = await browser_type.connect(endpoint_url, timeout=120000)
                recording_supported = False
            else:
                _browser = await browser_type.connect_over_cdp(endpoint_url, timeout=120000)

            context_options = {}
            if recording_supported:
                if self._record_video:
                    context_options = {"record_video_dir": self._video_dir}
                    context_options.update(self._build_emulation_context_options())
                    logger.info("Recording video in CDP mode.")
            else:
                logger.info("Recording video not supported in given CDP URL.")
            self._browser_context = await _browser.new_context(**context_options)
            page = await _browser.new_page()
            # page = await self._browser_context.new_page()
            await page.goto("https://www.testzeus.com", timeout=120000)

        else:
            if self.browser_type != "chromium":
                disable_args = []

            browser_type = getattr(self._playwright, self.browser_type)
            await self.prepare_extension()

            if self._record_video:
                await self._launch_browser_with_video(browser_type, user_dir, disable_args)
            else:
                await self._launch_persistent_browser(browser_type, user_dir, disable_args)

        # Start tracing only once after browser context is created
        await self._start_tracing()

    def _build_emulation_context_options(self) -> Dict[str, Any]:
        """
        Combine device descriptor with user overrides (locale, timezone, geolocation,
        color scheme, plus optional permissions).
        """
        context_options: Dict[str, Any] = {}

        # 1) If device_name is set, retrieve from built-in devices
        if self.device_name and self._playwright:
            device = self._playwright.devices.get(self.device_name)
            if device:
                context_options.update(device)
            else:
                logger.warning(f"Device '{self.device_name}' not found. Using custom viewport.")
                context_options["viewport"] = {
                    "width": self.user_viewport[0],
                    "height": self.user_viewport[1],
                }
        else:
            context_options["viewport"] = {
                "width": self.user_viewport[0],
                "height": self.user_viewport[1],
            }

        # 2) Additional overrides
        if self.user_locale:
            context_options["locale"] = self.user_locale
        if self.user_timezone:
            context_options["timezone_id"] = self.user_timezone
        if self.user_geolocation:
            context_options["geolocation"] = self.user_geolocation
            # Optionally also set geolocation permission
            # if you only want that single permission:
            # context_options["permissions"] = ["geolocation"]
        if self.user_color_scheme:
            context_options["color_scheme"] = self.user_color_scheme

        # 3) (New) If we want to allow all possible permissions, set them:
        if self.allow_all_permissions:
            context_options["permissions"] = ALL_POSSIBLE_PERMISSIONS

        return context_options

    async def _launch_persistent_browser(
        self,
        browser_type: BrowserType,
        user_dir: str,
        disable_args: Optional[List[str]] = None,
    ) -> None:
        if disable_args is None:
            disable_args = []

        logger.info(f"Launching {self.browser_type} with user dir: {user_dir}")

        try:
            browser_context_kwargs = {
                "headless": self.isheadless,
                "args": disable_args,
            }
            browser_context_kwargs.update(self._build_emulation_context_options())

            if self.browser_type == "chromium" and self._extension_path is not None:
                disable_args.append(f"--disable-extensions-except={self._extension_path}")
                disable_args.append(f"--load-extension={self._extension_path}")
            elif self.browser_type == "firefox" and self._extension_path is not None:
                browser_context_kwargs["firefox_user_prefs"] = {
                    "xpinstall.signatures.required": False,
                    "extensions.autoDisableScopes": 0,
                    "extensions.enabledScopes": 15,
                    "extensions.installDistroAddons": False,
                    "extensions.update.enabled": False,
                    "browser.shell.checkDefaultBrowser": False,
                    "browser.startup.homepage": "about:blank",
                    "toolkit.telemetry.reportingpolicy.firstRun": False,
                    "extensions.webextensions.userScripts.enabled": True,
                }

            self._browser_context = await browser_type.launch_persistent_context(user_dir, **browser_context_kwargs)
        except (PlaywrightError, OSError) as e:
            await self._handle_launch_exception(e, user_dir, browser_type, disable_args)

    async def _launch_browser_with_video(
        self,
        browser_type: BrowserType,
        user_dir: str,
        disable_args: Optional[List[str]] = None,
    ) -> None:
        logger.info(f"Launching {self.browser_type} with video recording enabled.")
        temp_user_dir = tempfile.mkdtemp(prefix="playwright-user-data-")
        # copy user_dir to temp in a separate thread
        if user_dir and os.path.exists(user_dir):
            await asyncio.to_thread(shutil.copytree, user_dir, temp_user_dir, True)
        else:
            user_dir = temp_user_dir

        try:
            if self.browser_type == "chromium" and self._extension_path is not None:
                disable_args.append(f"--disable-extensions-except={self._extension_path}")
                disable_args.append(f"--load-extension={self._extension_path}")

            browser = await browser_type.launch(
                headless=self.isheadless,
                args=disable_args,
            )

            context_options = {"record_video_dir": self._video_dir}
            context_options.update(self._build_emulation_context_options())

            self._browser_context = await browser.new_context(**context_options)  # type: ignore
        except Exception as e:
            logger.error(f"Failed to launch browser with video recording: {e}")
            raise e

    async def _handle_launch_exception(
        self,
        e: Exception,
        user_dir: str,
        browser_type: BrowserType,
        args: Optional[List[str]] = None,
    ) -> None:
        if "Target page, context or browser has been closed" in str(e):
            new_user_dir = tempfile.mkdtemp()
            logger.error(f"Failed to launch persistent context with user dir {user_dir}: {e}. " f"Trying with a new user dir {new_user_dir}")
            self._browser_context = await browser_type.launch_persistent_context(
                new_user_dir,
                headless=self.isheadless,
                args=args or [],
            )
        elif "Chromium distribution 'chrome' is not found" in str(e):
            raise ValueError("Chrome is not installed on this device. Install Google Chrome or use 'playwright install'.") from None
        else:
            raise e from None

    async def get_browser_context(self) -> BrowserContext:
        await self.ensure_browser_context()
        if self._browser_context is None:
            raise RuntimeError("Browser context is not available.")
        return self._browser_context

    async def setup_request_response_logging(self, page: Page) -> None:
        if not self.log_requests_responses:
            return
        page.on("request", self.log_request)
        page.on("response", self.log_response)

    def log_request(self, request: Any) -> None:
        try:
            post_data = request.post_data
            try:
                decoded_post_data = post_data.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                decoded_post_data = base64.b64encode(post_data).decode("utf-8")
        except Exception:
            decoded_post_data = None

        log_entry = {
            "type": "request",
            "timestamp": time.time(),
            "method": request.method,
            "url": request.url,
            "headers": request.headers,
            "post_data": decoded_post_data,
        }
        # Instead of writing directly, do it via asyncio
        asyncio.ensure_future(self._write_log_entry_to_file(log_entry, self.request_response_log_file))

    def log_response(self, response: Any) -> None:
        log_entry = {
            "type": "response",
            "timestamp": time.time(),
            "status": response.status,
            "url": response.url,
            "headers": response.headers,
            "body": None,
        }
        asyncio.ensure_future(self._write_log_entry_to_file(log_entry, self.request_response_log_file))

    async def _write_log_entry_to_file(self, log_entry: Dict, log_file: str) -> None:
        """Write a single log entry asynchronously."""
        try:
            line = json.dumps(log_entry, ensure_ascii=False) + "\n"

            # We'll open the file in append mode within a thread
            def append_line(filepath: str, text: str) -> None:
                """Append a line to a file."""
                with open(filepath, "a", encoding="utf-8") as file:
                    file.write(text)

            await asyncio.to_thread(append_line, log_file, line)
        except Exception as e:
            logger.error(f"Failed to write request/response log to file: {e}")

    async def get_current_url(self) -> Optional[str]:
        try:
            current_page: Page = await self.get_current_page()
            return current_page.url
        except Exception as e:
            logger.warning(f"Failed to get current URL: {e}")
        return None

    async def get_current_page(self) -> Page:
        try:
            browser_context = await self.get_browser_context()
            pages: list[Page] = [p for p in browser_context.pages if not p.is_closed()]
            page: Optional[Page] = pages[-1] if pages else None

            logger.debug(f"Current page: {page.url if page else None}")
            if page is None:
                logger.debug("Creating new page. No pages found.")
                page = await browser_context.new_page()
                await self.setup_request_response_logging(page)
                await self.setup_console_logging(page)
            return page

        except Exception as e:
            logger.warning(f"Error getting current page: {e}. Creating new context.")
            self._browser_context = None
            await self.ensure_browser_context()
            browser_context = await self.get_browser_context()
            pages = [p for p in browser_context.pages if not p.is_closed()]
            if pages:
                return pages[-1]
            else:
                return await browser_context.new_page()

    async def close_all_tabs(self, keep_first_tab: bool = True) -> None:
        browser_context = await self.get_browser_context()
        pages: list[Page] = browser_context.pages
        if keep_first_tab:
            pages_to_close = pages[1:]
        else:
            pages_to_close = pages
        for page in pages_to_close:
            await page.close()

    async def close_except_specified_tab(self, page_to_keep: Page) -> None:
        browser_context = await self.get_browser_context()
        for page in browser_context.pages:
            if page != page_to_keep:
                await page.close()

    async def go_to_homepage(self) -> None:
        page: Page = await self.get_current_page()
        await page.goto(self._homepage)

    async def set_navigation_handler(self) -> None:
        page: Page = await self.get_current_page()
        page.on("domcontentloaded", handle_navigation_for_mutation_observer)

        async def set_iframe_navigation_handlers() -> None:
            for frame in page.frames:
                if frame != page.main_frame:
                    frame.on("domcontentloaded", handle_navigation_for_mutation_observer)

        await set_iframe_navigation_handlers()

        await page.expose_function("dom_mutation_change_detected", dom_mutation_change_detected)
        page.on(
            "frameattached",
            lambda frame: frame.on("domcontentloaded", handle_navigation_for_mutation_observer),
        )

    async def highlight_element(self, selector: str) -> None:
        # try:
        #     element = await self.find_element(selector)
        #     if isinstance(element, ElementHandle):
        #         box = await element.bounding_box()
        #     else:
        #         await element.highlight()
        # except Exception as e:
        #     logger.warning(f"Error in highlight_element({selector}): {e}")
        pass

    async def highlight_element(self, selector: str) -> None:
        # try:
        #     element = await self.find_element(selector)
        #     if isinstance(element, ElementHandle):
        #         box = await element.bounding_box()
        #     else:
        #         await element.highlight()
        # except Exception as e:
        #     logger.warning(f"Error in highlight_element({selector}): {e}")
        pass

    async def receive_user_response(self, response: str) -> None:
        logger.debug(f"Received user response: {response}")
        if self.user_response_future and not self.user_response_future.done():
            self.user_response_future.set_result(response)

    async def prompt_user(self, message: str) -> str:
        logger.debug(f'Prompting user with: "{message}"')
        page = await self.get_current_page()

        self.user_response_future = asyncio.Future()
        result = await self.user_response_future
        logger.info(f'User response to "{message}": {result}')
        self.user_response_future = None
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
        take_snapshot_timeout: int = 5000,
    ) -> None:
        if not self._take_screenshots:
            return
        if page is None:
            page = await self.get_current_page()

        screenshot_name = name
        if include_timestamp:
            screenshot_name += f"_{int(time.time_ns())}"
        screenshot_name += ".png"
        screenshot_path = os.path.join(self.get_screenshots_dir(), screenshot_name)

        try:
            await page.wait_for_load_state(state=load_state, timeout=take_snapshot_timeout)
            screenshot_bytes = await page.screenshot(
                path=screenshot_path,
                full_page=full_page,
                timeout=take_snapshot_timeout,
                caret="initial",
                scale="device",
            )
            self._latest_screenshot_bytes = screenshot_bytes
            logger.debug(f"Screenshot saved: {screenshot_path}")
        except Exception as e:
            logger.error(f"Failed to take screenshot at {screenshot_path}: {e}")

    async def get_latest_screenshot_stream(self) -> Optional[BytesIO]:
        if self._latest_screenshot_bytes:
            return BytesIO(self._latest_screenshot_bytes)
        else:
            logger.warning("No screenshot available.")
            return None

    def get_latest_video_path(self) -> Optional[str]:
        if self._latest_video_path and os.path.exists(self._latest_video_path):
            return self._latest_video_path
        else:
            logger.warning("No video recording available.")
            return None

    async def close_browser_context(self) -> None:
        if self._browser_context:
            if self._record_video:
                pages = self._browser_context.pages
                for page in pages:
                    try:
                        if page.video:
                            video_path = await page.video.path()
                            if self.stake_id:
                                video_name = f"{self.stake_id}.webm"
                            else:
                                video_name = os.path.basename(video_path)
                            video_dir = os.path.dirname(video_path)
                            safe_url = page.url.replace("://", "_").replace("/", "_").replace(".", "_") if not page.url else "video_of"
                            new_video_path = os.path.join(video_dir, f"{safe_url}_{video_name}")

                            # rename asynchronously
                            def rename_file(src, dst):
                                os.rename(src, dst)

                            await asyncio.to_thread(rename_file, video_path, new_video_path)
                            self._latest_video_path = new_video_path
                            logger.info(f"Video recorded at {new_video_path}")
                    except Exception as e:
                        logger.error(f"Could not finalize video: {e}")

            # Stop and save tracing before closing context
            if self._enable_tracing:
                try:
                    timestamp = int(time.time())
                    trace_file = os.path.join(self._trace_dir, f"trace_{timestamp}.zip")
                    os.makedirs(self._trace_dir, exist_ok=True)

                    await self._browser_context.tracing.stop(path=trace_file)

                    if os.path.exists(trace_file):
                        logger.info(f"Trace saved successfully at: {trace_file}")
                    else:
                        logger.error(f"Trace file was not created at: {trace_file}")
                except Exception as e:
                    logger.error(f"Error stopping trace: {e}")
                    traceback.print_exc()

            await self._browser_context.close()
            self._browser_context = None

    async def update_processing_state(self, processing_state: str) -> None:
        pass

    async def command_completed(self, command: str, elapsed_time: Optional[float] = None) -> None:
        logger.debug(f'Command "{command}" completed.')

    # -------------------------------------------------------------------------
    # Additional helpers for stable network wait
    # -------------------------------------------------------------------------
    async def _wait_for_stable_network(self) -> None:
        page = await self.get_current_page()
        pending_requests = set()
        last_activity = asyncio.get_event_loop().time()

        RELEVANT_RESOURCE_TYPES = {
            "document",
            "stylesheet",
            "image",
            "font",
            "script",
            "iframe",
        }
        RELEVANT_CONTENT_TYPES = {
            "text/html",
            "text/css",
            "application/javascript",
            "image/",
            "font/",
            "application/json",
        }
        IGNORED_URL_PATTERNS = {
            "analytics",
            "tracking",
            "telemetry",
            "beacon",
            "metrics",
            "doubleclick",
            "adsystem",
            "adserver",
            "advertising",
            "facebook.com/plugins",
            "platform.twitter",
            "linkedin.com/embed",
            "livechat",
            "zendesk",
            "intercom",
            "crisp.chat",
            "hotjar",
            "push-notifications",
            "onesignal",
            "pushwoosh",
            "heartbeat",
            "ping",
            "alive",
            "webrtc",
            "rtmp://",
            "wss://",
            "cloudfront.net",
            "fastly.net",
        }

        async def on_request(request: Any) -> None:
            if request.resource_type not in RELEVANT_RESOURCE_TYPES:
                return
            if request.resource_type in {
                "websocket",
                "media",
                "eventsource",
                "manifest",
                "other",
            }:
                return
            url = request.url.lower()
            if any(p in url for p in IGNORED_URL_PATTERNS):
                return
            if url.startswith(("data:", "blob:")):
                return
            headers = request.headers
            if headers.get("purpose") == "prefetch" or headers.get("sec-fetch-dest") in ["video", "audio"]:
                return
            nonlocal last_activity
            pending_requests.add(request)
            last_activity = asyncio.get_event_loop().time()

        async def on_response(response: Any) -> None:
            request = response.request
            if request not in pending_requests:
                return
            content_type = response.headers.get("content-type", "").lower()
            if any(
                t in content_type
                for t in [
                    "streaming",
                    "video",
                    "audio",
                    "webm",
                    "mp4",
                    "event-stream",
                    "websocket",
                    "protobuf",
                ]
            ):
                pending_requests.remove(request)
                return
            if not any(ct in content_type for ct in RELEVANT_CONTENT_TYPES):
                pending_requests.remove(request)
                return
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > 5 * 1024 * 1024:
                pending_requests.remove(request)
                return
            nonlocal last_activity
            pending_requests.remove(request)
            last_activity = asyncio.get_event_loop().time()

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            start_time = asyncio.get_event_loop().time()
            while True:
                await asyncio.sleep(0.1)
                now = asyncio.get_event_loop().time()
                if (len(pending_requests) == 0) and ((now - last_activity) >= WAIT_FOR_NETWORK_IDLE):
                    break
                if now - start_time > MAX_WAIT_PAGE_LOAD_TIME:
                    logger.debug(f"Network timeout after {MAX_WAIT_PAGE_LOAD_TIME}s with {len(pending_requests)} pending requests")
                    break
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

        logger.debug(f"Network stabilized for {WAIT_FOR_NETWORK_IDLE} ms")

    async def wait_for_page_and_frames_load(self, timeout_overwrite: Optional[float] = None) -> None:
        start_time = time.time()
        try:
            await self._wait_for_stable_network()
        except Exception:
            logger.warning("Page load stable-network check failed, continuing...")

        elapsed = time.time() - start_time
        remaining = max((timeout_overwrite or MIN_WAIT_PAGE_LOAD_TIME) - elapsed, 0)
        logger.debug(f"Page loaded in {elapsed:.2f}s, waiting {remaining:.2f}s more for stability.")

    # -------------------------------------------------------------------------
    # NEW METHODS for updating context properties on the fly
    # -------------------------------------------------------------------------
    async def set_size(self, width: int, height: int) -> None:
        """Change the viewport size on the current page (runtime only)."""
        page = await self.get_current_page()
        await page.set_viewport_size({"width": width, "height": height})
        logger.debug(f"Viewport changed to {width}x{height} (runtime)")

    async def set_locale(self, locale: str) -> None:
        """
        Changing locale at runtime requires a new context in Playwright.
        We'll store the new locale, close & recreate the context, and reopen the homepage.
        """
        logger.debug(f"Updating locale to {locale}")
        self.user_locale = locale
        await self._recreate_browser_context()

    async def set_timezone(self, timezone: str) -> None:
        logger.debug(f"Updating timezone to {timezone}")
        self.user_timezone = timezone
        await self._recreate_browser_context()

    async def set_geolocation(self, latitude: float, longitude: float) -> None:
        logger.debug(f"Updating geolocation to lat={latitude}, long={longitude}")
        self.user_geolocation = {"latitude": latitude, "longitude": longitude}
        context = await self.get_browser_context()
        await context.set_geolocation({"latitude": latitude, "longitude": longitude})

    async def set_color_scheme(self, color_scheme: str) -> None:
        logger.debug(f"Updating color scheme to {color_scheme}")
        self.user_color_scheme = color_scheme
        page = await self.get_current_page()
        await page.emulate_media(color_scheme=self.user_color_scheme)

    async def _recreate_browser_context(self) -> None:
        """Close the current context, re-create with updated emulation settings, then navigate home."""
        logger.debug("Recreating browser context to apply new emulation settings.")
        if self._browser_context:
            await self._browser_context.close()
            self._browser_context = None
        await self.create_browser_context()
        await self.go_to_homepage()

    async def perform_javascript_click(self, page: Page, selector: str, type_of_click: str) -> str:
        js_code = """(params) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const selector = params[0];
            const type_of_click = params[1];

            let element = findElementInShadowDOMAndIframes(document, selector);
            if (!element) {
                console.log(`perform_javascript_click: Element with selector ${selector} not found`);
                return `perform_javascript_click: Element with selector ${selector} not found`;
            }

            if (element.tagName.toLowerCase() === "a") {
                element.target = "_self";
            }
            
            let ariaExpandedBeforeClick = element.getAttribute('aria-expanded');

            // Get the element's bounding rectangle for mouse events
            const rect = element.getBoundingClientRect();
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;

            // Common mouse move event
            const mouseMove = new MouseEvent('mousemove', {
                bubbles: true,
                cancelable: true,
                clientX: centerX,
                clientY: centerY,
                view: window
            });
            element.dispatchEvent(mouseMove);

            // Handle different click types
            switch(type_of_click) {
                case 'right_click':
                    const contextMenuEvent = new MouseEvent('contextmenu', {
                        bubbles: true,
                        cancelable: true,
                        clientX: centerX,
                        clientY: centerY,
                        button: 2,
                        view: window
                    });
                    element.dispatchEvent(contextMenuEvent);
                    break;

                case 'double_click':
                    const dblClickEvent = new MouseEvent('dblclick', {
                        bubbles: true,
                        cancelable: true,
                        clientX: centerX,
                        clientY: centerY,
                        button: 0,
                        view: window
                    });
                    element.dispatchEvent(dblClickEvent);
                    break;

                case 'middle_click':
                    const middleClickEvent = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        button: 1,
                        view: window
                    });
                    element.dispatchEvent(middleClickEvent);
                    break;

                default: // normal click
                    element.click();
                    break;
            }

            const ariaExpandedAfterClick = element.getAttribute('aria-expanded');
            if (ariaExpandedBeforeClick === 'false' && ariaExpandedAfterClick === 'true') {
                return "Executed " + type_of_click + " on element with selector: " + selector + 
                    ". Very important: As a consequence, a menu has appeared where you may need to make further selection. " +
                    "Very important: Get all_fields DOM to complete the action.";
            }
            return "Executed " + type_of_click + " on element with selector: " + selector;
        }"""

        try:
            logger.info(f"Executing JavaScript '{type_of_click}' on element with selector: {selector}")
            result: str = await page.evaluate(get_js_with_element_finder(js_code), (selector, type_of_click))
            logger.debug(f"Executed JavaScript '{type_of_click}' on element with selector: {selector}")
            return result
        except Exception as e:
            logger.error(f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}. Error: {e}")
            traceback.print_exc()
            return f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}"

    async def is_element_present(self, selector: str, page: Optional[Page] = None) -> bool:
        """Check if an element is present in DOM/Shadow DOM/iframes."""
        if page is None:
            page = await self.get_current_page()

        # Try regular DOM first
        element = await page.query_selector(selector)
        if element:
            return True

        # Check Shadow DOM and iframes
        js_code = """(selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            return findElementInShadowDOMAndIframes(document, selector) !== null;
        }"""

        return await page.evaluate_handle(get_js_with_element_finder(js_code), selector)

    async def find_element(self, selector: str, page: Optional[Page] = None) -> Optional[ElementHandle]:
        """Find element in DOM/Shadow DOM/iframes and return ElementHandle."""
        if page is None:
            page = await self.get_current_page()

        # Try regular DOM first
        element = await page.query_selector(selector)
        if element:
            return element

        # Check Shadow DOM and iframes
        js_code = """(selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            return findElementInShadowDOMAndIframes(document, selector);
        }"""

        element = await page.evaluate_handle(get_js_with_element_finder(js_code), selector)
        if element:
            return element.as_element()

        return None

    async def perform_javascript_click(self, page: Page, selector: str, type_of_click: str) -> str:
        js_code = """(params) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            const selector = params[0];
            const type_of_click = params[1];

            let element = findElementInShadowDOMAndIframes(document, selector);
            if (!element) {
                console.log(`perform_javascript_click: Element with selector ${selector} not found`);
                return `perform_javascript_click: Element with selector ${selector} not found`;
            }

            if (element.tagName.toLowerCase() === "a") {
                element.target = "_self";
            }
            
            let ariaExpandedBeforeClick = element.getAttribute('aria-expanded');

            // Get the element's bounding rectangle for mouse events
            const rect = element.getBoundingClientRect();
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;

            // Common mouse move event
            const mouseMove = new MouseEvent('mousemove', {
                bubbles: true,
                cancelable: true,
                clientX: centerX,
                clientY: centerY,
                view: window
            });
            element.dispatchEvent(mouseMove);

            // Handle different click types
            switch(type_of_click) {
                case 'right_click':
                    const contextMenuEvent = new MouseEvent('contextmenu', {
                        bubbles: true,
                        cancelable: true,
                        clientX: centerX,
                        clientY: centerY,
                        button: 2,
                        view: window
                    });
                    element.dispatchEvent(contextMenuEvent);
                    break;

                case 'double_click':
                    const dblClickEvent = new MouseEvent('dblclick', {
                        bubbles: true,
                        cancelable: true,
                        clientX: centerX,
                        clientY: centerY,
                        button: 0,
                        view: window
                    });
                    element.dispatchEvent(dblClickEvent);
                    break;

                case 'middle_click':
                    const middleClickEvent = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        button: 1,
                        view: window
                    });
                    element.dispatchEvent(middleClickEvent);
                    break;

                default: // normal click
                    element.click();
                    break;
            }

            const ariaExpandedAfterClick = element.getAttribute('aria-expanded');
            if (ariaExpandedBeforeClick === 'false' && ariaExpandedAfterClick === 'true') {
                return "Executed " + type_of_click + " on element with selector: " + selector + 
                    ". Very important: As a consequence, a menu has appeared where you may need to make further selection. " +
                    "Very important: Get all_fields DOM to complete the action.";
            }
            return "Executed " + type_of_click + " on element with selector: " + selector;
        }"""

        try:
            logger.info(f"Executing JavaScript '{type_of_click}' on element with selector: {selector}")
            result: str = await page.evaluate(get_js_with_element_finder(js_code), (selector, type_of_click))
            logger.debug(f"Executed JavaScript '{type_of_click}' on element with selector: {selector}")
            return result
        except Exception as e:
            logger.error(f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}. Error: {e}")
            traceback.print_exc()
            return f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}"

    async def is_element_present(self, selector: str, page: Optional[Page] = None) -> bool:
        """Check if an element is present in DOM/Shadow DOM/iframes."""
        if page is None:
            page = await self.get_current_page()

        # Try regular DOM first
        element = await page.query_selector(selector)
        if element:
            return True

        # Check Shadow DOM and iframes
        js_code = """(selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            return findElementInShadowDOMAndIframes(document, selector) !== null;
        }"""

        return await page.evaluate_handle(get_js_with_element_finder(js_code), selector)

    async def find_element(self, selector: str, page: Optional[Page] = None) -> Optional[ElementHandle]:
        """Find element in DOM/Shadow DOM/iframes and return ElementHandle."""
        if page is None:
            page = await self.get_current_page()

        # Try regular DOM first
        element = await page.query_selector(selector)
        if element:
            return element

        # Check Shadow DOM and iframes
        js_code = """(selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            return findElementInShadowDOMAndIframes(document, selector);
        }"""

        element = await page.evaluate_handle(get_js_with_element_finder(js_code), selector)
        if element:
            return element.as_element()

        return None

    async def setup_console_logging(self, page: Page) -> None:
        """Attach an event listener to capture console logs if enabled."""
        if not self.log_console:
            return

        # Attach the listener
        page.on("console", self.log_console_message)

    def log_console_message(self, msg: Any) -> None:
        """Callback to handle console messages and write them to a file as JSON lines."""
        # Collect desired info
        log_entry = {
            "type": "console",
            "level": msg.type,  # 'log', 'warning', 'error', etc.
            "timestamp": time.time(),
            "text": msg.text,
            "location": msg.location,  # has 'url', 'lineNumber', 'columnNumber'
        }
        # Write asynchronously to console_log_file
        asyncio.ensure_future(self._write_log_entry_to_file(log_entry, self.console_log_file))
