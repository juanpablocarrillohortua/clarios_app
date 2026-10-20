"""Pydantic response models - the outbound half of the HTTP contract.

Layer: api

Responsibility:
    Defines exactly what the API returns. Every response that carries a computed
    number also carries the metadata needed to judge it: which run produced it,
    when, and what fraction of the underlying period was actually observed. A
    KPI without its coverage is a number without an error bar, and consumers
    will treat it as certain. These models never expose Bronze rows, internal
    surrogate keys, or database column names.

    The model shapes below are the Phase 1 contract and are complete; the
    handlers that populate them are the placeholders.

Depends on:
    ``pydantic`` and the standard library only.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

RunState = Literal["pending", "running", "completed", "failed", "cancelled"]


class ResponseModel(BaseModel):
    """Base for every response model."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ErrorResponse(ResponseModel):
    """The single error envelope every failing endpoint returns.

    Attributes:
        code: stable machine-readable code from
            :class:`~energy.domain.exceptions.EnergyError`, safe to branch on.
        message: human-readable description, in English.
        details: structured context, for example the offending column or plant.
        request_id: the correlation id, so a user can quote it in a ticket and an
            operator can find every log line for the request.
    """

    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    request_id: str = ""


class AuthClientConfig(ResponseModel):
    """What the web app needs to sign a user in.

    All four values are public: a browser shows them in the address of the
    sign-in page. None of them is a secret.

    Attributes:
        tenant_id: the Entra ID tenant users belong to.
        client_id: the app registration users sign in through.
        authority: the address the browser is sent to for sign-in.
        api_scope: the scope to request an access token for. The API accepts
            only tokens issued for it.
    """

    tenant_id: str
    client_id: str
    authority: str
    api_scope: str


class PowerBIClientConfig(ResponseModel):
    """Which Power BI report the dashboard shows, and where the app asks for it.

    The app loads the report from the Power BI Service with the signed-in
    user's own token. None of these values is a secret, and none opens anything
    without that token.

    Attributes:
        workspace_id: the workspace the report is published in.
        report_id: the report.
        scope: the scope to request a Power BI access token for.
        report_api_url: the address of Power BI's REST API that returns the
            report, with the address to embed it from.
        report_web_url: the report's address in the Power BI Service, for a
            link a person follows.
    """

    workspace_id: str
    report_id: str
    scope: str
    report_api_url: str
    report_web_url: str


class ClientConfigResponse(ResponseModel):
    """Settings the web app reads before anyone is signed in.

    Attributes:
        auth: the sign-in settings, or ``None`` when sign-in is not configured
            on this server.
        powerbi: the report of the dashboard, or ``None`` when none is
            configured on this server.
    """

    auth: AuthClientConfig | None
    powerbi: PowerBIClientConfig | None


class MeResponse(ResponseModel):
    """The signed-in caller, and what they may do.

    Attributes:
        subject: the caller's stable identifier in the identity provider.
        name: display name; ``None`` when the token carries none.
        username: the name the caller signs in with; ``None`` when the token
            carries none.
        roles: the roles assigned to the caller, in alphabetical order.
        permissions: everything those roles allow, in alphabetical order. The
            web app shows a section when its permission is listed here; it
            never interprets ``roles``.
        plant_scope: the plants the caller may access.
    """

    subject: str
    name: str | None
    username: str | None
    roles: list[str]
    permissions: list[str]
    plant_scope: list[str]


class DatasetColumn(ResponseModel):
    """One column of a dataset, as its spec declares it.

    Attributes:
        header: the header text, exactly as the workbook and the template write it.
        key: the key Bronze stores the column under, derived from the headers;
            a repeated header gets ``#2`` (``Almac #2``). Unique in a dataset.
        label: the key of a label in the web app's translation file, or ``None``
            to show the header.
        type: ``text``, ``integer``, ``decimal`` or ``date``.
        required: whether an empty cell is an error.
        pattern: for text, a regular expression the whole value must match.
        allowed: for text, the only values accepted.
        min: for numbers, the smallest value accepted.
        max: for numbers, the largest value accepted.
        severity: ``error`` or ``warning``: what breaking ``pattern``,
            ``allowed``, ``min`` or ``max`` does.
    """

    header: str
    key: str
    label: str | None
    type: Literal["text", "integer", "decimal", "date"]
    required: bool
    pattern: str | None
    allowed: list[str] | None
    min: int | float | None
    max: int | float | None
    severity: Literal["error", "warning"]


