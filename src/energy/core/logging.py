"""Structured logging setup built on structlog.

Layer: core

Configures one logging pipeline for the whole process: console rendering for
local development, JSON for deployed environments. The standard library
``logging`` module is routed through the same pipeline so that third-party logs
(uvicorn, SQLAlchemy, Prefect) come out in the same shape. A ``request_id``
context variable is bound automatically by the API middleware so every log line
emitted while handling a request can be correlated.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, TextIO

import structlog

from energy.core.constants import REQUEST_ID_LOG_KEY

if TYPE_CHECKING:
    from structlog.typing import EventDict, Processor, WrappedLogger

# Loggers that are noisy at INFO and add nothing at that level.
_NOISY_LOGGERS: dict[str, int] = {
    "uvicorn.access": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "botocore": logging.WARNING,
    "urllib3": logging.WARNING,
    "asyncio": logging.WARNING,
}

_configured = False


def _add_log_level_upper(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Render the level as an uppercase string for log-aggregator friendliness."""
    level = event_dict.get("level")
    if isinstance(level, str):
        event_dict["level"] = level.upper()
    return event_dict


def _drop_empty_request_id(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Remove ``request_id`` when it is not bound, keeping non-request logs clean."""
    if event_dict.get(REQUEST_ID_LOG_KEY) is None:
        event_dict.pop(REQUEST_ID_LOG_KEY, None)
    return event_dict


def _build_processors(*, json_output: bool) -> list[Processor]:
    """Assemble the structlog processor chain.

    Args:
        json_output: render as JSON when ``True``, as coloured console text when
            ``False``.

    Returns:
        The ordered processor chain, ending in a renderer.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _add_log_level_upper,
        _drop_empty_request_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor
    if json_output:
        shared.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    shared.append(renderer)
    return shared


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    force: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Configure structlog and the standard library logging module.

    Safe to call more than once: subsequent calls are ignored unless ``force`` is
    set, so importing a module never silently reconfigures a running service.

    Args:
        level: minimum level to emit, as a standard library level name
            (``DEBUG`` ... ``CRITICAL``).
        json_output: emit JSON lines instead of coloured console output. Use
            ``True`` in every deployed environment.
        force: reconfigure even if logging was already configured. Used by tests.
        stream: where log lines go; standard output when ``None``. A command
            whose standard output is its result (a JSON report, a table to pipe)
            sends them to standard error instead.
    """
    global _configured  # noqa: PLW0603
    if _configured and not force:
        return

    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=stream or sys.stdout,
        level=numeric_level,
        force=True,
    )

    structlog.configure(
        processors=_build_processors(json_output=json_output),
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    for name, noisy_level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(max(noisy_level, numeric_level))

    _configured = True


def configure_from_settings(force: bool = False, stream: TextIO | None = None) -> None:
    """Configure logging from the process settings.

    Args:
        force: reconfigure even if logging was already configured.
        stream: where log lines go; standard output when ``None``.
    """
    from energy.core.settings import get_settings  # noqa: PLC0415

    settings = get_settings()
    configure_logging(
        level=settings.logging.level,
        json_output=settings.logging.format == "json",
        force=force,
        stream=stream,
    )


def get_logger(name: str | None = None, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: logger name; pass ``__name__`` from the calling module.
        **initial_values: key/value pairs bound to every line this logger emits,
            for example ``layer="silver"`` or ``plant_id="P-001"``.

    Returns:
        A bound logger. Logging before :func:`configure_logging` runs still works
        - structlog falls back to its defaults - but output will be unformatted.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger


def bind_request_id(request_id: str) -> None:
    """Bind a request identifier to the current context.

    Every log line emitted afterwards on this task or thread carries the id.

    Args:
        request_id: the value of the ``X-Request-ID`` header, or a generated UUID4.
    """
    structlog.contextvars.bind_contextvars(**{REQUEST_ID_LOG_KEY: request_id})


def clear_request_context() -> None:
    """Clear all context variables bound to the current context."""
    structlog.contextvars.clear_contextvars()


__all__ = [
    "bind_request_id",
    "clear_request_context",
    "configure_from_settings",
    "configure_logging",
    "get_logger",
]
