"""The rules of committing a draft, without a database (``energy.domain.draft_commit``)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.domain.draft_commit import (
    INPUT_MODE_UPLOAD,
    CommitBlock,
    DifferenceCounts,
    blocked_by,
    flow_name,
    run_parameters,
    validation_digest,
    warnings_of,
)
from energy.domain.draft_reasons import ReasonId
from energy.domain.draft_rules import IssueSeverity, ReasonCount, RuleCount, RuleId
from energy.domain.drafts import Draft, DraftStatus, ExportFacts, ValidationSummary

pytestmark = pytest.mark.unit

DRAFT = UUID("00000000-0000-4000-8000-000000000071")
EARLIER = UUID("00000000-0000-4000-8000-000000000070")
BASE = UUID("00000000-0000-4000-8000-0000000000b0")
OWNER = "00000000-0000-4000-8000-000000000001"
DIGEST = "d" * 64
WHEN = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

WARNING = RuleCount(
    RuleId.CELL_TEXT_DAMAGED,
    IssueSeverity.WARNING,
    "Número Artículo",
    3,
    3,
    (ReasonCount(ReasonId.CODE_SCIENTIFIC_NOTATION, 3),),
)
INFO = RuleCount(RuleId.SCHEMA_COLUMN_UNKNOWN, IssueSeverity.INFO, "Comentarios", 1, 1)
ERROR = RuleCount(RuleId.CELL_REQUIRED, IssueSeverity.ERROR, "Núm Fact", 2, 2)


@pytest.fixture(scope="module")
def specs() -> dict[str, object]:
    catalogue = load_dataset_specs(Path(__file__).resolve().parents[2] / "config")
    return {spec.key: spec for spec in catalogue}


def summary(
    *counts: RuleCount, rows: int = 4, facts: ExportFacts | None = None
) -> ValidationSummary:
    return ValidationSummary(
        sheet_name="Datos",
        header_row=1,
        rows_read=rows,
        rows_with_errors=sum(c.rows for c in counts if c.severity is IssueSeverity.ERROR),
        rows_with_warnings=sum(c.rows for c in counts if c.severity is IssueSeverity.WARNING),
        counts=counts,
        spec_digest=DIGEST,
        facts=facts,
    )


def draft(
    validation: ValidationSummary | None, status: DraftStatus = DraftStatus.VALIDATED
) -> Draft:
    return Draft(
        draft_id=DRAFT,
        dataset_key="qad_facturas",
        source_key="qad_facturas",
        plant_code="1515",
        status=status,
        filename="Facturas octubre.xlsx",
        declared_bytes=100,
        size_bytes=100,
        content_hash="a" * 64,
        bucket="bronze-raw",
        object_key="1515/qad_facturas/2026/10/18/" + "a" * 64 + ".xlsx",
        created_by=OWNER,
        created_by_name="Ada Lovelace",
        created_at=WHEN,
        uploaded_at=WHEN,
        validated_at=WHEN if validation else None,
        expires_at=WHEN,
        failure_code=None,
        validation=validation,
        previous_draft_id=EARLIER,
    )


class TestBlockedBy:
    def test_a_validated_draft_without_errors_may_be_committed(self) -> None:
        assert blocked_by(draft(summary(WARNING, INFO))) is None

    @pytest.mark.parametrize(
        ("candidate", "already", "block"),
        [
            (draft(None, DraftStatus.UPLOADED), False, CommitBlock.NOT_VALIDATED),
            (draft(summary(), DraftStatus.COMMITTED), False, CommitBlock.COMMITTED),
            (draft(summary(ERROR, WARNING)), False, CommitBlock.ERRORS),
            (draft(summary(rows=0)), False, CommitBlock.NOTHING_TO_LOAD),
            (draft(summary()), True, CommitBlock.ALREADY_LOADED),
        ],
    )
    def test_each_reason_not_to_commit(
        self, candidate: Draft, already: bool, block: CommitBlock
    ) -> None:
        assert blocked_by(candidate, already_loaded=already) is block

    def test_errors_are_named_before_the_file_already_loaded(self) -> None:
        assert blocked_by(draft(summary(ERROR)), already_loaded=True) is CommitBlock.ERRORS


class TestValidationDigest:
    def test_the_same_result_gives_the_same_digest(self) -> None:
        assert validation_digest(summary(WARNING, INFO)) == validation_digest(
            summary(INFO, WARNING)
        )

    def test_how_many_are_listed_and_their_reasons_do_not_count(self) -> None:
        relisted = dataclasses.replace(WARNING, listed=1, reasons=())

        assert validation_digest(summary(relisted)) == validation_digest(summary(WARNING))

    @pytest.mark.parametrize(
        "changed",
        [
            summary(dataclasses.replace(WARNING, rows=4)),
            summary(WARNING, rows=5),
            dataclasses.replace(summary(WARNING), spec_digest="e" * 64),
            summary(WARNING, INFO),
        ],
    )
    def test_any_count_that_changes_changes_the_digest(self, changed: ValidationSummary) -> None:
        assert validation_digest(changed) != validation_digest(summary(WARNING))


def test_only_warnings_must_be_accepted() -> None:
    assert warnings_of(summary(WARNING, INFO)) == (WARNING,)


def test_a_run_is_named_after_its_file_as_the_loader_names_it() -> None:
    assert flow_name("Facturas octubre.xlsx") == "Facturas octubre"


class TestRunParameters:
    FACTS = ExportFacts(
        sheet_names=("Datos", "Info"),
        window_start=date(2025, 10, 3),
        window_end=date(2025, 10, 31),
        export_info={"Programa Exploración": "PRUEBA-01"},
        search_conditions=({"campo": "Fecha Efectiva"},),
    )

    def test_a_movement_records_the_loader_keys_and_its_own(self, specs: dict[str, object]) -> None:
        validated = draft(summary(WARNING, INFO, facts=self.FACTS))

        parameters = run_parameters(validated, specs["qad_facturas"], True, None)  # type: ignore[arg-type]

        assert json.loads(json.dumps(parameters)) == {
            "filename": "Facturas octubre.xlsx",
            "content_hash": "a" * 64,
            "sheets": [{"name": "Datos", "dataset_key": None, "header_row": 1}],
            "plant_code": "1515",
            "force": False,
            "window_start": None,
            "window_end": None,
            "uploaded_by": OWNER,
            "info": {"Programa Exploración": "PRUEBA-01"},
            "input_mode": INPUT_MODE_UPLOAD,
            "dataset_key": "qad_facturas",
            "draft_id": str(DRAFT),
            "previous_draft_id": str(EARLIER),
            "spec_digest": DIGEST,
            "uploaded_by_name": "Ada Lovelace",
            "validation": {
                "validated_at": WHEN.isoformat(),
                "digest": validation_digest(validated.validation),  # type: ignore[arg-type]
                "rows_read": 4,
                "rows_with_errors": 0,
                "rows_with_warnings": 3,
                "counts": [
                    {
                        "rule": "cell.text_damaged",
                        "severity": "warning",
                        "column_key": "Número Artículo",
                        "rows": 3,
                    },
                    {
                        "rule": "schema.column_unknown",
                        "severity": "info",
                        "column_key": "Comentarios",
                        "rows": 1,
                    },
                ],
                "accepted": {
                    "by": OWNER,
                    "warnings": [
                        {
                            "rule": "cell.text_damaged",
                            "severity": "warning",
                            "column_key": "Número Artículo",
                            "rows": 3,
                        }
                    ],
                },
            },
        }

    @pytest.mark.parametrize(("counts", "accept"), [((INFO,), True), ((WARNING,), False)])
    def test_nothing_is_recorded_as_accepted_without_warnings_or_acceptance(
        self, specs: dict[str, object], counts: tuple[RuleCount, ...], accept: bool
    ) -> None:
        parameters = run_parameters(draft(summary(*counts)), specs["qad_facturas"], accept, None)  # type: ignore[arg-type]

        assert parameters["validation"]["accepted"] is None  # type: ignore[index]

    def test_a_catalogue_records_how_it_differed_and_what_silver_keeps(
        self, specs: dict[str, object]
    ) -> None:
        validated = dataclasses.replace(draft(summary()), dataset_key="lineas_produccion")
        counts = DifferenceCounts(BASE, added=1, changed=2, removed=3, unchanged=4)

        parameters = run_parameters(validated, specs["lineas_produccion"], False, counts)  # type: ignore[arg-type]

        assert parameters["sheets"] == [
            {"name": "Lineas Produccion", "dataset_key": "lineas_produccion", "header_row": 1}
        ]
        assert parameters["difference"] == {
            "base_run_id": str(BASE),
            "added": 1,
            "changed": 2,
            "removed": 3,
            "unchanged": 4,
            "removed_rows": "kept",
        }

    def test_a_movement_records_no_difference(self, specs: dict[str, object]) -> None:
        counts = DifferenceCounts(None, 1, 0, 0, 0)

        parameters = run_parameters(draft(summary()), specs["qad_facturas"], False, counts)  # type: ignore[arg-type]

        assert "difference" not in parameters

    def test_a_draft_not_validated_has_no_run(self, specs: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="once validated"):
            run_parameters(draft(None, DraftStatus.UPLOADED), specs["qad_facturas"], False, None)  # type: ignore[arg-type]
