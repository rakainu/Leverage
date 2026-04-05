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

    # Console handler (force UTF-8 on Windows to handle emoji in Solana logs)
    console = logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
    )
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler (optional, created if data/ exists)
    log_dir = Path("data")
    if log_dir.exists():
        file_handler = logging.FileHandler(log_dir / "smc.log")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    # Parser debug logging for diagnosing buy detection
    logging.getLogger("smc.scanner.parser").setLevel(logging.DEBUG)

    return logger
