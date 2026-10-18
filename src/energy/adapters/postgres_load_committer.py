"""PostgreSQL implementation of the load committer port: a draft lands in Bronze.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.load_committer.LoadCommitter`. It is,
    with :mod:`energy.adapters.postgres_bronze_repo`, the only module that
    writes to Bronze, and the only one that reads staging and writes Bronze.

    A commit is one transaction on one connection:

    1. the draft is locked (``FOR UPDATE NOWAIT``) and checked by the caller;
    2. a lock of the dataset is taken (``pg_advisory_xact_lock``), waiting
       :data:`DATASET_LOCK_TIMEOUT` at most, so commits of one dataset take
       turns while commits of others do not wait;
    3. for a snapshot, the current version must still be the one the person
       confirmed against;
    4. the original is catalogued in ``bronze.raw_files`` unless Bronze knows
       its bytes, and a file whose sheet a completed run already landed is
       refused, as the command-line loader skips it;
    5. a run is opened, every staged row lands with one ``INSERT … SELECT``
       (the rows never leave the database), the count is checked, and the run
       is completed;
    6. the staged rows are deleted and the draft is marked committed.

    The statements that catalogue a file and open and complete a run are the
    command-line loader's own (imported from ``postgres_bronze_repo``), so
    both paths record a load the same way. Anything that fails rolls all of
    it back: Bronze is untouched, not even a failed run is left, and the draft
    is still validated with its rows.

    The confirmation's reads are here too: the current version of a dataset
    (the rule the Silver build uses to choose a snapshot's rows), the loads
    whose period meets a draft's, and how a catalogue's new version differs
    from the current one, by natural key.

Depends on:
    ``energy.adapters.postgres_bronze_repo``,
    ``energy.adapters.postgres_draft_repo``, ``energy.ports``,
    ``energy.domain``, ``energy.core``, ``sqlalchemy``, ``psycopg``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from psycopg.errors import LockNotAvailable
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from energy.adapters.postgres_bronze_repo import (
    COMPLETE_RUN,
    INSERT_FILE,
    INSERT_RUN,
    LANDING_COLUMNS,
    LANDING_TABLES,
    SELECT_FILE,
    TABLE_WITH_DATASET_KEY,
    landing_table,
)
from energy.adapters.postgres_draft_repo import DRAFT_COLUMNS, as_draft, rule_counts
from energy.core.constants import BRONZE_SCHEMA
from energy.core.logging import get_logger
from energy.domain.draft_commit import DifferenceCounts, LoadOrigin, RowChange
from energy.domain.drafts import DraftStatus
from energy.domain.exceptions import (
    AlreadyLoadedError,
    CommitCountMismatchError,
    CurrentVersionChangedError,
    DatasetBusyError,
    DraftBusyError,
    EnergyError,
    RepositoryError,
    UnknownDraftError,
)
from energy.ports.load_committer import (
    ColumnChange,
    CommittedLoad,
    DifferencePage,
    DifferenceRow,
    LoadReference,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date
    from uuid import UUID

    from sqlalchemy.engine import Connection, Engine, Row

    from energy.domain.drafts import Draft
    from energy.ports.load_committer import CommitOrder, DatasetTarget

logger = get_logger(__name__)

#: The first half of the advisory lock a commit takes on its dataset. The
#: second half is the hash of the dataset's place in Bronze. Two-key locks are
#: apart from the one-key lock of the Gold refresh and close (7215590330).
COMMIT_LOCK_CLASS: Final = 72_155_904
#: How long a commit waits for another commit of the same dataset.
DATASET_LOCK_TIMEOUT: Final = "10s"

# Every f-string below interpolates module constants only: the schema, a
# landing table's name from LANDING_TABLES and fixed fragments. Values travel
# as bound parameters. Hence the S608 / B608 suppressions.

_LOCK_DRAFT: Final = text(
    f"""
    SELECT {DRAFT_COLUMNS}
    FROM staging.drafts
    WHERE draft_id = :draft_id AND created_by = :owner AND expires_at > now()
    FOR UPDATE NOWAIT
    """  # noqa: S608  # nosec B608 - the column list is a constant
)
_SET_LOCK_TIMEOUT: Final = text(f"SET LOCAL lock_timeout = '{DATASET_LOCK_TIMEOUT}'")
_LOCK_DATASET: Final = text("SELECT pg_advisory_xact_lock(:lock_class, hashtext(:lock_name))")
_DELETE_STAGED: Final = text("DELETE FROM staging.draft_rows WHERE draft_id = :draft_id")
_MARK_COMMITTED: Final = text(
    """
    UPDATE staging.drafts
    SET status = 'committed', run_id = :run_id, committed_at = now(),
        expires_at = now() + interval '30 days'
    WHERE draft_id = :draft_id
    """
)


def _target_filter(table: str, alias: str) -> str:
    """Return the condition that picks a dataset's rows in its landing table.

    The hand-maintained sheets are told apart by their dataset key, every
    other source by its sheet: the rule the Silver build reads them by.
    """
    if table == TABLE_WITH_DATASET_KEY:
        return f"{alias}.dataset_key = :dataset_key"
    return f"{alias}.sheet_name = :sheet_name"


def _run_reference_columns(alias: str) -> str:
    """Return the columns a :class:`LoadReference` is read from."""
    return f"""
        {alias}.run_id, {alias}.finished_at, {alias}.window_start, {alias}.window_end,
        {alias}.parameters ->> 'filename' AS filename,
        {alias}.parameters ->> 'uploaded_by_name' AS uploaded_by_name,
        {alias}.parameters ? 'input_mode' AS from_app
    """


class _Statements:
    """The statements of one landing table, built once from fixed fragments."""

    def __init__(self, table: str) -> None:
        qualified = f"{BRONZE_SCHEMA}.{table}"
        mine = _target_filter(table, "landed")
        reference = _run_reference_columns("run")
        rows_of_dataset = f"""(
            SELECT count(*) FROM {qualified} AS landed
            WHERE landed.ingestion_run_id = run.run_id AND {mine}
        )"""  # noqa: S608  # nosec B608
        # The latest completed run that landed rows of the dataset: Silver's
        # own rule for a snapshot's current version (LATEST_RUNS).
        self.current_version = text(
            f"""
            SELECT {reference}, {rows_of_dataset} AS rows
            FROM {BRONZE_SCHEMA}.ingestion_runs AS run
            WHERE run.state = 'completed' AND run.source_key = :source_key
              AND EXISTS (
                SELECT 1 FROM {qualified} AS landed
                WHERE landed.ingestion_run_id = run.run_id AND {mine}
              )
            ORDER BY run.finished_at DESC, run.run_id DESC
            LIMIT 1
            """  # noqa: S608  # nosec B608
        )
        self.overlapping = text(
            f"""
            SELECT {reference}, {rows_of_dataset} AS rows
            FROM {BRONZE_SCHEMA}.ingestion_runs AS run
            WHERE run.state = 'completed' AND run.source_key = :source_key
              AND run.plant_code = :plant_code
              AND run.window_start IS NOT NULL AND run.window_end IS NOT NULL
              AND run.window_start <= :end AND run.window_end >= :start
              AND EXISTS (
                SELECT 1 FROM {qualified} AS landed
                WHERE landed.ingestion_run_id = run.run_id AND {mine}
              )
            ORDER BY run.finished_at DESC, run.run_id DESC
            """  # noqa: S608  # nosec B608
        )
        self.landed_by = text(
            f"""
            SELECT run.run_id
            FROM {BRONZE_SCHEMA}.raw_files AS archived
            JOIN {qualified} AS landed ON landed.raw_file_id = archived.raw_file_id
            JOIN {BRONZE_SCHEMA}.ingestion_runs AS run ON run.run_id = landed.ingestion_run_id
            WHERE archived.content_hash = :content_hash AND run.state = 'completed' AND {mine}
            ORDER BY run.finished_at DESC, run.run_id DESC
            LIMIT 1
            """  # noqa: S608  # nosec B608
        )
        columns: tuple[str, ...] = LANDING_COLUMNS
        shared = (
            "CAST(:run_id AS uuid), CAST(:raw_file_id AS uuid), CAST(:source_system AS text),"
            " CAST(:source_uri AS text), CAST(:sheet_name AS text),"
            " staged.source_row, staged.content_hash, CAST(:plant_code AS text), staged.payload"
        )
        if table == TABLE_WITH_DATASET_KEY:
            columns = (*columns, "dataset_key")
            shared += ", CAST(:dataset_key AS text)"
        self.land = text(
            f"""
            INSERT INTO {qualified} ({", ".join(columns)})
            SELECT {shared}
            FROM staging.draft_rows AS staged
            WHERE staged.draft_id = :draft_id
            ORDER BY staged.source_row
            """  # noqa: S608  # nosec B608
        )
        old = f"""
            SELECT source_row, content_hash, payload
            FROM {qualified} AS landed
            WHERE landed.ingestion_run_id = CAST(:base_run_id AS uuid) AND {mine}
        """  # noqa: S608  # nosec B608
        self.difference_counts = text(
            f"{_pairs(old)} SELECT change, count(*) AS rows FROM pairs GROUP BY change"  # noqa: S608  # nosec B608
        )
        self.difference_count = text(
            f"{_pairs(old)} SELECT count(*) FROM pairs WHERE change = :change"  # noqa: S608  # nosec B608
        )
        self.difference_page = text(
            f"""
            {_pairs(old)}
            SELECT change, key, source_row, previous_source_row, payload, previous_payload
            FROM pairs
            WHERE change = :change
            ORDER BY key, coalesce(source_row, previous_source_row)
            OFFSET :offset LIMIT :limit
            """  # noqa: S608  # nosec B608
        )


def _keyed(rows: str) -> str:
    """Return rows with their natural key and its occurrence among rows of that key.

    The key's values are trimmed of spaces, as the Silver build trims text
    (``pg_temp.eq_txt``); rows that share a key pair in sheet order.
    """
    key = """ARRAY(
        SELECT coalesce(btrim(source.payload ->> key_name.name), '')
        FROM unnest(CAST(:natural_key AS text[])) WITH ORDINALITY AS key_name(name, position)
        ORDER BY key_name.position
    )"""
    return f"""
        SELECT keyed.*, row_number() OVER (PARTITION BY keyed.key ORDER BY keyed.source_row) AS seen
        FROM (SELECT {key} AS key, source.* FROM ({rows}) AS source) AS keyed
    """  # noqa: S608  # nosec B608


def _pairs(old: str) -> str:
    """Return the CTE that pairs a draft's rows with a load's, by key and occurrence."""
    new = """
        SELECT source_row, content_hash, payload
        FROM staging.draft_rows WHERE draft_id = :draft_id
    """
    return f"""
        WITH fresh AS ({_keyed(new)}),
        current AS ({_keyed(old)}),
        pairs AS (
            SELECT coalesce(fresh.key, current.key) AS key,
                   fresh.source_row, current.source_row AS previous_source_row,
                   fresh.payload, current.payload AS previous_payload,
                   CASE
                       WHEN current.key IS NULL THEN 'added'
                       WHEN fresh.key IS NULL THEN 'removed'
                       WHEN fresh.content_hash = current.content_hash THEN 'unchanged'
                       ELSE 'changed'
                   END AS change
            FROM fresh
            FULL JOIN current ON current.key = fresh.key AND current.seen = fresh.seen
        )
    """  # noqa: S608  # nosec B608


_STATEMENTS: Final[Mapping[str, _Statements]] = {
    table: _Statements(table) for table in dict.fromkeys(LANDING_TABLES.values())
}


def _statements(source_key: str) -> _Statements:
    """Return the statements of the landing table a source writes to."""
    return _STATEMENTS[landing_table(source_key)]


def _target(target: DatasetTarget) -> dict[str, object]:
    """Return the parameters that name a dataset's place in Bronze."""
    return {
        "source_key": target.source_key,
        "sheet_name": target.sheet_name,
        "dataset_key": target.dataset_key,
    }


