"""PostgreSQL implementation of the load ledger port: Bronze's record of loads, read.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.load_ledger.LoadLedger` over
    ``bronze.ingestion_runs``, ``bronze.raw_files`` and the landing tables. It
    only reads. Every load is listed, whichever way it came: a run whose
    parameters carry ``input_mode`` came from the web app, any other from the
    command line. Both record the same keys for the file and its sheets
    (``filename``, ``content_hash``, ``sheets``), which is what the filters and
    the detail read.

    Every query is limited to the plants the caller may see.

Depends on:
    ``energy.adapters.postgres_bronze_repo``, ``energy.ports``,
    ``energy.domain``, ``energy.core``, ``sqlalchemy``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text

from energy.adapters.postgres_bronze_repo import (
    LANDING_TABLES,
    TABLE_WITH_DATASET_KEY,
    landing_table,
)
from energy.core.constants import BRONZE_SCHEMA
from energy.domain.draft_commit import LoadOrigin
from energy.ports.load_committer import LoadReference
from energy.ports.load_ledger import (
    ArchivedOriginal,
    LoadPage,
    LoadRecord,
    LoadSort,
    LoadSummary,
    Person,
    SheetLoaded,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from sqlalchemy.engine import Engine, Row
    from sqlalchemy.sql.elements import TextClause

    from energy.ports.load_committer import DatasetTarget
    from energy.ports.load_ledger import LoadFilter

# Every f-string below interpolates module constants only. Values travel as
# bound parameters. Hence the S608 / B608 suppressions.

_SUMMARY_COLUMNS: Final = """
    run.run_id, run.state, run.source_key, run.started_at, run.finished_at,
    run.window_start, run.window_end, run.row_count, run.error, run.plant_code,
    run.flow_name, run.parameters,
    run.parameters ->> 'filename' AS filename,
    run.parameters ->> 'dataset_key' AS dataset_key,
    coalesce(run.parameters ->> 'uploaded_by', 'system') AS uploaded_by,
    run.parameters ->> 'uploaded_by_name' AS uploaded_by_name,
    run.parameters ? 'input_mode' AS from_app,
    coalesce((
        SELECT sum((accepted ->> 'rows')::bigint)
        FROM jsonb_array_elements(
            CASE jsonb_typeof(run.parameters -> 'validation' -> 'accepted' -> 'warnings')
                WHEN 'array' THEN run.parameters -> 'validation' -> 'accepted' -> 'warnings'
                ELSE '[]'::jsonb
            END
        ) AS accepted
    ), 0) AS warnings_accepted
"""

# A filter left empty matches every load: the casts tell PostgreSQL the type of
# a parameter that may be null.
_FILTER: Final = """
    WHERE run.plant_code = ANY(CAST(:plants AS text[]))
      AND (CAST(:source_key AS text) IS NULL
           OR (run.source_key = CAST(:source_key AS text)
               AND run.parameters -> 'sheets' @> CAST(:sheet_match AS jsonb)))
      AND (CAST(:state AS text) IS NULL OR run.state = CAST(:state AS text))
      AND (CAST(:uploaded_by AS text) IS NULL
           OR coalesce(run.parameters ->> 'uploaded_by', 'system') = CAST(:uploaded_by AS text))
      AND (CAST(:origin AS text) IS NULL
           OR (run.parameters ? 'input_mode') = (CAST(:origin AS text) = 'app'))
      AND (CAST(:period_from AS date) IS NULL
           OR (run.window_end IS NOT NULL AND run.window_end >= CAST(:period_from AS date)))
      AND (CAST(:period_to AS date) IS NULL
           OR (run.window_start IS NOT NULL AND run.window_start <= CAST(:period_to AS date)))
      AND (CAST(:started_from AS date) IS NULL OR run.started_at >= CAST(:started_from AS date))
      AND (CAST(:started_to AS date) IS NULL OR run.started_at < CAST(:started_to AS date) + 1)
