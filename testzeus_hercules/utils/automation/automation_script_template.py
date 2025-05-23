import os
import time
import logging
import traceback
import json
from datetime import datetime
from typing import Optional, Dict, Tuple, Literal, Any, List, Callable
import asyncio
import zipfile
import httpx 
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright as playwright
from playwright.async_api import BrowserContext, BrowserType, ElementHandle
from playwright.async_api import Error as PlaywrightError  # for exception handling
import tempfile
import shutil
from playwright.async_api import Page, Playwright

# Configure the logger
logging.basicConfig(
    level=logging.INFO,  # Set the minimum logging level
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),      # Log to a file
        logging.StreamHandler()              # Also print to console
    ]
)
# Create a logger object
logger = logging.getLogger(__name__)



ALL_POSSIBLE_PERMISSIONS = [
    # "accelerometer",
    # "accessibility-events",
    # "ambient-light-sensor",
    # "background-sync",
    # "camera",
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

CHROMIUM_PERMISSIONS = [
    "clipboard-read",
    "clipboard-write",
]

# Add new constants for browser channels
BROWSER_CHANNELS = Literal[
    "chrome",
    "chrome-beta",
    "chrome-dev",
    "chrome-canary",
    "msedge",
    "msedge-beta",
    "msedge-dev",
    "msedge-canary",
    "firefox",
    "firefox-beta",
    "firefox-dev-edition",
    "firefox-nightly",
]

MAX_WAIT_PAGE_LOAD_TIME = 0.6
WAIT_FOR_NETWORK_IDLE = 2

def browser_config() -> Dict[str, Any]:
    file_path = "./config/browser_config.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as file:
            return json.load(file)
    else:
        logger.error(f"Configuration file {file_path} not found.")
        return {}
    
    
   
class PlaywrightTest:

    _instances: Dict[str, "PlaywrightTest"] = {}
    _default_instance: Optional["PlaywrightTest"] = None
    _homepage = "about:blank"

    def __new__(
        cls, *args, stake_id: Optional[str] = None, **kwargs
    ) -> "PlaywrightTest":
        # If no stake_id provided and we have a default instance, return it
        if stake_id is None:
            if cls._default_instance is None:
                # Create default instance with stake_id "0"
                instance = super().__new__(cls)
                instance.__initialized = False
                cls._default_instance = instance
                cls._instances["0"] = instance
                logger.debug(
                    "Created default PlaywrightTest instance with stake_id '0'"
                )
            return cls._default_instance

        # If stake_id provided, get or create instance for that stake_id
        if stake_id not in cls._instances:
            instance = super().__new__(cls)
            instance.__initialized = False
            cls._instances[stake_id] = instance
            logger.debug(
                f"Created new PlaywrightTest instance for stake_id '{stake_id}'"
            )
            # If this is the first instance ever, make it the default
            if cls._default_instance is None:
                cls._default_instance = instance
        return cls._instances[stake_id]
    
    
    @classmethod
    def get_instance(cls, stake_id: Optional[str] = None) -> "PlaywrightTest":
        """Get PlaywrightTest instance for given stake_id, or default instance if none provided."""
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
        """Close and remove a specific PlaywrightTest instance."""
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
        """Close all PlaywrightTest instances."""
        for stake_id in list(cls._instances.keys()):
            cls.close_instance(stake_id)


    def __init__(
        self,
        stake_id: Optional[str] = None,
        allow_all_permissions: bool = True,
       
    ):
        self.allow_all_permissions = allow_all_permissions
        if hasattr(self, "_PlaywrightManager__initialized") and self.__initialized:
            return  # Already inited, no-op
        self.__initialized = True
        self.browser_config = browser_config()
        self.browser_type = self.browser_config.get("browser_type", None)
        self.browser_channel = self.browser_config.get("browser_channel", None)
        self.browser_path = self.browser_config.get("browser_path", None)
        self.browser_version = self.browser_config.get("browser_version", None)
        self.isheadless = self.browser_config.get("headless", None)
        self.screenshots_dir = self.browser_config.get("screenshots_dir", None)
        # self.take_screenshots = self.browser_config.get("take_screenshots", None)
        self.cdp_config = self.browser_config.get("cdp_config", None)
        self.cdp_reuse_tabs = self.browser_config.get("cdp_reuse_tabs", None)
        self.cdp_navigate_on_connect = self.browser_config.get("cdp_navigate_on_connect", None)
        self.record_video = self.browser_config.get("record_video", None)
        self.video_dir = self.browser_config.get("video_dir", None)
        self.log_requests_responses = self.browser_config.get("log_requests_responses", None)
        self.device_name = self.browser_config.get("device_name", None)
        self.viewport = self.browser_config.get("viewport", None)
        self.user_locale = self.browser_config.get("locale", None)
        self.user_timezone = self.browser_config.get("timezone", None)
        self.user_geolocation  = self.browser_config.get("geolocation", None)
        self.user_color_scheme = self.browser_config.get("color_scheme", None)
        self.allow_all_permissions = self.browser_config.get("allow_all_permissions", None)
        self._take_screenshots = self.browser_config.get("take_screenshots", False)
        self._take_bounding_box_screenshots = self.browser_config.get("take_bounding_box_screenshots", None) 
        self.stake_id = stake_id or "0"
        self.enable_tracing = self.browser_config.get("enable_tracing", None)
        self.trace_dir = self.browser_config.get("trace_dir", None)
        self.browser_cookies =  self.browser_config.get("browser_cookies", None)
        self._playwright: Optional[Playwright] = None
        self._browser_context: Optional[BrowserContext] = None
        self.__async_initialize_done = False
        self._latest_screenshot_bytes: Optional[bytes] = None
        self._extension_cache_dir = os.path.join(
            ".", ".cache", "browser", self.browser_type, "extension"
        )
    
    
    async def async_initialize(self) -> None:
        if self.__async_initialize_done:
            return

        # Create required directories
        os.makedirs(self.screenshots_dir, exist_ok=True)
        os.makedirs(self.video_dir, exist_ok=True)
        if self.enable_tracing:
            os.makedirs(self.trace_dir, exist_ok=True)

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
        """
        Prepare browser extensions (uBlock Origin) if enabled in config.
        """
        # Skip if extensions are disabled in config
        if not self.browser_config.get("should_enable_ublock_extension", False):
            logger.info(
                "uBlock extension is disabled in config. Skipping installation."
            )
            return

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
                    logger.info(
                        f"Extension downloaded and saved to {extension_file_path}"
                    )
                else:
                    logger.error(
                        f"Failed to download extension from {extension_url}, "
                        f"status {response.status_code}"
                    )
                    return

        if self.browser_type == "chromium":
            extension_unzip_dir = os.path.join(extension_dir, extension_dir_name)
            if not os.path.exists(extension_unzip_dir):
                # Unzip asynchronously
                def unzip_archive(zip_path, extract_dir):
                    with zipfile.ZipFile(zip_path, "r") as zip_ref:
                        zip_ref.extractall(extract_dir)

                await asyncio.to_thread(
                    unzip_archive, extension_file_path, extension_unzip_dir
                )
            self._extension_path = extension_unzip_dir + "/uBlock0.chromium"
        elif self.browser_type == "firefox":
            self._extension_path = extension_file_path
    
    async def _start_tracing(self, context_type: str = "browser") -> None:
        """Helper method to start tracing for a browser context."""
        if not self.enable_tracing:
            return

        try:
            await self._browser_context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=True,
            )
            logger.info(f"Tracing started for {context_type} context")
        except Exception as e:

            traceback.print_exc()
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
                _browser = await browser_type.connect_over_cdp(
                    endpoint_url, timeout=120000
                )

            # Prepare context options
            context_options = {}
            if recording_supported and self._record_video:
                context_options = {"record_video_dir": self._video_dir}
                context_options.update(self._build_emulation_context_options())
                logger.info("Recording video in CDP mode.")

            self._browser_context = None
            # Context selection logic
            if _browser.contexts:
                if self.cdp_reuse_tabs:
                    logger.info("Reusing existing browser context.")
                    self._browser_context = _browser.contexts[0]

            if not self._browser_context:
                logger.info("Creating new browser context.")
                self._browser_context = await _browser.new_context(**context_options)

            # Page selection logic - More robust implementation to prevent continuous tab creation
            pages = self._browser_context.pages

            if pages:  # and self.cdp_reuse_tabs:
                # First, check if there's a non-empty page we can use
                usable_page = None

                for idx, p in enumerate(pages):
                    try:
                        # Very quick check without a timeout parameter
                        current_url = await p.evaluate("window.location.href")
                        logger.info(f"Found usable page {idx} with URL: {current_url}")

                        # Skip about:blank pages if there are better options
                        if (
                            current_url not in ["about:blank", "chrome://newtab/"]
                            or usable_page is None
                        ):
                            usable_page = p
                            # If we found a non-empty page, prefer that one
                            if current_url not in ["about:blank", "chrome://newtab/"]:
                                break
                    except Exception as e:

                        traceback.print_exc()
                        logger.debug(f"Page {idx} not usable: {e}")

                if usable_page:
                    logger.info(
                        f"Reusing existing page with URL: {await usable_page.evaluate('window.location.href')}"
                    )
                    page = usable_page

                    try:
                        # Set this page as active by bringing it to focus
                        await page.bring_to_front()
                        logger.info("Brought reused page to front")
                    except Exception as e:
                        # Don't let bring_to_front failures block us

                        traceback.print_exc()
                        logger.warning(
                            f"Failed to bring page to front, but continuing: {e}"
                        )
                else:
                    logger.info("No usable existing pages found. Creating new page.")
                    page = await self._browser_context.new_page()
            else:
                logger.info(
                    "Creating new page as no existing pages found or reuse disabled."
                )
                page = await self._browser_context.new_page()

            # Only navigate if explicitly configured to do so
            if self.cdp_navigate_on_connect:
                logger.info("Navigating to Google as specified in configuration.")
                await page.goto("https://www.google.com", timeout=120000)
            else:
                logger.info("Skipping navigation on CDP connection as configured.")

                # Only modify blank pages
                try:
                    current_url = await page.evaluate("window.location.href")
                    if current_url in ["about:blank", "chrome://newtab/"]:
                        logger.info("Setting minimal HTML content for empty tab")
                        await page.set_content(
                            "<html><body><h1>Connected via TestZeus Hercules</h1><p>Tab is ready for automation.</p></body></html>"
                        )
                        logger.info(
                            f"Tab content set, current URL: {await page.evaluate('window.location.href')}"
                        )
                except Exception as e:

                    traceback.print_exc()
                    logger.warning(f"Failed to set content for empty tab: {e}")
                    # Don't block on this failure

            # Add cookies if provided
            await self._add_cookies_if_provided()

        else:
            if self.browser_type != "chromium":
                disable_args = []

            browser_type = getattr(self._playwright, self.browser_type)
            await self.prepare_extension()

            if self.record_video:
                await self._launch_browser_with_video(
                    browser_type, user_dir, disable_args
                )
            else:
                await self._launch_persistent_browser(
                    browser_type, user_dir, disable_args
                )

        # Start tracing only once after browser context is created
        await self._start_tracing()

    def _build_emulation_context_options(self) -> Dict[str, Any]:
        """
        Build context options for emulation based on device name and other settings.
        """
        context_options = {}

        # 1) Device emulation
        if self.device_name:
            device = self._playwright.devices.get(self.device_name)
            print("########### Device #########")
            print(device)
            print("$$$$$$$$$$$$$$$")
            if device:
                context_options.update(device)
            else:
                logger.warning(
                    f"Device '{self.device_name}' not found in Playwright devices."
                )
        else:
            # Set viewport manually if no device
            context_options["viewport"] = {
                "width": self.user_viewport[0],
                "height": self.user_viewport[1],
            }

        # 2) Override locale, timezone, geolocation, color scheme
        if self.user_locale:
            context_options["locale"] = self.user_locale
        if self.user_timezone:
            context_options["timezone_id"] = self.user_timezone
        if self.user_geolocation:
            context_options["geolocation"] = self.user_geolocation
        if self.user_color_scheme:
            context_options["color_scheme"] = self.user_color_scheme

        # 3) Set permissions based on browser type and allow_all_permissions flag
        if self.allow_all_permissions:
            permissions = ALL_POSSIBLE_PERMISSIONS.copy()
            if self.browser_type == "chromium":
                permissions.extend(CHROMIUM_PERMISSIONS)
            context_options["permissions"] = permissions

        return context_options
    
    async def _launch_browser_with_video(
        self,
        browser_type: BrowserType,
        user_dir: str,
        disable_args: Optional[List[str]] = None,
    ) -> None:
        channel_info = (
            f" (channel: {self.browser_channel})" if self.browser_channel else ""
        )
        version_info = (
            f" (version: {self.browser_version})" if self.browser_version else ""
        )
        path_info = f" (custom path: {self.browser_path})" if self.browser_path else ""

        logger.info(
            f"Launching {self.browser_type}{channel_info}{version_info}{path_info} "
            f"with video recording enabled."
        )
        temp_user_dir = tempfile.mkdtemp(prefix="playwright-user-data-")
        # copy user_dir to temp in a separate thread
        if user_dir and os.path.exists(user_dir):
            await asyncio.to_thread(shutil.copytree, user_dir, temp_user_dir, True)
        else:
            user_dir = temp_user_dir

        try:
            if self.browser_type == "chromium" and self._extension_path is not None:
                disable_args.append(
                    f"--disable-extensions-except={self._extension_path}"
                )
                disable_args.append(f"--load-extension={self._extension_path}")

            launch_options = {
                "headless": self.isheadless,
                "args": disable_args or [],
            }

            # Handle browser-specific launch options
            if self.browser_type == "chromium":
                if self.browser_channel:
                    launch_options["channel"] = self.browser_channel
                # Note: version is handled during installation, not at launch time
            elif self.browser_type == "firefox":
                firefox_prefs = {
                    "app.update.auto": False,
                    "browser.shell.checkDefaultBrowser": False,
                    "media.navigator.permission.disabled": True,
                    "permissions.default.screen": 1,
                    "media.getusermedia.window.enabled": True,
                }

                # Auto-accept screen sharing if enabled in config
                if self.browser_config["should_auto_accept_screen_sharing"]:
                    firefox_prefs.update(
                        {
                            "permissions.default.camera": 1,  # 0=ask, 1=allow, 2=block
                            "permissions.default.microphone": 1,
                            "permissions.default.desktop-notification": 1,
                            "media.navigator.streams.fake": True,
                            "media.getusermedia.screensharing.enabled": True,
                            "media.getusermedia.browser.enabled": True,
                            "dom.disable_beforeunload": True,
                            "media.autoplay.default": 0,
                            "media.autoplay.enabled": True,
                            "privacy.webrtc.legacyGlobalIndicator": False,
                            "privacy.webrtc.hideGlobalIndicator": True,
                            "permissions.default.desktop": 1,
                        }
                    )

                launch_options["firefox_user_prefs"] = firefox_prefs
                # Note: version is handled during installation, not at launch time
            elif self.browser_type == "webkit":
                # WebKit doesn't support channels or direct version specification at launch
                pass

            # Add custom executable path if specified
            if self.browser_path:
                launch_options["executable_path"] = self.browser_path

            browser = await browser_type.launch(**launch_options)

            context_options = {"record_video_dir": self._video_dir}
            context_options.update(self._build_emulation_context_options())

            self._browser_context = await browser.new_context(**context_options)

            # Add cookies if provided
            await self._add_cookies_if_provided()

        except Exception as e:

            traceback.print_exc()
            logger.error(f"Failed to launch browser with video recording: {e}")
            raise e
        
    async def _launch_persistent_browser(
        self,
        browser_type: BrowserType,
        user_dir: str,
        disable_args: Optional[List[str]] = None,
    ) -> None:
        if disable_args is None:
            disable_args = []

        channel_info = (
            f" (channel: {self.browser_channel})" if self.browser_channel else ""
        )
        version_info = (
            f" (version: {self.browser_version})" if self.browser_version else ""
        )
        path_info = f" (custom path: {self.browser_path})" if self.browser_path else ""

        logger.info(
            f"Launching {self.browser_type}{channel_info}{version_info}{path_info} "
            f"with user dir: {user_dir}"
        )

        try:
            browser_context_kwargs = {
                "headless": self.isheadless,
                "args": disable_args,
            }

            # Handle browser-specific launch options
            if self.browser_type == "chromium":
                if self.browser_channel:
                    browser_context_kwargs["channel"] = self.browser_channel
                # Note: version is handled during installation, not at launch time
            elif self.browser_type == "firefox":
                firefox_prefs = {
                    "app.update.auto": False,
                    "browser.shell.checkDefaultBrowser": False,
                    "media.navigator.permission.disabled": True,
                    "permissions.default.screen": 1,
                    "media.getusermedia.window.enabled": True,
                }

                # Auto-accept screen sharing if enabled in config
                if self.browser_config["should_auto_accept_screen_sharing"]:
                    firefox_prefs.update(
                        {
                            "permissions.default.camera": 1,  # 0=ask, 1=allow, 2=block
                            "permissions.default.microphone": 1,
                            "permissions.default.desktop-notification": 1,
                            "media.navigator.streams.fake": True,
                            "media.getusermedia.screensharing.enabled": True,
                            "media.getusermedia.browser.enabled": True,
                            "dom.disable_beforeunload": True,
                            "media.autoplay.default": 0,
                            "media.autoplay.enabled": True,
                            "privacy.webrtc.legacyGlobalIndicator": False,
                            "privacy.webrtc.hideGlobalIndicator": True,
                            "permissions.default.desktop": 1,
                        }
                    )

                if self.browser_channel:
                    browser_context_kwargs["firefox_user_prefs"] = firefox_prefs
                else:
                    browser_context_kwargs["firefox_user_prefs"] = firefox_prefs
                # Note: version is handled during installation, not at launch time
            elif self.browser_type == "webkit":
                # WebKit doesn't support channels or direct version specification at launch
                pass

            # Add custom executable path if specified
            if self.browser_path:
                browser_context_kwargs["executable_path"] = self.browser_path

            # Install specific version if requested
            if self.browser_version:
                try:
                    # Install the specific version before launching
                    install_command = (
                        f"playwright install {self.browser_type}@{self.browser_version}"
                    )
                    logger.info(f"Installing browser version: {install_command}")
                    process = await asyncio.create_subprocess_shell(
                        install_command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await process.communicate()
                    if process.returncode != 0:
                        logger.error(
                            f"Failed to install browser version: {stderr.decode()}"
                        )
                except Exception as e:

                    traceback.print_exc()
                    logger.error(f"Error installing browser version: {e}")
                    raise e

            # Update browser_context_kwargs with emulation options (includes cookies if set)
            browser_context_kwargs.update(self._build_emulation_context_options())

            if self.browser_type == "chromium" and self._extension_path is not None:
                disable_args.append(
                    f"--disable-extensions-except={self._extension_path}"
                )
                disable_args.append(f"--load-extension={self._extension_path}")
            elif self.browser_type == "firefox" and self._extension_path is not None:
                # Merge with existing firefox_user_prefs if any
                firefox_user_prefs = browser_context_kwargs.get(
                    "firefox_user_prefs", {}
                )
                firefox_user_prefs.update(
                    {
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
                )
                browser_context_kwargs["firefox_user_prefs"] = firefox_user_prefs

            self._browser_context = await browser_type.launch_persistent_context(
                user_dir, **browser_context_kwargs
            )

            # Add cookies if provided
            await self._add_cookies_if_provided()

        except (PlaywrightError, OSError) as e:
            await self._handle_launch_exception(e, user_dir, browser_type, disable_args)

    async def _handle_launch_exception(
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
            launch_options = {
                "headless": self.isheadless,
                "args": args or [],
            }

            # Add channel and version based on browser type
            if self.browser_type == "chromium":
                if self.browser_channel:
                    launch_options["channel"] = self.browser_channel
                if self.browser_version:
                    launch_options["version"] = self.browser_version
            elif self.browser_type == "firefox":
                if self.browser_channel:
                    launch_options["channel"] = self.browser_channel
                if self.browser_version:
                    launch_options["version"] = self.browser_version
            elif self.browser_type == "webkit":
                if self.browser_version:
                    launch_options["version"] = self.browser_version

            if self.browser_path:
                launch_options["executable_path"] = self.browser_path

            self._browser_context = await browser_type.launch_persistent_context(
                new_user_dir, **launch_options
            )

            # Add cookies if provided
            await self._add_cookies_if_provided()

        elif any(err in str(e) for err in ["is not found", "Executable doesn't exist"]):
            channel_info = (
                f" (channel: {self.browser_channel})" if self.browser_channel else ""
            )
            version_info = (
                f" (version: {self.browser_version})" if self.browser_version else ""
            )
            path_info = (
                f" (custom path: {self.browser_path})" if self.browser_path else ""
            )

            browser_name = {
                "chromium": "Chrome",
                "firefox": "Firefox",
                "webkit": "WebKit",
            }.get(self.browser_type, self.browser_type)

            raise ValueError(
                f"{browser_name}{channel_info}{version_info}{path_info} is not installed on this device. "
                f"Install the browser or use 'playwright install {self.browser_type}' "
                f"with appropriate version arguments."
            ) from None
        else:
            raise e from None

    async def get_browser_context(self) -> BrowserContext:
        await self.ensure_browser_context()
        if self._browser_context is None:
            raise RuntimeError("Browser context is not available.")
        return self._browser_context
    
    async def get_current_url(self) -> Optional[str]:
        try:
            current_page: Page = await self.get_current_page()
            return current_page.url
        except Exception as e:

            traceback.print_exc()
            logger.warning(f"Failed to get current URL: {e}")
        return None
    
    async def get_current_page(self) -> Page:
        """
        Get the current active page, or reuse an existing one if available.
        Only creates a new page if no pages exist or all existing pages are closed.

        This is a high-level method used throughout the codebase. To ensure
        consistency, it now uses reuse_or_create_tab internally.
        """
        try:
            browser_context = await self.get_browser_context()

            # Instead of duplicating logic, use our tab reuse method with force_new_tab=False
            # This ensures the same tab reuse logic is used everywhere
            return await self.reuse_or_create_tab(force_new_tab=False)
        except Exception as e:

            traceback.print_exc()
            logger.warning(f"Error getting current page: {e}. Creating new context.")
            self._browser_context = None
            await self.ensure_browser_context()

            # Try again with the new context
            return await self.reuse_or_create_tab(force_new_tab=False)

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
        await page.wait_for_load_state("domcontentloaded")
        page.on("domcontentloaded", handle_navigation_for_mutation_observer)

        async def set_iframe_navigation_handlers() -> None:
            for frame in page.frames:
                if frame != page.main_frame:
                    frame.on(
                        "domcontentloaded", handle_navigation_for_mutation_observer
                    )

        await set_iframe_navigation_handlers()

        await page.expose_function(
            "dom_mutation_change_detected", dom_mutation_change_detected
        )
        page.on(
            "frameattached",
            lambda frame: frame.on(
                "domcontentloaded", handle_navigation_for_mutation_observer
            ),
        )

    async def highlight_element(self, selector: str) -> None:
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
        return self.screenshots_dir
    
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
            await self.wait_for_load_state_if_enabled(
                page=page, state=load_state, timeout=take_snapshot_timeout
            )
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

            traceback.print_exc()
            logger.error(f"Failed to take screenshot at {screenshot_path}: {e}")

    async def get_latest_screenshot_stream(self) -> Optional[BytesIO]:
        if not self._latest_screenshot_bytes:
            # Take a new screenshot if none exists
            page = await self.get_current_page()
            await self.take_screenshots("latest_screenshot", page)

        if self._latest_screenshot_bytes:
            return BytesIO(self._latest_screenshot_bytes)
        else:
            logger.warning("Failed to take screenshot.")
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
                            safe_url = (
                                page.url.replace("://", "_")
                                .replace("/", "_")
                                .replace(".", "_")
                                if not page.url
                                else "video_of"
                            )
                            new_video_path = os.path.join(
                                video_dir, f"{safe_url}_{video_name}"
                            )

                            # rename asynchronously
                            def rename_file(src, dst):
                                os.rename(src, dst)

                            await asyncio.to_thread(
                                rename_file, video_path, new_video_path
                            )
                            self._latest_video_path = new_video_path
                            logger.info(f"Video recorded at {new_video_path}")
                    except Exception as e:

                        traceback.print_exc()
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

                    traceback.print_exc()
                    logger.error(f"Error stopping trace: {e}")

            await self._browser_context.close()
            self._browser_context = None

    async def update_processing_state(self, processing_state: str) -> None:
        pass

    async def command_completed(
        self, command: str, elapsed_time: Optional[float] = None
    ) -> None:
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
            if headers.get("purpose") == "prefetch" or headers.get(
                "sec-fetch-dest"
            ) in ["video", "audio"]:
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
                if (len(pending_requests) == 0) and (
                    (now - last_activity) >= WAIT_FOR_NETWORK_IDLE
                ):
                    break
                if now - start_time > MAX_WAIT_PAGE_LOAD_TIME:
                    logger.debug(
                        f"Network timeout after {MAX_WAIT_PAGE_LOAD_TIME}s with {len(pending_requests)} pending requests"
                    )
                    break
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

        logger.debug(f"Network stabilized for {WAIT_FOR_NETWORK_IDLE} ms")

    async def wait_for_page_and_frames_load(
        self, timeout_overwrite: Optional[float] = None
    ) -> None:
        """Wait for the page and all frames to load."""
        page = await self.get_current_page()

        try:
            await self._wait_for_stable_network()
        except Exception as e:

            traceback.print_exc()
            logger.warning("Page load stable-network check failed, continuing...")

    async def wait_for_load_state_if_enabled(
        self,
        page: Page,
        state: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> None:
        """
        Wait for the page to reach a specific state if wait_for_load_state is enabled.

        Args:
            page: The playwright page object
            state: The state to wait for (load, domcontentloaded, networkidle)
            timeout: Maximum time to wait for in milliseconds
        """
        if not self.browser_config.get("should_skip_wait_for_load_state", False):
            await page.wait_for_load_state(state=state, timeout=timeout)

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
        # Note: create_browser_context already calls _add_cookies_if_provided
        await self.go_to_homepage()

    async def perform_javascript_click(
        self, page: Page, selector: str, type_of_click: str
    ) -> str:
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

            // Check if we're in Salesforce
            const isSalesforce = window.location.href.includes('lightning/') || 
                                window.location.href.includes('force.com') || 
                                document.querySelector('.slds-dropdown, lightning-base-combobox') !== null;
                                
            // Check if element is SVG or SVG child
            const isSvgElement = element.tagName.toLowerCase() === 'svg' || 
                                element.ownerSVGElement !== null ||
                                element.namespaceURI === 'http://www.w3.org/2000/svg';

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
                    // For SVG elements or Salesforce, use event sequence approach
                    if (isSvgElement || isSalesforce) {
                        // SVG elements need full event sequence
                        // Create and dispatch mousedown event first
                        const mouseDown = new MouseEvent('mousedown', {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: centerX,
                            clientY: centerY,
                            button: 0
                        });
                        element.dispatchEvent(mouseDown);
                        
                        const mouseUpEvent = new MouseEvent('mouseup', {
                            bubbles: true,
                            cancelable: true,
                            clientX: centerX,
                            clientY: centerY,
                            button: 0,
                            view: window
                        });
                        element.dispatchEvent(mouseUpEvent);

                        const clickEvent = new MouseEvent('click', {
                            bubbles: true,
                            cancelable: true,
                            clientX: centerX,
                            clientY: centerY,
                            button: 0,
                            view: window
                        });
                        element.dispatchEvent(clickEvent);
                    } else {
                        // For regular HTML elements, try direct click first, fallback to event sequence
                        try {
                            // Try the native click method first
                            element.click();
                        } catch (error) {
                            console.log('Native click failed, using event sequence');
                            // Fallback to event sequence
                            const mouseDown = new MouseEvent('mousedown', {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                clientX: centerX,
                                clientY: centerY,
                                button: 0
                            });
                            element.dispatchEvent(mouseDown);
                            
                            const mouseUpEvent = new MouseEvent('mouseup', {
                                bubbles: true,
                                cancelable: true,
                                clientX: centerX,
                                clientY: centerY,
                                button: 0,
                                view: window
                            });
                            element.dispatchEvent(mouseUpEvent);

                            const clickEvent = new MouseEvent('click', {
                                bubbles: true,
                                cancelable: true,
                                clientX: centerX,
                                clientY: centerY,
                                button: 0,
                                view: window
                            });
                            element.dispatchEvent(clickEvent);
                            
                            // If it's a link and click wasn't prevented, handle navigation
                            if (element.tagName.toLowerCase() === 'a' && element.href) {
                                window.location.href = element.href;
                            }
                        }
                    }
                    break;
            }

            const ariaExpandedAfterClick = element.getAttribute('aria-expanded');
            if (ariaExpandedBeforeClick === 'false' && ariaExpandedAfterClick === 'true') {
                return "Executed " + type_of_click + " on element with selector: " + selector + 
                    ". Very important: As a consequence, a menu has appeared where you may need to make further selection. " +
                    "Very important: Get all_fields DOM to complete the action." + " The click is best effort, so verify the outcome.";
            }
            return "Executed " + type_of_click + " on element with selector: " + selector + " The click is best effort, so verify the outcome.";
        }"""

        try:
            logger.info(
                f"Executing JavaScript '{type_of_click}' on element with selector: {selector}"
            )
            result: str = await page.evaluate(
                get_js_with_element_finder(js_code), (selector, type_of_click)
            )
            logger.debug(
                f"Executed JavaScript '{type_of_click}' on element with selector: {selector}"
            )
            return result
        except Exception as e:

            traceback.print_exc()
            logger.error(
                f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}. Error: {e}"
            )
            traceback.print_exc()
            return f"Error executing JavaScript '{type_of_click}' on element with selector: {selector}"

    async def is_element_present(
        self, selector: str, page: Optional[Page] = None
    ) -> bool:
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

    async def find_element(
        self,
        selector: str,
        page: Optional[Page] = None,
        element_name: Optional[str] = None,
    ) -> Optional[ElementHandle]:
        """Find element in DOM/Shadow DOM/iframes and return ElementHandle.
        Also captures a screenshot with the element's bounding box and metadata overlay if enabled.

        Args:
            selector: The selector to find the element
            page: Optional page instance to search in
            element_name: Optional friendly name for the element (used in screenshot naming)
        """
        if page is None:
            page = await self.get_current_page()

        # Try regular DOM first
        element = await page.query_selector(selector)
        if element:
            if self._take_bounding_box_screenshots:
                await self._capture_element_with_bbox(
                    element, page, selector, element_name
                )
            return element

        # Check Shadow DOM and iframes
        js_code = """(selector) => {
            /*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/
            return findElementInShadowDOMAndIframes(document, selector);
        }"""

        element = await page.evaluate_handle(
            get_js_with_element_finder(js_code), selector
        )
        if element:
            element_handle = element.as_element()
            if element_handle:
                if self._take_bounding_box_screenshots:
                    await self._capture_element_with_bbox(
                        element_handle, page, selector, element_name
                    )
            return element_handle

        return None

    async def _capture_element_with_bbox(
        self,
        element: ElementHandle,
        page: Page,
        selector: str,
        element_name: Optional[str] = None,
    ) -> None:
        """Capture screenshot with bounding box and metadata overlay."""
        try:
            # Get element's bounding box
            bbox = await element.bounding_box()
            if not bbox:
                return

            # Get element's accessibility info
            accessibility_info = await element.evaluate(
                """element => {
                return {
                    ariaLabel: element.getAttribute('aria-label'),
                    role: element.getAttribute('role'),
                    name: element.getAttribute('name'),
                    title: element.getAttribute('title')
                }
            }"""
            )

            # Use the first non-empty value from accessibility info
            element_identifier = next(
                (
                    val
                    for val in [
                        accessibility_info.get("ariaLabel"),
                        accessibility_info.get("role"),
                        accessibility_info.get("name"),
                        accessibility_info.get("title"),
                    ]
                    if val
                ),
                "element",  # default if no accessibility info found
            )

            # Construct screenshot name
            screenshot_name = f"{element_identifier}_{element_name or selector}_bbox_{int(datetime.now().timestamp())}"

            # Take screenshot using existing method
            await self.take_screenshots(
                name=screenshot_name,
                page=page,
                full_page=True,
                include_timestamp=False,
            )

            # Get the latest screenshot using get_latest_screenshot_stream
            screenshot_stream = await self.get_latest_screenshot_stream()
            if not screenshot_stream:
                logger.error("Failed to get screenshot for bounding box overlay")
                return

            image = Image.open(screenshot_stream)
            draw = ImageDraw.Draw(image)

            # Draw bounding box
            draw.line(
                [
                    (bbox["x"], bbox["y"]),
                    (bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]),
                ],
                width=4,
            )

            # Prepare metadata text
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            url = page.url
            test_name = self.stake_id or "default"
            element_info = f"Element: {element_identifier} by {element_name}"

            # Create metadata text block with word wrapping
            metadata = [
                f"Timestamp: {current_time}",
                f"URL: {url}",
                f"Test: {test_name}",
                element_info,
            ]

            # Calculate text position and size
            try:
                font = ImageFont.truetype("Arial", 14)
            except Exception as e:
                logger.error(f"Failed to load font: {e}")
                font = ImageFont.load_default()

            # Increase text padding by 10%
            text_padding = 11  # Original 10 + 10%
            line_height = 22  # Original 20 + 10%

            # Calculate text dimensions with word wrapping
            max_width = min(
                image.width * 0.4, 400
            )  # Reduced from 500px to 400px for better wrapping
            wrapped_lines = []

            for text in metadata:
                if text.startswith("URL: "):
                    # Special handling for URLs - break into chunks
                    url_prefix = "URL: "
                    url_text = text[len(url_prefix) :]
                    current_line = url_prefix

                    # Break URL into segments of reasonable length
                    segment_length = (
                        40  # Adjust this value to control URL segment length
                    )
                    start = 0
                    while start < len(url_text):
                        end = start + segment_length
                        if end < len(url_text):
                            # Look for a good breaking point
                            break_chars = ["/", "?", "&", "-", "_", "."]
                            for char in break_chars:
                                pos = url_text[start : end + 10].find(char)
                                if pos != -1:
                                    end = start + pos + 1
                                    break
                        else:
                            end = len(url_text)

                        segment = url_text[start:end]
                        if start == 0:
                            wrapped_lines.append(url_prefix + segment)
                        else:
                            wrapped_lines.append(" " * len(url_prefix) + segment)
                        start = end
                else:
                    # Normal text wrapping for non-URL lines
                    words = text.split()
                    current_line = words[0]
                    for word in words[1:]:
                        test_line = current_line + " " + word
                        test_width = draw.textlength(test_line, font=font)
                        if test_width <= max_width:
                            current_line = test_line
                        else:
                            wrapped_lines.append(current_line)
                            current_line = word
                    wrapped_lines.append(current_line)

            # Calculate background dimensions with some extra padding
            bg_width = max_width + (text_padding * 2)
            bg_height = (line_height * len(wrapped_lines)) + (text_padding * 2)

            # Draw background rectangle for metadata
            bg_x = image.width - bg_width - text_padding
            bg_y = text_padding

            # Draw semi-transparent background
            bg_color = (0, 0, 0, 128)
            bg_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            bg_draw = ImageDraw.Draw(bg_layer)
            bg_draw.rectangle(
                [bg_x, bg_y, bg_x + bg_width, bg_y + bg_height], fill=bg_color
            )

            # Composite the background onto the main image
            image = Image.alpha_composite(image.convert("RGBA"), bg_layer)
            draw = ImageDraw.Draw(image)

            # Draw wrapped text
            current_y = bg_y + text_padding
            for line in wrapped_lines:
                draw.text(
                    (bg_x + text_padding, current_y), line, fill="white", font=font
                )
                current_y += line_height

            # Save the modified screenshot
            screenshot_path = os.path.join(
                self.get_screenshots_dir(), f"{screenshot_name}.png"
            )

            # Convert back to RGB before saving as PNG
            image = image.convert("RGB")
            image.save(screenshot_path, "PNG")

            logger.debug(f"Saved bounding box screenshot: {screenshot_path}")

        except Exception as e:
            logger.error(f"Failed to capture element with bounding box: {e}")
            traceback.print_exc()

    async def _add_cookies_if_provided(self) -> None:
        """
        Add cookies to the browser context if they are provided in the configuration.
        This method should be called after the browser context is created.
        """
        if self.browser_cookies and self._browser_context:
            try:
                logger.info(
                    f"Adding {len(self.browser_cookies)} cookies to browser context"
                )
                await self._browser_context.add_cookies(self.browser_cookies)
                logger.info("Cookies added successfully")
            except Exception as e:

                traceback.print_exc()
                logger.error(f"Failed to add cookies to browser context: {e}")

    async def reuse_or_create_tab(self, force_new_tab: bool = False) -> Page:
        """
        Reuse an existing tab or create a new one if needed.

        Args:
            force_new_tab: If True, always create a new tab regardless of existing tabs

        Returns:
            A Page object (either existing or newly created)
        """
        context = await self.get_browser_context()

        # Get all non-closed pages
        pages = [p for p in context.pages if not p.is_closed()]
        logger.debug(f"Found {len(pages)} existing tabs")

        # If we need to create a new tab or there are no existing tabs
        if force_new_tab or not pages:
            logger.info("Creating a new tab (forced or no existing tabs)")
            page = await context.new_page()
            return page

        # Try to reuse existing tab (use the most recent one)
        page = pages[-1]  # The most recently used page

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as e:
            logger.warning(f"Failed to wait for networkidle: {e}")
        finally:
            await page.wait_for_load_state("domcontentloaded")

        # Check if the page is responsive, with a shorter timeout to avoid hanging
        try:
            # Simple check (the timeout is applied at a higher level)
            await page.evaluate("1")
            logger.info(f"Reusing existing tab with URL: {page.url}")
            # try:
            #     # Bring the tab to the front
            #     await page.bring_to_front()
            # except Exception as e:

            #     traceback.print_exc()
            #     # Don't let bring_to_front failures prevent tab reuse
            #     logger.warning(f"Failed to bring tab to front, but continuing: {e}")

            return page
        except Exception as e:

            traceback.print_exc()
            # If the page isn't responsive, try the next one
            logger.warning(f"First tab not responsive: {e}")

            # Try other tabs if available, from most to least recent
            for i in range(len(pages) - 2, -1, -1):
                try:
                    page = pages[i]
                    await page.wait_for_load_state("domcontentloaded")
                    await page.evaluate("1")
                    logger.info(f"Reusing alternative tab with URL: {page.url}")

                    try:
                        await page.bring_to_front()
                    except Exception as bring_err:

                        traceback.print_exc()
                        logger.warning(
                            f"Failed to bring tab to front, but continuing: {bring_err}"
                        )

                    return page
                except Exception as tab_err:

                    traceback.print_exc()
                    logger.warning(f"Alternative tab {i} not responsive: {tab_err}")

        # If all tabs are unresponsive, create a new one
        logger.info("All existing tabs unresponsive, creating a new tab")
        page = await context.new_page()
        return page

# -------------------------------------------------------------------------
# JS Helper Functions
# -------------------------------------------------------------------------

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
    """
    Combines the element finder code with specific action code.

    Args:
        action_js_code: JavaScript code that uses findElementInShadowDOMAndIframes

    Returns:
        Combined JavaScript code
    """
    pattern = "/*INJECT_FIND_ELEMENT_IN_SHADOW_DOM*/"
    if pattern in action_js_code:
        return action_js_code.replace(pattern, TEMPLATES["FIND_ELEMENT_IN_SHADOW_DOM"])
    else:
        return action_js_code
    


# -------------------------------------------------------------------------
# DOM Mutation Observer
# -------------------------------------------------------------------------
DOM_change_callback: list[Callable[[str], None]] = []

async def add_mutation_observer(page: Page) -> None:
    """
    Adds a mutation observer to the page to detect changes in the DOM.
    When changes are detected, the observer calls the dom_mutation_change_detected function in the browser context.
    This changes can be detected by subscribing to the dom_mutation_change_detected function by individual tools.

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

async def handle_navigation_for_mutation_observer(page: Page) -> None:
    await add_mutation_observer(page)

async def dom_mutation_change_detected(changes_detected: str) -> None:
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
