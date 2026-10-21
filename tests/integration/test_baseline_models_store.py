"""``gold.baseline_models`` (revision ``0010``) through its PostgreSQL store.

Layer: tests (integration)

The store keeps the promises of the in-memory one of
``tests/fixtures/baseline_models.py``: a model comes back as it was stored,
storing again supersedes rather than overwrites, and only one model of each
key is in force. The table refuses a second current row by itself.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.fixtures.baseline_models import daily_model, orec_model

from energy.adapters.postgres_baseline_models import PostgresBaselineModelStore
from energy.domain.baseline_models import ModelKey

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


@pytest.fixture
def store(gold_engine: Engine) -> Iterator[PostgresBaselineModelStore]:
    """An empty table, emptied again afterwards."""
    with gold_engine.begin() as connection:
        connection.execute(text("DELETE FROM gold.baseline_models"))
    yield PostgresBaselineModelStore(gold_engine)
    with gold_engine.begin() as connection:
        connection.execute(text("DELETE FROM gold.baseline_models"))


def _rows(engine: Engine) -> list[tuple[str, bool]]:
    with engine.connect() as connection:
        return [
            (row.model_key, row.superseded_at is None)
            for row in connection.execute(
                text("SELECT model_key, superseded_at FROM gold.baseline_models ORDER BY id")
            )
        ]


def test_a_model_comes_back_as_it_was_stored(store: PostgresBaselineModelStore) -> None:
    stored = store.replace([orec_model(), daily_model()])

    back = store.current(ModelKey.OREC_MENSUAL)
    assert back is not None
    assert back.trained_at == stored[0].trained_at
    assert back == replace(orec_model(), trained_at=back.trained_at)
    assert [model.spec.key for model in store.list_current()] == [
        ModelKey.FA_DIARIO,
        ModelKey.OREC_MENSUAL,
    ]
    assert store.current(ModelKey.FA_MENSUAL) is None


def test_storing_again_supersedes_and_keeps_the_old_row(
    store: PostgresBaselineModelStore, gold_engine: Engine
) -> None:
    store.replace([orec_model()])
    store.replace([orec_model(), daily_model()])

    assert _rows(gold_engine) == [
        ("orec_mensual", False),
        ("orec_mensual", True),
        ("fa_diario", True),
    ]


def test_a_model_trained_with_other_variables_is_not_served(
    store: PostgresBaselineModelStore, gold_engine: Engine
) -> None:
    """The spec in the code changed since it was trained: it must be trained again."""
    store.replace([orec_model()])
    with gold_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE gold.baseline_models"
                " SET coefficients = jsonb_set(coefficients, '{features,1,feature}', :name)"
            ),
            {"name": json.dumps("turnos")},
        )

    assert store.current(ModelKey.OREC_MENSUAL) is None
    assert store.list_current() == []


def test_the_table_refuses_a_second_current_model(
    store: PostgresBaselineModelStore, gold_engine: Engine
) -> None:
    store.replace([orec_model()])

    with pytest.raises(IntegrityError), gold_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gold.baseline_models (model_key, spec, coefficients, metrics,"
                " history, feature_ranges, training_start, training_end, n_training, coverage)"
                " SELECT model_key, spec, coefficients, metrics, history, feature_ranges,"
                " training_start, training_end, n_training, coverage"
                " FROM gold.baseline_models"
            )
        )


def test_the_table_refuses_an_unknown_model(gold_engine: Engine) -> None:
    """Ensamble has no model (ADR 0012)."""
    with pytest.raises(IntegrityError), gold_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gold.baseline_models (model_key, spec, coefficients, metrics,"
                " history, feature_ranges, training_start, training_end, n_training, coverage)"
                " VALUES ('ensamble_mensual', '{}', '{}', '{}', '[]', '[]',"
                " '2026-01-01', '2026-06-01', 6, 1)"
            )
        )