class DatasetDescription(ResponseModel):
    """One kind of data people load, as its spec declares it.

    Attributes:
        key: the dataset's name; its template is at
            ``/api/datasets/{key}/template``.
        kind: ``snapshot`` (replaced whole on every load) or ``movement`` (rows
            add up, each dated by ``period_column``).
        origin: ``export`` (out of QAD, uploaded only) or ``catalogue`` (kept by
            hand).
        label: the key of the dataset's name in the web app's translation file.
        description: the key of its description there.
        entry_modes: how rows may be entered: only ``upload`` (ADR 0009).
        sheet: the worksheet a file must hold.
        header_row: the row of that sheet that holds the headers.
        natural_key: the column keys that identify a row.
        period_column: the key of the column that dates each row; ``None`` for
            a snapshot.
        columns: every column, in the workbook's order.
        last_load: the latest completed load of the dataset; ``None`` before one.
    """

    key: str
    kind: Literal["snapshot", "movement"]
    origin: Literal["export", "catalogue"]
    label: str
    description: str
    entry_modes: list[Literal["upload"]]
    sheet: str
    header_row: int
    natural_key: list[str]
    period_column: str | None
    columns: list[DatasetColumn]
    last_load: LoadReferenceResponse | None = None


class UploadLimits(ResponseModel):
    """How large a file may be, for the web app to check before it sends one.

    Attributes:
        max_file_bytes: the largest file accepted (300 MiB). The API and Caddy
            refuse anything larger.
        validated_in_request_bytes: the largest file the API validates itself
            (5 MiB). A larger one is archived, and its validation answers 501
            until the worker validates large files (increment 12).
    """

    max_file_bytes: int
    validated_in_request_bytes: int


class DatasetsResponse(ResponseModel):
    """Every dataset spec the server holds, by key.

    Attributes:
        datasets: the datasets.
        limits: how large an uploaded file may be.
    """

    datasets: list[DatasetDescription]
    limits: UploadLimits


IssueSeverityName = Literal["error", "warning", "info"]
DraftStatusName = Literal["receiving", "uploaded", "validated", "committed", "failed"]


class ReasonCountResponse(ResponseModel):
    """How many issues of one rule in one column have one first reason.

    Attributes:
        reason: the reason's ID, such as ``code.lost_leading_zeros``.
        rows: how many issues it comes first in, exactly.
    """

    reason: str
    rows: int


class RuleCountResponse(ResponseModel):
    """How often one rule was broken in one column of a draft.

    Attributes:
        rule: the rule's ID, such as ``cell.pattern``.
        severity: ``error``, ``warning`` or ``info``.
        column: the column's key; ``None`` for the file as a whole.
        rows: every row that broke it, exactly.
        listed: how many of those are in the issues list (at most 1,000).
        reasons: the same rows by their first reason, most first; they add
            up to ``rows``.
    """

    rule: str
    severity: IssueSeverityName
    column: str | None
    rows: int
    listed: int
    reasons: list[ReasonCountResponse]


class DraftValidationResponse(ResponseModel):
    """What the last validation of a draft found.

    Attributes:
        validated_at: when it ran.
        sheet: the sheet that was read.
        header_row: the row its headers were read from.
        rows_read: the data rows read, as Bronze would keep them.
        rows_with_errors: rows with at least one error.
        rows_with_warnings: rows with at least one warning.
        blocking: whether an error stops this file from being loaded.
        counts: how often each rule was broken in each column.
    """

    validated_at: AwareDatetime
    sheet: str
    header_row: int
    rows_read: int
    rows_with_errors: int
    rows_with_warnings: int
    blocking: bool
    counts: list[RuleCountResponse]


class PreviousFileResponse(ResponseModel):
    """The file a correction is compared with.

    Attributes:
        draft_id: its draft.
        filename: its name, as given.
        validated_at: when it was last validated; ``None`` if never.
    """

    draft_id: UUID
    filename: str
    validated_at: AwareDatetime | None


class CountOnlyResponse(ResponseModel):
    """A rule and column with too many issues on both sides to compare one by one.

    Attributes:
        rule: the rule's ID.
        column: the column's key; ``None`` for the file as a whole.
        before: its issues in the earlier file, exactly.
        after: its issues in the correction, exactly.
    """

    rule: str
    column: str | None
    before: int
    after: int