"""

_SORT_COLUMNS: Final[Mapping[LoadSort, str]] = {
    LoadSort.STARTED_AT: "run.started_at",
    LoadSort.ROWS: "run.row_count",
}

_COUNT: Final = text(
    f"SELECT count(*) FROM {BRONZE_SCHEMA}.ingestion_runs AS run {_FILTER}"  # noqa: S608  # nosec B608
)


def _page_statement(sort: LoadSort, descending: bool) -> TextClause:
    """Return the statement of one page, in one of the fixed orders."""
    column = _SORT_COLUMNS[sort]
    direction = "DESC" if descending else "ASC"
    return text(
        f"""
        SELECT {_SUMMARY_COLUMNS}
        FROM {BRONZE_SCHEMA}.ingestion_runs AS run
        {_FILTER}
        ORDER BY {column} {direction}, run.started_at {direction}, run.run_id {direction}
        OFFSET :offset LIMIT :limit
        """  # noqa: S608  # nosec B608
    )


_PAGES: Final[Mapping[tuple[LoadSort, bool], TextClause]] = {
    (sort, descending): _page_statement(sort, descending)
    for sort in LoadSort
    for descending in (True, False)
}

_PEOPLE: Final = text(
    f"""
    SELECT DISTINCT ON (run.parameters ->> 'uploaded_by')
           run.parameters ->> 'uploaded_by' AS subject,
           run.parameters ->> 'uploaded_by_name' AS name
    FROM {BRONZE_SCHEMA}.ingestion_runs AS run
    WHERE run.plant_code = ANY(CAST(:plants AS text[])) AND run.parameters ? 'input_mode'
      AND run.parameters ? 'uploaded_by'
    ORDER BY run.parameters ->> 'uploaded_by', run.started_at DESC
    """  # noqa: S608  # nosec B608
)

_ONE: Final = text(
    f"""
    SELECT {_SUMMARY_COLUMNS},
           archived.bucket, archived.object_key, archived.filename AS archived_filename,
           archived.content_type, archived.size_bytes, archived.content_hash AS archived_hash
    FROM {BRONZE_SCHEMA}.ingestion_runs AS run
    LEFT JOIN {BRONZE_SCHEMA}.raw_files AS archived
      ON archived.content_hash = run.parameters ->> 'content_hash'
    WHERE run.run_id = :run_id AND run.plant_code = ANY(CAST(:plants AS text[]))
    """  # noqa: S608  # nosec B608
)


def _sheet_rows_statement(table: str) -> TextClause:
    """Return the statement that counts a run's rows in one landing table, by sheet."""
    dataset_key = "landed.dataset_key" if table == TABLE_WITH_DATASET_KEY else "NULL"
    return text(
        f"""
        SELECT landed.sheet_name, {dataset_key} AS dataset_key, count(*) AS rows
        FROM {BRONZE_SCHEMA}.{table} AS landed
        WHERE landed.ingestion_run_id = :run_id
        GROUP BY 1, 2
        ORDER BY 1, 2
        """  # noqa: S608  # nosec B608
    )


_SHEET_ROWS: Final[Mapping[str, TextClause]] = {
    table: _sheet_rows_statement(table) for table in LANDING_TABLES.values()
}

_LAST_LOAD: Final = text(
    f"""
    SELECT run.run_id, run.finished_at, run.window_start, run.window_end, run.row_count AS rows,
           run.parameters ->> 'filename' AS filename,
           run.parameters ->> 'uploaded_by_name' AS uploaded_by_name,
           run.parameters ? 'input_mode' AS from_app
    FROM {BRONZE_SCHEMA}.ingestion_runs AS run
    WHERE run.state = 'completed' AND run.plant_code = ANY(CAST(:plants AS text[]))
      AND run.source_key = :source_key
      AND run.parameters -> 'sheets' @> CAST(:sheet_match AS jsonb)
    ORDER BY run.finished_at DESC, run.run_id DESC
    LIMIT 1
    """  # noqa: S608  # nosec B608
)


def _sheet_match(target: DatasetTarget) -> str:
    """Return the JSON a run's ``sheets`` must contain to be a load of this dataset."""
    sheet: dict[str, str] = {"name": target.sheet_name}
    if target.dataset_key is not None:
        sheet["dataset_key"] = target.dataset_key
    return json.dumps([sheet], ensure_ascii=False)


def _sheets(value: object) -> tuple[SheetLoaded, ...]:
    """Return the sheets a run's parameters name, skipping anything malformed."""
    if not isinstance(value, list):
        return ()
    return tuple(
        SheetLoaded(str(item["name"]), item.get("dataset_key"))
        for item in value
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )


