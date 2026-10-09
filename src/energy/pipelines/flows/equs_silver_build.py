"""Build the Silver layer of the equivalent-batteries (EQUS) data.

Layer: pipelines

Responsibility:
    The composition root of the Bronze to Silver build: it wires the
    :class:`~energy.services.equs_silver_builder.EqusSilverBuilder` to the
    PostgreSQL adapter and runs it. It follows the Bronze load in the monthly
    close: first :mod:`energy.pipelines.flows.equs_bronze_load` lands the
    workbooks, then this brings Silver up to date with them.

    Like the load, it is a plain function rather than a Prefect flow, so it can
    run from a shell on a machine with no Prefect server. A scheduled flow can
    wrap :func:`build_silver` unchanged.

Depends on:
    ``energy.services.equs_silver_builder``, ``energy.adapters``,
    ``energy.db.session``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.adapters.postgres_equs_silver import PostgresSilverBuildRepository
from energy.db.session import get_engine
from energy.services.equs_silver_builder import EqusSilverBuilder

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from energy.domain.equs_silver import SilverBuildReport


def build_builder(engine: Engine | None = None) -> EqusSilverBuilder:
    """Wire the Silver builder to the database.

    Args:
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        A ready :class:`~energy.services.equs_silver_builder.EqusSilverBuilder`.
    """
    return EqusSilverBuilder(PostgresSilverBuildRepository(engine or get_engine()))


def build_silver(engine: Engine | None = None) -> SilverBuildReport:
    """Bring Silver up to date with Bronze.

    One transaction: Silver either reflects Bronze as it was when the build
    started, or is left untouched. Building again changes nothing.

    Args:
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The data-quality report of the build.

    Raises:
        PipelineError: if Bronze holds no completed run.
        RepositoryError: if a step fails.
    """
    return build_builder(engine).build()


__all__ = ["build_builder", "build_silver"]
