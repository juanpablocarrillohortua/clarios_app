"""Drafts: a file uploaded through the web app, while it is checked.

Layer: api

A person chooses a dataset, uploads one ``.xlsx`` file and has it validated
against the dataset's spec. Nothing reaches Bronze from here: the file is
archived and its rows and issues are staged (schema ``staging``) until a later
increment commits them.

- ``POST /drafts?dataset_key=<key>`` takes the file as the **raw request
  body** (not a form), so it streams to object storage a part at a time and
  is never held whole: ``Content-Length`` is required, the file's name travels
  percent-encoded in ``X-File-Name``, and the body may be up to 300 MiB. It
  answers 201 with the new draft, or 200 with the earlier draft when the same
  person uploads the same bytes for the same dataset again.
- ``GET /drafts/{draft_id}`` returns a draft and what its last validation found.
- ``POST /drafts/{draft_id}/validate`` checks the file against the spec,
  replacing earlier results. A file over 5 MiB answers 501 until the worker
  validates large files (increment 12).
- ``GET /drafts/{draft_id}/issues`` pages through the issues, filtered by
  severity, rule and column.

Every route needs ``loads:write``, attached where ``api/main.py`` mounts this
router, and a draft is visible to its owner only: someone else's answers 404.

The token is checked once, before the body is read. Nothing checks it again
while a long upload arrives, so a token that expires meanwhile changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import unquote
from uuid import UUID

import anyio
import anyio.from_thread
import anyio.to_thread
from fastapi import APIRouter, Header, Query, Request, Response, status
from starlette.requests import ClientDisconnect

from energy.api.deps import DraftServiceDep, PrincipalDep
from energy.api.schemas.responses import (
    DraftResponse,
    DraftValidationResponse,
    ErrorResponse,
    IssueResponse,
    IssuesPageResponse,
    RuleCountResponse,
)
from energy.domain.draft_rules import IssueSeverity, RuleId
from energy.domain.exceptions import InvalidFilenameError, UploadAbortedError
from energy.ports.draft_repository import IssueFilter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from energy.domain.draft_rules import Issue
    from energy.domain.drafts import Draft

router = APIRouter(prefix="/drafts", tags=["drafts"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No draft of yours."}
}


class _RequestBody:
    """The request's body, read from a worker thread one chunk at a time.

    The service that streams the file to object storage is synchronous and
    runs in a worker thread. Each read asks the event loop for the next chunk
    of the body, so nothing is read ahead of what the storage has taken and
    the memory used stays one part, whatever the size of the file.

    Args:
        request: the request whose body is read.
    """

    def __init__(self, request: Request) -> None:
        self._chunks: AsyncIterator[bytes] = request.stream().__aiter__()
        self._pending = b""
        self._done = False

    async def _next(self) -> bytes:
        """Return the next chunk of the body, or ``b""`` at its end."""
        try:
            return await anext(self._chunks)
        except StopAsyncIteration:
            return b""

    def read(self, size: int = -1, /) -> bytes:
        """Return up to ``size`` bytes; ``b""`` at the end of the body.

        Raises:
            UploadAbortedError: if the client went away.
        """
        if not self._pending:
            if self._done:
                return b""
            try:
                chunk = anyio.from_thread.run(self._next)
            except ClientDisconnect as exc:
                raise UploadAbortedError("the client went away during the upload") from exc
            if not chunk:
                self._done = True
                return b""
            self._pending = chunk
        if size < 0 or size >= len(self._pending):
            chunk, self._pending = self._pending, b""
        else:
            chunk, self._pending = self._pending[:size], self._pending[size:]
        return chunk


def _draft(draft: Draft) -> DraftResponse:
    """Describe a draft for its owner. Where it is archived stays inside."""
    validation = None
    if draft.validation is not None and draft.validated_at is not None:
        summary = draft.validation
        validation = DraftValidationResponse(
            validated_at=draft.validated_at,
            sheet=summary.sheet_name,
            header_row=summary.header_row,
            rows_read=summary.rows_read,
            rows_with_errors=summary.rows_with_errors,
            rows_with_warnings=summary.rows_with_warnings,
            blocking=summary.blocking,
            counts=[
                RuleCountResponse(
                    rule=count.rule.value,
                    severity=count.severity.value,
                    column=count.column_key,
                    rows=count.rows,
                    listed=count.listed,
                )
                for count in summary.counts
            ],
        )
    return DraftResponse(
        draft_id=draft.draft_id,
        dataset_key=draft.dataset_key,
        status=draft.status.value,
        filename=draft.filename,
        declared_bytes=draft.declared_bytes,
        size_bytes=draft.size_bytes,
        content_hash=draft.content_hash,
        created_at=draft.created_at,
        uploaded_at=draft.uploaded_at,
        expires_at=draft.expires_at,
        failure_code=draft.failure_code,
        validation=validation,
    )


def _issue(issue: Issue) -> IssueResponse:
    """Describe one issue: its place, its rule and what the sentence needs."""
    return IssueResponse(
        row=issue.source_row,
        column=issue.column_letter,
        column_key=issue.column_key,
        rule=issue.rule.value,
        severity=issue.severity.value,
        value=issue.value,
        params={
            name: list(value) if isinstance(value, tuple) else value
            for name, value in issue.params.items()
        },
    )


def _filename(header: str | None) -> str:
    """Decode the percent-encoded file name of ``X-File-Name``."""
    try:
        return unquote(header or "", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidFilenameError("the file name is not percent-encoded UTF-8") from exc


@router.post(
    "",
    summary="Upload a file as a new draft",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": DraftResponse,
            "description": "The same bytes were already uploaded: the earlier draft.",
        },
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such dataset."},
        status.HTTP_411_LENGTH_REQUIRED: {"model": ErrorResponse, "description": "No size."},
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "model": ErrorResponse,
            "description": "Over 300 MiB.",
        },
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "model": ErrorResponse,
            "description": "Not an .xlsx file, or empty.",
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The .xlsx file itself, as the raw body.",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_draft(
    request: Request,
    response: Response,
    principal: PrincipalDep,
    service: DraftServiceDep,
    dataset_key: Annotated[str, Query(description="The dataset the file is for.")],
    x_file_name: Annotated[
        str | None, Header(description="The file's name, percent-encoded UTF-8.")
    ] = None,
    content_length: Annotated[int | None, Header(description="The file's size.")] = None,
    content_type: Annotated[str, Header(description="The file's type.")] = "",
) -> DraftResponse:
    """Archive an uploaded file as it arrives, and record its draft.

    Args:
        request: the request, whose body is the file.
        response: the response, whose status and ``Location`` are set here.
        principal: the signed-in caller, who owns the draft.
        service: the draft service.
        dataset_key: the dataset the file is for.
        x_file_name: the file's name, percent-encoded UTF-8.
        content_length: the file's size, required.
        content_type: the file's declared type.

    Returns:
        The draft, ``uploaded``: 201 when new, 200 for the same bytes again.

    Raises:
        UnknownDatasetError: no such dataset (404).
        LengthRequiredError: no ``Content-Length`` (411).
        FileTooLargeError: over 300 MiB, announced or counted (413).
        UnsupportedFileFormatError: not an ``.xlsx`` file (415).
        InvalidFilenameError: no usable file name (400).
        SourceUnavailableError: object storage is unreachable (503).
    """
    draft = await anyio.to_thread.run_sync(
        service.open_upload,
        dataset_key,
        _filename(x_file_name),
        content_type,
        content_length,
        principal.subject,
        principal.display_name,
    )
    outcome = await anyio.to_thread.run_sync(service.receive, draft, _RequestBody(request))
    if outcome.created:
        response.headers["Location"] = (
            f"{request.scope.get('root_path', '')}/drafts/{draft.draft_id}"
        )
    else:
        response.status_code = status.HTTP_200_OK
    return _draft(outcome.draft)


@router.get(
    "/{draft_id}",
    summary="One draft, and what its validation found",
    status_code=status.HTTP_200_OK,
    responses=_NOT_FOUND,
)
def get_draft(draft_id: UUID, principal: PrincipalDep, service: DraftServiceDep) -> DraftResponse:
    """Return one of the caller's drafts.

    Raises:
        UnknownDraftError: no live draft of the caller has that ID (404).
    """
    return _draft(service.get(draft_id, principal.subject))


@router.post(
    "/{draft_id}/validate",
    summary="Check a draft's file against its dataset spec",
    status_code=status.HTTP_200_OK,
    responses={
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "The file has not arrived, or another validation is running.",
        },
        status.HTTP_501_NOT_IMPLEMENTED: {
            "model": ErrorResponse,
            "description": "Files over 5 MB are validated by a job not built yet.",
        },
    },
)
def validate_draft(
    draft_id: UUID, principal: PrincipalDep, service: DraftServiceDep
) -> DraftResponse:
    """Validate one of the caller's drafts, replacing earlier results.

    Raises:
        UnknownDraftError: no live draft of the caller has that ID (404).
        DraftBusyError: its file has not arrived, or it is being validated (409).
        NotImplementedError: the file is over 5 MiB (501).
    """
    return _draft(service.validate(draft_id, principal.subject))


@router.get(
    "/{draft_id}/issues",
    summary="A page of a draft's issues",
    status_code=status.HTTP_200_OK,
    responses=_NOT_FOUND,
)
def list_issues(
    draft_id: UUID,
    principal: PrincipalDep,
    service: DraftServiceDep,
    severity: IssueSeverity | None = None,
    rule: RuleId | None = None,
    column: Annotated[str | None, Query(description="A column's key.")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> IssuesPageResponse:
    """Return a page of the issues of one of the caller's drafts.

    Raises:
        UnknownDraftError: no live draft of the caller has that ID (404).
    """
    page = service.issues(
        draft_id,
        principal.subject,
        IssueFilter(severity=severity, rule=rule, column_key=column),
        offset,
        limit,
    )
    return IssuesPageResponse(
        total=page.total,
        offset=offset,
        limit=limit,
        items=[_issue(issue) for issue in page.items],
    )


__all__ = ["router"]
