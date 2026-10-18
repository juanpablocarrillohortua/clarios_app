"""PostgreSQL implementation of the draft repository port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.draft_repository.DraftRepository` against
    schema ``staging`` (revision 0007). Every read is filtered by the owner and
    by the draft's expiry, so a person never sees a draft of someone else and
    an expired draft is gone before the purge removes it.

    A validation runs in one transaction that holds ``FOR UPDATE NOWAIT`` on
    the draft: it removes what the previous validation staged, streams the new
    rows and issues with ``COPY`` in batches, and records the counts. A second
    validation of the same draft is refused at once instead of waiting.

    The statements name schema ``staging`` only. Nothing here reads or writes
    Bronze; a unit test reads this file to keep it so.

Depends on:
    ``energy.ports.draft_repository``, ``energy.domain``, ``energy.core``,
    ``sqlalchemy``, ``psycopg``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from itertools import batched
from typing import TYPE_CHECKING, Any, Final

import psycopg
from psycopg.errors import LockNotAvailable
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from energy.domain.draft_rules import Issue, IssueSeverity, RuleCount, RuleId
from energy.domain.drafts import Draft, DraftStatus, ValidationSummary
from energy.domain.exceptions import DraftBusyError, RepositoryError, UnknownDraftError
from energy.ports.draft_repository import ExpiredDraft, IssuePage

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.engine import Connection, Engine, Row

    from energy.domain.landing import LandingRow
    from energy.ports.draft_repository import ArchivedFile, IssueFilter, NewDraft

DEFAULT_BATCH_SIZE: Final = 5_000

_DRAFT_COLUMNS: Final = """
    draft_id, dataset_key, source_key, plant_code, status, filename, declared_bytes,
    size_bytes, content_hash, bucket, object_key, created_by, created_by_name,
    created_at, uploaded_at, validated_at, expires_at, failure_code,
    sheet_name, header_row, rows_read, rows_with_errors, rows_with_warnings