class ComparisonResponse(ResponseModel):
    """How a correction compares with the file it corrects.

    Attributes:
        state: ``ready``; or why there is no comparison:
            ``not_validated``, ``previous_not_validated``, ``previous_gone``,
            ``rules_changed``.
        previous: the earlier file, while it is kept.
        previous_revalidated: the earlier file was validated again after
            the correction, so both are judged by the same rules.
        cleared: issues of the earlier file the correction no longer has.
        remaining: issues of the earlier file the correction still has.
        new: issues of the correction the earlier file did not have.
        by_count_only: rules and columns compared by count alone; they are
            not in the three numbers above.
    """

    state: Literal[
        "ready", "not_validated", "previous_not_validated", "previous_gone", "rules_changed"
    ]
    previous: PreviousFileResponse | None
    previous_revalidated: bool
    cleared: int
    remaining: int
    new: int
    by_count_only: list[CountOnlyResponse]


class DraftResponse(ResponseModel):
    """One uploaded file, while it is prepared. Only its owner sees it.

    Attributes:
        draft_id: the draft's ID.
        dataset_key: the dataset spec it is checked against.
        status: ``receiving``, ``uploaded``, ``validated``, ``committed`` or
            ``failed``.
        filename: the file's name, as given.
        declared_bytes: the size the upload announced.
        size_bytes: the size received; ``None`` until the file has arrived.
        content_hash: the file's SHA-256; ``None`` until then.
        created_at: when the upload began.
        uploaded_at: when the original was archived.
        expires_at: when the draft is purged if nobody works on it.
        failure_code: why the upload failed, for a ``failed`` draft.
        validation: what the last validation found; ``None`` before one.
        previous_draft_id: the draft whose file this one corrects, if any.
        comparison: for a correction, how it compares with that file.
        run_id: for a ``committed`` draft, the load that landed it.
        committed_at: when it was committed.
    """

    draft_id: UUID
    dataset_key: str
    status: DraftStatusName
    filename: str
    declared_bytes: int
    size_bytes: int | None
    content_hash: str | None
    created_at: AwareDatetime
    uploaded_at: AwareDatetime | None
    expires_at: AwareDatetime
    failure_code: str | None
    validation: DraftValidationResponse | None
    previous_draft_id: UUID | None
    comparison: ComparisonResponse | None
    run_id: UUID | None = None
    committed_at: AwareDatetime | None = None


class ReasonResponse(ResponseModel):
    """One likely reason for an issue.

    Attributes:
        reason: the reason's ID; the web app shows why it happened and what
            to do in Excel (``loads.reasons.<id>`` in its translation file).
        params: what its sentences need: a date, a suggested value ...
    """

    reason: str
    params: dict[str, str | int | list[str]]


class IssueResponse(ResponseModel):
    """One problem found in a draft's file.

    The web app turns ``rule`` and ``params`` into a sentence of its own
    language; the API sends no prose about the data.

    Attributes:
        row: the Excel row; ``None`` for the file as a whole.
        column: the Excel column letter; ``None`` when not in one column.
        column_key: the column's key; ``None`` for the file as a whole.
        rule: the rule's ID, such as ``cell.type``.
        severity: ``error``, ``warning`` or ``info``.
        value: the text found, at most 200 characters.
        params: what the sentence needs: the pattern, the allowed values ...
        reasons: why it probably happened, most likely first; never empty.
        change: for a correction, ``new`` or ``remaining``; ``None`` for any
            other draft, or where its rule and column are compared by count.
    """

    row: int | None
    column: str | None
    column_key: str | None
    rule: str
    severity: IssueSeverityName
    value: str | None
    params: dict[str, str | int | list[str]]
    reasons: list[ReasonResponse]
    change: Literal["new", "remaining"] | None


class IssuesPageResponse(ResponseModel):
    """A page of a draft's issues.

    Attributes:
        total: how many listed issues match the filter.
        offset: where this page starts.
        limit: the largest page asked for.
        items: the issues, by row then column, the file's own first.
    """

    total: int
    offset: int
    limit: int
    items: list[IssueResponse]


LoadOriginName = Literal["app", "cli"]


class LoadReferenceResponse(ResponseModel):
    """A completed load, as a confirmation or a dataset names it.

    Attributes:
        run_id: the load's ID; its detail is at ``/api/loads/{run_id}``.
        origin: ``app`` (the web app) or ``cli`` (the command line).
        finished_at: when it completed.
        filename: the file it came from.
        rows: the rows it landed of the dataset.
        uploaded_by_name: who loaded it; ``None`` from the command line.
        window_start: the first date its rows cover, for movements.
        window_end: the last date, likewise.
    """

    run_id: UUID
    origin: LoadOriginName
    finished_at: AwareDatetime | None
    filename: str | None
    rows: int
    uploaded_by_name: str | None
    window_start: date | None
    window_end: date | None


