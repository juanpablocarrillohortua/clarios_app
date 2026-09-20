"""Production normalisation into comparable equivalent units.

Layer: domain

Responsibility:
    Converts a heterogeneous product mix into a single comparable denominator so
    that Specific Energy Consumption means the same thing across plants, months,
    and product lines. A plant that switches from an easy product to a hard one
    will show worse raw kWh-per-tonne while being no less efficient; equivalence
    factors remove that distortion. This module owns the arithmetic only - the
    factors themselves live in ``config/equivalent_units.yaml`` and are loaded
    and passed in by the caller, so the domain never reads a file.

Depends on:
    ``energy.domain.exceptions``, standard library.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EquivalenceFactor:
    """The energy-equivalence weight of one product relative to the reference.

    Attributes:
        product_code: the product this factor applies to.
        factor: multiplier converting physical output into equivalent units. The
            reference product has a factor of exactly 1. A product that takes
            twice the energy per tonne has a factor of 2.
        valid_from: first instant this factor applies, timezone-aware UTC.
        valid_to: first instant it stops applying, exclusive; ``None`` means
            open-ended. Factors are versioned because reformulations and process
            changes make them drift, and historical KPIs must not be silently
            rewritten when a new factor is agreed.
        basis: what the factor is expressed per, for example ``"tonne"``.
    """

    product_code: str
    factor: Decimal
    valid_from: datetime
    valid_to: datetime | None
    basis: str

    def is_valid_at(self, moment: datetime) -> bool:
        """Return whether this factor applies at a given instant.

        Args:
            moment: timezone-aware UTC datetime to test.

        Returns:
            ``True`` when ``valid_from <= moment < valid_to``.

        Raises:
            NaiveDatetimeError: if ``moment`` is timezone-naive.
        """
        raise NotImplementedError("Phase 2: factor validity window")


def to_equivalent_units(
    production: Mapping[str, Decimal],
    factors: Mapping[str, EquivalenceFactor],
) -> Decimal:
    """Convert a product mix into a single equivalent-unit total.

    Formula:
        ``equivalent_units = sum(quantity[p] * factor[p] for p in production)``

    Args:
        production: physical output per product code over the period, in each
            product's own basis unit.
        factors: the equivalence factor to apply per product code. Must cover
            every key present in ``production``.

    Returns:
        The total production expressed in equivalent units, suitable as the
        denominator of :func:`energy.domain.kpi_formulas.specific_energy_consumption`.

    Raises:
        ConfigurationError: if ``production`` contains a product code with no
            corresponding factor. Silently skipping it would understate output
            and overstate SEC, so this must fail loudly.
    """
    raise NotImplementedError("Phase 2: product mix normalisation")


def resolve_factors(
    catalogue: Mapping[str, tuple[EquivalenceFactor, ...]],
    moment: datetime,
) -> dict[str, EquivalenceFactor]:
    """Select the factor version applicable to each product at a given instant.

    Args:
        catalogue: all known factor versions per product code, as loaded from
            ``config/equivalent_units.yaml``.
        moment: the instant to resolve for, timezone-aware UTC. Use the *start*
            of the period being computed so a mid-period factor change does not
            retroactively alter the first half.

    Returns:
        Exactly one factor per product code that has a valid version at
        ``moment``. Products with no valid version are omitted.

    Raises:
        ConfigurationError: if a product has more than one factor valid at
            ``moment``, which means the configured validity windows overlap.
    """
    raise NotImplementedError("Phase 2: temporal factor resolution")


def validate_catalogue(catalogue: Mapping[str, tuple[EquivalenceFactor, ...]]) -> None:
    """Check a loaded factor catalogue for internal consistency.

    Run at startup and in CI against ``config/equivalent_units.yaml`` so a bad
    edit fails fast rather than producing quietly wrong KPIs weeks later.

    Args:
        catalogue: all factor versions per product code.

    Raises:
        ConfigurationError: if any product has overlapping validity windows, a
            gap between consecutive versions, a non-positive factor, or if no
            product is designated as the reference with a factor of 1.
    """
    raise NotImplementedError("Phase 2: factor catalogue validation")


__all__ = [
    "EquivalenceFactor",
    "resolve_factors",
    "to_equivalent_units",
    "validate_catalogue",
]
