"""Load the reference dimension tables from configuration.

Layer: scripts (operational tooling)

Responsibility:
    Populates ``reference.plants``, ``reference.meters``, ``reference.shifts``
    and ``reference.targets`` from ``config/sources.yaml`` and the seed
    definitions beside it. These tables are human-maintained dimension data, not
    pipeline output, so they are seeded explicitly rather than arriving through
    ingestion. The script is idempotent: it upserts on the natural keys
    (``plants.code``, ``meters.(plant_id, tag)``) so re-running after adding one
    plant does not duplicate the other six, and never deletes a row that
    configuration no longer mentions - a meter removed from a YAML file has
    historical readings pointing at it.

Depends on:
    ``energy.db.session``, ``energy.db.models.reference``, ``energy.core``.

Implemented in:
    Phase 2 - Domain model and KPI engine

Usage:
    python scripts/seed_reference_data.py [--config-dir config] [--dry-run]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def seed_plants(session: Session, config_dir: Path, dry_run: bool = False) -> int:
    """Upsert the plant dimension from configuration.

    Args:
        session: an open database session.
        config_dir: directory holding ``sources.yaml``.
        dry_run: report what would change without writing.

    Returns:
        The number of plants inserted or updated.

    Raises:
        ConfigurationError: if a plant declares an unrecognised IANA timezone.
    """
    raise NotImplementedError("Phase 2: plant dimension seeding")


def seed_meters(session: Session, config_dir: Path, dry_run: bool = False) -> int:
    """Upsert the meter dimension from configuration.

    Args:
        session: an open database session.
        config_dir: directory holding the source definitions.
        dry_run: report what would change without writing.

    Returns:
        The number of meters inserted or updated.

    Raises:
        EntityNotFoundError: if a meter references a plant code that does not
            exist, which usually means the plant seed step was skipped.
        ConfigurationError: if a submeter names a parent that would form a cycle.
    """
    raise NotImplementedError("Phase 2: meter dimension seeding")


def seed_shifts(session: Session, config_dir: Path, dry_run: bool = False) -> int:
    """Upsert shift patterns from configuration.

    Args:
        session: an open database session.
        config_dir: directory holding the shift definitions.
        dry_run: report what would change without writing.

    Returns:
        The number of shift patterns inserted or updated.

    Raises:
        ConfigurationError: if two patterns for one plant overlap in validity and
            cover the same weekday and hours.
    """
    raise NotImplementedError("Phase 2: shift pattern seeding")


def seed_targets(session: Session, config_dir: Path, dry_run: bool = False) -> int:
    """Upsert KPI targets from configuration.

    Args:
        session: an open database session.
        config_dir: directory holding the target definitions.
        dry_run: report what would change without writing.

    Returns:
        The number of targets inserted or updated.

    Raises:
        UnknownKPIError: if a target names a KPI absent from ``kpis.yaml``.
        ConfigurationError: if two targets for one plant and KPI have
            overlapping validity windows.
    """
    raise NotImplementedError("Phase 2: KPI target seeding")


def main() -> int:
    """Run every seeding step inside one transaction.

    All-or-nothing: a partially seeded reference layer is worse than an empty
    one, because ingestion would run and silently attribute readings to the
    wrong plants.

    Returns:
        Process exit code: 0 on success, 1 on a configuration error.
    """
    raise NotImplementedError("Phase 2: seeding entry point")


if __name__ == "__main__":
    raise SystemExit(main())
