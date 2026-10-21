"""The energy baseline models, and their forecasts (ADR 0012).

Layer: api

- ``GET /models`` lists the models in force;
- ``GET /models/{model_key}`` gives one in full: what it predicts and from
  what, its equation and the p-value of each coefficient, its training and
  validation figures, and the history it was judged on;
- ``POST /models/{model_key}/forecast`` evaluates its equation for the values
  a person enters, and returns every term, so the forecast can be traced.

The models are trained offline (``energy models train``) and kept in
``gold.baseline_models``; this router only reads them. The arithmetic of a
forecast is the domain's (:func:`energy.domain.baseline_models.forecast`):
the web app draws what it is given and computes nothing.

Needs ``models:view``, which ``api/main.py`` attaches where it mounts this router.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, status

from energy.api.deps import BaselineModelServiceDep
from energy.api.schemas.requests import ForecastRequest
from energy.api.schemas.responses import (
    ErrorResponse,
    ForecastResponse,
    ForecastTermResponse,
    ModelDetailResponse,
    ModelFeatureResponse,
    ModelHistoryPointResponse,
    ModelMetricsResponse,
    ModelSummaryResponse,
)
from energy.domain.baseline_models import default_inputs, equation

if TYPE_CHECKING:
    from energy.domain.baseline_models import BaselineModel, Forecast

router = APIRouter(prefix="/models", tags=["models"])


def _trained_at(model: BaselineModel) -> datetime:
    """When a stored model was trained; a stored model always has the time."""
    if model.trained_at is None:  # pragma: no cover - the store always sets it
        msg = "a stored model has no training time"
        raise ValueError(msg)
    return model.trained_at


def _summary_fields(model: BaselineModel) -> dict[str, object]:
    spec = model.spec
    return {
        "key": spec.key.value,
        "area": spec.area,
        "granularity": spec.granularity.value,
        "target": spec.target,
        "unit": spec.unit,
        "trained_at": _trained_at(model),
        "training_start": model.training_start,
        "training_end": model.training_end,
        "n_training": model.n_training,
        "coverage": model.coverage,
        "validation_wape": model.metrics.validation_wape,
        "validation_mase": model.metrics.validation_mase,
    }


def _summary(model: BaselineModel) -> ModelSummaryResponse:
    return ModelSummaryResponse.model_validate(_summary_fields(model))


def _detail(model: BaselineModel) -> ModelDetailResponse:
    defaults = default_inputs(model)
    features = []
    for feature, coefficient in zip(model.spec.features, model.coefficients, strict=True):
        span = model.range_of(feature.name)
        features.append(
            ModelFeatureResponse(
                name=feature.name,
                kind=feature.kind.value,
                entered=feature.entered,
                minimum=feature.minimum,
                maximum=feature.maximum,
                reference=feature.reference,
                training_minimum=span.minimum if span else None,
                training_maximum=span.maximum if span else None,
                default=defaults.get(feature.name),
                coefficient=coefficient.value,
                p_value=coefficient.p_value,
                significant=coefficient.significant,
            )
        )
    metrics = model.metrics
    return ModelDetailResponse.model_validate(
        {
            **_summary_fields(model),
            "sources": list(model.spec.sources),
            "equation": equation(model),
            "intercept": model.intercept_at_reference,
            "intercept_p_value": model.intercept.p_value,
            "intercept_significant": model.intercept.significant,
            "features": features,
            "metrics": ModelMetricsResponse(
                train_r2=metrics.train_r2,
                train_wape=metrics.train_wape,
                validation_r2=metrics.validation_r2,
                validation_mae=metrics.validation_mae,
                validation_wape=metrics.validation_wape,
                validation_mase=metrics.validation_mase,
                naive_wape=metrics.naive_wape,
                diebold_mariano_p=metrics.diebold_mariano_p,
                alert_band=metrics.alert_band,
                validation_periods=metrics.validation_periods,
            ),
            "next_period": model.next_period,
            "history": [
                ModelHistoryPointResponse(
                    period=point.period,
                    actual=point.actual,
                    predicted=point.predicted,
                    naive=point.naive,
                    inputs=dict(point.inputs),
                )
                for point in model.history
            ],
        }
    )


def _forecast(model: BaselineModel, result: Forecast) -> ForecastResponse:
    return ForecastResponse(
        key=result.model_key.value,
        unit=model.spec.unit,
        period=result.period,
        prediction=result.prediction,
        low=result.low,
        high=result.high,
        intercept=result.intercept,
        terms=[
            ForecastTermResponse(
                feature=term.feature,
                coefficient=term.coefficient,
                value=term.value,
                reference=term.reference,
                contribution=term.contribution,
            )
            for term in result.terms
        ],
        per_unit=result.per_unit,
        outside_training=list(result.outside_training),
    )


@router.get("", summary="List the energy baseline models in force")
def list_models(models: BaselineModelServiceDep) -> list[ModelSummaryResponse]:
    """List the models that have been trained, in a fixed order.

    A model never trained is not listed. A plain function: the database is
    read in FastAPI's thread pool.
    """
    return [_summary(model) for model in models.list_models()]


@router.get(
    "/{model_key}",
    summary="One energy baseline model in full",
    responses={404: {"model": ErrorResponse, "description": "Unknown or never trained."}},
)
def get_model(model_key: str, models: BaselineModelServiceDep) -> ModelDetailResponse:
    """Return a model: its equation, its figures, its variables and its history.

    Raises:
        UnknownModelError: if no model has that key (HTTP 404).
        ModelNotTrainedError: if it was never trained (HTTP 404).
    """
    return _detail(models.model(model_key))


@router.post(
    "/{model_key}/forecast",
    summary="Forecast a model's next period for the values entered",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Unknown or never trained."},
        422: {"model": ErrorResponse, "description": "A value is unknown, missing or invalid."},
    },
)
def forecast_model(
    model_key: str, request: ForecastRequest, models: BaselineModelServiceDep
) -> ForecastResponse:
    """Evaluate the model's equation for the values a person entered.

    A ``POST`` because the values travel in the body; nothing is stored.

    Raises:
        UnknownModelError: if no model has that key (HTTP 404).
        ModelNotTrainedError: if it was never trained (HTTP 404).
        InvalidForecastInputError: if a value is unknown, missing or out of
            range (HTTP 422).
    """
    model = models.model(model_key)
    return _forecast(model, models.forecast(model_key, request.inputs))


__all__ = ["router"]