class WarningCountResponse(ResponseModel):
    """A warning a person accepts by confirming: its rule, its column, how many rows.

    Attributes:
        rule: the rule's ID.
        column: the column's key; ``None`` for the file as a whole.
        rows: rows with this warning.
        reasons: the rows counted by their most likely reason.
    """

    rule: str
    column: str | None
    rows: int
    reasons: list[ReasonCountResponse]


class DifferenceCountsResponse(ResponseModel):
    """How a catalogue's new version differs from its current one, by natural key.

    Attributes:
        base_run_id: the current version's load; ``None`` when there is none.
        added: rows only the new version has.
        changed: rows both have, with different values.
        removed: rows only the current version has.
        unchanged: rows both have, alike.
        removed_rows: what Silver does with a removed row: ``kept`` (it stays,
            the table is never emptied) or ``dropped`` (it goes at the next
            update of the figures). From the dataset spec.
    """

    base_run_id: UUID | None
    added: int
    changed: int
    removed: int
    unchanged: int
    removed_rows: Literal["kept", "dropped"] | None


class PeriodResponse(ResponseModel):
    """The first and the last date a movement's rows cover."""

    start: date
    end: date


CommitBlockName = Literal[
    "not_validated", "errors", "nothing_to_load", "already_loaded", "committed"
]


class ConfirmationResponse(ResponseModel):
    """What confirming a draft would write. Only its owner sees it.

    Attributes:
        draft_id: the draft.
        dataset_key: its dataset.
        kind: ``snapshot`` or ``movement``.
        filename: its file.
        sheet: the sheet its rows come from.
        can_confirm: whether ``POST /api/drafts/{draft_id}/commit`` would land it.
        blocked_by: why not, when it would not.
        validation_digest: what a commit must carry: the validation the person sees.
        rows: the rows that would land.
        warnings: the warnings confirming accepts.
        warnings_to_accept: whether there are any.
        period: for a movement, the dates its rows cover.
        overlapping_loads: for a movement, the loads that already cover that period.
        current_version: for a catalogue, the load that is current now.
        difference: for a catalogue, how the new version differs from it.
        already_loaded_run_id: the load of the same file, when there is one.
        run_id: for a committed draft, its load.
    """

    draft_id: UUID
    dataset_key: str
    kind: Literal["snapshot", "movement"]
    filename: str
    sheet: str
    can_confirm: bool
    blocked_by: CommitBlockName | None
    validation_digest: str | None
    rows: int
    warnings: list[WarningCountResponse]
    warnings_to_accept: bool
    period: PeriodResponse | None
    overlapping_loads: list[LoadReferenceResponse]
    current_version: LoadReferenceResponse | None
    difference: DifferenceCountsResponse | None
    already_loaded_run_id: UUID | None
    run_id: UUID | None


class ColumnChangeResponse(ResponseModel):
    """One column of a changed row: before and now. ``None`` is an empty cell."""

    column: str
    before: str | None
    after: str | None


class DifferenceRowResponse(ResponseModel):
    """One row of a catalogue that the new version adds, changes or removes.

    Attributes:
        change: ``added``, ``changed`` or ``removed``.
        key: the row's natural key values, in the spec's order.
        row: its row in the new file; ``None`` for a removed row.
        previous_row: its row in the current version; ``None`` for an added row.
        values: an added or a removed row's values, by column key.
        columns: a changed row's columns that differ.
    """

    change: Literal["added", "changed", "removed"]
    key: list[str | None]
    row: int | None
    previous_row: int | None
    values: dict[str, str]
    columns: list[ColumnChangeResponse]


class DifferencePageResponse(ResponseModel):
    """A page of the rows of one kind of change."""

    total: int
    offset: int
    limit: int
    items: list[DifferenceRowResponse]


class SheetLoadedResponse(ResponseModel):
    """One sheet of a load, and in its detail the rows landed from it."""

    name: str
    dataset_key: str | None
    rows: int | None


