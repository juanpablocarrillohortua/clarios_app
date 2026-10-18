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
  severity, rule, column, first reason and, for a correction, change. Every
  issue carries its likely reasons, which the web app turns into why it
  happened and what to do in Excel.
- ``POST /drafts/{draft_id}/corrections`` takes a corrected file, uploaded
  exactly as ``POST /drafts`` takes one, for the dataset of the draft it
  corrects. The new draft is linked to that one, which lives on as long.
- ``GET /drafts/{draft_id}/cleared`` pages through the issues of the
  corrected file that its correction no longer has.

A correction's draft carries a ``comparison``: how many issues were cleared,
remain and are new. Data is corrected in Excel, not here (ADR 0009).

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
    ComparisonResponse,
    CountOnlyResponse,
    DraftResponse,
    DraftValidationResponse,
    ErrorResponse,
    IssueResponse,
    IssuesPageResponse,
    PreviousFileResponse,
    ReasonCountResponse,
    ReasonResponse,
    RuleCountResponse,
)
from energy.domain.draft_comparison import IssueChange
from energy.domain.draft_reasons import ReasonId
from energy.domain.draft_rules import IssueSeverity, RuleId
from energy.domain.exceptions import InvalidFilenameError, UploadAbortedError
from energy.ports.draft_repository import IssueFilter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping

    from energy.domain.draft_comparison import Comparison
    from energy.domain.draft_rules import Issue
    from energy.domain.drafts import Draft
    from energy.ports.draft_repository import IssuePage
    from energy.services.draft_service import DraftService

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


def _comparison(compared: Comparison | None) -> ComparisonResponse | None:
    """Describe how a correction compares with the file it corrects."""
    if compared is None:
        return None
    previous = compared.previous
    return ComparisonResponse(
        state=compared.state.value,
        previous=PreviousFileResponse(
            draft_id=previous.draft_id,
            filename=previous.filename,
            validated_at=previous.validated_at,
        )
        if previous is not None
        else None,
        previous_revalidated=compared.previous_revalidated,
        cleared=compared.cleared,
        remaining=compared.remaining,
        new=compared.new,
        by_count_only=[
            CountOnlyResponse(
                rule=group.rule.value,
                column=group.column_key,
                before=group.before,
                after=group.after,
            )
            for group in compared.by_count_only
        ],
    )


def _draft(draft: Draft, compared: Comparison | None = None) -> DraftResponse:
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
                    reasons=[
                        ReasonCountResponse(reason=by_reason.reason.value, rows=by_reason.rows)
                        for by_reason in count.reasons
                    ],
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
        previous_draft_id=draft.previous_draft_id,
        comparison=_comparison(compared),
    )


def _described(service: DraftService, draft: Draft, owner: str) -> DraftResponse:
    """Describe a draft with, for a correction, how it compares."""
    return _draft(draft, service.comparison(draft, owner))


def _json(params: Mapping[str, str | int | tuple[str, ...]]) -> dict[str, str | int | list[str]]:
    """Return params as JSON wants them: tuples as lists."""
    return {
        name: list(value) if isinstance(value, tuple) else value for name, value in params.items()
    }


def _issue(issue: Issue, change: IssueChange | None = None) -> IssueResponse:
    """Describe one issue: its place, its rule, its reasons and what the sentences need."""
    return IssueResponse(
        row=issue.source_row,
        column=issue.column_letter,
        column_key=issue.column_key,
        rule=issue.rule.value,
        severity=issue.severity.value,
        value=issue.value,
        params=_json(issue.params),
        reasons=[
            ReasonResponse(reason=reason.id.value, params=_json(reason.params))
            for reason in issue.reasons
        ],
        change=change.value if change is not None else None,
    )


