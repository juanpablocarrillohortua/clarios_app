"""Pure KPI formulas: specific energy consumption, load factor, peak ratio.

Layer: domain

Responsibility:
    Owns the arithmetic definition of every KPI the platform reports, expressed
    as pure functions over plain values. Each function is total and deterministic:
    given the same inputs it returns the same output, with no database access, no
    configuration lookup, and no logging. The formulas themselves are *declared*
    in ``config/kpis.yaml``; this module *implements* them, and
    :mod:`energy.pipelines.tasks.compute_kpis` is what applies them across a
    dataframe to populate the Gold layer. Keeping them here means they can be
    property-tested with Hypothesis in isolation.

Depends on:
    ``energy.domain.exceptions``, ``energy.domain.value_objects``,
    ``energy.core.constants``.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from energy.domain.value_objects import EnergyAmount, TimeWindow


def specific_energy_consumption(
    energy: EnergyAmount,
    production: Decimal,
) -> Decimal:
    """Compute Specific Energy Consumption (SEC): energy per unit produced.

    SEC is the platform's headline efficiency KPI. It is only comparable across
    plants and periods when ``production`` has already been normalised into
    equivalent units by :mod:`energy.domain.equivalent_units`; passing raw
    physical output of different product mixes produces a meaningless number.

    Formula:
        ``SEC = energy_kwh / equivalent_units_produced``

    Args:
        energy: energy consumed over the period, in kWh.
        production: equivalent units produced over the same period. Must be
            strictly positive.

    Returns:
        kWh consumed per equivalent unit produced.

    Raises:
        UnitMismatchError: if ``energy`` is not expressed in kWh.
        ZeroDivisionError: if ``production`` is zero. Callers computing SEC over
            a period with no production must skip the KPI rather than report
            infinity - an idle plant has no efficiency, not an infinite one.
    """
    raise NotImplementedError("Phase 2: SEC formula")


def load_factor(
    consumed: EnergyAmount,
    peak_demand: Decimal,
    window: TimeWindow,
) -> Decimal:
    """Compute the load factor: how evenly demand was spread over the period.

    A load factor near 1 means the plant drew close to its peak continuously; a
    low value means short expensive spikes above a much lower average, which is
    what demand-charge reduction work targets.

    Formula:
        ``load_factor = energy_kwh / (peak_demand_kw * window_hours)``

    Args:
        consumed: total energy drawn over the window, in kWh.
        peak_demand: the highest demand observed in the window, in kW. Must be
            strictly positive.
        window: the period the measurement covers, used for its length in hours.

    Returns:
        A dimensionless ratio in ``(0, 1]``. A value above 1 indicates
        inconsistent inputs (peak demand measured on a coarser interval than the
        energy) and callers should treat it as a data-quality signal.

    Raises:
        UnitMismatchError: if ``consumed`` is not expressed in kWh.
        ZeroDivisionError: if ``peak_demand`` is zero or the window has no length.
    """
    raise NotImplementedError("Phase 2: load factor formula")


def peak_ratio(
    peak_demand: Decimal,
    average_demand: Decimal,
) -> Decimal:
    """Compute the peak-to-average demand ratio.

    The reciprocal view of :func:`load_factor`, kept separate because operations
    teams read it directly ("we peak at three times our average").

    Formula:
        ``peak_ratio = peak_demand_kw / average_demand_kw``

    Args:
        peak_demand: highest demand in the period, in kW.
        average_demand: mean demand over the same period, in kW. Must be strictly
            positive.

    Returns:
        A dimensionless ratio greater than or equal to 1.

    Raises:
        ZeroDivisionError: if ``average_demand`` is zero.
    """
    raise NotImplementedError("Phase 2: peak ratio formula")


def energy_intensity(
    energy: EnergyAmount,
    floor_area_m2: Decimal,
) -> Decimal:
    """Compute energy intensity per unit of floor area.

    Used for site-level comparison where production is not the right denominator
    (offices, warehouses, utilities buildings).

    Formula:
        ``intensity = energy_kwh / floor_area_m2``

    Args:
        energy: energy consumed over the period, in kWh.
        floor_area_m2: conditioned floor area, in square metres. Strictly positive.

    Returns:
        kWh per square metre.

    Raises:
        UnitMismatchError: if ``energy`` is not expressed in kWh.
        ZeroDivisionError: if ``floor_area_m2`` is zero.
    """
    raise NotImplementedError("Phase 2: energy intensity formula")


def baseline_deviation(
    actual: Decimal,
    expected: Decimal,
) -> Decimal:
    """Compute the relative deviation of actual consumption from its baseline.

    The sign convention matters and is fixed here: a **positive** value means the
    plant consumed *more* than the baseline predicted, which is worse. Reversing
    this anywhere downstream inverts every savings report.

    Formula:
        ``deviation = (actual - expected) / expected``

    Args:
        actual: measured consumption for the period, in kWh.
        expected: consumption predicted by the energy baseline regression from
            :mod:`energy.analytics.baseline`, in kWh. Strictly positive.

    Returns:
        A signed ratio; multiply by 100 for a percentage.

    Raises:
        ZeroDivisionError: if ``expected`` is zero.
    """
    raise NotImplementedError("Phase 2: baseline deviation formula")


def avoided_consumption(
    baseline: EnergyAmount,
    actual: EnergyAmount,
) -> EnergyAmount:
    """Compute energy avoided against a baseline, the IPMVP savings quantity.

    Args:
        baseline: what the baseline model predicts for the period, in kWh.
        actual: what was actually consumed, in kWh.

    Returns:
        ``baseline - actual``. Positive means savings were realised; negative
        means consumption exceeded the baseline.

    Raises:
        UnitMismatchError: if the two amounts are not in the same unit.
    """
    raise NotImplementedError("Phase 2: avoided consumption formula")


def weighted_average(
    values: Sequence[Decimal],
    weights: Sequence[Decimal],
) -> Decimal:
    """Compute a weighted average, the building block for period roll-ups.

    Rolling an hourly SEC up to a daily SEC is *not* the mean of the hourly
    values: it must be weighted by production, or hours with negligible output
    dominate the result. This helper exists so that mistake is made in one place
    and fixed in one place.

    Args:
        values: the values to average.
        weights: the weight of each value. Same length as ``values``, non-negative,
            and not all zero.

    Returns:
        The weighted mean.

    Raises:
        ValueError: if the sequences differ in length or are empty.
        ZeroDivisionError: if every weight is zero.
    """
    raise NotImplementedError("Phase 2: weighted average helper")


__all__ = [
    "avoided_consumption",
    "baseline_deviation",
    "energy_intensity",
    "load_factor",
    "peak_ratio",
    "specific_energy_consumption",
    "weighted_average",
]