class LoadSummaryResponse(ResponseModel):
    """One load, as the history lists it.

    Attributes:
        run_id: the load's ID.
        origin: ``app`` or ``cli``.
        state: the run's state.
        source_key: the Bronze source.
        dataset_key: the dataset spec, for a load from the web app.
        sheets: the sheets it landed.
        filename: the file it came from.
        rows: the rows it landed.
        started_at: when it started.
        finished_at: when it ended.
        window_start: the first date its rows cover, for movements.
        window_end: the last date, likewise.
        uploaded_by_name: who loaded it; ``None`` from the command line.
        mine: whether the caller loaded it.
        warnings_accepted: rows with warnings accepted when it was confirmed.
        error: why it failed, for a failed run.
    """

    run_id: UUID
    origin: LoadOriginName
    state: RunState
    source_key: str
    dataset_key: str | None
    sheets: list[SheetLoadedResponse]
    filename: str | None
    rows: int
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    window_start: date | None
    window_end: date | None
    uploaded_by_name: str | None
    mine: bool
    warnings_accepted: int
    error: str


class PersonResponse(ResponseModel):
    """Someone who loaded through the web app, for the person filter."""

    subject: str
    name: str | None


class LoadsPageResponse(ResponseModel):
    """A page of the history of loads.

    Attributes:
        total: how many loads match the filter.
        offset: where this page starts.
        limit: its largest size.
        items: the loads.
        people: everyone who loaded through the web app.
    """

    total: int
    offset: int
    limit: int
    items: list[LoadSummaryResponse]
    people: list[PersonResponse]


class OriginalFileResponse(ResponseModel):
    """The file a load came from; download it at ``/api/loads/{run_id}/original``."""

    filename: str
    size_bytes: int
    content_hash: str


class RunCountResponse(ResponseModel):
    """How many rows broke one rule in one column, as a load recorded it."""

    rule: str
    severity: IssueSeverityName
    column: str | None
    rows: int


class LoadValidationResponse(ResponseModel):
    """What the validation of a load's file found, as the load recorded it.

    Attributes:
        validated_at: when the file was validated.
        rows_read: rows read.
        rows_with_warnings: rows with a warning.
        counts: every rule broken, by column.
        accepted: the warnings accepted when the load was confirmed; ``None``
            when there were none.
    """

    validated_at: AwareDatetime | None
    rows_read: int
    rows_with_warnings: int
    counts: list[RunCountResponse]
    accepted: list[RunCountResponse] | None


class LoadDetailResponse(LoadSummaryResponse):
    """One load in detail.

    Attributes:
        plant_code: the plant.
        flow_name: the name the load was recorded under.
        original: the file it came from; ``None`` if Bronze catalogues none.
        validation: for a load from the web app, what its validation found.
        difference: for a catalogue loaded from the web app, how it differed
            from the version it replaced.
        draft_id: the draft it came from, for a load from the web app.
        draft_available: whether the caller can still open that draft.
    """

    plant_code: str
    flow_name: str
    original: OriginalFileResponse | None
    validation: LoadValidationResponse | None
    difference: DifferenceCountsResponse | None
    draft_id: UUID | None
    draft_available: bool


class HealthResponse(ResponseModel):
    """Liveness probe payload.

    Attributes:
        status: always ``"ok"`` when the process answers at all.
        service: the configured application name.
        version: the running application version.
        environment: which environment this process believes it is in.
    """

    status: str
    service: str
    version: str
    environment: str


class CheckResponse(ResponseModel):
    """One dependency probe result inside a readiness payload.

    Attributes:
        name: probe identifier, for example ``"connectivity"``.
        status: ``healthy``, ``degraded``, or ``unhealthy``.
        detail: short explanation of the outcome.
        data: structured extras, for example the measured freshness lag.
    """

    name: str
    status: Literal["healthy", "degraded", "unhealthy"]
    detail: str
    data: dict[str, object] = Field(default_factory=dict)


class ReadinessResponse(ResponseModel):
    """Readiness probe payload aggregating every dependency check.

    Attributes:
        status: the worst individual probe status.
        checked_at: when the probes ran, UTC.
        checks: the individual probe results, so an operator can see which
            dependency is at fault rather than only that something is.
        service: the configured application name.
        version: the running application version.
    """

    status: Literal["healthy", "degraded", "unhealthy"]
    checked_at: AwareDatetime
    checks: list[CheckResponse]
    service: str
    version: str


