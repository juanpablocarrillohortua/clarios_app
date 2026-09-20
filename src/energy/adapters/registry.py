"""Builds concrete adapters from declarative configuration.

Layer: adapters

Responsibility:
    Turns ``config/sources.yaml`` into live
    :class:`~energy.ports.energy_source.EnergySource` instances. This is the one
    place where a source key becomes a Python class, which is what makes adding
    a plant or a source a configuration change rather than a code change: the
    pipelines ask the registry for "every source for plant MTY-01" and never
    name a concrete adapter. The registry validates the whole configuration at
    startup - unknown adapter kinds, duplicate keys, conflicting priorities and
    unparseable frequencies all fail here, loudly, rather than at 02:00 during
    the nightly run.

Depends on:
    ``energy.ports.energy_source``, ``energy.ports.object_storage``,
    ``energy.adapters.*``, ``energy.domain.exceptions``, ``energy.core``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from energy.ports.energy_source import EnergySource, SourceDescriptor
    from energy.ports.object_storage import ObjectStorage

#: Adapter kind as written in ``sources.yaml`` mapped to its implementing class.
#: Populated in Phase 3; a kind absent from this mapping is a configuration error.
ADAPTER_KINDS: Mapping[str, str] = {
    "excel": "energy.adapters.excel_source.ExcelSource",
    "erp": "energy.adapters.erp_source.ERPSource",
    "historian": "energy.adapters.historian_source.HistorianSource",
}


class SourceRegistry:
    """Resolves configured source keys to constructed adapter instances.

    Args:
        config: the parsed contents of ``sources.yaml``.
        storage: object storage, injected into adapters that need it.
    """

    def __init__(self, config: Mapping[str, object], storage: ObjectStorage) -> None:
        raise NotImplementedError("Phase 3: registry construction")

    @classmethod
    def from_file(cls, path: Path, storage: ObjectStorage) -> SourceRegistry:
        """Load and validate a registry from a YAML file.

        Args:
            path: path to ``sources.yaml``.
            storage: object storage to inject into file-based adapters.

        Returns:
            A validated registry.

        Raises:
            ConfigurationError: if the file is missing, unparseable, or fails
                :meth:`validate`.
        """
        raise NotImplementedError("Phase 3: registry loading from YAML")

    def get(self, source_key: str) -> EnergySource:
        """Return the adapter for one configured source.

        Args:
            source_key: the key as written in ``sources.yaml``.

        Returns:
            A constructed adapter implementing
            :class:`~energy.ports.energy_source.EnergySource`.

        Raises:
            UnknownSourceError: if the key is not configured.
        """
        raise NotImplementedError("Phase 3: source resolution")

    def for_plant(self, plant_code: str) -> Sequence[EnergySource]:
        """Return every adapter configured for a plant, in priority order.

        Priority order matters: consolidation resolves conflicts by taking the
        first source that supplies a value for an interval, so the sequence this
        returns *is* the conflict-resolution policy.

        Args:
            plant_code: the plant to resolve sources for.

        Returns:
            Adapters sorted by ascending ``priority``.

        Raises:
            EntityNotFoundError: if the plant has no configured sources.
        """
        raise NotImplementedError("Phase 3: per-plant source resolution")

    def descriptors(self) -> Sequence[SourceDescriptor]:
        """Return the descriptor of every configured source.

        Returns:
            All descriptors, for display in the API and the ops runbook.
        """
        raise NotImplementedError("Phase 3: descriptor listing")

    def validate(self) -> None:
        """Check the whole configuration for internal consistency.

        Called at startup and in CI against the committed ``sources.yaml``.

        Raises:
            ConfigurationError: if an adapter kind is unknown, a source key is
                duplicated, two sources for one plant share a priority, a
                declared frequency does not divide the canonical hour, or a
                referenced plant code does not exist in the reference data.
        """
        raise NotImplementedError("Phase 3: configuration validation")


def build_registry(config_dir: Path, storage: ObjectStorage) -> SourceRegistry:
    """Build the process-wide source registry from the config directory.

    Args:
        config_dir: directory holding ``sources.yaml``.
        storage: object storage to inject into file-based adapters.

    Returns:
        A validated :class:`SourceRegistry`.

    Raises:
        ConfigurationError: if the configuration is missing or invalid.
    """
    raise NotImplementedError("Phase 3: registry factory")


__all__ = ["ADAPTER_KINDS", "SourceRegistry", "build_registry"]
