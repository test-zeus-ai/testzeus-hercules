import os
import logging
import traceback
from typing import Optional, Dict, Tuple


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


class PlaywrightTest:

    def __init__(
        self,
        browser_type: Optional[str] = None,
        browser_channel = None,
        browser_path: Optional[str] = None,
        browser_version: Optional[str] = None,  # New parameter for browser version
        headless: Optional[bool] = None,
        screenshots_dir: Optional[str] = None,
        take_screenshots: Optional[bool] = None,
        cdp_config: Optional[Dict] = None,
        cdp_reuse_tabs: Optional[bool] = False,  # New parameter to control tab reuse
        cdp_navigate_on_connect: Optional[bool] = True,  # New parameter to control navigation
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
        take_bounding_box_screenshots: Optional[bool] = None, 
    ):
        self.browser_type = browser_type
        self.browser_channel = browser_channel
        self.browser_path = browser_path
        self.browser_version = browser_version
        self.headless = headless
        self.screenshots_dir = screenshots_dir
        self.take_screenshots = take_screenshots    
        self.cdp_config = cdp_config
        self.cdp_reuse_tabs = cdp_reuse_tabs
        self.cdp_navigate_on_connect = cdp_navigate_on_connect
        self.record_video = record_video
        self.video_dir = video_dir
        self.log_requests_responses = log_requests_responses
        self.request_response_log_file = request_response_log_file  
        self.device_name = device_name
        self.viewport = viewport
        self.locale = locale
        self.timezone = timezone
        self.geolocation = geolocation
        self.color_scheme = color_scheme
        self.allow_all_permissions = allow_all_permissions
        self.log_console = log_console
        self.console_log_file = console_log_file
        self.take_bounding_box_screenshots = take_bounding_box_screenshots  
    
    
    async def prepare_extension(self) -> None:
        """
        Prepare browser extensions (uBlock Origin) if enabled in config.
        """
        # Skip if extensions are disabled in config
        if not get_global_conf().should_enable_ublock_extension():
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

            if self._record_video:
                await self._launch_browser_with_video(
                    browser_type, user_dir, disable_args
                )
            else:
                await self._launch_persistent_browser(
                    browser_type, user_dir, disable_args
                )

        # Start tracing only once after browser context is created
        await self._start_tracing()