class KPIPoint(ResponseModel):
    """One KPI value at one period.

    Attributes:
        period_start: start of the period, UTC and aligned to the canonical grid.
        value: the computed value.
        coverage: fraction of the period backed by reliable readings, in
            ``[0, 1]``. Always present, so a consumer can never plot a
            half-observed period as if it were complete.
    """

    period_start: AwareDatetime
    value: Decimal
    coverage: Decimal = Field(ge=0, le=1)


class KPISeriesResponse(ResponseModel):
    """A single KPI as a time series for a single plant.

    Attributes:
        plant_code: the plant the series belongs to.
        kpi_key: which KPI this is.
        unit: the unit the values are expressed in, for example ``"kWh/unit"``.
        granularity: the aggregation level of the points.
        points: the series, in chronological order. Periods with no stored value
            are omitted rather than zero-filled.
        computed_at: when the newest point in the series was produced.
    """

    plant_code: str
    kpi_key: str
    unit: str
    granularity: Literal["hour", "day", "week", "month"]
    points: list[KPIPoint]
    computed_at: AwareDatetime | None = None


class KPISummaryItem(ResponseModel):
    """The latest value of one KPI for one plant.

    Attributes:
        plant_code: the plant.
        kpi_key: which KPI.
        value: the most recent stored value.
        unit: the unit the value is expressed in.
        period_start: the period the value applies to.
        coverage: how complete that period was.
        previous_value: the immediately preceding period's value, for a delta
            indicator. ``None`` when there is no prior period.
    """

    plant_code: str
    kpi_key: str
    value: Decimal
    unit: str
    period_start: AwareDatetime
    coverage: Decimal = Field(ge=0, le=1)
    previous_value: Decimal | None = None


class KPISummaryResponse(ResponseModel):
    """Latest KPI values across the requested plants.

    Attributes:
        items: one entry per plant and KPI.
        generated_at: when this summary was assembled, UTC.
    """

    items: list[KPISummaryItem]
    generated_at: AwareDatetime


class RejectedReading(ResponseModel):
    """One reading a push batch rejected.

    Attributes:
        index: position in the submitted ``readings`` array, so the client can
            correct precisely the offending entry.
        meter_tag: the tag the entry referenced.
        reason: why it was rejected, in English.
    """

    index: int
    meter_tag: str
    reason: str


class IngestAcceptedResponse(ResponseModel):
    """Outcome of a pushed sensor batch.

    Attributes:
        batch_id: the client-supplied idempotency key, echoed back.
        accepted: how many readings were written to Bronze.
        rejected: how many were refused.
        duplicate: whether this batch id had already been ingested.
        rejections: per-entry detail for the refused readings.
        watermark_utc: the newest timestamp now held for this plant and source,
            so the device knows exactly where to resume.
    """

    batch_id: UUID
    accepted: int
    rejected: int
    duplicate: bool
    rejections: list[RejectedReading] = Field(default_factory=list)
    watermark_utc: AwareDatetime | None = None


class ChatAnswerResponse(ResponseModel):
    """The assistant's answer to one question.

    Attributes:
        answer: the text of the answer, plain text: the web app never renders
            it as HTML.
    """

    answer: str


__all__ = [
    "AuthClientConfig",
    "ChatAnswerResponse",
    "CheckResponse",
    "ClientConfigResponse",
    "ColumnChangeResponse",
    "CommitBlockName",
    "ComparisonResponse",
    "ConfirmationResponse",
    "CountOnlyResponse",
    "DifferenceCountsResponse",
    "DifferencePageResponse",
    "DifferenceRowResponse",
    "DraftResponse",
    "DraftStatusName",
    "DraftValidationResponse",
    "ErrorResponse",
    "HealthResponse",
    "IngestAcceptedResponse",
    "IssueResponse",
    "IssueSeverityName",
    "IssuesPageResponse",
    "KPIPoint",
    "KPISeriesResponse",
    "KPISummaryItem",
    "KPISummaryResponse",
    "LoadDetailResponse",
    "LoadOriginName",
    "LoadReferenceResponse",
    "LoadSummaryResponse",
    "LoadValidationResponse",
    "LoadsPageResponse",
    "MeResponse",
    "OriginalFileResponse",
    "PeriodResponse",
    "PersonResponse",
    "PowerBIClientConfig",
    "PreviousFileResponse",
    "ReadinessResponse",
    "ReasonCountResponse",
    "ReasonResponse",
    "RejectedReading",
    "RuleCountResponse",
    "RunCountResponse",
    "RunState",
    "SheetLoadedResponse",
    "UploadLimits",
    "WarningCountResponse",
]
