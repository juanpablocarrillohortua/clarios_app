"""Adapter reading high-frequency sensor data from a plant historian.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.energy_source.EnergySource` for process
    historians (PI, Wonderware, or an OPC-UA gateway). Historians store
    irregularly sampled, compressed time series: values are written only when
    they change by more than a deadband, so the absence of a point means "no
    significant change", not "no data". Interpreting that correctly is this
    adapter's core responsibility - it requests interpolated or time-weighted
    values at the cadence the source declares, rather than reading raw
    compressed points and treating gaps as missing data. Resampling onto the
    canonical grid still happens later, in
    :mod:`energy.pipelines.tasks.consolidate`.

Depends on:
    ``energy.ports.energy_source``, ``energy.domain.exceptions``,
    ``energy.core``, ``polars``, ``httpx``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from energy.ports.energy_source import SourceBatch, SourceDescriptor

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from energy.domain.value_objects import TimeWindow

#: How the historian should aggregate raw compressed points onto a cadence.
RetrievalMode = Literal["interpolated", "time_weighted", "raw"]


@dataclass(frozen=True, slots=True)
class HistorianConnection:
    """Connection parameters for one historian endpoint.

    Attributes:
        base_url: historian REST or OPC-UA gateway base URL.
        tag_prefix: prefix applied to every meter tag to form a historian tag.
        retrieval_mode: how to sample the compressed series. ``time_weighted`` is
            correct for consumption totals; ``interpolated`` for instantaneous
            readings such as demand.
        max_tags_per_request: how many tags may be requested at once before the
            gateway rejects the call.
        timeout_seconds: per-request timeout.
        verify_tls: whether to verify the gateway certificate. Plant networks
            often use an internal CA; disabling it is a deliberate, configured
            decision rather than a default.
    """

    base_url: str
    tag_prefix: str
    retrieval_mode: RetrievalMode
    max_tags_per_request: int
    timeout_seconds: int
    verify_tls: bool = True


class HistorianSource:
    """Reads sampled sensor series from a plant historian.

    Args:
        descriptor: the configured source identity.
        connection: how to reach the historian.
        tags: the historian tags to read, one per meter.
    """

    def __init__(
        self,
        descriptor: SourceDescriptor,
        connection: HistorianConnection,
        tags: Sequence[str],
    ) -> None:
        raise NotImplementedError("Phase 3: historian source construction")

    @property
    def descriptor(self) -> SourceDescriptor:
        """Return this source's static identity and capabilities."""
        raise NotImplementedError("Phase 3: descriptor accessor")

    def health_check(self) -> bool:
        """Confirm the historian answers and every configured tag exists.

        Returns:
            ``True`` when the gateway responds and resolves all tags.

        Raises:
            SourceUnavailableError: if the gateway is unreachable or a configured
                tag does not exist, which usually means an instrument was
                renamed and readings have been silently missing since.
        """
        raise NotImplementedError("Phase 3: historian reachability and tag check")

    def fetch(self, window: TimeWindow | None = None) -> SourceBatch:
        """Read sampled values for the configured tags over a period.

        Args:
            window: the period to read, in UTC. Required for this source: a
                historian holds years of sub-minute data and an unbounded read
                would never complete.

        Returns:
            A batch with one row per tag and sample, at the source's declared
            native frequency.

        Raises:
            SourceUnavailableError: if the gateway is unreachable or times out.
            ValueError: if ``window`` is ``None``.
        """
        raise NotImplementedError("Phase 3: historian sampled read")

    def fetch_chunks(
        self,
        window: TimeWindow | None = None,
        chunk_size: int = 100_000,
    ) -> Iterator[SourceBatch]:
        """Read a long period as a sequence of bounded time slices.

        Slicing is by time rather than by row count, because the historian API is
        queried per interval and the row count of a slice is not known in advance.

        Args:
            window: the period to read, in UTC.
            chunk_size: target maximum rows per batch, used to size the time
                slices from the declared native frequency and tag count.

        Yields:
            Successive batches covering consecutive slices of ``window``.

        Raises:
            SourceUnavailableError: if the gateway becomes unreachable mid-read.
        """
        raise NotImplementedError("Phase 3: sliced historian read")


__all__ = ["HistorianConnection", "HistorianSource", "RetrievalMode"]
