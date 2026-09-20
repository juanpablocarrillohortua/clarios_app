"""Pure business logic with zero I/O.

Layer: domain

This package is the innermost ring of the hexagon. It knows what a plant, a
meter, a reading and a KPI *are*, and how they relate, but it has no idea where
they are stored or how they arrive. It must never import a database driver, an
HTTP client, a dataframe backend, or any other ``energy`` package except itself.

Everything here is deterministic and unit-testable without fixtures, containers,
or network access.
"""

from __future__ import annotations

from energy.domain.exceptions import (
    ConfigurationError,
    DataQualityError,
    DomainRuleError,
    EnergyError,
    IngestionError,
    PipelineError,
    RepositoryError,
)

__all__ = [
    "ConfigurationError",
    "DataQualityError",
    "DomainRuleError",
    "EnergyError",
    "IngestionError",
    "PipelineError",
    "RepositoryError",
]
