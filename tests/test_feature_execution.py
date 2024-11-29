import os
import shutil
import subprocess
import pytest
from tests.test_base import setup_test_environment, copy_feature_files, compare_results
from dotenv.main import dotenv_values


# Function to retrieve all feature folders
def get_feature_folders() -> list[str]:
    test_features_dir = os.path.join(os.path.dirname(__file__), "test_features")
    return [name for name in os.listdir(test_features_dir) if os.path.isdir(os.path.join(test_features_dir, name))]


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

    # Execute Hercules with the updated .env file
    try:
        result = subprocess.run(
            ["poetry", "run", "python", "-m", "testzeus_hercules"],
            check=True,
            capture_output=True,
            env=load_env_in_dict | dict(os.environ),
            encoding="utf-8",
            text=True,
            errors="replace",
        )
        print(f"Standard Output:\n{result.stdout}")
        print(f"Standard Error:\n{result.stderr}")
        print(f"Return Code: {result.returncode}")
    except subprocess.CalledProcessError as e:
        print(f"Subprocess failed with return code {e.returncode}")
        print(f"Standard Output:\n{e.stdout}")
        print(f"Standard Error:\n{e.stderr}")
        assert False, f"Hercules execution failed for {feature_folder}"

    # assert on result.returncode == 0, f"Hercules execution failed for {feature_folder}"
    assert result.returncode == 0, f"Hercules execution failed for {feature_folder}"

    # Compare results
    expected_file = os.path.join(feature_path, "expected_results.txt")
    output_folder = os.path.join(current_test_data_path, "output")
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
