"""Port for the drafts kept in schema ``staging``.

Layer: ports

Responsibility:
    Declares how the application keeps a draft while it is prepared: the
    draft itself, the rows its validation staged, the issues found and how
    often each rule was broken. It is a working area. Nothing here writes to
    Bronze, and a draft is visible only to the person who made it, so every
    read takes the owner and answers as if a draft of anyone else did not
    exist.

    A validation replaces the previous results of a draft in one transaction,
    holding a lock on the draft: a second validation of the same draft is
    refused rather than queued, and a validation that fails leaves the old
    results in place.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from contextlib import AbstractContextManager
    from datetime import datetime
    from uuid import UUID

    from energy.domain.draft_rules import Issue, IssueSeverity, RuleId
    from energy.domain.drafts import Draft, ValidationSummary
    from energy.domain.landing import LandingRow


@dataclass(frozen=True, slots=True)
class NewDraft:
    """A draft whose bytes are about to arrive.

    Attributes:
        dataset_key: the dataset spec it is for.
        source_key: the Bronze source of that spec.
        plant_code: the plant the data belongs to.
        filename: the cleaned file name, as data.
        content_type: what the client declared.
        declared_bytes: the size the upload announced.
        created_by: the person, as the identity provider's subject.
        created_by_name: their name, for display.
    """

    dataset_key: str
    source_key: str
    plant_code: str
    filename: str
    content_type: str
    declared_bytes: int
    created_by: str
    created_by_name: str | None


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    """Where a draft's original was archived.

    Attributes:
        bucket: the bucket.
        object_key: the key, which carries the file's hash.
        size_bytes: the file's size.
        content_hash: its SHA-256.
    """

    bucket: str
    object_key: str
    size_bytes: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class IssueFilter:
    """Which issues of a draft to list. ``None`` means any.

    Attributes:
        severity: one severity.
        rule: one rule.
        column_key: one column, by payload key.
    """

    severity: IssueSeverity | None = None
    rule: RuleId | None = None
    column_key: str | None = None


@dataclass(frozen=True, slots=True)
class IssuePage:
    """A page of a draft's listed issues.

    Attributes:
        total: how many listed issues match the filter.
        items: this page of them, ordered by row, then column, with the
            file's and the header's own issues first.
    """

    total: int
    items: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class ExpiredDraft:
    """A draft the purge may remove.

    Attributes:
        draft_id: the draft.
        bucket: where its original is archived; ``None`` if it never arrived.
        object_key: its key there.
        content_hash: its hash.
    """

    draft_id: UUID
    bucket: str | None
    object_key: str | None
    content_hash: str | None


@runtime_checkable
class DraftValidation(Protocol):
    """The writes of one validation, all in one transaction."""

    def stage_rows(self, rows: Iterable[LandingRow]) -> int:
        """Stage rows as Bronze would land them; return how many."""
        ...

    def add_issues(self, issues: Sequence[Issue]) -> None:
        """Store listed issues."""
        ...

    def finish(self, summary: ValidationSummary) -> Draft:
        """Record what the validation found and return the validated draft."""
        ...


@runtime_checkable
class DraftRepository(Protocol):
    """Interface for keeping drafts."""

    def create(self, draft: NewDraft) -> Draft:
        """Record a draft in state ``receiving`` and commit it."""
        ...

    def mark_uploaded(self, draft_id: UUID, archived: ArchivedFile) -> Draft:
        """Record where the original was archived; the draft is ``uploaded``."""
        ...

    def mark_failed(self, draft_id: UUID, failure_code: str) -> None:
        """Record that the upload failed, and why."""
        ...

    def find(self, draft_id: UUID, owner: str) -> Draft | None:
        """Return a live draft of ``owner``, or ``None``.

        A draft past its expiry, or of someone else, is ``None``.
        """
        ...

    def find_duplicate(
        self, owner: str, dataset_key: str, content_hash: str, excluding: UUID
    ) -> Draft | None:
        """Return a live draft of ``owner`` holding the same bytes for the same dataset."""
        ...

    def validation(self, draft_id: UUID, owner: str) -> AbstractContextManager[DraftValidation]:
        """Open a validation of a draft, replacing its previous results.

        The draft is locked for the duration; leaving the block without an
        error commits, an error rolls everything back.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
            DraftBusyError: if another validation of it is running.
        """
        ...

    def issues(
        self, draft_id: UUID, owner: str, wanted: IssueFilter, offset: int, limit: int
    ) -> IssuePage:
        """Return a page of a draft's listed issues.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
        """
        ...

    def expired(self, now: datetime, only: UUID | None = None) -> tuple[ExpiredDraft, ...]:
        """Return the drafts past their expiry, and uploads that never finished.

        With ``only``, return that one draft, expired or not, whoever made it.
        """
        ...

    def references(self, bucket: str, object_key: str, excluding: Sequence[UUID]) -> bool:
        """Return whether a draft not in ``excluding`` points at that object."""
        ...

    def remove(self, draft_ids: Sequence[UUID]) -> int:
        """Remove drafts and everything staged for them; return how many."""
        ...


__all__ = [
    "ArchivedFile",
    "DraftRepository",
    "DraftValidation",
    "ExpiredDraft",
    "IssueFilter",
    "IssuePage",
    "NewDraft",
]
