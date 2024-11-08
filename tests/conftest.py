import pytest
import os
import shutil

# Global constants
RUN_DATA_PATH = os.path.join(os.getcwd(), "run_data")
TEST_FEATURES_PATH = os.path.join(os.getcwd(), "test_features")


# Fixture to clean and prepare run_data directory
@pytest.fixture(scope="session", autouse=True)
def prepare_run_data() -> None:
    if os.path.exists(RUN_DATA_PATH):
        shutil.rmtree(RUN_DATA_PATH)
    os.makedirs(RUN_DATA_PATH)
