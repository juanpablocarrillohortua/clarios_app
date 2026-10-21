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
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from energy.core.constants import MAX_CHAT_HISTORY

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


class CommitDraftRequest(StrictModel):
    """What a person confirms when they commit a draft (increment 7).

    Who confirms is never part of it: it is the draft's owner, the caller.

    Attributes:
        validation_digest: the ``validation_digest`` of the confirmation the
            person saw. A validation that changed since is refused (409).
        accept_warnings: ``true`` when the person accepts the draft's warnings.
            A draft with warnings is refused without it (422).
        base_run_id: for a catalogue, the ``current_version`` the person saw;
            ``null`` when there was none. Another load having become current
            since is refused (409). Ignored for movements.
    """

    validation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    accept_warnings: bool = False
    base_run_id: UUID | None = None


#: The longest question, in characters.
MAX_QUESTION_CHARS: Final = 4_000
#: The longest earlier message, in characters: an answer may be longer than a question.
MAX_TURN_CHARS: Final = 20_000


class ChatTurn(StrictModel):
    """One earlier message of a conversation with the assistant.

    Attributes:
        role: who wrote it: the person (``user``) or the assistant.
        content: its text.
    """

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_TURN_CHARS)


class ChatRequest(StrictModel):
    """A question to the assistant, with the conversation so far.

    The API stores nothing: the caller keeps the conversation and sends it
    with each question. Only the last ``CHAT_CONTEXT_WINDOW_LIMIT`` messages
    reach the model.

    Attributes:
        history: the earlier messages, oldest first, without the question.
        question: what the person asks now. A blank one is refused with
            ``empty_question`` (422) by the service, not here, so the refusal
            has the API's error envelope.
    """

    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_CHAT_HISTORY)
    question: str = Field(max_length=MAX_QUESTION_CHARS)


#: The most variables a forecast may name: every model has three or fewer.
MAX_FORECAST_INPUTS: Final = 10


class ForecastRequest(StrictModel):
    """The values a person enters for a baseline model's forecast.

    Attributes:
        inputs: one value per variable the person enters, by name (``eq``,
            ``dias`` or ``domingo``). Which names a model takes, and their
            ranges, are in ``GET /models/{model_key}``; the service refuses
            anything else with ``invalid_forecast_input`` (422).
    """

    inputs: dict[str, float] = Field(max_length=MAX_FORECAST_INPUTS)


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
    "MAX_FORECAST_INPUTS",
    "MAX_QUESTION_CHARS",
    "MAX_TURN_CHARS",
    "BackfillRequest",
    "ChatRequest",
    "ChatTurn",
    "CommitDraftRequest",
    "ForecastRequest",
    "Granularity",
    "KPIQueryRequest",
    "SensorBatchRequest",
    "SensorReadingRequest",
    "StrictModel",
    "TimeRangeRequest",
    "parse_query_window",
]
