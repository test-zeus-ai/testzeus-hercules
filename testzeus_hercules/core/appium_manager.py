import asyncio
import subprocess
from subprocess import Popen
import time
import os
import base64
import json
import xml.etree.ElementTree as ET
import io
from typing import Optional, Dict, Any, List, TypeVar, Union, cast

from appium import webdriver
from appium.webdriver.webdriver import WebDriver
from appium.options.common import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
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
        platformVersion: Optional[str] = None,
        udid: Optional[str] = None,
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
        self.platformVersion = platformVersion or conf.get_appium_platform_version()
        self.udid = udid or conf.get_appium_device_uuid()
        
        # Handle app paths based on platform
        if self.platformName.lower() == "android":
            self.app = app or conf.get_appium_apk_path() or conf.get_app_path() or ""
        else:  # iOS
            self.app = app or conf.get_appium_ios_app_path() or conf.get_app_path() or ""
            
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
        self._latest_video_path: Optional[str] = None

        logger.debug(
            f"AppiumManager init (stake_id={self.stake_id}) - platformName={self.platformName}, "
            f"deviceName={self.deviceName}, automationName={self.automationName}, app={self.app}"
        )
        
        self.request_response_log_file = None

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

    async def check_emulator_running(self) -> bool:
        """
        Check if any Android emulator is currently running.
        Returns True if an emulator is running, False otherwise.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                'adb', 'devices',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            output = stdout.decode()
            
            # Check if any emulator is listed in adb devices
            return 'emulator' in output
        except Exception as e:
            logger.error(f"Error checking emulator status: {e}")
            return False

    async def wait_for_emulator_boot(self, timeout: int = 180) -> bool:
        """
        Wait for the emulator to complete booting by checking boot_completed property.
        Returns True if device booted successfully, False if timeout occurred.
        
        Args:
            timeout: Maximum time to wait in seconds (default 3 minutes)
        """
        start_time = time.time()
        check_interval = 2  # Check every 2 seconds
        
        while (time.time() - start_time) < timeout:
            try:
                # Check if device is responding
                process = await asyncio.create_subprocess_exec(
                    'adb', 'shell', 'getprop', 'sys.boot_completed',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                boot_completed = stdout.decode().strip()
                
                if boot_completed == "1":
                    logger.info("Emulator has completed booting")
                    # Additional check for package manager
                    pkg_process = await asyncio.create_subprocess_exec(
                        'adb', 'shell', 'pm', 'path', 'android',
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await pkg_process.communicate()
                    if pkg_process.returncode == 0:
                        logger.info("Package manager is ready")
                        return True
                
                logger.debug(f"Waiting for emulator to boot... ({int(time.time() - start_time)}s)")
                await asyncio.sleep(check_interval)
                
            except Exception as e:
                logger.warning(f"Error checking emulator boot status: {e}")
                await asyncio.sleep(check_interval)
                
        logger.error(f"Emulator boot timeout after {timeout} seconds")
        return False

    async def async_initialize(self) -> None:
        """
        Asynchronously initialize the AppiumManager.

        First checks if an emulator is running:
        - If running, proceeds with normal initialization
        - If not running, starts an emulator and waits for it to boot
        
        Then creates artifact directories, starts the Appium server (if needed),
        and creates a new Appium session.
        """
        # First check if emulator is running
        emulator_running = await self.check_emulator_running()
        
        if not emulator_running:
            logger.info("No emulator running. Starting a new emulator...")
            avd_name = get_global_conf().get_emulator_avd_name() or "Medium_Phone_API_35"
            await self.start_emulator(avd_name)
            
            # Wait for emulator to boot with dynamic checking
            logger.info("Waiting for emulator to boot completely...")
            if not await self.wait_for_emulator_boot():
                raise Exception("Emulator failed to boot within the timeout period")
            
        await self.setup_artifacts()
        if self.start_server:
            await self.start_appium_server()
        await self.create_session()

    # ─── APPIUM SERVER & EMULATOR MANAGEMENT ─────────────────────────────

    async def wait_for_appium_server(self, timeout: int = 60) -> bool:
        import aiohttp
        start_time = time.time()
        check_interval = 1  # Check every second
        server_url = self.appium_server_url or f"http://127.0.0.1:{self.server_port}"
        status_url = f"{server_url}/status"

        async with aiohttp.ClientSession() as session:
            while (time.time() - start_time) < timeout:
                try:
                    async with session.get(status_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            logger.info(f"Appium server status: {data}")
                            if data['value']['ready'] == True:
                                logger.info("Appium server is responsive")
                                return True
                except Exception as e:
                    logger.debug(f"Waiting for Appium server... ({int(time.time() - start_time)}s)")
                
                await asyncio.sleep(check_interval)

        logger.error(f"Appium server not responding after {timeout} seconds")
        return False

    def _setup_request_response_logging(self) -> None:
        """
        Set up request/response logging for the Appium server.
        Creates a new log file for the current session.
        """
        if not self._logs_dir:
            return

        timestamp = int(time.time())
        self._request_log_path = os.path.join(
            self._logs_dir, f"appium_requests_{timestamp}.log"
        )
        logger.info(f"Request/response logging enabled at {self._request_log_path}")

    def _log_request_response(self, request_data: Dict[str, Any], response_data: Dict[str, Any]) -> None:
        """
        Log request and response data to the request log file.
        
        Args:
            request_data: Dictionary containing request information
            response_data: Dictionary containing response information
        """
        if not hasattr(self, '_request_log_path'):
            return

        try:
            log_entry = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'request': request_data,
                'response': response_data
            }
            with open(self._request_log_path, 'a', encoding='utf-8') as f:
                json.dump(log_entry, f)
                f.write('\n')
        except Exception as e:
            logger.error(f"Error logging request/response: {e}")

    async def start_appium_server(self) -> None:
        """
        Spawn a new Appium server instance using subprocess.
        Also starts tasks to capture server stdout/stderr logs and request/response logging.
        Includes dynamic wait for server readiness.
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
            
            # Set up request/response logging
            self._setup_request_response_logging()
            
            # Start log capture tasks
            if self._logs_dir and self.appium_process and self.appium_process.stdout and self.appium_process.stderr:
                self._log_tasks.append(
                    asyncio.create_task(self._capture_stream(self.appium_process.stdout, "appium_stdout.log"))
                )
                self._log_tasks.append(
                    asyncio.create_task(self._capture_stream(self.appium_process.stderr, "appium_stderr.log"))
                )
            
            # Wait for server to be fully responsive
            logger.info("Waiting for Appium server to be ready...")
            if not await self.wait_for_appium_server():
                raise Exception("Appium server failed to respond within the timeout period")
                
            logger.info("Appium server started and responding successfully.")
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
            avd_name = get_global_conf().get_emulator_avd_name() or "Medium_Phone_API_35"
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
        await asyncio.sleep(120)
        await self.async_initialize()

    async def wait_for_session_ready(self, timeout: int = 30) -> bool:
        """
        Wait for the Appium session to be fully ready by checking device responsiveness.
        Returns True if session is ready, False if timeout occurred.
        
        Args:
            timeout: Maximum time to wait in seconds (default 30 seconds)
        """
        if not self.driver:
            return False

        start_time = time.time()
        check_interval = 1  # Check every second
        
        while (time.time() - start_time) < timeout:
            try:
                # Try to get device screen size as a basic check
                loop = asyncio.get_running_loop()
                driver = cast(WebDriver, self.driver)
                await loop.run_in_executor(None, driver.get_window_size)
                logger.info("Appium session is responsive")
                return True
            except Exception as e:
                logger.debug(f"Waiting for session to be ready... ({int(time.time() - start_time)}s)")
                await asyncio.sleep(check_interval)

        logger.error(f"Session not responding after {timeout} seconds")
        return False

    async def create_session(self) -> None:
        """
        Create an Appium session by initializing the WebDriver.
        Includes request/response logging and session stability checks.
        """
        desired_caps = {
            "platformName": self.platformName,
            "deviceName": self.deviceName,
            "automationName": self.automationName,
        }
        
        # Add app path if specified
        if self.app:
            desired_caps["app"] = self.app
            
        # Add platform version if specified
        if self.platformVersion:
            desired_caps["platformVersion"] = self.platformVersion
            
        # Add device UUID if specified
        if self.udid:
            desired_caps["udid"] = self.udid

        # Merge any extra capabilities.
        desired_caps.update(self.extra_capabilities)

        server_url = self.appium_server_url or f"http://127.0.0.1:{self.server_port}"
        logger.info(f"Creating Appium session with server: {server_url} and capabilities: {desired_caps}")
        try:
            loop = asyncio.get_running_loop()
            
            # Log the session creation request
            self._log_request_response(
                {"command": "create_session", "capabilities": desired_caps},
                {"status": "pending"}
            )
            
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
            
            # Wait for session to be fully ready
            logger.info("Waiting for session to stabilize...")
            if not await self.wait_for_session_ready():
                raise Exception("Session failed to stabilize within the timeout period")
            
            # Log successful session creation
            self._log_request_response(
                {"command": "create_session", "capabilities": desired_caps},
                {"status": "success", "session_id": self.driver.session_id}
            )
            logger.info("Appium session created and stabilized successfully.")
            
        except Exception as e:
            # Log failed session creation
            self._log_request_response(
                {"command": "create_session", "capabilities": desired_caps},
                {"status": "error", "error": str(e)}
            )
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
            self._latest_video_path = video_path
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
    # ─── ELEMENT FINDING STRATEGIES ─────────────────────────────────────────────

    def _parse_bounds(self, bounds_str: str) -> Optional[Dict[str, int]]:
        """
        Parse bounds string in format '[x1,y1][x2,y2]' into coordinates.
        
        Args:
            bounds_str: String representation of element bounds
            
        Returns:
            Dictionary with x1,y1,x2,y2 coordinates or None if invalid format
        """
        try:
            # Remove brackets and split into coordinates
            coords = bounds_str.replace('[', '').replace(']', '').split(',')
            if len(coords) != 4:
                return None
                
            # Parse into integers
            x1, y1, x2, y2 = map(int, coords)
            return {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2}
        except Exception as e:
            logger.debug(f"Error parsing bounds {bounds_str}: {e}")
            return None

    async def find_element_by_bounds(
        self,
        bounds: str,
        tag_name: Optional[str] = None
    ) -> Optional[WebElement]:
        """
        Find element by its bounding box coordinates.
        
        Args:
            bounds: Element bounds in format '[x1,y1][x2,y2]'
            tag_name: Optional tag/class name to filter by
            
        Returns:
            WebElement if found, None otherwise
        """
        if not self.driver:
            return None
            
        try:
            coords = self._parse_bounds(bounds)
            if not coords:
                return None
                
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)

            # Construct XPath with bounds and optional tag
            # Construct XPath with bounds and optional tag
            tag_condition = ""
            if tag_name:
                tag_condition = f"@class='{tag_name}' and "
            xpath = f"//*[{tag_condition}@bounds='{bounds}' and @enabled='true' and @displayed='true']"
            
            return await loop.run_in_executor(
                None,
                lambda: driver.find_element(AppiumBy.XPATH, xpath)
            )
        except Exception as e:
            logger.debug(f"Element not found by bounds {bounds}: {e}")
            return None

    async def find_element_best_match(
        self,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None,
        bounds: Optional[str] = None,
        tag_name: Optional[str] = None
    ) -> WebElement:
        """
        Try finding element using resource ID first, then accessibility ID, then bounds.
        
        Args:
            res_id: Resource ID of the element (Android: resource-id, iOS: name)
            accessibility_id: Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)
            bounds: Element bounds in format '[x1,y1][x2,y2]'
            tag_name: Optional tag/class name to filter by when using bounds

        Returns:
            WebElement: Found element

        Raises:
            Exception: If element cannot be found using any strategy
        """
        if not self.driver:
            logger.error("No Appium session available for finding element.")
            raise RuntimeError("No active Appium session")

        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)

            # Try resource ID first if provided
            if res_id:
                logger.debug(f"Trying to find element by resource ID: {res_id}")
                try:
                    return await loop.run_in_executor(
                        None, lambda: driver.find_element(AppiumBy.ID, res_id)
                    )
                except Exception as e:
                    logger.debug(f"Element not found by resource ID: {e}")

            # Then try accessibility ID if provided
            if accessibility_id:
                logger.debug(f"Trying to find element by accessibility ID: {accessibility_id}")
                try:
                    return await loop.run_in_executor(
                        None, lambda: driver.find_element(AppiumBy.ACCESSIBILITY_ID, accessibility_id)
                    )
                except Exception as e:
                    logger.debug(f"Element not found by accessibility ID: {e}")

            # Finally try bounds if provided
            if bounds:
                logger.debug(f"Trying to find element by bounds: {bounds}")
                element = await self.find_element_by_bounds(bounds, tag_name)
                if element:
                    return element

            # If no strategy succeeded, raise an error
            error_msg = (
                "Could not find element. Tried:\n" +
                (f"- Resource ID: {res_id}\n" if res_id else "") +
                (f"- Accessibility ID: {accessibility_id}\n" if accessibility_id else "") +
                (f"- Bounds: {bounds}\n" if bounds else "")
            )
            raise Exception(error_msg)

        except Exception as e:
            logger.error(str(e))
            raise e

    # ─── BASIC INTERACTION METHODS WITH PRE/POST SCREENSHOTS ───────────────────

    async def click_by_id(self, res_id: Optional[str] = None, accessibility_id: Optional[str] = None) -> None:
        """
        Click on an element identified by resource ID and/or accessibility ID.
        Will try resource ID first if provided, then accessibility ID if provided.
        Captures a screenshot before and after the click.

        Args:
            res_id: Resource ID of the element (Android: resource-id, iOS: name)
            accessibility_id: Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)

        Raises:
            RuntimeError: If neither res_id nor accessibility_id is provided
            RuntimeError: If no active Appium session
            Exception: If element cannot be found or clicked
        """
        if not res_id and not accessibility_id:
            raise RuntimeError("At least one of res_id or accessibility_id must be provided")

        screenshot_name = f"click_{res_id or ''}_{accessibility_id or ''}"
        await self.take_screenshot(f"before_{screenshot_name}")
        
        if not self.driver:
            logger.error("No Appium session available for click action.")
            raise RuntimeError("No active Appium session")

        try:
            element = await self.find_element_best_match(res_id, accessibility_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, element.click)
            logger.info(f"Clicked on element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}")
        except Exception as e:
            logger.error(f"Error clicking element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}. Error: {e}")
            raise e
        await self.take_screenshot(f"after_{screenshot_name}")

    async def enter_text_by_id(
        self,
        text: str,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None
    ) -> None:
        """
        Enter text into an element identified by resource ID and/or accessibility ID.
        Will try resource ID first if provided, then accessibility ID if provided.
        Captures a screenshot before and after entering text.

        Args:
            text: The text to enter into the element
            res_id: Resource ID of the element (Android: resource-id, iOS: name)
            accessibility_id: Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)

        Raises:
            RuntimeError: If neither res_id nor accessibility_id is provided
            RuntimeError: If no active Appium session
            Exception: If element cannot be found or text cannot be entered
        """
        if not res_id and not accessibility_id:
            raise RuntimeError("At least one of res_id or accessibility_id must be provided")

        screenshot_name = f"enter_text_{res_id or ''}_{accessibility_id or ''}"
        await self.take_screenshot(f"before_{screenshot_name}")
        
        if not self.driver:
            logger.error("No Appium session available for entering text.")
            raise RuntimeError("No active Appium session")

        try:
            element = await self.find_element_best_match(res_id, accessibility_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: element.send_keys(text))
            logger.info(f"Entered text into element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}")
        except Exception as e:
            logger.error(f"Error entering text in element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}. Error: {e}")
            raise e
        await self.take_screenshot(f"after_{screenshot_name}")

    async def clear_text_by_id(
        self,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None
    ) -> None:
        """
        Clear text from an element identified by resource ID and/or accessibility ID.
        Will try resource ID first if provided, then accessibility ID if provided.
        Captures a screenshot before and after clearing the text.

        Args:
            res_id: Resource ID of the element (Android: resource-id, iOS: name)
            accessibility_id: Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)

        Raises:
            RuntimeError: If neither res_id nor accessibility_id is provided
            RuntimeError: If no active Appium session
            Exception: If element cannot be found or cleared
        """
        if not res_id and not accessibility_id:
            raise RuntimeError("At least one of res_id or accessibility_id must be provided")

        screenshot_name = f"clear_text_{res_id or ''}_{accessibility_id or ''}"
        await self.take_screenshot(f"before_{screenshot_name}")
        
        if not self.driver:
            logger.error("No Appium session available for clearing text.")
            raise RuntimeError("No active Appium session")

        try:
            element = await self.find_element_best_match(res_id, accessibility_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, element.clear)
            logger.info(f"Cleared text from element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}")
        except Exception as e:
            logger.error(f"Error clearing text from element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}. Error: {e}")
            raise e
        await self.take_screenshot(f"after_{screenshot_name}")

    async def long_press_by_id(
        self,
        duration: int = 1000,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None
    ) -> None:
        """
        Perform a long press on an element identified by resource ID and/or accessibility ID.
        Will try resource ID first if provided, then accessibility ID if provided.
        Duration is in milliseconds.
        Captures a screenshot before and after the long press.

        Args:
            duration: Duration of long press in milliseconds (default: 1000ms)
            res_id: Resource ID of the element (Android: resource-id, iOS: name)
            accessibility_id: Accessibility ID of the element (Android: content-desc, iOS: accessibilityIdentifier)

        Raises:
            RuntimeError: If neither res_id nor accessibility_id is provided
            RuntimeError: If no active Appium session
            Exception: If element cannot be found or long press cannot be performed
        """
        if not res_id and not accessibility_id:
            raise RuntimeError("At least one of res_id or accessibility_id must be provided")

        screenshot_name = f"long_press_{res_id or ''}_{accessibility_id or ''}"
        await self.take_screenshot(f"before_{screenshot_name}")
        
        if not self.driver:
            logger.error("No Appium session available for long press.")
            raise RuntimeError("No active Appium session")

        try:
            element = await self.find_element_best_match(res_id, accessibility_id)
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            def perform_w3c_long_press() -> None:
                action = ActionChains(driver)
                # Move to element and perform long press
                action.move_to_element(element)
                action.click_and_hold()
                action.pause(duration / 1000.0)  # Convert ms to seconds
                action.release()
                return action.perform()
                
            await loop.run_in_executor(None, perform_w3c_long_press)
            logger.info(f"Performed long press on element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}")
        except Exception as e:
            logger.error(f"Error performing long press on element. Resource ID: {res_id}, Accessibility ID: {accessibility_id}. Error: {e}")
            raise e
        await self.take_screenshot(f"after_{screenshot_name}")

    async def perform_tap(self, x: int, y: int) -> None:
        """
        Perform a tap action at the given (x, y) coordinates.
        Captures a screenshot before and after the tap.
        """
        await self.take_screenshot("before_tap")
        if not self.driver:
            logger.error("No Appium session available for performing tap.")
            raise e
        logger.info(f"Performing tap at coordinates ({x}, {y})")
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            await loop.run_in_executor(None, lambda: driver.tap([(x, y)]))
        except Exception as e:
            logger.error(f"Error performing tap: {e}")
            raise e
        await self.take_screenshot("after_tap")

    async def perform_swipe(
        self, end_x: int, end_y: int, start_x: int, start_y: int, duration: int = 800
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
            raise e
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
        Retrieve a detailed accessibility tree (UI hierarchy) of the current screen.
        Handles both Android and iOS element attributes.
        """
        if not self.driver:
            logger.error("No Appium session available to get accessibility tree.")
            return {}
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            source = await loop.run_in_executor(None, lambda: driver.page_source)
            root = ET.fromstring(source)

            def parse_element_all(elem: ET.Element) -> Dict[str, Any]:
                attrib = elem.attrib
                element_data = {
                    "children": [parse_element_all(child) for child in list(elem)]
                }
                for key, value in attrib.items():
                    element_data[key] = value
                if len(element_data["children"]) == 0:
                    del element_data["children"]
                if len(element_data) > 1:
                    element_data["tag"] = elem.tag
                return element_data
            
            tree = parse_element_all(root)
            logger.info("Accessibility tree retrieved successfully.")
            return tree
        except Exception as e:
            logger.error(f"Error retrieving accessibility tree: {e}")
            raise e

    # ─── SEE SCREEN (RETURN PIL IMAGE) ───────────────────────────────────────────

    async def see_screen(self) -> Optional[Image.Image]:
        """
        Capture a screenshot and return it as a PIL Image object.
        """
        if not self.driver:
            logger.error("No Appium session available to capture screen.")
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
            raise e

    # ─── GET VIEWPORT SIZE (SCREEN RESOLUTION) ───────────────────────────────────

    async def get_viewport_size(self) -> Optional[Dict[str, int]]:
        """
        Get the current viewport (screen resolution) of the device.
        Returns a dictionary with 'width' and 'height' keys.
        """
        if not self.driver:
            logger.error("No Appium session available to get viewport size.")
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            size = await loop.run_in_executor(None, driver.get_window_size)
            logger.info(f"Viewport size: {size}")
            return size
        except Exception as e:
            logger.error(f"Error getting viewport size: {e}")
            raise e

    # ─── COMMAND AND STATE TRACKING ───────────────────────────────────────────

    async def command_completed(self, command: str, elapsed_time: Optional[float] = None) -> None:
        """
        Log when a command is completed.
        """
        time_info = f" (took {elapsed_time:.2f}s)" if elapsed_time is not None else ""
        logger.debug(f'Command "{command}" completed{time_info}')

    async def update_processing_state(self, processing_state: str) -> None:
        """
        Update the current processing state. This is a no-op for mobile automation
        but implemented for API compatibility with PlaywrightManager.
        """
        logger.debug(f"Processing state updated to: {processing_state}")

    async def get_current_screen_state(self) -> str:
        """
        For mobile automation, instead of a URL we return a serialized version
        of the current screen's accessibility tree as JSON.
        """
        return "Use tool to check"

    async def get_viewport_size(self) -> Optional[Dict[str, int]]:
        """
        Get the current viewport (screen resolution) of the device.
        Returns a dictionary with 'width' and 'height' keys.
        """
        if not self.driver:
            logger.error("No Appium session available to get viewport size.")

        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            size = await loop.run_in_executor(None, driver.get_window_size)
            logger.info(f"Viewport size: {size}")
            return size
        except Exception as e:
            logger.error(f"Error getting viewport size: {e}")
            raise e

    def get_latest_video_path(self) -> Optional[str]:
        """
        Get the path of the latest recorded video.
        Returns None if no video is available.
        """
        if self._latest_video_path and os.path.exists(self._latest_video_path):
            return self._latest_video_path
        else:
            logger.warning("No video recording available.")
            return None
        
    async def press_key(self, key_name: str) -> None:
        """
        Press a keyboard key by name for both iOS and Android platforms.
        
        Args:
            key_name: Name of the key to press (e.g., 'enter', 'tab', 'space')
        """
        await self.take_screenshot(f"before_press_key_{key_name}")
        if not self.driver:
            logger.error("No Appium session available for key press.")
            raise RuntimeError("No active Appium session")
            
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            # Map common key names to their corresponding codes for both platforms
            android_key_mapping = {
                'enter': 66,      # KEYCODE_ENTER
                'tab': 61,        # KEYCODE_TAB
                'space': 62,      # KEYCODE_SPACE
                'backspace': 67,  # KEYCODE_DEL
                'delete': 112,    # KEYCODE_FORWARD_DEL
                'escape': 111,    # KEYCODE_ESCAPE
                'up': 19,         # KEYCODE_DPAD_UP
                'down': 20,       # KEYCODE_DPAD_DOWN
                'left': 21,       # KEYCODE_DPAD_LEFT
                'right': 22,      # KEYCODE_DPAD_RIGHT
            }
            
            ios_key_mapping = {
                'enter': '\ue007',
                'tab': '\ue004',
                'space': ' ',
                'backspace': '\ue003',
                'delete': '\ue017',
                'escape': '\ue00c',
                'up': '\ue013',
                'down': '\ue015',
                'left': '\ue012',
                'right': '\ue014',
            }
            
            key_name_lower = key_name.lower()
            
            if self.platformName.lower() == "android":
                # Handle Android key press using key codes
                key_code = android_key_mapping.get(key_name_lower)
                if key_code is not None:
                    await loop.run_in_executor(None, lambda: driver.press_keycode(key_code))
                else:
                    # For character keys not in the mapping
                    await loop.run_in_executor(None, lambda: driver.execute_script(
                        'mobile: shell',
                        {
                            'command': 'input',
                            'args': ['text', key_name]
                        }
                    ))
            else:  # iOS
                # Handle iOS key press using Unicode characters
                key_code = ios_key_mapping.get(key_name_lower, key_name)
                action = ActionChains(driver)
                await loop.run_in_executor(None, lambda: action.send_keys(key_code).perform())
            
            logger.info(f"Pressed key: {key_name}")
            
        except Exception as e:
            logger.error(f"Error pressing key '{key_name}': {e}")
            raise e
            
        await self.take_screenshot(f"after_press_key_{key_name}")

    async def press_hardware_key(self, key_name: str) -> None:
        """
        Press a hardware key by name.
        
        Args:
            key_name: Name of hardware key (e.g., 'volume_up', 'volume_down', 'power')
        """
        await self.take_screenshot(f"before_press_hardware_{key_name}")
        if not self.driver:
            logger.error("No Appium session available for hardware key press.")
            raise RuntimeError("No active Appium session")
            
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            # Map hardware key names to their corresponding key codes
            android_key_mapping = {
                'volume_up': 24,
                'volume_down': 25,
                'power': 26,
                'camera': 27,
                'call': 5,
                'end_call': 6,
                'menu': 82,
                'back': 4,
                'home': 3,
                'app_switch': 187,
            }
            
            if self.platformName.lower() == "android":
                key_code = android_key_mapping.get(key_name.lower())
                if key_code is None:
                    raise ValueError(f"Unknown hardware key: {key_name}")
                    
                await loop.run_in_executor(None, lambda: driver.press_keycode(key_code))
                
            else:  # iOS
                # On iOS, some hardware actions are handled differently
                if key_name.lower() in ['volume_up', 'volume_down']:
                    await loop.run_in_executor(
                        None, 
                        lambda: driver.execute_script('mobile: pressButton', {'name': key_name.lower()})
                    )
                elif key_name.lower() == 'home':
                    await loop.run_in_executor(
                        None,
                        lambda: driver.execute_script('mobile: pressButton', {'name': 'home'})
                    )
                else:
                    raise ValueError(f"Unsupported hardware key for iOS: {key_name}")
                    
            logger.info(f"Pressed hardware key: {key_name}")
            
        except Exception as e:
            logger.error(f"Error pressing hardware key '{key_name}': {e}")
            raise e
            
        await self.take_screenshot(f"after_press_hardware_{key_name}")

    async def press_back(self) -> None:
        """
        Press the back button (Android) or perform back gesture (iOS).
        """
        await self.take_screenshot("before_press_back")
        if not self.driver:
            logger.error("No Appium session available for back action.")
            raise RuntimeError("No active Appium session")
            
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            if self.platformName.lower() == "android":
                await loop.run_in_executor(None, lambda: driver.press_keycode(4))  # 4 is Android's back button
            else:  # iOS
                # For iOS, we need to use gestures or navigation commands
                await loop.run_in_executor(
                    None,
                    lambda: driver.execute_script('mobile: swipe', {'direction': 'right'})
                )
                
            logger.info("Pressed back button")
            
        except Exception as e:
            logger.error(f"Error pressing back button: {e}")
            raise e
            
        await self.take_screenshot("after_press_back")

    async def press_home(self) -> None:
        """
        Press the home button/perform home action.
        """
        await self.take_screenshot("before_press_home")
        if not self.driver:
            logger.error("No Appium session available for home action.")
            raise RuntimeError("No active Appium session")
            
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            if self.platformName.lower() == "android":
                await loop.run_in_executor(None, lambda: driver.press_keycode(3))  # 3 is Android's home button
            else:  # iOS
                await loop.run_in_executor(
                    None,
                    lambda: driver.execute_script('mobile: pressButton', {'name': 'home'})
                )
                
            logger.info("Pressed home button")
            
        except Exception as e:
            logger.error(f"Error pressing home button: {e}")
            raise e
            
        await self.take_screenshot("after_press_home")

    async def press_app_switch(self) -> None:
        """
        Press the app switcher/recent apps button.
        """
        await self.take_screenshot("before_press_app_switch")
        if not self.driver:
            logger.error("No Appium session available for app switch action.")
            raise RuntimeError("No active Appium session")
            
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            if self.platformName.lower() == "android":
                await loop.run_in_executor(None, lambda: driver.press_keycode(187))  # 187 is Android's recent apps button
            else:  # iOS
                # For iOS 13+, use the app switcher gesture
                await loop.run_in_executor(
                    None,
                    lambda: driver.execute_script('mobile: swipe', {'direction': 'up', 'duration': 1.0})
                )
                
            logger.info("Pressed app switch button")
            
        except Exception as e:
            logger.error(f"Error pressing app switch button: {e}")
            raise e
            
        await self.take_screenshot("after_press_app_switch")

    async def press_key_combination(self, key_combination: str) -> None:
        """
        Press a combination of keys simultaneously (e.g., "Control+A" for select all).
        
        Args:
            key_combination: Key combination string (e.g., "Control+A", "Shift+Tab")
        """
        await self.take_screenshot(f"before_press_key_combo_{key_combination}")
        if not self.driver:
            logger.error("No Appium session available for key combination.")
            raise RuntimeError("No active Appium session")
            
        try:
            loop = asyncio.get_running_loop()
            driver = cast(WebDriver, self.driver)
            
            # Split the combination into individual keys
            keys = key_combination.split("+")
            
            # Map modifier keys to their codes
            android_modifier_keys = {
                'control': 113,  # KEYCODE_CTRL_LEFT
                'ctrl': 113,     # KEYCODE_CTRL_LEFT
                'shift': 59,     # KEYCODE_SHIFT_LEFT
                'alt': 57,       # KEYCODE_ALT_LEFT
                'meta': 117,     # KEYCODE_META_LEFT
                'command': 117,  # KEYCODE_META_LEFT
            }
            
            if self.platformName.lower() == "android":
                # For Android, we need to handle modifier keys specially
                modifiers = []
                main_key = None
                
                for key in keys:
                    key_lower = key.lower()
                    if key_lower in android_modifier_keys:
                        modifiers.append(android_modifier_keys[key_lower])
                    else:
                        # The last non-modifier key is the main key
                        main_key = key
                
                # Press all modifier keys
                for modifier in modifiers:
                    await loop.run_in_executor(None, lambda m=modifier: driver.press_keycode(m))
                
                # Press the main key if there is one
                if main_key:
                    await self.press_key(main_key)
                
                # Release modifier keys in reverse order
                for modifier in reversed(modifiers):
                    await loop.run_in_executor(None, lambda m=modifier: driver.keyUp(m))
                    
            else:  # iOS
                # For iOS, we can use the ActionChains
                action = ActionChains(driver)
                
                # Convert keys to iOS format
                ios_keys = []
                for key in keys:
                    key_lower = key.lower()
                    if key_lower in ['control', 'ctrl']:
                        ios_keys.append('\ue009')  # Control
                    elif key_lower == 'shift':
                        ios_keys.append('\ue008')  # Shift
                    elif key_lower == 'alt':
                        ios_keys.append('\ue00A')  # Alt
                    elif key_lower in ['command', 'meta']:
                        ios_keys.append('\ue03D')  # Command
                    else:
                        ios_keys.append(key)
                
                # Press all keys in sequence
                for key in ios_keys:
                    action.key_down(key)
                for key in reversed(ios_keys):
                    action.key_up(key)
                    
                await loop.run_in_executor(None, action.perform)
            
            logger.info(f"Pressed key combination: {key_combination}")
            
        except Exception as e:
            logger.error(f"Error pressing key combination '{key_combination}': {e}")
            raise e
            
        await self.take_screenshot(f"after_press_key_combo_{key_combination}")