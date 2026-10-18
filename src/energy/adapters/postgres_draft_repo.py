"""PostgreSQL implementation of the draft repository port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.draft_repository.DraftRepository` against
    schema ``staging`` (revisions 0007 to 0009). Every read is filtered by the owner and
    by the draft's expiry, so a person never sees a draft of someone else and
    an expired draft is gone before the purge removes it.

    A validation runs in one transaction that holds ``FOR UPDATE NOWAIT`` on
    the draft: it removes what the previous validation staged, streams the new
    rows and issues with ``COPY`` in batches, and records the counts. A second
    validation of the same draft is refused at once instead of waiting.

    A correction's issues are matched with the earlier draft's by rule,
    column and row identity (``match_key``), in SQL, for the rules and
    columns both drafts list in full or one of them lacks: the same test as
    :func:`energy.domain.draft_comparison.compared_by_row`.

    The statements name schema ``staging`` only. Nothing here reads or writes
    Bronze, Silver or Gold; a unit test reads this file to keep it so. The
    commit, which reads staging and writes Bronze in one transaction, is
    :mod:`energy.adapters.postgres_load_committer`; it reads drafts with
    :data:`DRAFT_COLUMNS`, :func:`as_draft` and :func:`rule_counts`.

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

from energy.domain.draft_comparison import IssueChange
from energy.domain.draft_reasons import Reason, ReasonId, ranked
from energy.domain.draft_rules import Issue, IssueSeverity, ReasonCount, RuleCount, RuleId
from energy.domain.drafts import Draft, DraftStatus, ExportFacts, ValidationSummary
from energy.domain.exceptions import DraftBusyError, RepositoryError, UnknownDraftError
from energy.ports.draft_repository import ExpiredDraft, IssuePage

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.engine import Connection, Engine, Row

    from energy.domain.draft_comparison import GroupKey
    from energy.domain.landing import LandingRow
    from energy.ports.draft_repository import ArchivedFile, IssueFilter, NewDraft

DEFAULT_BATCH_SIZE: Final = 5_000

DRAFT_COLUMNS: Final = """
    draft_id, dataset_key, source_key, plant_code, status, filename, declared_bytes,
    size_bytes, content_hash, bucket, object_key, created_by, created_by_name,
    created_at, uploaded_at, validated_at, expires_at, failure_code,
    sheet_name, header_row, rows_read, rows_with_errors, rows_with_warnings,
    previous_draft_id, spec_digest, run_id, committed_at,
    sheet_names, window_start, window_end, export_info, search_conditions
"""
_DRAFT_COLUMNS: Final = DRAFT_COLUMNS

_INSERT_DRAFT: Final = text(
    f"""
    INSERT INTO staging.drafts
        (dataset_key, source_key, plant_code, filename, content_type, declared_bytes,
         created_by, created_by_name, previous_draft_id)
    VALUES
        (:dataset_key, :source_key, :plant_code, :filename, :content_type, :declared_bytes,
         :created_by, :created_by_name, :previous_draft_id)
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

