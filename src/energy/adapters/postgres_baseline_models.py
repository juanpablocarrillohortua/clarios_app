"""PostgreSQL side of the energy baseline models: the store, and Gold's daily production.

Layer: adapters

Responsibility:
    - :class:`PostgresBaselineModelStore` implements
      :class:`~energy.ports.baseline_models.BaselineModelStore` over
      ``gold.baseline_models`` (revision ``0010``). Storing new models stamps
      ``superseded_at`` on the current row of each and inserts the new ones,
      in one transaction: a model is never overwritten. A stored model whose
      variables are not those of its spec in the code today is not served:
      it must be trained again.
    - :class:`PostgresDailyProduction` reads the equivalent batteries F&A
      produced each day (FORMACION + DECORADO), summed from the rule rows of
      Gold (``v_reporte_qad_regla``, ``v_unit_ship_regla``) and the factors of
      ``silver.factores_equ``, as ``gold.v_cantidad_cuenta`` sums them by
      month. Every day between the first and the last movement is returned, a
      day without production as zero.

Depends on:
    ``energy.domain``, ``energy.core``, ``sqlalchemy``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import bindparam, text

from energy.core.constants import GOLD_SCHEMA, SILVER_SCHEMA
from energy.domain.baseline_models import (
    SPECS,
    BaselineModel,
    Coefficient,
    FeatureRange,
    HistoryPoint,
    ModelKey,
    ModelMetrics,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.engine import Engine, Row

#: The processes whose production is F&A's.
FA_PROCESSES: Final = ("FORMACION", "DECORADO")

# Every f-string below interpolates module constants only. Values travel as
# bound parameters. Hence the S608 / B608 suppressions.
_TABLE: Final = f"{GOLD_SCHEMA}.baseline_models"

_COLUMNS: Final = (
    "model_key, trained_at, spec, coefficients, metrics, history, feature_ranges,"
    " training_start, training_end, n_training, coverage"
)

_SUPERSEDE: Final = text(
    f"UPDATE {_TABLE} SET superseded_at = now()"  # noqa: S608  # nosec B608
    " WHERE model_key = :model_key AND superseded_at IS NULL"
)

_INSERT: Final = text(
    f"INSERT INTO {_TABLE} (model_key, spec, coefficients, metrics, history,"  # noqa: S608  # nosec B608
    " feature_ranges, training_start, training_end, n_training, coverage)"
    " VALUES (:model_key, CAST(:spec AS jsonb), CAST(:coefficients AS jsonb),"
    " CAST(:metrics AS jsonb), CAST(:history AS jsonb), CAST(:feature_ranges AS jsonb),"
    " :training_start, :training_end, :n_training, :coverage)"
    " RETURNING trained_at"
)

_CURRENT: Final = text(
    f"SELECT {_COLUMNS} FROM {_TABLE}"  # noqa: S608  # nosec B608
    " WHERE superseded_at IS NULL AND model_key = :model_key"
)

_ALL_CURRENT: Final = text(
    f"SELECT {_COLUMNS} FROM {_TABLE}"  # noqa: S608  # nosec B608
    " WHERE superseded_at IS NULL ORDER BY model_key"
)

_DAILY_PRODUCTION: Final = text(
    f"""
    WITH pareja AS (
        SELECT fecha_efectiva, version, cuenta_stats, en_vigor, valor
        FROM {GOLD_SCHEMA}.v_reporte_qad_regla
        UNION ALL
        SELECT fecha_efectiva, version, cuenta_stats, en_vigor, valor
        FROM {GOLD_SCHEMA}.v_unit_ship_regla
    )
    SELECT
        pareja.fecha_efectiva AS dia,
        coalesce(sum(pareja.valor * factor.factor)
            FILTER (WHERE factor.suma_eq AND factor.proceso IN :processes), 0) AS eq
    FROM pareja
    LEFT JOIN {SILVER_SCHEMA}.factores_equ AS factor
      ON factor.version = pareja.version AND factor.cuenta_stats = pareja.cuenta_stats
    WHERE pareja.en_vigor
    GROUP BY pareja.fecha_efectiva
    """  # noqa: S608  # nosec B608
).bindparams(bindparam("processes", expanding=True))


def _spec_json(model: BaselineModel) -> dict[str, Any]:
    """What the model was trained as, recorded beside it."""
    spec = model.spec
    return {
        "key": spec.key.value,
        "area": spec.area,
        "granularity": spec.granularity.value,
        "target": spec.target,
        "unit": spec.unit,
        "features": list(spec.feature_names),
        "training_window": spec.training_window,
        "validation_periods": spec.validation_periods,
        "hac_lags": spec.hac_lags,
        "sources": list(spec.sources),
    }


def _coefficient_json(coefficient: Coefficient) -> dict[str, Any]:
    return {
        "feature": coefficient.feature,
        "value": coefficient.value,
        "p_value": coefficient.p_value,
        "centre": coefficient.centre,
    }


def _coefficient(raw: Mapping[str, Any]) -> Coefficient:
    return Coefficient(
        feature=str(raw["feature"]),
        value=float(raw["value"]),
        p_value=float(raw["p_value"]),
        centre=None if raw.get("centre") is None else float(raw["centre"]),
    )


def _history_json(point: HistoryPoint) -> dict[str, Any]:
    return {
        "period": point.period.isoformat(),
        "actual": point.actual,
        "inputs": dict(point.inputs),
        "predicted": point.predicted,
        "naive": point.naive,
    }


def _history(raw: Mapping[str, Any]) -> HistoryPoint:
    return HistoryPoint(
        period=date.fromisoformat(str(raw["period"])),
        actual=float(raw["actual"]),
        inputs={str(key): float(value) for key, value in raw["inputs"].items()},
        predicted=None if raw.get("predicted") is None else float(raw["predicted"]),
        naive=None if raw.get("naive") is None else float(raw["naive"]),
    )


def _parameters(model: BaselineModel) -> dict[str, Any]:
    """The bound values of one stored model."""
    return {
        "model_key": model.spec.key.value,
        "spec": json.dumps(_spec_json(model)),
        "coefficients": json.dumps(
            {
                "intercept": _coefficient_json(model.intercept),
                "features": [_coefficient_json(item) for item in model.coefficients],
            }
        ),
        "metrics": json.dumps(asdict(model.metrics)),
        "history": json.dumps([_history_json(point) for point in model.history]),
        "feature_ranges": json.dumps(
            [
                {"feature": item.feature, "minimum": item.minimum, "maximum": item.maximum}
                for item in model.ranges
            ]
        ),
        "training_start": model.training_start,
        "training_end": model.training_end,
        "n_training": model.n_training,
        "coverage": round(model.coverage, 4),
    }


def _model(row: Row[Any]) -> BaselineModel | None:
    """A stored row as a model, or ``None`` if its variables are not the spec's today."""
    spec = SPECS[ModelKey(row.model_key)]
    coefficients = row.coefficients
    features = tuple(_coefficient(item) for item in coefficients["features"])
    if tuple(item.feature for item in features) != spec.feature_names:
        return None
    return BaselineModel(
        spec=spec,
        intercept=_coefficient(coefficients["intercept"]),
        coefficients=features,
        metrics=ModelMetrics(**row.metrics),
        history=tuple(_history(point) for point in row.history),
        ranges=tuple(
            FeatureRange(str(item["feature"]), float(item["minimum"]), float(item["maximum"]))
            for item in row.feature_ranges
        ),
        training_start=row.training_start,
        training_end=row.training_end,
        n_training=int(row.n_training),
        coverage=float(row.coverage),
        trained_at=row.trained_at,
    )


