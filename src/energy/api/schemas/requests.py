"""Pydantic request models - the inbound half of the HTTP contract.

Layer: api

Responsibility:
    Defines the exact shape the API accepts, and rejects anything else before it
    reaches a service. These models are deliberately decoupled from both the
    domain entities and the SQLModel tables: a database column rename must never
    become a breaking API change. Validation here is structural only - types,
    ranges, required fields, timezone-awareness. Business rules such as "this
    meter belongs to this plant" belong to the domain and are checked later.

    The model shapes below are the Phase 1 contract and are complete; the
    handlers that consume them are the placeholders.

Depends on:
    ``pydantic`` and the standard library only.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Granularity = Literal["hour", "day", "week", "month"]


class StrictModel(BaseModel):
    """Base for every request model: unknown fields are an error, not a warning.

    Silently ignoring an unexpected field hides typos in client code, which
    surface much later as missing data.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TimeRangeRequest(StrictModel):
    """A half-open UTC period, reused by every range-scoped request.

    Attributes:
        start: period start, inclusive. Must carry a UTC offset.
        end: period end, exclusive. Must carry a UTC offset and be after
            ``start``.
    """

    start: AwareDatetime = Field(description="Period start, inclusive, ISO-8601 with offset.")
    end: AwareDatetime = Field(description="Period end, exclusive, ISO-8601 with offset.")

    @model_validator(mode="after")
    def _check_order(self) -> TimeRangeRequest:
        """Reject inverted or empty ranges.

        Raises:
            ValueError: if ``end`` is not strictly after ``start``.
        """
        if self.end <= self.start:
            msg = "end must be strictly after start"
            raise ValueError(msg)
        return self


class SensorReadingRequest(StrictModel):
    """One reading pushed by an edge device.

    Attributes:
        meter_tag: source-system tag of the meter, unique within the plant.
        ts_utc: the interval start the reading covers. Must carry a UTC offset;
            naive timestamps are rejected rather than assumed to be UTC.
        value: the measured quantity, in the meter's declared unit.
        unit: the unit the value is expressed in, for cross-checking against the
            meter's configured unit.
        quality: the device's own quality assessment, if it has one.
    """

    meter_tag: str = Field(min_length=1, max_length=64)
    ts_utc: AwareDatetime
    value: Decimal
    unit: str = Field(min_length=1, max_length=16)
    quality: Literal["ok", "estimated", "suspect"] = "ok"


class SensorBatchRequest(StrictModel):
    """A batch of readings pushed by one device in one call.

    Attributes:
        batch_id: client-generated identifier, stable across retries. This is the
            idempotency key: the same id with the same payload is a no-op.
        plant_code: the plant the readings belong to.
        source_key: the configured source this device represents.
        emitted_at: when the device sent the batch, used to measure end-to-end lag.
        readings: the readings, at least one and at most 10 000 per call.
    """

    batch_id: UUID
    plant_code: str = Field(min_length=1, max_length=32)
    source_key: str = Field(min_length=1, max_length=64)
    emitted_at: AwareDatetime
    readings: Annotated[list[SensorReadingRequest], Field(min_length=1, max_length=10_000)]


class KPIQueryRequest(TimeRangeRequest):
    """Parameters for a Gold-layer KPI query.

    Attributes:
        plant_codes: plants to include; empty means every plant the caller may see.
        kpi_keys: KPIs to include; empty means every configured KPI.
        granularity: the aggregation level to read.
        include_coverage: attach the coverage fraction to each point.
    """

    plant_codes: list[str] = Field(default_factory=list)
    kpi_keys: list[str] = Field(default_factory=list)
    granularity: Granularity = "day"
    include_coverage: bool = True


class BackfillRequest(TimeRangeRequest):
    """Parameters for a historical backfill run.

    Attributes:
        plant_code: the plant to backfill.
        source_keys: sources to replay; empty means every configured source.
        force: re-ingest payloads whose content hash is already in Bronze. Off by
            default, because the normal reason to re-run is a *new* file, and
            forcing bypasses the duplicate protection that keeps Bronze honest.
    """

    plant_code: str = Field(min_length=1, max_length=32)
    source_keys: list[str] = Field(default_factory=list)
    force: bool = False


class UploadMetadataRequest(StrictModel):
    """Form metadata accompanying a file upload.

    Attributes:
        plant_code: the plant the file belongs to.
        source_key: the configured source this file represents.
        reporting_period: the month the workbook covers, as ``YYYY-MM``, used to
            detect a file uploaded against the wrong period.
        notes: free-text note from the uploader, stored with the Bronze run.
    """

    plant_code: str = Field(min_length=1, max_length=32)
    source_key: str = Field(min_length=1, max_length=64)
    reporting_period: str = Field(pattern=r"^\d{4}-\d{2}$")
    notes: str = Field(default="", max_length=1000)


def parse_query_window(start: datetime, end: datetime) -> TimeRangeRequest:
    """Build a validated time range from two loose datetimes.

    Used by query-parameter endpoints, which receive bare datetimes rather than a
    request body.

    Args:
        start: period start, inclusive. Must be timezone-aware.
        end: period end, exclusive. Must be timezone-aware.

    Returns:
        A validated :class:`TimeRangeRequest`.

    Raises:
        NaiveDatetimeError: if either bound is timezone-naive.
        InvalidTimeWindowError: if ``end`` is not after ``start``.
    """
    raise NotImplementedError("Phase 3: query-parameter window parsing")


__all__ = [
    "BackfillRequest",
    "Granularity",
    "KPIQueryRequest",
    "SensorBatchRequest",
    "SensorReadingRequest",
    "StrictModel",
    "TimeRangeRequest",
    "UploadMetadataRequest",
    "parse_query_window",
]
