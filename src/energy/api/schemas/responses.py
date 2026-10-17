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
        entry_modes: how rows may be entered: ``upload``, ``grid``, ``form``.
        sheet: the worksheet a file must hold.
        header_row: the row of that sheet that holds the headers.
        natural_key: the column keys that identify a row.
        period_column: the key of the column that dates each row; ``None`` for
            a snapshot.
        columns: every column, in the workbook's order.
    """

    key: str
    kind: Literal["snapshot", "movement"]
    origin: Literal["export", "catalogue"]
    label: str
    description: str
    entry_modes: list[Literal["upload", "grid", "form"]]
    sheet: str
    header_row: int
    natural_key: list[str]
    period_column: str | None
    columns: list[DatasetColumn]


class DatasetsResponse(ResponseModel):
    """Every dataset spec the server holds, by key.

    Attributes:
        datasets: the datasets.
    """

    datasets: list[DatasetDescription]


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


class UploadAcceptedResponse(ResponseModel):
    """Acknowledgement of a file upload.

    Attributes:
        run_id: the ingestion run to poll for the outcome. ``None`` when the
            upload was a duplicate and no run was started.
        content_hash: SHA-256 of the uploaded bytes.
        object_key: where the original file was archived.
        duplicate: whether these exact bytes had already been ingested. A
            duplicate is a successful, idempotent no-op, not an error.
        filename: the original filename as submitted.
        size_bytes: the size of the uploaded payload.
    """

    run_id: UUID | None
    content_hash: str
    object_key: str
    duplicate: bool
    filename: str
    size_bytes: int


class UploadHistoryItem(ResponseModel):
    """One past upload and how its ingestion ended.

    Attributes:
        run_id: the ingestion run, if one was started.
        filename: the original filename.
        plant_code: the plant the file was uploaded for.
        source_key: the configured source it represented.
        uploaded_at: when it was received, UTC.
        state: the final state of the ingestion run.
        rows_ingested: how many Bronze rows it produced.
        rows_quarantined: how many rows were rejected during promotion to Silver.
    """

    run_id: UUID | None
    filename: str
    plant_code: str
    source_key: str
    uploaded_at: AwareDatetime
    state: RunState
    rows_ingested: int
    rows_quarantined: int


class UploadHistoryResponse(ResponseModel):
    """A page of upload history.

    Attributes:
        items: the uploads, newest first.
        total: how many uploads match the filter overall.
    """

    items: list[UploadHistoryItem]
    total: int


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


class LayerCounts(ResponseModel):
    """Row counts a run produced in each medallion layer.

    Attributes:
        bronze: rows appended to Bronze.
        silver: rows written or superseded in Silver.
        gold: KPI rows written to Gold.
        quarantined: rows routed to quarantine instead of Silver.
    """

    bronze: int = 0
    silver: int = 0
    gold: int = 0
    quarantined: int = 0


class RunSummary(ResponseModel):
    """One pipeline run, summarised.

    Attributes:
        run_id: the run identifier.
        flow_name: which flow ran.
        plant_code: the plant it ran for, or ``None`` for multi-plant flows.
        state: the run state.
        window_start: start of the period the run covered.
        window_end: end of the period the run covered.
        started_at: when the run began, UTC.
        finished_at: when it ended, UTC; ``None`` while still running.
        duration_seconds: wall-clock duration; ``None`` while still running.
        counts: rows produced per layer.
    """

    run_id: UUID
    flow_name: str
    plant_code: str | None
    state: RunState
    window_start: AwareDatetime | None
    window_end: AwareDatetime | None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    duration_seconds: float | None = None
    counts: LayerCounts = Field(default_factory=LayerCounts)


class RunListResponse(ResponseModel):
    """A page of pipeline runs.

    Attributes:
        items: the runs, newest first.
        total: how many runs match the filter overall.
    """

    items: list[RunSummary]
    total: int


class TaskOutcome(ResponseModel):
    """One task within a run.

    Attributes:
        task_name: which task.
        state: how it ended.
        started_at: when it began, UTC.
        finished_at: when it ended, UTC.
        message: error message when it failed, otherwise empty.
    """

    task_name: str
    state: RunState
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    message: str = ""


class RunDetailResponse(RunSummary):
    """Full detail of one pipeline run.

    Attributes:
        tasks: the task timeline, in execution order.
        error: the failure message when the run failed, otherwise empty.
        parameters: the parameters the run was invoked with, which is what a
            retry replays.
    """

    tasks: list[TaskOutcome] = Field(default_factory=list)
    error: str = ""
    parameters: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "AuthClientConfig",
    "CheckResponse",
    "ClientConfigResponse",
    "ErrorResponse",
    "HealthResponse",
    "IngestAcceptedResponse",
    "KPIPoint",
    "KPISeriesResponse",
    "KPISummaryItem",
    "KPISummaryResponse",
    "LayerCounts",
    "MeResponse",
    "PowerBIClientConfig",
    "ReadinessResponse",
    "RejectedReading",
    "RunDetailResponse",
    "RunListResponse",
    "RunState",
    "RunSummary",
    "TaskOutcome",
    "UploadAcceptedResponse",
    "UploadHistoryItem",
    "UploadHistoryResponse",
]
