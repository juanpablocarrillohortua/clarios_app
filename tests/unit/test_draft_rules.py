"""The checks of an uploaded file, rule by rule (``energy.domain.draft_rules``).

The rows are given as a workbook reader returns them: strings, numbers and
dates, row 1 being the header row of the committed ``lineas_produccion`` and
``qad_facturas`` specs. Each test breaks one rule and expects exactly the
issue it names.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from energy.adapters.equs_silver_sql import FUNCTIONS
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.domain.dataset_spec import Severity
from energy.domain.draft_rules import (
    SILVER_NUMBER,
    IssueCollector,
    IssueSeverity,
    RuleId,
    check_sheet,
    loose,
    sheet_issue,
)
from energy.domain.landing import landing_rows

if TYPE_CHECKING:
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetCatalogue, DatasetSpec
    from energy.domain.draft_rules import Issue

HEADERS = (
    "Línea",
    "Almac",
    "Descripción",
    "Planificación de Unidades por Hora",
    "Ubicación de Posdeducción",
    "Método de cantidad de transacción",
    "Programador",
    "Tiempo de configuración  (D H:M:S)",
    "Firma Automática",
    "Última Firma Automática",
)
SOUND = ["FAFORM01", "1010", "Formación 1", 120.5, "", "", "", "1 02:30:00", "Sí", ""]


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> DatasetCatalogue:
    return load_dataset_specs(repo_root / "config")


@pytest.fixture
def lines(catalogue: DatasetCatalogue) -> DatasetSpec:
    return catalogue.get("lineas_produccion")


def row(**changes: Any) -> list[Any]:
    """A sound row of the lines catalogue, with some cells changed by letter."""
    cells = list(SOUND)
    for letter, value in changes.items():
        cells["ABCDEFGHIJ".index(letter)] = value
    return cells


def check(
    spec: DatasetSpec, rows: list[list[Any]], listed: int = 1000
) -> tuple[list[Issue], IssueCollector, list[Any]]:
    collector = IssueCollector(listed)
    staged = list(check_sheet(spec, rows, collector))
    return collector.take(), collector, staged


def found(issues: list[Issue]) -> list[tuple[int | None, str | None, str]]:
    return [(issue.source_row, issue.column_letter, issue.rule.value) for issue in issues]


def test_a_sound_sheet_has_no_issue_and_stages_every_row(lines: DatasetSpec) -> None:
    rows = [list(HEADERS), row(), row(A="ENSAM02", I="No")]

    issues, collector, staged = check(lines, rows)

    assert issues == []
    assert collector.counts() == ()
    assert len(staged) == 2


def test_the_rows_staged_are_exactly_the_rows_bronze_lands(lines: DatasetSpec) -> None:
    rows = [list(HEADERS), row(), ["", "", "", "", "", "", "", "", "", ""], row(A="faform02")]

    _issues, _collector, staged = check(lines, rows)

    assert staged == list(landing_rows(rows))


@pytest.mark.parametrize(
    ("changes", "rule", "letter"),
    [
        ({"A": ""}, RuleId.CELL_REQUIRED, "A"),
        ({"A": "   "}, RuleId.CELL_REQUIRED, "A"),
        ({"A": "faform02"}, RuleId.CELL_PATTERN, "A"),
        ({"B": "101"}, RuleId.CELL_PATTERN, "B"),
        ({"D": "doce"}, RuleId.CELL_TYPE, "D"),
        ({"D": "1.234,56"}, RuleId.CELL_TYPE, "D"),
        ({"D": -5}, RuleId.CELL_RANGE, "D"),
        ({"H": "2:30"}, RuleId.CELL_PATTERN, "H"),
        ({"I": "Tal vez"}, RuleId.CELL_ALLOWED, "I"),
        ({"I": "si"}, RuleId.CELL_ALLOWED, "I"),
    ],
)
def test_each_cell_rule_names_its_row_column_and_rule(
    lines: DatasetSpec, changes: dict[str, Any], rule: RuleId, letter: str
) -> None:
    issues, collector, _staged = check(lines, [list(HEADERS), row(), row(**changes)])

    assert found(issues) == [(3, letter, rule.value)]
    assert issues[0].severity is IssueSeverity.ERROR
    assert collector.rows_with_errors == 1


def test_a_value_is_judged_trimmed_of_spaces_as_silver_reads_it(lines: DatasetSpec) -> None:
    issues, _collector, _staged = check(lines, [list(HEADERS), row(A="  FAFORM01 ", B=" 1010")])

    assert issues == []


def test_an_issue_carries_the_value_found_and_what_the_message_needs(lines: DatasetSpec) -> None:
    issues, _collector, _staged = check(
        lines, [list(HEADERS), row(I="Tal vez"), row(D=-5), row(B="101")]
    )

    allowed, too_low, pattern = issues
    assert allowed.value == "Tal vez"
    assert allowed.params == {"allowed": ("Sí", "No")}
    assert allowed.column_key == "Firma Automática"
    assert too_low.value == "-5"
    assert too_low.params == {"min": "0"}
    assert pattern.params == {"pattern": "^[0-9]{4}$"}


def test_a_column_marked_warning_breaks_as_a_warning(lines: DatasetSpec) -> None:
    columns = tuple(
        dataclasses.replace(column, severity=Severity.WARNING) if column.key == "Almac" else column
        for column in lines.columns
    )
    spec = dataclasses.replace(lines, columns=columns)

    issues, collector, _staged = check(spec, [list(HEADERS), row(B="101")])

    assert issues[0].severity is IssueSeverity.WARNING
    assert (collector.rows_with_errors, collector.rows_with_warnings) == (0, 1)


def test_only_the_first_broken_rule_of_a_cell_is_reported(lines: DatasetSpec) -> None:
    # Not a number, so its range is not checked.
    issues, _collector, _staged = check(lines, [list(HEADERS), row(D="menos cinco")])

    assert found(issues) == [(2, "D", "cell.type")]


class TestTypes:
    @pytest.fixture
    def invoices(self, catalogue: DatasetCatalogue) -> DatasetSpec:
        return catalogue.get("qad_facturas")

    def rows(self, spec: DatasetSpec, **values: Any) -> list[list[Any]]:
        sound: dict[str, Any] = {
            "Núm Fact": "F1",
            "Orden": "O1",
            "Línea": 1,
            "Número Artículo": "X",
            "Cantidad fac": 1,
            "Fecha Efectiva": datetime(2025, 10, 1),  # noqa: DTZ001 - Excel has no time zones
        }
        sound.update(values)
        cells = [sound.get(column.key, "") for column in spec.columns]
        return [list(spec.headers), cells]

    @pytest.mark.parametrize("value", [datetime(2025, 10, 1, 8, 30), date(2025, 10, 1)])  # noqa: DTZ001 - Excel has no time zones
    def test_a_date_column_takes_date_cells(self, invoices: DatasetSpec, value: object) -> None:
        issues, _collector, _staged = check(
            invoices, self.rows(invoices, **{"Fecha Efectiva": value})
        )

        assert issues == []

    @pytest.mark.parametrize("value", ["2025-10-01", "01/10/2025", 45992])
    def test_a_date_typed_as_text_or_a_number_is_not_a_date(
        self, invoices: DatasetSpec, value: object
    ) -> None:
        issues, _collector, _staged = check(
            invoices, self.rows(invoices, **{"Fecha Efectiva": value})
        )

        assert [issue.rule for issue in issues] == [RuleId.CELL_TYPE]
        assert issues[0].params == {"type": "date"}

    @pytest.mark.parametrize("value", [3, 3.0, "3", "+3", "3."])
    def test_a_whole_number_may_be_written_several_ways(
        self, invoices: DatasetSpec, value: object
    ) -> None:
        issues, _collector, _staged = check(invoices, self.rows(invoices, Línea=value))

        assert issues == []

    @pytest.mark.parametrize("value", [1.5, "1,5", "uno", "1e3"])
    def test_anything_else_is_not_a_whole_number(
        self, invoices: DatasetSpec, value: object
    ) -> None:
        issues, _collector, _staged = check(invoices, self.rows(invoices, Línea=value))

        assert [issue.rule for issue in issues] == [RuleId.CELL_TYPE]

    def test_a_range_has_both_bounds(self, invoices: DatasetSpec) -> None:
        issues, _collector, _staged = check(
            invoices, self.rows(invoices, **{"Porcentaje Descuento": 100.5})
        )

        assert issues[0].params == {"min": "0", "max": "100"}

    def test_the_numbers_accepted_are_the_silver_builds(self) -> None:
        silver = re.search(
            r"btrim\(valor\) ~ '([^']+)' THEN btrim\(valor\)::numeric", FUNCTIONS.sql
        )

        assert silver is not None
        assert silver.group(1) == SILVER_NUMBER


class TestHeader:
    def test_a_header_differing_only_by_an_accent_is_an_error(self, lines: DatasetSpec) -> None:
        header = list(HEADERS)
        header[2] = "Descripcion"

        issues, _collector, _staged = check(lines, [header, row()])

        assert found(issues) == [(1, "C", "schema.header_mismatch")]
        assert issues[0].params == {"found": "Descripcion", "expected": "Descripción"}
        assert issues[0].column_key == "Descripción"

    def test_a_missing_column_is_an_error_only_when_required(self, lines: DatasetSpec) -> None:
        header = [name for name in HEADERS if name not in {"Línea", "Programador"}]
        cells = [value for index, value in enumerate(SOUND) if index not in {0, 6}]

        issues, _collector, _staged = check(lines, [header, cells])

        assert {(issue.column_key, issue.severity) for issue in issues} == {
            ("Línea", IssueSeverity.ERROR),
            ("Programador", IssueSeverity.WARNING),
        }
        assert {issue.rule for issue in issues} == {RuleId.SCHEMA_COLUMN_MISSING}

    def test_an_unknown_column_is_reported_for_information(self, lines: DatasetSpec) -> None:
        issues, _collector, _staged = check(
            lines, [[*HEADERS, "Comentarios"], [*row(), "una nota"]]
        )

        assert found(issues) == [(1, "K", "schema.column_unknown")]
        assert issues[0].severity is IssueSeverity.INFO

    def test_data_under_no_header_is_reported_once(self, lines: DatasetSpec) -> None:
        rows = [list(HEADERS), [*row(), "", "suelto"], [*row(), "", "otro"]]

        issues, _collector, _staged = check(lines, rows)

        assert found(issues) == [(1, "L", "schema.column_unknown")]

    def test_the_order_of_the_columns_does_not_matter(self, lines: DatasetSpec) -> None:
        issues, _collector, _staged = check(lines, [list(reversed(HEADERS)), list(reversed(row()))])

        assert issues == []

    def test_comparing_loosely_ignores_case_spaces_and_accents(self) -> None:
        assert loose(" Planificación de  Unidades ") == loose("planificacion deunidades")
        assert loose("Línea") != loose("Líneas")


class TestFile:
    def test_an_empty_header_row_stops_the_check(self, lines: DatasetSpec) -> None:
        issues, _collector, staged = check(lines, [["", ""], row()])

        assert found(issues) == [(1, None, "file.header_row_empty")]
        assert staged == []

    def test_a_sheet_shorter_than_its_header_row_stops_the_check(self, lines: DatasetSpec) -> None:
        issues, _collector, _staged = check(lines, [])

        assert found(issues) == [(1, None, "file.header_row_empty")]

    def test_a_header_without_rows_is_an_error(self, lines: DatasetSpec) -> None:
        issues, _collector, _staged = check(lines, [list(HEADERS), [""] * 10])

        assert found(issues) == [(None, None, "file.no_rows")]

    def test_a_missing_sheet_lists_the_sheets_found(self, lines: DatasetSpec) -> None:
        issue = sheet_issue(lines, ["Hoja1", "Resumen"])

        assert issue is not None
        assert issue.rule is RuleId.FILE_SHEET_MISSING
        assert issue.params == {"sheet": "Lineas Produccion", "sheets": ("Hoja1", "Resumen")}
        assert sheet_issue(lines, ["Lineas Produccion"]) is None


class TestCollector:
    def test_it_lists_the_first_issues_and_counts_them_all(self, lines: DatasetSpec) -> None:
        rows = [list(HEADERS), *[row(B="1") for _ in range(5)], row(A="x")]

        issues, collector, _staged = check(lines, rows, listed=2)

        assert len(issues) == 3
        counts = {
            (count.rule, count.column_key): (count.rows, count.listed)
            for count in collector.counts()
        }
        assert counts == {
            (RuleId.CELL_PATTERN, "Almac"): (5, 2),
            (RuleId.CELL_PATTERN, "Línea"): (1, 1),
        }
        assert collector.rows_with_errors == 6

    def test_taken_issues_are_forgotten(self, lines: DatasetSpec) -> None:
        collector = IssueCollector()
        list(check_sheet(lines, [list(HEADERS), row(B="1")], collector))

        assert collector.pending == 1
        assert len(collector.take()) == 1
        assert collector.pending == 0
        assert collector.take() == []


def test_every_rule_has_a_name_and_a_message_in_the_web_app(repo_root: Path) -> None:
    texts = json.loads((repo_root / "frontend/src/i18n/es.json").read_text(encoding="utf-8"))
    rules = texts["loads"]["rules"]

    for rule in RuleId:
        group, name = rule.value.split(".")
        entry = rules[group][name]
        assert isinstance(entry["name"], str), rule
        assert isinstance(entry["message"], str), rule
    for severity in IssueSeverity:
        assert isinstance(texts["loads"]["severity"][severity.value], str)
