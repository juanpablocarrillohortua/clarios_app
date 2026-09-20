"""Tests for the pure KPI formulas.

Layer: tests (unit)

Covers :mod:`energy.domain.kpi_formulas`. These are pure functions over plain
values, so every case here runs without a database, a container, or a fixture -
which is exactly why the formulas live in the domain layer.

Each test is skipped until Phase 2 implements the formula it covers. The
docstring states the property being asserted, so the test can be written from it
directly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

PHASE = "Phase 2: KPI formulas are not implemented yet"


@pytest.mark.skip(reason=PHASE)
def test_sec_divides_energy_by_production() -> None:
    """SEC equals energy divided by equivalent units produced.

    Given 1000 kWh and 250 equivalent units, SEC is 4.0 kWh/unit.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_sec_rejects_zero_production() -> None:
    """SEC raises rather than returning infinity when production is zero.

    An idle plant has no efficiency; reporting infinity would poison every
    downstream average and render a dashboard unreadable.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_sec_rejects_non_kwh_energy() -> None:
    """SEC raises UnitMismatchError when energy is not expressed in kWh."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_load_factor_is_bounded_by_one() -> None:
    """Load factor never exceeds 1 for internally consistent inputs.

    With energy = peak_demand * window_hours the plant drew its peak
    continuously, which is the definition of a load factor of exactly 1.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_load_factor_and_peak_ratio_are_reciprocal() -> None:
    """Load factor and peak ratio are reciprocals of one another.

    Property test: for any positive peak and average demand,
    load_factor * peak_ratio == 1 within floating-point tolerance. Use Hypothesis
    to generate the pairs rather than hand-picking them.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_baseline_deviation_sign_convention() -> None:
    """Positive deviation means consuming MORE than the baseline predicted.

    The single most consequential sign convention in the platform: reversing it
    turns every reported overconsumption into a reported saving.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_avoided_consumption_is_baseline_minus_actual() -> None:
    """Avoided consumption is positive when actual is below baseline."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_weighted_average_differs_from_plain_mean() -> None:
    """Weighted averaging of a ratio is not the mean of the ratios.

    Given two hours with very different production, the production-weighted SEC
    must differ from the arithmetic mean of the two hourly SEC values. This is
    the mistake `weighted_average` exists to prevent, so it is asserted directly.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_weighted_average_rejects_mismatched_lengths() -> None:
    """Weighted average raises when values and weights differ in length."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_formulas_use_decimal_not_float() -> None:
    """Totals retain Decimal precision across a large number of additions.

    Summing a million readings as floats drifts enough to matter on a billing
    reconciliation; Decimal is the reason the domain does not use float.
    """
    raise AssertionError("not implemented")
