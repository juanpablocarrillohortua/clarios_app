"""Cross-cutting concerns: configuration, logging, and shared constants.

Layer: core

This package sits underneath every other layer and must never import from them.
It owns process-wide configuration (``settings``), the structured logging setup
(``logging``), and the literal values that the whole platform agrees on
(``constants``).
"""

from __future__ import annotations

from energy.core.constants import (
    BRONZE_SCHEMA,
    CANONICAL_GRID_FREQUENCY,
    GOLD_SCHEMA,
    REFERENCE_SCHEMA,
    SILVER_SCHEMA,
)
from energy.core.logging import configure_logging, get_logger
from energy.core.settings import Settings, get_settings

__all__ = [
    "BRONZE_SCHEMA",
    "CANONICAL_GRID_FREQUENCY",
    "GOLD_SCHEMA",
    "REFERENCE_SCHEMA",
    "SILVER_SCHEMA",
    "Settings",
    "configure_logging",
    "get_logger",
    "get_settings",
]