class PostgresBaselineModelStore:
    """``gold.baseline_models``: one current row per model, the earlier ones superseded."""

    def __init__(self, engine: Engine) -> None:
        """Build the store over an engine."""
        self._engine = engine

    def replace(self, models: Sequence[BaselineModel]) -> list[BaselineModel]:
        """Supersede the current row of each model and insert the new ones, in one transaction."""
        stored: list[BaselineModel] = []
        with self._engine.begin() as connection:
            for model in models:
                connection.execute(_SUPERSEDE, {"model_key": model.spec.key.value})
                trained_at = connection.execute(_INSERT, _parameters(model)).scalar_one()
                stored.append(replace(model, trained_at=trained_at))
        return stored

    def current(self, key: ModelKey) -> BaselineModel | None:
        """Return the current model of that key, if it was trained as the spec says."""
        with self._engine.connect() as connection:
            row = connection.execute(_CURRENT, {"model_key": key.value}).one_or_none()
        return None if row is None else _model(row)

    def list_current(self) -> list[BaselineModel]:
        """Return the current model of every key, if trained as the spec says."""
        with self._engine.connect() as connection:
            rows = connection.execute(_ALL_CURRENT).all()
        return [model for row in rows if (model := _model(row)) is not None]


class PostgresDailyProduction:
    """F&A's equivalent batteries, day by day, from Gold."""

    def __init__(self, engine: Engine) -> None:
        """Build the reader over an engine."""
        self._engine = engine

    def __call__(self) -> dict[date, float]:
        """Return every day from the first movement to the last; zero when nothing was produced."""
        with self._engine.connect() as connection:
            rows = connection.execute(_DAILY_PRODUCTION, {"processes": FA_PROCESSES}).all()
        produced = {row.dia: float(row.eq) for row in rows}
        if not produced:
            return {}
        first, last = min(produced), max(produced)
        span = (last - first).days + 1
        days = (first + timedelta(days=offset) for offset in range(span))
        return {day: produced.get(day, 0.0) for day in days}


__all__ = ["FA_PROCESSES", "PostgresBaselineModelStore", "PostgresDailyProduction"]