"""

_INSERT_DRAFT: Final = text(
    f"""
    INSERT INTO staging.drafts
        (dataset_key, source_key, plant_code, filename, content_type, declared_bytes,
         created_by, created_by_name)
    VALUES
        (:dataset_key, :source_key, :plant_code, :filename, :content_type, :declared_bytes,
         :created_by, :created_by_name)
    RETURNING {_DRAFT_COLUMNS}
    """  # noqa: S608  # nosec B608 - the column list is a constant
)

_MARK_UPLOADED: Final = text(
    f"""
    UPDATE staging.drafts
    SET status = 'uploaded', size_bytes = :size_bytes, content_hash = :content_hash,
        bucket = :bucket, object_key = :object_key, uploaded_at = now(),
        expires_at = now() + interval '30 days'
    WHERE draft_id = :draft_id AND status = 'receiving'
    RETURNING {_DRAFT_COLUMNS}
    """  # noqa: S608  # nosec B608
)

_MARK_FAILED: Final = text(
    """
    UPDATE staging.drafts
    SET status = 'failed', failure_code = :failure_code
    WHERE draft_id = :draft_id AND status = 'receiving'
    """
)

_SELECT_DRAFT: Final = text(
    f"""
    SELECT {_DRAFT_COLUMNS}
    FROM staging.drafts
    WHERE draft_id = :draft_id AND created_by = :owner AND expires_at > now()
    """  # noqa: S608  # nosec B608
)

_LOCK_DRAFT: Final = text(
    f"""
    SELECT {_DRAFT_COLUMNS}
    FROM staging.drafts
    WHERE draft_id = :draft_id AND created_by = :owner AND expires_at > now()
    FOR UPDATE NOWAIT
    """  # noqa: S608  # nosec B608
)

_SELECT_DUPLICATE: Final = text(
    f"""
    SELECT {_DRAFT_COLUMNS}
    FROM staging.drafts
    WHERE created_by = :owner AND dataset_key = :dataset_key AND content_hash = :content_hash
      AND status IN ('uploaded', 'validated') AND expires_at > now()
      AND draft_id <> :excluding
    ORDER BY created_at DESC
    LIMIT 1
    """  # noqa: S608  # nosec B608
)

_SELECT_COUNTS: Final = text(
    """
    SELECT rule_id, severity, column_key, rows_affected, issues_listed
    FROM staging.draft_rule_counts
    WHERE draft_id = :draft_id
    ORDER BY rule_id, column_key
    """
)

_CLEAR_RESULTS: Final = (
    text("DELETE FROM staging.draft_rows WHERE draft_id = :draft_id"),
    text("DELETE FROM staging.draft_issues WHERE draft_id = :draft_id"),
    text("DELETE FROM staging.draft_rule_counts WHERE draft_id = :draft_id"),
)

_INSERT_COUNT: Final = text(
    """
    INSERT INTO staging.draft_rule_counts
        (draft_id, rule_id, column_key, severity, rows_affected, issues_listed)
    VALUES (:draft_id, :rule_id, :column_key, :severity, :rows_affected, :issues_listed)
    """
)

_FINISH_VALIDATION: Final = text(
    f"""
    UPDATE staging.drafts
    SET status = 'validated', validated_at = now(), expires_at = now() + interval '30 days',
        sheet_name = :sheet_name, header_row = :header_row, rows_read = :rows_read,
        rows_with_errors = :rows_with_errors, rows_with_warnings = :rows_with_warnings
    WHERE draft_id = :draft_id
    RETURNING {_DRAFT_COLUMNS}
    """  # noqa: S608  # nosec B608
)

# A filter left empty matches every value: the casts tell PostgreSQL the type
# of a parameter that may be null.
_ISSUE_FILTER: Final = """
    WHERE draft_id = :draft_id
      AND (CAST(:severity AS text) IS NULL OR severity = CAST(:severity AS text))
      AND (CAST(:rule_id AS text) IS NULL OR rule_id = CAST(:rule_id AS text))
      AND (CAST(:column_key AS text) IS NULL OR column_key = CAST(:column_key AS text))
"""

_COUNT_ISSUES: Final = text(
    f"SELECT count(*) FROM staging.draft_issues {_ISSUE_FILTER}"  # noqa: S608  # nosec B608
)

_SELECT_ISSUES: Final = text(
    f"""
    SELECT rule_id, severity, source_row, column_index, column_key, value, params
    FROM staging.draft_issues
    {_ISSUE_FILTER}
    ORDER BY source_row NULLS FIRST, column_index NULLS FIRST, rule_id, issue_id
    OFFSET :offset LIMIT :limit
    """  # noqa: S608  # nosec B608
)

_SELECT_EXPIRED: Final = text(
    """
    SELECT draft_id, bucket, object_key, content_hash
    FROM staging.drafts
    WHERE CASE
        WHEN CAST(:only AS uuid) IS NOT NULL THEN draft_id = CAST(:only AS uuid)
        ELSE expires_at <= :now
          OR (status IN ('receiving', 'failed') AND created_at <= :now - interval '1 day')
    END
    ORDER BY created_at
    """
)

_SELECT_REFERENCED: Final = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM staging.drafts
        WHERE bucket = :bucket AND object_key = :object_key
          AND NOT (draft_id = ANY(:excluding))
    )
    """
)

_DELETE_DRAFTS: Final = text("DELETE FROM staging.drafts WHERE draft_id = ANY(:draft_ids)")

_COPY_ROWS: Final = (
    "COPY staging.draft_rows (draft_id, source_row, content_hash, payload) FROM STDIN"
)
_COPY_ISSUES: Final = (
    "COPY staging.draft_issues (draft_id, rule_id, severity, source_row, column_index,"
    " column_key, value, params) FROM STDIN"
)


