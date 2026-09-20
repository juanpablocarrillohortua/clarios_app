"""SQLModel tables for the shared reference schema.

Layer: db

Responsibility:
    Maps the dimension tables every medallion layer joins against: plants,
    meters, shifts, and targets. These are slowly changing and human-maintained,
    seeded by ``scripts/seed_reference_data.py`` rather than by a pipeline.
    ``targets`` is temporally versioned because a KPI target agreed for 2026 must
    not retroactively change how 2025 was judged. The DDL for these tables lives
    in ``migrations/versions/0002_create_bronze_schema.py``, which provisions the
    ``reference`` schema first because Silver and Gold reference it.

Depends on:
    ``sqlmodel``, ``energy.core.constants``.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

SCHEMA = "reference"


class Plant:
    """Maps ``reference.plants``.

    Columns:
        plant_id: UUID primary key.
        code: short unique plant code, for example ``"MTY-01"``.
        name: full plant name.
        timezone: IANA timezone name, used for shift alignment and reporting.
        country: ISO 3166-1 alpha-2 code.
        floor_area_m2: conditioned area, the denominator of energy intensity.
        commissioned_on: date the plant entered service.
        is_active: whether the plant is currently in scope for ingestion.
        created_at: row creation timestamp, UTC.
        updated_at: last modification timestamp, UTC.
    """

    __tablename__ = "plants"

    def as_domain(self) -> object:
        """Convert this row into its domain entity.

        Returns:
            An :class:`~energy.domain.entities.Plant`.

        Raises:
            DomainRuleError: if the stored row violates a domain invariant, which
                means the reference data was seeded incorrectly.
        """
        raise NotImplementedError("Phase 2: plant row to domain entity")


class Meter:
    """Maps ``reference.meters``.

    Columns:
        meter_id: UUID primary key.
        plant_id: foreign key to ``reference.plants``.
        tag: source-system tag, unique within a plant.
        commodity: electricity, gas, or steam.
        unit: canonical unit of the readings.
        multiplier: factor from raw register value to canonical unit.
        is_virtual: whether readings are derived rather than measured.
        parent_meter_id: self-referencing key for submetering hierarchies.
        installed_on: date the meter was installed.
        removed_on: date it was removed; null while in service.
    """

    __tablename__ = "meters"

    def as_domain(self) -> object:
        """Convert this row into its domain entity.

        Returns:
            An :class:`~energy.domain.entities.Meter`.
        """
        raise NotImplementedError("Phase 2: meter row to domain entity")


class Shift:
    """Maps ``reference.shifts``.

    Columns:
        shift_id: UUID primary key.
        plant_id: foreign key to ``reference.plants``.
        name: human-facing shift name.
        starts_at: local start time, inclusive.
        ends_at: local end time, exclusive; may precede ``starts_at`` when the
            shift crosses midnight.
        weekdays: ISO weekday numbers this shift is worked on.
        valid_from: first date this pattern applies.
        valid_to: first date it stops applying; null means open-ended.
    """

    __tablename__ = "shifts"


class Target:
    """Maps ``reference.targets``.

    Temporally versioned: a target is bound to the period it governs, so
    historical performance is always judged against the target that was in force
    at the time rather than the current one.

    Columns:
        target_id: UUID primary key.
        plant_id: foreign key to ``reference.plants``.
        kpi_key: which KPI this target governs.
        value: the target value.
        unit: the unit the target is expressed in.
        valid_from: first instant the target applies, UTC.
        valid_to: first instant it stops applying, UTC; null means open-ended.
        direction: whether lower or higher is better, which decides the sign of
            the variance reported against it.
    """

    __tablename__ = "targets"


__all__ = ["SCHEMA", "Meter", "Plant", "Shift", "Target"]