def _reference(row: Row[Any]) -> LoadReference:
    """Map a run's row to the reference a confirmation shows."""
    return LoadReference(
        run_id=row.run_id,
        origin=LoadOrigin.APP if row.from_app else LoadOrigin.CLI,
        finished_at=row.finished_at,
        filename=row.filename,
        rows=int(row.rows),
        uploaded_by_name=row.uploaded_by_name,
        window_start=row.window_start,
        window_end=row.window_end,
    )


def _column_changes(
    before: Mapping[str, str], after: Mapping[str, str]
) -> tuple[ColumnChange, ...]:
    """Return the columns whose values differ, in the new row's order, then the old one's."""
    names = list(dict.fromkeys([*after, *before]))
    return tuple(
        ColumnChange(name, before.get(name), after.get(name))
        for name in names
        if before.get(name) != after.get(name)
    )


def _is_lock_failure(exc: OperationalError) -> bool:
    """Return whether a database error is a lock that could not be had."""
    return isinstance(exc.orig, LockNotAvailable)


class PostgresLoadCommitter:
    """Lands drafts in Bronze, and reads what a confirmation shows.

    Args:
        engine: the SQLAlchemy engine to work through.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # What the confirmation reads
    # ------------------------------------------------------------------
    def current_version(self, target: DatasetTarget, plant_code: str) -> LoadReference | None:  # noqa: ARG002 - Silver's rule ignores the plant
        """Return the latest completed load of a dataset: its current version."""
        with self._engine.connect() as connection:
            return self._current(connection, target)

    def _current(self, connection: Connection, target: DatasetTarget) -> LoadReference | None:
        """Return the current version, read on a given connection."""
        row = connection.execute(
            _statements(target.source_key).current_version, _target(target)
        ).one_or_none()
        return _reference(row) if row is not None else None

    def landed_by(self, content_hash: str, target: DatasetTarget) -> UUID | None:
        """Return the completed run that landed this file's sheet for this dataset, if any."""
        with self._engine.connect() as connection:
            return self._landed_by(connection, content_hash, target)

    def _landed_by(
        self, connection: Connection, content_hash: str, target: DatasetTarget
    ) -> UUID | None:
        """Return the run that landed a file's sheet, read on a given connection."""
        found: UUID | None = connection.execute(
            _statements(target.source_key).landed_by,
            {**_target(target), "content_hash": content_hash},
        ).scalar_one_or_none()
        return found

    def overlapping(
        self, target: DatasetTarget, plant_code: str, start: date, end: date
    ) -> tuple[LoadReference, ...]:
        """Return the completed loads of a dataset whose period meets ``start``..``end``."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _statements(target.source_key).overlapping,
                {**_target(target), "plant_code": plant_code, "start": start, "end": end},
            ).all()
        return tuple(_reference(row) for row in rows)

    def difference(
        self,
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
    ) -> DifferenceCounts:
        """Count how a draft's rows differ from a load's, by natural key."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _statements(target.source_key).difference_counts,
                self._pairing(draft_id, base_run_id, target, natural_key),
            ).all()
        counts = {row.change: int(row.rows) for row in rows}
        return DifferenceCounts(
            base_run_id=base_run_id,
            added=counts.get("added", 0),
            changed=counts.get("changed", 0),
            removed=counts.get("removed", 0),
            unchanged=counts.get("unchanged", 0),
        )

    def difference_page(
        self,
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
        change: RowChange,
        offset: int,
        limit: int,
    ) -> DifferencePage:
        """Return a page of the rows of one kind of change, ordered by key."""
        statements = _statements(target.source_key)
        parameters = {
            **self._pairing(draft_id, base_run_id, target, natural_key),
            "change": change.value,
        }
        with self._engine.connect() as connection:
            total = connection.execute(statements.difference_count, parameters).scalar_one()
            rows = connection.execute(
                statements.difference_page, {**parameters, "offset": offset, "limit": limit}
            ).all()
        items = []
        for row in rows:
            after: dict[str, str] = dict(row.payload or {})
            before: dict[str, str] = dict(row.previous_payload or {})
            items.append(
                DifferenceRow(
                    change=change,
                    key=tuple((after or before).get(name) for name in natural_key),
                    source_row=row.source_row,
                    previous_source_row=row.previous_source_row,
                    values=after
                    if change is RowChange.ADDED
                    else before
                    if change is RowChange.REMOVED
                    else {},
                    columns=_column_changes(before, after) if change is RowChange.CHANGED else (),
                )
            )
        return DifferencePage(total=int(total), items=tuple(items))

    @staticmethod
    def _pairing(
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
    ) -> dict[str, object]:
        """Return the parameters that pair a draft's rows with a load's."""
        return {
            **_target(target),
            "draft_id": draft_id,
            "base_run_id": base_run_id,
            "natural_key": list(natural_key),
        }

    # ------------------------------------------------------------------
    # The commit
    # ------------------------------------------------------------------
    def commit(self, order: CommitOrder, check: Callable[[Draft], None]) -> CommittedLoad:
        """Land a draft in Bronze, in one transaction. See the module's docstring."""
        try:
            with self._engine.begin() as connection:
                return self._commit(connection, order, check)
        except EnergyError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not commit the draft", draft_id=str(order.draft_id), reason=str(exc)
            ) from exc

    def _commit(
        self, connection: Connection, order: CommitOrder, check: Callable[[Draft], None]
    ) -> CommittedLoad:
        """The steps of a commit, on the connection of its transaction."""
        draft = self._lock_draft(connection, order)
        if draft.status is DraftStatus.COMMITTED and draft.run_id is not None:
            rows = draft.validation.rows_read if draft.validation else 0
            return CommittedLoad(run_id=draft.run_id, rows=rows, created=False)
        check(draft)
        self._lock_dataset(connection, order.target)
        if order.snapshot:
            current = self._current(connection, order.target)
            current_id = current.run_id if current is not None else None
            if current_id != order.base_run_id:
                raise CurrentVersionChangedError(
                    "another load became the current version",
                    expected=str(order.base_run_id),
                    current=str(current_id),
                )
        raw_file = self._catalogue(connection, order)
        run_id: UUID = connection.execute(INSERT_RUN, self._run(order)).scalar_one()
        landed = connection.execute(
            _statements(order.target.source_key).land,
            {
                **_target(order.target),
                "draft_id": order.draft_id,
                "run_id": run_id,
                "raw_file_id": raw_file.raw_file_id,
                "source_system": order.run.source_system,
                "source_uri": f"{raw_file.bucket}://{raw_file.object_key}#{order.target.sheet_name}",
                "plant_code": order.run.plant_code,
            },
        ).rowcount
        if landed != order.rows:
            raise CommitCountMismatchError(
                "the rows landed are not the rows staged",
                draft_id=str(order.draft_id),
                staged=order.rows,
                landed=landed,
            )
        connection.execute(
            COMPLETE_RUN,
            {"run_id": run_id, "row_count": landed, "window_start": None, "window_end": None},
        )
        connection.execute(_DELETE_STAGED, {"draft_id": order.draft_id})
        connection.execute(_MARK_COMMITTED, {"draft_id": order.draft_id, "run_id": run_id})
        logger.info(
            "draft committed",
            draft_id=str(order.draft_id),
            run_id=str(run_id),
            source_key=order.target.source_key,
            rows=landed,
        )
        return CommittedLoad(run_id=run_id, rows=landed, created=True)

    @staticmethod
    def _lock_draft(connection: Connection, order: CommitOrder) -> Draft:
        """Lock the owner's draft for the transaction, or refuse at once."""
        try:
            row = connection.execute(
                _LOCK_DRAFT, {"draft_id": order.draft_id, "owner": order.owner}
            ).one_or_none()
        except OperationalError as exc:
            if _is_lock_failure(exc):
                raise DraftBusyError(
                    "the draft is being validated or committed", draft_id=str(order.draft_id)
                ) from exc
            raise
        if row is None:
            raise UnknownDraftError("no draft of yours has that ID", draft_id=str(order.draft_id))
        return as_draft(row, rule_counts(connection, order.draft_id))

    @staticmethod
    def _lock_dataset(connection: Connection, target: DatasetTarget) -> None:
        """Wait for other commits of the dataset, up to the timeout."""
        connection.execute(_SET_LOCK_TIMEOUT)
        name = f"{target.source_key}:{target.sheet_name}:{target.dataset_key or ''}"
        try:
            connection.execute(_LOCK_DATASET, {"lock_class": COMMIT_LOCK_CLASS, "lock_name": name})
        except OperationalError as exc:
            if _is_lock_failure(exc):
                raise DatasetBusyError(
                    "another commit of this dataset is running", dataset=name
                ) from exc
            raise

    def _catalogue(self, connection: Connection, order: CommitOrder) -> Row[Any]:
        """Return the original's catalogue entry, adding it if Bronze does not know the bytes."""
        file = order.file
        known = connection.execute(SELECT_FILE, {"content_hash": file.content_hash}).one_or_none()
        if known is not None:
            landed = self._landed_by(connection, file.content_hash, order.target)
            if landed is not None:
                raise AlreadyLoadedError(
                    "this file was already loaded for this dataset", run_id=str(landed)
                )
            return known
        added = connection.execute(
            INSERT_FILE,
            {
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
            },
        ).one_or_none()
        if added is None:
            # Catalogued by another transaction a moment ago: keep theirs.
            added = connection.execute(SELECT_FILE, {"content_hash": file.content_hash}).one()
        return added

    @staticmethod
    def _run(order: CommitOrder) -> dict[str, object]:
        """Return the parameters of the statement that opens the run."""
        run = order.run
        return {
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


__all__ = ["COMMIT_LOCK_CLASS", "DATASET_LOCK_TIMEOUT", "PostgresLoadCommitter"]
