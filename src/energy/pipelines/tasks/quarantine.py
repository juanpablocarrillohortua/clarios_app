"""Task handling rows rejected on the way into Silver.

Layer: pipelines

Responsibility:
    Persists rejected rows to ``silver.quarantine`` with the rule they broke and
    enough provenance to locate the offending value in its original source file,
    then decides whether the rejection rate warrants alerting a human. The
    guiding principle is that rejected data is *evidence*, not garbage: a row
    here is the starting point of a conversation with a plant about their data,
    so it retains the raw values, the parsed values, and the rule, rather than
    just a count. Nothing in this module discards a row.

Depends on:
    ``energy.ports.notifier``, ``energy.schemas.silver``, ``energy.domain``,
    ``energy.core``, ``polars``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    import polars as pl
    from sqlalchemy.orm import Session

    from energy.ports.notifier import Notifier


@dataclass(frozen=True, slots=True)
class QuarantineSummary:
    """What a run quarantined and whether it warrants attention.

    Attributes:
        run_id: the run being summarised.
        rows_quarantined: how many rows were rejected.
        rows_total: how many rows the run processed.
        rejection_rate: rejected divided by total.
        by_rule: rejected row count per failing rule.
        by_source: rejected row count per source system.
        alert_raised: whether a notification was sent.
    """

    run_id: UUID
    rows_quarantined: int
    rows_total: int
    rejection_rate: float
    by_rule: dict[str, int]
    by_source: dict[str, int]
    alert_raised: bool


def persist_quarantine(
    session: Session,
    rejected: pl.DataFrame,
    run_id: UUID,
) -> int:
    """Write rejected rows to ``silver.quarantine``.

    Args:
        session: an open session; the caller owns the transaction so quarantine
            rows and the accepted Silver rows commit together or not at all.
        rejected: rows shaped for the quarantine contract.
        run_id: the run that rejected them.

    Returns:
        The number of rows written.

    Raises:
        RepositoryError: if the write fails.
    """
    raise NotImplementedError("Phase 3: quarantine persistence")


def summarise(
    rejected: pl.DataFrame,
    rows_total: int,
    run_id: UUID,
) -> QuarantineSummary:
    """Summarise a run's rejections by rule and source.

    Args:
        rejected: the quarantined rows.
        rows_total: how many rows the run processed in total.
        run_id: the run being summarised.

    Returns:
        A :class:`QuarantineSummary` with ``alert_raised`` not yet set.
    """
    raise NotImplementedError("Phase 3: quarantine summarisation")


def alert_if_needed(
    summary: QuarantineSummary,
    notifier: Notifier,
    warn_threshold: float = 0.01,
    critical_threshold: float = 0.05,
) -> QuarantineSummary:
    """Notify a human when the rejection rate crosses a threshold.

    Deduplicated per plant and rule so a source that has been malformed for a
    week produces one alert rather than seven.

    Args:
        summary: the run's quarantine summary.
        notifier: the notifier port to send through.
        warn_threshold: rejection rate above which a warning is sent.
        critical_threshold: rejection rate above which a critical alert is sent.

    Returns:
        The summary with ``alert_raised`` set. Never raises: a failed
        notification must not fail a run that otherwise succeeded.
    """
    raise NotImplementedError("Phase 3: quarantine alerting")


def resolve(session: Session, quarantine_ids: list[UUID], resolution: str) -> int:
    """Mark quarantined rows as dealt with.

    Args:
        session: an open session.
        quarantine_ids: the rows to resolve.
        resolution: what was done, in English, for the audit trail.

    Returns:
        The number of rows marked resolved.

    Raises:
        EntityNotFoundError: if any id is unknown.
    """
    raise NotImplementedError("Phase 3: quarantine resolution")


__all__ = ["QuarantineSummary", "alert_if_needed", "persist_quarantine", "resolve", "summarise"]
