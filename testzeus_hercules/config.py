# config.py at the project source code root
import os
from typing import Optional

from dotenv import load_dotenv

IS_TEST_ENV = os.environ.get("IS_TEST_ENV", "false").lower() == "true"

if not IS_TEST_ENV:
    env_file_path: str = ".env"
    load_dotenv(env_file_path, verbose=True, override=True)

from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

# Check that either LLM_MODEL_NAME and LLM_MODEL_API_KEY are set together,
# or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY are set together
llm_model_name = os.environ.get("LLM_MODEL_NAME")
llm_model_api_key = os.environ.get("LLM_MODEL_API_KEY")
agents_llm_config_file = os.environ.get("AGENTS_LLM_CONFIG_FILE")
agents_llm_config_file_ref_key = os.environ.get("AGENTS_LLM_CONFIG_FILE_REF_KEY")

if (llm_model_name and llm_model_api_key) and (
    agents_llm_config_file or agents_llm_config_file_ref_key
):
    logger.error(
        "Provide either LLM_MODEL_NAME and LLM_MODEL_API_KEY together, or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY together, not both."
    )
    exit(1)

if (not llm_model_name or not llm_model_api_key) and (
    not agents_llm_config_file or not agents_llm_config_file_ref_key
):
    logger.error(
        "Either LLM_MODEL_NAME and LLM_MODEL_API_KEY must be set together, or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY must be set together. user --llm-model and --llm-model-api-key in hercules command"
    )
    exit(1)

MODE = os.environ.get("MODE", "prod")
DEFAULT_TEST_ID = "default"

# Get the project source root path
PROJECT_SOURCE_ROOT = os.environ.get("PROJECT_SOURCE_ROOT", "./opt")
CDP_ENDPOINT_URL = os.environ.get("CDP_ENDPOINT_URL")

PROJECT_ROOT = PROJECT_SOURCE_ROOT
PROJECT_TEMP_PATH = os.path.join(PROJECT_ROOT, "temp")
PROJECT_TEST_ROOT = os.path.join(PROJECT_ROOT, "test")

INPUT_GHERKIN_FILE_PATH = os.environ.get("INPUT_GHERKIN_FILE_PATH") or os.path.join(
    PROJECT_ROOT, "input/test.feature"
)
TMP_GHERKIN_PATH = os.path.join(PROJECT_ROOT, "gherkin_files")
JUNIT_XML_BASE_PATH = os.environ.get("JUNIT_XML_BASE_PATH") or os.path.join(
    PROJECT_ROOT, "output"
)

SOURCE_LOG_FOLDER_PATH = os.path.join(PROJECT_ROOT, "log_files")
TEST_DATA_PATH = os.environ.get("TEST_DATA_PATH") or os.path.join(
    PROJECT_ROOT, "test_data"
)
SCREEN_SHOT_PATH = os.path.join(PROJECT_ROOT, "proofs")

if "HF_HOME" not in os.environ:
    os.environ["HF_HOME"] = "./.cache"

if "TOKENIZERS_PARALLELISM" not in os.environ:
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def get_cdp_config() -> dict | None:
    """
    Get the CDP config.
    """
    if CDP_ENDPOINT_URL:
        cdp_config = {
            "endpoint_url": CDP_ENDPOINT_URL,
        }
        return cdp_config
    return None


def set_default_test_id(test_id: str) -> None:
    """
    Set the default test ID.

    Args:
        test_id (str): The test ID.
    """
    global DEFAULT_TEST_ID
    DEFAULT_TEST_ID = test_id


def reset_default_test_id() -> None:
    """
    Reset the default test ID.
    """
    global DEFAULT_TEST_ID
    DEFAULT_TEST_ID = "default"


def should_run_headless() -> bool:
    """
    Check if the system should run in headless mode.
    """
    return os.environ.get("HEADLESS", "true").lower().strip() == "true"


def should_record_video() -> bool:
    """
    Check if the system should record video.
    """
    return os.environ.get("RECORD_VIDEO", "true").lower().strip() == "true"


def should_take_screenshots() -> bool:
    """
    Check if the system should take screenshots.
    """
    return os.environ.get("TAKE_SCREENSHOTS", "true").lower().strip() == "true"


def get_browser_type() -> str:
    """
    Get the browser type to use.

    Returns:
        str: The browser type.
    """
    return os.environ.get("BROWSER_TYPE", "chromium")


def should_capture_network() -> bool:
    """
    Check if the system should capture network traffic.
    """
    return os.environ.get("CAPTURE_NETWORK", "true").lower().strip() == "true"