def _page(page: IssuePage, offset: int, limit: int) -> IssuesPageResponse:
    """Describe a page of issues, each with its change when the page has them."""
    changes = page.changes or (None,) * len(page.items)
    return IssuesPageResponse(
        total=page.total,
        offset=offset,
        limit=limit,
        items=[_issue(issue, change) for issue, change in zip(page.items, changes, strict=True)],
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

    def open_draft() -> Draft:
        return service.open_upload(
            dataset_key,
            _filename(x_file_name),
            content_type,
            content_length,
            principal.subject,
            principal.display_name,
        )

    return await _receive(request, response, service, principal.subject, open_draft)


async def _receive(
    request: Request,
    response: Response,
    service: DraftService,
    owner: str,
    open_draft: Callable[[], Draft],
) -> DraftResponse:
    """Record a draft, archive the body as it arrives, and describe the outcome.

    Answers 201 with ``Location`` for a new draft, 200 with the earlier draft
    for the same bytes again.
    """
    draft = await anyio.to_thread.run_sync(open_draft)
    outcome = await anyio.to_thread.run_sync(service.receive, draft, _RequestBody(request))
    if outcome.created:
        response.headers["Location"] = (
            f"{request.scope.get('root_path', '')}/drafts/{draft.draft_id}"
        )
    else:
        response.status_code = status.HTTP_200_OK
    return await anyio.to_thread.run_sync(_described, service, outcome.draft, owner)


@router.post(
    "/{draft_id}/corrections",
    summary="Upload a corrected file for a draft",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": DraftResponse,
            "description": "The same bytes were already uploaded: that draft.",
        },
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "The draft's own file never arrived.",
        },
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
            "description": "The corrected .xlsx file itself, as the raw body.",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_correction(
    draft_id: UUID,
    request: Request,
    response: Response,
    principal: PrincipalDep,
    service: DraftServiceDep,
    x_file_name: Annotated[
        str | None, Header(description="The file's name, percent-encoded UTF-8.")
    ] = None,
    content_length: Annotated[int | None, Header(description="The file's size.")] = None,
    content_type: Annotated[str, Header(description="The file's type.")] = "",
) -> DraftResponse:
    """Archive a corrected file as a new draft linked to the one it corrects.

    The upload is the same as ``POST /drafts``; the dataset is the corrected
    draft's.

    Raises:
        UnknownDraftError: no live draft of the caller has that ID (404).
        DraftBusyError: that draft's own file never arrived (409).
        LengthRequiredError: no ``Content-Length`` (411).
        FileTooLargeError: over 300 MiB, announced or counted (413).
        UnsupportedFileFormatError: not an ``.xlsx`` file (415).
        InvalidFilenameError: no usable file name (400).
        SourceUnavailableError: object storage is unreachable (503).
    """

    def open_draft() -> Draft:
        return service.open_correction(
            draft_id,
            _filename(x_file_name),
            content_type,
            content_length,
            principal.subject,
            principal.display_name,
        )

    return await _receive(request, response, service, principal.subject, open_draft)


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
    return _described(service, service.get(draft_id, principal.subject), principal.subject)


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
    validated = service.validate(draft_id, principal.subject)
    return _described(service, validated, principal.subject)


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
    reason: Annotated[ReasonId | None, Query(description="A first reason.")] = None,
    change: Annotated[
        IssueChange | None, Query(description="For a correction: new or remaining.")
    ] = None,
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
        IssueFilter(severity=severity, rule=rule, column_key=column, reason=reason, change=change),
        offset,
        limit,
    )
    return _page(page, offset, limit)


@router.get(
    "/{draft_id}/cleared",
    summary="A page of the issues a correction cleared",
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No draft of yours, or no correction to compare.",
        }
    },
)
def list_cleared(
    draft_id: UUID,
    principal: PrincipalDep,
    service: DraftServiceDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> IssuesPageResponse:
    """Return a page of the issues of the corrected file that the correction no longer has.

    Raises:
        UnknownDraftError: no live draft of the caller has that ID, or it is
            no correction that can be compared (404).
    """
    return _page(service.cleared(draft_id, principal.subject, offset, limit), offset, limit)


__all__ = ["router"]
