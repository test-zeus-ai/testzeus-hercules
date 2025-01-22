from datetime import datetime


def get_timestamp_str() -> str:
    """Generate a timestamp string in the format YYYYMMDD_HHMMSS"""
    return "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