def _counts(connection: Connection, draft_id: UUID) -> tuple[RuleCount, ...]:
    """Return how often each rule was broken in a draft."""
    rows = connection.execute(_SELECT_COUNTS, {"draft_id": draft_id}).all()
    return tuple(
        RuleCount(
            RuleId(row.rule_id),
            IssueSeverity(row.severity),
            row.column_key or None,
            int(row.rows_affected),
            int(row.issues_listed),
        )
        for row in rows
    )


def _as_draft(row: Row[Any], counts: tuple[RuleCount, ...] = ()) -> Draft:
    """Build a draft from its row and, once validated, its counts."""
    validation = None
    if row.status == DraftStatus.VALIDATED:
        validation = ValidationSummary(
            sheet_name=row.sheet_name,
            header_row=row.header_row,
            rows_read=row.rows_read,
            rows_with_errors=row.rows_with_errors,
            rows_with_warnings=row.rows_with_warnings,
            counts=counts,
        )
    return Draft(
        draft_id=row.draft_id,
        dataset_key=row.dataset_key,
        source_key=row.source_key,
        plant_code=row.plant_code,
        status=DraftStatus(row.status),
        filename=row.filename,
        declared_bytes=row.declared_bytes,
        size_bytes=row.size_bytes,
        content_hash=row.content_hash,
        bucket=row.bucket,
        object_key=row.object_key,
        created_by=row.created_by,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
        uploaded_at=row.uploaded_at,
        validated_at=row.validated_at,
        expires_at=row.expires_at,
        failure_code=row.failure_code,
        validation=validation,
    )


def _as_issue(row: Row[Any]) -> Issue:
    """Build an issue from its row."""
    params = {
        name: tuple(value) if isinstance(value, list) else value
        for name, value in dict(row.params).items()
    }
    return Issue(
        RuleId(row.rule_id),
        IssueSeverity(row.severity),
        row.source_row,
        row.column_index,
        row.column_key,
        row.value,
        params,
    )


class _PostgresDraftValidation:
    """The writes of one validation, inside the caller's transaction.

    Args:
        connection: the connection holding the draft's lock.
        draft_id: the draft.
        batch_size: rows sent per ``COPY`` statement.
    """

    def __init__(self, connection: Connection, draft_id: UUID, batch_size: int) -> None:
        self._connection = connection
        self._draft_id = draft_id
        self._batch_size = batch_size

    def _cursor(self) -> psycopg.Cursor[Any]:
        """Return a cursor of the driver's own connection, for ``COPY``."""
        driver_connection = self._connection.connection.driver_connection
        if driver_connection is None:  # pragma: no cover - a live connection has one
            raise RepositoryError("database connection is closed")
        cursor: psycopg.Cursor[Any] = driver_connection.cursor()
        return cursor

    def stage_rows(self, rows: Iterable[LandingRow]) -> int:
        """Stage rows as Bronze would land them; return how many."""
        staged = 0
        with self._cursor() as cursor:
            for batch in batched(rows, self._batch_size):
                with cursor.copy(_COPY_ROWS) as copy:
                    for row in batch:
                        copy.write_row(
                            (self._draft_id, row.source_row, row.content_hash, row.payload_json)
                        )
                staged += len(batch)
        return staged

    def add_issues(self, issues: Sequence[Issue]) -> None:
        """Store listed issues."""
        if not issues:
            return
        with self._cursor() as cursor, cursor.copy(_COPY_ISSUES) as copy:
            for issue in issues:
                copy.write_row(
                    (
                        self._draft_id,
                        issue.rule.value,
                        issue.severity.value,
                        issue.source_row,
                        issue.column_index,
                        issue.column_key,
                        issue.value,
                        json.dumps(dict(issue.params), ensure_ascii=False),
                    )
                )

    def finish(self, summary: ValidationSummary) -> Draft:
        """Record what the validation found and return the validated draft."""
        for count in summary.counts:
            self._connection.execute(
                _INSERT_COUNT,
                {
                    "draft_id": self._draft_id,
                    "rule_id": count.rule.value,
                    "column_key": count.column_key or "",
                    "severity": count.severity.value,
                    "rows_affected": count.rows,
                    "issues_listed": count.listed,
                },
            )
        row = self._connection.execute(
            _FINISH_VALIDATION,
            {
                "draft_id": self._draft_id,
                "sheet_name": summary.sheet_name,
                "header_row": summary.header_row,
                "rows_read": summary.rows_read,
                "rows_with_errors": summary.rows_with_errors,
                "rows_with_warnings": summary.rows_with_warnings,
            },
        ).one()
        return _as_draft(row, summary.counts)


