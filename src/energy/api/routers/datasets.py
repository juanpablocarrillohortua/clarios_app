"""The dataset specs, and the template of each.

Layer: api

A dataset spec says what the app expects of one kind of data people load: its
sheet, its columns and the rules of each, how its rows are kept and how they
may be entered (:mod:`energy.domain.dataset_spec`). The specs are files in
``config/datasets/``, read once when the API is built; a malformed one stops
the API at start.

- ``GET /datasets`` lists every spec, for the web app to build its loading
  pages from, with the size limits of an uploaded file, so the browser
  refuses a file too large before it sends a byte;
- ``GET /datasets/{dataset_key}/template`` returns the empty workbook a user
  fills in: the dataset's sheet, with exactly the spec's headers.

Both need ``loads:write``, which ``api/main.py`` attaches where it mounts this
router: specs serve loading data, which is an operator's work.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from energy.api.deps import DatasetCatalogueDep, TemplateWriterDep
from energy.api.schemas.responses import (
    DatasetColumn,
    DatasetDescription,
    DatasetsResponse,
    ErrorResponse,
    UploadLimits,
)
from energy.domain.dataset_spec import ColumnSpec, DatasetSpec
from energy.domain.drafts import CHECK_IN_REQUEST_MAX_BYTES, MAX_FILE_BYTES

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _column(column: ColumnSpec) -> DatasetColumn:
    """Describe one column for the web app."""
    return DatasetColumn(
        header=column.header,
        key=column.key,
        label=column.label,
        type=column.type.value,
        required=column.required,
        pattern=column.pattern,
        allowed=list(column.allowed) if column.allowed is not None else None,
        min=column.minimum,
        max=column.maximum,
        severity=column.severity.value,
    )


def _dataset(spec: DatasetSpec) -> DatasetDescription:
    """Describe one dataset for the web app. Bronze and Silver stay out of it."""
    return DatasetDescription(
        key=spec.key,
        kind=spec.kind.value,
        origin=spec.origin.value,
        label=spec.label,
        description=spec.description,
        entry_modes=[mode.value for mode in spec.entry_modes],
        sheet=spec.bronze.sheet,
        header_row=spec.bronze.header_row,
        natural_key=list(spec.natural_key),
        period_column=spec.period_column,
        columns=[_column(column) for column in spec.columns],
    )


@router.get(
    "",
    summary="Every dataset spec",
    status_code=status.HTTP_200_OK,
)
async def list_datasets(catalogue: DatasetCatalogueDep) -> DatasetsResponse:
    """Return every dataset spec, sorted by key, and how large a file may be.

    Args:
        catalogue: the specs the API was built with.

    Returns:
        A :class:`~energy.api.schemas.responses.DatasetsResponse`.
    """
    return DatasetsResponse(
        datasets=[_dataset(spec) for spec in catalogue],
        limits=UploadLimits(
            max_file_bytes=MAX_FILE_BYTES,
            validated_in_request_bytes=CHECK_IN_REQUEST_MAX_BYTES,
        ),
    )


@router.get(
    "/{dataset_key}/template",
    summary="The empty workbook of one dataset",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "description": "The workbook: one sheet, the spec's headers, no data.",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}},
        },
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such dataset."},
    },
)
async def download_template(
    dataset_key: str, catalogue: DatasetCatalogueDep, writer: TemplateWriterDep
) -> Response:
    """Return the template of one dataset, as a file to save.

    Args:
        dataset_key: the dataset's key, as ``GET /datasets`` lists it.
        catalogue: the specs the API was built with.
        writer: the writer of templates.

    Returns:
        The workbook, named ``<dataset_key>.xlsx``.

    Raises:
        UnknownDatasetError: if no spec has that key (HTTP 404).
    """
    spec = catalogue.get(dataset_key)
    return Response(
        content=writer.write(spec),
        media_type=writer.media_type,
        headers={"Content-Disposition": f'attachment; filename="{spec.key}{writer.suffix}"'},
    )


__all__ = ["router"]
