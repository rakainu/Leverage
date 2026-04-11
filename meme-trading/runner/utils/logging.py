"""Structured JSON logging to stdout via structlog."""
import logging
import sys

import structlog

_configured = False


def _configure_structlog(log_level: int) -> None:
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


def configure_logging(level: str = "INFO") -> None:
    """Set up structlog + stdlib logging to emit JSON lines to stdout.

    Safe to call multiple times — idempotent.
    """
    global _configured
    log_level = getattr(logging, level.upper(), logging.INFO)

    if _configured:
        # Allow level updates on re-configure without duplicating handlers.
        logging.getLogger().setLevel(log_level)
        _configure_structlog(log_level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    root = logging.getLogger()
    # Replace existing handlers to keep output clean during tests.
    root.handlers = [handler]
    root.setLevel(log_level)

    _configure_structlog(log_level)

    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog bound logger with the given name.

    The name is bound into the event dict as ``logger`` so it appears in
    every JSON line this logger emits.
    """
    return structlog.get_logger().bind(logger=name)