class PostgresDraftRepository:
    """Drafts in schema ``staging``.

    Args:
        engine: the SQLAlchemy engine to work through.
        batch_size: rows sent per ``COPY`` statement.
    """

    def __init__(self, engine: Engine, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        if batch_size < 1:
            msg = f"batch_size must be positive, got {batch_size}"
            raise ValueError(msg)
        self._engine = engine
        self._batch_size = batch_size

    def create(self, draft: NewDraft) -> Draft:
        """Record a draft in state ``receiving`` and commit it."""
        parameters = {
            "dataset_key": draft.dataset_key,
            "source_key": draft.source_key,
            "plant_code": draft.plant_code,
            "filename": draft.filename,
            "content_type": draft.content_type,
            "declared_bytes": draft.declared_bytes,
            "created_by": draft.created_by,
            "created_by_name": draft.created_by_name,
        }
        try:
            with self._engine.begin() as connection:
                row = connection.execute(_INSERT_DRAFT, parameters).one()
        except SQLAlchemyError as exc:
            raise RepositoryError("could not record the draft", reason=str(exc)) from exc
        return _as_draft(row)

    def mark_uploaded(self, draft_id: UUID, archived: ArchivedFile) -> Draft:
        """Record where the original was archived; the draft is ``uploaded``."""
        parameters = {
            "draft_id": draft_id,
            "size_bytes": archived.size_bytes,
            "content_hash": archived.content_hash,
            "bucket": archived.bucket,
            "object_key": archived.object_key,
        }
        try:
            with self._engine.begin() as connection:
                row = connection.execute(_MARK_UPLOADED, parameters).one_or_none()
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not record the upload", draft_id=str(draft_id), reason=str(exc)
            ) from exc
        if row is None:
            raise DraftBusyError("the draft is no longer receiving", draft_id=str(draft_id))
        return _as_draft(row)

    def mark_failed(self, draft_id: UUID, failure_code: str) -> None:
        """Record that the upload failed, and why."""
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    _MARK_FAILED, {"draft_id": draft_id, "failure_code": failure_code}
                )
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not record the failed upload", draft_id=str(draft_id), reason=str(exc)
            ) from exc

    def find(self, draft_id: UUID, owner: str) -> Draft | None:
        """Return a live draft of ``owner``, or ``None``."""
        with self._engine.connect() as connection:
            row = connection.execute(
                _SELECT_DRAFT, {"draft_id": draft_id, "owner": owner}
            ).one_or_none()
            if row is None:
                return None
            return _as_draft(row, _counts(connection, draft_id))

    def find_duplicate(
        self, owner: str, dataset_key: str, content_hash: str, excluding: UUID
    ) -> Draft | None:
        """Return a live draft of ``owner`` holding the same bytes for the same dataset."""
        with self._engine.connect() as connection:
            row = connection.execute(
                _SELECT_DUPLICATE,
                {
                    "owner": owner,
                    "dataset_key": dataset_key,
                    "content_hash": content_hash,
                    "excluding": excluding,
                },
            ).one_or_none()
            if row is None:
                return None
            return _as_draft(row, _counts(connection, row.draft_id))

    @contextmanager
    def validation(self, draft_id: UUID, owner: str) -> Iterator[_PostgresDraftValidation]:
        """Open a validation of a draft, replacing its previous results.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
            DraftBusyError: if it is not archived yet, or another validation
                of it holds its lock.
            RepositoryError: if the database refuses a write.
        """
        try:
            with self._engine.begin() as connection:
                try:
                    row = connection.execute(
                        _LOCK_DRAFT, {"draft_id": draft_id, "owner": owner}
                    ).one_or_none()
                except OperationalError as exc:
                    if isinstance(exc.orig, LockNotAvailable):
                        raise DraftBusyError(
                            "another validation of this draft is running", draft_id=str(draft_id)
                        ) from exc
                    raise
                if row is None:
                    raise UnknownDraftError("no draft of yours has that ID", draft_id=str(draft_id))
                if row.status not in {DraftStatus.UPLOADED, DraftStatus.VALIDATED}:
                    raise DraftBusyError("the draft's file has not arrived", draft_id=str(draft_id))
                for statement in _CLEAR_RESULTS:
                    connection.execute(statement, {"draft_id": draft_id})
                yield _PostgresDraftValidation(connection, draft_id, self._batch_size)
        except (SQLAlchemyError, psycopg.Error) as exc:
            raise RepositoryError(
                "could not record the validation", draft_id=str(draft_id), reason=str(exc)
            ) from exc

    def issues(
        self, draft_id: UUID, owner: str, wanted: IssueFilter, offset: int, limit: int
    ) -> IssuePage:
        """Return a page of a draft's listed issues.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
        """
        parameters = {
            "draft_id": draft_id,
            "severity": wanted.severity.value if wanted.severity else None,
            "rule_id": wanted.rule.value if wanted.rule else None,
            "column_key": wanted.column_key,
        }
        with self._engine.connect() as connection:
            owned = connection.execute(
                _SELECT_DRAFT, {"draft_id": draft_id, "owner": owner}
            ).one_or_none()
            if owned is None:
                raise UnknownDraftError("no draft of yours has that ID", draft_id=str(draft_id))
            total = connection.execute(_COUNT_ISSUES, parameters).scalar_one()
            rows = connection.execute(
                _SELECT_ISSUES, {**parameters, "offset": offset, "limit": limit}
            ).all()
        return IssuePage(total=int(total), items=tuple(_as_issue(row) for row in rows))

    def expired(self, now: datetime, only: UUID | None = None) -> tuple[ExpiredDraft, ...]:
        """Return the drafts past their expiry, and uploads that never finished.

        With ``only``, return that one draft, expired or not, whoever made it.
        """
        with self._engine.connect() as connection:
            rows = connection.execute(_SELECT_EXPIRED, {"now": now, "only": only}).all()
        return tuple(
            ExpiredDraft(row.draft_id, row.bucket, row.object_key, row.content_hash) for row in rows
        )

    def references(self, bucket: str, object_key: str, excluding: Sequence[UUID]) -> bool:
        """Return whether a draft not in ``excluding`` points at that object."""
        with self._engine.connect() as connection:
            found = connection.execute(
                _SELECT_REFERENCED,
                {"bucket": bucket, "object_key": object_key, "excluding": list(excluding)},
            ).scalar_one()
        return bool(found)

    def remove(self, draft_ids: Sequence[UUID]) -> int:
        """Remove drafts and everything staged for them; return how many."""
        if not draft_ids:
            return 0
        try:
            with self._engine.begin() as connection:
                result = connection.execute(_DELETE_DRAFTS, {"draft_ids": list(draft_ids)})
        except SQLAlchemyError as exc:
            raise RepositoryError("could not remove drafts", reason=str(exc)) from exc
        return int(result.rowcount)


__all__ = ["DEFAULT_BATCH_SIZE", "PostgresDraftRepository"]
