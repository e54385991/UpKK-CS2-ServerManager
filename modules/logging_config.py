"""
Logging configuration for CS2 Server Manager
Configures rotating file handler with automatic log rotation
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

# Log directory and file settings
LOG_DIR = "logs"
LOG_FILE = "cs2_manager.log"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 10  # Keep 10 backup files
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _get_log_level(level_str: str) -> int:
    """
    Convert string log level to logging constant
    
    Args:
        level_str: Log level as string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Returns:
        Logging level constant (defaults to INFO if invalid)
    """
    if not level_str or not isinstance(level_str, str):
        return logging.INFO
    
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    return level_map.get(level_str.upper(), logging.INFO)


def setup_logging(level: int = logging.INFO, asyncssh_level: Optional[str] = None) -> None:
    """
    Configure logging with rotating file handler.
    
    Args:
        level: Logging level (default: INFO)
        asyncssh_level: AsyncSSH logging level as string (e.g., "WARNING", "ERROR")
                       If None, uses the same level as general logging
    """
    # Create logs directory if it doesn't exist
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    log_file_path = os.path.join(LOG_DIR, LOG_FILE)
    
    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    
    # Create rotating file handler
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    
    # Create console handler for stdout
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    
    # Get root logger and configure it
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Add handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Configure asyncssh logging level separately
    if asyncssh_level:
        asyncssh_log_level = _get_log_level(asyncssh_level)
        logging.getLogger('asyncssh').setLevel(asyncssh_log_level)
        logging.getLogger('asyncssh.sftp').setLevel(asyncssh_log_level)
        logging.info(f"AsyncSSH logging level set to: {asyncssh_level}")
    
    # Log startup message
    logging.info(f"Logging initialized - file: {log_file_path}, max size: {MAX_LOG_SIZE // (1024*1024)}MB, backups: {BACKUP_COUNT}")
