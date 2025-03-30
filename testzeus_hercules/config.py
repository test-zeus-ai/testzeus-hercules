# config_manager.py

import argparse
import json
import os
from typing import Any, Dict, List, Literal, Optional, Union

from dotenv import load_dotenv
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.timestamp_helper import get_timestamp_str

TS = get_timestamp_str()

# Add browser channel types
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

# Add Portkey configuration types
PORTKEY_STRATEGY_TYPE = Literal["fallback", "loadbalance"]

# Type aliases for better readability
ConfigDict = Dict[str, Any]
PortkeyConfig = Dict[str, Any]
PathsDict = Dict[str, str]


class BaseConfigManager:
    """
    The base class that contains the common logic for:
      - argument parsing
      - loading from dict/JSON
      - optional env variable merging
      - directory creation
      - environment checks
    """

    def __init__(self, config_dict: ConfigDict, ignore_env: bool = False) -> None:
        """
        Initialize the config manager with config_dict as base.

        Args:
            config_dict: The base configuration dictionary.
            ignore_env: If True, environment variables will be ignored.
        """
        # Initialize instance variables
        self.timestamp: str = TS
        self.paths: PathsDict = {}
        self._config: ConfigDict = config_dict.copy()
        self._ignore_env: bool = ignore_env
        self._default_test_id: str = "default"

        # 1) Possibly load .env if not in test environment
        is_test_env = os.environ.get("IS_TEST_ENV", "false").lower() == "true"
        if not is_test_env and not self._ignore_env:
            # Load .env if it exists
            env_file_path: str = ".env"
            load_dotenv(env_file_path, verbose=True, override=True)

        # 2) Parse command-line arguments to override env (if not ignoring env)
        if not self._ignore_env:
            self._parse_arguments()

        # 3) Merge environment variables if not ignoring env
        if not self._ignore_env:
            self._merge_from_env()

        # 4) Perform the same LLM checks as your original code
        self._check_llm_config()

        # 5) Provide or finalize certain defaults
        #    (some might have been overridden by env/arguments)
        self._finalize_defaults()

    @classmethod
    def from_dict(cls, config_dict: ConfigDict, ignore_env: bool = False) -> "BaseConfigManager":
        """Create a BaseConfigManager instance from a dictionary.

        Args:
            config_dict: Configuration dictionary
            ignore_env: Whether to ignore environment variables

        Returns:
            BaseConfigManager instance
        """
        return cls(config_dict=config_dict, ignore_env=ignore_env)

    @classmethod
    def from_json(cls, json_file_path: str, ignore_env: bool = False) -> "BaseConfigManager":
        """Create a BaseConfigManager instance from a JSON file.

        Args:
            json_file_path: Path to JSON configuration file
            ignore_env: Whether to ignore environment variables

        Returns:
            BaseConfigManager instance
        """
        with open(json_file_path, "r", encoding="utf-8") as f:
            config_dict = json.load(f)
        return cls(config_dict=config_dict, ignore_env=ignore_env)

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _parse_arguments(self) -> None:
        """
        Parse Hercules-specific command-line arguments
        and place them into the environment for consistency.
        """
        parser = argparse.ArgumentParser(description="Hercules: The World's First Open-Source AI Agent for End-to-End Testing")
        # Basic path configuration
        parser.add_argument("--input-file", type=str, help="Path to the input file.", required=False)
        parser.add_argument(
            "--output-path",
            type=str,
            help="Path to the output directory.",
            required=False,
        )
        parser.add_argument(
            "--test-data-path",
            type=str,
            help="Path to the test data directory.",
            required=False,
        )
        parser.add_argument(
            "--project-base",
            type=str,
            help="Path to the project base directory.",
            required=False,
        )

        # LLM Model Configuration
        parser.add_argument(
            "--llm-model",
            type=str,
            help="Name of the LLM model.",
            required=False,
        )
        parser.add_argument(
            "--llm-model-api-key",
            type=str,
            help="API key for the LLM model.",
            required=False,
        )
        parser.add_argument(
            "--llm-model-base-url",
            type=str,
            help="Base URL for the LLM API.",
            required=False,
        )
        parser.add_argument(
            "--llm-model-api-type",
            type=str,
            help="Type of API (openai, anthropic, azure, etc.).",
            required=False,
        )
        parser.add_argument(
            "--llm-temperature",
            type=float,
            help="Temperature for LLM sampling (0.0-1.0).",
            required=False,
        )

        # LLM Configuration File
        parser.add_argument(
            "--agents-llm-config-file",
            type=str,
            help="Path to the agents LLM configuration file.",
            required=False,
        )
        parser.add_argument(
            "--agents-llm-config-file-ref-key",
            type=str,
            help="Reference key for the agents LLM configuration file.",
            required=False,
        )

        # Portkey Configuration
        parser.add_argument(
            "--enable-portkey",
            action="store_true",
            help="Enable Portkey integration for LLM routing",
            required=False,
        )
        parser.add_argument(
            "--portkey-api-key",
            type=str,
            help="API key for Portkey",
            required=False,
        )
        parser.add_argument(
            "--portkey-strategy",
            type=str,
            choices=["fallback", "loadbalance"],
            help="Portkey routing strategy (fallback or loadbalance)",
            required=False,
        )

        # Test execution options
        parser.add_argument(
            "--bulk",
            action="store_true",
            help="Execute tests in bulk from tests directory",
            required=False,
        )
        parser.add_argument(
            "--reuse-vector-db",
            action="store_true",
            help="Reuse existing vector DB instead of creating fresh one",
            required=False,
        )

        # Browser options
        parser.add_argument(
            "--browser-channel",
            type=str,
            help="Browser channel to use (e.g., chrome-beta, firefox-nightly)",
            required=False,
        )
        parser.add_argument(
            "--browser-path",
            type=str,
            help="Custom path to browser executable",
            required=False,
        )
        parser.add_argument(
            "--browser-version",
            type=str,
            help="Specific browser version to use (e.g., '114', '115.0.1', 'latest')",
            required=False,
        )
        parser.add_argument(
            "--enable-ublock",
            action="store_true",
            help="Enable uBlock Origin extension",
            required=False,
        )
        parser.add_argument(
            "--disable-ublock",
            action="store_true",
            help="Disable uBlock Origin extension",
            required=False,
        )
        parser.add_argument(
            "--auto-accept-screen-sharing",
            action="store_true",
            help="Automatically accept screen sharing prompts",
            required=False,
        )
        parser.add_argument(
            "--disable-auto-accept-screen-sharing",
            action="store_true",
            help="Disable automatic acceptance of screen sharing prompts",
            required=False,
        )

        # Parse known args; ignore unknown if you have other custom arguments
        args, _ = parser.parse_known_args()

        # Basic path configuration
        if args.input_file:
            os.environ["INPUT_GHERKIN_FILE_PATH"] = args.input_file
        if args.output_path:
            os.environ["JUNIT_XML_BASE_PATH"] = args.output_path
        if args.test_data_path:
            os.environ["TEST_DATA_PATH"] = args.test_data_path
        if args.project_base:
            os.environ["PROJECT_SOURCE_ROOT"] = args.project_base

        # LLM Model Configuration
        if args.llm_model:
            os.environ["LLM_MODEL_NAME"] = args.llm_model
        if args.llm_model_api_key:
            os.environ["LLM_MODEL_API_KEY"] = args.llm_model_api_key
        if args.llm_model_base_url:
            os.environ["LLM_MODEL_BASE_URL"] = args.llm_model_base_url
        if args.llm_model_api_type:
            os.environ["LLM_MODEL_API_TYPE"] = args.llm_model_api_type
        if args.llm_temperature is not None:
            os.environ["LLM_MODEL_TEMPERATURE"] = str(args.llm_temperature)

        # LLM Configuration File
        if args.agents_llm_config_file:
            os.environ["AGENTS_LLM_CONFIG_FILE"] = args.agents_llm_config_file
        if args.agents_llm_config_file_ref_key:
            os.environ["AGENTS_LLM_CONFIG_FILE_REF_KEY"] = args.agents_llm_config_file_ref_key

        # Portkey Configuration
        if args.enable_portkey:
            os.environ["ENABLE_PORTKEY"] = "true"
        if args.portkey_api_key:
            os.environ["PORTKEY_API_KEY"] = args.portkey_api_key
        if args.portkey_strategy:
            os.environ["PORTKEY_STRATEGY"] = args.portkey_strategy

        # Test execution options
        if args.bulk:
            os.environ["EXECUTE_BULK"] = "true"
        if args.reuse_vector_db:
            os.environ["REUSE_VECTOR_DB"] = "true"

        # Browser options
        if args.browser_channel:
            os.environ["BROWSER_CHANNEL"] = args.browser_channel
        if args.browser_path:
            os.environ["BROWSER_PATH"] = args.browser_path
        if args.browser_version:
            os.environ["BROWSER_VERSION"] = args.browser_version
        if args.enable_ublock:
            os.environ["ENABLE_UBLOCK_EXTENSION"] = "true"
        if args.disable_ublock:
            os.environ["ENABLE_UBLOCK_EXTENSION"] = "false"
        if args.auto_accept_screen_sharing:
            os.environ["AUTO_ACCEPT_SCREEN_SHARING"] = "true"
        if args.disable_auto_accept_screen_sharing:
            os.environ["AUTO_ACCEPT_SCREEN_SHARING"] = "false"

    def _merge_from_env(self) -> None:
        """
        Merge any relevant environment variables into the configuration dictionary.
        """
        # The main keys used in your original config:
        relevant_keys = [
            "MODE",
            "PROJECT_SOURCE_ROOT",
            "INPUT_GHERKIN_FILE_PATH",
            "JUNIT_XML_BASE_PATH",
            "TEST_DATA_PATH",
            "BROWSER_TYPE",
            "HEADLESS",
            "RECORD_VIDEO",
            "TAKE_SCREENSHOTS",
            "CAPTURE_NETWORK",
            "CDP_ENDPOINT_URL",
            # LLM model configuration
            "LLM_MODEL_NAME",
            "LLM_MODEL_API_KEY",
            "LLM_MODEL_BASE_URL",
            "LLM_MODEL_API_TYPE",
            "LLM_MODEL_API_VERSION",
            "LLM_MODEL_PROJECT_ID",
            "LLM_MODEL_REGION",
            "LLM_MODEL_CLIENT_HOST",
            "LLM_MODEL_NATIVE_TOOL_CALLS",
            "LLM_MODEL_HIDE_TOOLS",
            "LLM_MODEL_AWS_REGION",
            "LLM_MODEL_AWS_ACCESS_KEY",
            "LLM_MODEL_AWS_SECRET_KEY",
            "LLM_MODEL_AWS_PROFILE_NAME",
            "LLM_MODEL_AWS_SESSION_TOKEN",
            "LLM_MODEL_PRICING",
            # LLM parameter configuration
            "LLM_MODEL_TEMPERATURE",
            "LLM_MODEL_CACHE_SEED",
            "LLM_MODEL_SEED",
            "LLM_MODEL_MAX_TOKENS",
            "LLM_MODEL_PRESENCE_PENALTY",
            "LLM_MODEL_FREQUENCY_PENALTY",
            "LLM_MODEL_STOP",
            # Agent configuration file
            "AGENTS_LLM_CONFIG_FILE",
            "AGENTS_LLM_CONFIG_FILE_REF_KEY",
            # Other configuration
            "HF_HOME",
            "TOKENIZERS_PARALLELISM",
            "DONT_CLOSE_BROWSER",
            "TOKEN_VERBOSE",
            "BROWSER_RESOLUTION",
            "RUN_DEVICE",
            "LOCALE",
            "TIMEZONE",
            "GEOLOCATION",
            "COLOR_SCHEME",
            "LOAD_EXTRA_TOOLS",
            "GEO_PROVIDER",
            "GEO_API_KEY",
            "EXECUTE_BULK",
            "USE_DYNAMIC_LTM",
            "REUSE_VECTOR_DB",
            "ENABLE_BROWSER_LOGS",
            "BROWSER_CHANNEL",
            "BROWSER_VERSION",
            "BROWSER_PATH",
            "ENABLE_PLAYWRIGHT_TRACING",
            "ENABLE_BOUNDING_BOX_SCREENSHOTS",
            "ENABLE_UBLOCK_EXTENSION",
            "AUTO_ACCEPT_SCREEN_SHARING",
            "NO_WAIT_FOR_LOAD_STATE",
            "BROWSER_COOKIES",
            # Portkey-related environment variables
            "ENABLE_PORTKEY",
            "PORTKEY_API_KEY",
            "PORTKEY_STRATEGY",
            "PORTKEY_CACHE_ENABLED",
            "PORTKEY_TARGETS",
            "PORTKEY_GUARDRAILS",
            "PORTKEY_RETRY_COUNT",
            "PORTKEY_TIMEOUT",
            "PORTKEY_CACHE_TTL",
        ]

        for key in relevant_keys:
            if key in os.environ:
                self._config[key] = os.environ[key]

    def _check_llm_config(self) -> None:
        """
        Check LLM configuration including Portkey settings if enabled.
        """
        llm_model_name = self._config.get("LLM_MODEL_NAME")
        llm_model_api_key = self._config.get("LLM_MODEL_API_KEY")
        agents_llm_config_file = self._config.get("AGENTS_LLM_CONFIG_FILE")
        agents_llm_config_file_ref_key = self._config.get("AGENTS_LLM_CONFIG_FILE_REF_KEY")

        # Check if Portkey is enabled
        if self._config.get("ENABLE_PORTKEY", "").lower() == "true":
            portkey_api_key = self._config.get("PORTKEY_API_KEY")
            if not portkey_api_key:
                logger.error("PORTKEY_API_KEY must be set when Portkey is enabled")
                exit(1)

            # Validate Portkey strategy if provided
            portkey_strategy = self._config.get("PORTKEY_STRATEGY")
            if portkey_strategy and portkey_strategy not in ["fallback", "loadbalance"]:
                logger.error("Invalid PORTKEY_STRATEGY. Must be either 'fallback' or 'loadbalance'")
                exit(1)

            # Validate JSON configurations if provided
            for json_key in ["PORTKEY_TARGETS", "PORTKEY_GUARDRAILS"]:
                json_value = self._config.get(json_key)
                if json_value:
                    try:
                        json.loads(json_value)
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON in {json_key}")
                        exit(1)

            # Validate numeric values
            try:
                if retry_count := self._config.get("PORTKEY_RETRY_COUNT"):
                    int(retry_count)
                if timeout := self._config.get("PORTKEY_TIMEOUT"):
                    float(timeout)
                if cache_ttl := self._config.get("PORTKEY_CACHE_TTL"):
                    int(cache_ttl)
            except ValueError as e:
                logger.error(f"Invalid numeric value in Portkey configuration: {e}")
                exit(1)

        if (llm_model_name and llm_model_api_key) and (agents_llm_config_file or agents_llm_config_file_ref_key):
            logger.error("Provide either LLM_MODEL_NAME and LLM_MODEL_API_KEY together, " "or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY together, not both.")
            exit(1)

        if (not llm_model_name or not llm_model_api_key) and (not agents_llm_config_file or not agents_llm_config_file_ref_key):
            logger.error(
                "Either LLM_MODEL_NAME and LLM_MODEL_API_KEY must be set together, "
                "or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY must be set together. "
                "Use --llm-model and --llm-model-api-key in hercules command."
            )
            exit(1)

    def _finalize_defaults(self) -> None:
        """
        Provide or finalize default values for configuration options.
        """
        self._config.setdefault("MODE", "prod")
        self._config.setdefault("DEFAULT_TEST_ID", "default")

        project_source_root = self._config.get("PROJECT_SOURCE_ROOT", "./opt")
        self._config["PROJECT_SOURCE_ROOT"] = project_source_root

        # Fill in paths if missing
        self._config.setdefault(
            "INPUT_GHERKIN_FILE_PATH",
            os.path.join(project_source_root, "input/test.feature"),
        )
        self._config.setdefault("JUNIT_XML_BASE_PATH", os.path.join(project_source_root, "output"))
        self._config.setdefault("TEST_DATA_PATH", os.path.join(project_source_root, "test_data"))
        self._config.setdefault("SCREEN_SHOT_PATH", os.path.join(project_source_root, "proofs"))
        self._config.setdefault("PROJECT_TEMP_PATH", os.path.join(project_source_root, "temp"))
        self._config.setdefault("SOURCE_LOG_FOLDER_PATH", os.path.join(project_source_root, "log_files"))
        self._config.setdefault("TMP_GHERKIN_PATH", os.path.join(project_source_root, "gherkin_files"))

        # Extra environment defaults from original code
        if "HF_HOME" not in self._config:
            self._config["HF_HOME"] = "./.cache"
        if "TOKENIZERS_PARALLELISM" not in self._config:
            self._config["TOKENIZERS_PARALLELISM"] = "false"

        self._config.setdefault("TOKEN_VERBOSE", "false")
        self._config.setdefault("BROWSER_RESOLUTION", "1920,1080")
        self._config.setdefault("RUN_DEVICE", "desktop")
        self._config.setdefault("LOAD_EXTRA_TOOLS", "false")
        self._config.setdefault("LOCALE", "en-US")
        self._config.setdefault("TIMEZONE", None)
        self._config.setdefault("GEOLOCATION", None)
        self._config.setdefault("COLOR_SCHEME", "light")

        # HEADLESS, RECORD_VIDEO, etc. can also be "finalized" here if needed
        self._config.setdefault("HEADLESS", "true")
        self._config.setdefault("RECORD_VIDEO", "true")
        self._config.setdefault("TAKE_SCREENSHOTS", "true")

        self._config.setdefault("CAPTURE_NETWORK", "true")
        self._config.setdefault("DONT_CLOSE_BROWSER", "false")
        self._config.setdefault("GEO_PROVIDER", None)
        self._config.setdefault("GEO_API_KEY", None)
        self._config.setdefault("REACTION_DELAY_TIME", "0.1")
        self._config.setdefault("EXECUTE_BULK", "false")
        self._config.setdefault("ENABLE_PLAYWRIGHT_TRACING", "false")
        self._config.setdefault("REUSE_VECTOR_DB", "false")
        self._config.setdefault("USE_DYNAMIC_LTM", "false")
        self._config.setdefault("ENABLE_BROWSER_LOGS", "false")
        self._config.setdefault("ENABLE_BOUNDING_BOX_SCREENSHOTS", "false")
        self._config.setdefault("AUTO_ACCEPT_SCREEN_SHARING", "true")

        self._config.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

        # Browser configuration
        self._config.setdefault("BROWSER_TYPE", "chromium")
        self._config.setdefault("BROWSER_CHANNEL", None)  # Default to stable channel
        self._config.setdefault("BROWSER_PATH", None)  # Default to system browser
        self._config.setdefault("BROWSER_VERSION", None)  # Default to latest version
        self._config.setdefault("BROWSER_COOKIES", None)  # Default to no cookies

        if self._config["MODE"] == "debug":
            self.timestamp = "0"

        # Set default values for config parameters that weren't specified.
        # Set defaults for missing values
        defaults = {
            "NO_WAIT_FOR_LOAD_STATE": "false",
        }

        for key, value in defaults.items():
            self._config.setdefault(key, value)

        # Add Portkey-related defaults with reasonable values
        self._config.setdefault("ENABLE_PORTKEY", "false")

        # LLM Model Configuration defaults
        # --------------------------------------------------
        # Core model settings
        self._config.setdefault("LLM_MODEL_NAME", "gpt-4o")
        self._config.setdefault("LLM_MODEL_API_KEY", None)  # No default for security
        self._config.setdefault("LLM_MODEL_API_TYPE", "openai")
        # self._config.setdefault("LLM_MODEL_BASE_URL", "https://api.openai.com/v1")
        # self._config.setdefault("LLM_MODEL_API_VERSION", None)

        # Cloud provider specific settings
        # self._config.setdefault("LLM_MODEL_PROJECT_ID", None)  # For Google/GCP
        # self._config.setdefault("LLM_MODEL_REGION", None)  # For cloud providers
        # self._config.setdefault("LLM_MODEL_CLIENT_HOST", None)  # For custom hosting

        # AWS-specific settings
        # self._config.setdefault("LLM_MODEL_AWS_REGION", "us-east-1")
        # self._config.setdefault("LLM_MODEL_AWS_ACCESS_KEY", None)
        # self._config.setdefault("LLM_MODEL_AWS_SECRET_KEY", None)
        # self._config.setdefault("LLM_MODEL_AWS_PROFILE_NAME", None)
        # self._config.setdefault("LLM_MODEL_AWS_SESSION_TOKEN", None)

        # Model behavior settings
        # self._config.setdefault("LLM_MODEL_NATIVE_TOOL_CALLS", "true")
        # self._config.setdefault("LLM_MODEL_HIDE_TOOLS", "false")
        # self._config.setdefault("LLM_MODEL_PRICING", "0.0")  # Default no cost tracking

        # LLM Parameters defaults (based on OpenAI recommendations)
        # --------------------------------------------------
        self._config.setdefault("LLM_MODEL_TEMPERATURE", "0.0")  # Default to deterministic
        self._config.setdefault("LLM_MODEL_CACHE_SEED", None)  # No default seed
        self._config.setdefault("LLM_MODEL_SEED", None)  # No default seed
        self._config.setdefault("LLM_MODEL_MAX_TOKENS", "4096")  # Default reasonable output
        # self._config.setdefault("LLM_MODEL_PRESENCE_PENALTY", "0.0")
        # self._config.setdefault("LLM_MODEL_FREQUENCY_PENALTY", "0.0")
        # self._config.setdefault("LLM_MODEL_STOP", None)  # No default stop sequences

    # -------------------------------------------------------------------------
    # Public Getters & Setters
    # -------------------------------------------------------------------------

    def get_config(self) -> ConfigDict:
        """Return the underlying config dictionary."""
        return self._config

    def get_mode(self) -> str:
        return self._config["MODE"]

    def get_project_source_root(self) -> str:
        return self._config["PROJECT_SOURCE_ROOT"]

    def set_default_test_id(self, test_id: str = "running_interactive") -> None:
        self._default_test_id = test_id
        self._config["DEFAULT_TEST_ID"] = test_id

    def reset_default_test_id(self) -> None:
        self.set_default_test_id("default")

    def get_default_test_id(self) -> str:
        return self._default_test_id

    def get_dont_close_browser(self) -> bool:
        return self._config["DONT_CLOSE_BROWSER"].lower().strip() == "true"

    def get_cdp_config(self) -> Optional[ConfigDict]:
        """Return CDP config if `CDP_ENDPOINT_URL` is set."""
        cdp_endpoint_url = self._config.get("CDP_ENDPOINT_URL")
        if cdp_endpoint_url:
            return {"endpoint_url": cdp_endpoint_url}
        return None

    def get_browser_cookies(self) -> Optional[List[Dict[str, Any]]]:
        """
        Return browser cookies if `BROWSER_COOKIES` is set.
        Expected format is a JSON string representing a list of cookie objects:
        [
            {
                'name': 'mycookie',
                'value': 'cookie_value',
                'domain': 'example.com',
                'path': '/',
                'httpOnly': True,
                'secure': True,
                'expires': -1
            }
        ]
        """
        browser_cookies = self._config.get("BROWSER_COOKIES")
        if browser_cookies:
            logger.info(f"BROWSER_COOKIES: {browser_cookies}")
            try:
                return json.loads(browser_cookies)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse BROWSER_COOKIES: {e}")
        return None

    def should_run_headless(self) -> bool:
        return self._config["HEADLESS"].lower().strip() == "true"

    def should_record_video(self) -> bool:
        return self._config["RECORD_VIDEO"].lower().strip() == "true"

    def should_take_screenshots(self) -> bool:
        return self._config["TAKE_SCREENSHOTS"].lower().strip() == "true"

    def get_browser_type(self) -> str:
        return self._config["BROWSER_TYPE"]

    def should_capture_network(self) -> bool:
        return self._config["CAPTURE_NETWORK"].lower().strip() == "true"

    def get_hf_home(self) -> str:
        return self._config["HF_HOME"]

    def get_delay_time(self) -> float:
        """Return the reaction delay time in seconds."""
        return float(self._config["REACTION_DELAY_TIME"])

    def should_execute_bulk(self) -> bool:
        """Return whether tests should be executed in bulk mode"""
        return self._config["EXECUTE_BULK"].lower().strip() == "true"

    def should_enable_tracing(self) -> bool:
        """Check if Playwright tracing should be enabled"""
        return self._config.get("ENABLE_PLAYWRIGHT_TRACING", "false").lower() == "true"

    def should_reuse_vector_db(self) -> bool:
        """Return whether to reuse existing vector DB or create fresh one."""
        return self._config["REUSE_VECTOR_DB"].lower().strip() == "true"

    def should_use_dynamic_ltm(self) -> bool:
        """Return whether to use dynamic LTM or static LTM."""
        return self._config["USE_DYNAMIC_LTM"].lower().strip() == "true"

    def should_enable_browser_logs(self) -> bool:
        """Check if browser logging should be enabled"""
        return self._config.get("ENABLE_BROWSER_LOGS", "false").lower() == "true"

    def get_browser_channel(self) -> Optional[BROWSER_CHANNELS]:
        """Get the configured browser channel (e.g., chrome-beta, firefox-nightly)"""
        return self._config.get("BROWSER_CHANNEL")

    def get_browser_path(self) -> Optional[str]:
        """Get the custom browser executable path if configured"""
        return self._config.get("BROWSER_PATH")

    def get_browser_version(self) -> Optional[str]:
        """Get the configured browser version (e.g., '114', '115.0.1', 'latest')"""
        return self._config.get("BROWSER_VERSION")

    def should_take_bounding_box_screenshots(self) -> bool:
        """Check if bounding box screenshots should be enabled"""
        return self._config.get("ENABLE_BOUNDING_BOX_SCREENSHOTS", "false").lower() == "true"

    def should_enable_ublock_extension(self) -> bool:
        """Check if uBlock extension should be enabled"""
        return self._config.get("ENABLE_UBLOCK_EXTENSION", "true").lower() == "true"

    def should_auto_accept_screen_sharing(self) -> bool:
        """Check if screen sharing should be automatically accepted"""
        return self._config.get("AUTO_ACCEPT_SCREEN_SHARING", "true").lower() == "true"

    def should_skip_wait_for_load_state(self) -> bool:
        """Return whether to skip wait_for_load_state calls."""
        return self._config["NO_WAIT_FOR_LOAD_STATE"].lower().strip() == "true"

    # -------------------------------------------------------------------------
    # Directory creation logic (mirroring your original code)
    # -------------------------------------------------------------------------

    def get_input_gherkin_file_path(self) -> str:
        path = self._config["INPUT_GHERKIN_FILE_PATH"]
        if not os.path.exists(path):
            base_path = os.path.dirname(path)
            if base_path and not os.path.exists(base_path):
                os.makedirs(base_path)
                logger.info(f"Created INPUT_GHERKIN_FILE_PATH folder at: {base_path}")
        return path

    def get_tmp_gherkin_path(self) -> str:
        path = self._config["TMP_GHERKIN_PATH"]
        if not os.path.exists(path):
            os.makedirs(path)
            logger.info(f"Created TMP_GHERKIN_PATH folder at: {path}")
        return path

    def get_junit_xml_base_path(self) -> str:
        """Get path to junit XML output folder"""
        return self.paths["junit_xml"]

    def get_test_data_path(self) -> str:
        path = self._config["TEST_DATA_PATH"]
        if not os.path.exists(path):
            os.makedirs(path)
            logger.info(f"Created test data folder at: {path}")
        return path

    def get_project_temp_path(self, test_id: Optional[str] = None) -> str:
        test_id = test_id or self._default_test_id
        base_path = self._config["PROJECT_TEMP_PATH"]
        path = os.path.join(base_path, test_id)
        if not os.path.exists(path):
            os.makedirs(path)
            logger.info(f"Created project_temp_path folder at: {path}")
        return path

    def get_token_verbose(self) -> bool:
        return self._config["TOKEN_VERBOSE"].lower().strip() == "true"

    def get_resolution(self) -> str:
        return self._config["BROWSER_RESOLUTION"]

    def get_run_device(self) -> str:
        return self._config["RUN_DEVICE"]

    def get_load_extra_tools(self) -> str:
        return self._config["LOAD_EXTRA_TOOLS"]

    def get_locale(self) -> str:
        return self._config["LOCALE"]

    def get_timezone(self) -> str:
        return self._config["TIMEZONE"]

    def get_geolocation(self) -> str:
        return self._config["GEOLOCATION"]

    def get_color_scheme(self) -> str:
        return self._config["COLOR_SCHEME"]

    def get_geo_provider(self) -> str:
        return self._config["GEO_PROVIDER"]

    def get_geo_api_key(self) -> str:
        return self._config["GEO_API_KEY"]

    def get_trace_path(self, stake_id: Optional[str] = None) -> PathsDict:
        """Get all trace related paths for a test run."""
        base_path = self.get_project_source_root()
        test_id = stake_id if stake_id else self._default_test_id

        paths: PathsDict = {
            "proofs": os.path.join(base_path, "proofs", test_id, self.timestamp),
            "junit_xml": os.path.join(base_path, "output", self.timestamp),
            "log_files": os.path.join(base_path, "log_files", test_id, self.timestamp),
        }

        # Create directories
        for path in paths.values():
            os.makedirs(path, exist_ok=True)

        self.paths = paths
        return paths

    def ensure_trace_dirs(self, paths: dict[str, str]) -> None:
        """Ensure all trace directories exist"""
        for path in paths.values():
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def get_proof_path(self, test_id: Optional[str] = None) -> str:
        """Get path to proof folder, optionally including test_id"""
        paths = self.get_trace_path(stake_id=test_id)
        return paths["proofs"]

    def get_source_log_folder_path(self, test_id: Optional[str] = None) -> str:
        """Get path to log folder, optionally including test_id"""
        paths = self.get_trace_path(stake_id=test_id)
        return paths["log_files"]

    # -------------------------------------------------------------------------
    # Telemetry
    # -------------------------------------------------------------------------

    def send_config_telemetry(self) -> None:
        """
        Send a snapshot of the config to telemetry (like your final block).
        """
        config_brief = {
            "MODE": self.get_mode(),
            "HEADLESS": self.should_run_headless(),
            "RECORD_VIDEO": self.should_record_video(),
            "TAKE_SCREENSHOTS": self.should_take_screenshots(),
            "BROWSER_TYPE": self.get_browser_type(),
            "CAPTURE_NETWORK": self.should_capture_network(),
            "REACTION_DELAY_TIME": self.get_delay_time(),  # Changed key name in telemetry
            "PORTKEY_ENABLED": self.is_portkey_enabled(),
        }
        add_event(
            EventType.CONFIG,
            EventData(detail="General Config", additional_data=config_brief),
        )

    # Add Portkey-specific getters
    def is_portkey_enabled(self) -> bool:
        """Check if Portkey integration is enabled."""
        return self._config["ENABLE_PORTKEY"].lower() == "true"

    def get_portkey_api_key(self) -> Optional[str]:
        """Get the Portkey API key if configured."""
        return self._config.get("PORTKEY_API_KEY")

    def get_portkey_config(self) -> PortkeyConfig:
        """Get the complete Portkey configuration."""
        config: PortkeyConfig = {}

        if self.is_portkey_enabled():
            # Add strategy if configured
            strategy = self._config.get("PORTKEY_STRATEGY")
            if strategy:
                config["strategy"] = {"mode": strategy}

            # Add cache settings if enabled
            if self._config.get("PORTKEY_CACHE_ENABLED", "").lower() == "true":
                config["cache"] = {
                    "mode": "semantic",
                    "ttl": int(self._config.get("PORTKEY_CACHE_TTL", "3600")),
                }

            # Add retry and timeout settings
            config["retry"] = {
                "count": int(self._config.get("PORTKEY_RETRY_COUNT", "3")),
                "timeout": float(self._config.get("PORTKEY_TIMEOUT", "30.0")),
            }

            # Add targets if configured
            targets = self._config.get("PORTKEY_TARGETS")
            if targets:
                try:
                    config["targets"] = json.loads(targets)
                except json.JSONDecodeError:
                    logger.warning("Invalid PORTKEY_TARGETS JSON, skipping")

            # Add guardrails if configured
            guardrails = self._config.get("PORTKEY_GUARDRAILS")
            if guardrails:
                try:
                    config["guardrails"] = json.loads(guardrails)
                except json.JSONDecodeError:
                    logger.warning("Invalid PORTKEY_GUARDRAILS JSON, skipping")

        return config


