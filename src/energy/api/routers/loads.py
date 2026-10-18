"""Loads: the history of what landed in Bronze, and each load in detail.

Layer: api

A load is a run of ``bronze.ingestion_runs``. The history lists every load,
whichever way it came: confirmed in the web app (increment 7) or run from the
command line. Who sees it is anyone with ``loads:write``, for the plants of
their scope; a load is evidence everyone who loads data may read.

- ``GET /loads`` lists them, filtered by dataset, state, person, origin,
  period covered and day started, sorted and paged.
- ``GET /loads/{run_id}`` returns one: its file, its sheets and the rows of
  each, and for a load from the web app what its validation found, the
  warnings accepted and, for a catalogue, how it differed from the version it
  replaced.
- ``GET /loads/{run_id}/original`` streams the file it came from, through the
  API: the archive is never exposed. Its hash is checked as it goes, and the
  last piece is held back until it matches, so a damaged file never arrives
  whole.

Every route needs ``loads:write``, attached where ``api/main.py`` mounts this
router. Names are display names only: a person's sign-in name is never served.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from energy.api.deps import DatasetCatalogueDep, LoadServiceDep, PrincipalDep
from energy.api.schemas.responses import (
    DifferenceCountsResponse,
    ErrorResponse,
    LoadDetailResponse,
    LoadReferenceResponse,
    LoadsPageResponse,
    LoadSummaryResponse,
    LoadValidationResponse,
    OriginalFileResponse,
    PersonResponse,
    RunCountResponse,
    RunState,
    SheetLoadedResponse,
)
from energy.core.logging import get_logger
from energy.domain.draft_commit import LoadOrigin
from energy.domain.exceptions import ContentHashMismatchError
from energy.ports.load_ledger import LoadFilter, LoadSort
from energy.services.load_service import target_of

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from io import BufferedReader

    from energy.ports.load_committer import LoadReference
    from energy.ports.load_ledger import ArchivedOriginal, LoadRecord, LoadSummary
    from energy.services.load_service import LoadView

logger = get_logger(__name__)

router = APIRouter(prefix="/loads", tags=["loads"])

#: The piece of an original read at a time while it is streamed.
STREAM_CHUNK_BYTES = 1024 * 1024

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No load of your plants."}
}


def load_reference(reference: LoadReference) -> LoadReferenceResponse:
    """Describe a completed load, as a confirmation or a dataset names it."""
    return LoadReferenceResponse(
        run_id=reference.run_id,
        origin=reference.origin.value,
        finished_at=reference.finished_at,
        filename=reference.filename,
        rows=reference.rows,
        uploaded_by_name=reference.uploaded_by_name,
        window_start=reference.window_start,
        window_end=reference.window_end,
    )


def _summary_fields(summary: LoadSummary, viewer: str) -> dict[str, Any]:
    """Return what a history shows of a load, for one viewer."""
    return {
        "run_id": summary.run_id,
        "origin": summary.origin.value,
        "state": summary.state,
        "source_key": summary.source_key,
        "dataset_key": summary.dataset_key,
        "sheets": [
            SheetLoadedResponse(name=sheet.name, dataset_key=sheet.dataset_key, rows=sheet.rows)
            for sheet in summary.sheets
        ],
        "filename": summary.filename,
        "rows": summary.rows,
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "window_start": summary.window_start,
        "window_end": summary.window_end,
        "uploaded_by_name": summary.uploaded_by_name,
        "mine": summary.origin is LoadOrigin.APP and summary.uploaded_by == viewer,
        "warnings_accepted": summary.warnings_accepted,
        "error": summary.error,
    }


def _counts(items: object) -> list[RunCountResponse]:
    """Return a run's recorded counts, skipping anything malformed."""
    if not isinstance(items, list):
        return []
    return [
        RunCountResponse(
            rule=str(item["rule"]),
            severity=item["severity"],
            column=item.get("column_key"),
            rows=int(item["rows"]),
        )
        for item in items
        if isinstance(item, dict) and {"rule", "severity", "rows"} <= item.keys()
    ]


def _validation(recorded: object) -> LoadValidationResponse | None:
    """Return what a load recorded of its validation, if it came from the web app."""
    if not isinstance(recorded, dict):
        return None
    accepted = recorded.get("accepted")
    return LoadValidationResponse(
        validated_at=recorded.get("validated_at"),
        rows_read=int(recorded.get("rows_read", 0)),
        rows_with_warnings=int(recorded.get("rows_with_warnings", 0)),
        counts=_counts(recorded.get("counts")),
        accepted=_counts(accepted.get("warnings")) if isinstance(accepted, dict) else None,
    )


def difference_counts(recorded: object) -> DifferenceCountsResponse | None:
    """Return a catalogue's difference as a load recorded it."""
    if not isinstance(recorded, dict):
        return None
    return DifferenceCountsResponse.model_validate(recorded)


def _draft_id(parameters: Mapping[str, object]) -> UUID | None:
    """Return the draft a load came from, if its parameters name one."""
    value = parameters.get("draft_id")
    try:
        return UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _original(original: ArchivedOriginal | None) -> OriginalFileResponse | None:
    """Describe the file a load came from, without saying where it is archived."""
    if original is None:
        return None
    return OriginalFileResponse(
        filename=original.filename,
        size_bytes=original.size_bytes,
        content_hash=original.content_hash,
    )


