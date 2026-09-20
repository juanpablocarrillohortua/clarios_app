"""Application service for generating operational and management reports.

Layer: services

Responsibility:
    Assembles periodic reports - monthly plant performance, variance against
    target, data-quality summaries - from Gold, renders them, and archives the
    artefact in object storage so the exact document that was circulated can be
    retrieved later. Reports read Gold only. Recomputing figures at render time
    would let a report disagree with the dashboard that prompted it, so any
    number a report shows must already exist as a stored KPI row.

Depends on:
    ``energy.domain``, ``energy.ports.kpi_repository``,
    ``energy.ports.object_storage``, ``energy.ports.notifier``, ``energy.core``.

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from energy.domain.value_objects import TimeWindow
    from energy.ports.kpi_repository import KPIRepository
    from energy.ports.notifier import Notifier
    from energy.ports.object_storage import ObjectStorage

ReportFormat = Literal["pdf", "xlsx", "html"]


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    """A rendered and archived report.

    Attributes:
        report_id: identifier of this rendering.
        title: report title, in English.
        report_format: the rendered format.
        object_key: where the artefact was archived.
        download_url: presigned URL for retrieving it.
        expires_at: when that URL stops working.
        covered_window: the period the report covers.
        coverage: the minimum data coverage across the figures it contains, so a
            reader can tell a complete month from a partially observed one.
    """

    report_id: str
    title: str
    report_format: ReportFormat
    object_key: str
    download_url: str
    expires_at: datetime
    covered_window: TimeWindow
    coverage: float


class ReportService:
    """Generates, archives, and distributes periodic reports.

    Args:
        kpis: port for reading Gold values.
        storage: object storage port for archiving artefacts.
        notifier: port for distributing finished reports.
        reports_bucket: bucket artefacts are written to.
    """

    def __init__(
        self,
        kpis: KPIRepository,
        storage: ObjectStorage,
        notifier: Notifier,
        reports_bucket: str,
    ) -> None:
        raise NotImplementedError("Phase 5: report service construction")

    def monthly_plant_report(
        self,
        plant_code: str,
        window: TimeWindow,
        report_format: ReportFormat = "pdf",
    ) -> GeneratedReport:
        """Generate the monthly performance report for one plant.

        Args:
            plant_code: the plant to report on.
            window: the month to cover, aligned to month boundaries in UTC.
            report_format: the output format.

        Returns:
            The :class:`GeneratedReport` describing the archived artefact.

        Raises:
            EntityNotFoundError: if the plant code is unknown.
            InvalidTimeWindowError: if the window is not a whole month.
        """
        raise NotImplementedError("Phase 5: monthly plant report")

    def data_quality_report(self, window: TimeWindow) -> GeneratedReport:
        """Generate the cross-plant data-quality summary.

        Reports coverage, quarantined row counts by rule, source availability,
        and consolidation conflicts, so the platform's own reliability is
        visible rather than assumed.

        Args:
            window: the period to cover.

        Returns:
            The archived data-quality report.
        """
        raise NotImplementedError("Phase 5: data quality report")

    def distribute(self, report: GeneratedReport, recipients: Sequence[str]) -> int:
        """Send a finished report to its recipients.

        Args:
            report: the report to distribute.
            recipients: addresses or channels to send to.

        Returns:
            How many deliveries the transport accepted. Delivery failure is
            logged and reported, never raised: an undelivered report must not
            roll back its generation.
        """
        raise NotImplementedError("Phase 5: report distribution")


__all__ = ["GeneratedReport", "ReportFormat", "ReportService"]
