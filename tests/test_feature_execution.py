import os
import shutil
import subprocess
import sys
import pytest
from tests.test_base import setup_test_environment, copy_feature_files, compare_results
from dotenv.main import dotenv_values


def is_llm_provider_auth_failure(stderr: str | None) -> bool:
    """Return True when a subprocess failed because CI provider credentials are invalid."""
    if not stderr:
        return False

    normalized = stderr.lower()
    return "authenticationerror" in normalized and ("invalid_api_key" in normalized or "incorrect api key" in normalized)


def test_is_llm_provider_auth_failure() -> None:
    assert is_llm_provider_auth_failure("openai.AuthenticationError: invalid_api_key")
    assert is_llm_provider_auth_failure("AuthenticationError: Incorrect API key provided")
    assert not is_llm_provider_auth_failure("TypeError: unexpected keyword argument")


# Function to retrieve all feature folders
def get_feature_folders() -> list[str]:
    test_features_dir = os.path.join(os.path.dirname(__file__), "test_features")
    return [name for name in os.listdir(test_features_dir) if os.path.isdir(os.path.join(test_features_dir, name))]


def get_default_playwright_browsers_path(home: str) -> str:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches", "ms-playwright")
    if os.name == "nt":
        return os.path.join(home, "AppData", "Local", "ms-playwright")
    return os.path.join(home, ".cache", "ms-playwright")


# Parameterize the test function to run for each feature folder
@pytest.mark.flaky(reruns=2, reruns_delay=2)
@pytest.mark.parametrize("feature_folder", get_feature_folders())
def test_feature_execution(feature_folder: str) -> None:
    """
    Test the execution of a feature.

    Args:
        feature_folder (str): The folder containing the feature to test.
    """
    # Setup paths and environment

    current_test_data_path, input_path, test_data_path = setup_test_environment(feature_folder)
    feature_path = os.path.join(os.path.dirname(__file__), "test_features", feature_folder)
    copy_feature_files(feature_path, input_path, test_data_path)

    env_file_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_env_in_dict = dict(os.environ)
    og_env_in_dict = dotenv_values(env_file_path)
    load_env_in_dict.update(og_env_in_dict)
    load_env_in_dict["PROJECT_SOURCE_ROOT"] = current_test_data_path
    load_env_in_dict["IS_TEST_ENV"] = "true"
    load_env_in_dict["MODE"] = "debug"
    load_env_in_dict["ENABLE_TELEMETRY"] = "0"
    load_env_in_dict["AUTO_MODE"] = "1"
    load_env_in_dict["TOKEN_VERBOSE"] = "true"
    load_env_in_dict["GEO_PROVIDER"] = "maps_co"
    load_env_in_dict["LOAD_EXTRA_TOOLS"] = "true"
    load_env_in_dict["RECORD_VIDEO"] = "false"
    load_env_in_dict["ENABLE_UBLOCK_EXTENSION"] = "false"
    load_env_in_dict["LANG"] = "en_US.UTF-8"
    real_home = os.path.expanduser("~")
    load_env_in_dict["PLAYWRIGHT_BROWSERS_PATH"] = get_default_playwright_browsers_path(real_home)
    load_env_in_dict["NLTK_DATA"] = os.path.join(real_home, "nltk_data")
    load_env_in_dict["HOME"] = current_test_data_path
    load_env_in_dict["BROWSER_STORAGE_DIR"] = os.path.join(current_test_data_path, "browser-profile")
    load_env_in_dict["MPLCONFIGDIR"] = os.path.join(current_test_data_path, "matplotlib")
    load_env_in_dict["XDG_CACHE_HOME"] = os.path.join(current_test_data_path, ".cache")

    # Execute Hercules with the updated .env file
    try:
        result = subprocess.run(
            [sys.executable, "-m", "testzeus_hercules"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=load_env_in_dict,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        print(f"Standard Output:\n{result.stdout}")
        print(f"Standard Error:\n{result.stderr}")
        print(f"Return Code: {result.returncode}")
    except subprocess.CalledProcessError as e:
        print(f"Subprocess failed with return code {e.returncode}")
        print(f"Standard Output:\n{e.stdout}")
        print(f"Standard Error:\n{e.stderr}")
        if is_llm_provider_auth_failure(e.stderr):
            pytest.skip("LLM provider rejected CI credentials; skipping live feature execution.")
        assert False, f"Hercules execution failed for {feature_folder}"

    # assert on result.returncode == 0, f"Hercules execution failed for {feature_folder}"
    assert result.returncode == 0, f"Hercules execution failed for {feature_folder}"

    # Compare results
    expected_file = os.path.join(feature_path, "expected_results.txt")
    output_folder = os.path.join(current_test_data_path, "output/0")
    os.makedirs(output_folder, exist_ok=True)

    comparison_result = compare_results(expected_file, output_folder)
    assert comparison_result, f"Test failed for {feature_folder}"

    # Clean up
    # shutil.rmtree(current_test_data_path)


def test_generate_final_report() -> None:
    """
    After all tests, generate a final report.
    """
    with open("run_data/results.xml", "w", encoding="utf-8") as final_report:
        final_report.write("<testResults>\n")
        # Summarize individual test logs here
        final_report.write("</testResults>\n")
