import os

# The remaining v1 modules (kept for the API security scan) read this at import
# time. Set before any testzeus_hercules import.
os.environ.setdefault("IS_TEST_ENV", "true")
