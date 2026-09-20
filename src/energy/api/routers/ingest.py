"""Sensor and historian push endpoint feeding the Bronze layer.

Layer: api

Responsibility:
    Receives readings pushed by plant edge devices and historian bridges, as
    opposed to the pull-based adapters that go and fetch data. The handler
    validates the envelope, stamps provenance, and appends to Bronze
    synchronously - edge devices buffer locally and need an immediate,
    authoritative accept or reject rather than a job id. It performs no
    normalisation, no unit conversion, and no aggregation: whatever arrives is
    stored verbatim and the batch pipeline promotes it later.

Depends on:
    ``energy.api.deps``, ``energy.api.schemas``,
    ``energy.ports.reading_repository``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from fastapi import APIRouter, status

from energy.api.deps import APIKeyDep, SessionDep, SettingsDep
from energy.api.schemas.requests import SensorBatchRequest
from energy.api.schemas.responses import IngestAcceptedResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post(
    "/readings",
    summary="Push a batch of sensor readings",
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_readings(
    payload: SensorBatchRequest,
    session: SessionDep,
    settings: SettingsDep,
    api_key: APIKeyDep,
) -> IngestAcceptedResponse:
    """Append a batch of pushed readings to Bronze.

    The batch is idempotent on its ``batch_id``: a device that retries after a
    network timeout does not duplicate rows. Individual readings that fail
    envelope validation are rejected and reported per index, while the rest of
    the batch is still accepted - one bad sensor must not block a whole plant.

    Args:
        payload: the batch envelope with its readings.
        session: injected database session.
        settings: injected process configuration.
        api_key: injected device credential.

    Returns:
        An :class:`~energy.api.schemas.responses.IngestAcceptedResponse` with the
        accepted and rejected counts, and the index and reason of each rejection.

    Raises:
        EntityNotFoundError: if the batch names an unknown plant or meter tag,
            surfaced as HTTP 404.
        DuplicateReadingError: if ``batch_id`` was already ingested with a
            different payload, surfaced as HTTP 409.
    """
    raise NotImplementedError("Phase 3: sensor batch ingestion")


@router.get(
    "/watermark/{plant_code}/{source_key}",
    summary="Read the current ingestion watermark",
    status_code=status.HTTP_200_OK,
)
async def read_watermark(
    session: SessionDep,
    plant_code: str,
    source_key: str,
) -> dict[str, object]:
    """Return the newest timestamp ingested for a plant and source.

    Edge devices call this on reconnect to learn where to resume, which is what
    lets them replay only the gap instead of their whole local buffer.

    Args:
        session: injected database session.
        plant_code: the plant to inspect.
        source_key: the source to inspect.

    Returns:
        The watermark timestamp in UTC, or ``None`` when nothing has been
        ingested for that pair yet.

    Raises:
        EntityNotFoundError: if the plant or source is unknown.
    """
    raise NotImplementedError("Phase 3: watermark read endpoint")


__all__ = ["router"]
