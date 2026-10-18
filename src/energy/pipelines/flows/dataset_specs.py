"""Read the dataset specs outside the API.

Layer: pipelines

Responsibility:
    The composition root of ``energy datasets check``: it reads the specs with
    the same loader the API uses when it starts
    (:func:`energy.adapters.yaml_dataset_specs.load_dataset_specs`), from
    ``CONFIG_DIR`` unless told otherwise. A spec this accepts is one the API
    accepts, so an edit can be checked without restarting anything.

Depends on:
    ``energy.adapters``, ``energy.core``, ``energy.domain``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.core.settings import get_settings

if TYPE_CHECKING:
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetCatalogue


def read_dataset_specs(config_dir: Path | None = None) -> DatasetCatalogue:
    """Read and check every dataset spec.

    Args:
        config_dir: the configuration directory; ``CONFIG_DIR`` when ``None``.

    Returns:
        The :class:`~energy.domain.dataset_spec.DatasetCatalogue`.

    Raises:
        DatasetSpecError: listing every problem of every spec.
    """
    return load_dataset_specs(config_dir or get_settings().data.config_dir)


__all__ = ["read_dataset_specs"]
