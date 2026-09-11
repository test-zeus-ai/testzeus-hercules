import logging
import os
from logging.handlers import RotatingFileHandler

from pythonjsonlogger import jsonlogger

logger = logging.getLogger(__name__)

# Path of the active log file, or None when file logging is disabled
_log_file_path: str | None = None


# Custom function to configure the logger
def configure_logger(level: str = "INFO") -> None:
    global _log_file_path

    log_format = os.getenv("LOG_MESSAGES_FORMAT", "text").lower()

    # Set log level for the main logger
    logger.setLevel(level.upper())

    # Create a handler for logging
    handler = logging.StreamHandler()

    if log_format == "json":
        # JSON format
        formatter = jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s %(filename)s %(lineno)d",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        # Text format
        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(levelname)s {%(filename)s:%(lineno)d} - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)

    # Optional rotating file handler, enabled via the LOG_FILE_PATH env var
    file_path = os.getenv("LOG_FILE_PATH", "").strip()
    file_handler: RotatingFileHandler | None = None
    if file_path:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        file_handler = RotatingFileHandler(file_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(formatter)

    http_loggers = ["openai", "langchain", "langgraph"]

    # Handlers are replaced on every call; close any file handler from a
    # previous configuration so re-calling does not leak file handles.
    for old_handler in [*logger.handlers, *(h for http_logger in http_loggers for h in logging.getLogger(http_logger).handlers)]:
        if isinstance(old_handler, RotatingFileHandler):
            old_handler.close()

    logger.handlers = []  # Clear existing handlers
    logger.addHandler(handler)
    if file_handler is not None:
        logger.addHandler(file_handler)

    # Ensure other loggers have the same handler
    for http_logger in http_loggers:
        lib_logger = logging.getLogger(http_logger)
        lib_logger.setLevel(level.upper())
        lib_logger.handlers = []  # Clear any existing handlers
        lib_logger.addHandler(handler)  # Add the same handler
        if file_handler is not None:
            lib_logger.addHandler(file_handler)  # Add the same file handler

    _log_file_path = file_path or None


# Call the configure logger function to set up the logger initially
configure_logger(level="INFO")


# Function to set log level
def set_log_level(level: str) -> None:
    """
    Set the log level for the logger.

    Parameters:
    - level (str): A logging level such as 'debug', 'info', 'warning', 'error', or 'critical'.
    """
    configure_logger(level)


def get_log_file_path() -> str | None:
    """
    Return the path of the active log file, or None if file logging is disabled.
    """
    return _log_file_path


# Set default log levels for other libraries
# logging.getLogger("httpcore").setLevel(logging.DEBUG)
# logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("openai").setLevel(logging.INFO)
# logging.getLogger("langgraph").setLevel(logging.DEBUG)
logging.getLogger("matplotlib.pyplot").setLevel(logging.WARNING)
logging.getLogger("PIL.PngImagePlugin").setLevel(logging.WARNING)
logging.getLogger("PIL.Image").setLevel(logging.WARNING)

# Re-export the logger for ease of use
__all__ = ["logger", "set_log_level", "get_log_file_path"]
