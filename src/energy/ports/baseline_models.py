"""Ports of the energy baseline models: their data, their training, and where they are kept.

Layer: ports

Responsibility:
    Declares the three collaborators of
    :class:`~energy.services.baseline_model_service.BaselineModelService`:

    - :class:`BaselineTrainingSource` gives the rows a model is trained on:
      one period each, its target and its variables. Energy comes from the
      plant's workbooks, which were never loaded into the database; daily
      production comes from Gold.
    - :class:`BaselineTrainer` fits one model and judges it out of sample. It
      is the only collaborator that needs a statistics library.
    - :class:`BaselineModelStore` keeps the trained models, superseding the
      earlier ones, and gives back the current one of each.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from energy.domain.baseline_models import BaselineModel, ModelKey, ModelSpec


@dataclass(frozen=True, slots=True)
class TrainingRow:
    """One period a model can be trained on.

    Attributes:
        period: the first day of the month, or the day.
        target: the value to predict (the period's kWh).
        features: one value per variable of the model, derived ones included.
    """

    period: date
    target: float
    features: Mapping[str, float]


@runtime_checkable
class BaselineTrainingSource(Protocol):
    """Gives the rows a model is trained on, oldest first."""

    def rows(self, spec: ModelSpec) -> Sequence[TrainingRow]:
        """Return every complete period the model's sources hold.

        A period with its target or a variable missing is left out: missing is
        never zero.

        Args:
            spec: the model.

        Returns:
            The periods, oldest first.

        Raises:
            SourceUnavailableError: if a source cannot be read.
        """
        ...


@runtime_checkable
class BaselineTrainer(Protocol):
    """Fits one model, and judges it out of sample."""

    def fit(self, spec: ModelSpec, rows: Sequence[TrainingRow]) -> BaselineModel:
        """Train a model on its window and validate it one step ahead.

        Args:
            spec: the model.
            rows: every period available, oldest first.

        Returns:
            The trained model, not yet stored (``trained_at`` is ``None``).

        Raises:
            DataQualityError: if there are too few rows to validate it.
        """
        ...


@runtime_checkable
class BaselineModelStore(Protocol):
    """Keeps the trained models: one current row per model, the earlier ones superseded."""

    def replace(self, models: Sequence[BaselineModel]) -> Sequence[BaselineModel]:
        """Store new models, superseding the current one of each, in one transaction.

        Args:
            models: the models just trained.

        Returns:
            The same models, with the time they were stored.
        """
        ...

    def current(self, key: ModelKey) -> BaselineModel | None:
        """Return the current model of that key, or ``None`` if it was never trained."""
        ...

    def list_current(self) -> Sequence[BaselineModel]:
        """Return the current model of every key that was trained."""
        ...


__all__ = ["BaselineModelStore", "BaselineTrainer", "BaselineTrainingSource", "TrainingRow"]
