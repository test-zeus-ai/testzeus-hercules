import logging
from logging.handlers import RotatingFileHandler

import pytest
from testzeus_hercules.utils.logger import configure_logger, get_log_file_path, logger

LIB_LOGGERS = ["openai", "langchain", "langgraph"]


@pytest.fixture
def restore_handlers():
    saved_main = logger.handlers[:]
    saved_lib = {name: logging.getLogger(name).handlers[:] for name in LIB_LOGGERS}
    yield
    # Detach and close any file handlers created by the test, then restore
    # the original handler state.
    for target in [logger, *(logging.getLogger(name) for name in LIB_LOGGERS)]:
        for handler in list(target.handlers):
            target.removeHandler(handler)
            if isinstance(handler, RotatingFileHandler):
                handler.close()
    logger.handlers = saved_main
    for name, handlers in saved_lib.items():
        logging.getLogger(name).handlers = handlers


def _file_handlers(target: logging.Logger) -> list[RotatingFileHandler]:
    return [h for h in target.handlers if isinstance(h, RotatingFileHandler)]


def test_log_file_receives_messages(tmp_path, monkeypatch, restore_handlers) -> None:
    # File lives inside a not-yet-existing directory to verify makedirs
    log_file = tmp_path / "logs" / "hercules.log"
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))

    configure_logger()

    assert get_log_file_path() == str(log_file)
    assert len(_file_handlers(logger)) == 1
    for name in LIB_LOGGERS:
        assert len(_file_handlers(logging.getLogger(name))) == 1

    message = "file runtime smoke message"
    logger.info(message)

    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists()
    assert message in log_file.read_text(encoding="utf-8")


def test_double_configure_keeps_single_file_handler(tmp_path, monkeypatch, restore_handlers) -> None:
    log_file = tmp_path / "hercules.log"
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))

    configure_logger()
    configure_logger()

    assert len(_file_handlers(logger)) == 1
    for name in LIB_LOGGERS:
        assert len(_file_handlers(logging.getLogger(name))) == 1

    message = "message after reconfigure"
    logger.info(message)
    for handler in logger.handlers:
        handler.flush()

    assert message in log_file.read_text(encoding="utf-8")


def test_no_file_handler_when_env_unset(monkeypatch, restore_handlers) -> None:
    monkeypatch.delenv("LOG_FILE_PATH", raising=False)

    configure_logger()

    assert get_log_file_path() is None
    assert _file_handlers(logger) == []
    for name in LIB_LOGGERS:
        assert _file_handlers(logging.getLogger(name)) == []


def test_empty_log_file_path_is_ignored(monkeypatch, restore_handlers) -> None:
    monkeypatch.setenv("LOG_FILE_PATH", "")

    configure_logger()

    assert get_log_file_path() is None
    assert _file_handlers(logger) == []
    for name in LIB_LOGGERS:
        assert _file_handlers(logging.getLogger(name)) == []
