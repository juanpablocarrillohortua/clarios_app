"""Tests for production normalisation into equivalent units.

Layer: tests (unit)

Covers :mod:`energy.domain.equivalent_units`. The emphasis is on the temporal
versioning of factors, because that is what keeps a newly agreed factor from
retroactively rewriting last year's reported efficiency.

Each test is skipped until Phase 2.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

PHASE = "Phase 2: equivalent-unit normalisation is not implemented yet"


@pytest.mark.skip(reason=PHASE)
def test_reference_product_has_factor_one() -> None:
    """The reference product converts one-for-one."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_mix_is_weighted_by_factor() -> None:
    """A product mix sums to the factor-weighted total.

    100 units at 1.0 plus 100 units at 1.45 gives 245 equivalent units.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_unknown_product_raises() -> None:
    """A product with no configured factor raises ConfigurationError.

    Skipping it silently would understate production and therefore overstate
    SEC - a quiet error that reads as a genuine efficiency regression.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_factor_resolution_is_temporal() -> None:
    """The factor in force at the period start is the one applied.

    With AGM at 1.45 until 2025-07 and 1.38 afterwards, a June 2025 period uses
    1.45 and an August 2025 period uses 1.38.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_mid_period_factor_change_does_not_rewrite_history() -> None:
    """Resolving at the period start keeps an earlier period's result stable.

    Recomputing June 2025 after the July factor change must reproduce the
    originally reported number exactly.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_overlapping_validity_windows_raise() -> None:
    """Two factor versions valid at the same instant raise ConfigurationError."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_gap_between_versions_raises() -> None:
    """A gap between consecutive factor versions raises ConfigurationError.

    A period falling in the gap would otherwise silently drop that product from
    the production total.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_non_positive_factor_raises() -> None:
    """A zero or negative equivalence factor raises ConfigurationError."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_committed_catalogue_is_valid() -> None:
    """The catalogue committed at config/equivalent_units.yaml passes validation.

    Runs `validate_catalogue` against the real file, so a bad edit fails in CI
    rather than producing quietly wrong KPIs weeks later.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_mixed_basis_products_are_not_summed_blindly() -> None:
    """Products measured per tonne and per unit are not added without conversion.

    LEAD-INGOT has basis `tonne` while the battery products have basis `unit`;
    summing them directly is meaningless and must raise.
    """
    raise AssertionError("not implemented")
