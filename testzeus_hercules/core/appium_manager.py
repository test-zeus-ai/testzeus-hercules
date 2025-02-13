import asyncio
import subprocess
from subprocess import Popen
import time
import os
import base64
import xml.etree.ElementTree as ET
import io
from typing import Optional, Dict, Any, List, TypeVar, Union, cast

from appium import webdriver
from appium.webdriver.webdriver import WebDriver
from appium.options.common import AppiumOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webelement import WebElement
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

from PIL import Image


class AppiumManager:
    """
    Manages an Appium session and, optionally, the Appium server and emulator.
    Provides:
      - Basic interaction methods (click, enter text, clear text, long press, tap, swipe)
        with pre/post screenshot capture.
      - End-to-end session (screen) recording.
      - Screenshot and artifact management (screenshots, videos, logs).
      - Accessibility tree snapshot (a simplified UI hierarchy).
      - Device/emulator startup and connection.
      - Device and Appium log capture for debugging.
      - see_screen: returns the current screenshot as a PIL Image.
      - get_viewport_size: returns the current screen resolution of the device.
      
    A stake_id is maintained (default "0") so that the artifacts (proof path) are created
    accordingly, but the singleton remains single instance.
    
    If a remote appium_server_url is provided (or found in configuration),
    a local server is not started.
    """

    _instances: Dict[str, "AppiumManager"] = {}
    _default_instance: Optional["AppiumManager"] = None
    _initialized: bool = False

    def __new__(cls, *args: Any, stake_id: Optional[str] = None, **kwargs: Any) -> "AppiumManager":
        # If no stake_id provided and we have a default instance, return it
        if stake_id is None:
            if cls._default_instance is None:
                # Create default instance with stake_id "0"
                instance = super().__new__(cls)
                instance._initialized = False
                cls._default_instance = instance
                cls._instances["0"] = instance
                logger.debug("Created default AppiumManager instance with stake_id '0'")
            return cls._default_instance

        # If stake_id provided, get or create instance for that stake_id
        if stake_id not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instances[stake_id] = instance
            logger.debug(f"Created new AppiumManager instance for stake_id '{stake_id}'")
            # If this is the first instance ever, make it the default
            if cls._default_instance is None:
                cls._default_instance = instance
        return cls._instances[stake_id]

    @classmethod
    def get_instance(cls, stake_id: Optional[str] = None) -> "AppiumManager":
        """Get AppiumManager instance for given stake_id, or default instance if none provided."""
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
        """Close and remove a specific AppiumManager instance."""
        target_id = stake_id if stake_id is not None else "0"
        if target_id in cls._instances:
            instance = cls._instances[target_id]
            asyncio.create_task(instance.stop_appium())
            del cls._instances[target_id]
            if instance == cls._default_instance:
                cls._default_instance = None
                # If there are other instances, make the first one the default
                if cls._instances:
                    cls._default_instance = next(iter(cls._instances.values()))

    @classmethod
    def close_all_instances(cls) -> None:
        """Close all AppiumManager instances."""
        for stake_id in list(cls._instances.keys()):
            cls.close_instance(stake_id)

    async def stop_appium(self) -> None:
        """Stop all Appium-related resources."""
        await self.quit_session()
        await self.stop_appium_server()
        await self.stop_emulator()

    def __init__(
        self,
        stake_id: Optional[str] = None,
        appium_server_url: Optional[str] = None,
        start_server: bool = True,
        server_port: int = 4723,
        platformName: Optional[str] = None,
        deviceName: Optional[str] = None,
        automationName: Optional[str] = None,
        app: Optional[str] = None,
        extra_capabilities: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the AppiumManager.

        A stake_id is stored (defaulting to "0"). If a remote appium_server_url is provided,
        a local server is not started.
        """
        # Avoid reinitialization in a singleton.
        if self._initialized:
            return
        self._initialized = True

        self.stake_id = stake_id or "0"

        conf = get_global_conf()
        self.appium_server_url = appium_server_url or conf.get_appium_server_url()
        # If a server URL is provided, skip starting a local server.
        self.start_server = start_server if self.appium_server_url is None else False
        self.server_port = server_port or conf.get_appium_server_port() or 4723

        self.platformName = platformName or conf.get_platform_name() or "Android"
        self.deviceName = deviceName or conf.get_device_name() or "emulator-5554"
        self.automationName = automationName or conf.get_automation_name() or "UiAutomator2"
        self.app = app or conf.get_app_path() or ""
        self.extra_capabilities = extra_capabilities or conf.get_appium_capabilities() or {}

        self.driver: Optional[WebDriver] = None
        self.appium_process: Optional[Popen[bytes]] = None
        self.emulator_process: Optional[Popen[bytes]] = None

        # For capturing Appium server logs.
        self._log_tasks: List[asyncio.Task[None]] = []

        # Artifact folders (screenshots, videos, logs) will be created based on proof_path.
        self._screenshots_dir: Optional[str] = None
        self._video_dir: Optional[str] = None
        self._logs_dir: Optional[str] = None

        logger.debug(
            f"AppiumManager init (stake_id={self.stake_id}) - platformName={self.platformName}, "
            f"deviceName={self.deviceName}, automationName={self.automationName}, app={self.app}"
        )

    async def setup_artifacts(self) -> None:
        """
        Setup artifact directories for screenshots, videos, and logs.
        Uses the stake_id when retrieving the proof path.
        """
        proof_path = get_global_conf().get_proof_path(test_id=self.stake_id)
        self._screenshots_dir = os.path.join(proof_path, "screenshots")
        self._video_dir = os.path.join(proof_path, "videos")
        self._logs_dir = os.path.join(proof_path, "logs")
        os.makedirs(self._screenshots_dir, exist_ok=True)
        os.makedirs(self._video_dir, exist_ok=True)
        os.makedirs(self._logs_dir, exist_ok=True)
        logger.info(f"Artifact directories set up at {proof_path}")

    async def async_initialize(self) -> None:
        """
        Asynchronously initialize the AppiumManager.

        This creates artifact directories, starts the Appium server (if needed),
        and creates a new Appium session.
        """
        await self.setup_artifacts()
        if self.start_server:
            await self.start_appium_server()
        await self.create_session()

    # ─── APPIUM SERVER & EMULATOR MANAGEMENT ─────────────────────────────

    async def start_appium_server(self) -> None:
        """
        Spawn a new Appium server instance using subprocess.
        Also starts tasks to capture server stdout/stderr logs.
        """
        if self.appium_process is not None:
            logger.info("Appium server already running.")
            return

        command = ["appium", "--address", "0.0.0.0", "-p", str(self.server_port)]
        logger.info(f"Starting Appium server with command: {' '.join(command)}")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.appium_process = cast(Popen[bytes], process)
            # Start log capture tasks.
            if self._logs_dir and self.appium_process and self.appium_process.stdout and self.appium_process.stderr:
                self._log_tasks.append(
                    asyncio.create_task(self._capture_stream(self.appium_process.stdout, "appium_stdout.log"))
                )
                self._log_tasks.append(
                    asyncio.create_task(self._capture_stream(self.appium_process.stderr, "appium_stderr.log"))
                )
            # Allow time for the server to start.
            await asyncio.sleep(3)
            logger.info("Appium server started successfully.")
        except Exception as e:
            logger.error(f"Failed to start Appium server: {e}")
            raise e

    async def _capture_stream(self, stream: asyncio.StreamReader, file_name: str) -> None:
        """
        Capture output from a given stream and write it to a log file.
        """
        if not self._logs_dir:
            return
        log_file_path = os.path.join(self._logs_dir, file_name)
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    f.write(line.decode("utf-8"))
        except Exception as e:
            logger.error(f"Error capturing stream for {file_name}: {e}")

    async def stop_appium_server(self) -> None:
        """
        Stop the spawned Appium server and cancel any log capture tasks.
        """
        if self.appium_process:
            logger.info("Stopping Appium server.")
            self.appium_process.terminate()
            try:
                process = cast(Popen[bytes], self.appium_process)
                # Create a Future for the process.wait()
                wait_future = asyncio.create_task(process.wait())
                await asyncio.wait_for(wait_future, timeout=10.0)
                logger.info("Appium server stopped.")
            except asyncio.TimeoutError:
                logger.warning("Appium server did not stop in time; killing process.")
                self.appium_process.kill()
            self.appium_process = None

        for task in self._log_tasks:
            task.cancel()
        self._log_tasks.clear()

    async def stop_emulator(self) -> None:
        """
        Stop the emulator process gracefully.
        First attempts to terminate the process, then kills it if termination times out.
        """
        if self.emulator_process:
            logger.info("Stopping emulator process.")
            try:
                process = cast(Popen[bytes], self.emulator_process)
                # Create a Future for the process.wait()
                wait_future = asyncio.create_task(process.wait())
                process.terminate()
                try:
                    await asyncio.wait_for(wait_future, timeout=10.0)
                    logger.info("Emulator process terminated gracefully.")
                except asyncio.TimeoutError:
                    logger.warning("Emulator process did not stop in time; killing process.")
                    process.kill()
            except ProcessLookupError:
                logger.info("Emulator process has already exited.")
            except Exception as e:
                logger.error(f"Error while stopping emulator process: {e}")
            finally:
                self.emulator_process = None

    async def start_emulator(self, avd_name: Optional[str] = None) -> None:
        """
        Start an Android emulator using the provided AVD name or one from configuration.
        """
        if not avd_name:
            avd_name = get_global_conf().get_emulator_avd_name() or "Pixel_3_API_30"
        command = ["emulator", "-avd", avd_name]
        logger.info(f"Starting emulator with AVD: {avd_name}")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.emulator_process = cast(Popen[bytes], process)
            # Wait until the emulator boots up (ideally poll for boot completion).
            await asyncio.sleep(10)
            logger.info("Emulator started successfully.")
        except Exception as e:
            logger.error(f"Error starting emulator: {e}")
            raise e

    async def create_emulator_and_connect(self, avd_name: Optional[str] = None) -> None:
        """
        Start the emulator and then initialize the Appium session.
        """
        await self.start_emulator(avd_name)
        # Optionally wait additional time for the device to boot completely.
        await asyncio.sleep(40)
        await self.async_initialize()

    async def create_session(self) -> None:
        """
        Create an Appium session by initializing the WebDriver.
        """
        desired_caps = {
            "platformName": self.platformName,
            "deviceName": self.deviceName,
            "automationName": self.automationName,
        }
        if self.app:
            desired_caps["app"] = self.app

        # Merge any extra capabilities.
        desired_caps.update(self.extra_capabilities)

        server_url = self.appium_server_url or f"http://127.0.0.1:{self.server_port}"
        logger.info(f"Creating Appium session with server: {server_url} and capabilities: {desired_caps}")
        try:
            loop = asyncio.get_running_loop()
            
            # Create AppiumOptions instance with modern W3C capabilities
            options = AppiumOptions()
            # Add W3C standard capabilities
            for k, v in desired_caps.items():
                options.set_capability(str(k), str(v))
            
            # Initialize WebDriver with W3C protocol
            self.driver = await loop.run_in_executor(
                None, lambda: webdriver.Remote(
                    command_executor=server_url,
                    options=options
                )
            )
            logger.info("Appium session created successfully.")
        except Exception as e:
            
            logger.error(f"Failed to create Appium session: {e}")
            raise e

    async def quit_session(self) -> None:
        """
        Quit the Appium session and clean up the driver.
        """
        if self.driver:
            logger.info("Quitting Appium session.")
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.driver.quit)
                logger.info("Appium session quit successfully.")
            except Exception as e:
                logger.error(f"Error while quitting Appium session: {e}")
            self.driver = None

    # ─── SCREENSHOT & SESSION RECORDING ─────────────────────────────────────────

    async def take_screenshot(self, name: str, include_timestamp: bool = True) -> Optional[str]:
        """
        Capture a screenshot and save it in the screenshots directory.
        Returns the full path to the screenshot file.
        """
        if not self.driver:
            logger.error("No Appium session available for taking screenshot.")
            return None
        if not self._screenshots_dir:
            logger.error("Screenshots directory not set.")
            return None

        timestamp = f"_{int(time.time_ns())}" if include_timestamp else ""
        filename = f"{name}{timestamp}.png"
        file_path = os.path.join(self._screenshots_dir, filename)
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            await loop.run_in_executor(None, lambda: driver.get_screenshot_as_file(file_path))
            logger.info(f"Screenshot saved: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error taking screenshot {file_path}: {e}")
            return None

    async def start_screen_recording(self) -> None:
        """
        Start screen recording using Appium's built-in API.
        """
        if not self.driver:
            logger.error("No Appium session available for screen recording.")
            return
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            await loop.run_in_executor(None, lambda: driver.start_recording_screen())
            logger.info("Screen recording started.")
        except Exception as e:
            logger.error(f"Error starting screen recording: {e}")

    async def stop_screen_recording(self) -> Optional[str]:
        """
        Stop screen recording and save the video in the videos directory.
        Returns the path to the saved video.
        """
        if not self.driver:
            logger.error("No Appium session available for stopping screen recording.")
            return None
        if not self._video_dir:
            logger.error("Videos directory not set.")
            return None
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            recording_data = await loop.run_in_executor(None, lambda: driver.stop_recording_screen())
            video_bytes = base64.b64decode(recording_data)
            video_path = os.path.join(self._video_dir, f"recording_{int(time.time())}.mp4")
            with open(video_path, "wb") as f:
                f.write(video_bytes)
            logger.info(f"Screen recording saved to {video_path}")
            return video_path
        except Exception as e:
            logger.error(f"Error stopping screen recording: {e}")
            return None

    # ─── DEVICE LOG CAPTURE ─────────────────────────────────────────────────────

    async def capture_device_logs(self) -> None:
        """
        Capture device logs (e.g., logcat for Android or syslog for iOS)
        and save them to a file in the logs directory.
        """
        if not self.driver:
            logger.error("No Appium session available to capture device logs.")
            return
        if not self._logs_dir:
            logger.error("Logs directory not set.")
            return
        try:
            if not isinstance(self.driver, WebDriver):
                logger.error("Driver is not properly initialized")
                return
                
            loop = asyncio.get_running_loop()
            log_type = "logcat" if self.platformName.lower() == "android" else "syslog"
            logs = await loop.run_in_executor(None, lambda: self.driver.get_log(log_type))
            log_file_path = os.path.join(self._logs_dir, f"device_logs_{int(time.time())}.txt")
            with open(log_file_path, "w", encoding="utf-8") as f:
                for entry in logs:
                    f.write(f"{entry}\n")
            logger.info(f"Device logs captured in {log_file_path}")
        except Exception as e:
            logger.error(f"Error capturing device logs: {e}")

    # ─── BASIC INTERACTION METHODS WITH PRE/POST SCREENSHOTS ───────────────────

    async def click_by_accessibility(self, accessibility_name: str) -> None:
        """
        Click on an element identified by its accessibility name.
        Captures a screenshot before and after the click.
        """
        await self.take_screenshot(f"before_click_{accessibility_name}")
        if not self.driver:
            logger.error("No Appium session available for click action.")
            return
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            element = await loop.run_in_executor(
                None, lambda: driver.find_element(By.ACCESSIBILITY_ID, accessibility_name)
            )
            await loop.run_in_executor(None, element.click)
            logger.info(f"Clicked on element with accessibility: {accessibility_name}")
        except Exception as e:
            logger.error(f"Error clicking element with accessibility '{accessibility_name}': {e}")
        await self.take_screenshot(f"after_click_{accessibility_name}")

    async def enter_text_by_accessibility(self, accessibility_name: str, text: str) -> None:
        """
        Enter text into an element identified by its accessibility name.
        Captures a screenshot before and after entering text.
        """
        await self.take_screenshot(f"before_enter_text_{accessibility_name}")
        if not self.driver:
            logger.error("No Appium session available for entering text.")
            return
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            element = await loop.run_in_executor(
                None, lambda: driver.find_element(By.ACCESSIBILITY_ID, accessibility_name)
            )
            await loop.run_in_executor(None, lambda: element.send_keys(text))
            logger.info(f"Entered text into element with accessibility: {accessibility_name}")
        except Exception as e:
            logger.error(f"Error entering text in element with accessibility '{accessibility_name}': {e}")
        await self.take_screenshot(f"after_enter_text_{accessibility_name}")

    async def clear_text_by_accessibility(self, accessibility_name: str) -> None:
        """
        Clear text from an element identified by its accessibility name.
        Captures a screenshot before and after clearing the text.
        """
        await self.take_screenshot(f"before_clear_text_{accessibility_name}")
        if not self.driver:
            logger.error("No Appium session available for clearing text.")
            return
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            element = await loop.run_in_executor(
                None, lambda: driver.find_element(By.ACCESSIBILITY_ID, accessibility_name)
            )
            await loop.run_in_executor(None, element.clear)
            logger.info(f"Cleared text in element with accessibility: {accessibility_name}")
        except Exception as e:
            logger.error(f"Error clearing text in element with accessibility '{accessibility_name}': {e}")
        await self.take_screenshot(f"after_clear_text_{accessibility_name}")

    async def long_press_by_accessibility(self, accessibility_name: str, duration: int = 1000) -> None:
        """
        Perform a long press on an element identified by its accessibility name.
        Duration is in milliseconds.
        Captures a screenshot before and after the long press.
        """
        await self.take_screenshot(f"before_long_press_{accessibility_name}")
        if not self.driver:
            logger.error("No Appium session available for long press.")
            return
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            element = await loop.run_in_executor(
                None, lambda: driver.find_element(By.ACCESSIBILITY_ID, accessibility_name)
            )
            
            def perform_w3c_long_press() -> None:
                action = ActionChains(driver)
                # Move to element and perform long press
                action.move_to_element(element)
                action.click_and_hold()
                action.pause(duration / 1000.0)  # Convert ms to seconds
                action.release()
                return action.perform()
                
            await loop.run_in_executor(None, perform_w3c_long_press)
            logger.info(f"Performed long press on element with accessibility: {accessibility_name}")
        except Exception as e:
            logger.error(f"Error performing long press on element with accessibility '{accessibility_name}': {e}")
        await self.take_screenshot(f"after_long_press_{accessibility_name}")

    async def perform_tap(self, x: int, y: int) -> None:
        """
        Perform a tap action at the given (x, y) coordinates.
        Captures a screenshot before and after the tap.
        """
        await self.take_screenshot("before_tap")
        if not self.driver:
            logger.error("No Appium session available for performing tap.")
            return
        logger.info(f"Performing tap at coordinates ({x}, {y})")
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            await loop.run_in_executor(None, lambda: driver.tap([(x, y)]))
        except Exception as e:
            logger.error(f"Error performing tap: {e}")
        await self.take_screenshot("after_tap")

    async def perform_swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 800
    ) -> None:
        """
        Perform a swipe gesture from (start_x, start_y) to (end_x, end_y).
        Duration is in milliseconds.
        Captures a screenshot before and after the swipe.
        """
        await self.take_screenshot("before_swipe")
        if not self.driver:
            logger.error("No Appium session available for performing swipe.")
            return
        logger.info(f"Performing swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}) with duration {duration}")
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            await loop.run_in_executor(
                None, lambda: driver.swipe(start_x, start_y, end_x, end_y, duration)
            )
        except Exception as e:
            logger.error(f"Error performing swipe: {e}")
        await self.take_screenshot("after_swipe")

    async def scroll_up(self) -> bool:
        """
        Scroll up by one screen height.
        Returns False if end is hit (determined by comparing before/after screenshots).
        """
        if not self.driver:
            logger.error("No Appium session available for scrolling.")
            return False

        # Get viewport size
        viewport = await self.get_viewport_size()
        if not viewport:
            logger.error("Unable to get viewport size for scrolling.")
            return False

        # Take screenshot before scrolling
        before_screen = await self.see_screen()
        if not before_screen:
            logger.error("Unable to capture screen for scroll comparison.")
            return False

        # Calculate scroll coordinates (scroll from bottom to top)
        start_x = viewport['width'] // 2
        start_y = viewport['height'] * 3 // 4  # Start 3/4 down the screen
        end_y = viewport['height'] // 4        # End 1/4 down the screen

        # Perform the scroll
        await self.perform_swipe(start_x, start_y, start_x, end_y, 500)

        # Take screenshot after scrolling to check if we hit the end
        after_screen = await self.see_screen()
        if not after_screen:
            logger.error("Unable to capture screen after scroll.")
            return False

        # Convert PIL images to bytes for comparison
        before_bytes = before_screen.tobytes()
        after_bytes = after_screen.tobytes()

        # If the before and after screenshots are identical, we've hit the end
        return before_bytes != after_bytes

    async def scroll_down(self) -> bool:
        """
        Scroll down by one screen height.
        Returns False if end is hit (determined by comparing before/after screenshots).
        """
        if not self.driver:
            logger.error("No Appium session available for scrolling.")
            return False

        # Get viewport size
        viewport = await self.get_viewport_size()
        if not viewport:
            logger.error("Unable to get viewport size for scrolling.")
            return False

        # Take screenshot before scrolling
        before_screen = await self.see_screen()
        if not before_screen:
            logger.error("Unable to capture screen for scroll comparison.")
            return False

        # Calculate scroll coordinates (scroll from top to bottom)
        start_x = viewport['width'] // 2
        start_y = viewport['height'] // 4  # Start 1/4 down the screen
        end_y = viewport['height'] * 3 // 4  # End 3/4 down the screen

        # Perform the scroll
        await self.perform_swipe(start_x, start_y, start_x, end_y, 500)

        # Take screenshot after scrolling to check if we hit the end
        after_screen = await self.see_screen()
        if not after_screen:
            logger.error("Unable to capture screen after scroll.")
            return False

        # Convert PIL images to bytes for comparison
        before_bytes = before_screen.tobytes()
        after_bytes = after_screen.tobytes()

        # If the before and after screenshots are identical, we've hit the end
        return before_bytes != after_bytes

    # ─── ACCESSIBILITY TREE SNAPSHOT ─────────────────────────────────────────────

    async def get_accessibility_tree(self) -> Dict[str, Any]:
        """
        Retrieve a simplified accessibility tree (UI hierarchy) of the current screen.

        Parses the XML returned by the Appium page source API and extracts key attributes:
        tag, resource-id, content-desc, text, and class along with children.
        """
        if not self.driver:
            logger.error("No Appium session available to get accessibility tree.")
            return {}
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            source = await loop.run_in_executor(None, lambda: driver.page_source)
            root = ET.fromstring(source)

            def parse_element(elem: ET.Element) -> Dict[str, Any]:
                attrib = elem.attrib
                return {
                    "tag": elem.tag,
                    "resource-id": attrib.get("resource-id"),
                    "content-desc": attrib.get("content-desc"),
                    "text": attrib.get("text"),
                    "class": attrib.get("class"),
                    "children": [parse_element(child) for child in list(elem)]
                }

            tree = parse_element(root)
            logger.info("Accessibility tree retrieved successfully.")
            return tree
        except Exception as e:
            logger.error(f"Error retrieving accessibility tree: {e}")
            return {}

    # ─── SEE SCREEN (RETURN PIL IMAGE) ───────────────────────────────────────────

    async def see_screen(self) -> Optional[Image.Image]:
        """
        Capture a screenshot and return it as a PIL Image object.
        """
        if not self.driver:
            logger.error("No Appium session available to capture screen.")
            return None
        try:
            loop = asyncio.get_running_loop()
            if not isinstance(self.driver, WebDriver):
                logger.error("Driver is not properly initialized")
                return None
            screenshot_base64 = await loop.run_in_executor(None, lambda: self.driver.get_screenshot_as_base64())
            image_data = base64.b64decode(screenshot_base64)
            image = Image.open(io.BytesIO(image_data))
            logger.info("Screen captured and converted to PIL Image.")
            return image
        except Exception as e:
            logger.error(f"Error capturing screen as image: {e}")
            return None

    # ─── GET VIEWPORT SIZE (SCREEN RESOLUTION) ───────────────────────────────────

    async def get_viewport_size(self) -> Optional[Dict[str, int]]:
        """
        Get the current viewport (screen resolution) of the device.
        Returns a dictionary with 'width' and 'height' keys.
        """
        if not self.driver:
            logger.error("No Appium session available to get viewport size.")
            return None
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            size = await loop.run_in_executor(None, driver.get_window_size)
            logger.info(f"Viewport size: {size}")
            return size
        except Exception as e:
            logger.error(f"Error getting viewport size: {e}")
            return None