def load_detail(record: LoadRecord, viewer: str, draft_available: bool) -> LoadDetailResponse:
    """Describe one load in detail, for one viewer."""
    parameters = record.parameters
    return LoadDetailResponse(
        **_summary_fields(record.summary, viewer),
        plant_code=record.plant_code,
        flow_name=record.flow_name,
        original=_original(record.original),
        validation=_validation(parameters.get("validation")),
        difference=difference_counts(parameters.get("difference")),
        draft_id=_draft_id(parameters),
        draft_available=draft_available,
    )


def load_view(view: LoadView, viewer: str) -> LoadDetailResponse:
    """Describe one load as the service read it for a viewer."""
    return load_detail(view.record, viewer, view.draft_available)


@router.get("", summary="The history of loads", status_code=status.HTTP_200_OK)
def list_loads(
    principal: PrincipalDep,
    service: LoadServiceDep,
    catalogue: DatasetCatalogueDep,
    dataset: Annotated[str | None, Query(description="A dataset spec's key.")] = None,
    state: Annotated[RunState | None, Query(description="A run state.")] = None,
    user: Annotated[
        str | None, Query(description="`me`, or the subject of someone in `people`.")
    ] = None,
    origin: Annotated[
        Literal["app", "cli"] | None, Query(description="The web app or the command line.")
    ] = None,
    period_from: Annotated[date | None, Query(description="Covers this day or later.")] = None,
    period_to: Annotated[date | None, Query(description="Covers this day or earlier.")] = None,
    started_from: Annotated[
        date | None, Query(alias="from", description="Started this day or later.")
    ] = None,
    started_to: Annotated[
        date | None, Query(alias="to", description="Started this day or earlier.")
    ] = None,
    sort: LoadSort = LoadSort.STARTED_AT,
    order: Literal["asc", "desc"] = "desc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> LoadsPageResponse:
    """Return a page of the loads of the caller's plants.

    Raises:
        UnknownDatasetError: ``dataset`` names no spec (404).
    """
    wanted = LoadFilter(
        plant_scope=principal.plant_scope,
        target=target_of(catalogue.get(dataset)) if dataset else None,
        state=state,
        uploaded_by=principal.subject if user == "me" else user,
        origin=LoadOrigin(origin) if origin else None,
        period_from=period_from,
        period_to=period_to,
        started_from=started_from,
        started_to=started_to,
    )
    page = service.loads(wanted, sort, order == "desc", offset, limit)
    return LoadsPageResponse(
        total=page.total,
        offset=offset,
        limit=limit,
        items=[
            LoadSummaryResponse(**_summary_fields(item, principal.subject)) for item in page.items
        ],
        people=[PersonResponse(subject=person.subject, name=person.name) for person in page.people],
    )


@router.get(
    "/{run_id}",
    summary="One load in detail",
    status_code=status.HTTP_200_OK,
    responses=_NOT_FOUND,
)
def get_load(run_id: UUID, principal: PrincipalDep, service: LoadServiceDep) -> LoadDetailResponse:
    """Return one load of the caller's plants.

    Raises:
        UnknownLoadError: no load of the caller's plants has that ID (404).
    """
    return load_view(
        service.load(run_id, principal.plant_scope, principal.subject), principal.subject
    )


def _disposition(filename: str) -> str:
    """Return a ``Content-Disposition`` that keeps any name, for any browser (RFC 6266)."""
    plain = "".join(
        char if char.isascii() and char.isprintable() and char not in '"\\' else "_"
        for char in filename
    )
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _verified(original: ArchivedOriginal, stream: BufferedReader) -> Iterator[bytes]:
    """Yield a file's bytes, holding the last piece back until its hash matches.

    A file that does not match stops one piece short of its announced length,
    so the browser reports the download as broken instead of keeping it.
    """
    digest = hashlib.sha256()
    held: bytes | None = None
    try:
        while chunk := stream.read(STREAM_CHUNK_BYTES):
            digest.update(chunk)
            if held is not None:
                yield held
            held = chunk
    finally:
        stream.close()
    if digest.hexdigest() != original.content_hash:
        logger.error("an original does not match its hash", object_key=original.object_key)
        raise ContentHashMismatchError(
            "the archived file does not match its hash", content_hash=original.content_hash
        )
    if held is not None:
        yield held


@router.get(
    "/{run_id}/original",
    summary="The file a load came from",
    status_code=status.HTTP_200_OK,
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
            "description": "The file, as it was uploaded.",
        },
        **_NOT_FOUND,
    },
)
def download_original(
    run_id: UUID, principal: PrincipalDep, service: LoadServiceDep
) -> StreamingResponse:
    """Stream the file a load of the caller's plants came from.

    Raises:
        UnknownLoadError: no load of the caller's plants has that ID, or none
            whose file Bronze catalogues (404).
        EntityNotFoundError: the archive no longer holds the file (404).
    """
    original, stream = service.original(run_id, principal.plant_scope)
    return StreamingResponse(
        _verified(original, stream),
        media_type=original.content_type,
        headers={
            "Content-Disposition": _disposition(original.filename),
            "Content-Length": str(original.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


__all__ = [
    "STREAM_CHUNK_BYTES",
    "difference_counts",
    "load_detail",
    "load_reference",
    "load_view",
    "router",
]
