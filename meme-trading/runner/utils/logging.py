"""Structured JSON logging to stdout via structlog."""
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Set up structlog to emit JSON lines to stdout.

    Safe to call multiple times — each call re-binds the filter level.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> "structlog.typing.FilteringBoundLogger":
    """Get a structlog bound logger with the `logger` field preset to `name`."""
    return structlog.get_logger().bind(logger=name)
