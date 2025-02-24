import subprocess
from subprocess import Popen, CompletedProcess, PIPE
import time
import os
import re
import json
import base64
import json
import xml.etree.ElementTree as ET
import io
from typing import Callable, Optional, Dict, Any, List, Tuple, TypeVar, Union, cast
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import threading
import shutil

from appium import webdriver
from appium.webdriver.webdriver import WebDriver
from appium.options.common import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webelement import WebElement
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

from PIL import Image


def add_bounds_data(node: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and standardize bounding box coordinates from element data."""
    try:
        # For Android nodes
        if "bounds" in node:
            # Remove the original bounds field
            bounds_str = node.pop("bounds")
            matches = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
            if len(matches) == 2:
                start_x, start_y = map(int, matches[0])
                end_x, end_y = map(int, matches[1])
                node["bounds_data"] = {
                    "start_x": start_x,
                    "start_y": start_y,
                    "end_x": end_x,
                    "end_y": end_y,
                }
        # For iOS nodes
        elif all(key in node for key in ["x", "y", "width", "height"]):
            x = int(node.pop("x"))
            y = int(node.pop("y"))
            width = int(node.pop("width"))
            height = int(node.pop("height"))
            node["bounds_data"] = {
                "start_x": x,
                "start_y": y,
                "end_x": x + width,
                "end_y": y + height,
            }
    except Exception as e:
        logger.warning(f"Error processing bounds for node: {e}")
    return node


def find_best_matching_node(
    tree: Dict[str, Any],
    res_id: Optional[str] = None,
    accessibility_id: Optional[str] = None,
    bounds_data: Optional[Dict[str, int]] = None,
    tag_name: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Find the best matching node in the accessibility tree based on given criteria.
    Returns tuple of (best_matching_node, match_score) where score is between 0 and 1.
    """

    def calculate_node_score(node: Dict[str, Any]) -> float:
        score = 0.0
        total_criteria = 0

        # Check resource ID match
        if res_id:
            total_criteria += 1
            if any(node.get(k) == res_id for k in ["resource-id", "name"]):
                score += 1.0

        # Check accessibility ID match
        if accessibility_id:
            total_criteria += 1
            if any(node.get(k) == accessibility_id for k in ["content-desc", "label"]):
                score += 1.0

        # Check bounds match
        if bounds_data and "bounds_data" in node:
            total_criteria += 1
            node_bounds = node["bounds_data"]
            # Allow for some pixel tolerance (e.g., ±5 pixels)
            tolerance = 5
            if all(
                abs(node_bounds.get(k, 0) - bounds_data.get(k, 0)) <= tolerance
                for k in ["start_x", "start_y", "end_x", "end_y"]
            ):
                score += 1.0

        # Check tag name match
        if tag_name:
            total_criteria += 1
            if node.get("tag") == tag_name:
                score += 1.0

        return score / max(total_criteria, 1)

    def traverse_tree(node: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], float]:
        best_score = calculate_node_score(node)
        best_node = node if best_score > 0 else None

        # Traverse children
        if "children" in node:
            for child in node["children"]:
                child_node, child_score = traverse_tree(child)
                if child_score > best_score:
                    best_score = child_score
                    best_node = child_node

        return best_node, best_score

    return traverse_tree(tree)


