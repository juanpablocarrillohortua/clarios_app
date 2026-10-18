"""Committing a draft: when it may land in Bronze, and what its run records.

Layer: domain

Responsibility:
    Holds the rules of increment 7 that need no database:

    * whether a draft may be committed, and if not why
      (:func:`blocked_by`): errors block, warnings do not (question 6 of the
      frontend plan), and a draft without rows has nothing to load;
    * the digest of a validation (:func:`validation_digest`). The person
      confirms a draft as they saw it: the commit carries the digest, and a
      validation that changed in between refuses it, so nobody accepts
      warnings they were never shown;
    * the warnings a person must accept, and how the acceptance is recorded;
    * the run's ``parameters`` (:func:`run_parameters`): the keys the
      command-line loader writes, with the same meaning, plus the web app's
      own: where the rows came from, the validation, the warnings accepted,
      and for a snapshot its difference from the version it replaced.

    Who confirmed is never taken from what a client sends: it is the draft's
    owner, the token's subject recorded at upload.

    The module is pure: standard library only.

Depends on:
    ``energy.domain``, standard library only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import TYPE_CHECKING, Final

from energy.domain.dataset_spec import DatasetKind
from energy.domain.draft_rules import IssueSeverity
from energy.domain.drafts import DraftStatus

if TYPE_CHECKING:
    from uuid import UUID

    from energy.domain.dataset_spec import DatasetSpec, RemovedRows
    from energy.domain.draft_rules import RuleCount
    from energy.domain.drafts import Draft, ValidationSummary

#: How a load entered through the web app records where its rows came from.
INPUT_MODE_UPLOAD: Final = "upload"


class CommitBlock(StrEnum):
    """Why a draft cannot be committed now."""

    NOT_VALIDATED = "not_validated"
    ERRORS = "errors"
    NOTHING_TO_LOAD = "nothing_to_load"
    ALREADY_LOADED = "already_loaded"
    COMMITTED = "committed"


class RowChange(StrEnum):
    """How a row of a snapshot's new version differs from the current one."""

    ADDED = "added"
    CHANGED = "changed"
    REMOVED = "removed"


class LoadOrigin(StrEnum):
    """Which way a load reached Bronze: the web app, or the command line."""

    APP = "app"
    CLI = "cli"


@dataclass(frozen=True, slots=True)
class DifferenceCounts:
    """How a snapshot's new version differs from the current one, by natural key.

    Attributes:
        base_run_id: the run of the current version; ``None`` when there is
            none, and every row is added.
        added: keys only the new version has.
        changed: keys both have, with different values.
        removed: keys only the current version has.
        unchanged: keys both have, with the same values.
    """

    base_run_id: UUID | None
    added: int
    changed: int
    removed: int
    unchanged: int


def blocked_by(draft: Draft, already_loaded: bool = False) -> CommitBlock | None:
    """Return why a draft cannot be committed, or ``None`` when it can.

    Args:
        draft: the draft.
        already_loaded: whether a completed run already landed its file's
            sheet for its dataset.

    Returns:
        The first reason that applies, in the order a person would fix them.
    """
    if draft.status is DraftStatus.COMMITTED:
        return CommitBlock.COMMITTED
    summary = draft.validation
    if draft.status is not DraftStatus.VALIDATED or summary is None:
        return CommitBlock.NOT_VALIDATED
    if summary.blocking:
        return CommitBlock.ERRORS
    if summary.rows_read == 0:
        return CommitBlock.NOTHING_TO_LOAD
    if already_loaded:
        return CommitBlock.ALREADY_LOADED
    return None


def warnings_of(summary: ValidationSummary) -> tuple[RuleCount, ...]:
    """Return the warnings a person must accept before the draft lands."""
    return tuple(count for count in summary.counts if count.severity is IssueSeverity.WARNING)


