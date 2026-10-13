"""Excel and CSV upload endpoints feeding the Bronze layer.

Layer: api

Responsibility:
    Accepts spreadsheet uploads from plant staff, stores the exact bytes in
    object storage, and registers an ingestion run. The handler never parses the
    workbook inline: parsing is slow and failure-prone, and belongs to the
    Prefect flow it triggers, so the caller gets an immediate run id and polls
    :mod:`energy.api.routers.runs` for the outcome. Nothing is written to Silver
    or Gold from here.

    Every route here needs the permission ``loads:write``. It is attached where
    the router is mounted, in :mod:`energy.api.main`.

Depends on:
    ``energy.api.deps``, ``energy.api.schemas``,
    ``energy.services.upload_service``, ``energy.ports.object_storage``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile, status

from energy.api.deps import SessionDep, SettingsDep
from energy.api.schemas.responses import UploadAcceptedResponse, UploadHistoryResponse

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", summary="Upload a workbook for ingestion", status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    session: SessionDep,
    settings: SettingsDep,
    file: UploadFile = File(description="Excel or CSV file to ingest."),
    plant_code: str = Form(description="Plant the file belongs to."),
    source_key: str = Form(description="Source key as defined in config/sources.yaml."),
) -> UploadAcceptedResponse:
    """Accept a file, archive it, and schedule its ingestion.

    The file is hashed first. If the hash is already present in Bronze the
    upload is acknowledged as a duplicate and no run is started, which makes a
    double-click or a retried request harmless.

    Args:
        session: injected database session.
        settings: injected process configuration.
        file: the uploaded workbook or CSV.
        plant_code: which plant the data belongs to.
        source_key: which configured source this file represents.

    Returns:
        An :class:`~energy.api.schemas.responses.UploadAcceptedResponse` with the
        run id to poll, the content hash, and whether it was a duplicate.

    Raises:
        UnsupportedFileFormatError: if the file is not a readable format,
            surfaced as HTTP 415.
        UnknownSourceError: if ``source_key`` is not configured, HTTP 400.
        EntityNotFoundError: if ``plant_code`` is unknown, HTTP 404.
    """
    raise NotImplementedError("Phase 3: workbook upload and run scheduling")


@router.get("", summary="List recent uploads", status_code=status.HTTP_200_OK)
async def list_uploads(
    session: SessionDep,
    plant_code: str | None = None,
    limit: int = 50,
) -> UploadHistoryResponse:
    """List recently uploaded files and how their ingestion ended.

    Args:
        session: injected database session.
        plant_code: restrict to one plant; ``None`` lists every plant.
        limit: maximum entries to return, newest first.

    Returns:
        An :class:`~energy.api.schemas.responses.UploadHistoryResponse` with one
        entry per upload, including its run outcome and quarantined row count.
    """
    raise NotImplementedError("Phase 3: upload history listing")


__all__ = ["router"]