REFERENCE_DICT = {
    "ios": {
        "type": "t",
        "name": "n",
        "label": "l",
        "enabled": "en",
        "visible": "vis",
        "accessible": "acc",
        "index": "i",
        "tag": "tg",
        "bounds_data": {
            "bounds_data": "bb",  # key for the bounds_data field itself
            "start_x": "sx",
            "start_y": "sy",
            "end_x": "ex",
            "end_y": "ey",
        },
        "children": "c",
    },
    "android": {
        "index": "i",
        "package": "pkg",
        "class": "cls",
        "text": "txt",
        "resource-id": "rid",
        "checkable": "chk",
        "checked": "chkd",
        "clickable": "clck",
        "enabled": "en",
        "focusable": "fcs",
        "focused": "fcd",
        "long-clickable": "lck",
        "password": "pwd",
        "scrollable": "scrl",
        "selected": "sel",
        "displayed": "dsp",
        "a11y-important": "a11y",
        "screen-reader-focusable": "srf",
        "drawing-order": "dor",
        "showing-hint": "shh",
        "text-entry-key": "tek",
        "dismissable": "dsm",
        "a11y-focused": "a11yf",
        "heading": "hdg",
        "live-region": "lr",
        "context-clickable": "cc",
        "content-invalid": "cinv",
        "tag": "tg",
        "pane-title": "pt",
        "content-desc": "cd",
        "bounds_data": {
            "bounds_data": "bb",  # key for the bounds_data field itself
            "start_x": "sx",
            "start_y": "sy",
            "end_x": "ex",
            "end_y": "ey",
        },
        "children": "c",
    },
}


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

    _ui_thread_pool: Optional[ThreadPoolExecutor] = None
    _instances: Dict[str, "AppiumManager"] = {}
    _default_instance: Optional["AppiumManager"] = None
    _initialized: bool = False
    _accessibility_tree_cache: Optional[Dict[str, Any]] = (
        None  # Cache for storing accessibility tree
    )

    def __new__(
        cls, *args: Any, stake_id: Optional[str] = None, **kwargs: Any
    ) -> "AppiumManager":
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
            logger.debug(
                f"Created new AppiumManager instance for stake_id '{stake_id}'"
            )
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
        try:
            if stake_id:
                if stake_id in cls._instances:
                    instance = cls._instances[stake_id]
                    instance.quit_session()
                    del cls._instances[stake_id]
                    logger.info(
                        f"Closed AppiumManager instance for stake_id: {stake_id}"
                    )
            else:
                for instance in cls._instances.values():
                    instance.quit_session()
                cls._instances.clear()
                logger.info("Closed all AppiumManager instances")
        except Exception as e:
            logger.error(f"Error closing AppiumManager instance: {str(e)}")

    @classmethod
    def close_all_instances(cls) -> None:
        """Close all AppiumManager instances."""
        try:
            for instance in cls._instances.values():
                instance.quit_session()
            cls._instances.clear()
            logger.info("Closed all AppiumManager instances")
        except Exception as e:
            logger.error(f"Error closing all AppiumManager instances: {str(e)}")

    @classmethod
    def _get_thread_pool(cls) -> ThreadPoolExecutor:
        """Get or create the optimized thread pool for UI operations."""
        if cls._ui_thread_pool is None:
            # Calculate optimal number of workers based on CPU cores
            # Use max(4, CPU_COUNT) to ensure we have enough threads for UI operations
            worker_count = max(30, multiprocessing.cpu_count())
            cls._ui_thread_pool = ThreadPoolExecutor(
                max_workers=worker_count, thread_name_prefix="AppiumUI"
            )
        return cls._ui_thread_pool

    def _run_in_ui_thread(self, func: Callable[[], Any], identifier: str) -> Any:
        """Execute a function in the UI thread and measure its execution time."""
        start_time = time.time()
        logger.info(f"[APPIUM_DRIVER_TIMING] Starting driver interaction: {identifier}")

        try:
            result = func()
            end_time = time.time()
            logger.info(
                f"[APPIUM_DRIVER_TIMING] Completed driver interaction: {identifier} in {end_time - start_time:.2f} seconds"
            )
            return result
        except Exception as e:
            end_time = time.time()
            logger.error(
                f"[APPIUM_DRIVER_TIMING] Failed driver interaction: {identifier} after {end_time - start_time:.2f} seconds: {str(e)}"
            )
            raise

    def stop_appium(self) -> None:
        """
        Stop all Appium-related resources.
        This includes:
        - Active Appium session
        - Appium server
        - Device (Android emulator or iOS simulator)
        """
        logger.info(f"Stopping Appium resources for stake_id '{self.stake_id}'")
        self.quit_session()
        self.stop_appium_server()
        self.stop_device()
        self.cleanup_thread_pool()

    def cleanup_thread_pool(self) -> None:
        """Clean up the UI thread pool."""
        if self._ui_thread_pool is not None:
            self._ui_thread_pool.shutdown(wait=True)
            self._ui_thread_pool = None

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
        should_take_screenshots: bool = False,
        should_record_video: bool = False,
        should_capture_network: bool = False,
    ):
        """Initialize AppiumManager with configuration."""
        self.stake_id = stake_id or "0"
        self.appium_server_url = appium_server_url
        self.start_server = start_server
        self.appium_port = server_port
        self.platformName = platformName or "Android"
        self.deviceName = deviceName
        self.automationName = automationName
        self.app = app
        self.platformVersion = platformVersion
        self.udid = udid
        self.extra_capabilities = extra_capabilities or {}

        # Logging and artifacts
        self.artifacts_dir = os.path.join("artifacts", self.stake_id)
        self.screenshots_dir = os.path.join(self.artifacts_dir, "screenshots")
        self.videos_dir = os.path.join(self.artifacts_dir, "videos")
        self._network_log_path = os.path.join(self.artifacts_dir, "network.log")

        # State tracking
        self.driver: Optional[WebDriver] = None
        self.appium_process: Optional[Popen[bytes]] = None
        self.emulator_process: Optional[Popen[bytes]] = None
        self._accessibility_tree_cache: Optional[Dict[str, Any]] = None

        # Feature flags
        self.should_take_screenshots = should_take_screenshots
        self.should_record_video = should_record_video
        self.should_capture_network = should_capture_network

        # Initialize if not already done
        if not self._initialized:
            self.initialize()

    def setup_artifacts(self) -> None:
        """Set up artifacts directory and files."""
        try:
            # Create artifacts directory if it doesn't exist
            os.makedirs(self.artifacts_dir, exist_ok=True)

            # Set up log files
            self.device_log_file = os.path.join(self.artifacts_dir, "device.log")
            self.appium_log_file = os.path.join(self.artifacts_dir, "appium.log")
            self.bugreport_file = os.path.join(self.artifacts_dir, "bugreport.txt")

            # Create empty log files
            for log_file in [
                self.device_log_file,
                self.appium_log_file,
                self.bugreport_file,
            ]:
                with open(log_file, "w") as f:
                    f.write("")

            logger.info("Set up artifacts directory and files")
        except Exception as e:
            logger.error(f"Error setting up artifacts: {str(e)}")
            raise

    def verify_ios_environment(self) -> None:
        """Verify iOS environment setup."""
        try:
            # Check for required iOS tools
            required_tools = ["xcodebuild", "xcrun"]
            for tool in required_tools:
                if not shutil.which(tool):
                    raise EnvironmentError(f"Required iOS tool not found: {tool}")

            # Check for iOS simulator
            simulator_list = subprocess.run(
                ["xcrun", "simctl", "list"], capture_output=True, text=True
            )
            if simulator_list.returncode != 0:
                raise EnvironmentError("Failed to list iOS simulators")

            logger.info("iOS environment verified")
        except Exception as e:
            logger.error(f"Error verifying iOS environment: {str(e)}")
            raise

    def check_device_status(self) -> bool:
        """
        Check if any Android emulator or iOS simulator is currently running.
        Returns True if a device is running, False otherwise.
        """
        if self.platformName.lower() == "android":
            try:
                process = subprocess.run(
                    ["adb", "devices"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return "emulator" in process.stdout
            except Exception as e:
                logger.error(f"Error checking Android emulator status: {e}")
                return False
        else:  # iOS
            try:
                process = subprocess.run(
                    ["xcrun", "simctl", "list", "devices"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return "Booted" in process.stdout
            except Exception as e:
                logger.error(f"Error checking iOS simulator status: {e}")
                return False

    def wait_for_device_boot(self, timeout: int = 180) -> bool:
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
                    process = subprocess.run(
                        ["adb", "shell", "getprop", "sys.boot_completed"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    boot_completed = process.stdout.strip()

                    if boot_completed == "1":
                        logger.info("Android emulator has completed booting")
                        # Additional check for package manager
                        pkg_process = subprocess.run(
                            ["adb", "shell", "pm", "path", "android"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        if pkg_process.returncode == 0:
                            logger.info("Package manager is ready")
                            return True

                    logger.debug(
                        f"Waiting for Android emulator to boot... ({int(time.time() - start_time)}s)"
                    )
                    time.sleep(check_interval)

                except Exception as e:
                    logger.warning(f"Error checking Android emulator boot status: {e}")
                    time.sleep(check_interval)
        else:  # iOS
            while (time.time() - start_time) < timeout:
                try:
                    # Check simulator status using simctl
                    process = subprocess.run(
                        ["xcrun", "simctl", "list", "devices"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    if "Booted" in process.stdout:
                        logger.info("iOS simulator has completed booting")
                        return True

                    logger.debug(
                        f"Waiting for iOS simulator to boot... ({int(time.time() - start_time)}s)"
                    )
                    time.sleep(check_interval)

                except Exception as e:
                    logger.warning(f"Error checking iOS simulator boot status: {e}")
                    time.sleep(check_interval)

            logger.error(f"Device boot timeout after {timeout} seconds")
            return False

    def initialize(self) -> None:
        """
        Initialize the AppiumManager with enhanced error handling.

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
                self.verify_ios_environment()

            # Check if device is running
            device_running = self.check_device_status()
            logger.info(
                f"Device status check: {'Running' if device_running else 'Not running'}"
            )

            if not device_running:
                if self.platformName.lower() == "android":
                    logger.info("Starting new Android emulator...")
                    avd_name = (
                        get_global_conf().get_emulator_avd_name()
                        or "Medium_Phone_API_35"
                    )
                    self.start_device(avd_name=avd_name)

                    logger.info("Waiting for Android device to boot...")
                    if not self.wait_for_device_boot():
                        raise Exception("Android device failed to boot within timeout")
                else:  # iOS
                    logger.info("Starting new iOS simulator...")
                    device_name = (
                        get_global_conf().get_ios_simulator_device() or "iPhone 14"
                    )
                    self.start_device(device_name=device_name)
            else:
                logger.info(f"Using existing {self.platformName} device")

            # Set up artifacts directory
            logger.info("Setting up artifacts directory...")
            self.setup_artifacts()

            # Start or connect to Appium server
            if self.start_server:
                logger.info("Starting Appium server...")
                self.start_appium_server()

            # Create test session
            logger.info("Creating Appium session...")
            self.create_session()

            logger.info("Initialization completed successfully")

        except Exception as e:
            error_msg = f"Initialization failed: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg) from e

    # ─── APPIUM SERVER & EMULATOR MANAGEMENT ─────────────────────────────

    def _write_log_entry(self, log_entry: Dict[str, Any], log_file: str) -> None:
        """Write a log entry to a log file synchronously."""
        try:
            line = json.dumps(log_entry, ensure_ascii=False) + "\n"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"Error writing log entry: {e}")

    def _cleanup_appium_processes(self) -> None:
        """Clean up any existing Appium processes and port usage."""
        try:
            # First check if the port is in use
            port = str(self.server_port)
            process = subprocess.run(
                ["lsof", "-i", f":{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output = process.stdout

            if output:
                # Port is in use, get PIDs
                pids = []
                for line in output.splitlines()[1:]:  # Skip header
                    pid = line.split()[1]
                    pids.append(pid)

                # Kill processes
                for pid in pids:
                    try:
                        subprocess.run(["kill", "-9", pid])
                        logger.info(f"Killed process {pid} using port {port}")
                    except Exception as e:
                        logger.warning(f"Failed to kill process {pid}: {e}")
        except Exception as e:
            logger.error(f"Error cleaning up Appium processes: {e}")

    def start_device(
        self, avd_name: Optional[str] = None, device_name: Optional[str] = None
    ) -> None:
        """
        Start an Android emulator or iOS simulator based on platform configuration.

        Args:
            avd_name: Optional AVD name for Android emulator
            device_name: Optional device name for iOS simulator
        """
        if self.platformName.lower() == "android":
            if not avd_name:
                avd_name = (
                    get_global_conf().get_emulator_avd_name() or "Medium_Phone_API_35"
                )
            command = ["emulator", "-avd", avd_name]
            logger.info(f"Starting Android emulator with AVD: {avd_name}")
            try:
                process = subprocess.Popen(
                    command,
                    stdout=PIPE,
                    stderr=PIPE,
                )
                self.emulator_process = process
                logger.info("Android emulator process started.")
            except Exception as e:
                logger.error(f"Error starting Android emulator: {e}")
                raise e
        else:  # iOS
            if not device_name:
                device_name = (
                    get_global_conf().get_ios_simulator_device() or "iPhone 14"
                )
            logger.info(f"Starting iOS simulator with device: {device_name}")

            try:
                # Get list of available simulators
                process = subprocess.run(
                    ["xcrun", "simctl", "list", "devices"],
                    stdout=PIPE,
                    stderr=PIPE,
                    text=True,
                )
                devices_output = process.stdout

                # Find simulator ID if it exists
                pattern = f"{device_name}.*?\\((.*?)\\)"
                match = re.search(pattern, devices_output)
                simulator_id = match.group(1) if match else None

                if not simulator_id:
                    # Create new simulator
                    create_process = subprocess.run(
                        ["xcrun", "simctl", "create", device_name, device_name],
                        stdout=PIPE,
                        stderr=PIPE,
                        text=True,
                    )
                    simulator_id = create_process.stdout.strip()
                    logger.info(
                        f"Created new simulator {device_name} (ID: {simulator_id})"
                    )

                # Boot the simulator
                boot_process = subprocess.Popen(
                    ["xcrun", "simctl", "boot", simulator_id],
                    stdout=PIPE,
                    stderr=PIPE,
                )
                self.emulator_process = boot_process

                # Open Simulator.app
                subprocess.run(
                    ["open", "-a", "Simulator"],
                    stdout=PIPE,
                    stderr=PIPE,
                )

                logger.info(
                    f"iOS simulator {device_name} (ID: {simulator_id}) starting up"
                )
                time.sleep(2)  # Brief pause to let simulator initialize

            except Exception as e:
                logger.error(f"Error handling iOS simulator: {e}")
                raise e

    def create_device_and_connect(self) -> None:
        """
        Start the appropriate device and initialize the Appium session.
        """
        try:
            # Start device based on platform
            self.start_device()

            # Create and initialize Appium session
            self.create_session()

            # Wait for session to be ready
            self.wait_for_session_ready()

            logger.info("Device started and Appium session initialized successfully")

        except Exception as e:
            logger.error(f"Error in create_device_and_connect: {e}")
            raise e

    def start_appium_server(self) -> None:
        """Start the Appium server."""
        try:
            # Check if Appium is installed
            if not shutil.which("appium"):
                raise EnvironmentError("Appium not found in PATH")

            # Start Appium server
            cmd = [
                "appium",
                "-p",
                str(self.appium_port),
                "--log",
                self.appium_log_file,
                "--log-timestamp",
                "--local-timezone",
            ]

            self.appium_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            # Wait for server to start
            time.sleep(5)  # Give the server some time to start

            # Check if server started successfully
            if self.appium_process.poll() is not None:
                raise RuntimeError("Appium server failed to start")

            logger.info(f"Started Appium server on port {self.appium_port}")
        except Exception as e:
            logger.error(f"Error starting Appium server: {str(e)}")
            raise

    def stop_device(self) -> None:
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
                    process.terminate()
                    try:
                        process.wait(timeout=10.0)
                        logger.info("Android emulator process terminated gracefully.")
                    except subprocess.TimeoutExpired:
                        logger.warning(
                            "Android emulator process did not stop in time; killing process."
                        )
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
                subprocess.run(
                    ["xcrun", "simctl", "shutdown", "all"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                )
                logger.info("All iOS simulators shutdown initiated.")

                # Kill Simulator.app if it's running
                subprocess.run(
                    ["pkill", "-9", "Simulator"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,  # Don't raise error if process not found
                )
                logger.info("Simulator.app terminated.")

                # Clear any remaining process
                if self.emulator_process:
                    try:
                        process = cast(Popen[bytes], self.emulator_process)
                        process.terminate()
                        process.wait(timeout=5.0)
                    except Exception:
                        pass
                    self.emulator_process = None

            except Exception as e:
                logger.error(f"Error while stopping iOS simulator: {e}")

    def stop_appium_server(self) -> None:
        """
        Stop the spawned Appium server and cancel any log capture tasks.
        """
        if self.appium_process:
            logger.info("Stopping Appium server.")
            self.appium_process.terminate()
            self.appium_process.kill()
            self.appium_process = None

    def create_session(self) -> None:
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
            self.verify_ios_environment()

        # Set base capabilities based on platform
        if self.platformName.lower() == "android":
            desired_caps = {
                "platformName": self.platformName,
                "deviceName": self.deviceName,
                "automationName": self.automationName or "UiAutomator2",
                "autoGrantPermissions": True,  # Automatically grant app permissions
                "noReset": False,  # Always do a clean install
                "newCommandTimeout": 300,  # Longer timeout for debugging
                "settings[waitForIdleTimeout]": 0,  # Android-specific setting for idle timeout
            }
        else:  # iOS
            # First check iOS SDK setup
            try:
                # Check Xcode select path
                process = subprocess.run(
                    ["xcode-select", "-p"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if process.returncode != 0:
                    raise Exception("Xcode command line tools not properly set up")

                # Get available SDK versions
                process = subprocess.run(
                    ["xcrun", "xcodebuild", "-showsdks"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                sdk_output = process.stdout

                # Find latest iOS simulator SDK version
                import re

                sdk_versions = re.findall(r"iphonesimulator(\d+\.\d+)", sdk_output)
                if not sdk_versions:
                    raise Exception("No iOS simulator SDK found")

                ios_version = max(sdk_versions, key=float)
                logger.info(f"Found iOS SDK version: {ios_version}")

                # Get simulator device info
                process = subprocess.run(
                    ["xcrun", "simctl", "list", "devices"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                sim_output = process.stdout

                # Get device model
                device_model = self.deviceName or "iPhone 14"
                if "Booted" in sim_output:
                    device_pattern = (
                        f"({device_model}|iPhone.*?)\\s+\\([^)]+\\)\\s+\\(Booted\\)"
                    )
                    device_match = re.search(device_pattern, sim_output)
                    if device_match:
                        device_model = device_match.group(1)

                logger.info(
                    f"Using iOS {ios_version} with device model: {device_model}"
                )

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
        logger.info(
            f"Creating Appium session with server: {server_url} and capabilities: {desired_caps}"
        )
        try:
            # Log the session creation request
            self._write_log_entry(
                {
                    "type": "request",
                    "command": "create_session",
                    "capabilities": desired_caps,
                    "status": "pending",
                    "timestamp": time.time(),
                },
                self._network_log_path,
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
            self.driver = webdriver.Remote(command_executor=server_url, options=options)

            # Only set waitForIdleTimeout for Android if not set in capabilities
            if (
                self.platformName.lower() == "android"
                and "settings[waitForIdleTimeout]" not in desired_caps
            ):
                self.driver.update_settings({"waitForIdleTimeout": 0})

            # Wait for session to be fully ready
            logger.info("Waiting for session to stabilize...")
            if not self.wait_for_session_ready():
                raise Exception("Session failed to stabilize within the timeout period")

            # Additional Android-specific stability checks
            if self.platformName.lower() == "android":
                logger.info("Performing additional Android stability checks...")
                if not self.wait_for_android_stability():
                    raise Exception("Android services failed to stabilize")

            # Set up logging
            if self.driver:
                self.setup_request_response_logging(self.driver)
                self.setup_console_logging(self.driver)

            # Start screen recording
            self.start_screen_recording()

            # Log successful session creation
            self._write_log_entry(
                {
                    "command": "create_session",
                    "status": "success",
                    "session_id": self.driver.session_id,
                    "capabilities": desired_caps,
                },
                self._network_log_path,
            )
            logger.info(
                "Appium session created, stabilized, and recording started successfully."
            )

        except Exception as e:
            # Log failed session creation
            if hasattr(self, "_network_log_path"):
                self._write_log_entry(
                    {
                        "type": "request",
                        "command": "create_session",
                        "capabilities": desired_caps,
                        "status": "error",
                        "error": str(e),
                        "timestamp": time.time(),
                    },
                    self._network_log_path,
                )
            logger.error(f"Failed to create Appium session: {e}")
            raise e

    def quit_session(self) -> None:
        """Quit the Appium session."""
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None

            if self.appium_process:
                self.appium_process.terminate()
                self.appium_process = None

            self.stop_device()

            logger.info("Quit Appium session")
        except Exception as e:
            logger.error(f"Error quitting session: {str(e)}")

    def take_screenshot(self, name: str = "screenshot") -> Optional[str]:
        """Take a screenshot of the device screen."""
        try:
            if not self.driver:
                raise RuntimeError("No active driver session")

            # Create screenshots directory if it doesn't exist
            os.makedirs(self.screenshots_dir, exist_ok=True)

            # Generate screenshot path
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            screenshot_path = os.path.join(
                self.screenshots_dir, f"{name}_{timestamp}.png"
            )

            # Take screenshot
            self.driver.get_screenshot_as_file(screenshot_path)

            logger.info(f"Screenshot saved to: {screenshot_path}")
            return screenshot_path

        except Exception as e:
            logger.error(f"Error taking screenshot: {str(e)}")
            return None

    # ─── SCREENSHOT & SESSION RECORDING ─────────────────────────────────────────

    def start_screen_recording(self) -> None:
        """Start recording the device screen."""
        try:
            if not self.driver:
                raise RuntimeError("No active driver session")

            # Create videos directory if it doesn't exist
            os.makedirs(self.videos_dir, exist_ok=True)

            # Start recording
            self.driver.start_recording_screen()

            logger.info("Started screen recording")
        except Exception as e:
            logger.error(f"Error starting screen recording: {str(e)}")
            raise

    def stop_screen_recording(self) -> Optional[str]:
        """Stop recording the device screen and save the video."""
        try:
            if not self.driver:
                raise RuntimeError("No active driver session")

            # Stop recording and get video data
            video_data = self.driver.stop_recording_screen()

            # Generate video path
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            video_path = os.path.join(self.videos_dir, f"recording_{timestamp}.mp4")

            # Save video
            with open(video_path, "wb") as f:
                f.write(base64.b64decode(video_data))

            logger.info(f"Screen recording saved to: {video_path}")
            return video_path

        except Exception as e:
            logger.error(f"Error stopping screen recording: {str(e)}")
            return None

    # ─── DEVICE LOG CAPTURE ─────────────────────────────────────────────────────

    def _move_bugreport_files(self) -> None:
        """Move bugreport files from device to local storage."""
        try:
            # Get bugreport files from device
            result = subprocess.run(
                ["adb", "shell", "ls", "/sdcard/bugreport*"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result.returncode != 0:
                logger.warning("No bugreport files found on device")
                return

            files = result.stdout.strip().split("\n")

            # Create bugreports directory if it doesn't exist
            os.makedirs("bugreports", exist_ok=True)

            # Pull each file
            for file in files:
                if not file:  # Skip empty lines
                    continue

                filename = os.path.basename(file)
                subprocess.run(
                    ["adb", "pull", file, f"bugreports/{filename}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                # Remove file from device after pulling
                subprocess.run(
                    ["adb", "shell", "rm", file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            logger.info("Successfully moved bugreport files")

        except Exception as e:
            logger.error(f"Error moving bugreport files: {e}")
            raise e

    def capture_device_logs(self) -> None:
        """Capture device logs using bugreport."""
        try:
            # Generate bugreport
            subprocess.run(
                ["adb", "bugreport"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            # Move generated files
            self._move_bugreport_files()

            logger.info("Device logs captured successfully")

        except Exception as e:
            logger.error(f"Error capturing device logs: {e}")
            raise e

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
            coords = bounds_str.replace("[", "").replace("]", "").split(",")
            if len(coords) != 4:
                return None

            # Parse into integers
            x1, y1, x2, y2 = map(int, coords)
            return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        except Exception as e:
            logger.debug(f"Error parsing bounds {bounds_str}: {e}")
            return None

    def find_element_best_match(
        self,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None,
        bounds_data: Optional[Dict[str, int]] = None,
        tag_name: Optional[str] = None,
    ) -> WebElement:
        """Find element using the best available locator strategy."""
        start_time = time.time()
        logger.info("[APPIUM_DRIVER_TIMING] Starting element search")

        try:
            if not self.driver:
                raise RuntimeError("No active Appium session")

            driver = cast(WebDriver, self.driver)

            def try_direct_locators() -> Optional[WebElement]:
                """Try direct locator strategies first."""
                if accessibility_id:
                    try:
                        return driver.find_element(
                            AppiumBy.ACCESSIBILITY_ID, accessibility_id
                        )
                    except Exception:
                        pass

                if res_id:
                    try:
                        return driver.find_element(AppiumBy.ID, res_id)
                    except Exception:
                        pass
                return None

            def find_in_tree() -> Optional[WebElement]:
                """Find element using accessibility tree matching."""
                if self._accessibility_tree_cache is None:
                    self.get_accessibility_tree()

                # Call find_best_matching_node with all available parameters
                best_node, score = find_best_matching_node(
                    tree=self._accessibility_tree_cache,
                    res_id=res_id,
                    accessibility_id=accessibility_id,
                    bounds_data=bounds_data,
                    tag_name=tag_name,
                )

                if best_node and score > 0:
                    # Try to find element using the matched node's identifiers
                    if best_node.get("content-desc") or best_node.get(
                        "accessibilityIdentifier"
                    ):
                        try:
                            return driver.find_element(
                                AppiumBy.ACCESSIBILITY_ID,
                                best_node.get("content-desc")
                                or best_node.get("accessibilityIdentifier"),
                            )
                        except Exception:
                            pass

                    if best_node.get("resource-id") or best_node.get("name"):
                        try:
                            return driver.find_element(
                                AppiumBy.ID,
                                best_node.get("resource-id") or best_node.get("name"),
                            )
                        except Exception:
                            pass

                    # Last resort - try by bounds if available
                    if "bounds_data" in best_node:
                        bounds = best_node["bounds_data"]
                        try:
                            if self.platformName.lower() == "android":
                                # Android format
                                xpath = (
                                    f"//*[@bounds='[{bounds['start_x']},{bounds['start_y']}]"
                                    f"[{bounds['end_x']},{bounds['end_y']}]']"
                                )
                            else:
                                # iOS format - use position and size attributes
                                x, y = bounds["start_x"], bounds["start_y"]
                                width = bounds["end_x"] - bounds["start_x"]
                                height = bounds["end_y"] - bounds["start_y"]
                                xpath = (
                                    f"//*[@x='{x}' and @y='{y}' and "
                                    f"@width='{width}' and @height='{height}']"
                                )

                            return driver.find_element(AppiumBy.XPATH, xpath)
                        except Exception:
                            pass
                return None

            # First try: Tree matching
            element = find_in_tree()
            if not element:
                # Second try: Direct locators
                element = try_direct_locators()
                if not element:
                    # If all attempts fail, raise exception with details
                    error_msg = (
                        f"Element not found using: " f"Resource ID: {res_id}, "
                        if res_id
                        else (
                            "" f"Accessibility ID: {accessibility_id}, "
                            if accessibility_id
                            else (
                                "" f"Bounds: {bounds_data}, "
                                if bounds_data
                                else "" f"Tag: {tag_name}" if tag_name else ""
                            )
                        )
                    )
                    raise Exception(error_msg)

            # Third try: Refresh tree and try again
            self.clear_accessibility_tree_cache()
            return element
        except Exception as e:
            end_time = time.time()
            logger.error(
                f"[APPIUM_DRIVER_TIMING] Failed to find element after {end_time - start_time:.2f} seconds: {str(e)}"
            )
            raise

    def click_by_id(
        self,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None,
        bounds_data: Optional[Dict[str, int]] = None,
    ) -> None:
        """Click on an element using any available identifier."""
        if self.should_take_screenshots:
            screenshot_name = (
                f"click_{res_id or ''}"
                f"_{accessibility_id or ''}"
                f"_{bounds_data['start_x'] if bounds_data else ''}"
            )
            self.take_screenshot(f"before_{screenshot_name}")

        try:
            # Use find_element_best_match with all available parameters
            element = self.find_element_best_match(
                res_id=res_id,
                accessibility_id=accessibility_id,
                bounds_data=bounds_data,
            )

            element.click()
            self.clear_accessibility_tree_cache()

            used_identifier = (
                f"Bounds: {bounds_data}"
                if bounds_data
                else (
                    f"Resource ID: {res_id}"
                    if res_id
                    else f"Accessibility ID: {accessibility_id}"
                )
            )
            logger.info(f"Clicked element using {used_identifier}")

        except Exception as e:
            logger.error(f"Error clicking element: {e}")
            raise e

        if self.should_take_screenshots:
            self.take_screenshot(f"after_{screenshot_name}")

    def enter_text_by_id(
        self,
        text: str,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None,
        bounds_data: Optional[Dict[str, int]] = None,
    ) -> None:
        """Enter text using any available identifier."""
        if self.should_take_screenshots:
            screenshot_name = (
                f"enter_text_{res_id or ''}"
                f"_{accessibility_id or ''}"
                f"_{bounds_data['start_x'] if bounds_data else ''}"
            )
            self.take_screenshot(f"before_{screenshot_name}")

        try:
            element = self.find_element_best_match(
                res_id=res_id,
                accessibility_id=accessibility_id,
                bounds_data=bounds_data,
            )

            element.send_keys(text)
            self.clear_accessibility_tree_cache()

            used_identifier = (
                f"Bounds: {bounds_data}"
                if bounds_data
                else (
                    f"Resource ID: {res_id}"
                    if res_id
                    else f"Accessibility ID: {accessibility_id}"
                )
            )
            logger.info(f"Entered text in element using {used_identifier}")

        except Exception as e:
            logger.error(f"Error entering text: {e}")
            raise e

        if self.should_take_screenshots:
            self.take_screenshot(f"after_{screenshot_name}")

    def clear_text_by_id(
        self,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None,
        bounds_data: Optional[Dict[str, int]] = None,
    ) -> None:
        """Clear text using any available identifier."""
        if self.should_take_screenshots:
            screenshot_name = (
                f"clear_text_{res_id or ''}"
                f"_{accessibility_id or ''}"
                f"_{bounds_data['start_x'] if bounds_data else ''}"
            )
            self.take_screenshot(f"before_{screenshot_name}")

        try:
            element = self.find_element_best_match(
                res_id=res_id,
                accessibility_id=accessibility_id,
                bounds_data=bounds_data,
            )

            element.clear()
            self.clear_accessibility_tree_cache()

            used_identifier = (
                f"Bounds: {bounds_data}"
                if bounds_data
                else (
                    f"Resource ID: {res_id}"
                    if res_id
                    else f"Accessibility ID: {accessibility_id}"
                )
            )
            logger.info(f"Cleared text in element using {used_identifier}")

        except Exception as e:
            logger.error(f"Error clearing text: {e}")
            raise e

        if self.should_take_screenshots:
            self.take_screenshot(f"after_{screenshot_name}")

    def long_press_by_id(
        self,
        duration: int = 1000,
        res_id: Optional[str] = None,
        accessibility_id: Optional[str] = None,
        bounds_data: Optional[Dict[str, int]] = None,
    ) -> None:
        """Perform long press using any available identifier."""
        if self.should_take_screenshots:
            screenshot_name = (
                f"long_press_{res_id or ''}"
                f"_{accessibility_id or ''}"
                f"_{bounds_data['start_x'] if bounds_data else ''}"
            )
            self.take_screenshot(f"before_{screenshot_name}")

        try:
            element = self.find_element_best_match(
                res_id=res_id,
                accessibility_id=accessibility_id,
                bounds_data=bounds_data,
            )

            driver = cast(WebDriver, self.driver)

            action = ActionChains(driver)
            action.move_to_element(element)
            action.click_and_hold()
            action.pause(duration / 1000.0)
            action.release()
            action.perform()

            self.clear_accessibility_tree_cache()

            used_identifier = (
                f"Bounds: {bounds_data}"
                if bounds_data
                else (
                    f"Resource ID: {res_id}"
                    if res_id
                    else f"Accessibility ID: {accessibility_id}"
                )
            )
            logger.info(f"Long pressed element using {used_identifier}")

        except Exception as e:
            logger.error(f"Error performing long press: {e}")
            raise e

        if self.should_take_screenshots:
            self.take_screenshot(f"after_{screenshot_name}")

    def perform_tap(self, x: int, y: int) -> None:
        """
        Perform a tap action at the given (x, y) coordinates.
        Captures a screenshot before and after the tap.
        Clears accessibility tree cache after interaction.
        """
        if self.should_take_screenshots:
            self.take_screenshot("before_tap")
        if not self.driver:
            logger.error("No Appium session available for performing tap.")
            raise RuntimeError("No active Appium session")
        logger.info(f"Performing tap at coordinates ({x}, {y})")
        try:
            driver = cast(WebDriver, self.driver)
            driver.tap([(x, y)])
            # Clear cache after interaction
            self.clear_accessibility_tree_cache()
        except Exception as e:
            logger.error(f"Error performing tap: {e}")
            raise e
        if self.should_take_screenshots:
            self.take_screenshot("after_tap")

    def perform_swipe(
        self, end_x: int, end_y: int, start_x: int, start_y: int, duration: int = 800
    ) -> None:
        """
        Perform a swipe gesture from (start_x, start_y) to (end_x, end_y).
        Duration is in milliseconds.
        Captures a screenshot before and after the swipe.
        Clears accessibility tree cache after interaction.
        """
        if self.should_take_screenshots:
            self.take_screenshot("before_swipe")
        if not self.driver:
            logger.error("No Appium session available for performing swipe.")
            raise RuntimeError("No active Appium session")
        logger.info(
            f"Performing swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}) with duration {duration}"
        )
        try:
            driver = cast(WebDriver, self.driver)
            driver.swipe(start_x, start_y, end_x, end_y, duration)
            # Clear cache after interaction
            self.clear_accessibility_tree_cache()
        except Exception as e:
            logger.error(f"Error performing swipe: {e}")
            raise e
        if self.should_take_screenshots:
            self.take_screenshot("after_swipe")

    def scroll_up(self) -> bool:
        """
        Scroll up by one screen height.
        Returns False if end is hit (determined by comparing before/after screenshots).
        Clears accessibility tree cache after interaction.
        """
        if not self.driver:
            logger.error("No Appium session available for scrolling.")
            return False

        # Get viewport size
        viewport = self.get_viewport_size()
        if not viewport:
            logger.error("Unable to get viewport size for scrolling.")
            return False

        # Take screenshot before scrolling
        before_screen = self.see_screen()
        if not before_screen:
            logger.error("Unable to capture screen for scroll comparison.")
            return False

        # Calculate scroll coordinates (scroll from bottom to top)
        start_x = viewport["width"] // 2
        start_y = viewport["height"] * 3 // 4  # Start 3/4 down the screen
        end_y = viewport["height"] // 4  # End 1/4 down the screen

        # Perform the scroll
        self.perform_swipe(start_x, start_y, start_x, end_y, 500)
        # Clear cache after interaction - already handled by perform_swipe
        # but adding here for clarity
        self.clear_accessibility_tree_cache()

        # Take screenshot after scrolling to check if we hit the end
        after_screen = self.see_screen()
        if not after_screen:
            logger.error("Unable to capture screen after scroll.")
            return False

        # Convert PIL images to bytes for comparison
        before_bytes = before_screen.tobytes()
        after_bytes = after_screen.tobytes()

        # If the before and after screenshots are identical, we've hit the end
        return before_bytes != after_bytes

    def scroll_down(self) -> bool:
        """
        Scroll down by one screen height.
        Returns False if end is hit (determined by comparing before/after screenshots).
        Clears accessibility tree cache after interaction.
        """
        if not self.driver:
            logger.error("No Appium session available for scrolling.")
            return False

        # Get viewport size
        viewport = self.get_viewport_size()
        if not viewport:
            logger.error("Unable to get viewport size for scrolling.")
            return False

        # Take screenshot before scrolling
        before_screen = self.see_screen()
        if not before_screen:
            logger.error("Unable to capture screen for scroll comparison.")
            return False

        # Calculate scroll coordinates (scroll from top to bottom)
        start_x = viewport["width"] // 2
        start_y = viewport["height"] // 4  # Start 1/4 down the screen
        end_y = viewport["height"] * 3 // 4  # End 3/4 down the screen

        # Perform the scroll
        self.perform_swipe(start_x, start_y, start_x, end_y, 500)
        # Clear cache after interaction - already handled by perform_swipe
        # but adding here for clarity
        self.clear_accessibility_tree_cache()

        # Take screenshot after scrolling to check if we hit the end
        after_screen = self.see_screen()
        if not after_screen:
            logger.error("Unable to capture screen after scroll.")
            return False

        # Convert PIL images to bytes for comparison
        before_bytes = before_screen.tobytes()
        after_bytes = after_screen.tobytes()

        # If the before and after screenshots are identical, we've hit the end
        return before_bytes != after_bytes

    # ─── ACCESSIBILITY TREE SNAPSHOT ─────────────────────────────────────────────

    def get_accessibility_tree(self) -> Dict[str, Any]:
        """
        Retrieve a detailed accessibility tree (UI hierarchy) of the current screen.
        Uses cached tree if available, otherwise fetches fresh tree.
        Handles both Android and iOS element attributes.
        """
        if not self.driver:
            logger.error("No Appium session available to get accessibility tree.")
            return {}

        try:
            # If cache exists, return it
            if self._accessibility_tree_cache is not None:
                logger.debug("Returning cached accessibility tree")
                return self._accessibility_tree_cache

            driver = cast(WebDriver, self.driver)
            source = driver.page_source
            root = ET.fromstring(source)

            def parse_element_all(elem: ET.Element) -> Dict[str, Any]:
                attrib = elem.attrib
                element_data: Dict[str, Any] = {
                    "children": [parse_element_all(child) for child in list(elem)]
                }

                # Check accessibility-related fields
                is_accessible = (
                    attrib.get("accessible") == "true"
                    or attrib.get("visible") == "true"
                    or attrib.get("a11y-important") == "true"
                    or attrib.get("a11y-focused") == "true"
                    or attrib.get("content-desc")
                    or attrib.get("accessibilityIdentifier")
                    or attrib.get("resource-id")
                    or attrib.get("name")
                    or attrib.get("text")
                    or attrib.get("label")
                )

                # Add all attributes to element data
                element_data.update(attrib)

                # Add accessibility flag
                element_data["is_accessible"] = is_accessible

                # Add bounds data if available
                if "bounds" in attrib:
                    try:
                        # Parse Android bounds format: [x1,y1][x2,y2]
                        bounds_match = re.match(
                            r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrib["bounds"]
                        )
                        if bounds_match:
                            x1, y1, x2, y2 = map(int, bounds_match.groups())
                            element_data["bounds_data"] = {
                                "start_x": x1,
                                "start_y": y1,
                                "end_x": x2,
                                "end_y": y2,
                            }
                    except Exception as e:
                        logger.error(f"Error parsing bounds: {e}")

                return element_data

            # Parse the entire tree
            tree = parse_element_all(root)

            # Cache the tree
            self._accessibility_tree_cache = tree
            return tree

        except Exception as e:
            logger.error(f"Error getting accessibility tree: {e}")
            return {}
