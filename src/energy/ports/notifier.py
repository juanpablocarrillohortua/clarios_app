"""Port for outbound operational notifications.

Layer: ports

Responsibility:
    Declares how the platform tells a human that something needs attention: an
    ingestion run failed, a source stopped delivering, a KPI breached a
    threshold, or an anomaly was detected. The protocol is deliberately
    transport-agnostic - email, Slack, Teams, or a webhook are adapter concerns.
    Notifications are strictly side-channel: no pipeline decision, retry, or
    Gold value may depend on whether delivery succeeded, so a failed notification
    is logged and swallowed rather than aborting a run.

Depends on:
    ``energy.domain``, standard library typing only.

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID


class Severity(StrEnum):
    """How urgently a notification needs human attention."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Notification:
    """One message to deliver to one or more recipients.

    Attributes:
        subject: short single-line summary, in English.
        body: the full message. Plain text; adapters may render it into their
            own markup.
        severity: how urgent this is. Adapters map it onto their own channels,
            for example routing ``CRITICAL`` to an on-call rotation.
        dedup_key: stable key identifying the *condition*, not the occurrence.
            Adapters must collapse repeats within their configured window, so a
            source that has been down for six hours produces one alert rather
            than a hundred.
        recipients: explicit addresses or channel names; empty means the
            adapter's configured default audience.
        context: structured detail attached to the message, for example
            ``{"plant_code": "MTY-01", "run_id": "..."}``.
        occurred_at: when the underlying condition was detected, UTC.
        run_id: the pipeline run that raised it, when applicable.
    """

    subject: str
    body: str
    severity: Severity
    dedup_key: str
    recipients: Sequence[str]
    context: Mapping[str, object]
    occurred_at: datetime
    run_id: UUID | None = None


@runtime_checkable
class Notifier(Protocol):
    """Interface for delivering operational notifications."""

    def send(self, notification: Notification) -> bool:
        """Deliver one notification.

        Must never raise. A transport failure is logged and reported through the
        return value, because a pipeline that crashes on an unreachable mail
        server has turned a warning into an outage.

        Args:
            notification: the message to deliver.

        Returns:
            ``True`` when the transport accepted the message, ``False`` when
            delivery failed or the message was suppressed as a duplicate.
        """
        ...

    def send_batch(self, notifications: Sequence[Notification]) -> int:
        """Deliver several notifications, coalescing where the transport allows.

        A nightly run that fails for six plants should produce one digest, not
        six separate messages.

        Args:
            notifications: the messages to deliver.

        Returns:
            How many were accepted by the transport.
        """
        ...

    def health_check(self) -> bool:
        """Confirm the transport is reachable and credentials are valid.

        Returns:
            ``True`` when the transport answers. Never raises.
        """
        ...


__all__ = ["Notification", "Notifier", "Severity"]
