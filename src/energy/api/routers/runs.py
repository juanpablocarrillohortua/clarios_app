"""Pipeline run status and history endpoints.

Layer: api

Responsibility:
    Makes Prefect flow runs observable without giving every user access to the
    Prefect UI. Reports what ran, over which window, how many rows reached each
    medallion layer, and how many were quarantined. Read-only by design except
    for the explicit retry endpoint: this router must never start an ad-hoc
    pipeline, because an unscheduled run competing with the nightly flow can
    produce overlapping writes to the same Gold period.

    Every route here needs the permission ``loads:write``. It is attached where
    the router is mounted, in :mod:`energy.api.main`.

Depends on:
    ``energy.api.deps``, ``energy.api.schemas``,
    ``energy.services.report_service``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from energy.api.deps import SessionDep
from energy.api.schemas.responses import RunDetailResponse, RunListResponse

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", summary="List pipeline runs", status_code=status.HTTP_200_OK)
async def list_runs(
    session: SessionDep,
    plant_code: str | None = None,
    state: str | None = Query(default=None, description="running, completed, or failed."),
    limit: int = 50,
) -> RunListResponse:
    """List recent pipeline runs, newest first.

    Args:
        session: injected database session.
        plant_code: restrict to one plant; ``None`` lists every plant.
        state: restrict to one run state; ``None`` lists every state.
        limit: maximum runs to return.

    Returns:
        A :class:`~energy.api.schemas.responses.RunListResponse` with one summary
        per run: flow name, window, state, duration, and row counts per layer.
    """
    raise NotImplementedError("Phase 3: run listing")


@router.get("/{run_id}", summary="Inspect one pipeline run", status_code=status.HTTP_200_OK)
async def get_run(session: SessionDep, run_id: UUID) -> RunDetailResponse:
    """Return full detail for one run, including per-task outcomes.

    Args:
        session: injected database session.
        run_id: the run to inspect.

    Returns:
        A :class:`~energy.api.schemas.responses.RunDetailResponse` with the task
        timeline, rows written per layer, quarantine summary, and the error
        message when the run failed.

    Raises:
        EntityNotFoundError: if the run id is unknown, surfaced as HTTP 404.
    """
    raise NotImplementedError("Phase 3: run detail lookup")


@router.get(
    "/{run_id}/quarantine",
    summary="List rows quarantined by a run",
    status_code=status.HTTP_200_OK,
)
async def get_run_quarantine(
    session: SessionDep,
    run_id: UUID,
    limit: int = 100,
) -> dict[str, object]:
    """Return the rows a run rejected and why.

    This is the endpoint a data steward uses to fix a source file: it shows the
    original values, the failed validation rule, and the Bronze provenance
    needed to locate the offending cell in the uploaded workbook.

    Args:
        session: injected database session.
        run_id: the run whose rejections to list.
        limit: maximum rows to return.

    Returns:
        The quarantined rows with their failure reasons and source coordinates.

    Raises:
        EntityNotFoundError: if the run id is unknown.
    """
    raise NotImplementedError("Phase 3: quarantine inspection endpoint")


@router.post("/{run_id}/retry", summary="Retry a failed run", status_code=status.HTTP_202_ACCEPTED)
async def retry_run(session: SessionDep, run_id: UUID) -> dict[str, object]:
    """Re-submit a failed run with its original parameters.

    Only failed runs may be retried. Re-running a successful one is refused
    because the Gold replacement it would perform is indistinguishable from a
    correction and would obscure the audit trail.

    Args:
        session: injected database session.
        run_id: the failed run to retry.

    Returns:
        The id of the newly scheduled run.

    Raises:
        EntityNotFoundError: if the run id is unknown, HTTP 404.
        PipelineError: if the run did not fail, surfaced as HTTP 409.
    """
    raise NotImplementedError("Phase 3: run retry endpoint")


__all__ = ["router"]