def get_input_gherkin_file_path() -> str:
    """
    Check if the INPUT_GHERKIN_FILE_PATH exists, and if not, create it.

    Returns:
        str: The input Gherkin file path.
    """
    if not os.path.exists(INPUT_GHERKIN_FILE_PATH):
        base_path = os.path.dirname(INPUT_GHERKIN_FILE_PATH)
        if not os.path.exists(base_path):
            os.makedirs(base_path)
        logger.info(
            f"Created INPUT_GHERKIN_FILE_PATH folder at: {INPUT_GHERKIN_FILE_PATH}"
        )
    return INPUT_GHERKIN_FILE_PATH


def get_tmp_gherkin_path() -> str:
    """
    Check if the TMP_GHERKIN_PATH exists, and if not, create it.

    Returns:
        str: The temporary Gherkin path.
    """
    if not os.path.exists(TMP_GHERKIN_PATH):
        os.makedirs(TMP_GHERKIN_PATH)
        logger.info(f"Created TMP_GHERKIN_PATH folder at: {TMP_GHERKIN_PATH}")
    return TMP_GHERKIN_PATH


def get_junit_xml_base_path() -> str:
    """
    Check if the JUNIT_XML_BASE_PATH exists, and if not, create it.

    Returns:
        str: The JUnit XML base path.
    """
    if not os.path.exists(JUNIT_XML_BASE_PATH):
        os.makedirs(JUNIT_XML_BASE_PATH)
        logger.info(f"Created JUNIT_XML_BASE_PATH folder at: {JUNIT_XML_BASE_PATH}")
    return JUNIT_XML_BASE_PATH


def get_source_log_folder_path(test_id: Optional[str] = None) -> str:
    """
    Check if the source log folder exists for the given test_id, and if not, create it.

    Args:
        test_id (Optional[str]): The test ID. Defaults to DEFAULT_TEST_ID.

    Returns:
        str: The source log folder path.
    """
    test_id = test_id or DEFAULT_TEST_ID
    source_log_folder_path = os.path.join(SOURCE_LOG_FOLDER_PATH, test_id)
    if not os.path.exists(source_log_folder_path):
        os.makedirs(source_log_folder_path)
        logger.info(
            f"Created source_log_folder_path folder at: {source_log_folder_path}"
        )
    return source_log_folder_path


def get_test_data_path() -> str:
    """
    Check if the test data folder exists for the given test_id, and if not, create it.
    Returns:
        str: The test data folder path.
    """
    test_data_path = TEST_DATA_PATH
    if not os.path.exists(test_data_path):
        os.makedirs(test_data_path)
        logger.info(f"Created test data folder at: {test_data_path}")
    return test_data_path


def get_screen_shot_path(test_id: Optional[str] = None) -> str:
    """
    Check if the screenshot folder exists for the given test_id, and if not, create it.

    Args:
        test_id (Optional[str]): The test ID. Defaults to DEFAULT_TEST_ID.

    Returns:
        str: The screenshot folder path.
    """
    test_id = test_id or DEFAULT_TEST_ID
    screen_shot_path = os.path.join(SCREEN_SHOT_PATH, test_id)
    if not os.path.exists(screen_shot_path):
        os.makedirs(os.path.join(screen_shot_path, "screenshots"))
        os.makedirs(os.path.join(screen_shot_path, "videos"))
        logger.info(f"Created screen_shot_path folder at: {screen_shot_path}")
    return screen_shot_path


def get_project_temp_path(test_id: Optional[str] = None) -> str:
    """
    Check if the project temporary path exists for the given test_id, and if not, create it.

    Args:
        test_id (Optional[str]): The test ID. Defaults to DEFAULT_TEST_ID.

    Returns:
        str: The project temporary path.
    """
    test_id = test_id or DEFAULT_TEST_ID
    project_temp_path = os.path.join(PROJECT_TEMP_PATH, test_id)
    if not os.path.exists(project_temp_path):
        os.makedirs(project_temp_path)
        logger.info(f"Created project_temp_path folder at: {project_temp_path}")
    return project_temp_path


# send general config to telemetry, use get methods to get the values
config_brief = {
    "MODE": MODE,
    "HEADLESS": should_run_headless(),
    "RECORD_VIDEO": should_record_video(),
    "TAKE_SCREENSHOTS": should_take_screenshots(),
    "BROWSER_TYPE": get_browser_type(),
    "CAPTURE_NETWORK": should_capture_network(),
}
add_event(
    EventType.CONFIG, EventData(detail="General Config", additional_data=config_brief)
)
