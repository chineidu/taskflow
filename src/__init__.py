import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.absolute()


def create_logger(
    name: str = "logger",
    log_level: int = logging.INFO,
    log_file: str | None = None,
) -> logging.Logger:
    """
    Create a configured logger with custom date formatting.

    Parameters:
    -----------
    name : str, optional
        Name of the logger, by default 'logger'
    log_level : int, optional
        Logging level, by default logging.INFO
    log_file : str, optional
        Path to log file. If None, logs to console, by default None

    Returns:
    --------
    logging.Logger
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Clear any existing handlers
    logger.handlers.clear()

    # Create formatter with YYYY-MM-DD HH:MM:SS format
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def add_file_handler(logger: logging.Logger, log_file: str | Path) -> logging.FileHandler:
    """Dynamically adds a file handler using the standard app format.
    
    Parameters
    ----------
    logger : logging.Logger
        The logger instance to which the file handler will be added.
    log_file : str | Path
        The path to the log file.

    Returns
    -------
    logging.FileHandler
        The added file handler instance.
    """
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return file_handler


__all__ = ["ROOT"]
