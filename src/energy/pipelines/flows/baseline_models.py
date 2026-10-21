"""Train the energy baseline models, and read the ones in force.

Layer: pipelines

Responsibility:
    The composition root of the baseline models' training (ADR 0012). It wires
    :class:`~energy.services.baseline_model_service.BaselineModelService` to
    the plant's energy workbooks, Gold's daily production, the scikit-learn
    trainer and the store in ``gold.baseline_models``.

    Training runs on the host (``make models-train``): the workbooks live in
    ``data/`` and scikit-learn in the ``analytics`` extra, neither of which the
    api image has. The trainer is imported only when training, so reading the
    models needs neither.

Depends on:
    ``energy.services``, ``energy.adapters``, ``energy.db.session``, ``energy.core``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from energy.adapters.energy_workbooks import EnergyWorkbookSource
from energy.adapters.postgres_baseline_models import (
    PostgresBaselineModelStore,
    PostgresDailyProduction,
)
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.domain.exceptions import ConfigurationError, DatabaseUnavailableError
from energy.services.baseline_model_service import BaselineModelService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Engine

    from energy.domain.baseline_models import BaselineModel, ModelKey
    from energy.ports.baseline_models import BaselineTrainer


def _trainer() -> BaselineTrainer:
    """The scikit-learn trainer, imported only now: it needs the ``analytics`` extra.

    Raises:
        ConfigurationError: if scikit-learn or statsmodels is not installed.
    """
    try:
        from energy.adapters.sklearn_baseline_trainer import (  # noqa: PLC0415
            SklearnBaselineTrainer,
        )
    except ImportError as exc:
        raise ConfigurationError(
            "Training needs scikit-learn and statsmodels: install the analytics extra",
            missing=exc.name,
        ) from exc
    return SklearnBaselineTrainer()


def train_models(
    keys: Sequence[ModelKey] | None = None,
    *,
    data_dir: Path | None = None,
    dry_run: bool = False,
    engine: Engine | None = None,
) -> list[BaselineModel]:
    """Train the baseline models and store them, superseding the ones in force.

    Args:
        keys: the models to train; all three when ``None``.
        data_dir: the folder of the energy workbooks; ``ENERGY_DATA_DIR`` when ``None``.
        dry_run: train and validate, and store nothing.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The trained models.

    Raises:
        ConfigurationError: if the analytics extra is not installed.
        SourceUnavailableError: if a workbook cannot be read.
        DataQualityError: if a model has too few periods to be validated.
        DatabaseUnavailableError: if the database cannot be read or written.
    """
    engine = engine or get_engine()
    folder = Path(data_dir or get_settings().data.energy_data_dir)
    service = BaselineModelService(
        PostgresBaselineModelStore(engine),
        source=EnergyWorkbookSource(folder, PostgresDailyProduction(engine)),
        trainer=_trainer(),
    )
    try:
        return service.train(keys, dry_run=dry_run)
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("The database could not be read or written") from exc


def current_models(*, engine: Engine | None = None) -> list[BaselineModel]:
    """Return the model in force of every key that was trained.

    Raises:
        DatabaseUnavailableError: if the database cannot be read.
    """
    service = BaselineModelService(PostgresBaselineModelStore(engine or get_engine()))
    try:
        return service.list_models()
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError("The database could not be read") from exc


__all__ = ["current_models", "train_models"]