def validation_digest(summary: ValidationSummary) -> str:
    """Return the SHA-256 of what a validation found.

    Two validations with the same result give the same digest, so validating
    a file again does not undo a person's acceptance; any change of a count
    does.
    """
    canonical = json.dumps(
        {
            "spec": summary.spec_digest,
            "sheet": summary.sheet_name,
            "header_row": summary.header_row,
            "rows_read": summary.rows_read,
            "rows_with_errors": summary.rows_with_errors,
            "rows_with_warnings": summary.rows_with_warnings,
            "counts": sorted(
                [count.rule.value, count.severity.value, count.column_key or "", count.rows]
                for count in summary.counts
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def flow_name(filename: str) -> str:
    """Return the name a run is recorded under: the file's stem, as the loader does."""
    return PurePath(filename).stem or filename


def _counts(counts: tuple[RuleCount, ...]) -> list[dict[str, object]]:
    """Return counts by rule and column as a run records them."""
    return [
        {
            "rule": count.rule.value,
            "severity": count.severity.value,
            "column_key": count.column_key,
            "rows": count.rows,
        }
        for count in counts
    ]


def run_parameters(
    draft: Draft,
    spec: DatasetSpec,
    accept_warnings: bool,
    difference: DifferenceCounts | None,
) -> dict[str, object]:
    """Return the ``parameters`` of the run that commits a draft.

    The first keys are the command-line loader's own
    (``BronzeLoader._new_run``), with the same meaning, so a history reads
    both kinds of run the same way. The rest are the web app's.

    Args:
        draft: a validated draft, with what its workbook says about itself.
        spec: its dataset spec.
        accept_warnings: whether its owner accepted its warnings.
        difference: for a snapshot, how it differs from the current version.

    Returns:
        A JSON-serialisable mapping.

    Raises:
        ValueError: if the draft was not validated.
    """
    summary = draft.validation
    if summary is None or draft.validated_at is None:
        msg = "a draft is committed only once validated"
        raise ValueError(msg)
    facts = summary.facts
    warnings = warnings_of(summary)
    accepted = (
        {"by": draft.created_by, "warnings": _counts(warnings)}
        if warnings and accept_warnings
        else None
    )
    parameters: dict[str, object] = {
        "filename": draft.filename,
        "content_hash": draft.content_hash,
        "sheets": [
            {
                "name": spec.bronze.sheet,
                "dataset_key": spec.bronze.dataset_key,
                "header_row": spec.bronze.header_row,
            }
        ],
        "plant_code": draft.plant_code,
        "force": False,
        # Nobody states a period: it is read from the rows, as the loader does.
        "window_start": None,
        "window_end": None,
        "uploaded_by": draft.created_by,
        "info": dict(facts.export_info) if facts else {},
        "input_mode": INPUT_MODE_UPLOAD,
        "dataset_key": spec.key,
        "draft_id": str(draft.draft_id),
        "previous_draft_id": str(draft.previous_draft_id) if draft.previous_draft_id else None,
        "spec_digest": summary.spec_digest,
        "uploaded_by_name": draft.created_by_name,
        "validation": {
            "validated_at": draft.validated_at.isoformat(),
            "digest": validation_digest(summary),
            "rows_read": summary.rows_read,
            "rows_with_errors": summary.rows_with_errors,
            "rows_with_warnings": summary.rows_with_warnings,
            "counts": _counts(summary.counts),
            "accepted": accepted,
        },
    }
    if spec.kind is DatasetKind.SNAPSHOT and difference is not None:
        parameters["difference"] = _difference(difference, spec.removed_rows)
    return parameters


def _difference(counts: DifferenceCounts, removed_rows: RemovedRows | None) -> dict[str, object]:
    """Return a snapshot's difference as its run records it."""
    return {
        "base_run_id": str(counts.base_run_id) if counts.base_run_id else None,
        "added": counts.added,
        "changed": counts.changed,
        "removed": counts.removed,
        "unchanged": counts.unchanged,
        "removed_rows": removed_rows.value if removed_rows else None,
    }


__all__ = [
    "INPUT_MODE_UPLOAD",
    "CommitBlock",
    "DifferenceCounts",
    "LoadOrigin",
    "RowChange",
    "blocked_by",
    "flow_name",
    "run_parameters",
    "validation_digest",
    "warnings_of",
]