# Working on a correction is working on the draft it corrects.
_EXTEND_PREVIOUS: Final = text(
    """
    UPDATE staging.drafts
    SET expires_at = greatest(expires_at, now() + interval '30 days')
    WHERE draft_id = :previous_draft_id AND created_by = :created_by AND expires_at > now()
    """
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
      AND status IN ('uploaded', 'validated', 'committed') AND expires_at > now()
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

_SELECT_REASON_COUNTS: Final = text(
    """
    SELECT rule_id, column_key, reason_id, rows_affected
    FROM staging.draft_reason_counts
    WHERE draft_id = :draft_id
    ORDER BY rule_id, column_key, rows_affected DESC, reason_id
    """
)

_CLEAR_RESULTS: Final = (
    text("DELETE FROM staging.draft_rows WHERE draft_id = :draft_id"),
    text("DELETE FROM staging.draft_issues WHERE draft_id = :draft_id"),
    text("DELETE FROM staging.draft_rule_counts WHERE draft_id = :draft_id"),
    text("DELETE FROM staging.draft_reason_counts WHERE draft_id = :draft_id"),
)

_INSERT_COUNT: Final = text(
    """
    INSERT INTO staging.draft_rule_counts
        (draft_id, rule_id, column_key, severity, rows_affected, issues_listed)
    VALUES (:draft_id, :rule_id, :column_key, :severity, :rows_affected, :issues_listed)
    """
)

_INSERT_REASON_COUNT: Final = text(
    """
    INSERT INTO staging.draft_reason_counts
        (draft_id, rule_id, column_key, reason_id, rows_affected)
    VALUES (:draft_id, :rule_id, :column_key, :reason_id, :rows_affected)
    """
)

_FINISH_VALIDATION: Final = text(
    f"""
    UPDATE staging.drafts
    SET status = 'validated', validated_at = now(), expires_at = now() + interval '30 days',
        sheet_name = :sheet_name, header_row = :header_row, rows_read = :rows_read,
        rows_with_errors = :rows_with_errors, rows_with_warnings = :rows_with_warnings,
        spec_digest = :spec_digest, sheet_names = CAST(:sheet_names AS text[]),
        window_start = :window_start, window_end = :window_end,
        export_info = CAST(:export_info AS jsonb),
        search_conditions = CAST(:search_conditions AS jsonb)
    WHERE draft_id = :draft_id
    RETURNING {_DRAFT_COLUMNS}
    """  # noqa: S608  # nosec B608
)

_ISSUE_COLUMNS: Final = """
    issue.issue_id, issue.rule_id, issue.severity, issue.source_row, issue.column_index,
    issue.column_key, issue.value, issue.params, issue.reasons, issue.reason_id,
    issue.match_key
"""

# The same issue in the other draft: same rule, column and row identity. A
# file's or a header's problem has no identity, hence IS NOT DISTINCT FROM.
_SAME_ISSUE_IN_PREVIOUS: Final = """
    SELECT 1 FROM staging.draft_issues AS other
    WHERE other.draft_id = CAST(:previous_id AS uuid)
      AND other.rule_id = issue.rule_id
      AND other.column_key IS NOT DISTINCT FROM issue.column_key
      AND other.match_key IS NOT DISTINCT FROM issue.match_key
"""
_SAME_ISSUE_IN_DRAFT: Final = """
    SELECT 1 FROM staging.draft_issues AS other
    WHERE other.draft_id = CAST(:draft_id AS uuid)
      AND other.rule_id = issue.rule_id
      AND other.column_key IS NOT DISTINCT FROM issue.column_key
      AND other.match_key IS NOT DISTINCT FROM issue.match_key
"""

# The counts of an issue's rule and column in its own draft and in the other.
# They are compared row by row when the other draft has none of them, or both
# list every one.
_COUNTS_IN_PREVIOUS: Final = """
    LEFT JOIN staging.draft_rule_counts AS mine
      ON mine.draft_id = issue.draft_id AND mine.rule_id = issue.rule_id
     AND mine.column_key = coalesce(issue.column_key, '')
    LEFT JOIN staging.draft_rule_counts AS theirs
      ON theirs.draft_id = CAST(:previous_id AS uuid) AND theirs.rule_id = issue.rule_id
     AND theirs.column_key = coalesce(issue.column_key, '')
"""
_COUNTS_IN_DRAFT: Final = """
    LEFT JOIN staging.draft_rule_counts AS mine
      ON mine.draft_id = issue.draft_id AND mine.rule_id = issue.rule_id
     AND mine.column_key = coalesce(issue.column_key, '')
    LEFT JOIN staging.draft_rule_counts AS theirs
      ON theirs.draft_id = CAST(:draft_id AS uuid) AND theirs.rule_id = issue.rule_id
     AND theirs.column_key = coalesce(issue.column_key, '')
"""
_BY_ROW: Final = """(
    theirs.rule_id IS NULL
    OR (mine.rows_affected = mine.issues_listed AND theirs.rows_affected = theirs.issues_listed)
)"""

# A draft's issues, each with what became of it against the draft it corrects:
# null without one, or where its rule and column are compared by count only.
_ISSUES_WITH_CHANGE: Final = f"""
    SELECT {_ISSUE_COLUMNS},
        CASE
            WHEN CAST(:previous_id AS uuid) IS NULL OR NOT {_BY_ROW} THEN NULL
            WHEN EXISTS ({_SAME_ISSUE_IN_PREVIOUS}) THEN 'remaining'
            ELSE 'new'
        END AS change
    FROM staging.draft_issues AS issue
    {_COUNTS_IN_PREVIOUS}
    WHERE issue.draft_id = :draft_id
"""  # noqa: S608  # nosec B608 - fixed fragments only

# A filter left empty matches every value: the casts tell PostgreSQL the type
# of a parameter that may be null.
_ISSUE_FILTER: Final = """
    WHERE (CAST(:severity AS text) IS NULL OR severity = CAST(:severity AS text))
      AND (CAST(:rule_id AS text) IS NULL OR rule_id = CAST(:rule_id AS text))
      AND (CAST(:column_key AS text) IS NULL OR column_key = CAST(:column_key AS text))
      AND (CAST(:reason_id AS text) IS NULL OR reason_id = CAST(:reason_id AS text))
      AND (CAST(:change AS text) IS NULL OR change = CAST(:change AS text))
"""

_ORDER: Final = "ORDER BY source_row NULLS FIRST, column_index NULLS FIRST, rule_id, issue_id"

_COUNT_ISSUES: Final = text(
    f"SELECT count(*) FROM ({_ISSUES_WITH_CHANGE}) AS listed {_ISSUE_FILTER}"  # noqa: S608  # nosec B608
)

_SELECT_ISSUES: Final = text(
    f"""
    SELECT * FROM ({_ISSUES_WITH_CHANGE}) AS listed
    {_ISSUE_FILTER}
    {_ORDER}
    OFFSET :offset LIMIT :limit
    """  # noqa: S608  # nosec B608
)

# For each rule and column, how many issues of a correction the earlier draft had.
_SELECT_MATCHED: Final = text(
    f"""
    SELECT issue.rule_id, coalesce(issue.column_key, '') AS column_key, count(*) AS matched
    FROM staging.draft_issues AS issue
    WHERE issue.draft_id = :draft_id
      AND EXISTS ({_SAME_ISSUE_IN_PREVIOUS})
    GROUP BY 1, 2
    """  # noqa: S608  # nosec B608
)

# The earlier draft's issues the correction no longer has.
_CLEARED: Final = f"""
    SELECT {_ISSUE_COLUMNS}
    FROM staging.draft_issues AS issue
    {_COUNTS_IN_DRAFT}
    WHERE issue.draft_id = :previous_id
      AND {_BY_ROW}
      AND NOT EXISTS ({_SAME_ISSUE_IN_DRAFT})
"""  # noqa: S608  # nosec B608 - fixed fragments only
_COUNT_CLEARED: Final = text(
    f"SELECT count(*) FROM ({_CLEARED}) AS cleared"  # noqa: S608  # nosec B608
)
_SELECT_CLEARED: Final = text(
    f"SELECT * FROM ({_CLEARED}) AS cleared {_ORDER} OFFSET :offset LIMIT :limit"  # noqa: S608  # nosec B608
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
    " column_key, value, params, reasons, reason_id, match_key) FROM STDIN"
)


def rule_counts(connection: Connection, draft_id: UUID) -> tuple[RuleCount, ...]:
    """Return how often each rule was broken in a draft."""
    rows = connection.execute(_SELECT_COUNTS, {"draft_id": draft_id}).all()
    reasons: dict[tuple[str, str], list[ReasonCount]] = {}
    for row in connection.execute(_SELECT_REASON_COUNTS, {"draft_id": draft_id}):
        reasons.setdefault((row.rule_id, row.column_key), []).append(
            ReasonCount(ReasonId(row.reason_id), int(row.rows_affected))
        )
    return tuple(
        RuleCount(
            RuleId(row.rule_id),
            IssueSeverity(row.severity),
            row.column_key or None,
            int(row.rows_affected),
            int(row.issues_listed),
            tuple(reasons.get((row.rule_id, row.column_key), ())),
        )
        for row in rows
    )


def _facts(row: Row[Any]) -> ExportFacts | None:
    """Return what a draft's workbook said about itself, if its validation kept it."""
    if row.sheet_names is None:
        return None
    return ExportFacts(
        sheet_names=tuple(row.sheet_names),
        window_start=row.window_start,
        window_end=row.window_end,
        export_info=dict(row.export_info or {}),
        search_conditions=tuple(dict(item) for item in (row.search_conditions or [])),
    )


def as_draft(row: Row[Any], counts: tuple[RuleCount, ...] = ()) -> Draft:
    """Build a draft from its row and, once validated, its counts."""
    validation = None
    if row.status in {DraftStatus.VALIDATED, DraftStatus.COMMITTED}:
        validation = ValidationSummary(
            sheet_name=row.sheet_name,
            header_row=row.header_row,
            rows_read=row.rows_read,
            rows_with_errors=row.rows_with_errors,
            rows_with_warnings=row.rows_with_warnings,
            counts=counts,
            spec_digest=row.spec_digest,
            facts=_facts(row),
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
        previous_draft_id=row.previous_draft_id,
        run_id=row.run_id,
        committed_at=row.committed_at,
    )


def _values(stored: Mapping[str, Any]) -> dict[str, Any]:
    """Return params read from JSON, with lists back as tuples."""
    return {
        name: tuple(value) if isinstance(value, list) else value for name, value in stored.items()
    }


def _as_issue(row: Row[Any]) -> Issue:
    """Build an issue from its row.

    An issue stored before revision 0008 has no reasons; it gets its rule's own.
    """
    rule = RuleId(row.rule_id)
    params = _values(dict(row.params))
    reasons = tuple(
        Reason(ReasonId(item["id"]), _values(item.get("params", {}))) for item in row.reasons
    )
    return Issue(
        rule,
        IssueSeverity(row.severity),
        row.source_row,
        row.column_index,
        row.column_key,
        row.value,
        params,
        reasons or ranked(rule, params, ()),
        row.match_key,
    )


def _reasons_json(reasons: Sequence[Reason]) -> str:
    """Return an issue's reasons as stored: an array of ``{"id", "params"}``."""
    return json.dumps(
        [{"id": reason.id.value, "params": dict(reason.params)} for reason in reasons],
        ensure_ascii=False,
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
                        _reasons_json(issue.reasons),
                        issue.reasons[0].id.value if issue.reasons else None,
                        issue.match_key,
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
            for by_reason in count.reasons:
                self._connection.execute(
                    _INSERT_REASON_COUNT,
                    {
                        "draft_id": self._draft_id,
                        "rule_id": count.rule.value,
                        "column_key": count.column_key or "",
                        "reason_id": by_reason.reason.value,
                        "rows_affected": by_reason.rows,
                    },
                )
        facts = summary.facts
        row = self._connection.execute(
            _FINISH_VALIDATION,
            {
                "draft_id": self._draft_id,
                "sheet_name": summary.sheet_name,
                "header_row": summary.header_row,
                "rows_read": summary.rows_read,
                "rows_with_errors": summary.rows_with_errors,
                "rows_with_warnings": summary.rows_with_warnings,
                "spec_digest": summary.spec_digest,
                "sheet_names": list(facts.sheet_names) if facts else None,
                "window_start": facts.window_start if facts else None,
                "window_end": facts.window_end if facts else None,
                "export_info": json.dumps(dict(facts.export_info), ensure_ascii=False)
                if facts
                else None,
                "search_conditions": json.dumps(
                    [dict(item) for item in facts.search_conditions], ensure_ascii=False
                )
                if facts
                else None,
            },
        ).one()
        return as_draft(row, summary.counts)


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
            "previous_draft_id": draft.previous_draft_id,
        }
        try:
            with self._engine.begin() as connection:
                row = connection.execute(_INSERT_DRAFT, parameters).one()
                if draft.previous_draft_id is not None:
                    connection.execute(_EXTEND_PREVIOUS, parameters)
        except SQLAlchemyError as exc:
            raise RepositoryError("could not record the draft", reason=str(exc)) from exc
        return as_draft(row)

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
        return as_draft(row)

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
            return as_draft(row, rule_counts(connection, draft_id))

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
            return as_draft(row, rule_counts(connection, row.draft_id))

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
                if row.status == DraftStatus.COMMITTED:
                    raise DraftBusyError("the draft was committed", draft_id=str(draft_id))
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
        self,
        draft_id: UUID,
        owner: str,
        wanted: IssueFilter,
        offset: int,
        limit: int,
        previous_id: UUID | None = None,
    ) -> IssuePage:
        """Return a page of a draft's listed issues.

        With ``previous_id``, each item says whether the earlier draft had it.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
        """
        parameters = {
            "draft_id": draft_id,
            "previous_id": previous_id,
            "severity": wanted.severity.value if wanted.severity else None,
            "rule_id": wanted.rule.value if wanted.rule else None,
            "column_key": wanted.column_key,
            "reason_id": wanted.reason.value if wanted.reason else None,
            "change": wanted.change.value if wanted.change else None,
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
        changes = (
            tuple(IssueChange(row.change) if row.change else None for row in rows)
            if previous_id is not None
            else ()
        )
        return IssuePage(
            total=int(total), items=tuple(_as_issue(row) for row in rows), changes=changes
        )

    def matched(self, draft_id: UUID, previous_id: UUID) -> Mapping[GroupKey, int]:
        """Return, per rule and column, how many issues of a draft the earlier one had."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_MATCHED, {"draft_id": draft_id, "previous_id": previous_id}
            ).all()
        return {(row.rule_id, row.column_key): int(row.matched) for row in rows}

    def cleared(self, draft_id: UUID, previous_id: UUID, offset: int, limit: int) -> IssuePage:
        """Return a page of the earlier draft's listed issues the correction no longer has."""
        parameters = {"draft_id": draft_id, "previous_id": previous_id}
        with self._engine.connect() as connection:
            total = connection.execute(_COUNT_CLEARED, parameters).scalar_one()
            rows = connection.execute(
                _SELECT_CLEARED, {**parameters, "offset": offset, "limit": limit}
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


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DRAFT_COLUMNS",
    "PostgresDraftRepository",
    "as_draft",
    "rule_counts",
]
