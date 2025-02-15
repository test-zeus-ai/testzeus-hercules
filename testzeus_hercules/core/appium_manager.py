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
from testzeus_hercules.core import ios_gestures

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
        """
        Stop all Appium-related resources.
        This includes:
        - Active Appium session
        - Appium server
        - Device (Android emulator or iOS simulator)
        """
        await self.quit_session()
        await self.stop_appium_server()
        await self.stop_device()

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

    async def check_device_status(self) -> bool:
        """
        Check if any Android emulator or iOS simulator is currently running.
        Returns True if a device is running, False otherwise.
        """
        if self.platformName.lower() == "android":
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
                logger.error(f"Error checking Android emulator status: {e}")
                return False
        else:  # iOS
            try:
                process = await asyncio.create_subprocess_exec(
                    'xcrun', 'simctl', 'list', 'devices',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                output = stdout.decode()
                
                # Check if any simulator is in "Booted" state
                return 'Booted' in output
            except Exception as e:
                logger.error(f"Error checking iOS simulator status: {e}")
                return False

    async def wait_for_device_boot(self, timeout: int = 180) -> bool:
        """
        Wait for the device (Android emulator or iOS simulator) to complete booting.
        Returns True if device booted successfully, False if timeout occurred.
        
        Args:
            timeout: Maximum time to wait in seconds (default 3 minutes)
        """
        start_time = time.time()
        check_interval = 2  # Check every 2 seconds
        
        if self.platformName.lower() == "android":
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
                        logger.info("Android emulator has completed booting")
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
                    
                    logger.debug(f"Waiting for Android emulator to boot... ({int(time.time() - start_time)}s)")
                    await asyncio.sleep(check_interval)
                    
                except Exception as e:
                    logger.warning(f"Error checking Android emulator boot status: {e}")
                    await asyncio.sleep(check_interval)
        else:  # iOS
            while (time.time() - start_time) < timeout:
                try:
                    # Check simulator status using simctl
                    process = await asyncio.create_subprocess_exec(
                        'xcrun', 'simctl', 'list', 'devices',
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, _ = await process.communicate()
                    output = stdout.decode()
                    
                    if 'Booted' in output:
                        # Check if SpringBoard is running
                        springboard_check = await asyncio.create_subprocess_exec(
                            'xcrun', 'simctl', 'spawn', 'booted', 'pgrep', 'SpringBoard',
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        sproc_stdout, _ = await springboard_check.communicate()
                        
                        if sproc_stdout:  # If we get a PID back, SpringBoard is running
                            # One final check for system readiness
                            runtime_check = await asyncio.create_subprocess_exec(
                                'xcrun', 'simctl', 'spawn', 'booted', 'launchctl', 'print', 'system',
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            await runtime_check.communicate()
                            
                            if runtime_check.returncode == 0:
                                logger.info("iOS simulator is fully booted and ready")
                                return True
                    
                    # Still waiting
                    wait_time = int(time.time() - start_time)
                    if wait_time % 5 == 0:  # Log every 5 seconds
                        logger.debug(f"Waiting for iOS simulator to boot... ({wait_time}s)")
                    await asyncio.sleep(check_interval)
                    
                except Exception as e:
                    logger.warning(f"Error checking iOS simulator boot status: {e}")
                    await asyncio.sleep(check_interval)
                    
        logger.error(f"Device boot timeout after {timeout} seconds")
        return False

    async def async_initialize(self) -> None:
        """
        Asynchronously initialize the AppiumManager with enhanced error handling.
        
        Initialization steps:
        1. Verify environment (especially for iOS)
        2. Check/start appropriate device
        3. Set up artifacts directory
        4. Start/connect to Appium server
        5. Create test session
        
        Raises:
            Exception: If any initialization step fails
        """
        try:
            # For iOS, verify environment first
            if self.platformName.lower() == "ios":
                logger.info("Verifying iOS environment...")
                await self.verify_ios_environment()
            
            # Check if device is running
            device_running = await self.check_device_status()
            logger.info(f"Device status check: {'Running' if device_running else 'Not running'}")
            
            if not device_running:
                if self.platformName.lower() == "android":
                    logger.info("Starting new Android emulator...")
                    avd_name = get_global_conf().get_emulator_avd_name() or "Medium_Phone_API_35"
                    await self.start_device(avd_name=avd_name)
                    
                    logger.info("Waiting for Android device to boot...")
                    if not await self.wait_for_device_boot():
                        raise Exception("Android device failed to boot within timeout")
                else:  # iOS
                    logger.info("Starting new iOS simulator...")
                    device_name = get_global_conf().get_ios_simulator_device() or "iPhone 14"
                    await self.start_device(device_name=device_name)
            else:
                logger.info(f"Using existing {self.platformName} device")
            
            # Set up artifacts directory
            logger.info("Setting up artifacts directory...")
            await self.setup_artifacts()
            
            # Start or connect to Appium server
            if self.start_server:
                logger.info("Starting Appium server...")
                await self.start_appium_server()
            
            # Create test session
            logger.info("Creating Appium session...")
            await self.create_session()
            
            logger.info("Initialization completed successfully")
            
        except Exception as e:
            error_msg = f"Initialization failed: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg) from e

    # ─── APPIUM SERVER & EMULATOR MANAGEMENT ─────────────────────────────

    async def verify_ios_environment(self) -> None:
        """Verify iOS development environment is properly set up."""
        if self.platformName.lower() != "ios":
            return
            
        try:
            # Check Xcode path
            process = await asyncio.create_subprocess_exec(
                'xcode-select', '-p',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            if not stdout:
                raise Exception("Xcode path not set")
                
            # Check available runtimes first
            runtimes_cmd = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'list', 'runtimes',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await runtimes_cmd.communicate()
            runtime_output = stdout.decode()

            if 'iOS' not in runtime_output:
                # Try to download the runtime
                logger.info("No iOS runtime found. Attempting to install...")
                download_cmd = await asyncio.create_subprocess_exec(
                    'xcodebuild', '-downloadPlatform', 'iOS',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await download_cmd.communicate()

            # Check iOS SDK again after potential runtime installation
            process = await asyncio.create_subprocess_exec(
                'xcrun', 'xcodebuild', '-showsdks',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if 'iphonesimulator' not in stdout.decode():
                # Run first-time Xcode setup
                logger.info("Running Xcode first-time setup...")
                setup_cmd = await asyncio.create_subprocess_exec(
                    'sudo', 'xcodebuild', '-runFirstLaunch',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await setup_cmd.communicate()
                
                # Try SDK check one more time
                process = await asyncio.create_subprocess_exec(
                    'xcrun', 'xcodebuild', '-showsdks',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                if 'iphonesimulator' not in stdout.decode():
                    raise Exception("iOS SDK not found. Please install Xcode and run 'xcodebuild -runFirstLaunch'")
                
            # Check WebDriverAgent
            wda_path = '~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj'
            if not os.path.exists(os.path.expanduser(wda_path)):
                raise Exception("WebDriverAgent not found")
                
            logger.info("iOS environment verified successfully")
            
        except Exception as e:
            setup_guide = """
    iOS Environment Setup Instructions:

    1. Xcode Setup:
    - Install Xcode from the App Store
    - Launch Xcode and complete first-time setup
    - Install additional components when prompted

    2. Command Line Tools:
    $ xcode-select --install
    $ sudo xcodebuild -license accept
    $ sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

    3. iOS Simulator Setup:
    $ sudo xcodebuild -runFirstLaunch
    $ xcodebuild -downloadPlatform iOS

    4. WebDriverAgent Setup:
    $ cd /usr/local/lib/node_modules/appium/node_modules/appium-xcuitest-driver/WebDriverAgent
    $ xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination 'platform=iOS Simulator,name=iPhone 14' build-for-testing

    5. Verify Setup:
    $ xcrun simctl list runtimes    # Should show iOS runtimes
    $ xcrun xcodebuild -showsdks    # Should show iOS simulator SDKs
    $ xcrun simctl list devices     # Should show available simulators

    For more details, see: https://appium.io/docs/en/2.0/quickstart/install/
    """
            error_details = f"""
    Environment Check Results:
    - Error: {str(e)}
    - Xcode Path: {await self._get_xcode_path()}
    - Available SDKs: {await self._get_available_sdks()}
    - Simulator Runtimes: {await self._get_simulator_runtimes()}
    """
            logger.error("iOS environment verification failed!")
            logger.error(error_details)
            logger.error(setup_guide)
            raise Exception(f"iOS environment setup incomplete: {error_details}")

    async def _get_xcode_path(self) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                'xcode-select', '-p',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return stdout.decode().strip()
        except Exception:
            return "Not found"

    async def _get_available_sdks(self) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                'xcrun', 'xcodebuild', '-showsdks',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return stdout.decode().strip()
        except Exception:
            return "No SDKs found"

    async def _get_simulator_runtimes(self) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'list', 'runtimes',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return stdout.decode().strip()
        except Exception:
            return "No runtimes found"

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

    async def _cleanup_appium_processes(self) -> None:
        """Clean up any existing Appium processes and port usage."""
        try:
            # First check if the port is in use
            port = str(self.server_port)
            process = await asyncio.create_subprocess_exec(
                'lsof', '-i', f':{port}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            output = stdout.decode()
            
            if output:
                logger.info(f"Port {port} is in use. Cleaning up...")
                # Kill processes using our port
                for line in output.split('\n')[1:]:  # Skip header
                    if line:
                        pid = line.split()[1]
                        try:
                            kill_cmd = await asyncio.create_subprocess_exec(
                                'kill', '-9', pid,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            await kill_cmd.communicate()
                            logger.info(f"Killed process {pid} using port {port}")
                        except Exception as e:
                            logger.warning(f"Error killing process {pid}: {e}")

            # Then check for any Appium processes
            process = await asyncio.create_subprocess_exec(
                'pgrep', '-f', 'appium',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            pids = stdout.decode().strip().split('\n')
            
            # Kill each found Appium process
            for pid in pids:
                if pid:
                    try:
                        logger.info(f"Killing existing Appium process (PID: {pid})")
                        kill_cmd = await asyncio.create_subprocess_exec(
                            'kill', '-9', pid,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        await kill_cmd.communicate()
                    except Exception as e:
                        logger.warning(f"Error killing Appium process {pid}: {e}")
            
            # Brief pause to ensure processes are terminated
            await asyncio.sleep(2)
            
            # Verify port is now free
            process = await asyncio.create_subprocess_exec(
                'lsof', '-i', f':{port}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            if stdout.decode().strip():
                raise Exception(f"Port {port} is still in use after cleanup")
            
        except Exception as e:
            logger.warning(f"Error during Appium process cleanup: {e}")

    async def start_appium_server(self) -> None:
        """
        Start an Appium server with automatic cleanup of existing processes.
        
        This method will:
        1. Clean up any existing Appium processes
        2. Verify the port is available
        3. Start a new Appium server
        4. Set up logging
        5. Wait for server to be ready
        
        Raises:
            Exception: If server fails to start or isn't responsive
        """
        # Clean up existing processes
        await self._cleanup_appium_processes()

        if self.appium_process is not None:
            logger.info("New Appium server instance already started.")
            return

        logger.info(f"Starting Appium server on port {self.server_port}")
        command = [
            "appium",
            "--address", "0.0.0.0",
            "-p", str(self.server_port),
            "--allow-insecure", "chromedriver_autodownload",
            "--relaxed-security",
            "--session-override"
        ]
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

    async def stop_device(self) -> None:
        """
        Stop the device (Android emulator or iOS simulator) gracefully.
        First attempts to terminate the process, then kills it if termination times out.
        For iOS, also shuts down any running simulators.
        """
        if self.platformName.lower() == "android":
            if self.emulator_process:
                logger.info("Stopping Android emulator process.")
                try:
                    process = cast(Popen[bytes], self.emulator_process)
                    # Create a Future for the process.wait()
                    wait_future = asyncio.create_task(process.wait())
                    process.terminate()
                    try:
                        await asyncio.wait_for(wait_future, timeout=10.0)
                        logger.info("Android emulator process terminated gracefully.")
                    except asyncio.TimeoutError:
                        logger.warning("Android emulator process did not stop in time; killing process.")
                        process.kill()
                except ProcessLookupError:
                    logger.info("Android emulator process has already exited.")
                except Exception as e:
                    logger.error(f"Error while stopping Android emulator process: {e}")
                finally:
                    self.emulator_process = None
        else:  # iOS
            try:
                # Shutdown all running simulators
                shutdown_cmd = await asyncio.create_subprocess_exec(
                    'xcrun', 'simctl', 'shutdown', 'all',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await shutdown_cmd.communicate()
                logger.info("All iOS simulators shutdown initiated.")
                
                # Kill Simulator.app if it's running
                kill_cmd = await asyncio.create_subprocess_exec(
                    'pkill', '-9', 'Simulator',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await kill_cmd.communicate()
                logger.info("Simulator.app terminated.")
                
                # Clear any remaining process
                if self.emulator_process:
                    try:
                        process = cast(Popen[bytes], self.emulator_process)
                        process.terminate()
                        await asyncio.wait_for(asyncio.create_task(process.wait()), timeout=5.0)
                    except Exception:
                        pass
                    self.emulator_process = None
                    
            except Exception as e:
                logger.error(f"Error while stopping iOS simulator: {e}")

    async def start_device(self, avd_name: Optional[str] = None, device_name: Optional[str] = None) -> None:
        """
        Start an Android emulator or iOS simulator based on platform configuration.
        
        Args:
            avd_name: Optional AVD name for Android emulator
            device_name: Optional device name for iOS simulator
        """
        if self.platformName.lower() == "android":
            if not avd_name:
                avd_name = get_global_conf().get_emulator_avd_name() or "Medium_Phone_API_35"
            command = ["emulator", "-avd", avd_name]
            logger.info(f"Starting Android emulator with AVD: {avd_name}")
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.emulator_process = cast(Popen[bytes], process)
                logger.info("Android emulator process started.")
            except Exception as e:
                logger.error(f"Error starting Android emulator: {e}")
                raise e
        else:  # iOS
            if not device_name:
                device_name = get_global_conf().get_ios_simulator_device() or "iPhone 14"
            try:
                # Check for existing running simulator
                process = await asyncio.create_subprocess_exec(
                    'xcrun', 'simctl', 'list', 'devices',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                output = stdout.decode()
                
                # Find if our device exists
                simulator_id = None
                import re
                
                pattern = f"{device_name} \\(([^)]+)\\)(?: \\(Booted\\))?"
                matches = re.finditer(pattern, output)
                for match in matches:
                    simulator_id = match.group(1)
                    if '(Booted)' in match.group(0):
                        logger.info(f"Found already booted simulator {device_name} (ID: {simulator_id})")
                        self.emulator_process = None  # No process to track since already running
                        return

                # Create simulator if it doesn't exist
                if not simulator_id:
                    create_cmd = await asyncio.create_subprocess_exec(
                        'xcrun', 'simctl', 'create', device_name, device_name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await create_cmd.communicate()
                    simulator_id = stdout.decode().strip()
                    logger.info(f"Created new simulator {device_name} (ID: {simulator_id})")

                # Boot the simulator if not already running
                boot_cmd = await asyncio.create_subprocess_exec(
                    'xcrun', 'simctl', 'boot', simulator_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                self.emulator_process = cast(Popen[bytes], boot_cmd)
                await boot_cmd.communicate()
                
                # Open Simulator.app to show the UI
                open_cmd = await asyncio.create_subprocess_exec(
                    'open', '-a', 'Simulator',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await open_cmd.communicate()
                logger.info(f"iOS simulator {device_name} (ID: {simulator_id}) booting up")
                await asyncio.sleep(2)  # Brief pause to let simulator initialize
                
            except Exception as e:
                logger.error(f"Error handling iOS simulator: {e}")
                raise e
            stdout, stderr = await create_cmd.communicate()
            # Create new simulator and get its ID
            create_cmd = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'create', device_name, device_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await create_cmd.communicate()
            simulator_id = stdout.decode().strip()
            logger.info(f"Created new simulator {device_name} (ID: {simulator_id})")
            
            # Boot the simulator if it's not already running
            check_state_cmd = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'list', 'devices',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await check_state_cmd.communicate()
            if f"(Booted)" not in stdout.decode():
                # Simulator needs to be booted
                boot_cmd = await asyncio.create_subprocess_exec(
                    'xcrun', 'simctl', 'boot', simulator_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                self.emulator_process = cast(Popen[bytes], boot_cmd)
            
            # Open Simulator.app to show the UI
            open_cmd = await asyncio.create_subprocess_exec(
                'open', '-a', 'Simulator',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await open_cmd.communicate()
            
            logger.info(f"iOS simulator {device_name} (ID: {simulator_id}) starting up")

    async def create_device_and_connect(
        self,
        avd_name: Optional[str] = None,
        device_name: Optional[str] = None
    ) -> None:
        """
        Start the appropriate device (Android emulator or iOS simulator) and initialize the Appium session.
        
        Args:
            avd_name: Optional name for Android Virtual Device
            device_name: Optional name for iOS simulator device
        """
        if self.platformName.lower() == "android":
            await self.start_device(avd_name=avd_name)
        else:  # iOS
            await self.start_device(device_name=device_name)
            
        # Optionally wait additional time for the device to boot completely.
        # This is on top of the normal boot checks for extra stability
        boot_wait = 120 if self.platformName.lower() == "android" else 60
        await asyncio.sleep(boot_wait)
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
        
        For iOS, ensures the development environment is properly configured
        with Xcode and necessary SDKs before attempting to create a session.
        
        Includes:
        - Environment verification
        - Capability configuration
        - Request/response logging
        - Session stability checks
        
        Raises:
            Exception: If environment setup is incomplete or session creation fails
        """
        # Verify environment first for iOS
        if self.platformName.lower() == "ios":
            await self.verify_ios_environment()
            
        # Set base capabilities based on platform
        if self.platformName.lower() == "android":
            desired_caps = {
                "platformName": self.platformName,
                "deviceName": self.deviceName,
                "automationName": self.automationName or "UiAutomator2",
                "autoGrantPermissions": True,  # Automatically grant app permissions
                "noReset": False,  # Always do a clean install
                "newCommandTimeout": 300,  # Longer timeout for debugging
            }
        else:  # iOS
            # First check iOS SDK setup
            try:
                # Check Xcode select path
                xcode_path_cmd = await asyncio.create_subprocess_exec(
                    'xcode-select', '-p',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await xcode_path_cmd.communicate()
                if xcode_path_cmd.returncode != 0:
                    raise Exception("Xcode command line tools not properly set up")
                
                # Get available SDK versions
                sdk_cmd = await asyncio.create_subprocess_exec(
                    'xcrun', 'xcodebuild', '-showsdks',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await sdk_cmd.communicate()
                sdk_output = stdout.decode()
                
                # Find latest iOS simulator SDK version
                import re
                sdk_versions = re.findall(r'iphonesimulator(\d+\.\d+)', sdk_output)
                if not sdk_versions:
                    raise Exception("No iOS simulator SDK found")
                    
                ios_version = max(sdk_versions, key=float)
                logger.info(f"Found iOS SDK version: {ios_version}")
                
                # Get simulator device info
                sim_cmd = await asyncio.create_subprocess_exec(
                    'xcrun', 'simctl', 'list', 'devices',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await sim_cmd.communicate()
                sim_output = stdout.decode()
                
                # Get device model
                device_model = self.deviceName or "iPhone 14"
                if 'Booted' in sim_output:
                    device_pattern = f"({device_model}|iPhone.*?)\\s+\\([^)]+\\)\\s+\\(Booted\\)"
                    device_match = re.search(device_pattern, sim_output)
                    if device_match:
                        device_model = device_match.group(1)
                
                logger.info(f"Using iOS {ios_version} with device model: {device_model}")
                
                # Set up iOS capabilities with detected device info
                desired_caps = {
                    # Base capabilities
                    "platformName": "iOS",
                    "platformVersion": ios_version,
                    "deviceName": device_model,
                    "automationName": "XCUITest",
                    
                    # WebDriverAgent settings
                    "useNewWDA": True,
                    "wdaLaunchTimeout": 120000,
                    "wdaConnectionTimeout": 120000,
                    "shouldUseSingletonTestManager": False,
                    
                    # Logging and debugging
                    "showXcodeLog": True,
                    "includeSafariInWebviews": True,
                    
                    # Performance settings
                    "maxTypingFrequency": 10,
                    "nativeInstrumentsLib": True,
                    
                    # Reset behavior
                    "fullReset": False,
                    "noReset": True,
                    
                    # Additional iOS settings
                    "startIWDP": True,
                    "webviewConnectTimeout": 90000,
                    "preventWDAAttachments": True,
                    "clearSystemFiles": True,
                }
            except Exception as e:
                error_msg = str(e)
                setup_instructions = """
iOS Environment Setup Required:
1. Install Xcode from the App Store
2. Install Xcode Command Line Tools: xcode-select --install
3. Accept Xcode license: sudo xcodebuild -license accept
4. Select Xcode version: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
5. Install iOS Simulator: xcodebuild -downloadPlatform iOS
                """
                
                if "Xcode command line tools not properly set up" in error_msg:
                    logger.error(f"Xcode setup incomplete. {setup_instructions}")
                elif "No iOS simulator SDK found" in error_msg:
                    logger.error(f"iOS Simulator SDK missing. {setup_instructions}")
                else:
                    logger.error(f"iOS environment error: {error_msg}")
                    logger.error(setup_instructions)
                
                raise Exception(f"iOS environment not properly configured: {error_msg}")
            
            # Get iOS-specific configuration
            conf = get_global_conf()
            if conf.get_ios_bundle_id():
                desired_caps["bundleId"] = conf.get_ios_bundle_id()
            if conf.get_xcode_org_id():
                desired_caps["xcodeOrgId"] = conf.get_xcode_org_id()
            if conf.get_xcode_signing_id():
                desired_caps["xcodeSigningId"] = conf.get_xcode_signing_id()
            if conf.get_webdriveragent_path():
                desired_caps["webDriverAgentUrl"] = conf.get_webdriveragent_path()
            
            logger.debug(f"Setting up iOS capabilities: {desired_caps}")
        
        # Add common capabilities
        if self.app:
            desired_caps["app"] = self.app
        if self.platformVersion:
            desired_caps["platformVersion"] = self.platformVersion
        if self.udid:
            desired_caps["udid"] = self.udid

        # Override with any user-specified capabilities
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
            # Add capabilities with proper type preservation
            for k, v in desired_caps.items():
                if isinstance(v, bool):
                    options.set_capability(k, v)  # Keep booleans as booleans
                elif isinstance(v, (int, float)):
                    options.set_capability(k, v)  # Keep numbers as numbers
                else:
                    options.set_capability(k, str(v))  # Convert strings to strings
            
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
    # ─── IOS SPECIFIC GESTURES ─────────────────────────────────────────────────

    async def perform_ios_pinch(
        self,
        scale: float = 0.5,
        velocity: float = 1.0,
        element_id: Optional[str] = None
    ) -> None:
        """
        Perform a pinch gesture (zoom in/out) on iOS.
        Scale > 1 zooms in, scale < 1 zooms out.
        """
        await self.take_screenshot("before_ios_pinch")
        if not self.driver:
            logger.error("No Appium session available for iOS pinch.")
            raise RuntimeError("No active Appium session")

        try:
            driver = cast(WebDriver, self.driver)
            await ios_gestures.perform_pinch(driver, scale, velocity, element_id)
        except Exception as e:
            logger.error(f"Error performing iOS pinch: {e}")
            raise e

        await self.take_screenshot("after_ios_pinch")

    async def perform_ios_force_touch(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        element_id: Optional[str] = None,
        pressure: float = 0.8,
        duration: float = 0.5
    ) -> None:
        """
        Perform a force touch (3D Touch) gesture on iOS.
        Requires x,y coordinates or an element_id.
        """
        await self.take_screenshot("before_force_touch")
        if not self.driver:
            logger.error("No Appium session available for force touch.")
            raise RuntimeError("No active Appium session")

        try:
            driver = cast(WebDriver, self.driver)
            await ios_gestures.perform_force_touch(
                driver, x, y, element_id, pressure, duration
            )
        except Exception as e:
            logger.error(f"Error performing force touch: {e}")
            raise e

        await self.take_screenshot("after_force_touch")

    async def perform_ios_double_tap(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        element_id: Optional[str] = None,
        duration: float = 0.1
    ) -> None:
        """
        Perform an iOS-optimized double tap gesture.
        Requires x,y coordinates or an element_id.
        """
        await self.take_screenshot("before_double_tap")
        if not self.driver:
            logger.error("No Appium session available for double tap.")
            raise RuntimeError("No active Appium session")

        try:
            driver = cast(WebDriver, self.driver)
            await ios_gestures.perform_double_tap(
                driver, x, y, element_id, duration
            )
        except Exception as e:
            logger.error(f"Error performing double tap: {e}")
            raise e

        await self.take_screenshot("after_double_tap")

    async def perform_ios_haptic(self, type: str = "selection") -> None:
        """
        Trigger iOS haptic feedback.
        Type can be 'selection', 'light', 'medium', or 'heavy'.
        """
        if not self.driver:
            logger.error("No Appium session available for haptic feedback.")
            raise RuntimeError("No active Appium session")

        try:
            driver = cast(WebDriver, self.driver)
            await ios_gestures.perform_haptic(driver, type)
        except Exception as e:
            logger.error(f"Error performing haptic feedback: {e}")
            raise e

    async def handle_ios_alert(
        self,
        action: str,
        button_label: Optional[str] = None
    ) -> Optional[Any]:
        """
        Handle iOS system alerts.
        
        Args:
            action: One of 'accept', 'dismiss', 'getButtons', 'click'
            button_label: Required when action is 'click'
        
        Returns:
            Alert buttons list when action is 'getButtons',
            None for other actions
        """
        if not self.driver:
            logger.error("No Appium session available for alert handling.")
            raise RuntimeError("No active Appium session")

        try:
            driver = cast(WebDriver, self.driver)
            return await ios_gestures.perform_alert_action(
                driver, action, button_label
            )
        except Exception as e:
            logger.error(f"Error handling iOS alert: {e}")
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