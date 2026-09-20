"""Generate realistic synthetic readings for local development and demos.

Layer: scripts (operational tooling)

Responsibility:
    Produces a plausible Bronze-and-Silver dataset so the dashboards, KPIs and
    analytics can be exercised without access to real plant data. The point is
    realism, not volume: the generated series carry the structure the platform is
    built to handle - daily and weekly shift cycles, an annual weather response,
    production-correlated consumption, and deliberate defects (gaps, duplicates,
    unit errors, a flatlined sensor, a step change) so that validation,
    quarantine, consolidation and anomaly detection all have something real to
    act on. Data generated here is always tagged ``source_system='synthetic'``
    so it can never be mistaken for measured data.

Depends on:
    ``energy.db.session``, ``energy.core``, ``polars``, ``numpy``.

Implemented in:
    Phase 2 - Domain model and KPI engine

Usage:
    python scripts/generate_synthetic_data.py --days 90 --seed 42
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    import polars as pl
    from sqlalchemy.orm import Session


def generate_consumption(
    plant_code: str,
    start: datetime,
    end: datetime,
    seed: int = 42,
) -> pl.DataFrame:
    """Generate an hourly electricity series with realistic structure.

    The series is composed as base load + shift pattern + weekday effect +
    weather response + production correlation + noise, so that a baseline
    regression fitted against it recovers meaningful coefficients rather than
    fitting pure noise.

    Args:
        plant_code: the plant to generate for; used to vary scale and pattern.
        start: first interval, timezone-aware UTC and grid-aligned.
        end: bound, exclusive.
        seed: random seed, so a demo is reproducible.

    Returns:
        One row per meter and hour with a ``value`` in kWh.

    Raises:
        InvalidTimeWindowError: if the bounds are unaligned or inverted.
    """
    raise NotImplementedError("Phase 2: synthetic consumption generation")


def generate_production(
    plant_code: str,
    start: datetime,
    end: datetime,
    seed: int = 42,
) -> pl.DataFrame:
    """Generate a daily production series with a shifting product mix.

    The mix shifts deliberately over time so that equivalent-unit normalisation
    visibly changes the resulting SEC - without it, the normalisation step has
    nothing to demonstrate.

    Args:
        plant_code: the plant to generate for.
        start: first day, UTC.
        end: bound, exclusive.
        seed: random seed.

    Returns:
        One row per day and product code with a quantity.
    """
    raise NotImplementedError("Phase 2: synthetic production generation")


def inject_defects(frame: pl.DataFrame, seed: int = 42) -> pl.DataFrame:
    """Introduce the data problems the platform is designed to catch.

    Injects, at low rates: missing intervals, duplicated readings with differing
    values, a unit error (a block reported in Wh rather than kWh), negative
    values from a register reset, a flatlined sensor, and a sustained level
    shift. Each defect is chosen to exercise a specific mechanism - gap
    detection, deduplication, range validation, quarantine, flatline detection,
    and changepoint detection respectively.

    Args:
        frame: the clean generated series.
        seed: random seed.

    Returns:
        The series with defects injected and a ``defect_kind`` column recording
        what was done to each affected row, so tests can assert the platform
        caught exactly what was planted.
    """
    raise NotImplementedError("Phase 2: defect injection")


def load_to_database(session: Session, frames: dict[str, pl.DataFrame]) -> dict[str, int]:
    """Write generated data into Bronze, tagged as synthetic.

    Args:
        session: an open database session.
        frames: generated frames keyed by target table.

    Returns:
        Row counts written per table.

    Raises:
        RepositoryError: if the write fails.
    """
    raise NotImplementedError("Phase 2: synthetic data loading")


def main() -> int:
    """Generate and load a synthetic dataset.

    Returns:
        Process exit code: 0 on success, 1 on failure.
    """
    raise NotImplementedError("Phase 2: synthetic data entry point")


if __name__ == "__main__":
    raise SystemExit(main())
