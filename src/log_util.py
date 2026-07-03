"""Write bot output to console and logs/bot.log (rotated)."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging():
    log_dir = os.environ.get("LOG_DIR", os.path.join(os.path.dirname(__file__), "..", "logs"))
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "bot.log")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    return log_path


def install_print_logger():
    """Copy every print() into bot.log; console unchanged."""
    import builtins

    original_print = builtins.print

    def logged_print(*args, **kwargs):
        message = " ".join(str(a) for a in args)
        if message:
            logging.info(message)
        original_print(*args, **kwargs)

    builtins.print = logged_print
