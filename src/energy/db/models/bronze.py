"""SQLModel tables for the Bronze schema.

Layer: db

Responsibility:
    Maps the append-only landing tables. Bronze rows are never updated and never
    deleted by application code; the only thing that removes them is the
    TimescaleDB retention policy. Every table here carries the provenance columns
    listed in :data:`energy.core.constants.PROVENANCE_COLUMNS`, and the raw
    record is kept whole in a JSONB ``payload`` column rather than being split
    into typed columns - a source that adds a field must not require a migration,
    and a value that fails to parse must still be stored so it can be diagnosed.
    The DDL, including hypertable and compression settings, lives in
    ``migrations/versions/0002`` and ``0005``. Revision 0002 also creates five
    landing tables for the equivalent-batteries exports (``raw_rct_wo``,
    ``raw_facturas``, ``raw_product_struc``, ``raw_item_master``,
    ``raw_manual_sheets``); they are mapped when their loader is built.

Depends on:
    ``sqlmodel``, ``energy.core.constants``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

SCHEMA = "bronze"


class RawReading:
    """Maps ``bronze.raw_readings`` - the landing table for every ingested record.

    Hypertable partitioned on ``ingested_at``.

    Columns:
        raw_reading_id: UUID, part of the composite primary key.
        ingested_at: when the row was written, UTC. Partitioning column, and part
            of the primary key because TimescaleDB requires the time column in
            every unique constraint.
        source_system: which configured source produced it.
        source_uri: object-storage key or query reference locating the original.
        source_row: 1-based row index within the payload, so a rejected value can
            be pointed at a specific spreadsheet cell.
        ingestion_run_id: the run that wrote it.
        content_hash: SHA-256 of the raw payload; the deduplication key.
        plant_code: denormalised for partition pruning on plant-scoped reads.
        payload: JSONB holding the record exactly as read, with no coercion.
    """

    __tablename__ = "raw_readings"


class IngestionRun:
    """Maps ``bronze.ingestion_runs`` - the ledger of every ingestion attempt.

    Columns:
        run_id: UUID primary key.
        flow_name: which Prefect flow ran.
        source_key: which kind of export it loaded; one of the keys the
            migration allows (``qad_rct_wo``, ``qad_facturas``,
            ``qad_product_struc``, ``item_master``, ``manual_sheets``).
        source_system: the source it read.
        plant_code: the plant it ran for.
        state: pending, running, completed, failed, or cancelled.
        window_start: first date of the business period the extract covers;
            null when it is unknown.
        window_end: last date of that period; null when it is unknown.
        qad_program: the QAD program that produced the export, as its Info
            sheet reports it; null for sources without one.
        qad_exploration: the QAD exploration name, from the same sheet.
        qad_user: the QAD user who ran the query, from the same sheet.
        queried_at: when the query ran, UTC, from the same sheet.
        search_conditions: JSONB array holding the export's search conditions
            as delivered; they can be stale, so nothing is derived from them.
        started_at: when the run began, UTC.
        finished_at: when it ended, UTC; null while running.
        row_count: rows written to Bronze.
        error: failure message; empty on success.
        parameters: JSONB of the invocation parameters, which is what a retry
            replays.
    """

    __tablename__ = "ingestion_runs"


class RawFile:
    """Maps ``bronze.raw_files`` - the catalogue of archived source payloads.

    One row per physical file received, pointing at the immutable copy in object
    storage. This is what makes the audit chain complete: a Gold number resolves
    to Silver rows, which resolve to Bronze rows, which resolve to a row here,
    which resolves to the exact bytes a plant sent.

    Columns:
        raw_file_id: UUID primary key.
        content_hash: SHA-256 of the file; unique, so a re-upload is detected
            before the file is parsed.
        bucket: object-storage bucket holding it.
        object_key: key within the bucket.
        filename: original filename as submitted.
        content_type: MIME type.
        size_bytes: payload size.
        plant_code: the plant it was uploaded for.
        source_key: the configured source it represents; restricted to the
            same keys as ``IngestionRun.source_key``.
        sheet_names: the worksheets found in the workbook; empty for a file
            that has none.
        uploaded_by: who submitted it.
        uploaded_at: when it was received, UTC.
    """

    __tablename__ = "raw_files"


__all__ = ["SCHEMA", "IngestionRun", "RawFile", "RawReading"]
