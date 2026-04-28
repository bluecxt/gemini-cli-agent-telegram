"""
Logging Configuration - Sets up structured logging.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
import os

""" Ensure logs directory exists """
os.makedirs("logs", exist_ok=True)


class YoloFilter(logging.Filter):
    def filter(self, record):
        # Filter out the YOLO mode warning message
        return "YOLO mode is enabled" not in record.getMessage()

def setup_logger():
    """Configures a structured logger with console and file output."""
    logger = logging.getLogger("GeminiAgent")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    """ Console Handler """
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(YoloFilter())

    """ File Handler (Rotates at 5MB, keeps 5 backups) """
    file_handler = RotatingFileHandler(
        "logs/agent.log", maxBytes=5*1024*1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(YoloFilter())

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
