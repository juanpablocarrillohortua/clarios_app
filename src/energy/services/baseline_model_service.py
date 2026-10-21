"""The energy baseline models: train them, and serve them to the web app.

Layer: services

Responsibility:
    Trains the models of :data:`~energy.domain.baseline_models.SPECS` and
    stores them (``energy models train``), and serves the current ones with
    their forecasts (``/api/models``, ADR 0012). Training needs a source of
    rows and a trainer; serving needs only the store, so the API builds this
    service without a statistics library.

Depends on:
    ``energy.domain``, ``energy.ports``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.domain.baseline_models import SPECS, ModelKey, default_inputs, forecast
from energy.domain.exceptions import ConfigurationError, ModelNotTrainedError, UnknownModelError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from energy.domain.baseline_models import BaselineModel, Forecast
    from energy.ports.baseline_models import (
        BaselineModelStore,
        BaselineTrainer,
        BaselineTrainingSource,
    )


def model_key(key: str) -> ModelKey:
    """Return the model key of that text.

    Raises:
        UnknownModelError: if no model has that key.
    """
    try:
        return ModelKey(key)
    except ValueError:
        raise UnknownModelError("No model has that key", model_key=key) from None


class BaselineModelService:
    """Trains, stores and serves the energy baseline models."""

    def __init__(
        self,
        store: BaselineModelStore,
        *,
        source: BaselineTrainingSource | None = None,
        trainer: BaselineTrainer | None = None,
    ) -> None:
        """Build the service.

        Args:
            store: where the models are kept.
            source: the rows to train on; needed by :meth:`train` only.
            trainer: fits the models; needed by :meth:`train` only.
        """
        self._store = store
        self._source = source
        self._trainer = trainer

    def train(
        self, keys: Sequence[ModelKey] | None = None, *, dry_run: bool = False
    ) -> list[BaselineModel]:
        """Train models and store them, superseding the current ones.

        Every model is trained before any is stored, so a failure stores none.

        Args:
            keys: the models to train; every model when ``None``.
            dry_run: train and validate, but store nothing.

        Returns:
            The trained models, with the time they were stored unless ``dry_run``.

        Raises:
            ConfigurationError: if the service was built without a source or a trainer.
        """
        if self._source is None or self._trainer is None:
            raise ConfigurationError("Training needs a source of rows and a trainer")
        trained = [
            self._trainer.fit(SPECS[key], self._source.rows(SPECS[key]))
            for key in (keys or tuple(SPECS))
        ]
        if dry_run:
            return trained
        return list(self._store.replace(trained))

    def list_models(self) -> list[BaselineModel]:
        """Return the current model of every key that was trained, in the order of :data:`SPECS`."""
        current = {model.spec.key: model for model in self._store.list_current()}
        return [current[key] for key in SPECS if key in current]

    def model(self, key: str) -> BaselineModel:
        """Return the current model of that key.

        Raises:
            UnknownModelError: if no model has that key.
            ModelNotTrainedError: if the model was never trained.
        """
        found = self._store.current(model_key(key))
        if found is None:
            raise ModelNotTrainedError("The model has not been trained", model_key=key)
        return found

    def defaults(self, key: str) -> dict[str, float]:
        """Return the values a forecast of that model starts from."""
        return default_inputs(self.model(key))

    def forecast(self, key: str, inputs: Mapping[str, float]) -> Forecast:
        """Forecast the next period of a model for the values entered.

        Raises:
            UnknownModelError: if no model has that key.
            ModelNotTrainedError: if the model was never trained.
            InvalidForecastInputError: if a value is unknown, missing or out of range.
        """
        return forecast(self.model(key), inputs)


__all__ = ["BaselineModelService", "model_key"]