def _summary(row: Row[Any], sheets: tuple[SheetLoaded, ...] | None = None) -> LoadSummary:
    """Map a run's row to what a history lists of it."""
    parameters: Mapping[str, Any] = row.parameters or {}
    return LoadSummary(
        run_id=row.run_id,
        origin=LoadOrigin.APP if row.from_app else LoadOrigin.CLI,
        state=row.state,
        source_key=row.source_key,
        dataset_key=row.dataset_key,
        sheets=sheets if sheets is not None else _sheets(parameters.get("sheets")),
        filename=row.filename,
        rows=int(row.row_count),
        started_at=row.started_at,
        finished_at=row.finished_at,
        window_start=row.window_start,
        window_end=row.window_end,
        uploaded_by=row.uploaded_by,
        uploaded_by_name=row.uploaded_by_name,
        warnings_accepted=int(row.warnings_accepted),
        error=row.error or "",
    )


def _parameters(wanted: LoadFilter) -> dict[str, object]:
    """Return the bound values of a filter."""
    target = wanted.target
    return {
        "plants": list(wanted.plant_scope),
        "source_key": target.source_key if target else None,
        "sheet_match": _sheet_match(target) if target else None,
        "state": wanted.state,
        "uploaded_by": wanted.uploaded_by,
        "origin": wanted.origin.value if wanted.origin else None,
        "period_from": wanted.period_from,
        "period_to": wanted.period_to,
        "started_from": wanted.started_from,
        "started_to": wanted.started_to,
    }


class PostgresLoadLedger:
    """Bronze's record of loads, read for the history and the detail pages.

    Args:
        engine: the SQLAlchemy engine to read through.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def loads(
        self, wanted: LoadFilter, sort: LoadSort, descending: bool, offset: int, limit: int
    ) -> LoadPage:
        """Return a page of loads."""
        parameters = _parameters(wanted)
        with self._engine.connect() as connection:
            total = connection.execute(_COUNT, parameters).scalar_one()
            rows = connection.execute(
                _PAGES[(sort, descending)], {**parameters, "offset": offset, "limit": limit}
            ).all()
            people = connection.execute(_PEOPLE, {"plants": parameters["plants"]}).all()
        return LoadPage(
            total=int(total),
            items=tuple(_summary(row) for row in rows),
            people=tuple(
                sorted(
                    (Person(row.subject, row.name) for row in people),
                    key=lambda person: ((person.name or "").casefold(), person.subject),
                )
            ),
        )

    def load(self, run_id: UUID, plant_scope: Sequence[str]) -> LoadRecord | None:
        """Return one load of the caller's plants, with each sheet's rows counted."""
        with self._engine.connect() as connection:
            row = connection.execute(
                _ONE, {"run_id": run_id, "plants": list(plant_scope)}
            ).one_or_none()
            if row is None:
                return None
            counted = connection.execute(
                _SHEET_ROWS[landing_table(row.source_key)], {"run_id": run_id}
            ).all()
        sheets = tuple(
            SheetLoaded(item.sheet_name, item.dataset_key, int(item.rows)) for item in counted
        ) or tuple(
            SheetLoaded(sheet.name, sheet.dataset_key, 0)
            for sheet in _sheets((row.parameters or {}).get("sheets"))
        )
        original = None
        if row.bucket is not None:
            original = ArchivedOriginal(
                bucket=row.bucket,
                object_key=row.object_key,
                filename=row.archived_filename,
                content_type=row.content_type,
                size_bytes=int(row.size_bytes),
                content_hash=row.archived_hash,
            )
        return LoadRecord(
            summary=_summary(row, sheets),
            plant_code=row.plant_code,
            flow_name=row.flow_name,
            original=original,
            parameters=dict(row.parameters or {}),
        )

    def last_loads(
        self, targets: Sequence[DatasetTarget], plant_scope: Sequence[str]
    ) -> Mapping[DatasetTarget, LoadReference]:
        """Return the latest completed load of each dataset that has one."""
        found: dict[DatasetTarget, LoadReference] = {}
        with self._engine.connect() as connection:
            for target in targets:
                row = connection.execute(
                    _LAST_LOAD,
                    {
                        "plants": list(plant_scope),
                        "source_key": target.source_key,
                        "sheet_match": _sheet_match(target),
                    },
                ).one_or_none()
                if row is not None:
                    found[target] = LoadReference(
                        run_id=row.run_id,
                        origin=LoadOrigin.APP if row.from_app else LoadOrigin.CLI,
                        finished_at=row.finished_at,
                        filename=row.filename,
                        rows=int(row.rows),
                        uploaded_by_name=row.uploaded_by_name,
                        window_start=row.window_start,
                        window_end=row.window_end,
                    )
        return found


__all__ = ["PostgresLoadLedger"]
