"""Structured logging configuration for SMC."""

import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return the root SMC logger."""
    logger = logging.getLogger("smc")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler (optional, created if data/ exists)
    log_dir = Path("data")
    if log_dir.exists():
        file_handler = logging.FileHandler(log_dir / "smc.log")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger
