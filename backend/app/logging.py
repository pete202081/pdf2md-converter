"""Structured JSON logging setup (structlog).

Every log line is a JSON object with a UTC timestamp, log level and any bound
context (e.g. ``correlation_id``, ``job_id``), which makes the output trivial
to ship to Docker's log driver and any log aggregator.
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib + structlog to emit JSON logs to stdout."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Return a bound structured logger."""
    return structlog.get_logger(name) if name else structlog.get_logger()
