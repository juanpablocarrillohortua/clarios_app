"""PostgreSQL implementation of the Bronze landing port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.bronze_landing.BronzeLandingRepository`
    against the ``bronze`` schema created by migration 0002. It is the only
    module that knows which landing table a source key writes to. Rows are
    streamed with ``COPY`` in batches, all inside one transaction that also
    marks the run completed and records the period its rows cover, so a load is
    all-or-nothing. The adapter only
    inserts into landing tables and only updates ``bronze.ingestion_runs``:
    Bronze is append-only, and the database would reject anything else.

Depends on:
    ``energy.ports.bronze_landing``, ``energy.domain.exceptions``,
    ``energy.core``, ``sqlalchemy``, ``psycopg``.
"""

from __future__ import annotations

import json
from itertools import batched
from typing import TYPE_CHECKING, Any, Final

import psycopg
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from energy.core.constants import BRONZE_SCHEMA
from energy.core.logging import get_logger
from energy.domain.exceptions import RepositoryError, UnknownSourceError
from energy.ports.bronze_landing import RawFile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from datetime import date
    from uuid import UUID

    from sqlalchemy.engine import Engine, Row

    from energy.ports.bronze_landing import LandingTarget, NewRawFile, NewRun, SheetBatch

logger = get_logger(__name__)

# Source key -> landing table. The keys match the check constraint on
# bronze.ingestion_runs.source_key.
LANDING_TABLES: Final[Mapping[str, str]] = {
    "qad_rct_wo": "raw_rct_wo",
    "qad_facturas": "raw_facturas",
    "qad_product_struc": "raw_product_struc",
    "item_master": "raw_item_master",
    "manual_sheets": "raw_manual_sheets",
}

# The one landing table that names its catalogue.
TABLE_WITH_DATASET_KEY: Final = "raw_manual_sheets"

DEFAULT_BATCH_SIZE: Final = 5_000

LANDING_COLUMNS: Final = (
    "ingestion_run_id",
    "raw_file_id",
    "source_system",
    "source_uri",
    "sheet_name",
    "source_row",
    "content_hash",
    "plant_code",
    "payload",
)

# Every f-string query in this module interpolates module constants only - the
# schema and the landing table names - never a value that came from outside. All
# values travel as bound parameters. Hence the S608 / B608 suppressions.
#
# The web app's commit (postgres_load_committer.py) runs these same statements
# inside its one transaction, so a file catalogued and a run opened and
# completed are recorded the same way whichever path lands them.
SELECT_FILE: Final = text(
    f"""
    SELECT raw_file_id, content_hash, bucket, object_key
    FROM {BRONZE_SCHEMA}.raw_files
    WHERE content_hash = :content_hash
    """  # noqa: S608  # nosec B608
)

INSERT_FILE: Final = text(
    f"""
    INSERT INTO {BRONZE_SCHEMA}.raw_files
        (content_hash, bucket, object_key, filename, content_type, size_bytes,
         plant_code, source_key, sheet_names, uploaded_by)
    VALUES
        (:content_hash, :bucket, :object_key, :filename, :content_type, :size_bytes,
         :plant_code, :source_key, CAST(:sheet_names AS text[]), :uploaded_by)
    ON CONFLICT (content_hash) DO NOTHING
    RETURNING raw_file_id, content_hash, bucket, object_key
    """  # noqa: S608  # nosec B608
)

INSERT_RUN: Final = text(
    f"""
    INSERT INTO {BRONZE_SCHEMA}.ingestion_runs
        (flow_name, source_key, source_system, plant_code, state,
         window_start, window_end, qad_program, qad_exploration, qad_user,
         queried_at, search_conditions, parameters)
    VALUES
        (:flow_name, :source_key, :source_system, :plant_code, 'running',
         :window_start, :window_end, :qad_program, :qad_exploration, :qad_user,
         :queried_at, CAST(:search_conditions AS jsonb), CAST(:parameters AS jsonb))
    RETURNING run_id
    """  # noqa: S608  # nosec B608
)

# A bound of the period that the rows did not give keeps what the run was opened
# with, which is the caller's own date or nothing.
COMPLETE_RUN: Final = text(
    f"""
    UPDATE {BRONZE_SCHEMA}.ingestion_runs
    SET state = 'completed', finished_at = now(), row_count = :row_count, error = '',
        window_start = COALESCE(CAST(:window_start AS date), window_start),
        window_end = COALESCE(CAST(:window_end AS date), window_end)
    WHERE run_id = :run_id
    """  # noqa: S608  # nosec B608
)

