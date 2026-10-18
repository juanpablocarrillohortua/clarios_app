"""The meaning of a dataset spec, and every kind of mistake a spec file can hold.

Each mistake is its own case, checked by the path and the problem its message
names: that message is what the person fixing the file reads, so it must point
at the field and say what is wrong. The specs here are made up; the committed
ones are checked in ``test_dataset_specs_committed.py``.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest

from energy.domain.dataset_spec import (
    ColumnType,
    DatasetKind,
    DatasetOrigin,
    EntryMode,
    Severity,
    build_catalogue,
    parse_dataset_spec,
)
from energy.domain.exceptions import DatasetSpecError, UnknownDatasetError
from energy.domain.landing import header_keys

if TYPE_CHECKING:
    from collections.abc import Callable

ORIGIN = "config/datasets/example.yaml"
REMOVE = object()


def catalogue_spec() -> dict[str, Any]:
    """A sound spec of a hand-kept catalogue, with made-up columns."""
    return {
        "version": 1,
        "key": "example",
        "kind": "snapshot",
        "label": "datasets.example.name",
        "description": "datasets.example.description",
        "entry_modes": ["upload"],
        "bronze": {"source_key": "manual_sheets", "sheet": "Hoja1", "dataset_key": "example"},
        "silver": {"table": "silver.example"},
        "natural_key": ["Código"],
        "columns": [
            {"header": "Código", "type": "text", "required": True, "pattern": "^[A-Z]{2}$"},
            {"header": "Activo", "type": "text", "allowed": ["Sí", "No"], "severity": "warning"},
            {"header": "Código", "type": "text"},
            {"header": "Cantidad", "type": "decimal", "min": 0, "max": 10},
        ],
    }


def export_spec() -> dict[str, Any]:
    """A sound spec of a QAD export: movements, dated by Fecha Efectiva."""
    return {
        "version": 1,
        "key": "example",
        "kind": "movement",
        "label": "datasets.example.name",
        "description": "datasets.example.description",
        "entry_modes": ["upload"],
        "bronze": {"source_key": "qad_facturas", "sheet": "Datos"},
        "silver": {"table": "silver.unit_ship"},
        "natural_key": ["Factura"],
        "period_column": "Fecha Efectiva",
        "columns": [
            {"header": "Factura", "type": "text", "required": True},
            {"header": "Fecha Efectiva", "type": "date", "required": True},
            {"header": "Fecha de la Factura", "type": "date", "required": True},
        ],
    }


def changed(make: Callable[[], dict[str, Any]], *path: str | int, to: object) -> object:
    """Return a spec with one value changed, or removed when ``to`` is REMOVE."""
    spec = copy.deepcopy(make())
    parent: Any = spec
    for step in path[:-1]:
        parent = parent[step]
    if to is REMOVE:
        del parent[path[-1]]
    else:
        parent[path[-1]] = to
    return spec


def problems_of(raw: object) -> tuple[str, ...]:
    with pytest.raises(DatasetSpecError) as caught:
        parse_dataset_spec(raw, ORIGIN, expected_key="example")
    return caught.value.problems


# =============================================================================
# A sound spec
# =============================================================================
def test_a_catalogue_is_read_whole() -> None:
    spec = parse_dataset_spec(catalogue_spec(), ORIGIN, expected_key="example")

    assert spec.key == "example"
    assert spec.kind is DatasetKind.SNAPSHOT
    assert spec.origin is DatasetOrigin.CATALOGUE
    assert spec.entry_modes == (EntryMode.UPLOAD,)
    assert spec.bronze.sheet == "Hoja1"
    assert spec.bronze.dataset_key == "example"
    assert spec.bronze.header_row == 1
    assert spec.silver_table == "silver.example"
    assert spec.natural_key == ("Código",)
    assert spec.period_column is None
    code, active, _again, quantity = spec.columns
    assert (code.type, code.required, code.pattern) == (ColumnType.TEXT, True, "^[A-Z]{2}$")
    assert (active.allowed, active.severity) == (("Sí", "No"), Severity.WARNING)
    assert (quantity.minimum, quantity.maximum) == (0, 10)


def test_an_export_comes_from_qad_and_is_dated() -> None:
    spec = parse_dataset_spec(export_spec(), ORIGIN)

    assert spec.origin is DatasetOrigin.EXPORT
    assert spec.kind is DatasetKind.MOVEMENT
    assert spec.period_column == "Fecha Efectiva"
    assert spec.bronze.dataset_key is None


def test_a_repeated_header_is_keyed_as_bronze_keys_it() -> None:
    spec = parse_dataset_spec(catalogue_spec(), ORIGIN)

    assert spec.headers == ("Código", "Activo", "Código", "Cantidad")
    assert spec.keys == ("Código", "Activo", "Código #2", "Cantidad")
    assert spec.keys == header_keys(spec.headers)


def test_every_problem_is_reported_not_only_the_first() -> None:
    raw = catalogue_spec()
    raw["kind"] = "catalogue"
    raw["silver"] = {"table": "gold.example"}

    problems = problems_of(raw)

    assert len(problems) == 2
    assert all(problem.startswith(f"{ORIGIN}: ") for problem in problems)


def test_the_error_lists_every_problem_under_one_heading() -> None:
    error = DatasetSpecError(["a: x: first", "b: y: second"])

    assert str(error) == "dataset specs are invalid (2 problems):\n  a: x: first\n  b: y: second"
    assert error.details == {"problems": ["a: x: first", "b: y: second"]}
    assert DatasetSpecError(["a: x: only"]).message == "dataset specs are invalid (1 problem)"


# =============================================================================
# Every kind of mistake
# =============================================================================
MISTAKES = [
    # The file
    pytest.param(["a list"], "(the file)", "must be a mapping, not a list", id="not-a-mapping"),
    pytest.param(changed(catalogue_spec, "kind", to=REMOVE), "kind", "missing", id="missing"),
    pytest.param(
        changed(catalogue_spec, "colums", to=[]), "colums", "unknown field", id="unknown-field"
    ),
    pytest.param(
        changed(catalogue_spec, "bronze", "sheets", to="Hoja1"),
        "bronze.sheets",
        "unknown field",
        id="unknown-field-inside",
    ),
    pytest.param(
        changed(catalogue_spec, "version", to=2),
        "version",
        "2 is not a version this build reads; it reads 1",
        id="version",
    ),
    pytest.param(
        changed(catalogue_spec, "version", to="1"),
        "version",
        "'1' is not a version",
        id="version-as-text",
    ),
    # Key, kind and labels
    pytest.param(
        changed(catalogue_spec, "key", to="Example"),
        "key",
        "'Example' is not lowercase snake_case",
        id="key-not-snake-case",
    ),
    pytest.param(
        changed(catalogue_spec, "key", to="other"),
        "key",
        "'other' differs from the file's name, 'example'",
        id="key-not-the-file-name",
    ),
    pytest.param(
        changed(catalogue_spec, "kind", to="catalogue"),
        "kind",
        "'catalogue' is not one of snapshot, movement",
        id="kind",
    ),
    pytest.param(
        changed(catalogue_spec, "label", to="Líneas de producción"),
        "label",
        "is not a translation key",
        id="label-not-a-key",
    ),
    # Entry modes
    pytest.param(
        changed(catalogue_spec, "entry_modes", to=[]),
        "entry_modes",
        "must be a non-empty list",
        id="no-entry-mode",
    ),
    pytest.param(
        changed(catalogue_spec, "entry_modes", to=["upload", "excel"]),
        "entry_modes[1]",
        "'excel' is not one of upload",
        id="entry-mode",
    ),
    pytest.param(
        changed(catalogue_spec, "entry_modes", to=["upload", "upload"]),
        "entry_modes[1]",
        "'upload' is listed twice",
        id="entry-mode-twice",
    ),
    pytest.param(
        changed(catalogue_spec, "entry_modes", to=["upload", "grid"]),
        "entry_modes[1]",
        "'grid' is not one of upload",
        id="grid-entry-dropped",
    ),
    pytest.param(
        changed(export_spec, "entry_modes", to=["form"]),
        "entry_modes[0]",
        "'form' is not one of upload",
        id="form-entry-dropped",
    ),
    # Bronze
    pytest.param(
        changed(catalogue_spec, "bronze", "source_key", to="sap"),
        "bronze.source_key",
        "'sap' is not a Bronze source",
        id="source",
    ),
    pytest.param(
        changed(catalogue_spec, "bronze", "dataset_key", to=REMOVE),
        "bronze.dataset_key",
        "missing; source 'manual_sheets' needs one",
        id="dataset-key-missing",
    ),
    pytest.param(
        changed(export_spec, "bronze", "dataset_key", to="facturas"),
        "bronze.dataset_key",
        "source 'qad_facturas' takes no dataset_key",
        id="dataset-key-not-taken",
    ),
    pytest.param(
        changed(catalogue_spec, "bronze", "dataset_key", to="Example Key"),
        "bronze.dataset_key",
        "'Example Key' is not lowercase snake_case",
        id="dataset-key-not-snake-case",
    ),
    pytest.param(
        changed(catalogue_spec, "bronze", "sheet", to="REPORTE QAD"),
        "bronze.sheet",
        "is a calculated sheet",
        id="calculated-sheet",
    ),
    pytest.param(
        changed(catalogue_spec, "bronze", "sheet", to="Hoja/1"),
        "bronze.sheet",
        "is not a sheet name Excel accepts",
        id="sheet-name",
    ),
    pytest.param(
        changed(catalogue_spec, "bronze", "header_row", to=0),
        "bronze.header_row",
        "must be a row number from 1",
        id="header-row",
    ),
    # Silver
    pytest.param(
        changed(catalogue_spec, "silver", "table", to="gold.example"),
        "silver.table",
        "'gold.example' is not a table such as silver.<name>",
        id="silver-table",
    ),
    # Columns
    pytest.param(
        changed(catalogue_spec, "columns", to=[]),
        "columns",
        "must be a non-empty list of columns",
        id="no-column",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 0, "header", to=REMOVE),
        "columns[0].header",
        "missing",
        id="header-missing",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 0, "header", to=" Código"),
        "columns[0].header",
        "begins or ends with a space, which Bronze trims",
        id="header-padded",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 3, "type", to="decimel"),
        "columns[3].type",
        "'decimel' is not one of text, integer, decimal, date",
        id="type",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 0, "required", to="yes"),
        "columns[0].required",
        "must be true or false",
        id="required",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 0, "pattern", to="(["),
        "columns[0].pattern",
        "'([' is not a regular expression",
        id="pattern-does-not-compile",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 3, "pattern", to="^[0-9]+$"),
        "columns[3].pattern",
        "applies to text columns, not decimal",
        id="pattern-on-a-number",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 1, "allowed", to=[]),
        "columns[1].allowed",
        "must be a non-empty list",
        id="allowed-empty",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 1, "allowed", to=["Sí", False]),
        "columns[1].allowed[1]",
        "must be quoted text, not false (YAML reads an unquoted Yes, No, On or Off",
        id="allowed-unquoted-no",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 1, "allowed", to=["Sí", "Sí"]),
        "columns[1].allowed[1]",
        "'Sí' is listed twice",
        id="allowed-twice",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 0, "min", to=1),
        "columns[0].min",
        "applies to number columns, not text",
        id="range-on-text",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 3, "min", to=20),
        "columns[3].min",
        "20 is greater than max, 10",
        id="min-above-max",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 3, "min", to="0"),
        "columns[3].min",
        "must be a number, not '0'",
        id="min-not-a-number",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 1, "severity", to="fatal"),
        "columns[1].severity",
        "'fatal' is not one of error, warning",
        id="severity",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 2, "severity", to="warning"),
        "columns[2].severity",
        "applies to pattern, allowed, min and max, and this column has none",
        id="severity-without-rules",
    ),
    pytest.param(
        changed(catalogue_spec, "columns", 0, "label", to="Código"),
        "columns[0].label",
        "is not a translation key",
        id="column-label",
    ),
    # Natural key
    pytest.param(
        changed(catalogue_spec, "natural_key", to=[]),
        "natural_key",
        "must be a non-empty list",
        id="no-natural-key",
    ),
    pytest.param(
        changed(catalogue_spec, "natural_key", to=["Codigo"]),
        "natural_key[0]",
        "'Codigo' names no column",
        id="natural-key-unknown",
    ),
    pytest.param(
        changed(catalogue_spec, "natural_key", to=["Activo"]),
        "natural_key[0]",
        "'Activo' identifies a row, so its column must be required",
        id="natural-key-not-required",
    ),
    pytest.param(
        changed(catalogue_spec, "natural_key", to=["Código", "Código"]),
        "natural_key[1]",
        "'Código' is listed twice",
        id="natural-key-twice",
    ),
    # Period
    pytest.param(
        changed(catalogue_spec, "period_column", to="Código"),
        "period_column",
        "a snapshot has no period column",
        id="period-of-a-snapshot",
    ),
    pytest.param(
        changed(export_spec, "kind", to="snapshot"),
        "kind",
        "source 'qad_facturas' holds movements, dated by 'Fecha Efectiva'",
        id="snapshot-of-movements",
    ),
    pytest.param(
        changed(export_spec, "period_column", to=REMOVE),
        "period_column",
        "missing; a movement is dated by one of its columns",
        id="period-missing",
    ),
    pytest.param(
        changed(export_spec, "period_column", to="Factura"),
        "period_column",
        "'Factura' must name a required date column",
        id="period-not-a-date",
    ),
    pytest.param(
        changed(export_spec, "period_column", to="Fecha de la Factura"),
        "period_column",
        "must be 'Fecha Efectiva', the column that dates source 'qad_facturas'",
        id="period-not-the-sources",
    ),
]


@pytest.mark.parametrize(("raw", "path", "problem"), MISTAKES)
def test_a_mistake_is_named_with_its_file_and_path(raw: object, path: str, problem: str) -> None:
    problems = problems_of(raw)

    assert any(line.startswith(f"{ORIGIN}: {path}: ") and problem in line for line in problems), (
        problems
    )


# =============================================================================
# The catalogue
# =============================================================================
def _spec(key: str, dataset_key: str) -> Any:
    raw = catalogue_spec()
    raw["key"] = key
    raw["bronze"]["dataset_key"] = dataset_key
    return parse_dataset_spec(raw, f"config/datasets/{key}.yaml")


def test_the_catalogue_is_sorted_by_key_and_finds_a_spec() -> None:
    catalogue = build_catalogue(
        {"b.yaml": _spec("second", "second"), "a.yaml": _spec("first", "first")}, "config/datasets"
    )

    assert [spec.key for spec in catalogue] == ["first", "second"]
    assert len(catalogue) == 2
    assert catalogue.get("second").key == "second"


def test_asking_for_an_unknown_dataset_is_a_not_found() -> None:
    catalogue = build_catalogue({"a.yaml": _spec("first", "first")}, "config/datasets")

    with pytest.raises(UnknownDatasetError) as caught:
        catalogue.get("nothing")

    assert caught.value.code == "unknown_dataset"
    assert caught.value.details == {"dataset_key": "nothing"}


def test_two_specs_may_not_share_a_key() -> None:
    with pytest.raises(DatasetSpecError) as caught:
        build_catalogue(
            {"a.yaml": _spec("same", "first"), "b.yaml": _spec("same", "second")},
            "config/datasets",
        )

    assert caught.value.problems == ("b.yaml: key: 'same' is also the key of a.yaml",)


def test_two_specs_may_not_land_in_the_same_place() -> None:
    with pytest.raises(DatasetSpecError) as caught:
        build_catalogue(
            {"a.yaml": _spec("first", "same"), "b.yaml": _spec("second", "same")},
            "config/datasets",
        )

    (problem,) = caught.value.problems
    assert problem.startswith("b.yaml: bronze: lands where a.yaml lands")


def test_a_catalogue_needs_a_spec() -> None:
    with pytest.raises(DatasetSpecError) as caught:
        build_catalogue({}, "config/datasets")

    assert caught.value.problems == (
        "config/datasets: holds no dataset spec; the API needs at least one",
    )
