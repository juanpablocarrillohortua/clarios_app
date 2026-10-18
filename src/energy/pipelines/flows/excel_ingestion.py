"""Flow ingesting an uploaded workbook into Bronze and promoting it.

Layer: pipelines

Responsibility:
    Triggered once an energy workbook has been archived. The route that would
    receive it is not built: the web app's ``/api/drafts`` takes EQUS data
    only, and the ``/api/uploads`` placeholder was removed in increment 7.
    Parses the workbook, lands its rows in Bronze, validates them into Silver,
    and recomputes the Gold periods the file touched. Its distinguishing concern
    is *scope discovery*: the flow does not know in advance which periods the
    workbook covers, so it derives the affected window from the parsed rows and
    recomputes exactly those periods - not the whole month, which would be
    wasteful, and not just the newest day, which would miss backdated
    corrections.

Depends on:
    ``energy.pipelines.tasks``, ``energy.adapters.excel_source``,
    ``energy.services.upload_service``, ``energy.core``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from energy.domain.value_objects import TimeWindow


def excel_ingestion(
    run_id: UUID,
    object_key: str,
    plant_code: str,
    source_key: str,
) -> dict[str, object]:
    """Parse an archived workbook and promote its contents through the layers.

    Args:
        run_id: the ingestion run registered by the upload service.
        object_key: object-storage key of the archived original.
        plant_code: the plant the file belongs to.
        source_key: the configured source the file represents.

    Returns:
        Rows landed per layer, rows quarantined, and the window the file covered.

    Raises:
        UnsupportedFileFormatError: if the workbook cannot be parsed.
        SchemaValidationError: if the sheet layout no longer matches the
            configured one, which means the workbook template changed.
        QuarantinedRowsError: if the share of rejected rows exceeds the
            configured tolerance, indicating the file itself is wrong rather
            than a few of its cells.
    """
    raise NotImplementedError("Phase 3: Excel ingestion flow")


def affected_window(run_id: UUID) -> TimeWindow:
    """Derive the period a just-ingested file covers.

    Args:
        run_id: the ingestion run whose Bronze rows to inspect.

    Returns:
        The smallest canonical-grid-aligned window containing every timestamp the
        run landed, which is the exact scope of Gold that must be recomputed.

    Raises:
        EntityNotFoundError: if the run wrote no rows.
    """
    raise NotImplementedError("Phase 3: affected window derivation")


__all__ = ["affected_window", "excel_ingestion"]