_FAIL_RUN: Final = text(
    f"""
    UPDATE {BRONZE_SCHEMA}.ingestion_runs
    SET state = 'failed', finished_at = now(), error = :error
    WHERE run_id = :run_id
    """  # noqa: S608  # nosec B608
)


def landing_table(source_key: str) -> str:
    """Return the landing table a source key writes to.

    Args:
        source_key: the source key.

    Returns:
        The unqualified table name.

    Raises:
        UnknownSourceError: if the source key has no landing table.
    """
    try:
        return LANDING_TABLES[source_key]
    except KeyError:
        raise UnknownSourceError("source key has no landing table", source_key=source_key) from None


def _as_raw_file(row: Row[Any]) -> RawFile:
    """Map a ``raw_files`` row to its port object."""
    return RawFile(
        raw_file_id=row.raw_file_id,
        content_hash=row.content_hash,
        bucket=row.bucket,
        object_key=row.object_key,
    )


class PostgresBronzeLandingRepository:
    """Bronze ledgers and landing tables in PostgreSQL.

    Args:
        engine: the SQLAlchemy engine to write through.
        batch_size: rows sent per ``COPY`` statement.
    """

    def __init__(self, engine: Engine, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        if batch_size < 1:
            msg = f"batch_size must be positive, got {batch_size}"
            raise ValueError(msg)
        self._engine = engine
        self._batch_size = batch_size

    def find_file(self, content_hash: str) -> RawFile | None:
        """Look a file up by the hash of its bytes.

        Args:
            content_hash: SHA-256 hex digest.

        Returns:
            The catalogue entry, or ``None`` when the file was never registered.
        """
        with self._engine.connect() as connection:
            row = connection.execute(SELECT_FILE, {"content_hash": content_hash}).one_or_none()
        return _as_raw_file(row) if row is not None else None

    def register_file(self, file: NewRawFile) -> RawFile:
        """Add a file to the catalogue, once per distinct hash.

        Args:
            file: the file to register.

        Returns:
            The catalogue entry; the existing one when the hash is already known.

        Raises:
            RepositoryError: if the write fails.
        """
        parameters = {
            "content_hash": file.content_hash,
            "bucket": file.bucket,
            "object_key": file.object_key,
            "filename": file.filename,
            "content_type": file.content_type,
            "size_bytes": file.size_bytes,
            "plant_code": file.plant_code,
            "source_key": file.source_key,
            "sheet_names": list(file.sheet_names),
            "uploaded_by": file.uploaded_by,
        }
        try:
            with self._engine.begin() as connection:
                row = connection.execute(INSERT_FILE, parameters).one_or_none()
                if row is None:
                    # Lost a race, or the file was registered before: keep theirs.
                    row = connection.execute(SELECT_FILE, {"content_hash": file.content_hash}).one()
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not register the file", filename=file.filename, reason=str(exc)
            ) from exc
        return _as_raw_file(row)

    def landed_sheets(
        self, raw_file_id: UUID, source_key: str
    ) -> frozenset[tuple[str, str | None]]:
        """Return the sheets of a file that a completed run already landed.

        Args:
            raw_file_id: the archived file.
            source_key: the source, which decides the landing table to look in.

        Returns:
            ``(sheet_name, dataset_key)`` pairs; ``dataset_key`` is ``None`` for
            every table but the hand-maintained catalogues.
        """
        table = landing_table(source_key)
        dataset_key = "landed.dataset_key" if table == TABLE_WITH_DATASET_KEY else "NULL"
        statement = text(
            f"""
            SELECT DISTINCT landed.sheet_name, {dataset_key} AS dataset_key
            FROM {BRONZE_SCHEMA}.{table} AS landed
            JOIN {BRONZE_SCHEMA}.ingestion_runs AS run
              ON run.run_id = landed.ingestion_run_id
            WHERE landed.raw_file_id = :raw_file_id
              AND run.state = 'completed'
            """  # noqa: S608  # nosec B608
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement, {"raw_file_id": raw_file_id}).all()
        return frozenset((row.sheet_name, row.dataset_key) for row in rows)

    def open_run(self, run: NewRun) -> UUID:
        """Record a run in state ``running`` and commit it.

        Args:
            run: the run to open.

        Returns:
            The id of the new run.

        Raises:
            RepositoryError: if the write fails.
        """
        parameters = {
            "flow_name": run.flow_name,
            "source_key": run.source_key,
            "source_system": run.source_system,
            "plant_code": run.plant_code,
            "window_start": run.window_start,
            "window_end": run.window_end,
            "qad_program": run.qad_program,
            "qad_exploration": run.qad_exploration,
            "qad_user": run.qad_user,
            "queried_at": run.queried_at,
            "search_conditions": json.dumps(list(run.search_conditions), ensure_ascii=False),
            "parameters": json.dumps(dict(run.parameters), ensure_ascii=False, default=str),
        }
        try:
            with self._engine.begin() as connection:
                run_id: UUID = connection.execute(INSERT_RUN, parameters).scalar_one()
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not open the run", flow_name=run.flow_name, reason=str(exc)
            ) from exc
        return run_id

    def land(
        self,
        target: LandingTarget,
        sheets: Iterable[SheetBatch],
        period: Callable[[], tuple[date | None, date | None]] | None = None,
    ) -> dict[str, int]:
        """Land every sheet of one load and complete its run, atomically.

        Args:
            target: the run, file and source the rows belong to.
            sheets: the sheets to land, in order.
            period: called after every sheet has been read, for the first and
                the last date the rows cover; recorded as the run is completed.

        Returns:
            Rows landed per sheet name.

        Raises:
            RepositoryError: if the database rejects the write. An error raised
                while reading ``sheets`` propagates unchanged. Either way the
                transaction is rolled back and nothing is landed.
        """
        table = landing_table(target.source_key)
        counts: dict[str, int] = {}
        try:
            with self._engine.begin() as connection:
                driver_connection = connection.connection.driver_connection
                if driver_connection is None:  # pragma: no cover - a live connection has one
                    raise RepositoryError("database connection is closed")
                with driver_connection.cursor() as cursor:
                    for sheet in sheets:
                        landed = self._copy_sheet(cursor, table, target, sheet)
                        counts[sheet.sheet_name] = counts.get(sheet.sheet_name, 0) + landed
                window_start, window_end = period() if period is not None else (None, None)
                connection.execute(
                    COMPLETE_RUN,
                    {
                        "run_id": target.run_id,
                        "row_count": sum(counts.values()),
                        "window_start": window_start,
                        "window_end": window_end,
                    },
                )
        except (SQLAlchemyError, psycopg.Error) as exc:
            raise RepositoryError(
                "could not land the rows", table=table, run_id=str(target.run_id), reason=str(exc)
            ) from exc

        logger.debug(
            "bronze rows landed",
            table=table,
            run_id=str(target.run_id),
            rows=sum(counts.values()),
        )
        return counts

    def _copy_sheet(
        self,
        cursor: psycopg.Cursor[Any],
        table: str,
        target: LandingTarget,
        sheet: SheetBatch,
    ) -> int:
        """Stream one sheet into its landing table and return the row count."""
        columns: tuple[str, ...] = LANDING_COLUMNS
        shared: tuple[object, ...] = (
            target.run_id,
            target.raw_file_id,
            target.source_system,
            sheet.source_uri,
            sheet.sheet_name,
        )
        tail: tuple[object, ...] = ()
        if table == TABLE_WITH_DATASET_KEY:
            columns = (*columns, "dataset_key")
            tail = (sheet.dataset_key,)

        statement = f"COPY {BRONZE_SCHEMA}.{table} ({', '.join(columns)}) FROM STDIN"
        plant_code = target.plant_code
        landed = 0
        for batch in batched(sheet.rows, self._batch_size):
            with cursor.copy(statement) as copy:
                for row in batch:
                    copy.write_row(
                        (
                            *shared,
                            row.source_row,
                            row.content_hash,
                            plant_code,
                            row.payload_json,
                            *tail,
                        )
                    )
            landed += len(batch)
        return landed

    def fail_run(self, run_id: UUID, error: str) -> None:
        """Mark a run as ``failed``.

        Args:
            run_id: the run.
            error: what went wrong.

        Raises:
            RepositoryError: if the write fails.
        """
        try:
            with self._engine.begin() as connection:
                connection.execute(_FAIL_RUN, {"run_id": run_id, "error": error})
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not mark the run as failed", run_id=str(run_id), reason=str(exc)
            ) from exc


__all__ = [
    "COMPLETE_RUN",
    "DEFAULT_BATCH_SIZE",
    "INSERT_FILE",
    "INSERT_RUN",
    "LANDING_COLUMNS",
    "LANDING_TABLES",
    "SELECT_FILE",
    "TABLE_WITH_DATASET_KEY",
    "PostgresBronzeLandingRepository",
    "landing_table",
]
