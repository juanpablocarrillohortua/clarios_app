"""SQLModel tables for the Gold schema.

Layer: db

Responsibility:
    Maps the KPI tables that dashboards and the API read. Gold is a *published*
    layer: it is rewritten wholesale per period by the KPI pipeline rather than
    mutated row by row, which is why every row carries the ``run_id`` that
    produced it and the ``coverage`` fraction behind it. Nothing writes here
    except :mod:`energy.pipelines.tasks.compute_kpis`, and nothing reads Silver
    to answer a dashboard query. The DDL, including the hypertable and the
    continuous aggregates that roll hourly values up to daily, lives in
    ``migrations/versions/0004`` and ``0005``.

Depends on:
    ``sqlmodel``, ``energy.core.constants``.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

SCHEMA = "gold"


class KPIHourly:
    """Maps ``gold.kpi_hourly`` - KPI values at hourly granularity.

    Hypertable partitioned on ``period_start``.

    Columns:
        kpi_hourly_id: UUID, part of the composite primary key.
        period_start: interval start on the canonical grid, UTC. Partitioning
            column.
        plant_id: foreign key to ``reference.plants``.
        kpi_key: which KPI, matching a key in ``config/kpis.yaml``.
        value: the computed value.
        unit: the unit it is expressed in.
        coverage: fraction of the period backed by reliable readings, ``[0, 1]``.
        computed_at: when the value was produced, UTC.
        run_id: the run that produced it.

    Unique on ``(plant_id, kpi_key, period_start)``.
    """

    __tablename__ = "kpi_hourly"


class KPIDaily:
    """Maps ``gold.kpi_daily`` - KPI values at daily granularity.

    Daily values are *not* the mean of the hourly ones: they are recomputed from
    Silver with production-weighted aggregation, because averaging a ratio over
    hours with unequal output gives the wrong answer. See
    :func:`energy.domain.kpi_formulas.weighted_average`.

    Columns:
        kpi_daily_id: UUID, part of the composite primary key.
        period_start: midnight UTC of the day.
        plant_id: foreign key to ``reference.plants``.
        kpi_key: which KPI.
        value: the computed value.
        unit: the unit it is expressed in.
        coverage: fraction of the day backed by reliable readings.
        target_value: the target in force for this day, denormalised so a
            variance can be rendered without a temporal join.
        computed_at: when the value was produced, UTC.
        run_id: the run that produced it.

    Unique on ``(plant_id, kpi_key, period_start)``.
    """

    __tablename__ = "kpi_daily"


class PlantDailySummary:
    """Maps ``gold.plant_daily_summary`` - one denormalised row per plant per day.

    The table the dashboard landing page reads. Deliberately wide so a summary
    view is one indexed lookup rather than a pivot over the long KPI tables.

    Columns:
        summary_id: UUID, part of the composite primary key.
        day: the day, midnight UTC.
        plant_id: foreign key to ``reference.plants``.
        energy_kwh: total electricity consumed.
        gas_kwh: total gas energy consumed.
        production_equivalent_units: normalised output.
        sec_kwh_per_unit: specific energy consumption; null when production was
            zero, which is distinct from zero.
        load_factor: daily load factor.
        peak_demand_kw: highest demand observed.
        baseline_kwh: what the energy baseline predicted for the day.
        deviation_pct: signed deviation from baseline; positive means worse.
        coverage: fraction of the day backed by reliable readings.
        computed_at: when the row was produced, UTC.
        run_id: the run that produced it.
    """

    __tablename__ = "plant_daily_summary"


__all__ = ["SCHEMA", "KPIDaily", "KPIHourly", "PlantDailySummary"]
