"""Why an issue happened and how to fix it in Excel (``energy.domain.draft_reasons``).

Each row of the table "How failure reasons are generated" in section 5 of
docs/frontend-plan.md has a test here: a synthetic file shows the defect, the
real workbook reader reads it, the checks run, and the issue's first reason is
the one the row names. The fixtures are ``lines_defects`` and
``invoices_defects`` of ``tests/fixtures/workbooks.py``, whose manifests hold
every issue each file must give. No real data.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.workbooks import (
    LINES_HEADERS,
    LINES_SHEET,
    NBSP,
    build_workbook,
    invoices_defects,
    lines_broken,
    lines_defects,
)

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.domain.draft_reasons import (
    Reason,
    ReasonId,
    cell_reasons,
    default_reason,
    edits_between,
    excel_serial_date,
    header_difference,
    header_reasons,
    invisible_characters,
    loose,
    ranked,
    repaired_encoding,
    repeats_headers,
    sheet_reasons,
    text_damage,
)
from energy.domain.draft_rules import (
    IssueCollector,
    IssueSeverity,
    RuleId,
    check_sheet,
    sheet_issue,
)

if TYPE_CHECKING:
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetCatalogue, DatasetSpec
    from energy.domain.draft_rules import Issue

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> DatasetCatalogue:
    return load_dataset_specs(repo_root / "config")


def checked(spec: DatasetSpec, data: bytes, listed: int = 1000) -> tuple[list[Issue], Any]:
    """Return the issues the checks find in a workbook, and the collector."""
    sheet = CalamineWorkbookReader().open_workbook(data).sheet(spec.bronze.sheet)
    collector = IssueCollector(listed)
    list(check_sheet(spec, sheet.rows, collector, sheet.first_row, sheet.first_column))
    return collector.take(), collector


def explained(issues: list[Issue]) -> Counter[tuple[int | None, str | None, str, str]]:
    """Return each issue as its row, column, rule and first reason."""
    return Counter(
        (issue.source_row, issue.column_letter, issue.rule.value, issue.reasons[0].id.value)
        for issue in issues
    )


def find(issues: list[Issue], row: int | None, column: str | None = None) -> Issue:
    """Return the one issue at a row and column, or the only one on the row."""
    found = [
        issue
        for issue in issues
        if issue.source_row == row and (column is None or issue.column_letter == column)
    ]
    assert len(found) == 1, found
    return found[0]


# -----------------------------------------------------------------------------
# The table of section 5, through real files
# -----------------------------------------------------------------------------
class TestTheDefectsOfSection5:
    def test_a_catalogue_gives_exactly_the_planted_issues_and_reasons(
        self, catalogue: DatasetCatalogue
    ) -> None:
        data, manifest = lines_defects()

        issues, _ = checked(catalogue.get("lineas_produccion"), data)

        assert explained(issues) == Counter(manifest)

    def test_an_export_gives_exactly_the_planted_issues_and_reasons(
        self, catalogue: DatasetCatalogue
    ) -> None:
        spec = catalogue.get("qad_facturas")
        data, manifest = invoices_defects(spec)

        issues, _ = checked(spec, data)

        assert explained(issues) == Counter(manifest)

    def test_1_a_date_typed_as_text(self, catalogue: DatasetCatalogue) -> None:
        spec = catalogue.get("qad_facturas")
        issues, _ = checked(spec, invoices_defects(spec)[0])

        assert find(issues, 3).reasons[0] == Reason(ReasonId.DATE_TYPED_AS_TEXT)

    def test_2_a_date_that_lost_its_format_says_which_date(
        self, catalogue: DatasetCatalogue
    ) -> None:
        spec = catalogue.get("qad_facturas")
        issues, _ = checked(spec, invoices_defects(spec)[0])

        reason = find(issues, 4).reasons[0]

        assert reason == Reason(ReasonId.DATE_LOST_FORMAT, {"date": "2025-12-01"})

    def test_3_thousands_points_and_a_decimal_comma(self, catalogue: DatasetCatalogue) -> None:
        spec = catalogue.get("qad_facturas")
        issues, _ = checked(spec, invoices_defects(spec)[0])

        assert find(issues, 5).reasons[0].id is ReasonId.NUMBER_TEXT_WITH_SEPARATORS

    @pytest.mark.parametrize(
        ("row", "reason"),
        [
            (5, ReasonId.CODE_LOST_LEADING_ZEROS),
            (6, ReasonId.CODE_SCIENTIFIC_NOTATION),
            (7, ReasonId.CODE_TRAILING_ZERO_DECIMAL),
        ],
    )
    def test_4_a_code_excel_turned_into_a_number(
        self, catalogue: DatasetCatalogue, row: int, reason: ReasonId
    ) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        assert find(issues, row, "B").reasons[0].id is reason

    def test_4_a_lost_zero_says_how_many_digits_the_code_has(
        self, catalogue: DatasetCatalogue
    ) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        assert find(issues, 5, "B").reasons[0].params == {"digits": 4}

    def test_4_a_code_without_a_pattern_is_caught_by_the_damage_rule(
        self, catalogue: DatasetCatalogue
    ) -> None:
        spec = catalogue.get("qad_facturas")
        issues, _ = checked(spec, invoices_defects(spec)[0])

        damaged = find(issues, 6)

        assert (damaged.rule, damaged.severity) == (
            RuleId.CELL_TEXT_DAMAGED,
            IssueSeverity.WARNING,
        )
        assert damaged.reasons[0].id is ReasonId.CODE_SCIENTIFIC_NOTATION

    def test_5_almost_an_allowed_value_names_the_one_meant(
        self, catalogue: DatasetCatalogue
    ) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        reason = find(issues, 3, "I").reasons[0]

        assert reason == Reason(ReasonId.ALLOWED_NEAR_MATCH, {"suggestion": "Sí"})

    def test_6_an_invisible_character_is_named(self, catalogue: DatasetCatalogue) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        for row, column in ((4, "B"), (11, "F")):
            reason = find(issues, row, column).reasons[0]
            assert reason == Reason(ReasonId.TEXT_INVISIBLE_CHARACTERS, {"characters": ("nbsp",)})

    def test_6_plain_spaces_are_no_problem_at_all(self, catalogue: DatasetCatalogue) -> None:
        spec = catalogue.get("lineas_produccion")
        row = ["FAFORM01", " 1010 ", "Formación ", 100, "", "Manual", "", "1 02:30:00", " Sí", ""]
        data = build_workbook({LINES_SHEET: [LINES_HEADERS, row]})

        issues, _ = checked(spec, data)

        assert issues == []

    def test_7_a_header_without_its_accent(self, catalogue: DatasetCatalogue) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        reason = find(issues, 1, "C").reasons[0]

        assert reason == Reason(ReasonId.HEADER_NEAR_MATCH, {"difference": "accent"})

    def test_7_a_misspelt_header_is_probably_the_missing_column(
        self, catalogue: DatasetCatalogue
    ) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        missing = next(i for i in issues if i.column_key == "Programador")

        assert missing.reasons[0] == Reason(ReasonId.HEADER_SIMILAR, {"found": "Programdor"})

    def test_8_a_header_read_in_the_wrong_encoding_says_what_it_should_be(
        self, catalogue: DatasetCatalogue
    ) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        missing = next(i for i in issues if i.column_key == "Ubicación de Posdeducción")

        assert missing.reasons[0].id is ReasonId.TEXT_WRONG_ENCODING
        assert missing.reasons[0].params["repaired"] == "Ubicación de Posdeducción"

    def test_8_a_value_read_in_the_wrong_encoding(self, catalogue: DatasetCatalogue) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        reason = find(issues, 10, "C").reasons[0]

        assert reason == Reason(ReasonId.TEXT_WRONG_ENCODING, {"repaired": "Formación"})

    def test_9_a_header_row_pasted_in_the_data_explains_its_whole_row(
        self, catalogue: DatasetCatalogue
    ) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        on_row = [issue for issue in issues if issue.source_row == 8]

        assert len(on_row) == 5
        assert all(issue.reasons[0].id is ReasonId.ROW_HEADER_REPEATED for issue in on_row)
        assert all(issue.reasons[-1].id.value.startswith("default.") for issue in on_row)

    def test_a_missing_sheet_with_another_name_is_probably_that_one(
        self, catalogue: DatasetCatalogue
    ) -> None:
        issue = sheet_issue(catalogue.get("lineas_produccion"), ["Hoja1", "Lineas produccion"])

        assert issue is not None
        assert issue.reasons[0] == Reason(ReasonId.SHEET_NEAR_MATCH, {"found": "Lineas produccion"})


# -----------------------------------------------------------------------------
# Every issue has a reason; counts by reason are exact
# -----------------------------------------------------------------------------
class TestEveryIssue:
    def test_ends_with_its_rules_own_reason(self, catalogue: DatasetCatalogue) -> None:
        data, _ = lines_broken()
        issues, _ = checked(catalogue.get("lineas_produccion"), data)

        assert issues
        for issue in issues:
            assert issue.reasons[-1] == default_reason(issue.rule, issue.params)

    def test_never_gives_one_reason_twice(self, catalogue: DatasetCatalogue) -> None:
        issues, _ = checked(catalogue.get("lineas_produccion"), lines_defects()[0])

        for issue in issues:
            ids = [reason.id for reason in issue.reasons]
            assert len(ids) == len(set(ids))

    def test_is_counted_by_its_first_reason_beyond_the_listed_ones(
        self, catalogue: DatasetCatalogue
    ) -> None:
        spec = catalogue.get("lineas_produccion")
        rows = [LINES_HEADERS]
        for number in range(30):
            almac = 101 if number % 3 else "1.01E+03"
            rows.append([f"FAFORM{number:02d}", almac, "", 1, "", "", "", "", "Sí", ""])
        issues, collector = checked(spec, build_workbook({LINES_SHEET: rows}), listed=5)

        almac = next(count for count in collector.counts() if count.column_key == "Almac")

        assert (almac.rows, almac.listed, len(issues)) == (30, 5, 5)
        assert {(by.reason, by.rows) for by in almac.reasons} == {
            (ReasonId.CODE_LOST_LEADING_ZEROS, 20),
            (ReasonId.CODE_SCIENTIFIC_NOTATION, 10),
        }
        assert sum(by.rows for by in almac.reasons) == almac.rows

    @pytest.mark.parametrize("rule", list(RuleId))
    def test_of_every_rule_has_a_reason_of_its_own(self, rule: RuleId) -> None:
        assert default_reason(rule, {}).id.value == f"default.{rule.value}"

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("macros", ReasonId.UNREADABLE_MACROS),
            ("corrupt", ReasonId.UNREADABLE_CORRUPT),
            ("not_a_spreadsheet", ReasonId.UNREADABLE_NOT_A_SPREADSHEET),
            ("not_a_zip", ReasonId.UNREADABLE_NOT_A_ZIP),
            ("anything else", ReasonId.DEFAULT_FILE_UNREADABLE),
        ],
    )
    def test_of_an_unreadable_file_says_why_it_cannot_be_read(
        self, reason: str, expected: ReasonId
    ) -> None:
        assert default_reason("file.unreadable", {"reason": reason}).id is expected


# -----------------------------------------------------------------------------
# The texts of the web app
# -----------------------------------------------------------------------------
def _reason_texts(repo_root: Path, reason: ReasonId) -> dict[str, str]:
    """Return the texts es.json holds for a reason."""
    node: Any = json.loads((repo_root / "frontend/src/i18n/es.json").read_text(encoding="utf-8"))
    for part in ["loads", "reasons", *reason.value.split(".")]:
        node = node[part]
    return node  # type: ignore[no-any-return]


@pytest.mark.parametrize("reason", list(ReasonId))
def test_every_reason_has_its_texts_in_the_web_app(repo_root: Path, reason: ReasonId) -> None:
    texts = _reason_texts(repo_root, reason)

    assert texts["fix"].strip()
    if reason.value.startswith("default."):
        # A rule's own reason has no "why": the rule's message says it.
        assert "why" not in texts
    else:
        assert texts["why"].strip()


def test_every_invisible_character_and_header_difference_has_a_name(repo_root: Path) -> None:
    loads = json.loads((repo_root / "frontend/src/i18n/es.json").read_text(encoding="utf-8"))[
        "loads"
    ]

    assert set(loads["characters"]) == {"nbsp", "tab", "zero_width", "line_break", "control"}
    assert set(loads["differences"]) == {"case", "accent", "spaces", "several"}


# -----------------------------------------------------------------------------
# The detectors one by one, and what they must not say
# -----------------------------------------------------------------------------
class TestDetectors:
    @pytest.mark.parametrize(
        ("serial", "expected"),
        [
            (1, date(1900, 1, 1)),
            (59, date(1900, 2, 28)),
            (61, date(1900, 3, 1)),
            (45992, date(2025, 12, 1)),
            (45992.75, date(2025, 12, 1)),
        ],
    )
    def test_an_excel_day_number_is_read_as_excel_reads_it(
        self, serial: float, expected: date
    ) -> None:
        assert excel_serial_date(serial) == expected

    @pytest.mark.parametrize("serial", [0, -3, 2_958_466])
    def test_a_number_outside_excels_days_is_no_date(self, serial: float) -> None:
        assert excel_serial_date(serial) is None

    def test_a_real_date_cell_gets_no_date_reason(self) -> None:
        when = datetime(2025, 10, 1)  # noqa: DTZ001 - Excel cells carry no time zone

        assert cell_reasons("cell.type", "date", "2025-10-01T00:00:00", when) == []

    def test_text_that_is_no_date_is_not_called_a_date(self) -> None:
        assert cell_reasons("cell.type", "date", "pronto", "pronto") == []

    def test_a_valid_number_with_a_point_is_no_problem(self, catalogue: DatasetCatalogue) -> None:
        spec = catalogue.get("lineas_produccion")
        row = ["FAFORM01", "1010", "", "1.234", "", "", "", "", "Sí", ""]

        issues, _ = checked(spec, build_workbook({LINES_SHEET: [LINES_HEADERS, row]}))

        assert issues == []

    @pytest.mark.parametrize("text", ["1.234,56", "12,5", "1.234.567", "1,234.56", "1 234,5"])
    def test_numbers_with_separators_typed_as_text(self, text: str) -> None:
        reasons = cell_reasons("cell.type", "decimal", text, text)

        assert reasons[0].id is ReasonId.NUMBER_TEXT_WITH_SEPARATORS

    def test_a_word_in_a_number_column_gets_no_separator_reason(self) -> None:
        assert cell_reasons("cell.type", "decimal", "doce", "doce") == []

    def test_an_exact_allowed_value_in_another_case_only_is_suggested(self) -> None:
        reasons = cell_reasons("cell.allowed", "text", "NO", "NO", allowed=("Sí", "No"))

        assert reasons == [Reason(ReasonId.ALLOWED_NEAR_MATCH, {"suggestion": "No"})]

    def test_a_value_far_from_every_allowed_one_gets_no_suggestion(self) -> None:
        assert cell_reasons("cell.allowed", "text", "Tal vez", "Tal vez", allowed=("Sí",)) == []

    def test_a_lowercase_code_is_told_to_be_upper_case(self) -> None:
        reasons = cell_reasons("cell.pattern", "text", "cop", "cop", pattern="^[A-Z]{3}$")

        assert reasons == [Reason(ReasonId.TEXT_LOWERCASE)]

    def test_a_number_cell_too_long_for_its_pattern_lost_no_zeros(self) -> None:
        reasons = cell_reasons("cell.pattern", "text", "10101", 10101.0, pattern="^[0-9]{4}$")

        assert reasons == []

    @pytest.mark.parametrize(
        ("text", "names"),
        [
            (f"1010{NBSP}", ("nbsp",)),
            ("a\tb", ("tab",)),
            ("línea uno\nlínea dos", ("line_break",)),
            ("a" + chr(0x200B) + "b", ("zero_width",)),
            ("limpio", ()),
        ],
    )
    def test_invisible_characters_are_named(self, text: str, names: tuple[str, ...]) -> None:
        assert invisible_characters(text) == names

    def test_free_text_may_hold_a_line_break(self) -> None:
        assert text_damage("Batería\nde 12 voltios") is None

    @pytest.mark.parametrize(
        "text", ["Formación 1", "1.5", "Versión 2", "SÃO", "x" * 50, "231001483"]
    )
    def test_sound_free_text_is_not_called_damaged(self, text: str) -> None:
        assert text_damage(text) is None

    def test_text_read_in_the_wrong_encoding_is_repaired(self) -> None:
        misread = "Descripción".encode().decode("cp1252")

        assert repaired_encoding(misread) == "Descripción"
        assert repaired_encoding("Descripción") is None

    @pytest.mark.parametrize(
        ("found", "expected", "difference"),
        [
            ("descripción", "Descripción", "case"),
            ("Descripcion", "Descripción", "accent"),
            ("Tiempo de configuración (D H:M:S)", "Tiempo de configuración  (D H:M:S)", "spaces"),
            ("descripcion", "Descripción", "several"),
        ],
    )
    def test_a_header_says_how_it_differs(self, found: str, expected: str, difference: str) -> None:
        assert header_difference(found, expected) == difference

    def test_a_header_far_from_the_missing_one_is_not_suggested(self) -> None:
        assert header_reasons("schema.column_missing", "Programador", None, ["Comentarios"]) == []

    def test_a_short_header_is_never_matched_by_edits(self) -> None:
        assert header_reasons("schema.column_missing", "Almac", None, ["Alm"]) == []

    def test_a_sheet_far_from_the_spec_is_not_suggested(self) -> None:
        assert sheet_reasons("Lineas Produccion", ["Hoja1", "Datos"]) == []

    def test_a_row_repeats_its_headers_only_when_most_cells_do(self) -> None:
        headers = ["Línea", "Almac", "Descripción", "Programador"]

        assert repeats_headers(zip(["LÍNEA", "almac", "", None], headers, strict=True))
        assert not repeats_headers(zip(["Línea", "1010", "Una", "Ana"], headers, strict=True))
        assert not repeats_headers(zip(["Línea", "", "", ""], headers, strict=True))

    def test_loose_ignores_case_spaces_accents_and_non_breaking_spaces(self) -> None:
        assert loose(f" Línea{NBSP}Uno ") == loose("lineauno")

    def test_edits_between_two_names(self) -> None:
        assert edits_between("programdor", "programador") == 1
        assert edits_between("almac", "almac") == 0

    def test_reasons_are_ranked_row_first_then_cell_then_rule(self) -> None:
        cell = Reason(ReasonId.TEXT_LOWERCASE)
        row = Reason(ReasonId.ROW_HEADER_REPEATED)

        reasons = ranked("cell.pattern", {}, [cell, cell], [row])

        assert [reason.id for reason in reasons] == [
            ReasonId.ROW_HEADER_REPEATED,
            ReasonId.TEXT_LOWERCASE,
            ReasonId.DEFAULT_CELL_PATTERN,
        ]
