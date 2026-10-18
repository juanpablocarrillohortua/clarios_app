"""An in-memory stand-in for the draft repository, for tests without a database.

It keeps the port's promises the tests rely on: a draft is visible to its
owner only and only while it is live; a validation replaces earlier results
only if it ends without an error; a second validation of a draft that is being
validated is refused. ``tests/integration/test_drafts_end_to_end.py`` checks
the PostgreSQL adapter against the same promises.
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from energy.domain.drafts import DRAFT_RETENTION, Draft, DraftStatus
from energy.domain.exceptions import DraftBusyError, UnknownDraftError
from energy.ports.draft_repository import ExpiredDraft, IssuePage

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from energy.domain.draft_rules import Issue
    from energy.domain.drafts import ValidationSummary
    from energy.domain.landing import LandingRow
    from energy.ports.draft_repository import ArchivedFile, IssueFilter, NewDraft


def _now() -> datetime:
    return datetime.now(UTC)


class _Validation:
    """What one validation writes, kept apart until it ends well."""

    def __init__(self, store: InMemoryDraftRepository, draft_id: UUID) -> None:
        self._store = store
        self._draft_id = draft_id
        self.rows: list[LandingRow] = []
        self.issues: list[Issue] = []

    def stage_rows(self, rows: Iterable[LandingRow]) -> int:
        before = len(self.rows)
        self.rows.extend(rows)
        return len(self.rows) - before

    def add_issues(self, issues: Sequence[Issue]) -> None:
        self.issues.extend(issues)

    def finish(self, summary: ValidationSummary) -> Draft:
        draft = dataclasses.replace(
            self._store.drafts[self._draft_id],
            status=DraftStatus.VALIDATED,
            validated_at=_now(),
            expires_at=_now() + DRAFT_RETENTION,
            validation=summary,
        )
        self._store.pending[self._draft_id] = draft
        return draft


class InMemoryDraftRepository:
    """Drafts kept in dictionaries."""

    def __init__(self) -> None:
        self.drafts: dict[UUID, Draft] = {}
        self.rows: dict[UUID, list[LandingRow]] = {}
        self.issue_lists: dict[UUID, list[Issue]] = {}
        self.pending: dict[UUID, Draft] = {}
        self._validating: set[UUID] = set()

    def _live(self, draft_id: UUID, owner: str) -> Draft | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft.created_by != owner or draft.expires_at <= _now():
            return None
        return draft

    def create(self, draft: NewDraft) -> Draft:
        created = Draft(
            draft_id=uuid4(),
            dataset_key=draft.dataset_key,
            source_key=draft.source_key,
            plant_code=draft.plant_code,
            status=DraftStatus.RECEIVING,
            filename=draft.filename,
            declared_bytes=draft.declared_bytes,
            size_bytes=None,
            content_hash=None,
            bucket=None,
            object_key=None,
            created_by=draft.created_by,
            created_by_name=draft.created_by_name,
            created_at=_now(),
            uploaded_at=None,
            validated_at=None,
            expires_at=_now() + DRAFT_RETENTION,
            failure_code=None,
            validation=None,
        )
        self.drafts[created.draft_id] = created
        return created

    def mark_uploaded(self, draft_id: UUID, archived: ArchivedFile) -> Draft:
        draft = self.drafts[draft_id]
        if draft.status is not DraftStatus.RECEIVING:
            raise DraftBusyError("the draft is no longer receiving")
        uploaded = dataclasses.replace(
            draft,
            status=DraftStatus.UPLOADED,
            size_bytes=archived.size_bytes,
            content_hash=archived.content_hash,
            bucket=archived.bucket,
            object_key=archived.object_key,
            uploaded_at=_now(),
            expires_at=_now() + DRAFT_RETENTION,
        )
        self.drafts[draft_id] = uploaded
        return uploaded

    def mark_failed(self, draft_id: UUID, failure_code: str) -> None:
        draft = self.drafts[draft_id]
        if draft.status is DraftStatus.RECEIVING:
            self.drafts[draft_id] = dataclasses.replace(
                draft, status=DraftStatus.FAILED, failure_code=failure_code
            )

    def find(self, draft_id: UUID, owner: str) -> Draft | None:
        return self._live(draft_id, owner)

    def find_duplicate(
        self, owner: str, dataset_key: str, content_hash: str, excluding: UUID
    ) -> Draft | None:
        for draft in self.drafts.values():
            if (
                draft.draft_id != excluding
                and draft.created_by == owner
                and draft.dataset_key == dataset_key
                and draft.content_hash == content_hash
                and draft.status in {DraftStatus.UPLOADED, DraftStatus.VALIDATED}
                and draft.expires_at > _now()
            ):
                return draft
        return None

    @contextmanager
    def validation(self, draft_id: UUID, owner: str) -> Iterator[_Validation]:
        draft = self._live(draft_id, owner)
        if draft is None:
            raise UnknownDraftError("no draft of yours has that ID")
        if draft_id in self._validating:
            raise DraftBusyError("another validation of this draft is running")
        if draft.status not in {DraftStatus.UPLOADED, DraftStatus.VALIDATED}:
            raise DraftBusyError("the draft's file has not arrived")
        self._validating.add(draft_id)
        writing = _Validation(self, draft_id)
        try:
            yield writing
        finally:
            self._validating.discard(draft_id)
        # Reached only when the block ended without an error: commit.
        self.drafts[draft_id] = self.pending.pop(draft_id)
        self.rows[draft_id] = writing.rows
        self.issue_lists[draft_id] = writing.issues

    def issues(
        self, draft_id: UUID, owner: str, wanted: IssueFilter, offset: int, limit: int
    ) -> IssuePage:
        if self._live(draft_id, owner) is None:
            raise UnknownDraftError("no draft of yours has that ID")
        matching = [
            issue
            for issue in self.issue_lists.get(draft_id, [])
            if (wanted.severity is None or issue.severity == wanted.severity)
            and (wanted.rule is None or issue.rule == wanted.rule)
            and (wanted.column_key is None or issue.column_key == wanted.column_key)
        ]
        matching.sort(
            key=lambda issue: (
                -1 if issue.source_row is None else issue.source_row,
                -1 if issue.column_index is None else issue.column_index,
                issue.rule.value,
            )
        )
        return IssuePage(total=len(matching), items=tuple(matching[offset : offset + limit]))

    def expired(self, now: datetime, only: UUID | None = None) -> tuple[ExpiredDraft, ...]:
        def gone(draft: Draft) -> bool:
            if only is not None:
                return draft.draft_id == only
            unfinished = draft.status in {DraftStatus.RECEIVING, DraftStatus.FAILED}
            return draft.expires_at <= now or (
                unfinished and draft.created_at <= now - timedelta(days=1)
            )

        return tuple(
            ExpiredDraft(draft.draft_id, draft.bucket, draft.object_key, draft.content_hash)
            for draft in self.drafts.values()
            if gone(draft)
        )

    def references(self, bucket: str, object_key: str, excluding: Sequence[UUID]) -> bool:
        return any(
            draft.bucket == bucket and draft.object_key == object_key
            for draft in self.drafts.values()
            if draft.draft_id not in excluding
        )

    def remove(self, draft_ids: Sequence[UUID]) -> int:
        removed = 0
        for draft_id in draft_ids:
            if self.drafts.pop(draft_id, None) is not None:
                removed += 1
            self.rows.pop(draft_id, None)
            self.issue_lists.pop(draft_id, None)
        return removed

    def expire(self, draft_id: UUID, days_ago: int = 1) -> None:
        """Make a draft expired, as if its time had passed."""
        draft = self.drafts[draft_id]
        past = _now() - timedelta(days=days_ago)
        self.drafts[draft_id] = dataclasses.replace(draft, expires_at=past, created_at=past)

    def payloads(self, draft_id: UUID) -> list[dict[str, str]]:
        """Return the staged payloads of a draft, decoded."""
        return [json.loads(row.payload_json) for row in self.rows.get(draft_id, [])]


__all__ = ["InMemoryDraftRepository"]
