import base64
import json
import os
import shutil
import tempfile
import time
import traceback
import zipfile
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from playwright.sync_api import BrowserContext, BrowserType, ElementHandle
from playwright.sync_api import Error as PlaywrightError  # for exception handling
from playwright.sync_api import Page, Playwright, sync_playwright
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.notification_manager import NotificationManager
from testzeus_hercules.utils.dom_mutation_observer import dom_mutation_change_detected
from testzeus_hercules.utils.js_helper import get_js_with_element_finder
from testzeus_hercules.utils.logger import logger

# Ensures that playwright does not wait for font loading when taking screenshots.
# Reference: https://github.com/microsoft/playwright/issues/28995
os.environ["PW_TEST_SCREENSHOT_NO_FONTS_READY"] = "1"

MAX_WAIT_PAGE_LOAD_TIME = 0.6
WAIT_FOR_NETWORK_IDLE = 2
MIN_WAIT_PAGE_LOAD_TIME = 0.05

ALL_POSSIBLE_PERMISSIONS = [
    "geolocation",
    "notifications",
]


class PlaywrightManager:
    """
    Manages Playwright instances and browsers. Now supports stake_id-based singleton instances.
    """

    _instances: Dict[str, "PlaywrightManager"] = {}
    _default_instance: Optional["PlaywrightManager"] = None
    _homepage = "about:blank"
    __initialized: bool = False

    def __new__(
        cls, *args: Any, stake_id: Optional[str] = None, **kwargs: Any
    ) -> "PlaywrightManager":
        # If no stake_id provided and we have a default instance, return it
        if stake_id is None:
            if cls._default_instance is None:
                # Create default instance with stake_id "0"
                instance = super().__new__(cls)
                instance.__initialized = False
                cls._default_instance = instance
                cls._instances["0"] = instance
                logger.debug(
                    "Created default PlaywrightManager instance with stake_id '0'"
                )
            return cls._default_instance

        # If stake_id provided, get or create instance for that stake_id
        if stake_id not in cls._instances:
            instance = super().__new__(cls)
            instance.__initialized = False
            cls._instances[stake_id] = instance
            logger.debug(
                f"Created new PlaywrightManager instance for stake_id '{stake_id}'"
            )
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
            instance.stop_playwright()
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
        cdp_config: Optional[Dict[str, Any]] = None,
        record_video: Optional[bool] = None,
        video_dir: Optional[str] = None,
        log_requests_responses: Optional[bool] = None,
        request_response_log_file: Optional[str] = None,
        # --- Emulation-specific args ---
        device_name: Optional[str] = None,
        viewport: Optional[Tuple[int, int]] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,  # e.g. "America/New_York"
        geolocation: Optional[
            Dict[str, float]
        ] = None,  # {"latitude": 51.5, "longitude": -0.13}
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
        self._record_video = (
            record_video
            if record_video is not None
            else get_global_conf().should_record_video()
        )
        self._latest_video_path = None

        proof_path = get_global_conf().get_proof_path(test_id=self.stake_id)

        # ----------------------
        # 1) BROWSER / HEADLESS
        # ----------------------
        self.browser_type = (
            browser_type or get_global_conf().get_browser_type() or "chromium"
        )
        self.isheadless = (
            headless
            if headless is not None
            else get_global_conf().should_run_headless()
        )
        self.cdp_config = cdp_config or get_global_conf().get_cdp_config()

        # ----------------------
        # 2) BASIC FLAGS
        # ----------------------
        self.notification_manager = NotificationManager()
        self._take_screenshots = (
            take_screenshots
            if take_screenshots is not None
            else get_global_conf().should_take_screenshots()
        )
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
        self.log_requests_responses = (
            log_requests_responses
            if log_requests_responses is not None
            else get_global_conf().should_capture_network()
        )
        self.request_response_logs: List[Dict[str, Any]] = []

        # ----------------------
        # 5) INIT PLAYWRIGHT & BROWSERS
        # ----------------------
        self._playwright: Optional[Playwright] = None
        self._browser_context: Optional[BrowserContext] = None
        self.__sync_initialize_done = False
        self._latest_screenshot_bytes: Optional[bytes] = None

        # Extension caching directory
        self._extension_cache_dir = os.path.join(
            ".", ".cache", "browser", self.browser_type, "extension"
        )
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
        self.user_geolocation = (
            geolocation or get_global_conf().get_geolocation()
        )  # or None
        self.user_color_scheme = (
            color_scheme or get_global_conf().get_color_scheme() or "light"
        )

        # If iPhone, override browser
        if self.device_name and "iphone" in self.device_name.lower():
            logger.info(
                f"Detected iPhone in device_name='{self.device_name}'; forcing browser_type=webkit."
            )
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

    def async_initialize(self) -> None:
        """
        DEPRECATED: This method is actually synchronous and will be removed in a future version.
        Use initialize() instead.
        """
        import warnings

        warnings.warn(
            "async_initialize() is deprecated and will be removed. Use initialize() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.initialize()

    def initialize(self) -> None:
        """Initialize the Playwright manager."""
        if self.__sync_initialize_done:
            return

        # Create required directories
        os.makedirs(self._screenshots_dir, exist_ok=True)
        os.makedirs(self._video_dir, exist_ok=True)
        if self._enable_tracing:
            os.makedirs(self._trace_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.request_response_log_file), exist_ok=True)
        os.makedirs(os.path.dirname(self.console_log_file), exist_ok=True)

        self.start_playwright()
        self.ensure_browser_context()

        # Additional setup
        self.setup_handlers()
        self.go_to_homepage()

        self.__sync_initialize_done = True

    def ensure_browser_context(self) -> None:
        if self._browser_context is None:
            self.create_browser_context()

    def setup_handlers(self) -> None:
        self.set_navigation_handler()

    def start_playwright(self) -> None:
        if not self._playwright:
            self._playwright = sync_playwright().start()

    def stop_playwright(self) -> None:
        self.close_browser_context()
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def prepare_extension(self) -> None:
        if os.name == "nt":
            logger.info("Skipping extension preparation on Windows.")
            return

        if self.browser_type == "chromium":
            extension_url = (
                "https://github.com/gorhill/uBlock/releases/download/1.61.0/"
                "uBlock0_1.61.0.chromium.zip"
            )
            extension_file_name = "uBlock0_1.61.0.chromium.zip"
            extension_dir_name = "uBlock0_1.61.0.chromium"
        elif self.browser_type == "firefox":
            extension_url = (
                "https://addons.mozilla.org/firefox/downloads/file/4359936/"
                "ublock_origin-1.60.0.xpi"
            )
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
            logger.info(f"Downloading extension from {extension_url}")
            response = httpx.get(extension_url)
            if response.status_code == 200:
                # Write synchronously
                with open(extension_file_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Extension downloaded and saved to {extension_file_path}")
            else:
                logger.error(
                    f"Failed to download extension from {extension_url}, "
                    f"status {response.status_code}"
                )
                return

        if self.browser_type == "chromium":
            extension_unzip_dir = os.path.join(extension_dir, extension_dir_name)
            if not os.path.exists(extension_unzip_dir):
                # Unzip synchronously
                with zipfile.ZipFile(extension_file_path, "r") as zip_ref:
                    zip_ref.extractall(extension_unzip_dir)
            self._extension_path = extension_unzip_dir + "/uBlock0.chromium"
        elif self.browser_type == "firefox":
            self._extension_path = extension_file_path

    def _start_tracing(self, context_type: str = "browser") -> None:
        """Helper method to start tracing for a browser context."""
        if not self._enable_tracing:
            return

        try:
            self._browser_context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=True,
            )
            logger.info(f"Tracing started for {context_type} context")
        except Exception as e:
            logger.error(f"Failed to start tracing for {context_type} context: {e}")

    def create_browser_context(self) -> None:
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
                _browser = getattr(browser_type, "connect")(
                    endpoint_url, timeout=120000
                )
                recording_supported = False
            else:
                _browser = getattr(browser_type, "connect_over_cdp")(
                    endpoint_url, timeout=120000
                )

            context_options = {}
            if recording_supported:
                if self._record_video:
                    context_options = {"record_video_dir": self._video_dir}
                    context_options.update(self._build_emulation_context_options())
                    logger.info("Recording video in CDP mode.")
            else:
                logger.info("Recording video not supported in given CDP URL.")
            self._browser_context = getattr(_browser, "new_context")(**context_options)
            page = getattr(_browser, "new_page")()
            page.goto("https://www.testzeus.com", timeout=120000)

        else:
            if self.browser_type != "chromium":
                disable_args = []

            browser_type = getattr(self._playwright, self.browser_type)
            self.prepare_extension()

            if self._record_video:
                self._launch_browser_with_video(browser_type, user_dir, disable_args)
            else:
                self._launch_persistent_browser(browser_type, user_dir, disable_args)

        # Start tracing only once after browser context is created
        self._start_tracing()

    def _build_emulation_context_options(self) -> Dict[str, Any]:
        """
        Combine device descriptor with user overrides (locale, timezone, geolocation,
        color scheme, plus optional permissions).
        """
        context_options: Dict[str, Any] = {}

        # 1) If device_name is set, retrieve from built-in devices
        if self.device_name and self._playwright:
            device = getattr(self._playwright, "devices").get(self.device_name)
            if device:
                context_options.update(device)
            else:
                logger.warning(
                    f"Device '{self.device_name}' not found. Using custom viewport."
                )
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

    def _launch_persistent_browser(
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
                disable_args.append(
                    f"--disable-extensions-except={self._extension_path}"
                )
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

            self._browser_context = getattr(browser_type, "launch_persistent_context")(
                user_dir, **browser_context_kwargs
            )
        except (PlaywrightError, OSError) as e:
            self._handle_launch_exception(e, user_dir, browser_type, disable_args)

    def _launch_browser_with_video(
        self,
        browser_type: BrowserType,
        user_dir: str,
        disable_args: Optional[List[str]] = None,
    ) -> None:
        logger.info(f"Launching {self.browser_type} with video recording enabled.")
        temp_user_dir = tempfile.mkdtemp(prefix="playwright-user-data-")
        # copy user_dir to temp in a separate thread
        if user_dir and os.path.exists(user_dir):
            shutil.copytree(user_dir, temp_user_dir, True)
        else:
            user_dir = temp_user_dir

        try:
            if self.browser_type == "chromium" and self._extension_path is not None:
                disable_args.append(
                    f"--disable-extensions-except={self._extension_path}"
                )
                disable_args.append(f"--load-extension={self._extension_path}")

            browser = getattr(browser_type, "launch")(
                headless=self.isheadless,
                args=disable_args,
            )

            context_options = {"record_video_dir": self._video_dir}
            context_options.update(self._build_emulation_context_options())

            self._browser_context = getattr(browser, "new_context")(**context_options)
        except Exception as e:
            logger.error(f"Failed to launch browser with video recording: {e}")
            raise e

    def _handle_launch_exception(
        self,
        e: Exception,
        user_dir: str,
        browser_type: BrowserType,
        args: Optional[List[str]] = None,
    ) -> None:
        if "Target page, context or browser has been closed" in str(e):
            new_user_dir = tempfile.mkdtemp()
            logger.error(
                f"Failed to launch persistent context with user dir {user_dir}: {e}. "
                f"Trying with a new user dir {new_user_dir}"
            )
            self._browser_context = getattr(browser_type, "launch_persistent_context")(
                new_user_dir,
                headless=self.isheadless,
                args=args or [],
            )
        elif "Chromium distribution 'chrome' is not found" in str(e):
            raise ValueError(
                "Chrome is not installed on this device. Install Google Chrome or use 'playwright install'."
            ) from None
        else:
            raise e from None

    def get_browser_context(self) -> BrowserContext:
        self.ensure_browser_context()
        if self._browser_context is None:
            raise RuntimeError("Browser context is not available.")
        return self._browser_context

    def setup_request_response_logging(self, page: Page) -> None:
        if not self.log_requests_responses:
            return
        page.on("request", self.log_request)
        page.on("response", self.log_response)

    def log_request(self, request: Any) -> None:
        """Log request details to file."""
        try:
            log_entry = {
                "type": "request",
                "url": request.url,
                "method": request.method,
                "headers": dict(request.headers),
                "timestamp": time.time(),
            }
            self._write_log_entry(log_entry, self.request_response_log_file)
        except Exception as e:
            logger.error(f"Error logging request: {e}")

    def log_response(self, response: Any) -> None:
        """Log response details to file."""
        try:
            log_entry = {
                "type": "response",
                "url": response.url,
                "status": response.status,
                "headers": dict(response.headers),
                "timestamp": time.time(),
            }
            self._write_log_entry(log_entry, self.request_response_log_file)
        except Exception as e:
            logger.error(f"Error logging response: {e}")

    def _write_log_entry(self, log_entry: Dict[str, Any], log_file: str) -> None:
        """Write a log entry to file synchronously."""
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                json.dump(log_entry, f)
                f.write("\n")
        except Exception as e:
            logger.error(f"Error writing log entry: {e}")

    def get_current_screen_state(self) -> Optional[str]:
        try:
            current_page: Page = self.get_current_page()
            return current_page.url
        except Exception as e:
            logger.warning(f"Failed to get current URL: {e}")
        return None

    def get_current_page(self) -> Page:
        try:
            browser_context = self.get_browser_context()
            pages: list[Page] = [p for p in browser_context.pages if not p.is_closed()]
            page: Optional[Page] = pages[-1] if pages else None

            if page is None:
                logger.debug("Creating new page. No pages found.")
                page = getattr(browser_context, "new_page")()
                self.setup_request_response_logging(page)
                self.setup_console_logging(page)
            return page

        except Exception as e:
            logger.warning(f"Error getting current page: {e}. Creating new context.")
            self._browser_context = None
            self.ensure_browser_context()
            browser_context = self.get_browser_context()
            pages = [p for p in browser_context.pages if not p.is_closed()]
            if pages:
                return pages[-1]
            else:
                return getattr(browser_context, "new_page")()

    def close_all_tabs(self, keep_first_tab: bool = True) -> None:
        browser_context = self.get_browser_context()
        pages: list[Page] = browser_context.pages
        if keep_first_tab:
            pages_to_close = pages[1:]
        else:
            pages_to_close = pages
        for page in pages_to_close:
            page.close()

    def close_except_specified_tab(self, page_to_keep: Page) -> None:
        browser_context = self.get_browser_context()
        for page in browser_context.pages:
            if page != page_to_keep:
                page.close()

    def go_to_homepage(self) -> None:
        page: Page = self.get_current_page()
        page.goto(self._homepage)

    def set_navigation_handler(self) -> None:
        """Set up navigation event handlers for the page and its frames."""
        page: Page = self.get_current_page()
        page.on(
            "domcontentloaded", lambda _: None
        )  # Placeholder for navigation handling

        def set_iframe_navigation_handlers() -> None:
            for frame in page.frames:
                if frame != page.main_frame:
                    frame.on(
                        "domcontentloaded", lambda _: None
                    )  # Placeholder for frame navigation handling

        set_iframe_navigation_handlers()

        page.expose_function(
            "dom_mutation_change_detected", dom_mutation_change_detected
        )

    def highlight_element(self, selector: str) -> None:
        pass

    def receive_user_response(self, response: str) -> None:
        logger.debug(f"Received user response: {response}")
        self.user_response_future = response

    def prompt_user(self, message: str) -> str:
        logger.debug(f'Prompting user with: "{message}"')
        page = self.get_current_page()

        result = input(message)
        logger.info(f'User response to "{message}": {result}')
        return result

    def set_take_screenshots(self, take_screenshots: bool) -> None:
        self._take_screenshots = take_screenshots

    def get_take_screenshots(self) -> bool:
        return self._take_screenshots

    def set_screenshots_dir(self, screenshots_dir: str) -> None:
        self._screenshots_dir = screenshots_dir

    def get_screenshots_dir(self) -> str:
        return self._screenshots_dir

    def take_screenshots(
        self,
        name: str,
        page: Optional[Page] = None,
        full_page: bool = True,
        load_state: str = "networkidle",
        take_snapshot_timeout: int = 30000,
    ) -> None:
        if not self._take_screenshots:
            return
        if page is None:
            page = self.get_current_page()

        screenshot_name = name
        if not screenshot_name.endswith(".png"):
            screenshot_name += ".png"

        os.makedirs(self._screenshots_dir, exist_ok=True)
        screenshot_path = os.path.join(self._screenshots_dir, screenshot_name)

        try:
            page.wait_for_load_state(state=load_state, timeout=take_snapshot_timeout)
            screenshot_bytes = page.screenshot(
                path=screenshot_path,
                full_page=full_page,
            )
            self._latest_screenshot_bytes = screenshot_bytes
            logger.info(f"Screenshot saved at {screenshot_path}")
        except Exception as e:
            logger.error(f"Failed to take screenshot at {screenshot_path}: {e}")

    def get_latest_screenshot_stream(self) -> Optional[BytesIO]:
        if self._latest_screenshot_bytes:
            return BytesIO(self._latest_screenshot_bytes)
        return None

    def get_latest_video_path(self) -> Optional[str]:
        if self._latest_video_path and os.path.exists(self._latest_video_path):
            return self._latest_video_path
        else:
            logger.warning("No video recording available.")
            return None

    def close_browser_context(self) -> None:
        if self._browser_context:
            if self._record_video:
                for page in self._browser_context.pages:
                    try:
                        if page.video:
                            video_path = page.video.path()
                            if self.stake_id:
                                video_name = f"{self.stake_id}.webm"
                            else:
                                video_name = "recording.webm"

                            os.makedirs(self._video_dir, exist_ok=True)
                            new_video_path = os.path.join(
                                self._video_dir,
                                video_name,
                            )

                            # rename synchronously
                            os.rename(video_path, new_video_path)
                            self._latest_video_path = new_video_path
                            logger.info(f"Video recorded at {new_video_path}")
                    except Exception as e:
                        logger.error(f"Failed to save video: {e}")
                        traceback.print_exc()

            if self._enable_tracing:
                try:
                    trace_file = os.path.join(
                        self._trace_dir, f"{self.stake_id or 'trace'}.zip"
                    )
                    os.makedirs(self._trace_dir, exist_ok=True)

                    getattr(self._browser_context, "tracing").stop(path=trace_file)

                    if os.path.exists(trace_file):
                        logger.info(f"Trace saved at {trace_file}")
                    else:
                        logger.warning(f"Trace file not found at {trace_file}")
                except Exception as e:
                    logger.error(f"Failed to save trace: {e}")
                    traceback.print_exc()

            getattr(self._browser_context, "close")()
            self._browser_context = None

    def update_processing_state(self, processing_state: str) -> None:
        pass

    def command_completed(
        self, command: str, elapsed_time: Optional[float] = None
    ) -> None:
        pass

    # Additional helpers for stable network wait
    # -------------------------------------------------------------------------
    def _wait_for_stable_network(self) -> None:
        page = self.get_current_page()
        pending_requests = set()
        last_activity = time.time()

        def on_request(request):
            pending_requests.add(request)
            nonlocal last_activity
            last_activity = time.time()

        def on_response(response):
            pending_requests.discard(response.request)
            nonlocal last_activity
            last_activity = time.time()

        page.on("request", on_request)
        page.on("response", on_response)

        # Wait for network to be idle
        while time.time() - last_activity < WAIT_FOR_NETWORK_IDLE:
            time.sleep(0.1)

        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

        logger.debug(f"Network stabilized for {WAIT_FOR_NETWORK_IDLE} ms")

    def wait_for_page_and_frames_load(
        self, timeout_overwrite: Optional[float] = None
    ) -> None:
        start_time = time.time()
        try:
            self._wait_for_stable_network()
        except Exception:
            logger.warning("Page load stable-network check failed, continuing...")

        # Calculate remaining time
        if timeout_overwrite:
            elapsed = time.time() - start_time
            remaining = max(0, timeout_overwrite - elapsed)
            logger.debug(f"Remaining timeout: {remaining}s")

    # NEW METHODS for updating context properties on the fly
    # -------------------------------------------------------------------------
    def set_size(self, width: int, height: int) -> None:
        """Change the viewport size on the current page (runtime only)."""
        page = self.get_current_page()
        page.set_viewport_size({"width": width, "height": height})
        logger.debug(f"Viewport changed to {width}x{height} (runtime)")

    def set_locale(self, locale: str) -> None:
        """
        Changing locale at runtime requires a new context in Playwright.
        This method will close the current context and create a new one.
        """
        logger.debug(f"Updating locale to {locale}")
        self.user_locale = locale
        self._recreate_browser_context()

    def set_timezone(self, timezone: str) -> None:
        logger.debug(f"Updating timezone to {timezone}")
        self.user_timezone = timezone
        self._recreate_browser_context()

    def set_geolocation(self, latitude: float, longitude: float) -> None:
        logger.debug(f"Updating geolocation to lat={latitude}, long={longitude}")
        self.user_geolocation = {"latitude": latitude, "longitude": longitude}
        context = self.get_browser_context()
        context.set_geolocation({"latitude": latitude, "longitude": longitude})

    def set_color_scheme(self, color_scheme: str) -> None:
        logger.debug(f"Updating color scheme to {color_scheme}")
        self.user_color_scheme = color_scheme
        page = self.get_current_page()
        page.emulate_media(color_scheme=self.user_color_scheme)

    def _recreate_browser_context(self) -> None:
        """Close the current context, re-create with updated emulation settings, then navigate home."""
        logger.debug("Recreating browser context to apply new emulation settings.")
        if self._browser_context:
            getattr(self._browser_context, "close")()
            self._browser_context = None
        self.create_browser_context()
        self.go_to_homepage()

    def perform_javascript_click(
        self, page: Page, selector: str, type_of_click: str
    ) -> str:
        """
        Execute a JavaScript click on an element.
        """
        js_code = """
        function findAndClick(selector, type_of_click) {
            let element = document.querySelector(selector);
            if (element) {
                element[type_of_click]();
                return "Click executed successfully";
            }
            return "Element not found";
        }
        """

        try:
            logger.info(
                f"Executing JavaScript '{type_of_click}' on element with selector: {selector}"
            )
            result: str = page.evaluate(
                get_js_with_element_finder(js_code), (selector, type_of_click)
            )
            return result
        except Exception as e:
            logger.error(f"Error executing JavaScript click: {e}")
            return f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}"

    def is_element_present(self, selector: str, page: Optional[Page] = None) -> bool:
        """Check if an element is present in DOM/Shadow DOM/iframes."""
        if page is None:
            page = self.get_current_page()

        # Try regular DOM first
        element = page.query_selector(selector)
        if element:
            return True

        # Try Shadow DOM and iframes using JavaScript
        js_code = """
        function findElement(selector) {
            // Try regular DOM first
            let element = document.querySelector(selector);
            if (element) return true;

            // Try Shadow DOM
            let elements = document.querySelectorAll('*');
            for (let elem of elements) {
                if (elem.shadowRoot) {
                    element = elem.shadowRoot.querySelector(selector);
                    if (element) return true;
                }
            }

            // Try iframes
            let iframes = document.querySelectorAll('iframe');
            for (let iframe of iframes) {
                try {
                    element = iframe.contentDocument.querySelector(selector);
                    if (element) return true;
                } catch (e) {
                    console.warn('Could not access iframe content:', e);
                }
            }

            return false;
        }
        """

        return page.evaluate_handle(get_js_with_element_finder(js_code), selector)

    def find_element(
        self, selector: str, page: Optional[Page] = None
    ) -> Optional[ElementHandle]:
        """Find element in DOM/Shadow DOM/iframes and return ElementHandle."""
        if page is None:
            page = self.get_current_page()

        # Try regular DOM first
        element = page.query_selector(selector)
        if element:
            return element

        # Try Shadow DOM and iframes using JavaScript
        js_code = """
        function findElement(selector) {
            // Try regular DOM first
            let element = document.querySelector(selector);
            if (element) return element;

            // Try Shadow DOM
            let elements = document.querySelectorAll('*');
            for (let elem of elements) {
                if (elem.shadowRoot) {
                    element = elem.shadowRoot.querySelector(selector);
                    if (element) return element;
                }
            }

            // Try iframes
            let iframes = document.querySelectorAll('iframe');
            for (let iframe of iframes) {
                try {
                    element = iframe.contentDocument.querySelector(selector);
                    if (element) return element;
                } catch (e) {
                    console.warn('Could not access iframe content:', e);
                }
            }

            return null;
        }
        """

        element = page.evaluate_handle(get_js_with_element_finder(js_code), selector)
        if element:
            return element.as_element()
        return None

    def setup_console_logging(self, page: Page) -> None:
        """Attach an event listener to capture console logs if enabled."""
        if not self.log_console:
            return

        def handle_console_message(msg):
            log_entry = {
                "type": msg.type,
                "text": msg.text,
                "timestamp": time.time(),
            }
            self._write_log_entry(log_entry, self.console_log_file)

        page.on("console", handle_console_message)
