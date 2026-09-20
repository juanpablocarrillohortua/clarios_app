"""SQLModel tables for the Silver schema.

Layer: db

Responsibility:
    Maps the validated, deduplicated, grid-aligned readings and the quarantine
    ledger that sits beside them. The pair is deliberate: every row that does not
    reach ``readings`` lands in ``quarantine`` with the rule it broke, so the
    count of rows entering Silver always equals the count landing plus the count
    quarantined. That invariant is what makes data loss detectable. The DDL,
    including the hypertable and the partial unique index that enforces
    ``(meter_id, ts_utc)`` uniqueness among non-superseded rows, lives in
    ``migrations/versions/0003`` and ``0005``.

Depends on:
    ``sqlmodel``, ``energy.core.constants``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

SCHEMA = "silver"


class Reading:
    """Maps ``silver.readings`` - validated readings on the canonical hourly grid.

    Hypertable partitioned on ``ts_utc``.

    Columns:
        reading_id: UUID, part of the composite primary key.
        ts_utc: interval start on the canonical grid, UTC. Partitioning column
            and part of every unique constraint.
        meter_id: foreign key to ``reference.meters``.
        plant_id: foreign key to ``reference.plants``, denormalised so the common
            plant-and-period query needs no join.
        value: the measured quantity in the meter's canonical unit.
        unit: the canonical unit.
        quality: one of :data:`energy.core.constants.QUALITY_FLAGS`.
        source_system: which source supplied the winning value.
        source_priority: the priority that source held, stored so a later
            higher-priority arrival can supersede this row without re-reading
            the configuration.
        ingestion_run_id: the run that wrote it.
        superseded_at: null for the live row; set when a correction replaces it.
            Superseding rather than deleting is what preserves the audit trail.
        created_at: when the row was written, UTC.
    """

    __tablename__ = "readings"


class Quarantine:
    """Maps ``silver.quarantine`` - rows rejected on the way into Silver.

    Columns:
        quarantine_id: UUID primary key.
        run_id: the run that rejected the row.
        bronze_row_id: reference back to the offending Bronze row.
        plant_code: the plant the row belonged to.
        source_system: the source it came from.
        rule: name of the validation rule that failed.
        reason: human-readable explanation, in English.
        raw_payload: JSONB with the original values, so a steward can see exactly
            what arrived.
        quarantined_at: when the row was rejected, UTC.
        resolved_at: when a steward dealt with it; null while outstanding.
        resolution: what was done about it.
    """

    __tablename__ = "quarantine"


class ConsolidationAudit:
    """Maps ``silver.consolidation_audit`` - how multi-source conflicts were resolved.

    Written whenever two or more sources supply a value for the same meter and
    interval. Without this, a user who sees a number different from their own
    spreadsheet has no way to discover that a higher-priority source overrode it.

    Columns:
        audit_id: UUID primary key.
        ts_utc: the contested interval, UTC.
        meter_id: the contested meter.
        winning_source: the source whose value was kept.
        losing_sources: array of sources that were overridden.
        winning_value: the value that was kept.
        losing_values: array of the values that were discarded.
        divergence_pct: relative spread between the candidate values. A large
            divergence is a data-quality alert, not a resolved conflict.
        run_id: the run that performed the consolidation.
        created_at: when the decision was made, UTC.
    """

    __tablename__ = "consolidation_audit"


__all__ = ["SCHEMA", "ConsolidationAudit", "Quarantine", "Reading"]