# ------------------------------------------------------------------------------
# Derived classes for Non-Singleton and Singleton usage
# ------------------------------------------------------------------------------


class NonSingletonConfigManager(BaseConfigManager):
    """
    A regular config manager that can be instantiated multiple times
    without any shared state among instances.
    """

    def __init__(self, config_dict: ConfigDict, ignore_env: bool = False):
        super().__init__(config_dict=config_dict, ignore_env=ignore_env)


class SingletonConfigManager(BaseConfigManager):
    """Singleton configuration manager for the entire application."""

    _instance: Optional["SingletonConfigManager"] = None

    def __init__(self, config_dict: ConfigDict, ignore_env: bool = False):
        if SingletonConfigManager._instance is not None:
            raise RuntimeError("Use SingletonConfigManager.instance() instead")
        super().__init__(config_dict=config_dict, ignore_env=ignore_env)

    @classmethod
    def instance(
        cls,
        config_dict: Optional[ConfigDict] = None,
        ignore_env: bool = False,
        override: bool = False,
    ) -> "SingletonConfigManager":
        if override and config_dict is not None:
            cls.reset_instance()
            cls._instance = cls(config_dict=config_dict or {}, ignore_env=ignore_env)
            logger.info("SingletonConfigManager instance reset with new config")
        elif cls._instance is None:
            cls._instance = cls(config_dict=config_dict or {}, ignore_env=ignore_env)
        elif config_dict is not None:
            cls._instance._config.update(config_dict)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None


def get_global_conf() -> SingletonConfigManager:
    return SingletonConfigManager.instance()


def set_global_conf(
    config_dict: Optional[ConfigDict] = None,
    ignore_env: bool = False,
    override: bool = False,
) -> SingletonConfigManager:
    res = SingletonConfigManager.instance(
        config_dict=config_dict,
        ignore_env=ignore_env,
        override=override,
    )
    return res


set_global_conf(
    {
        "MODE": "prod",
        "PROJECT_SOURCE_ROOT": "./opt",
    }
)

# Load telemetry after CONF is initialized
from testzeus_hercules.telemetry import EventData, EventType, add_event

logger.info("[Singleton] MODE: %s", get_global_conf().get_mode())
logger.info("[Singleton] Project Source Root: %s", get_global_conf().get_project_source_root())
# Send final telemetryCONF.send_config_telemetry()
