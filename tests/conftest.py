import os
import shutil

import pytest

os.environ.setdefault("IS_TEST_ENV", "true")
# The test suite must never emit telemetry (Sentry events): set before any
# testzeus_hercules import, since telemetry reads the flag at import time.
os.environ["ENABLE_TELEMETRY"] = "0"
os.environ["SENTRY_DSN"] = ""

# Global constants
RUN_DATA_PATH = os.path.join(os.getcwd(), "run_data")
TEST_FEATURES_PATH = os.path.join(os.getcwd(), "test_features")


# Fixture to clean and prepare run_data directory
@pytest.fixture(scope="session", autouse=True)
def prepare_run_data() -> None:
    if os.path.exists(RUN_DATA_PATH):
        shutil.rmtree(RUN_DATA_PATH)
    os.makedirs(RUN_DATA_PATH)
