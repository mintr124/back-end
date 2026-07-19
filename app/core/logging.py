"""
Logging configuration. Sets up a single stdout StreamHandler with a
consistent timestamped format for all application loggers.
"""
import logging
import sys


# Configure the root logger with a stdout handler and a structured format string.
def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
